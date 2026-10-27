"""Tests for the thin HTTP layer and archive extraction service."""

from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import httpx
from fastapi.testclient import TestClient

from geometry_engine_api.app import create_app
from geometry_engine_api.dependencies import get_archive_service
from geometry_engine_api.services.archive import ArchiveService, ExtractionResult, Settings, download_archive


ENGINE = Path(__file__).resolve().parents[1]


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ENGINE)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = Settings(data_dir=self.root / "data", storage_url="https://storage.example/scans")

    def archive(self, entries=None):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in (entries if entries is not None else {"mesh.ply": b"original mesh", "frames/000001/rgb.png": b"image"}).items():
                archive.writestr(name, data)
        return buffer.getvalue()

    def client(self, archive=None, settings=None, response_status=200):
        content = self.archive() if archive is None else archive
        transport = httpx.MockTransport(lambda request: httpx.Response(response_status, content=content))
        def download(url, destination, config):
            download_archive(url, destination, config, transport=transport)
        app = create_app()
        service = ArchiveService(settings or self.settings, download_fn=download)
        app.dependency_overrides[get_archive_service] = lambda: service
        return TestClient(app)

    def payload(self, **changes):
        return {"session_id": "scan-001", **changes}

    def test_extracts_into_session_folder_and_preserves_files_after_response(self):
        with self.client() as client:
            response = client.post("/sessions/extract", json=self.payload())
        self.assertEqual(response.status_code, 201, response.text)
        destination = self.settings.data_dir / "scan-001"
        self.assertEqual(response.json(), {"session_id": "scan-001", "directory": str(destination.resolve())})
        self.assertEqual((destination / "mesh.ply").read_bytes(), b"original mesh")
        self.assertEqual((destination / "frames/000001/rgb.png").read_bytes(), b"image")
        self.assertFalse((destination / "prepared").exists())
        self.assertEqual([p.name for p in self.settings.data_dir.iterdir()], ["scan-001"])

    def test_endpoint_uses_injected_service(self):
        calls = []
        class FakeService:
            def extract(inner, session_id):
                calls.append(session_id)
                return ExtractionResult(session_id, Path("data") / session_id)
        app = create_app()
        app.dependency_overrides[get_archive_service] = lambda: FakeService()
        with TestClient(app) as client:
            response = client.post("/sessions/extract", json=self.payload())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(calls, ["scan-001"])

    def test_duplicate_session_does_not_overwrite_existing_data(self):
        with self.client() as client:
            self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 201)
            self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 409)
        self.assertEqual((self.settings.data_dir / "scan-001/mesh.ply").read_bytes(), b"original mesh")

    def test_archive_service_does_not_require_or_process_a_mesh(self):
        with self.client(self.archive({"notes.txt": b"plain archive"})) as client:
            self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 201)
        self.assertEqual((self.settings.data_dir / "scan-001/notes.txt").read_bytes(), b"plain archive")

    def test_unconfigured_storage_is_rejected(self):
        with self.client(settings=replace(self.settings, storage_url="")) as client:
            self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 422)

    def test_bad_session_id_and_non_http_url_are_rejected(self):
        with self.client() as client:
            for change in ({"session_id": "../../escape"}, {"archive_url": "https://storage.example/scan.zip"}):
                self.assertEqual(client.post("/sessions/extract", json=self.payload(**change)).status_code, 422)
        for url in ("file:///etc/passwd", ""):
            with self.client(settings=replace(self.settings, storage_url=url)) as client:
                self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 422)

    def test_storage_url_is_read_from_environment(self):
        with patch.dict("os.environ", {"GEOMETRY_STORAGE_URL": "https://storage.example/scans"}):
            self.assertEqual(Settings.from_env().storage_url, "https://storage.example/scans")

    def test_session_id_selects_file_inside_storage(self):
        for base in ("https://storage.example/scans", "https://storage.example/scans/"):
            with self.subTest(base=base):
                urls = []
                def download(url, destination, settings):
                    urls.append(url)
                    destination.write_bytes(self.archive())
                settings = replace(self.settings, data_dir=self.root / str(len(base)), storage_url=base)
                service = ArchiveService(settings, download_fn=download)
                service.extract("scan-001")
                service.extract("scan-002")
                self.assertEqual(urls, ["https://storage.example/scans/scan-001", "https://storage.example/scans/scan-002"])

    def test_zip_traversal_is_rejected_without_partial_session_folder(self):
        with self.client(self.archive({"../escape.txt": b"bad"})) as client:
            self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 422)
        self.assertEqual(list(self.settings.data_dir.iterdir()), [])

    def test_download_and_extraction_limits(self):
        with self.client(settings=replace(self.settings, max_download_bytes=10)) as client:
            self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 502)
        with self.client(settings=replace(self.settings, max_extract_bytes=10)) as client:
            self.assertEqual(client.post("/sessions/extract", json=self.payload()).status_code, 422)
        self.assertEqual(list(self.settings.data_dir.iterdir()), [])

    def test_failed_download_is_cleaned_up_and_does_not_expose_signed_url(self):
        with self.client(response_status=403, settings=replace(self.settings, storage_url="https://storage.example/scans?secret=hidden")) as client:
            response = client.post("/sessions/extract", json=self.payload())
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("hidden", response.text)
        self.assertEqual(list(self.settings.data_dir.iterdir()), [])

    def test_only_one_business_endpoint_exists(self):
        with self.client() as client:
            paths = client.get("/openapi.json").json()["paths"]
        self.assertEqual(set(paths), {"/sessions/extract"})


if __name__ == "__main__":
    unittest.main()
