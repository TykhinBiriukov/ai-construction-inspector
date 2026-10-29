"""Project saved SAM masks, fuse views, and label an unchanged prepared mesh."""

from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .mask_fusion import FusionSettings, fuse_observations
from .mask_projection import MaskProjectionService, ProjectionSettings, file_hash, save_json
from .mesh import read_mesh
from .segmentation import _rename
from .segmented_mesh_export import UNKNOWN_COLOR, class_colors, export_segmented_mesh
from .session_layout import prepare_session, original_directory, session_reference
from .validation import read_object, session_file


def _verify_inputs(session, report):
    fingerprints = report.get("input_fingerprints")
    if not isinstance(fingerprints, dict) or not fingerprints:
        raise ValueError("Projection is missing its input fingerprints")
    for relative, expected in fingerprints.items():
        if file_hash(session_file(session, session, relative)) != expected:
            raise ValueError(f"Projection input changed: {relative}. Use a new projection directory.")


def _load_observations(projection, face_count):
    manifest = read_object(session_file(projection, projection, "observations.json"))
    rows = manifest.get("observations")
    if manifest.get("schema_version") != "1.0" or not isinstance(rows, list) or not isinstance(manifest.get("frames"), list):
        raise ValueError("Unsupported projected observation manifest")
    with np.load(session_file(projection, projection, "observations.npz"), allow_pickle=False) as arrays:
        offsets, ids, weights = (arrays[name] for name in ("offsets", "face_ids", "face_weights"))
    if (offsets.shape != (len(rows)+1,) or not np.issubdtype(offsets.dtype, np.integer)
            or offsets[0] != 0 or np.any(np.diff(offsets) < 0) or offsets[-1] != len(ids)
            or ids.ndim != 1 or not np.issubdtype(ids.dtype, np.integer)
            or np.any((ids < 0) | (ids >= face_count)) or weights.shape != ids.shape
            or not np.isfinite(weights).all() or np.any((weights < 0) | (weights > 1))):
        raise ValueError("Invalid sparse projection arrays")
    observations = []
    for index, row in enumerate(rows):
        start, end = offsets[index:index+2]
        observations.append({**row, "face_ids": ids[start:end], "face_weights": weights[start:end]})
    return manifest, observations


def _verify_projection(projection, report):
    fingerprints = report.get("artifact_fingerprints")
    if not isinstance(fingerprints, dict) or not {"observations.json", "observations.npz"}.issubset(fingerprints):
        raise ValueError("Projection cache lacks artifact hashes. Use a new --projection-dir.")
    for relative, expected in fingerprints.items():
        if file_hash(session_file(projection, projection, relative)) != expected:
            raise ValueError(f"Projection cache changed: {relative}. Use a new --projection-dir.")


