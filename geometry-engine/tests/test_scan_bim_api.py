"""Thin comparison endpoint: path boundaries, DI, and error contract."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from geometry_engine_api.app import create_app
from geometry_engine_api.dependencies import get_scan_vs_bim_service, get_settings
from geometry_engine_api.services.archive import Settings


class CompareBimApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "data"
        self.session = self.data / "processed" / "session_01"
        (self.session / "mesh_segmentation").mkdir(parents=True)
        (self.data / "references").mkdir()
        self.ifc = self.data / "references" / "room.ifc"
        self.ifc.write_text("endpoint only; mock service reads no geometry",encoding="utf-8")
        self.service = Mock()
        self.service.compare.return_value = {"status":"completed","objects":[]}
        self.app = create_app()
        self.app.dependency_overrides[get_settings] = lambda: Settings(data_dir=self.data)
        self.app.dependency_overrides[get_scan_vs_bim_service] = lambda: self.service
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.payload = {"session_id":"session_01","ifc_file":"room.ifc"}

    def post(self, **updates):
        return self.client.post("/sessions/compare-bim",json={**self.payload,**updates})

    def test_delegates_once_without_sam_or_storage_configuration(self):
        response = self.post()
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["session_id"],"session_01")
        self.assertEqual(response.json()["comparison"]["status"],"completed")
        self.service.compare.assert_called_once_with(self.session.resolve(),self.ifc.resolve(),
            output_dir=(self.session / "scan_vs_bim").resolve(),
            segmentation_dir=(self.session / "mesh_segmentation").resolve())

    def test_custom_segmentation_stage(self):
        (self.session / "agent_mesh_segmentation_v2").mkdir()
        response = self.post(segmentation_stage="agent_mesh_segmentation_v2")
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.service.compare.call_args.kwargs["segmentation_dir"],
                         (self.session / "agent_mesh_segmentation_v2").resolve())

    def test_traversal_and_extra_fields_are_rejected(self):
        for changes in [{"session_id":"../other"},{"ifc_file":"../room.ifc"},
                        {"ifc_file":"C:\\room.ifc"},{"segmentation_stage":"../other"},
                        {"unexpected":True},{"ifc_file":"room.txt"}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.post(**changes).status_code,422)
        self.service.compare.assert_not_called()

    def test_missing_input_is_404(self):
        self.assertEqual(self.post(ifc_file="missing.ifc").status_code,404)
        self.assertEqual(self.post(session_id="missing").status_code,404)
        self.assertEqual(self.post(segmentation_stage="missing").status_code,404)
        self.service.compare.assert_not_called()

    def test_error_mapping_and_runtime_details_are_sanitized(self):
        for error,status in [(FileExistsError("exists"),409),(FileNotFoundError("missing"),404),
                             (ValueError("Bad IFC units"),422),(RuntimeError("PRIVATE_DETAILS"),500),
                             (OSError("PRIVATE_DETAILS"),500)]:
            with self.subTest(error=type(error)):
                self.service.compare.side_effect = error
                response = self.post()
                self.assertEqual(response.status_code,status,response.text)
                self.assertNotIn("PRIVATE_DETAILS",response.text)

    def test_symlinked_segmentation_escape_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.session / "escape"
        try:
            link.symlink_to(outside,target_is_directory=True)
        except OSError:
            self.skipTest("Creating directory symlinks requires Windows privileges")
        # Remove this exact link before TemporaryDirectory cleanup visits it.
        self.addCleanup(link.unlink)
        self.assertEqual(self.post(segmentation_stage="escape").status_code,422)
        self.service.compare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
