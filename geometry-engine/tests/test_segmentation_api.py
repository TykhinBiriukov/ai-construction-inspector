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