def _preview(session, projection, staging, manifest, result):
    """Camera-aligned RGB/label pairs; no new reconstruction or remote calls."""
    frames = manifest["frames"]
    palette = class_colors(result["labels"])
    classes = result["face_class_id"]
    # Prefer views containing multiple supported classes, then diversify frame IDs.
    frame_labels = {}
    for row in manifest["observations"]:
        if row["face_count"]:
            frame_labels.setdefault(row["frame_id"], set()).add(row["label"])
    ranked = sorted(range(len(frames)), key=lambda i: (-len(frame_labels.get(frames[i]["frame_id"], set())), i))
    chosen = []
    gap = max(1, len(frames)//8)
    for index in ranked:
        if not chosen or all(abs(index-other) >= gap for other in chosen):
            chosen.append(index)
        if len(chosen) == min(4, len(frames)):
            break
    for index in ranked:
        if len(chosen) >= min(4, len(frames)):
            break
        if index not in chosen:
            chosen.append(index)
    width, height, caption = 512, 384, 30
    canvas = Image.new("RGB", (width*2, 58+len(chosen)*(height+caption)), "#171b23")
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 10), "RGB", fill="white")
    draw.text((width+12, 10), "MESH CLASSES (same camera)", fill="white")
    x = 12
    for label, color in [("unknown", UNKNOWN_COLOR), *zip(result["labels"], palette)]:
        draw.rectangle((x, 34, x+12, 46), fill=tuple(map(int, color)))
        draw.text((x+18, 34), label, fill="white")
        x += 140
    for row_index, index in enumerate(chosen):
        frame = frames[index]
        with np.load(session_file(projection, projection, frame["visibility"]), allow_pickle=False) as arrays:
            face_map = arrays["face_ids"]
        if face_map.shape != (frame["height"], frame["width"]) or np.any((face_map < -1) | (face_map >= len(classes))):
            raise ValueError("Invalid visibility map")
        pixels = np.full((*face_map.shape, 3), 26, dtype=np.uint8)
        visible = face_map >= 0
        pixels[visible] = UNKNOWN_COLOR
        labels = np.full(face_map.shape, -1, dtype=np.int32)
        labels[visible] = classes[face_map[visible]]
        known = labels >= 0
        pixels[known] = palette[labels[known]]
        y = 58+row_index*(height+caption)
        with Image.open(session_file(original_directory(session), session, frame["rgb"])) as photo:
            photo = ImageOps.contain(photo.convert("RGB"), (width, height), Image.Resampling.LANCZOS)
            canvas.paste(photo, ((width-photo.width)//2, y+(height-photo.height)//2))
        colored = ImageOps.contain(Image.fromarray(pixels), (width, height), Image.Resampling.NEAREST)
        canvas.paste(colored, (width+(width-colored.width)//2, y+(height-colored.height)//2))
        draw.text((12, y+height+7), f"Frame {frame['frame_id']} | gray = insufficient or conflicting evidence", fill="#d3dae6")
    canvas.save(staging/"preview.jpg", quality=92)


class MeshSegmentationService:
    """A local geometry stage consuming existing masks; requires no model key."""

    def __init__(self, projection_settings=None, fusion_settings=None):
        self.projection_settings = projection_settings or ProjectionSettings()
        self.fusion_settings = fusion_settings or FusionSettings()

    def segment(self, directory, output_dir=None, *, projection_dir=None, max_frames=None, progress=None):
        session = prepare_session(directory)
        original = original_directory(session)
        output = Path(output_dir).resolve() if output_dir is not None else session/"mesh_segmentation"
        projection = Path(projection_dir).resolve() if projection_dir is not None else session/"mask_projection"
        if (not original.is_dir() or output.parent != session
                or projection.parent != session or output == original or projection == original or output == projection):
            raise ValueError("Stage directories must be distinct direct children of the session, outside original")
        if output.exists():
            raise FileExistsError(f"Output exists: {output}")
        if max_frames is not None and (type(max_frames) is not int or max_frames < 1):
            raise ValueError("max_frames must be positive")
        reused_projection = projection.exists()
        if not reused_projection:
            MaskProjectionService(self.projection_settings).project(session, projection,
                                      max_frames=max_frames, progress=progress)
        projection_report_path = session_file(projection, projection, "report.json")
        projection_report = read_object(projection_report_path)
        if (projection_report.get("status") != "completed" or projection_report.get("schema_version") != "1.0"
                or projection_report.get("parameters") != asdict(self.projection_settings)):
            raise ValueError("Projection settings differ. Use a new --projection-dir.")
        available = projection_report.get("available_segmented_frames")
        if type(available) is not int or available < 1:
            raise ValueError("Invalid projected frame count")
        expected_count = min(max_frames, available) if max_frames is not None else available
        if projection_report.get("processed_frames") != expected_count:
            raise ValueError("Projection contains a different frame subset. Use a new --projection-dir.")
        _verify_inputs(session, projection_report)
        _verify_projection(projection, projection_report)
        mesh_path = session_file(session, session, projection_report.get("mesh"))
        if file_hash(mesh_path) != projection_report.get("mesh_sha256"):
            raise ValueError("Mesh changed after projection")
        vertices, _, faces = read_mesh(mesh_path)
        if len(faces) != projection_report.get("mesh_faces"):
            raise ValueError("Mesh face count differs from projection")
        manifest, observations = _load_observations(projection, len(faces))
        if len(manifest["frames"]) != expected_count or len(observations) != projection_report.get("observation_count"):
            raise ValueError("Projection manifest count mismatch")
        triangles = vertices[faces]
        areas = np.linalg.norm(np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0]), axis=1)*.5
        result = fuse_observations(faces, areas, observations, self.fusion_settings)
        known = result["face_class_id"] >= 0
        by_class = []
        for class_id, label in enumerate(result["labels"]):
            selected = result["face_class_id"] == class_id
            by_class.append({"class_id": class_id, "label": label, "face_count": int(selected.sum()),
                             "surface_area_m2": float(areas[selected].sum())})
        with TemporaryDirectory(prefix=".mesh-segmentation-", dir=session) as temporary:
            staging = Path(temporary)/"result"
            staging.mkdir()
            np.savez_compressed(staging/"labels.npz", **{name: result[name] for name in
                                ("face_class_id", "face_instance_id", "face_confidence", "face_view_count")})
            save_json(staging/"objects.json", {"schema_version": "1.0", "labels": result["labels"],
                       "objects": result["objects"], "observation_instance_ids": result["observation_instance_ids"],
                       "unconfirmed_observations": [row["observation_id"] for row in observations if not len(row["face_ids"])]})
            artifacts = export_segmented_mesh(staging, vertices, faces, result["face_class_id"],
                        result["face_instance_id"], result["face_confidence"], result["labels"], result["objects"])
            _preview(session, projection, staging, manifest, result)
            report = {"schema_version": "1.0", "status": "completed", "session_id": session.name,
                      "processed_frames": expected_count, "projection_reused": reused_projection,
                      "projection": session_reference(projection, session), "mesh": session_reference(mesh_path, session),
                      "source_mesh_sha256": projection_report["mesh_sha256"], "parameters": asdict(self.fusion_settings),
                      "total_vertices": len(vertices), "total_faces": len(faces), "labeled_faces": int(known.sum()),
                      "unknown_faces": int((~known).sum()), "labeled_surface_area_m2": float(areas[known].sum()),
                      "conflicting_class_faces": result["class_conflict_count"],
                      "insufficient_view_faces": result["insufficient_views_count"],
                      "total_surface_area_m2": float(areas.sum()), "classes": by_class,
                      "candidate_objects": len(result["objects"]), "observation_count": len(observations),
                      "supported_observations": projection_report["supported_observations"],
                      "unconfirmed_observations": sum(not len(row["face_ids"]) for row in observations),
                      "artifacts": {**artifacts, "labels": "labels.npz", "objects": "objects.json", "preview": "preview.jpg"},
                      "projection_fingerprints": {name: file_hash(projection/name) for name in
                                                  ("report.json", "observations.json", "observations.npz")},
                      "policies": {"geometry": "unchanged_coordinates_and_face_order", "unknown_id": -1,
                                   "instances": "candidate_associations_not_guaranteed_physical_objects",
                                   "confidence": "agreement_score_not_calibrated_probability",
                                   "missing_surfaces": "not_reconstructed", "outside_room_filter": "not_guaranteed",
                                   "model_requests": 0}}
            _verify_inputs(session, projection_report)
            _verify_projection(projection, projection_report)
            save_json(staging/"report.json", report)
            _rename(staging, output)
        return report
