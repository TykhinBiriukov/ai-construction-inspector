"""Thin SAM API and CLI contract tests; never call the external Meta API."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from geometry_engine.sam_backend import Sam3UnavailableError
from geometry_engine_api.app import create_app
from geometry_engine_api.dependencies import get_segmentation_service, get_settings
from geometry_engine_api.services.archive import Settings
import run_segmentation


ENGINE = Path(__file__).resolve().parents[1]


class SegmentationApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ENGINE)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = Settings(data_dir=self.root)
        self.service = Mock()
        self.report = {"status": "completed", "processed_count": 3, "detection_count": 4}
        self.service.segment.return_value = self.report
        self.app = create_app()
        self.app.dependency_overrides[get_settings] = lambda: self.settings
        self.app.dependency_overrides[get_segmentation_service] = lambda: self.service

    def post(self, body):
        with TestClient(self.app) as client:
            return client.post("/sessions/segment", json=body)

    def test_endpoint_delegates_without_requiring_supabase(self):
        response = self.post({"session_id": "47333462", "max_frames": 3})
        self.assertEqual(response.status_code, 201, response.text)
        self.service.segment.assert_called_once_with(self.root / "47333462", max_frames=3)
        self.assertEqual(response.json(), {"session_id": "47333462",
                         "directory": str(self.root / "47333462" / "segmentation"), "segmentation": self.report})

    def test_default_processes_all_selected_frames(self):
        self.assertEqual(self.post({"session_id": "scan-1"}).status_code, 201)
        self.service.segment.assert_called_once_with(self.root / "scan-1", max_frames=None)

    def test_resume_option_is_passed_to_the_service(self):
        self.assertEqual(self.post({"session_id": "scan-1", "resume": True}).status_code, 201)
        self.service.segment.assert_called_once_with(self.root / "scan-1", max_frames=None, resume=True)

    def test_invalid_ids_limits_and_extra_fields_are_rejected(self):
        for body in ({"session_id": "../elsewhere"}, {"session_id": "C:/private"},
                     {"session_id": "test", "max_frames": 0}, {"session_id": "test", "max_frames": True},
                     {"session_id": "test", "max_frames": "3"},
                     {"session_id": "test", "archive_url": "https://example.invalid"}):
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 422)
        self.service.segment.assert_not_called()

    def test_existing_output_returns_conflict(self):
        self.service.segment.side_effect = FileExistsError("existing result")
        self.assertEqual(self.post({"session_id": "test"}).status_code, 409)

    def test_invalid_session_returns_422(self):
        self.service.segment.side_effect = ValueError("Missing selected_frames.json")
        response = self.post({"session_id": "test"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("selected_frames.json", response.text)

    def test_upstream_error_does_not_expose_provider_payload_or_key(self):
        self.service.segment.side_effect = Sam3UnavailableError("upstream payload with secret-token")
        response = self.post({"session_id": "test"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret-token", response.text)

    def test_missing_key_returns_503_without_calling_provider(self):
        del self.app.dependency_overrides[get_segmentation_service]
        with patch.dict(os.environ, {"MODEL_API_KEY": ""}), patch("geometry_engine_api.dependencies.Sam3Backend") as backend:
            response = self.post({"session_id": "test"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("MODEL_API_KEY", response.text)
        backend.assert_not_called()

    def test_storage_error_is_sanitized(self):
        self.service.segment.side_effect = OSError("private internal details")
        response = self.post({"session_id": "test"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private internal details", response.text)

    def test_cli_missing_key_exits_before_model_construction(self):
        with patch("sys.argv", ["run_segmentation.py", "--max-frames", "3"]), \
                patch.dict(os.environ, {"MODEL_API_KEY": ""}), \
                patch.object(run_segmentation, "load_dotenv"), \
                patch.object(run_segmentation, "Sam3Backend") as backend:
            with self.assertRaises(SystemExit) as failure:
                run_segmentation.main()
        self.assertEqual(failure.exception.code, 1)
        backend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
