"""Validate an unpacked session without changing its source files."""

import json
from pathlib import Path, PureWindowsPath

import numpy as np

from .mesh import read_mesh


def session_file(root: Path, base: Path, value) -> Path:
    if not isinstance(value, str) or not value or PureWindowsPath(value).drive:
        raise ValueError("Expected a relative file path")
    relative = Path(value.replace("\\", "/"))
    path = (base / relative).resolve()
    if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(root):
        raise ValueError("Path must stay inside the session directory")
    if not path.is_file():
        raise ValueError(f"File is missing: {value}")
    return path


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


class SessionValidationService:
    def validate(self, directory) -> dict:
        session = Path(directory).resolve()
        if not session.is_dir():
            raise ValueError(f"Session directory does not exist: {session}")
        root = (session / "original").resolve()
        report = {
            "status": "invalid", "errors": [], "mesh": None,
            "frames": {"total": 0, "usable": 0, "incomplete": 0, "issues": []},
        }
        if not root.is_relative_to(session):
            report["errors"].append("original directory must stay inside the session")
            return self._save(session, report)
        try:
            capture_format = not (root / "scene.json").is_file() and (root / "capture.json").is_file()
            manifest = "capture.json" if capture_format else "scene.json"
            scene = read_object(session_file(root, root, manifest))
            if capture_format:
                if (scene.get("schema_version") != "1.0" or scene.get("coordinate_system") != "arkit"
                        or scene.get("matrix_storage") != "rows"):
                    report["errors"].append("Expected capture v1 with ARKit coordinates and row matrices")
                scene = dict(scene, coordinate_system={"length_unit": scene.get("length_unit")})
            coordinates = scene.get("coordinate_system")
            if not isinstance(coordinates, dict) or coordinates.get("length_unit") != "meter":
                report["errors"].append("Scene coordinates must be in meters")
        except (OSError, ValueError) as error:
            report["errors"].append(f"{manifest}: {error}")
            return self._save(session, report)

        try:
            mesh_path = session_file(root, root, scene.get("mesh"))
            vertices, _, faces = read_mesh(mesh_path)
            if not len(vertices) or not len(faces):
                raise ValueError("Mesh contains no surface")
            # Degeneracies and duplicate geometry are handled by preprocessing.
            report["mesh"] = {"path": scene["mesh"], "vertices": len(vertices), "faces": len(faces)}
        except (OSError, ValueError, IndexError) as error:
            report["errors"].append(f"mesh: {error}")

        try:
            seen = set()
            from contextlib import closing
            with closing(self.iter_index_lines(root, scene, capture_format)) as source:
                for line_number, line in enumerate(source, 1):
                    if not line.strip():
                        continue
                    report["frames"]["total"] += 1
                    issues = []
                    frame_id = f"line:{line_number}"
                    try:
                        entry = json.loads(line)
                        if not isinstance(entry, dict):
                            raise ValueError("Frame index entry must be an object")
                        frame_id = entry.get("frame_id")
                        if not isinstance(frame_id, str) or not frame_id or frame_id in seen:
                            raise ValueError("Missing or duplicate frame_id")
                        seen.add(frame_id)
                        metadata_path = session_file(root, root, entry.get("metadata"))
                        metadata = read_object(metadata_path)
                        if metadata.get("frame_id") != frame_id:
                            raise ValueError("frame_id differs from metadata")
                        issues.extend(self.check_frame_metadata(root, metadata_path.parent, metadata))
                    except (OSError, ValueError) as error:
                        issues.append(str(error))
                    if issues:
                        report["frames"]["incomplete"] += 1
                        report["frames"]["issues"].append({"frame_id": frame_id, "issues": issues})
                    else:
                        report["frames"]["usable"] += 1
            if scene.get("frame_count") != report["frames"]["total"]:
                report["errors"].append("frame_count differs from the frame index")
            if not report["frames"]["usable"]:
                report["errors"].append("No usable frames with RGB, intrinsics and pose")
        except (OSError, ValueError) as error:
            report["errors"].append(f"frame_index: {error}")

        if not report["errors"]:
            report["status"] = "valid_with_warnings" if report["frames"]["incomplete"] else "valid"
        return self._save(session, report)

    @staticmethod
    def iter_index_lines(root: Path, scene: dict, capture_format: bool):
        if not capture_format:
            index = session_file(root, root, scene.get("frame_index"))
            with index.open(encoding="utf-8") as source:
                yield from source
            return
        value = scene.get("frames_directory")
        if not isinstance(value, str) or not value or PureWindowsPath(value).drive:
            raise ValueError("Expected a relative frames directory")
        relative = Path(value.replace("\\", "/"))
        folder = (root / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or not folder.is_relative_to(root) or not folder.is_dir():
            raise ValueError("Invalid frames directory")
        for frame in sorted(folder.iterdir()):
            if frame.is_dir():
                yield json.dumps({"frame_id": frame.name, "metadata": (relative / frame.name / "metadata.json").as_posix()})

    @staticmethod
    def check_frame_metadata(root: Path, base: Path, metadata: dict) -> list[str]:
        issues = []
        for name in ("rgb", "depth", "confidence"):
            asset = metadata.get(name)
            if asset is None and name != "rgb":
                continue
            try:
                if not isinstance(asset, dict):
                    raise ValueError("Missing file description")
                path = session_file(root, base, asset.get("path"))
                if name in {"depth", "confidence"} and path.suffix == ".bin":
                    width, height = asset.get("width"), asset.get("height")
                    if type(width) is not int or type(height) is not int or min(width, height) <= 0:
                        raise ValueError("Invalid buffer dimensions")
                    expected_type = "float32" if name == "depth" else "uint8"
                    if asset.get("dtype") != expected_type:
                        raise ValueError("Invalid buffer dtype")
                    if name == "depth" and (asset.get("byte_order") != "little" or asset.get("unit") != "meter"):
                        raise ValueError("Expected little-endian depth in meters")
                    if path.stat().st_size != width * height * (4 if name == "depth" else 1):
                        raise ValueError("Buffer size differs from metadata")
            except (OSError, ValueError) as error:
                issues.append(f"{name}: {error}")
        try:
            intrinsics = metadata.get("intrinsics")
            if not isinstance(intrinsics, dict):
                raise ValueError()
            if "K" in intrinsics and "fx" not in intrinsics:
                k = np.asarray(intrinsics["K"], dtype=float)
                rgb = metadata.get("rgb")
                if (k.shape != (3, 3) or not np.isfinite(k).all() or not isinstance(rgb, dict)
                        or not np.allclose(k[2], [0, 0, 1]) or not np.allclose([k[0,1], k[1,0]], 0)):
                    raise ValueError()
                intrinsics = {"fx": k[0,0], "fy": k[1,1], "cx": k[0,2], "cy": k[1,2],
                              "width": rgb.get("width"), "height": rgb.get("height")}
            values = np.array([intrinsics[key] for key in ("fx", "fy", "cx", "cy", "width", "height")], dtype=float)
            if values.shape != (6,) or not np.isfinite(values).all() or (values[[0, 1, 4, 5]] <= 0).any():
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            issues.append("intrinsics: missing or invalid camera parameters")
        try:
            pose = metadata.get("pose")
            if not isinstance(pose, dict):
                raise ValueError()
            matrix = np.asarray(pose.get("T_world_from_camera"), dtype=float)
            if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
                    or not np.allclose(matrix[3], [0, 0, 0, 1])
                    or not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-4)
                    or not np.isclose(np.linalg.det(matrix[:3, :3]), 1, atol=1e-4)):
                raise ValueError()
        except (TypeError, ValueError):
            issues.append("pose: missing or invalid T_world_from_camera")
        return issues

    @staticmethod
    def _save(session: Path, report: dict) -> dict:
        output = session / "validation"
        output.mkdir(exist_ok=True)
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8",
        )
        return report
