"""Tests for the thin HTTP layer and archive extraction service."""

from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

import httpx
from fastapi.testclient import TestClient
from supabase import ClientOptions, create_client

from geometry_engine_api.app import create_app
from geometry_engine_api.dependencies import get_archive_service, get_settings, get_validation_service
from geometry_engine_api.services.archive import ArchiveService, ExtractionResult, Settings, download_archive


ENGINE = Path(__file__).resolve().parents[1]


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ENGINE)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = Settings(data_dir=self.root / "data", supabase_url="https://storage.example", supabase_secret_key="test-secret", storage_bucket="inspection-captures")
        self.requests = []

    def archive(self, entries=None):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in (entries if entries is not None else {"mesh.ply": b"original mesh", "frames/000001/rgb.png": b"image"}).items():
                archive.writestr(name, data)
        return buffer.getvalue()

    def client(self, archive=None, settings=None, response_status=200, signing_status=200):
        content = self.archive() if archive is None else archive
        def handle(request):
            self.requests.append(request)
            if request.method == "POST":
                if signing_status != 200:
                    return httpx.Response(signing_status, json={"statusCode": str(signing_status), "error": "Access denied", "message": "hidden"})
                return httpx.Response(200, json={"signedURL": "/object/sign/inspection-captures/scan-001.zip?token=hidden"})
            return httpx.Response(response_status, content=content)
        transport = httpx.MockTransport(handle)
        sdk_http = httpx.Client(transport=transport)
        self.addCleanup(sdk_http.close)
        sdk = create_client(self.settings.supabase_url, self.settings.supabase_secret_key,
                            options=ClientOptions(httpx_client=sdk_http, persist_session=False, auto_refresh_token=False))
        def download(url, destination, config):
            download_archive(url, destination, config, transport=transport)
        app = create_app()
        service = ArchiveService(settings or self.settings, sdk, download_fn=download)
        app.dependency_overrides[get_archive_service] = lambda: service
        app.dependency_overrides[get_validation_service] = lambda: Mock(validate=lambda directory: {"status": "valid"})
        return TestClient(app)

    def payload(self, **changes):
        return {"session_id": "scan-001", **changes}

    def test_extracts_into_session_folder_and_preserves_files_after_response(self):
        with self.client() as client:
            response = client.post("/sessions/extract", json=self.payload())
        self.assertEqual(response.status_code, 201, response.text)
        destination = self.settings.data_dir / "raw" / "scan-001"
        self.assertEqual(response.json(), {"session_id": "scan-001", "directory": str(destination.resolve()), "validation": {"status": "not_run", "reason": "Validation runs in /sessions/process"}})
        self.assertEqual((destination / "original/mesh.ply").read_bytes(), b"original mesh")
        self.assertEqual((destination / "original/frames/000001/rgb.png").read_bytes(), b"image")
        self.assertEqual(sorted(p.name for p in destination.iterdir()), ["archives", "original"])
        self.assertFalse((destination / "prepared").exists())
        self.assertEqual([p.name for p in (self.settings.data_dir / "raw").iterdir()], ["scan-001"])
        self.assertEqual(self.requests[0].url.path, "/storage/v1/object/sign/inspection-captures/scan-001.zip")
        self.assertEqual(self.requests[0].headers["apikey"], "test-secret")
        self.assertEqual(self.requests[1].url.path, "/storage/v1/object/sign/inspection-captures/scan-001.zip")

    def test_endpoint_uses_injected_service(self):
        calls = []
        class FakeService:
            def extract(inner, session_id):
                calls.append(session_id)
                return ExtractionResult(session_id, Path("data") / session_id)
        app = create_app()
        app.dependency_overrides[get_archive_service] = lambda: FakeService()
        app.dependency_overrides[get_validation_service] = lambda: Mock(validate=lambda directory: {"status": "valid"})
        with TestClient(app) as client:
            response = client.post("/sessions/extract", json=self.payload())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(calls, ["scan-001"])

    def test_duplicate_session_does_not_overwrite_existing_data(self):
        with self.client() as client:
            self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 201)
            self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 409)
        self.assertEqual((self.settings.data_dir / "raw" / "scan-001/original/mesh.ply").read_bytes(), b"original mesh")

    def test_archive_service_does_not_require_or_process_a_mesh(self):
        with self.client(self.archive({"notes.txt": b"plain archive"})) as client:
            self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 201)
        self.assertEqual((self.settings.data_dir / "raw" / "scan-001/original/notes.txt").read_bytes(), b"plain archive")

    def test_unconfigured_storage_is_rejected(self):
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: replace(self.settings, supabase_secret_key="")
        with TestClient(app) as client:
            response = client.post("/sessions/extract", json=self.payload())
        self.assertEqual(response.status_code, 422)
        self.assertIn("SUPABASE_SECRET_KEY", response.text)

    def test_bad_session_id_and_non_http_url_are_rejected(self):
        with self.client() as client:
            for change in ({"session_id": "../../escape"}, {"archive_url": "https://storage.example/scan.zip"}):
                self.assertEqual(client.post("/sessions/extract", json=self.payload(**change)).status_code, 422)
        for url in ("file:///etc/passwd", ""):
            app = create_app()
            app.dependency_overrides[get_settings] = lambda: replace(self.settings, supabase_url=url)
            with TestClient(app) as client:
                self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 422)

    def test_supabase_settings_are_read_from_environment(self):
        with patch.dict("os.environ", {"SUPABASE_URL": "https://storage.example", "SUPABASE_SECRET_KEY": "test-secret", "SUPABASE_STORAGE_BUCKET": "captures"}):
            config = Settings.from_env()
        self.assertEqual(config.supabase_url, "https://storage.example")
        self.assertEqual(config.storage_bucket, "captures")
        self.assertEqual(config.supabase_secret_key, "test-secret")
        self.assertNotIn("test-secret", repr(config))

    def test_session_id_selects_file_inside_storage(self):
        with self.client() as client:
            self.assertEqual(client.post("/sessions/extract", json={"session_id": "scan-002"}).status_code, 201)
        self.assertEqual(self.requests[0].url.path, "/storage/v1/object/sign/inspection-captures/scan-002.zip")

    def test_zip_traversal_is_rejected_without_partial_session_folder(self):
        with self.client(self.archive({"../escape.txt": b"bad"})) as client:
            self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 422)
        self.assertEqual(list((self.settings.data_dir / "raw").iterdir()), [])

    def test_download_and_extraction_limits(self):
        with self.client(settings=replace(self.settings, max_download_bytes=10)) as client:
            self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 502)
        with self.client(settings=replace(self.settings, max_extract_bytes=10)) as client:
            self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 422)
        self.assertEqual(list((self.settings.data_dir / "raw").iterdir()), [])

    def test_failed_download_is_cleaned_up_and_does_not_expose_signed_url(self):
        with self.client(response_status=403) as client:
            response = client.post("/sessions/extract", json=self.payload())
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("hidden", response.text)
        self.assertEqual(list((self.settings.data_dir / "raw").iterdir()), [])

    def test_supabase_signing_failure_is_cleaned_up(self):
        with self.client(signing_status=403) as client:
            response = client.post("/sessions/extract", json=self.payload())
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("hidden", response.text)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(list((self.settings.data_dir / "raw").iterdir()), [])

    def test_session_stage_endpoints_are_exposed(self):
        with self.client() as client:
            paths = client.get("/openapi.json").json()["paths"]
        self.assertEqual(set(paths), {"/sessions/extract", "/sessions/process"})


if __name__ == "__main__":
    unittest.main()
