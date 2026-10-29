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
