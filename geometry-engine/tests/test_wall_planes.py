"""Session-level plane detection checks with known walls and a horizontal floor."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from geometry_engine.mesh import read_mesh, write_mesh
from geometry_engine.preprocessing import preprocess_mesh
from geometry_engine.wall_plane_detection import WallPlaneSettings
from geometry_engine.wall_planes import WallPlaneService


ENGINE = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class WallPlaneServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ENGINE)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = self.make_session("scene")
        self.settings = WallPlaneSettings(sample_count=1200, num_iterations=300, max_planes=4, seed=4)
        self.service = WallPlaneService(self.settings)

    def make_session(self, name, *, phone=False):
        session = self.root / name
        original = session / "original"
        original.mkdir(parents=True)
        vertices, faces = [], []

        def rectangle(origin, horizontal, vertical):
            start = len(vertices)
            for row in range(7):
                for col in range(9):
                    vertices.append(np.asarray(origin) + col/8*np.asarray(horizontal) + row/6*np.asarray(vertical))
            for row in range(6):
                for col in range(8):
                    a = start + row*9 + col
                    faces.extend(((a, a+1, a+10), (a, a+10, a+9)))

        rectangle((0, 0, 0), (0, 3, 0), (0, 0, 2.6))
        rectangle((0, 3, 0), (4, 0, 0), (0, 0, 2.6))
        rectangle((0, 0, 0), (4, 0, 0), (0, 3, 0))
        vertices, faces = np.asarray(vertices, dtype=float), np.asarray(faces, dtype=np.int32)
        if phone:
            vertices = vertices[:, [0, 2, 1]]
        write_mesh(original / "mesh.ply", vertices, np.full((len(vertices), 4), 255, dtype=np.uint8), faces)
        if phone:
            manifest = {"schema_version": "1.0", "session_id": name, "mesh": "mesh.ply",
                        "coordinate_system": "arkit", "length_unit": "meter", "matrix_storage": "rows"}
        else:
            manifest = {"schema_version": "1.0", "scene_id": name, "dataset": "ARKitScenes", "mesh": "mesh.ply",
                        "coordinate_system": {"length_unit": "meter", "camera_axes": {"x": "right", "y": "down", "z": "forward"}}}
        write_json(original / ("capture.json" if phone else "scene.json"), manifest)
        preprocess_mesh(original / "mesh.ply", session / "mesh_preprocessing")
        self.add_wall_labels(session, phone=phone)
        return session

    def add_wall_labels(self, session, *, phone=False):
        mesh_path = session / "mesh_preprocessing/mesh.ply"
        vertices, _, faces = read_mesh(mesh_path)
        axis = 1 if phone else 2
        floor = np.max(np.abs(vertices[faces][:, :, axis]), axis=1) < 1e-8
        classes = np.where(floor, -1, 0).astype(np.int32)
        segmentation = session / "mesh_segmentation"
        segmentation.mkdir(exist_ok=True)
        write_json(segmentation / "report.json", {"status": "completed", "source_mesh_sha256": file_hash(mesh_path)})
        write_json(segmentation / "objects.json", {"labels": ["wall", "window", "door"]})
        np.savez_compressed(segmentation / "labels.npz", face_class_id=classes)
        return classes

    def add_partial_sam_labels(self):
        mesh_path = self.session / "mesh_preprocessing/mesh.ply"
        vertices, _, faces = read_mesh(mesh_path)
        center = vertices[faces].mean(axis=1)
        classes = np.full(len(faces), -1, dtype=np.int32)
        classes[(np.abs(center[:, 0]) < 1e-8) & (center[:, 2] > 1.3)] = 0
        segmentation = self.session / "mesh_segmentation"
        segmentation.mkdir(exist_ok=True)
        write_json(segmentation / "report.json", {"status": "completed", "source_mesh_sha256": file_hash(mesh_path)})
        write_json(segmentation / "objects.json", {"labels": ["wall"]})
        np.savez_compressed(segmentation / "labels.npz", face_class_id=classes)
        return classes

    def read_result(self, session=None, output="wall_planes"):
        folder = (session or self.session) / output
        with np.load(folder / "face_labels.npz", allow_pickle=False) as archive:
            assignments = archive["face_plane_id"].copy()
        planes = json.loads((folder / "planes.json").read_text(encoding="utf-8"))
        return planes, assignments

    def test_only_wall_labeled_faces_support_planes_and_inputs_are_preserved(self):
        inputs = [path for path in self.session.rglob("*") if path.is_file()]
        before = {path: file_hash(path) for path in inputs}
        report = self.service.detect(self.session)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["plane_count"], 2)
        self.assertEqual(report["up_axis"], "z")
        self.assertTrue(report["sam_wall_filter_applied"])
        self.assertEqual(report["wall_candidate_count"], 2)
        data, assignments = self.read_result()
        vertices, _, faces = read_mesh(self.session / "mesh_preprocessing/mesh.ply")
        floor = np.max(np.abs(vertices[faces, 2]), axis=1) < 1e-8
        np.testing.assert_array_equal(assignments[floor], -1)
        self.assertTrue(np.all(assignments[~floor] >= 0))
        self.assertEqual(assignments.shape, (len(faces),))
        self.assertEqual(assignments.dtype, np.int32)
        self.assertEqual(len(data["planes"]), 2)
        self.assertIn("not_physical_wall_boundary", data["extent_policy"])
        for plane in data["planes"]:
            normal, offset = np.asarray(plane["normal"]), plane["offset_m"]
            supported = vertices[faces[assignments == plane["plane_id"]]]
            np.testing.assert_allclose(supported @ normal + offset, 0, atol=1e-9)
            np.testing.assert_allclose(np.asarray(plane["corners_world"]) @ normal + offset, 0, atol=1e-9)
        for name in ("planes.json", "face_labels.npz", "viewer.html", "preview.png", "report.json"):
            self.assertGreater((self.session / "wall_planes" / name).stat().st_size, 0, name)
        with Image.open(self.session / "wall_planes/preview.png") as image:
            image.verify()
        self.assertEqual({path: file_hash(path) for path in inputs}, before)

    def test_partial_sam_mask_limits_plane_support_to_labeled_triangles(self):
        classes = self.add_partial_sam_labels()
        report = self.service.detect(self.session)
        self.assertTrue(report["sam_wall_filter_applied"])
        self.assertEqual(report["plane_count"], 1)
        self.assertEqual(report["wall_candidate_count"], 1)
        data, assignments = self.read_result()
        self.assertGreater(np.count_nonzero(assignments >= 0), 0)
        self.assertEqual(np.count_nonzero((assignments >= 0) & (classes != 0)), 0)
        self.assertEqual({plane["status"] for plane in data["planes"]}, {"wall_candidate"})
        self.assertTrue(all(plane["semantic_wall_fraction"] == 1 for plane in data["planes"]))

    def test_other_classes_cannot_become_plane_support(self):
        mesh_path = self.session / "mesh_preprocessing/mesh.ply"
        vertices, _, faces = read_mesh(mesh_path)
        classes = np.where(np.abs(vertices[faces].mean(axis=1)[:, 0]) < 1e-8, 0, 2).astype(np.int32)
        np.savez_compressed(self.session / "mesh_segmentation/labels.npz", face_class_id=classes)
        self.service.detect(self.session)
        _, assignments = self.read_result()
        self.assertTrue(np.all(assignments[classes != 0] == -1))

    def test_missing_sam_labels_is_rejected_without_geometry_fallback(self):
        (self.session / "mesh_segmentation").rename(self.session / "saved-segmentation")
        with self.assertRaisesRegex(ValueError, "SAM mesh labels are required"):
            self.service.detect(self.session)
        self.assertFalse((self.session / "wall_planes").exists())

    def test_repeat_in_new_stage_is_identical_and_existing_stage_is_protected(self):
        self.service.detect(self.session)
        before, assignments = self.read_result()
        self.service.detect(self.session, self.session / "wall-planes-second")
        after, second_assignments = self.read_result(output="wall-planes-second")
        self.assertEqual(before, after)
        np.testing.assert_array_equal(assignments, second_assignments)
        with self.assertRaises(FileExistsError):
            self.service.detect(self.session)

    def test_phone_capture_uses_y_up_and_keeps_floor_unassigned(self):
        phone = self.make_session("phone", phone=True)
        report = self.service.detect(phone)
        self.assertEqual(report["up_axis"], "y")
        self.assertEqual(report["plane_count"], 2)
        _, assignments = self.read_result(phone)
        vertices, _, faces = read_mesh(phone / "mesh_preprocessing/mesh.ply")
        floor = np.max(np.abs(vertices[faces, 1]), axis=1) < 1e-8
        np.testing.assert_array_equal(assignments[floor], -1)
        self.assertTrue(np.all(assignments[~floor] >= 0))

    def test_ambiguous_world_axis_requires_explicit_parameter(self):
        path = self.session / "original/scene.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        del manifest["dataset"]
        write_json(path, manifest)
        with self.assertRaisesRegex(ValueError, "up axis"):
            self.service.detect(self.session)
        self.assertFalse((self.session / "wall_planes").exists())
        report = self.service.detect(self.session, up_axis="z")
        self.assertEqual(report["up_axis_source"], "explicit_parameter")
        self.assertEqual(report["plane_count"], 2)

    def test_original_and_outside_stage_destinations_are_rejected(self):
        for output in (self.session / "original", self.root / "outside", self.session / "original/nested"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.service.detect(self.session, output)
        self.assertFalse((self.root / "outside").exists())

    def test_stale_semantic_mesh_reference_is_rejected(self):
        self.add_partial_sam_labels()
        write_json(self.session / "mesh_segmentation/report.json", {"source_mesh_sha256": "0" * 64})
        with self.assertRaisesRegex(ValueError, "different prepared mesh"):
            self.service.detect(self.session)
        self.assertFalse((self.session / "wall_planes").exists())

    def test_invalid_semantic_arrays_are_rejected(self):
        valid = self.add_partial_sam_labels()
        for invalid in (valid[:-1], valid.astype(float), np.full(valid.shape, 5, dtype=np.int32)):
            with self.subTest(shape=invalid.shape, dtype=invalid.dtype):
                np.savez_compressed(self.session / "mesh_segmentation/labels.npz", face_class_id=invalid)
                with self.assertRaisesRegex(ValueError, "semantic face labels"):
                    self.service.detect(self.session)
                self.assertFalse((self.session / "wall_planes").exists())

    def test_prepared_mesh_from_different_original_is_rejected(self):
        source = self.session / "original/mesh.ply"
        source.write_bytes(source.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "different original mesh"):
            self.service.detect(self.session)
        self.assertFalse((self.session / "wall_planes").exists())

    def test_export_failure_does_not_publish_partial_stage(self):
        with patch("geometry_engine.wall_planes.export_wall_plane_viewer", side_effect=OSError("export failed")):
            with self.assertRaisesRegex(OSError, "export failed"):
                self.service.detect(self.session)
        self.assertFalse((self.session / "wall_planes").exists())
        self.assertEqual(list(self.session.glob(".wall-planes-*")), [])


if __name__ == "__main__":
    unittest.main()
