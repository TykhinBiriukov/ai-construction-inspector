"""Session validation with real JSON, PLY and frame fixtures."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import numpy as np

from geometry_engine.mesh import write_mesh
from geometry_engine_api.app import create_app
from geometry_engine_api.dependencies import get_archive_service
from geometry_engine_api.services.archive import ExtractionResult
from geometry_engine.validation import SessionValidationService
from run_geometry import run_session


class SessionValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.session = Path(temporary.name)
        self.root = self.session / "original"
        self.frame = self.root / "frames" / "000001"
        self.frame.mkdir(parents=True)
        self.scene = {"mesh": "mesh.ply", "frame_index": "frames.jsonl", "frame_count": 1,
                      "coordinate_system": {"length_unit": "meter"}}
        self.metadata = {"frame_id": "000001", "rgb": {"path": "rgb.png"},
                         "intrinsics": {"fx": 200, "fy": 200, "cx": 128, "cy": 96, "width": 256, "height": 192},
                         "pose": {"T_world_from_camera": np.eye(4).tolist()}}
        self.save_json(self.root / "scene.json", self.scene)
        self.save_json(self.frame / "metadata.json", self.metadata)
        self.entry = {"frame_id": "000001", "metadata": "frames/000001/metadata.json"}
        (self.root / "frames.jsonl").write_text(json.dumps(self.entry) + "\n", encoding="utf-8")
        (self.frame / "rgb.png").write_bytes(b"image-placeholder")
        write_mesh(self.root / "mesh.ply", np.array([[0., 0, 0], [1., 0, 0], [0., 1, 0]]),
                   np.full((3, 4), 255, dtype=np.uint8), np.array([[0, 1, 2]]))
        self.service = SessionValidationService()

    @staticmethod
    def save_json(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_valid_scene_is_reported_without_changing_sources(self):
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        report = self.service.validate(self.session)
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["frames"]["usable"], 1)
        self.assertEqual(report["mesh"]["faces"], 1)
        self.assertEqual(json.loads((self.session / "validation" / "report.json").read_text()), report)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(set(before), {path for path in self.root.rglob("*") if path.is_file()})

    def test_missing_pose_skips_only_incomplete_frame(self):
        missing = dict(self.metadata, frame_id="000002", pose=None)
        self.save_json(self.frame / "missing.json", missing)
        with (self.root / "frames.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps({"frame_id": "000002", "metadata": "frames/000001/missing.json"}) + "\n")
        self.save_json(self.root / "scene.json", dict(self.scene, frame_count=2))
        report = self.service.validate(self.session)
        self.assertEqual(report["status"], "valid_with_warnings")
        self.assertEqual(report["frames"]["usable"], 1)
        self.assertEqual(report["frames"]["incomplete"], 1)

    def test_missing_image_and_invalid_pose_or_intrinsics_are_rejected(self):
        for change in ({"rgb": {"path": "missing.png"}}, {"pose": None},
                       {"pose": {"T_world_from_camera": np.zeros((4, 4)).tolist()}},
                       {"intrinsics": dict(self.metadata["intrinsics"], fx=0)}):
            with self.subTest(change=change):
                self.save_json(self.frame / "metadata.json", dict(self.metadata, **change))
                report = self.service.validate(self.session)
                self.assertEqual(report["status"], "invalid")
                self.assertEqual(report["frames"]["usable"], 0)

    def test_missing_or_malformed_scene_and_wrong_units_are_rejected(self):
        for content in ("[]", "{", json.dumps(dict(self.scene, coordinate_system={"length_unit": "mm"}))):
            with self.subTest(content=content):
                (self.root / "scene.json").write_text(content, encoding="utf-8")
                self.assertEqual(self.service.validate(self.session)["status"], "invalid")
        (self.root / "scene.json").unlink()
        self.assertEqual(self.service.validate(self.session)["status"], "invalid")

    def test_corrupt_mesh_is_rejected(self):
        (self.root / "mesh.ply").write_bytes(b"not-ply")
        report = self.service.validate(self.session)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any(error.startswith("mesh:") for error in report["errors"]))

    def test_index_count_malformed_entries_and_escaping_paths_are_rejected(self):
        for content in ("not-json\n", "[]\n", json.dumps(dict(self.entry, metadata="../outside.json")), ""):
            with self.subTest(content=content):
                (self.root / "frames.jsonl").write_text(content, encoding="utf-8")
                self.assertEqual(self.service.validate(self.session)["status"], "invalid")
        self.save_json(self.root / "scene.json", dict(self.scene, mesh="../outside.ply"))
        self.assertEqual(self.service.validate(self.session)["status"], "invalid")

    def test_endpoint_runs_validation_after_extraction(self):
        root = self.session
        class Archive:
            def extract(self, session_id):
                return ExtractionResult(session_id, root)
        app = create_app()
        app.dependency_overrides[get_archive_service] = lambda: Archive()
        with TestClient(app) as client:
            response = client.post("/sessions/extract", json={"session_id": "test"})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["validation"]["status"], "valid")
            (root / "original" / "mesh.ply").write_bytes(b"invalid")
            response = client.post("/sessions/extract", json={"session_id": "test"})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["validation"]["status"], "invalid")
        self.assertTrue((root / "validation" / "report.json").is_file())

    def test_capture_contract_and_binary_depth_size(self):
        (self.root / "scene.json").unlink()
        self.save_json(self.root / "capture.json", {
            "schema_version": "1.0", "session_id": "test", "mesh": "mesh.ply",
            "frames_directory": "frames", "frame_count": 1, "length_unit": "meter",
            "coordinate_system": "arkit", "matrix_storage": "rows",
        })
        metadata = dict(self.metadata)
        metadata["rgb"] = dict(metadata["rgb"], width=256, height=192)
        metadata["intrinsics"] = {"K": [[200,0,128],[0,200,96],[0,0,1]]}
        metadata["depth"] = {"path":"depth.bin", "width":2, "height":2, "dtype":"float32",
                             "byte_order":"little", "unit":"meter", "source":"sceneDepth"}
        self.save_json(self.frame / "metadata.json", metadata)
        np.ones((2,2), dtype="<f4").tofile(self.frame / "depth.bin")
        self.assertEqual(self.service.validate(self.session)["status"], "valid")
        (self.frame / "depth.bin").write_bytes(b"bad")
        report = self.service.validate(self.session)
        self.assertEqual(report["status"], "invalid")
        self.assertIn("Buffer size differs", str(report["frames"]["issues"]))

    def test_runner_validates_then_preprocesses_mesh(self):
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        result = run_session(self.session)
        self.assertEqual(result["validation"]["status"], "valid")
        self.assertTrue((self.session / "mesh_preprocessing" / "mesh.ply").is_file())
        self.assertTrue((self.session / "mesh_preprocessing" / "report.json").is_file())
        self.assertTrue((self.session / "validation" / "report.json").is_file())
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_runner_rejects_output_inside_original(self):
        with self.assertRaisesRegex(ValueError, "Output must not be inside original"):
            run_session(self.session, self.root / "processed")
        self.assertFalse((self.root / "processed").exists())

    def test_missing_original_is_reported_outside_original(self):
        empty = self.session / "empty-session"
        empty.mkdir()
        report = self.service.validate(empty)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue((empty / "validation" / "report.json").is_file())
        self.assertFalse((empty / "original").exists())

    def test_runner_stops_on_invalid_session(self):
        (self.root / "mesh.ply").write_bytes(b"bad")
        with patch("run_geometry.preprocess_mesh") as preprocess:
            with self.assertRaisesRegex(ValueError, "Session validation failed"):
                run_session(self.session)
            preprocess.assert_not_called()
        self.assertFalse((self.session / "mesh_preprocessing").exists())
