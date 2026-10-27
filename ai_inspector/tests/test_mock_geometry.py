import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from ai_inspector.config import Settings
from ai_inspector.errors import APIError
from ai_inspector.mock_geometry import MockGeometry, create_mock_app


def mock_settings(data_dir):
    return Settings(
        data_dir, "test-operator-token", "test-device-token",
        geometry_base_url="http://127.0.0.1:8001", inspector_public_base_url="http://127.0.0.1:8000",
        geometry_api_token="incoming-mock-token", geometry_service_token="outgoing-inspector-token",
    )


class MockGeometryTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / ".test-data"
        root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=root)
        self.assertTrue(Path(self.temporary.name).resolve().is_relative_to(root.resolve()))
        self.addCleanup(self.temporary.cleanup)
        self.settings = mock_settings(Path(self.temporary.name))
        self.service = MockGeometry(self.settings)
        self.job_id, self.session_id, self.inspection_id, self.observation_id = (str(uuid4()) for _ in range(4))
        self.files = []
        prefix = f"projects/demo-building/sessions/{self.session_id}/observations/{self.observation_id}/"
        for name, data in (("frame.json", b'{"frame":"fixture"}'), ("rgb.jpg", b"RGB fixture"), ("depth.f32", b"depth fixture")):
            self.files.append({
                "name": name, "object_key": prefix + name, "version_id": "version-" + name,
                "byte_length": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            })
        self.manifest = {
            "schema_version": "0.1", "format": "capture_manifest_v1", "session_id": self.session_id,
            "inspection_id": self.inspection_id, "project_id": "demo-building", "room_id": "room-204", "model_version": "model-v1",
            "capture_origin": "device", "input_mode": "live_capture", "observation_count": 1,
            "observations": [{"observation_id": self.observation_id, "segment_id": str(uuid4()), "files": self.files}],
        }
        self.manifest_bytes = json.dumps(self.manifest).encode()
        self.command = {
            "schema_version": "0.1", "job_id": self.job_id, "operation_key": str(uuid4()),
            "session_id": self.session_id, "inspection_id": self.inspection_id,
            "project_id": "demo-building", "room_id": "room-204", "model_version": "model-v1",
            "processing_profile_version": "geometry-v1", "source_ref": {
                "url": f"http://127.0.0.1:8000/internal/v1/sessions/{self.session_id}/capture-source",
                "manifest_sha256": hashlib.sha256(self.manifest_bytes).hexdigest(), "manifest_version_id": "manifest-version",
            },
        }
        self.manifest_url = "https://private-storage.example/manifest?signature=storage-secret"
        self.source_url = self.command["source_ref"]["url"]
        self.files_url = self.source_url + "/files"
        self.requests = []
        self.callbacks = []
        self.callback_status = 202
        self.storage_status = 200
        self.bad_hash = False
        self.foreign_files_url = False
        self.file_hash_mismatch = False
        self.redirect_manifest = False

    def transport(self, request):
        self.requests.append(request)
        if str(request.url).startswith("http://127.0.0.1:8000/"):
            self.assertEqual(request.headers.get("authorization"), "Bearer outgoing-inspector-token")
        if str(request.url) == self.source_url:
            return httpx.Response(200, json={
                "session_id": self.session_id, "job_id": self.job_id,
                "manifest_sha256": self.command["source_ref"]["manifest_sha256"], "manifest_version_id": "manifest-version",
                "manifest_byte_length": len(self.manifest_bytes), "manifest_url": self.manifest_url,
                "files_url": "https://foreign.example/files" if self.foreign_files_url else self.files_url,
            })
        if str(request.url) == self.manifest_url:
            self.assertNotIn("authorization", request.headers)
            if self.redirect_manifest:
                return httpx.Response(302, headers={"Location": "https://foreign.example/manifest"})
            content = b"x" * len(self.manifest_bytes) if self.bad_hash else self.manifest_bytes
            return httpx.Response(self.storage_status, content=content)
        if str(request.url).split("?")[0] == self.files_url:
            after = request.url.params.get("after")
            entries = self.files[:1] if after is None else self.files[1:]
            items = [{
                "observation_id": self.observation_id, **item,
                "download_url": "https://private-storage.example/file?signature=do-not-fetch",
            } for item in entries]
            if self.file_hash_mismatch:
                items[0]["sha256"] = "0" * 64
            return httpx.Response(200, json={"items": items, "next_cursor": "page-two" if after is None else None})
        if request.url.path == "/internal/v1/geometry-events":
            self.callbacks.append(json.loads(request.content))
            return httpx.Response(self.callback_status, json={"status": "accepted"})
        raise AssertionError(f"Unexpected mock transport request: {request.method} {request.url.host}{request.url.path}")

    def process(self):
        # Default client auth deliberately exercises the storage-token boundary.
        with httpx.Client(transport=httpx.MockTransport(self.transport), headers={"Authorization": "must-not-leak"}) as client:
            return self.service.process_next(client)

    def submit(self):
        body, status = self.service.submit(self.job_id, self.command)
        self.assertEqual(status, 201)
        return body

    def due(self):
        with self.service._transaction() as connection:
            connection.execute("UPDATE mock_geometry_jobs SET next_attempt_at=0,callback_next_attempt_at=0,lease_until=0")

    def assert_error(self, callback, status, code):
        with self.assertRaises(APIError) as caught:
            callback()
        self.assertEqual(caught.exception.status_code, status)
        self.assertEqual(caught.exception.body["code"], code)

    def test_submission_and_idempotency_survive_restart(self):
        first = self.submit()
        self.assertEqual((first["status"], first["revision"], first["analysis_origin"]), ("queued", 1, "mock"))
        restarted = MockGeometry(self.settings)
        replay, status = restarted.submit(self.job_id, copy.deepcopy(self.command))
        self.assertEqual((replay, status), (first, 200))
        self.assert_error(lambda: restarted.submit(self.job_id, self.command | {"room_id": "room-205"}), 409, "idempotency_conflict")
        another_id = str(uuid4())
        self.assert_error(lambda: restarted.submit(another_id, self.command | {"job_id": another_id}), 409, "idempotency_conflict")

    def test_source_url_is_fixed_to_configured_inspector_session(self):
        for url in (
            "https://foreign.example/capture-source", self.source_url + "?callback=evil",
            f"http://127.0.0.1:8000/internal/v1/sessions/{uuid4()}/capture-source",
        ):
            command = self.command | {"source_ref": self.command["source_ref"] | {"url": url}}
            self.assert_error(lambda: self.service.submit(self.job_id, command), 422, "invalid_source_url")
        self.assert_error(lambda: self.service.submit(self.job_id, self.command | {"project_id": "other-project"}), 403, "forbidden")

    def test_worker_generates_only_labelled_synthetic_ply_and_posts_callback(self):
        self.submit()
        self.assertTrue(self.process())
        job = self.service.get(self.job_id)
        self.assertEqual((job["status"], job["revision"], job["geometry_revision"]), ("ready", 3, 1))
        self.assertEqual(job["analysis_origin"], "mock")
        self.assertEqual(job["evidence_observation_ids"], [self.observation_id])
        self.assertEqual(job["excluded_observations"], [])
        artifact = self.service.artifact(self.job_id)
        metadata = job["artifacts"][0]
        self.assertIn(b"SYNTHETIC MOCK: not reconstructed", artifact)
        self.assertEqual(metadata["sha256"], hashlib.sha256(artifact).hexdigest())
        self.assertEqual(metadata["byte_length"], len(artifact))
        self.assertTrue(metadata["is_synthetic"])
        self.assertEqual(metadata["url"], f"http://127.0.0.1:8001/internal/v1/artifacts/{self.job_id}.ply")
        self.assertEqual(len(self.callbacks), 1)
        event = self.callbacks[0]
        self.assertEqual((event["type"], event["job_revision"], event["manifest_sha256"]), ("geometry.ready", 3, self.command["source_ref"]["manifest_sha256"]))
        self.assertFalse(any("do-not-fetch" in str(request.url) for request in self.requests))
        self.assertFalse(self.process())
        self.assertEqual(MockGeometry(self.settings).artifact(self.job_id), artifact)

    def test_callback_failure_keeps_ready_job_and_outbox_survives_restart(self):
        self.submit()
        self.callback_status = 503
        self.process()
        self.assertEqual(self.service.get(self.job_id)["status"], "ready")
        original_event = self.callbacks[0]
        self.service = MockGeometry(self.settings)
        self.callback_status = 202
        self.due()
        count = len(self.requests)
        self.assertTrue(self.process())
        self.assertEqual(self.callbacks[-1], original_event)
        self.assertEqual(len(self.requests), count + 1, "Callback retry must not regenerate or reread the source")

    def test_source_transfer_failure_retries_same_job(self):
        self.submit()
        self.storage_status = 403
        self.process()
        self.assertEqual(self.service.get(self.job_id)["status"], "fetching")
        self.assertEqual(self.callbacks, [])
        self.storage_status = 200
        self.due()
        self.process()
        self.assertEqual((self.service.get(self.job_id)["job_id"], self.service.get(self.job_id)["revision"]), (self.job_id, 3))

    def test_manifest_hash_mismatch_is_failed_without_artifact(self):
        self.submit()
        self.bad_hash = True
        self.process()
        job = self.service.get(self.job_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "mock_source_invalid")
        self.assertNotIn("storage-secret", repr(job["error"]))
        self.assertEqual(self.callbacks[0]["type"], "geometry.failed")
        self.assert_error(lambda: self.service.artifact(self.job_id), 404, "not_found")

    def test_file_inventory_mismatch_and_foreign_files_route_fail(self):
        self.submit()
        self.file_hash_mismatch = True
        self.process()
        self.assertEqual(self.service.get(self.job_id)["status"], "failed")
        # A separate durable job verifies the files route before downloading its manifest.
        self.command["job_id"] = self.job_id = str(uuid4())
        self.command["operation_key"] = str(uuid4())
        self.file_hash_mismatch = False
        self.foreign_files_url = True
        self.submit()
        count = len(self.requests)
        self.process()
        self.assertEqual(self.service.get(self.job_id)["status"], "failed")
        self.assertFalse(any(request.url.host == "foreign.example" for request in self.requests[count:]))

    def test_signed_download_redirect_is_not_followed(self):
        self.submit()
        self.redirect_manifest = True
        self.process()
        self.assertEqual(self.service.get(self.job_id)["status"], "fetching")
        self.assertFalse(any(request.url.host == "foreign.example" for request in self.requests))

    def test_abandoned_fetching_job_resumes_after_process_restart(self):
        self.submit()
        self.assertIsNotNone(self.service._claim())
        self.service = MockGeometry(self.settings)
        self.due()
        self.process()
        self.assertEqual(self.service.get(self.job_id)["status"], "ready")
        self.assertEqual(self.service.get(self.job_id)["revision"], 3)

    def test_changed_inspector_configuration_does_not_send_token_to_previous_host(self):
        self.submit()
        self.service = MockGeometry(replace(self.settings, inspector_public_base_url="http://localhost:9000"))
        self.process()
        self.assertEqual(self.requests, [])
        self.assertEqual(self.service.get(self.job_id)["status"], "fetching")

    def test_supabase_manifest_uses_original_hash_without_s3_versions(self):
        self.manifest["format"] = "capture_manifest_supabase_v1"
        for item in self.files:
            del item["version_id"]
        self.manifest_bytes = json.dumps(self.manifest).encode()
        self.command["source_ref"]["manifest_sha256"] = hashlib.sha256(self.manifest_bytes).hexdigest()
        del self.command["source_ref"]["manifest_version_id"]
        self.submit()
        self.process()
        self.assertEqual(self.service.get(self.job_id)["status"], "ready")
        self.assertEqual(self.service.get(self.job_id)["manifest_sha256"], hashlib.sha256(self.manifest_bytes).hexdigest())


class MockGeometryHTTPTests(unittest.TestCase):
    def test_mock_routes_require_service_token_and_advertise_mock_origin(self):
        # Run with the app's full test suite outside the Windows network sandbox.
        root = Path(__file__).resolve().parents[1] / ".test-data"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            self.assertTrue(Path(temporary).resolve().is_relative_to(root.resolve()))
            app = create_mock_app(mock_settings(Path(temporary)))
            with TestClient(app) as client:
                health = client.get("/health")
                self.assertEqual(health.json()["analysis_origin"], "mock")
                self.assertEqual(health.headers["x-geometry-origin"], "mock")
                self.assertEqual(client.get(f"/internal/v1/jobs/{uuid4()}").status_code, 401)
                response = client.get(f"/internal/v1/jobs/{uuid4()}", headers={"Authorization": "Bearer incoming-mock-token"})
                self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
