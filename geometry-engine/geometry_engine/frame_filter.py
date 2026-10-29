"""Select sharp, distinct camera views without modifying or copying source frames.

Pose metric follows the approach in hloc/pairs_from_poses.py: camera-center
distance and angle between optical axes (in-plane roll is ignored). This is
an independent sequential implementation, not hloc's pair-selection algorithm.
"""

from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image, UnidentifiedImageError

from .session_layout import prepare_session, original_directory, session_reference
from .validation import SessionValidationService, read_object, session_file


@dataclass(frozen=True)
class FrameFilterSettings:
    min_translation_m: float = 0.30
    min_view_angle_deg: float = 15.0
    min_sharpness: float = 20.0
    min_brightness: float = 10.0
    max_brightness: float = 245.0
    quality_max_dimension: int = 640

    def __post_init__(self):
        values = [self.min_translation_m, self.min_view_angle_deg, self.min_sharpness,
                  self.min_brightness, self.max_brightness]
        if not np.isfinite(values).all():
            raise ValueError("Frame filter thresholds must be finite")
        if self.min_translation_m <= 0 or not 0 < self.min_view_angle_deg <= 180 or self.min_sharpness < 0:
            raise ValueError("Invalid motion or sharpness thresholds")
        if not 0 <= self.min_brightness < self.max_brightness <= 255:
            raise ValueError("Brightness thresholds must satisfy 0 <= min < max <= 255")
        if type(self.quality_max_dimension) is not int or self.quality_max_dimension < 3:
            raise ValueError("quality_max_dimension must be an integer >= 3")


def camera_motion(reference, current):
    """Return translation in meters and optical-axis angle in degrees."""
    distance = float(np.linalg.norm(current[:3, 3] - reference[:3, 3]))
    # A consistent sign change of the camera's Z axis does not change this angle.
    a, b = reference[:3, 2], current[:3, 2]
    cosine = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
    angle = float(np.rad2deg(np.arccos(np.clip(cosine, -1., 1.))))
    return distance, angle


def image_quality(path: Path, expected_size, max_dimension: int) -> dict:
    with Image.open(path) as image:
        image.load()  # Decode now: an existing but corrupt image is not usable.
        if image.size != expected_size:
            raise ValueError("RGB dimensions differ from metadata")
        gray = image.convert("L")
        gray.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        pixels = np.asarray(gray, dtype=float)
    if min(pixels.shape) < 3:
        raise ValueError("Image is too small for sharpness assessment")
    laplacian = (pixels[:-2, 1:-1] + pixels[2:, 1:-1] + pixels[1:-1, :-2]
                 + pixels[1:-1, 2:] - 4*pixels[1:-1, 1:-1])
    return {"sharpness": float(laplacian.var()), "brightness": float(pixels.mean()),
            "assessment_width": pixels.shape[1], "assessment_height": pixels.shape[0]}


