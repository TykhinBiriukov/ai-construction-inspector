"""Segment selected RGB frames and save this stage's artifacts only."""

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

import numpy as np
from PIL import Image, ImageDraw

from geometry_engine.capture.session_layout import prepare_session, original_directory, session_reference
from geometry_engine.capture.validation import read_object, session_file


@dataclass(frozen=True)
class SegmentationSettings:
    prompts: tuple[str, ...] = ("wall", "floor", "ceiling", "window", "window frame", "door")
    min_confidence: float = 0.5
    min_mask_pixels: int = 64
    orientation: str = "gravity"

    def __post_init__(self):
        if (not isinstance(self.prompts, (tuple, list)) or not 1 <= len(self.prompts) <= 32
                or any(not isinstance(p, str) or not p.strip() or p != p.strip()
                       or len(p) > 120 or any(c in p for c in ",;\n\r") for p in self.prompts)
                or len(set(self.prompts)) != len(self.prompts)):
            raise ValueError("prompts must contain 1-32 distinct short concepts, one concept per item")
        object.__setattr__(self, "prompts", tuple(self.prompts))
        if self.orientation not in ("gravity", "native"):
            raise ValueError("orientation must be gravity or native")
        if not np.isfinite(self.min_confidence) or not 0 <= self.min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        if type(self.min_mask_pixels) is not int or self.min_mask_pixels < 1:
            raise ValueError("min_mask_pixels must be a positive integer")


def upright_quarter_turns(metadata, coordinates):
    """CCW image turns from gravity in ARKit camera coordinates (right/up/back).

    Near vertical views have ambiguous roll: retain native orientation there.
    Unknown coordinate conventions are not guessed.
    """
    if coordinates != "arkit":
        return 0
    rotation = np.asarray(metadata["pose"]["T_world_from_camera"], dtype=float)[:3, :3]
    if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
        raise ValueError("Invalid camera pose for image orientation")
    up = rotation.T @ np.array([0., 1., 0.])
    screen_up = np.array([up[0], -up[1]])
    if np.linalg.norm(screen_up) < .25:
        return 0
    # After CCW rotation, an original right vector becomes image-up.
    targets = np.array([[0., -1.], [1., 0.], [0., 1.], [-1., 0.]])
    return int(np.argmax(targets @ screen_up))


def _model_image(rgb_path, metadata_path, size, coordinates, orientation):
    with Image.open(rgb_path) as source:
        if source.size != size:
            raise ValueError("RGB dimensions differ from metadata")
        if source.width * source.height > 25_000_000:
            raise ValueError("RGB exceeds the 25 million pixel limit")
        source.load()
        image = source.convert("RGB")
        image.info.clear()
    metadata = read_object(metadata_path)
    turns = upright_quarter_turns(metadata, coordinates) if orientation == "gravity" else 0
    model_image = Image.fromarray(np.rot90(np.asarray(image), turns).copy())
    return image, model_image, turns


def _save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _previous_frames(output, inputs, settings, model, manifest_hash):
    report = read_object(session_file(output, output, "report.json"))
    saved = read_object(session_file(output, output, "detections.json"))
    parameters = json.loads(json.dumps(asdict(settings)))
    if (report.get("status") != "completed" or report.get("parameters") != parameters
            or report.get("model") != model or report.get("source_manifest_sha256") != manifest_hash):
        raise ValueError("Cannot resume: settings, model or selected_frames manifest changed; use a new --output")
    frames = saved.get("frames")
    if (saved.get("schema_version") != "1.0" or saved.get("source_path_base") != "session_directory"
            or saved.get("artifact_path_base") != "segmentation_directory"
            or not isinstance(frames, list) or report.get("processed_count") != len(frames)):
        raise ValueError("Cannot resume: invalid saved segmentation manifest")
    by_id = {row[0]["frame_id"]: row for row in inputs}
    cached = {}
    for frame in frames:
        identity = frame.get("frame_id") if isinstance(frame, dict) else None
        if identity not in by_id or identity in cached:
            raise ValueError("Cannot resume: saved frames differ from the requested selection; do not lower --max-frames")
        row, rgb, metadata, size = by_id[identity]
        if (frame.get("rgb") != row["rgb"] or frame.get("metadata") != row["metadata"]
                or (frame.get("width"), frame.get("height")) != size
                or frame.get("rgb_sha256") != hashlib.sha256(rgb.read_bytes()).hexdigest()
                or frame.get("metadata_sha256") != hashlib.sha256(metadata.read_bytes()).hexdigest()
                or not isinstance(frame.get("detections"), list)):
            raise ValueError(f"Cannot resume: source data changed for frame {identity}")
        session_file(output, output, frame.get("overlay"))
        for detection in frame["detections"]:
            mask_path = session_file(output, output, detection.get("mask"))
            with Image.open(mask_path) as mask:
                if mask.size != size or not np.isin(np.asarray(mask), (0, 255)).all():
                    raise ValueError(f"Cannot resume: invalid saved mask for frame {identity}")
        cached[identity] = frame
    return cached, report


