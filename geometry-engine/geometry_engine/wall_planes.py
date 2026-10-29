"""Detect candidate wall planes in a prepared session and preserve their evidence."""

from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from .mask_projection import file_hash, save_json
from .mesh import read_mesh
from .segmentation import _rename
from .session_layout import prepare_session, original_directory, session_reference
from .validation import read_object, session_file
from .wall_plane_detection import WallPlaneSettings, detect_wall_planes
from .wall_plane_viewer import export_wall_plane_viewer
from .wall_plane_preview import save_wall_plane_preview


def _up_axis(scene, requested):
    if requested is not None:
        if requested not in ("x", "y", "z"):
            raise ValueError("up_axis must be x, y or z")
        return requested, "explicit_parameter"
    coordinates = scene.get("coordinate_system")
    if coordinates == "arkit":
        return "y", "native_arkit_capture_contract"
    if isinstance(coordinates, dict) and coordinates.get("up_axis") in ("x", "y", "z"):
        return coordinates["up_axis"], "scene_coordinate_system"
    if scene.get("dataset") == "ARKitScenes":
        return "z", "arkitscenes_original_world"
    raise ValueError("World up axis is not specified; pass --up-axis x, y or z")


class WallPlaneService:
    def __init__(self, settings=None):
        self.settings = settings or WallPlaneSettings()

    def detect(self, directory, output_dir=None, *, up_axis=None, progress=None):
        session = prepare_session(directory)
        original = original_directory(session)
        output = Path(output_dir).resolve() if output_dir is not None else session / "wall_planes"
        if not original.is_dir() or output.parent != session or output == original:
            raise ValueError("Expected session/original and a stage output directly inside the session")
        if output.exists():
            raise FileExistsError(f"Output exists: {output}; choose a new --output directory")
        prepared = (session / "mesh_preprocessing").resolve()
        if prepared.parent != session:
            raise ValueError("Prepared mesh must stay inside the session")
        capture_path = session_file(original, original,
                          "scene.json" if (original / "scene.json").is_file() else "capture.json")
        scene = read_object(capture_path)
        coordinates = scene.get("coordinate_system")
        unit = scene.get("length_unit") if coordinates == "arkit" else (
            coordinates.get("length_unit") if isinstance(coordinates, dict) else None)
        if unit != "meter":
            raise ValueError("Scene coordinates must be in meters")
        up_axis, axis_source = _up_axis(scene, up_axis)
        mesh_path = session_file(prepared, prepared, "mesh.ply")
        source_mesh = session_file(original, original, scene.get("mesh"))
        prepared_report_path = session_file(prepared, prepared, "report.json")
        preparation = read_object(prepared_report_path)
        if preparation.get("source_sha256") != file_hash(source_mesh):
            raise ValueError("Prepared mesh belongs to a different original mesh")
        mesh_sha256 = file_hash(mesh_path)
        inputs = {session_reference(path, session): file_hash(path)
                  for path in (capture_path, source_mesh, mesh_path, prepared_report_path)}
        vertices, _, faces = read_mesh(mesh_path)
        semantic_classes, labels, wall_mask = None, None, None
        segmented = (session / "mesh_segmentation").resolve()
        if not segmented.is_dir():
            raise ValueError("Saved SAM mesh labels are required; run mesh segmentation before wall plane detection")
        if segmented.exists():
            if segmented.parent != session:
                raise ValueError("Segmentation must stay inside the session")
            report_path = session_file(segmented, segmented, "report.json")
            segmentation = read_object(report_path)
            if segmentation.get("source_mesh_sha256") != mesh_sha256:
                raise ValueError("SAM mesh labels refer to a different prepared mesh")
            object_path = session_file(segmented, segmented, "objects.json")
            labels = read_object(object_path).get("labels")
            if (not isinstance(labels, list) or any(not isinstance(name, str) for name in labels)
                    or len(set(labels)) != len(labels)):
                raise ValueError("Invalid semantic class names")
            label_path = session_file(segmented, segmented, "labels.npz")
            with np.load(label_path, allow_pickle=False) as arrays:
                semantic_classes = arrays["face_class_id"]
            if (semantic_classes.shape != (len(faces),) or not np.issubdtype(semantic_classes.dtype, np.integer)
                    or np.any((semantic_classes < -1) | (semantic_classes >= len(labels)))):
                raise ValueError("Invalid semantic face labels")
            wall_ids = [index for index, name in enumerate(labels) if name.casefold() == "wall"]
            wall_mask = np.isin(semantic_classes, wall_ids)
            for path in (report_path, object_path, label_path):
                inputs[session_reference(path, session)] = file_hash(path)
        if progress:
            progress(f"Mesh: {len(vertices)} vertices, {len(faces)} triangles; up axis: {up_axis.upper()}")
        result = detect_wall_planes(vertices, faces, wall_mask, up_axis=up_axis,
                                   settings=self.settings, progress=progress)
        planes, assignments = result["planes"], result["face_plane_id"]
        if np.any((assignments >= 0) & ~wall_mask):
            raise RuntimeError("Plane support includes faces without the SAM wall label")
        with TemporaryDirectory(prefix=".wall-planes-", dir=session) as temporary:
            staging = Path(temporary) / "result"
            staging.mkdir()
            save_json(staging / "planes.json", {
                "schema_version": "1.0", "unit": "meter", "up_axis": up_axis,
                "plane_equation": "normal dot point_world + offset_m = 0",
                "extent_policy": "support_rectangle_for_visualization_not_physical_wall_boundary",
                "mesh": session_reference(mesh_path, session), "mesh_sha256": mesh_sha256,
                "planes": planes,
            })
            np.savez_compressed(staging / "face_labels.npz", face_plane_id=assignments)
            if progress:
                progress(f"Exporting overlay for {len(planes)} planes")
            export_wall_plane_viewer(staging / "viewer.html", vertices, faces, assignments, planes,
                                    up_axis=up_axis, semantic_classes=semantic_classes, labels=labels)
            save_wall_plane_preview(staging / "preview.png", vertices, faces, assignments, planes, up_axis=up_axis)
            for relative, expected in inputs.items():
                if file_hash(session_file(session, session, relative)) != expected:
                    raise ValueError(f"Input changed during plane detection: {relative}")
            report = {
                "schema_version": "1.0", "status": "completed", "session_id": session.name,
                "parameters": asdict(self.settings), "up_axis": up_axis, "up_axis_source": axis_source,
                "input_fingerprints": inputs, "source_mesh_sha256": mesh_sha256,
                "total_vertices": len(vertices), "total_faces": len(faces), "plane_count": len(planes),
                "wall_candidate_count": sum(p["status"] == "wall_candidate" for p in planes),
                "assigned_faces": int(np.count_nonzero(assignments >= 0)),
                "sam_wall_filter_applied": True, "sam_wall_faces": int(wall_mask.sum()),
                "diagnostics": result["diagnostics"],
                "policies": {"geometry": "unchanged", "input": "sam_wall_labeled_faces_only",
                             "semantic_labels": "hard_filter_for_sampling_fitting_and_support",
                             "unassigned_face_id": -1, "display_rectangles": "not_wall_boundaries",
                             "hole_filling": False, "openings_reconstructed": False,
                             "sam_misclassification_may_affect_planes": True,
                             "model_requests": 0},
                "artifacts": {"planes": "planes.json", "face_labels": "face_labels.npz",
                              "viewer": "viewer.html", "preview": "preview.png"},
            }
            save_json(staging / "report.json", report)
            _rename(staging, output)
        return report