class FrameFilterService:
    def __init__(self, settings: FrameFilterSettings | None = None):
        self.settings = settings or FrameFilterSettings()

    def filter(self, directory, output_dir=None) -> dict:
        session = prepare_session(directory)
        original = original_directory(session)
        output = Path(output_dir).resolve() if output_dir is not None else session / "selected_frames"
        if not original.is_dir():
            raise ValueError("Session must contain an original directory")
        if not output.is_relative_to(session) or output.is_relative_to(original):
            raise ValueError("Frame filter output must be inside the session and outside original")
        if output.exists():
            raise FileExistsError(f"Output exists; choose another directory: {output}")
        capture = not (original / "scene.json").is_file() and (original / "capture.json").is_file()
        manifest = read_object(session_file(original, original, "capture.json" if capture else "scene.json"))
        if capture:
            if (manifest.get("schema_version") != "1.0" or manifest.get("coordinate_system") != "arkit"
                    or manifest.get("matrix_storage") != "rows" or manifest.get("length_unit") != "meter"):
                raise ValueError("Expected capture v1 with ARKit coordinates, row matrices and meters")
        elif manifest.get("coordinate_system", {}).get("length_unit") != "meter":
            raise ValueError("Expected scene coordinates in meters")

        decisions, candidates, seen = [], [], set()
        for line_number, line in enumerate(SessionValidationService.iter_index_lines(original, manifest, capture), 1):
            if not line.strip():
                continue
            decision = {"frame_id": f"line:{line_number}", "selected": False}
            decisions.append(decision)
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    raise ValueError("Frame index entry must be an object")
                identity = entry.get("frame_id")
                if not isinstance(identity, str) or not identity or identity in seen:
                    raise ValueError("Missing or duplicate frame_id")
                seen.add(identity)
                decision["frame_id"] = identity
                path = session_file(original, original, entry.get("metadata"))
                metadata = read_object(path)
                if metadata.get("frame_id") != identity:
                    raise ValueError("frame_id differs from metadata")
                issues = SessionValidationService.check_frame_metadata(original, path.parent, metadata)
                if issues:
                    raise ValueError("; ".join(issues))
                timestamp = metadata.get("timestamp_s")
                if type(timestamp) not in (float, int) or not np.isfinite(timestamp):
                    raise ValueError("Missing or nonfinite timestamp_s")
                rgb = metadata["rgb"]
                rgb_path = session_file(original, path.parent, rgb["path"])
                if type(rgb.get("width")) is not int or type(rgb.get("height")) is not int:
                    raise ValueError("Missing RGB dimensions")
                decision.update(timestamp_s=float(timestamp), metadata=session_reference(path, session),
                                rgb=session_reference(rgb_path, session))
                candidates.append((float(timestamp), identity, metadata, rgb_path, decision))
            except (OSError, ValueError, TypeError, KeyError) as error:
                decision.update(reason="invalid_metadata", detail=str(error))

        last_pose, last_id, selected = None, None, []
        for _, identity, metadata, rgb_path, decision in sorted(candidates, key=lambda item: (item[0], item[1])):
            try:
                quality = image_quality(rgb_path, (metadata["rgb"]["width"], metadata["rgb"]["height"]),
                                        self.settings.quality_max_dimension)
            except (OSError, ValueError, UnidentifiedImageError) as error:
                decision.update(reason="invalid_image", detail=str(error))
                continue
            decision["quality"] = quality
            if quality["brightness"] < self.settings.min_brightness:
                decision["reason"] = "too_dark"
                continue
            if quality["brightness"] > self.settings.max_brightness:
                decision["reason"] = "too_bright"
                continue
            if quality["sharpness"] < self.settings.min_sharpness:
                decision["reason"] = "blurry"
                continue
            pose = np.asarray(metadata["pose"]["T_world_from_camera"], dtype=float)
            if last_pose is None:
                decision["reason"] = "first_usable_frame"
            else:
                distance, angle = camera_motion(last_pose, pose)
                decision.update(reference_frame_id=last_id, translation_m=distance, view_angle_deg=angle)
                reasons = []
                if distance >= self.settings.min_translation_m:
                    reasons.append("translation")
                if angle + 1e-9 >= self.settings.min_view_angle_deg:
                    reasons.append("view_direction")
                if not reasons:
                    decision["reason"] = "redundant_view"
                    continue
                decision["reason"] = "+".join(reasons)
            decision["selected"] = True
            decision["tracking_state"] = metadata.get("tracking_state", "unknown")
            selected.append(dict(decision))
            last_pose, last_id = pose, identity

        report = {"schema_version": "1.0", "status": "completed" if selected else "failed",
                  "parameters": asdict(self.settings), "total_frames": len(decisions),
                  "selected_count": len(selected), "rejected_count": len(decisions)-len(selected),
                  "reason_counts": dict(Counter(row["reason"] for row in decisions)),
                  "source_frame_count": manifest.get("frame_count"),
                  "warnings": [], "decisions": decisions,
                  "policies": {"comparison": "last_selected_frame", "rotation": "optical_axis_angle_roll_ignored",
                               "source_files_modified": False, "quality_metric": "variance_of_4_neighbor_laplacian",
                               "tracking_state": "recorded_not_gated_when_unavailable"}}
        if manifest.get("frame_count") != len(decisions):
            report["warnings"].append("Manifest frame_count differs from the inspected index")
        if not selected:
            report["error"] = "No usable frames passed the quality thresholds"
        manifest_out = {"schema_version": "1.0", "session_id": manifest.get("session_id", manifest.get("scene_id", session.name)),
                        "path_base": "session_directory", "selected_count": len(selected), "frames": selected}
        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".frame-filter-", dir=session) as temporary:
            staging = Path(temporary) / "result"
            staging.mkdir()
            for filename, value in (("selected_frames.json", manifest_out), ("report.json", report)):
                (staging / filename).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")
            staging.rename(output)
        if not selected:
            raise ValueError(f"No usable frames; see {output / 'report.json'}")
        return report
