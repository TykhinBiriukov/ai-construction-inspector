"""Read raw ARKit capture v1 without requiring or using a phone-exported mesh.

RGB and depth must be registered, unrotated sensor images with the same aspect
ratio. Intrinsics describe RGB pixels; camera poses use native ARKit axes.
"""

from dataclasses import dataclass, replace
import json
from pathlib import Path, PureWindowsPath

import numpy as np
from PIL import Image
from .session_layout import raw_original_directory


MAX_PIXELS = 25_000_000


@dataclass(frozen=True)
class RawFrame:
    frame_id: str
    timestamp_s: float
    metadata_path: Path
    rgb_path: Path
    depth_path: Path
    confidence_path: Path | None
    K_depth: np.ndarray
    T_world_from_camera_cv: np.ndarray
    width: int
    height: int
    rgb_width: int
    rgb_height: int
    tracking_state: str = "unknown"
    session_id: str = ""


def _object(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def _path(root, base, value, *, directory=False):
    if not isinstance(value, str) or not value or PureWindowsPath(value).drive:
        raise ValueError("Expected a relative capture path")
    relative = Path(value.replace("\\", "/"))
    resolved = (base / relative).resolve()
    if relative.is_absolute() or ".." in relative.parts or not resolved.is_relative_to(root):
        raise ValueError(f"Path must stay inside original: {value}")
    if not (resolved.is_dir() if directory else resolved.is_file()):
        raise ValueError(f"Capture {'directory' if directory else 'file'} missing: {value}")
    return resolved


def _dimensions(asset):
    if not isinstance(asset, dict):
        raise ValueError("Missing image metadata")
    w, h = asset.get("width"), asset.get("height")
    if type(w) is not int or type(h) is not int or min(w, h) < 1 or w*h > MAX_PIXELS:
        raise ValueError("Invalid image dimensions")
    return w, h


def _frame(root, path):
    m = _object(path)
    if m.get("schema_version") != "1.0":
        raise ValueError("Expected raw frame schema_version 1.0")
    identity = m.get("frame_id")
    timestamp = m.get("timestamp_s")
    if not isinstance(identity, str) or not identity or identity != path.parent.name:
        raise ValueError("frame_id must match its directory name")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not np.isfinite(timestamp) or timestamp < 0:
        raise ValueError("Expected finite nonnegative timestamp_s")
    rgb, depth = m.get("rgb"), m.get("depth")
    rw, rh = _dimensions(rgb)
    w, h = _dimensions(depth)
    if rw*h != rh*w:
        raise ValueError("RGB/depth must use registered sensor grids with the same aspect ratio")
    rgb_path = _path(root, path.parent, rgb.get("path"))
    depth_path = _path(root, path.parent, depth.get("path"))
    if depth.get("dtype") != "float32" or depth.get("byte_order") != "little" or depth.get("unit") != "meter":
        raise ValueError("Expected packed little-endian float32 depth in meters")
    if depth_path.stat().st_size != w*h*4:
        raise ValueError("Depth byte count differs from its dimensions")
    confidence_path = None
    confidence = m.get("confidence")
    if confidence is not None:
        if _dimensions(confidence) != (w, h) or confidence.get("dtype") != "uint8":
            raise ValueError("Confidence must be uint8 on the depth pixel grid")
        confidence_path = _path(root, path.parent, confidence.get("path"))
        if confidence_path.stat().st_size != w*h:
            raise ValueError("Confidence byte count differs from its dimensions")
    intrinsics = m.get("intrinsics")
    if _dimensions(intrinsics) != (rw, rh):
        raise ValueError("Camera intrinsics must describe the stored RGB image")
    try:
        K = np.asarray(intrinsics.get("K"), dtype=np.float64)
        T = np.asarray(m["pose"]["T_world_from_camera"], dtype=np.float64)
    except (TypeError, KeyError, ValueError) as error:
        raise ValueError("Missing or invalid camera matrices") from error
    if (K.shape != (3, 3) or not np.isfinite(K).all() or min(K[0, 0], K[1, 1]) <= 0
            or not np.allclose(K[2], [0, 0, 1], atol=1e-8, rtol=0)
            or not np.allclose([K[0, 1], K[1, 0]], 0, atol=1e-8, rtol=0)
            or not (0 <= K[0, 2] < rw and 0 <= K[1, 2] < rh)):
        raise ValueError("Expected calibrated pinhole intrinsics")
    if (T.shape != (4, 4) or not np.isfinite(T).all()
            or not np.allclose(T[3], [0, 0, 0, 1], atol=1e-5, rtol=0)
            or not np.allclose(T[:3, :3].T @ T[:3, :3], np.eye(3), atol=1e-5, rtol=0)
            or not np.isclose(np.linalg.det(T[:3, :3]), 1, atol=1e-5, rtol=0)):
        raise ValueError("Expected a rigid camera-to-world pose")
    T = T.copy()
    T[3] = [0, 0, 0, 1]  # Accept float32 serialization roundoff, not nonrigid poses.
    K = np.diag([w/rw, h/rh, 1.]) @ K
    T = T @ np.diag([1., -1., -1., 1.])  # Open3D: right, down, forward.
    return RawFrame(identity, float(timestamp), path, rgb_path, depth_path, confidence_path,
                    K, T, w, h, rw, rh, str(m.get("tracking_state", "unknown")))


def load_raw_capture(session_dir) -> list[RawFrame]:
    """Validate metadata/buffer sizes and return frames sorted by timestamp.

    Pixel arrays are loaded by ``load_rgbd``. No mesh is read. All parts must use
    the same session, project, coordinate system and world-alignment declaration.
    """
    session = Path(session_dir).resolve()
    root = raw_original_directory(session)
    if not root.is_dir():
        raise ValueError("Expected a session directory containing original/")
    flat = root / "capture.json"
    parts = root / "parts"
    captures = sorted(parts.glob("*/capture.json")) if parts.is_dir() else []
    if flat.is_file() and captures:
        raise ValueError("Ambiguous capture: both flat and multipart inputs exist")
    if flat.is_file():
        captures = [flat]
    if not captures:
        raise ValueError("No capture.json in original/ or original/parts/*/")
    frames, seen_ids, seen_times = [], set(), set()
    semantics = None
    for capture_path in captures:
        if not capture_path.resolve().is_relative_to(root):
            raise ValueError("Capture path escapes original/")
        capture = _object(capture_path)
        if (capture.get("schema_version") != "1.0" or capture.get("coordinate_system") != "arkit"
                or capture.get("length_unit") != "meter" or capture.get("matrix_storage") != "rows"):
            raise ValueError("Expected raw capture v1 with ARKit coordinates, meters and row matrices")
        declared = tuple(capture.get(key) for key in ("session_id", "project_id", "world_alignment"))
        if not isinstance(declared[0], str) or not declared[0]:
            raise ValueError("Missing capture session_id")
        if semantics is not None and declared != semantics:
            raise ValueError("Capture parts have inconsistent session or coordinate declarations")
        semantics = declared
        folder = _path(root, capture_path.parent, capture.get("frames_directory"), directory=True)
        children = sorted(p for p in folder.iterdir() if p.is_dir())
        if type(capture.get("frame_count")) is not int or capture["frame_count"] != len(children):
            raise ValueError("frame_count differs from the number of frame directories")
        for child in children:
            metadata_path = _path(root, child, "metadata.json")
            frame = replace(_frame(root, metadata_path), session_id=declared[0])
            if frame.frame_id in seen_ids or frame.timestamp_s in seen_times:
                raise ValueError("Duplicate frame_id or timestamp_s across capture parts")
            seen_ids.add(frame.frame_id)
            seen_times.add(frame.timestamp_s)
            frames.append(frame)
    if not frames:
        raise ValueError("Capture contains no raw RGB-D frames")
    return sorted(frames, key=lambda frame: frame.timestamp_s)


def load_rgbd(frame: RawFrame, min_confidence=1, min_depth_m=.15, max_depth_m=5):
    """Return registered RGB uint8, meter depth float32 (invalid=0), and counts."""
    if type(min_confidence) is not int or not 0 <= min_confidence <= 2:
        raise ValueError("min_confidence must be 0, 1 or 2")
    if not np.isfinite([min_depth_m, max_depth_m]).all() or not 0 <= min_depth_m < max_depth_m:
        raise ValueError("Expected 0 <= min_depth_m < max_depth_m")
    with Image.open(frame.rgb_path) as image:
        image.load()
        if image.size != (frame.rgb_width, frame.rgb_height):
            raise ValueError("RGB dimensions differ from metadata")
        if image.getexif().get(274, 1) != 1:
            raise ValueError("RGB EXIF orientation must be 1; preserve sensor pixel orientation")
        rgb = np.array(image.convert("RGB").resize((frame.width, frame.height), Image.Resampling.BILINEAR))
    depth = np.fromfile(frame.depth_path, dtype="<f4").reshape(frame.height, frame.width)
    valid = np.isfinite(depth) & (depth >= min_depth_m) & (depth > 0) & (depth <= max_depth_m)
    range_valid = int(valid.sum())
    if frame.confidence_path is not None:
        confidence = np.fromfile(frame.confidence_path, dtype=np.uint8).reshape(depth.shape)
        if np.any(confidence > 2):
            raise ValueError("ARKit confidence values must be 0, 1 or 2")
        valid &= confidence >= min_confidence
    stats = {"pixels": int(depth.size), "range_valid_pixels": range_valid,
             "valid_pixels": int(valid.sum()), "valid_fraction": float(valid.mean()),
             "confidence_available": frame.confidence_path is not None}
    depth[~valid] = 0
    return np.ascontiguousarray(rgb), np.ascontiguousarray(depth, dtype=np.float32), stats