def _rename(source, destination):
    # Windows antivirus/indexing may briefly hold a newly written directory.
    for attempt in range(4):
        try:
            source.rename(destination)
            return
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.05 * (attempt+1))


def _publish(staging, output, session, replacing):
    if not replacing:
        _rename(staging, output)
        return
    # Both absolute move targets are direct children of this validated session.
    backup = session / f".segmentation-backup-{uuid4().hex}"
    if output.resolve().parent != session or backup.resolve().parent != session:
        raise ValueError("Publication paths must stay inside the session")
    _rename(output, backup)
    try:
        _rename(staging, output)
    except BaseException:
        _rename(backup, output)
        raise
    # The new directory now holds all previous artifacts plus the new frames.
    shutil.rmtree(backup)


def _overlay(image, detections, prompts):
    pixels = np.asarray(image).copy()
    palette = ((255, 150, 40), (45, 190, 255), (100, 225, 90), (225, 90, 220), (255, 225, 70))
    colors = {label: palette[i % len(palette)] for i, label in enumerate(prompts)}
    for row, mask in detections:
        color = colors[row["label"]]
        pixels[mask] = (pixels[mask] * 0.55 + np.asarray(color) * 0.45).astype(np.uint8)
    result = Image.fromarray(pixels)
    draw = ImageDraw.Draw(result)
    for row, _ in detections:
        x1, y1, x2, y2 = row["bbox_xyxy"]
        color = colors[row["label"]]
        draw.rectangle((x1, y1, x2-1, y2-1), outline=color, width=2)
        text = f"{row['label']} {row['score']:.2f}"
        draw.text((x1+2, y1+2), text, fill="white", stroke_width=1, stroke_fill="black")
    return result


