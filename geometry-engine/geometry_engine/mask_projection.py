"""Lift existing 2D masks onto visible, depth-supported mesh faces.

Depth agreement follows Open3DIS PointCloudToImageMapper, adapted to a triangle
z-buffer and explicit confidence/invalid-depth handling. See THIRD_PARTY_NOTICES.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image
from scipy.ndimage import binary_erosion

from .mesh import read_mesh
from .segmentation import _rename
from .validation import read_object, session_file
from .visibility import render_visibility


def file_hash(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def save_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")


@dataclass(frozen=True)
class ProjectionSettings:
    depth_abs_tolerance_m: float = 0.05
    depth_relative_tolerance: float = 0.02
    min_depth_confidence: int = 1
    mask_erode_pixels: int = 1
    min_observation_support: float = 0.2
    min_face_fraction: float = 0.5
    raster_scale: int = 2
    max_image_dimension: int = 1024

    def __post_init__(self):
        for name in ("depth_abs_tolerance_m", "depth_relative_tolerance", "min_observation_support", "min_face_fraction"):
            value = getattr(self, name)
            if not np.isfinite(value) or value < 0:
                raise ValueError(f"Invalid {name}")
        if not 0 < self.min_observation_support <= 1 or not 0 < self.min_face_fraction <= 1:
            raise ValueError("Support fractions must be in (0,1]")
        for name, minimum, maximum in (("min_depth_confidence", 0, 2), ("mask_erode_pixels", 0, 10),
                                      ("raster_scale", 1, 4), ("max_image_dimension", 16, 4096)):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"Invalid {name}")


def camera_parameters(metadata, coordinate_system):
    intrinsics = metadata["intrinsics"]
    if "K" in intrinsics:
        K = np.asarray(intrinsics["K"], dtype=float)
    else:
        K = np.array([[intrinsics["fx"], 0, intrinsics["cx"]],
                      [0, intrinsics["fy"], intrinsics["cy"]], [0, 0, 1]], dtype=float)
    transform = np.asarray(metadata["pose"]["T_world_from_camera"], dtype=float)
    if coordinate_system == "arkit":
        transform = transform @ np.diag([1., -1., -1., 1.])
    elif (not isinstance(coordinate_system, dict)
          or coordinate_system.get("camera_axes") != {"x": "right", "y": "down", "z": "forward"}):
        raise ValueError("Unknown camera axes; expected ARKit or explicit right/down/forward axes")
    for axis in ("width", "height"):
        if axis in intrinsics and intrinsics[axis] != metadata["rgb"][axis]:
            raise ValueError("Camera intrinsics must describe stored RGB pixels")
    return K, transform


def resize_nearest(array, size):
    return np.asarray(Image.fromarray(array).resize(size, Image.Resampling.NEAREST))


def load_depth(original, metadata_path, metadata, rgb_size):
    """Read registered depth; resize only matching-aspect RGB/depth sensor grids."""
    info = metadata.get("depth")
    if info is None:
        return None, None, ["depth_unavailable"]
    depth_path = session_file(original, metadata_path.parent, info.get("path"))
    width, height = info.get("width"), info.get("height")
    if type(width) is not int or type(height) is not int or min(width, height) < 1 or width*height > 25_000_000:
        raise ValueError("Invalid depth dimensions")
    if abs(width/height-rgb_size[0]/rgb_size[1]) > 0.01:
        raise ValueError("Depth and RGB grids have different aspect ratios; registration is required")
    if depth_path.suffix.lower() == ".bin":
        if (info.get("dtype") != "float32" or info.get("byte_order") != "little" or info.get("unit") != "meter"
                or depth_path.stat().st_size != width*height*4):
            raise ValueError("Expected packed little-endian float32 depth in meters")
        depth = np.fromfile(depth_path, dtype="<f4").reshape(height, width)
    else:
        if info.get("encoding") != "uint16_png" or info.get("scale_to_meters") != 0.001:
            raise ValueError("Expected uint16 PNG depth in millimeters")
        with Image.open(depth_path) as image:
            if image.size != (width, height) or image.mode not in ("I;16", "I;16L", "I"):
                raise ValueError("Depth image dimensions or type differ from metadata")
            depth = np.asarray(image).astype(np.float32)*0.001
    warnings = []
    confidence_info = metadata.get("confidence")
    confidence = None
    if confidence_info is not None:
        confidence_path = session_file(original, metadata_path.parent, confidence_info.get("path"))
        if (confidence_info.get("width"), confidence_info.get("height")) != (width, height):
            raise ValueError("Confidence grid must match depth")
        if confidence_path.suffix.lower() == ".bin":
            if confidence_info.get("dtype") != "uint8" or confidence_path.stat().st_size != width*height:
                raise ValueError("Expected packed uint8 confidence")
            confidence = np.fromfile(confidence_path, dtype=np.uint8).reshape(height, width)
        else:
            with Image.open(confidence_path) as image:
                confidence = np.asarray(image)
        if confidence.shape != depth.shape or not np.isin(confidence, (0, 1, 2)).all():
            raise ValueError("Invalid confidence grid")
        confidence = resize_nearest(confidence.astype(np.uint8), rgb_size)
    else:
        warnings.append("confidence_unavailable_depth_only")
    return resize_nearest(depth, rgb_size), confidence, warnings


class MaskProjectionService:
    def __init__(self, settings=None):
        self.settings = settings or ProjectionSettings()

    def project(self, directory, output_dir=None, *, max_frames=None, segmentation_dir=None, mesh_dir=None, progress=None):
        session = Path(directory).resolve()
        original = (session/"original").resolve()
        output = Path(output_dir).resolve() if output_dir is not None else session/"mask_projection"
        if not original.is_dir() or original.parent != session or output.parent != session or output == original:
            raise ValueError("Expected session/original and a stage output directly inside the session")
        if output.exists():
            raise FileExistsError(f"Output exists: {output}")
        segmentation = Path(segmentation_dir).resolve() if segmentation_dir is not None else session/"segmentation"
        prepared = Path(mesh_dir).resolve() if mesh_dir is not None else session/"mesh_preprocessing"
        if not segmentation.is_relative_to(session) or not prepared.is_relative_to(session):
            raise ValueError("Input stages must be inside the session")
        capture_path = session_file(original, original, "scene.json" if (original/"scene.json").is_file() else "capture.json")
        capture = read_object(capture_path)
        coordinates = capture.get("coordinate_system")
        unit = capture.get("length_unit") if coordinates == "arkit" else coordinates.get("length_unit") if isinstance(coordinates, dict) else None
        if unit != "meter":
            raise ValueError("Scene coordinates must be in meters")
        source_mesh = session_file(original, original, capture.get("mesh"))
        mesh_path = session_file(prepared, prepared, "mesh.ply")
        preparation = read_object(session_file(prepared, prepared, "report.json"))
        if preparation.get("source_sha256") != file_hash(source_mesh):
            raise ValueError("Prepared mesh belongs to different source geometry")
        vertices, _, faces = read_mesh(mesh_path)
        detections_path = session_file(segmentation, segmentation, "detections.json")
        manifest = read_object(detections_path)
        if (manifest.get("schema_version") != "1.0" or manifest.get("source_path_base") != "session_directory"
                or manifest.get("artifact_path_base") != "segmentation_directory" or not isinstance(manifest.get("frames"), list)):
            raise ValueError("Unsupported segmentation manifest")
        if max_frames is not None and (type(max_frames) is not int or max_frames < 1):
            raise ValueError("max_frames must be positive")
        inputs = manifest["frames"][:max_frames]
        if not inputs:
            raise ValueError("No segmented frames")
        fingerprints = {}
        def fingerprint(path):
            fingerprints[path.relative_to(session).as_posix()] = file_hash(path)
        for path in (capture_path, source_mesh, mesh_path, prepared/"report.json", detections_path):
            fingerprint(path)
        observations, frame_results, all_ids, all_weights, offsets = [], [], [], [], [0]
        seen_frames, seen_observations = set(), set()
        with TemporaryDirectory(prefix=".mask-projection-", dir=session) as temporary:
            staging = Path(temporary)/"result"
            (staging/"visibility").mkdir(parents=True)
            for frame in inputs:
                identity = frame.get("frame_id")
                if (not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", identity)
                        or identity.casefold() in seen_frames):
                    raise ValueError("Invalid or duplicate frame_id")
                seen_frames.add(identity.casefold())
                metadata_path = session_file(original, session, frame.get("metadata"))
                rgb_path = session_file(original, session, frame.get("rgb"))
                for path, field in ((metadata_path, "metadata_sha256"), (rgb_path, "rgb_sha256")):
                    fingerprint(path)
                    if fingerprints[path.relative_to(session).as_posix()] != frame.get(field):
                        raise ValueError(f"Source changed after SAM segmentation: {identity}")
                metadata = read_object(metadata_path)
                size = (frame.get("width"), frame.get("height"))
                if metadata.get("frame_id") != identity or size != (metadata["rgb"]["width"], metadata["rgb"]["height"]):
                    raise ValueError("Segmentation image and camera metadata disagree")
                if any(type(v) is not int or v <= 0 for v in size) or size[0]*size[1] > 25_000_000:
                    raise ValueError("Invalid RGB dimensions")
                if session_file(original, metadata_path.parent, metadata["rgb"]["path"]) != rgb_path:
                    raise ValueError("RGB path differs between segmentation and camera metadata")
                with Image.open(rgb_path) as image:
                    if image.size != size:
                        raise ValueError("RGB dimensions differ from camera metadata")
                K, transform = camera_parameters(metadata, coordinates)
                scale = min(self.settings.raster_scale, self.settings.max_image_dimension/max(size))
                raster_size = tuple(max(1, round(v*scale)) for v in size)
                scale_xy = np.array(raster_size)/np.array(size)
                raster_K = K.copy()
                raster_K[:2, :2] *= scale_xy[:, None]
                raster_K[:2, 2] = (K[:2, 2]+.5)*scale_xy-.5
                rendered_depth, face_map = render_visibility(vertices, faces, raster_K, transform, *raster_size)
                visible = face_map >= 0
                depth, confidence, warnings = load_depth(original, metadata_path, metadata, size)
                for name in ("depth", "confidence"):
                    if metadata.get(name) is not None:
                        fingerprint(session_file(original, metadata_path.parent, metadata[name]["path"]))
                agreement = np.zeros(face_map.shape, dtype=bool)
                if depth is not None:
                    depth = resize_nearest(depth, raster_size)
                    valid = np.isfinite(depth) & (depth > 0)
                    if confidence is not None:
                        valid &= resize_nearest(confidence, raster_size) >= self.settings.min_depth_confidence
                    agreement = (visible & valid & (np.abs(rendered_depth-depth) <=
                                 self.settings.depth_abs_tolerance_m+self.settings.depth_relative_tolerance*depth))
                total_per_face = np.bincount(face_map[visible], minlength=len(faces))
                frame_supported = 0
                for detection in frame["detections"]:
                    observation_id = detection.get("observation_id")
                    if not isinstance(observation_id, str) or observation_id in seen_observations:
                        raise ValueError("Invalid or duplicate observation_id")
                    seen_observations.add(observation_id)
                    label, score = detection.get("label"), detection.get("score")
                    if not isinstance(label, str) or not label or type(score) not in (int, float) or not np.isfinite(score) or not 0 <= score <= 1:
                        raise ValueError("Invalid mask class or score")
                    mask_path = session_file(segmentation, segmentation, detection.get("mask"))
                    fingerprint(mask_path)
                    with Image.open(mask_path) as image:
                        if image.size != size or image.mode != "L":
                            raise ValueError("Mask must match RGB pixels")
                        pixels = np.asarray(image)
                    if not np.isin(pixels, (0, 255)).all():
                        raise ValueError("Expected a binary 0/255 PNG mask")
                    mask = pixels != 0
                    if self.settings.mask_erode_pixels:
                        mask = binary_erosion(mask, iterations=self.settings.mask_erode_pixels, border_value=0)
                    mask = resize_nearest(mask.astype(np.uint8), raster_size) != 0
                    mask_pixels = int(mask.sum())
                    supported = mask & agreement
                    supported_pixels = int(supported.sum())
                    fraction = supported_pixels/max(1, mask_pixels)
                    ids, counts = np.unique(face_map[supported], return_counts=True)
                    weights = counts/np.maximum(1, total_per_face[ids])
                    keep = weights >= self.settings.min_face_fraction
                    ids, weights = ids[keep], weights[keep]
                    if fraction < self.settings.min_observation_support or not len(ids):
                        ids, weights = np.array([], dtype=np.int32), np.array([], dtype=float)
                    record = {"observation_id": observation_id, "frame_id": identity, "label": label, "score": score,
                              "status": "supported" if len(ids) else "unconfirmed", "mask": mask_path.relative_to(session).as_posix(),
                              "mask_pixels": mask_pixels, "supported_pixels": supported_pixels,
                              "support_fraction": fraction, "face_count": len(ids),
                              "no_mesh_pixels": int((mask & ~visible).sum()),
                              "depth_rejected_pixels": int((mask & visible & ~agreement).sum())}
                    observations.append(record)
                    all_ids.append(ids.astype(np.int32)); all_weights.append(weights.astype(np.float32))
                    offsets.append(offsets[-1]+len(ids))
                    frame_supported += int(bool(len(ids)))
                raster_path = f"visibility/{len(frame_results):06d}.npz"
                np.savez_compressed(staging/raster_path, face_ids=face_map, depth_m=rendered_depth.astype(np.float32),
                                    depth_supported=agreement)
                frame_results.append({"frame_id": identity, "rgb": frame["rgb"], "metadata": frame["metadata"],
                                      "visibility": raster_path, "width": raster_size[0], "height": raster_size[1],
                                      "warnings": warnings, "supported_observations": frame_supported})
                if progress:
                    progress(len(frame_results), len(inputs), identity, frame_supported)
            np.savez_compressed(staging/"observations.npz", offsets=np.asarray(offsets, dtype=np.int64),
                                face_ids=np.concatenate(all_ids) if all_ids else np.array([], dtype=np.int32),
                                face_weights=np.concatenate(all_weights) if all_weights else np.array([], dtype=np.float32))
            save_json(staging/"observations.json", {"schema_version": "1.0", "observations": observations, "frames": frame_results})
            for relative, expected in fingerprints.items():
                if file_hash(session_file(session, session, relative)) != expected:
                    raise ValueError("Source changed while projecting masks")
            report = {"schema_version": "1.0", "status": "completed", "parameters": asdict(self.settings),
                      "processed_frames": len(inputs), "available_segmented_frames": len(manifest["frames"]),
                      "observation_count": len(observations), "supported_observations": sum(r["status"] == "supported" for r in observations),
                      "mesh": mesh_path.relative_to(session).as_posix(), "mesh_sha256": file_hash(mesh_path),
                      "mesh_faces": len(faces), "input_fingerprints": fingerprints,
                      "artifact_fingerprints": {path.relative_to(staging).as_posix(): file_hash(path)
                                                for path in sorted(staging.rglob("*")) if path.is_file()},
                      "policies": {"depth_required_for_support": True, "instance_ids": "not_yet_associated",
                                   "visibility": "nearest_two_sided_triangle", "mask_edges": "eroded_before_resampling",
                                   "missing_surface": "kept_as_unconfirmed_observation", "outside_room_detection": "not_guaranteed"}}
            save_json(staging/"report.json", report)
            _rename(staging, output)
        return report
