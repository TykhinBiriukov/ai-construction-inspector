import hashlib
import io
import json
import stat
import tempfile
import unittest
import warnings
import zipfile
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from ai_inspector.api import create_app
from ai_inspector.config import Settings
from ai_inspector.errors import APIError
from ai_inspector.session_folders import SupabaseSessionFolders
from ai_inspector.supabase_captures import SupabaseCaptureSourceStore
from test_device_capture_case import FakeSupabase


class CaptureArchiveTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / ".test-data"
        root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(Path(self.temporary.name), "operator-token", "device-token",
                                 supabase_url="https://test.supabase.co", supabase_storage_bucket="captures",
                                 supabase_secret_key="sb_secret_fake", geometry_base_url="https://geometry.example",
                                 inspector_public_base_url="https://inspector.example", geometry_api_token="g-out-token",
                                 geometry_service_token="g-in-token", geometry_accepts_rgb_only=True)
        self.fake = FakeSupabase()
        self.cloud = httpx.Client(transport=httpx.MockTransport(self.fake))
        self.addCleanup(self.cloud.close)
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        self.service = self.client.app.state.service
        self.store = SupabaseCaptureSourceStore(self.settings, self.cloud)
        self.service.capture._supabase_source_store = self.store
        self.service.session_folders = SupabaseSessionFolders(self.settings, self.cloud)
        self.operator = {"Authorization": "Bearer operator-token"}
        self.device = {"Authorization": "Bearer device-token"}
        response = self.client.post("/v1/capture-sessions", headers=self.operator, json={
            "schema_version": "0.1", "request_id": str(uuid4()), "device_id": "iphone-1"})
        self.assertEqual(response.status_code, 201, response.text)
        self.session = response.json()
        self.base = "/v1/sessions/" + self.session["session_id"]
        self.prefix = self.session["storage"]["key_prefix"]
        self.client.post(self.base + "/capture-start", headers=self.device, json={
            "schema_version": "0.1", "operation_id": str(uuid4()),
            "device": {"name": "iPhone", "os_version": "test", "app_version": "test"}}).raise_for_status()
        self.observation_id, segment_id = str(uuid4()), str(uuid4())
        rgb = b"ZIP RGB transport fixture"
        desc = lambda name, data: {"name": name, "byte_length": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        frame = {"schema_version": "0.2", "session_id": self.session["session_id"], "observation_id": self.observation_id,
                 "frame_id": self.observation_id, "segment_id": segment_id, "instruction_id": None,
                 "capture_origin": "device", "tracking_state": "normal", "rgb": desc("rgb.jpg", rgb),
                 "depth_source": "none", "depth": None, "depth_size": None, "depth_unit": None,
                 "depth_to_rgb_mapping": None, "confidence": None}
        self.payloads = {"rgb.jpg": rgb, "frame.json": json.dumps(frame).encode()}
        self.manifest = {"schema_version": "0.1", "format": "capture_manifest_supabase_v1",
                         **{name: self.session[name] for name in ("session_id", "inspection_id", "project_id", "room_id", "model_version", "capture_origin", "input_mode")},
                         "capture_closed_at": "2026-10-27T14:00:00Z", "observation_count": 1,
                         "segments": [{"segment_id": segment_id, "created_at": "2026-10-27T13:00:00Z", "units": "m",
                                       "coordinate_convention": "arkit_world_camera_v1", "matrix_layout": "rows_column_vectors"}],
                         "observations": [{"observation_id": self.observation_id, "segment_id": segment_id,
                                           "files": [desc(name, data) | {"object_key": self.prefix + f"observations/{self.observation_id}/{name}"}
                                                     for name, data in self.payloads.items()]}]}

    def archive(self, *, extra=(), omit=(), payload_override=None, compression=zipfile.ZIP_DEFLATED):
        entries = [("capture-manifest.json", json.dumps(self.manifest).encode())]
        entries += [(f"observations/{self.observation_id}/{name}", content) for name, content in self.payloads.items()]
        output = io.BytesIO()
        with warnings.catch_warnings(), zipfile.ZipFile(output, "w", compression=compression) as archive:
            warnings.simplefilter("ignore", UserWarning)
            for name, content in entries + list(extra):
                if name not in omit:
                    archive.writestr(name, (payload_override or {}).get(name, content))
        return output.getvalue()

    def reference(self, content):
        completion_id = str(uuid4())
        ref = {"format": "supabase_capture_zip_v1", "storage_id": "capture-supabase",
               "archive_key": self.prefix + f"archives/{completion_id}.zip", "archive_byte_length": len(content),
               "archive_sha256": hashlib.sha256(content).hexdigest()}
        return completion_id, ref

    def stored_reference(self, content):
        completion_id, ref = self.reference(content)
        self.fake.objects["captures/" + ref["archive_key"]] = content
        return completion_id, ref

    def test_single_signed_zip_upload_completes_without_individual_photo_uploads(self):
        content = self.archive()
        completion_id, ref = self.reference(content)
        plan_body = {"schema_version": "0.1", "request_id": str(uuid4()), "kind": "capture_archive",
                     "completion_id": completion_id, "objects": [{"observation_id": None, "name": "capture.zip",
                     "media_type": "application/zip", "byte_length": len(content), "sha256": ref["archive_sha256"]}]}
        response = self.client.post(self.base + "/upload-plans", headers=self.device, json=plan_body)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()["items"][0]
        self.assertEqual(item["object_key"], ref["archive_key"])
        self.assertEqual(item["headers"]["Content-Type"], "application/zip")
        self.assertEqual(self.cloud.put(item["upload_url"], headers=item["headers"], content=content).status_code, 200)
        replay = self.client.post(self.base + "/upload-plans", headers=self.device, json=plan_body)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["items"][0]["status"], "already_uploaded")
        complete = {"schema_version": "0.1", "completion_id": completion_id, "capture_ref": ref}
        response = self.client.post(self.base + "/complete", headers=self.device, json=complete)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(self.service.capture.process_next())
        current = self.client.get(self.base, headers=self.operator).json()
        self.assertEqual(current["completion"]["status"], "accepted", current)
        self.assertEqual(current["source"]["manifest_sha256"], hashlib.sha256(json.dumps(self.manifest).encode()).hexdigest())
        self.assertNotEqual(current["source"]["manifest_sha256"], ref["archive_sha256"])
        accepted = current["source"]["capture_ref"]
        self.assertEqual(self.fake.objects["captures/" + accepted["archive"]["pinned_object_key"]], content)
        for name in self.payloads:
            self.assertNotIn("captures/" + self.prefix + f"observations/{self.observation_id}/{name}", self.fake.objects)
        command = []

        def generator(request):
            if request.method == "PUT":
                command.append(json.loads(request.content))
                return httpx.Response(201, json={"job_id": current["geometry_job"]["job_id"], "status": "queued"})
            return httpx.Response(200, json={**command[0], "status": "ready", "revision": 2, "geometry_revision": 1,
                "analysis_origin": "mock", "manifest_sha256": current["source"]["manifest_sha256"],
                "evidence_observation_ids": [self.observation_id], "excluded_observations": [], "artifacts": [{
                    "artifact_id": "zip-test", "kind": "point_cloud", "url": "https://geometry.example/zip.ply",
                    "media_type": "application/octet-stream", "byte_length": 10, "sha256": "a" * 64}]})

        with httpx.Client(transport=httpx.MockTransport(generator)) as client:
            self.service.capture.process_next(client)
            self.service.capture.process_next(client)
        self.assertEqual(self.client.get(self.base, headers=self.operator).json()["processing_state"], "completed")
        self.assertEqual(self.client.post(self.base + "/complete", headers=self.device, json=complete).status_code, 200)

    def test_archive_request_validation_and_size_limit(self):
        content = self.archive()
        completion_id, ref = self.reference(content)
        body = {"schema_version": "0.1", "request_id": str(uuid4()), "kind": "capture_archive", "completion_id": completion_id,
                "objects": [{"name": "capture.zip", "observation_id": None, "media_type": "application/zip", "byte_length": len(content), "sha256": ref["archive_sha256"]}]}
        for changed in (body | {"completion_id": None}, body | {"kind": "observation_files"},
                        body | {"objects": [body["objects"][0] | {"media_type": "image/jpeg"}]}):
            self.assertEqual(self.client.post(self.base + "/upload-plans", headers=self.device, json=changed).status_code, 422)
        too_big = body | {"objects": [body["objects"][0] | {"byte_length": self.settings.max_archive_bytes + 1}]}
        self.assertEqual(self.client.post(self.base + "/upload-plans", headers=self.device, json=too_big).status_code, 413)
        forbidden = ref | {"archive_key": "projects/another/sessions/other/archives/file.zip"}
        self.assertEqual(self.client.post(self.base + "/complete", headers=self.device,
                                        json={"schema_version": "0.1", "completion_id": completion_id, "capture_ref": forbidden}).status_code, 403)

    def test_corrupt_zip_is_rejected_without_geometry_job(self):
        completion_id, ref = self.stored_reference(b"not a valid ZIP")
        self.assertEqual(self.client.post(self.base + "/complete", headers=self.device,
                                        json={"schema_version": "0.1", "completion_id": completion_id, "capture_ref": ref}).status_code, 202)
        self.service.capture.process_next()
        current = self.client.get(self.base, headers=self.operator).json()
        self.assertEqual(current["completion"]["status"], "rejected")
        self.assertEqual(current["completion"]["error"]["code"], "capture_archive_invalid")
        self.assertEqual(current["capture_state"], "capturing")
        self.assertIsNone(current["geometry_job"])

    def assert_invalid_archive(self, content):
        _, ref = self.stored_reference(content)
        with self.assertRaises(APIError) as raised:
            self.store.validate(self.session, ref)
        self.assertEqual(raised.exception.status_code, 422)

    def test_unsafe_duplicate_symlink_missing_and_unlisted_members_are_rejected(self):
        symlink = zipfile.ZipInfo("link")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = [self.archive(extra=[("../escape", b"x")]), self.archive(extra=[("/absolute", b"x")]),
                 self.archive(extra=[("folder\\windows", b"x")]), self.archive(extra=[("capture-manifest.json", b"{}")]),
                 self.archive(extra=[("CAPTURE-MANIFEST.JSON", b"{}")]), self.archive(extra=[(symlink, b"target")]),
                 self.archive(omit=["capture-manifest.json"]), self.archive(extra=[("unlisted.txt", b"x")]),
                 self.archive(omit=[f"observations/{self.observation_id}/rgb.jpg"])]
        for index, content in enumerate(cases):
            with self.subTest(case=index):
                self.assert_invalid_archive(content)

    def test_member_hash_and_crc_are_checked(self):
        key = f"observations/{self.observation_id}/rgb.jpg"
        self.assert_invalid_archive(self.archive(payload_override={key: b"x" * len(self.payloads["rgb.jpg"])}))
        content = bytearray(self.archive(compression=zipfile.ZIP_STORED))
        position = content.index(self.payloads["rgb.jpg"])
        content[position] ^= 1
        self.assert_invalid_archive(bytes(content))

    def test_archive_and_unpacked_limits_are_enforced_before_acceptance(self):
        content = self.archive()
        _, ref = self.stored_reference(content)
        for settings in (replace(self.settings, max_archive_bytes=len(content) - 1),
                         replace(self.settings, max_unpacked_archive_bytes=10), replace(self.settings, max_archive_entries=2),
                         replace(self.settings, max_manifest_bytes=10)):
            with self.subTest(settings=settings), self.assertRaises(APIError) as raised:
                SupabaseCaptureSourceStore(settings, self.cloud).validate(self.session, ref)
            self.assertEqual(raised.exception.status_code, 422)

    def test_invalid_utf8_encryption_and_unsupported_compression_are_rejected(self):
        invalid_name = bytearray(self.archive())
        central = invalid_name.index(b"PK\x01\x02")
        flags = int.from_bytes(invalid_name[central + 8:central + 10], "little") | 0x800
        invalid_name[central + 8:central + 10] = flags.to_bytes(2, "little")
        invalid_name[central + 46] = 0xff
        self.assert_invalid_archive(bytes(invalid_name))
        encrypted = bytearray(self.archive())
        central = encrypted.index(b"PK\x01\x02")
        flags = int.from_bytes(encrypted[central + 8:central + 10], "little") | 1
        encrypted[central + 8:central + 10] = flags.to_bytes(2, "little")
        self.assert_invalid_archive(bytes(encrypted))
        self.assert_invalid_archive(self.archive(compression=zipfile.ZIP_BZIP2))

    def test_finder_metadata_is_ignored_after_path_and_size_checks(self):
        content = self.archive(extra=[(".DS_Store", b"metadata"), ("__MACOSX/._capture-manifest.json", b"metadata")])
        _, ref = self.stored_reference(content)
        result = self.store.validate(self.session, ref)
        self.assertEqual(result["observation_count"], 1)
        self.assertEqual(len(result["files"]), 2)

    def test_zip_cannot_change_previously_declared_photo_bytes(self):
        response = self.client.post(self.base + "/upload-plans", headers=self.device, json={
            "schema_version": "0.1", "request_id": str(uuid4()), "kind": "observation_files",
            "objects": [{"name": "rgb.jpg", "observation_id": self.observation_id, "media_type": "image/jpeg",
                         "byte_length": len(self.payloads["rgb.jpg"]), "sha256": "c" * 64}]})
        self.assertEqual(response.status_code, 201, response.text)
        completion_id, ref = self.stored_reference(self.archive())
        response = self.client.post(self.base + "/complete", headers=self.device,
                                    json={"schema_version": "0.1", "completion_id": completion_id, "capture_ref": ref})
        self.assertEqual(response.status_code, 202)
        self.service.capture.process_next()
        current = self.client.get(self.base, headers=self.operator).json()
        self.assertEqual(current["completion"]["status"], "rejected")
        self.assertEqual(current["completion"]["error"]["code"], "capture_source_conflict")
        self.assertIsNone(current["geometry_job"])


if __name__ == "__main__":
    unittest.main()
