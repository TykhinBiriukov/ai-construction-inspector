"""Optional opening inspection reuses registration through the existing API."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from geometry_engine_api.app import create_app
from geometry_engine_api.dependencies import (
    get_opening_comparison_service, get_scan_vs_bim_service, get_settings,
)
from geometry_engine_api.services.archive import Settings


ENGINE = Path(__file__).resolve().parents[1]


class OpeningApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ENGINE)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = self.root / "processed" / "session_01"
        self.segmentation = self.session / "mesh_segmentation"
        self.segmentation.mkdir(parents=True)
        self.references = self.root / "references"
        self.references.mkdir()
        self.ifc = self.references / "room.ifc"
        self.ifc.write_text("mock IFC", encoding="utf-8")
        self.registration = self.session / "scan_vs_bim"
        self.output = self.session / "opening_comparison"
        self.scan_service, self.opening_service = Mock(), Mock()
        self.scan_service.compare.return_value = {"status": "completed", "registration": {"accepted": True}}
        self.opening_service.compare.return_value = {"status": "completed", "findings": []}
        self.app = create_app()
        self.app.dependency_overrides[get_settings] = lambda: Settings(data_dir=self.root)
        self.app.dependency_overrides[get_scan_vs_bim_service] = lambda: self.scan_service
        self.app.dependency_overrides[get_opening_comparison_service] = lambda: self.opening_service
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def post(self, **extra):
        return self.client.post("/sessions/compare-bim", json={
            "session_id": "session_01", "ifc_file": "room.ifc", **extra,
        })

    def save_registration(self, report=None):
        self.registration.mkdir()
        (self.registration / "report.json").write_text(json.dumps(
            report if report is not None else self.scan_service.compare.return_value
        ), encoding="utf-8")

    def test_default_does_not_inspect_openings_or_change_response(self):
        response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.scan_service.compare.assert_called_once()
        self.opening_service.compare.assert_not_called()
        self.assertNotIn("opening_comparison", response.json())

    def test_existing_registration_is_reused_and_validated_by_opening_service(self):
        self.save_registration()
        response = self.post(compare_openings=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.scan_service.compare.assert_not_called()
        self.opening_service.compare.assert_called_once_with(
            self.session.resolve(), self.ifc.resolve(), output_dir=self.output.resolve(),
            segmentation_dir=self.segmentation.resolve(), comparison_dir=self.registration.resolve(),
        )
        self.assertEqual(response.json()["opening_comparison"]["status"], "completed")

    def test_new_registration_runs_before_opening_comparison(self):
        events = []
        self.scan_service.compare.side_effect = lambda *a, **k: events.append("registration") or {"status": "completed"}
        self.opening_service.compare.side_effect = lambda *a, **k: events.append("openings") or {"status": "completed"}
        response = self.post(compare_openings=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(events, ["registration", "openings"])

    def test_custom_semantic_stage_is_passed_to_both_services(self):
        alternative = self.session / "mesh_v2"
        alternative.mkdir()
        response = self.post(compare_openings=True, segmentation_stage="mesh_v2")
        self.assertEqual(response.status_code, 200, response.text)
        for service in (self.scan_service, self.opening_service):
            self.assertEqual(service.compare.call_args.kwargs["segmentation_dir"], alternative.resolve())

    def test_rejected_or_stale_registration_returns_validation_error(self):
        self.save_registration({"status": "registration_rejected"})
        self.opening_service.compare.side_effect = ValueError("Registration is not accepted")
        response = self.post(compare_openings=True)
        self.assertEqual(response.status_code, 422, response.text)
        self.scan_service.compare.assert_not_called()

    def test_existing_opening_result_does_not_trigger_registration_rerun(self):
        self.save_registration()
        self.opening_service.compare.side_effect = FileExistsError("exists")
        self.assertEqual(self.post(compare_openings=True).status_code, 409)
        self.scan_service.compare.assert_not_called()

    def test_invalid_saved_report_does_not_run_any_service(self):
        self.save_registration([])
        self.assertEqual(self.post(compare_openings=True).status_code, 422)
        self.scan_service.compare.assert_not_called()
        self.opening_service.compare.assert_not_called()

    def test_no_new_endpoint_is_added(self):
        paths = {route.path for route in self.app.routes if "POST" in getattr(route, "methods", set())}
        self.assertEqual(paths, {"/sessions/extract", "/sessions/process", "/sessions/compare-bim"})


if __name__ == "__main__":
    unittest.main()