class SegmentationService:
    """Inject a backend with predict(PIL RGB image, prompts) -> detections.

    Masks stay in the original image pixel coordinates. Instances are independent
    observations per image, not yet globally associated 3D objects.
    """

    def __init__(self, backend, settings: SegmentationSettings | None = None):
        self.backend = backend
        self.settings = settings or SegmentationSettings()

    def segment(self, directory, output_dir=None, *, max_frames=None, selected_frames=None, progress=None, resume=False) -> dict:
        session = prepare_session(directory)
        original = original_directory(session)
        if not session.is_dir() or not original.is_dir():
            raise ValueError("Session must contain an original directory")
        output = Path(output_dir).resolve() if output_dir is not None else session / "segmentation"
        if output.parent != session or output == original:
            raise ValueError("Segmentation output must be a new directory directly inside the session, outside original")
        replacing = output.exists()
        if replacing and not resume:
            raise FileExistsError(f"Output exists; choose another directory: {output}")
        if max_frames is not None and (type(max_frames) is not int or max_frames < 1):
            raise ValueError("max_frames must be a positive integer")
        index_path = (Path(selected_frames).resolve() if selected_frames is not None
                      else session / "selected_frames" / "selected_frames.json")
        if not index_path.is_relative_to(session) or not index_path.is_file():
            raise ValueError("selected_frames must be an existing manifest inside the session")
        manifest = read_object(index_path)
        frames = manifest.get("frames")
        if (manifest.get("schema_version") != "1.0" or manifest.get("path_base") != "session_directory"
                or not isinstance(frames, list) or not frames
                or type(manifest.get("selected_count")) is not int or manifest["selected_count"] != len(frames)):
            raise ValueError("Expected a nonempty selected_frames v1 manifest with matching selected_count")
        coordinates = None
        for name in ("capture.json", "scene.json"):
            if (original / name).is_file():
                coordinates = read_object(original / name).get("coordinate_system")
                break
        seen, inputs = set(), []
        reserved = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
        for row in frames[:max_frames]:
            if not isinstance(row, dict):
                raise ValueError("Selected frame must be an object")
            identity = row.get("frame_id")
            if (not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", identity)
                    or identity.casefold() in seen or identity.casefold() in reserved):
                raise ValueError("Missing, unsafe or duplicate frame_id")
            seen.add(identity.casefold())
            timestamp = row.get("timestamp_s")
            if timestamp is not None and (type(timestamp) not in (int, float) or not np.isfinite(timestamp)):
                raise ValueError("Frame timestamp must be a finite number")
            rgb_path = session_file(original, session, row.get("rgb"))
            metadata_path = session_file(original, session, row.get("metadata"))
            metadata = read_object(metadata_path)
            rgb = metadata.get("rgb", {})
            if (metadata.get("frame_id") != identity or not isinstance(rgb, dict)
                    or type(rgb.get("width")) is not int or type(rgb.get("height")) is not int
                    or min(rgb["width"], rgb["height"]) < 1
                    or session_file(original, metadata_path.parent, rgb.get("path")) != rgb_path):
                raise ValueError(f"Selected frame and RGB metadata disagree: {identity}")
            inputs.append((row, rgb_path, metadata_path, (rgb["width"], rgb["height"])))

        manifest_hash = hashlib.sha256(index_path.read_bytes()).hexdigest()
        cached, previous = ({}, {})
        if replacing:
            cached, previous = _previous_frames(output, inputs, self.settings,
                                                getattr(self.backend, "model", None), manifest_hash)
            if previous.get("coordinate_system") != coordinates:
                raise ValueError("Cannot resume: camera coordinate convention changed; use a new output")
            identity = getattr(self.backend, "model_info", None)
            if (("inference_identity" in previous or (isinstance(identity, dict)
                    and "inference_cache" in identity)) and previous.get("inference_identity") != identity):
                raise ValueError("Cannot resume: inference identity or cache epoch changed; use a new output")
        if cached and len(cached) == len(inputs):
            return {**previous, "reused_count": len(cached), "newly_processed_count": 0}
        if hasattr(self.backend, "prepare"):
            # Batch cache discovery downloads saved masks before local processing.
            # Keep RGB images lazy so a large session does not exhaust RAM.
            self.backend.prepare((_model_image(rgb, metadata, size, coordinates,
                self.settings.orientation)[1] for row, rgb, metadata, size in inputs
                if row["frame_id"] not in cached), self.settings.prompts)
        results, counts, rejected, empty_frames = [], Counter(), Counter(), []
        rejected.update(previous.get("rejected_counts", {}))
        with TemporaryDirectory(prefix=".segmentation-", dir=session) as temporary:
            staging = Path(temporary) / "result"
            (staging / "masks").mkdir(parents=True)
            (staging / "overlays").mkdir()
            for row, rgb_path, metadata_path, size in inputs:
                identity = row["frame_id"]
                if identity in cached:
                    frame = cached[identity]
                    for name in [frame["overlay"], *[d["mask"] for d in frame["detections"]]]:
                        source_path = session_file(output, output, name)
                        destination = staging / source_path.relative_to(output)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source_path, destination)
                    results.append(frame)
                    counts.update(d["label"] for d in frame["detections"])
                    if not frame["detections"]:
                        empty_frames.append(identity)
                    continue
                image, model_image, turns = _model_image(rgb_path, metadata_path, size, coordinates,
                                                         self.settings.orientation)
                detections = self.backend.predict(model_image, self.settings.prompts)
                saved, rendered = [], []
                for detection in detections:
                    mask = np.asarray(detection.mask)
                    score = float(detection.score)
                    if (mask.shape != (model_image.height, model_image.width) or mask.dtype != np.bool_
                            or detection.label not in self.settings.prompts
                            or not np.isfinite(score) or not 0 <= score <= 1):
                        raise ValueError(f"Invalid model detection for frame {identity}")
                    mask = np.rot90(mask, -turns).copy()
                    label = "window" if detection.label == "window frame" else detection.label
                    area = int(mask.sum())
                    if score < self.settings.min_confidence:
                        rejected["low_confidence"] += 1
                        continue
                    if area < self.settings.min_mask_pixels:
                        rejected["small_mask"] += 1
                        continue
                    ys, xs = np.nonzero(mask)
                    mask_relative = Path("masks") / identity / f"{len(saved):06d}.png"
                    (staging / mask_relative).parent.mkdir(exist_ok=True)
                    Image.fromarray(mask.astype(np.uint8) * 255).save(staging / mask_relative)
                    item = {"observation_id": f"{identity}:{len(saved):06d}", "label": label, "prompt": detection.label,
                            "score": score, "area_pixels": area,
                            "bbox_xyxy": [int(xs.min()), int(ys.min()), int(xs.max())+1, int(ys.max())+1],
                            "mask": mask_relative.as_posix()}
                    provider_id = getattr(detection, "object_id", None)
                    if provider_id is not None:
                        item["provider_object_id"] = str(provider_id)
                    saved.append(item)
                    rendered.append((item, mask))
                    counts[label] += 1
                overlay_relative = Path("overlays") / f"{identity}.jpg"
                output_labels = tuple(dict.fromkeys("window" if p == "window frame" else p for p in self.settings.prompts))
                _overlay(image, rendered, output_labels).save(staging / overlay_relative, quality=90)
                results.append({"frame_id": identity, "timestamp_s": row.get("timestamp_s"),
                                "rgb": session_reference(rgb_path, session),
                                "metadata": session_reference(metadata_path, session),
                                "rgb_sha256": hashlib.sha256(rgb_path.read_bytes()).hexdigest(),
                                "metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
                                "width": image.width, "height": image.height,
                                "model_rotation_ccw_degrees": turns * 90,
                                "overlay": overlay_relative.as_posix(), "detections": saved})
                if not saved:
                    empty_frames.append(identity)
                if progress is not None:
                    progress(len(results), len(inputs), identity, len(saved))
            report = {"schema_version": "1.0", "status": "completed",
                      "session_id": manifest.get("session_id", session.name),
                      "model": getattr(self.backend, "model", None), "parameters": asdict(self.settings),
                      "inference_identity": getattr(self.backend, "model_info", None),
                      "coordinate_system": coordinates,
                      "selected_count": len(frames), "processed_count": len(results),
                      "reused_count": len(cached), "newly_processed_count": len(results)-len(cached),
                      "detection_count": sum(counts.values()), "class_counts": dict(counts),
                      "rejected_counts": dict(rejected), "frames_without_detections": empty_frames,
                      "source_manifest": session_reference(index_path, session),
                      "source_manifest_sha256": manifest_hash,
                      "warnings": ["No detections passed the filters"] if not counts else [],
                      "outputs": {"detections": "detections.json", "masks": "masks", "overlays": "overlays"}}
            _save_json(staging / "detections.json", {"schema_version": "1.0",
                       "session_id": report["session_id"], "source_path_base": "session_directory",
                       "artifact_path_base": "segmentation_directory", "mask_values": [0, 255],
                       "bbox_convention": "xyxy_pixels_max_exclusive", "instance_scope": "per_frame_observation",
                       "frames": results})
            if hasattr(self.backend, "stats"):
                report["inference_cache"] = dict(self.backend.stats)
            _save_json(staging / "report.json", report)
            _publish(staging, output, session, replacing)
        return report
