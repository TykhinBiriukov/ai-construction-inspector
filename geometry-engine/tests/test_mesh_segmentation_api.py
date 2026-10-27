"""HTTP and CLI contracts for local mesh labeling; no external API requests."""

from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from geometry_engine_api.app import create_app
from geometry_engine_api.dependencies import get_mesh_segmentation_service, get_settings
from geometry_engine_api.services.archive import Settings
import run_mesh_segmentation


ENGINE = Path(__file__).resolve().parents[1]


class MeshSegmentationApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ENGINE)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = Settings(data_dir=self.root)
        self.report = {"processed_frames": 3, "labeled_faces": 120, "total_faces": 200, "candidate_objects": 2}
        self.service = Mock()
        self.service.segment.return_value = self.report
        self.app = create_app()
        self.app.dependency_overrides[get_settings] = lambda: self.settings
        self.app.dependency_overrides[get_mesh_segmentation_service] = lambda: self.service

    def post(self, body):
        with TestClient(self.app) as client:
            return client.post("/sessions/segment-mesh", json=body)

    def test_endpoint_delegates_and_returns_mesh_stage(self):
        response = self.post({"session_id": "47333462", "max_frames": 3})
        self.assertEqual(response.status_code, 201, response.text)
        self.service.segment.assert_called_once_with(self.root / "47333462", max_frames=3)
        self.assertEqual(response.json(), {"session_id": "47333462",
                         "directory": str(self.root / "47333462" / "mesh_segmentation"),
                         "mesh_segmentation": self.report})

    def test_default_processes_all_saved_frames(self):
        self.assertEqual(self.post({"session_id": "scan-1"}).status_code, 201)
        self.service.segment.assert_called_once_with(self.root / "scan-1", max_frames=None)

    def test_invalid_ids_limits_and_unsupported_options_are_rejected(self):
        for body in ({"session_id": "../elsewhere"}, {"session_id": "C:/private"},
                     {"session_id": "test", "max_frames": 0}, {"session_id": "test", "max_frames": True},
                     {"session_id": "test", "max_frames": "3"}, {"session_id": "test", "resume": True},
                     {"session_id": "test", "output": "elsewhere"}):
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 422)
        self.service.segment.assert_not_called()

    def test_existing_output_returns_conflict(self):
        self.service.segment.side_effect = FileExistsError("existing result")
        self.assertEqual(self.post({"session_id": "test"}).status_code, 409)

    def test_invalid_session_returns_422(self):
        self.service.segment.side_effect = ValueError("Missing segmentation report")
        response = self.post({"session_id": "test"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("Missing segmentation report", response.text)

    def test_storage_error_is_sanitized(self):
        self.service.segment.side_effect = OSError("private internal details")
        response = self.post({"session_id": "test"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private internal details", response.text)

    def test_default_di_needs_no_model_key_or_supabase_client(self):
        del self.app.dependency_overrides[get_mesh_segmentation_service]
        with patch.dict(os.environ, {"MODEL_API_KEY": "", "SUPABASE_SECRET_KEY": ""}), \
                patch("geometry_engine_api.dependencies.MeshSegmentationService", return_value=self.service) as factory, \
                patch("geometry_engine_api.dependencies.Sam3Backend") as backend, \
                patch("geometry_engine_api.dependencies.create_client") as storage:
            response = self.post({"session_id": "test"})
        self.assertEqual(response.status_code, 201, response.text)
        factory.assert_called_once_with()
        backend.assert_not_called()
        storage.assert_not_called()

    def test_cli_uses_saved_projection_and_custom_fusion_without_model_key(self):
        session = self.root / "test"
        output = session / "mesh_v2"
        projection = session / "mask_projection"
        arguments = ["run_mesh_segmentation.py", "--session", str(session), "--output", str(output),
                     "--projection-dir", str(projection), "--max-frames", "3", "--min-views", "3",
                     "--min-class-purity", "0.8"]
        with patch("sys.argv", arguments), patch.dict(os.environ, {"MODEL_API_KEY": ""}), \
                patch.object(run_mesh_segmentation, "MeshSegmentationService", return_value=self.service) as factory, \
                redirect_stdout(StringIO()) as stdout:
            run_mesh_segmentation.main()
        settings = factory.call_args.kwargs["fusion_settings"]
        self.assertEqual(settings.min_views, 3)
        self.assertEqual(settings.min_class_purity, 0.8)
        self.assertEqual(self.service.segment.call_args.args, (session, output))
        options = self.service.segment.call_args.kwargs
        self.assertEqual(options["projection_dir"], projection)
        self.assertEqual(options["max_frames"], 3)
        self.assertTrue(callable(options["progress"]))
        self.assertIn("120/200", stdout.getvalue())
        self.assertIn(str(output), stdout.getvalue())

    def test_cli_does_not_overwrite_an_existing_result(self):
        self.service.segment.side_effect = FileExistsError("Result already exists")
        with patch("sys.argv", ["run_mesh_segmentation.py"]), \
                patch.object(run_mesh_segmentation, "MeshSegmentationService", return_value=self.service), \
                redirect_stdout(StringIO()):
            with self.assertRaises(SystemExit) as failure:
                run_mesh_segmentation.main()
        self.assertEqual(failure.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
