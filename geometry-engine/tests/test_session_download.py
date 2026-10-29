"""Manifest downloads exercise the endpoint without external requests."""

from dataclasses import replace
from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import Mock
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from geometry_engine_api.app import create_app
from geometry_engine_api.dependencies import get_archive_service, get_validation_service
from geometry_engine_api.services.archive import ArchiveService, Settings


class SessionDownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = Settings(data_dir=self.root/"data")
        self.prefix = "projects/demo-building/sessions/scan-001"
        content = b'{"frame_id":"frame-001"}'
        self.asset = {"name": "frame.json", "object_key": self.prefix+"/observations/frame-001/frame.json",
                      "byte_length": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        self.manifest = {"format": "capture_manifest_supabase_v1", "project_id": "demo-building",
                         "session_id": "scan-001", "observation_count": 1,
                         "observations": [{"observation_id": "frame-001", "files": [self.asset]}]}
        self.objects = {self.prefix+"/.session.json": json.dumps({"project_id": "demo-building", "session_id": "scan-001"}).encode(),
                        self.asset["object_key"]: content}
        self.bucket = Mock()
        self.bucket.list.return_value = [{"id": "manifest-object", "name": "manifest-001.json"}]
        self.bucket.create_signed_url.side_effect = lambda key, expiry: {"signedURL": "https://storage.example/"+key}
        sdk = Mock()
        sdk.storage.from_.return_value = self.bucket

        def download(url, destination, settings):
            content = self.objects[urlsplit(url).path.lstrip("/")]
            if len(content) > settings.max_download_bytes:
                raise ValueError("too large")
            destination.write_bytes(content)

        self.service = ArchiveService(self.settings, sdk, download_fn=download)
        app = create_app()
        self.validator = Mock()
        app.dependency_overrides[get_archive_service] = lambda: self.service
        app.dependency_overrides[get_validation_service] = lambda: self.validator
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def request(self):
        self.objects[self.prefix+"/manifests/manifest-001.json"] = json.dumps(self.manifest).encode()
        return self.client.post("/sessions/extract", json={"project_id": "demo-building", "session_id": "scan-001"})

    def test_downloads_manifest_files_and_keeps_storage_layout(self):
        response = self.request()
        self.assertEqual(response.status_code, 201, response.text)
        original = self.settings.data_dir/"raw"/"scan-001/original"
        self.assertEqual((original/"observations/frame-001/frame.json").read_bytes(), self.objects[self.asset["object_key"]])
        self.assertTrue((original/"manifests/manifest-001.json").is_file())
        self.assertTrue((original/".session.json").is_file())
        self.assertEqual(response.json()["validation"]["status"], "not_run")
        self.validator.validate.assert_not_called()
        self.assertEqual(self.request().status_code, 409)

    def test_cross_session_paths_and_hash_mismatches_leave_no_partial_session(self):
        original_key = self.asset["object_key"]
        for change in ({"object_key": "projects/other/sessions/scan-001/private.txt"}, {"sha256": "0"*64}, {"name": "../escape.json"}):
            with self.subTest(change=change):
                original = dict(self.asset)
                try:
                    self.asset.update(change)
                    self.assertEqual(self.request().status_code, 422)
                    self.assertFalse((self.settings.data_dir/"raw"/"scan-001").exists())
                    self.assertEqual(list((self.settings.data_dir / "raw").iterdir()), [])
                finally:
                    self.asset.clear(); self.asset.update(original)
        self.assertIn(original_key, self.objects)

    def test_ambiguous_manifest_and_total_size_are_rejected(self):
        self.bucket.list.return_value *= 2
        self.assertEqual(self.request().status_code, 422)
        self.bucket.list.return_value = self.bucket.list.return_value[:1]
        self.service.settings = replace(self.settings, max_download_bytes=700)
        self.asset["byte_length"] = 1000
        self.assertEqual(self.request().status_code, 422)
        self.assertFalse((self.settings.data_dir/"raw"/"scan-001").exists())

    def test_invalid_project_id_is_rejected_before_storage_access(self):
        response = self.client.post("/sessions/extract", json={"project_id": "../escape", "session_id": "scan-001"})
        self.assertEqual(response.status_code, 422)
        self.bucket.create_signed_url.assert_not_called()

    def make_parts(self, entries=None):
        entries = entries or [{"capture.json": b"first", "mesh.ply": b"mesh-one"},
                              {"capture.json": b"second", "mesh.ply": b"mesh-two"}]
        parts = []
        for index, files in enumerate(entries, 1):
            data = BytesIO()
            with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, content in files.items():
                    archive.writestr(name, content)
            content = data.getvalue()
            name = f"capture-{index:04d}.zip"
            key = f"{self.prefix}/geometry/completion-001/{name}"
            self.objects[key] = content
            parts.append({"name": name, "object_key": key, "byte_length": len(content),
                          "sha256": hashlib.sha256(content).hexdigest(), "frame_ids": [f"frame-{index:03d}"],
                          "segment_id": "segment-001"})
        self.manifest = {"format": "geometry_capture_parts_v1", "schema_version": "1.0",
                         "project_id": "demo-building", "session_id": "scan-001", "completion_id": "completion-001",
                         "frame_count": len(parts), "parts": parts}
        return parts

    def test_downloads_and_extracts_parts_without_overwriting_shared_names(self):
        parts = self.make_parts()
        response = self.request()
        self.assertEqual(response.status_code, 201, response.text)
        session = self.settings.data_dir/"raw"/"scan-001"
        self.assertEqual((session/"original/parts/capture-0001/mesh.ply").read_bytes(), b"mesh-one")
        self.assertEqual((session/"original/parts/capture-0002/mesh.ply").read_bytes(), b"mesh-two")
        for part in parts:
            self.assertEqual((session/"archives"/part["name"]).read_bytes(), self.objects[part["object_key"]])
        report = json.loads((session/"download-report.json").read_text())
        self.assertEqual(report["format"], "geometry_capture_parts_v1")
        self.assertEqual(report["part_count"], 2)
        self.assertEqual(report["frame_count"], 2)
        self.assertTrue(report["sha256_verified"])
        self.assertEqual(response.json()["validation"]["status"], "not_run")

    def test_parts_enforce_hashes_paths_and_unique_frames(self):
        for fault in ("checksum", "path", "frames", "invalid_zip"):
            with self.subTest(fault=fault):
                parts = self.make_parts()
                if fault == "checksum":
                    parts[0]["sha256"] = "0"*64
                elif fault == "path":
                    parts[0]["object_key"] = "projects/other/geometry/capture-0001.zip"
                elif fault == "frames":
                    parts[1]["frame_ids"] = parts[0]["frame_ids"]
                else:
                    content = b"not-a-zip"
                    self.objects[parts[0]["object_key"]] = content
                    parts[0].update(byte_length=len(content), sha256=hashlib.sha256(content).hexdigest())
                self.assertEqual(self.request().status_code, 422)
                self.assertEqual(list((self.settings.data_dir / "raw").iterdir()), [])

    def test_parts_enforce_total_extraction_limit_and_reject_zip_traversal(self):
        self.make_parts([{"safe.txt": b"a"*8}, {"safe.txt": b"b"*8}])
        self.service.settings = replace(self.settings, max_extract_bytes=12)
        self.assertEqual(self.request().status_code, 422)
        self.assertEqual(list((self.settings.data_dir / "raw").iterdir()), [])
        self.service.settings = self.settings
        self.make_parts([{"../escape.txt": b"bad"}])
        self.assertEqual(self.request().status_code, 422)
        self.assertEqual(list((self.settings.data_dir / "raw").iterdir()), [])

    def test_unknown_manifest_format_returns_specific_error(self):
        self.manifest["format"] = "unknown_v1"
        response = self.request()
        self.assertEqual(response.status_code, 422)
        self.assertIn("Unsupported manifest format", response.json()["detail"])
