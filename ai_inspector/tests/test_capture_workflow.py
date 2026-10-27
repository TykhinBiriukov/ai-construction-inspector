import json
import hashlib
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from ai_inspector.api import create_app
from ai_inspector.config import Settings
from ai_inspector.errors import APIError
from ai_inspector.capture_sources import S3CaptureSourceStore


class FakeSources:
    def __init__(self, error=None):
        self.error = error
        self.validated = []
        self.file_names = ["frame.json", "rgb.jpg", "depth.f32"]

    def validate(self, session, capture_ref):
        self.validated.append(capture_ref.copy())
        if self.error:
            raise self.error
        files = [{"observation_id": "test-observation", "name": name,
                  "object_key": session["storage"]["key_prefix"] + "observations/test/" + name,
                  "version_id": f"version-{name}", "byte_length": 10, "sha256": "a" * 64}
                 for name in self.file_names]
        return {"observations": [], "observation_count": 1, "files": files, "excluded_observations": []}

    def download_access(self, capture_ref, files):
        return {
            "manifest_url": "https://s3.example/manifest?exact-version=" + capture_ref["manifest_version_id"],
            "expires_at": "2026-10-27T16:00:00Z",
            "items": [{**item, "download_url": "https://s3.example/file?version=" + item["version_id"],
                       "expires_at": "2026-10-27T16:00:00Z"} for item in files],
        }


class CaptureWorkflowTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / ".test-data"
        root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(Path(self.temporary.name), "operator-token", "device-token")
        self.operator = {"Authorization": "Bearer operator-token"}
        self.device = {"Authorization": "Bearer device-token"}
        self.client = self.make_client(self.settings)
        self.workflow = self.client.app.state.service.capture
        self.sources = FakeSources()
        self.workflow.source_store = self.sources
        intake = self.client.post(
            "/v1/inspections", headers=self.operator,
            data={"schema_version": "0.1", "request_id": str(uuid4()), "project_id": "demo-building",
                  "room_id": "room-204", "prompt": "Inspect this room"},
            files={"bim_model": ("room.ifc", b"opaque BIM transport fixture", "application/octet-stream")},
        )
        self.assertEqual(intake.status_code, 201, intake.text)
        self.inspection = intake.json()
        session = self.client.post(
            f"/v1/inspections/{self.inspection['inspection_id']}/capture-sessions", headers=self.operator,
            json={"schema_version": "0.1", "request_id": str(uuid4()), "device_id": "iphone-1",
                  "room_id": "room-204", "capture_origin": "device", "input_mode": "live_capture"},
        )
        self.assertEqual(session.status_code, 201, session.text)
        self.session = session.json()
        self.base = f"/v1/sessions/{self.session['session_id']}"
        self.start = {"schema_version": "0.1", "operation_id": str(uuid4()),
                      "device": {"name": "iPhone", "os_version": "test", "app_version": "0.1"}}
        self.assertEqual(self.client.post(self.base + "/capture-start", headers=self.device, json=self.start).status_code, 201)

    def make_client(self, settings):
        client = TestClient(create_app(settings))
        self.addCleanup(client.close)
        return client

    def configured_settings(self, **overrides):
        return replace(self.settings, **({
            "geometry_base_url": "https://geometry.example", "inspector_public_base_url": "https://inspector.example",
            "geometry_api_token": "outgoing-service-token", "geometry_service_token": "incoming-service-token",
        } | overrides))

    def completion(self):
        completion_id = str(uuid4())
        return {"schema_version": "0.1", "completion_id": completion_id, "capture_ref": {
            "storage_id": "capture-s3",
            "manifest_key": self.session["storage"]["key_prefix"] + f"manifests/{completion_id}.json",
            "manifest_version_id": "manifest-version-1", "manifest_byte_length": 512, "manifest_sha256": "b" * 64,
        }}

    def submit(self):
        body = self.completion()
        response = self.client.post(self.base + "/complete", json=body, headers=self.device)
        self.assertEqual(response.status_code, 202, response.text)
        return body

    def accepted(self):
        body = self.submit()
        self.assertTrue(self.workflow.process_next())
        self.assertEqual(self.current()["completion"]["status"], "accepted")
        return body

    def current(self, client=None):
        response = (client or self.client).get(self.base, headers=self.operator)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def make_due(self, workflow=None):
        with (workflow or self.workflow).database.transaction() as connection:
            connection.execute("UPDATE work_items SET next_attempt_at=0 WHERE status='pending'")

    def test_completion_is_durable_and_geometry_address_stays_empty(self):
        body = self.submit()
        pending = self.current()
        self.assertEqual((pending["capture_state"], pending["processing_state"]), ("submitted", "validating"))
        self.assertIsNone(pending["geometry_job"])
        self.assertEqual(pending["source"]["capture_ref"], body["capture_ref"])
        replay = self.client.post(self.base + "/complete", json=body, headers=self.device)
        self.assertEqual(replay.status_code, 200)
        restarted = self.make_client(self.settings)
        workflow = restarted.app.state.service.capture
        workflow.source_store = self.sources
        self.assertTrue(workflow.process_next())
        accepted = self.current(restarted)
        self.assertEqual(accepted["capture_state"], "sealed")
        self.assertEqual(accepted["processing_state"], "queued_geometry")
        self.assertEqual(self.sources.validated, [body["capture_ref"]])
        calls = []
        with httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request))) as generator:
            self.assertTrue(workflow.process_next(generator))
        self.assertEqual(calls, [])
        job = self.current(restarted)["geometry_job"]
        self.assertEqual(job["dispatch_status"], "pending")
        self.assertEqual(job["dispatch_error"]["code"], "geometry_not_configured")
        self.assertEqual(self.settings.geometry_base_url, "")

    def test_missing_s3_configuration_keeps_completion_pending(self):
        self.workflow._source_store = None
        self.submit()
        self.assertTrue(self.workflow.process_next())
        current = self.current()
        self.assertEqual(current["completion"]["status"], "pending")
        self.assertEqual(current["source"]["validation_error"]["code"], "capture_storage_not_configured")
        self.assertIsNone(current["geometry_job"])
        self.assertEqual(current["capture_state"], "submitted")

    def test_complete_replay_conflict_pending_exclusion_and_closed_session(self):
        body = self.submit()
        changed = body | {"capture_ref": body["capture_ref"] | {"manifest_sha256": "c" * 64}}
        self.assertEqual(self.client.post(self.base + "/complete", headers=self.device, json=changed).status_code, 409)
        response = self.client.post(self.base + "/complete", headers=self.device, json=self.completion())
        self.assertEqual(response.json()["code"], "completion_in_progress")
        self.workflow.process_next()
        replay = self.client.post(self.base + "/complete", headers=self.device, json=body)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["completion"]["status"], "accepted")
        response = self.client.post(self.base + "/complete", headers=self.device, json=self.completion())
        self.assertEqual(response.json()["code"], "session_closed")
        with self.workflow.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM geometry_jobs").fetchone()[0], 1)

    def test_bad_source_rejected_without_generator_job_then_new_completion_can_succeed(self):
        self.sources.error = APIError(422, "capture_source_incomplete", "Missing file", details={"missing_files": ["rgb.jpg"]})
        old = self.submit()
        self.workflow.process_next()
        current = self.current()
        self.assertEqual(current["capture_state"], "capturing")
        self.assertEqual(current["completion"]["status"], "rejected")
        self.assertIsNone(current["geometry_job"])
        self.sources.error = None
        self.accepted()
        old_replay = self.client.post(self.base + "/complete", headers=self.device, json=old)
        self.assertEqual(old_replay.json()["completion"]["status"], "rejected")

    def test_temporary_source_error_retries_same_completion(self):
        self.sources.error = APIError(503, "capture_storage_unavailable", "Temporary error", retryable=True)
        body = self.submit()
        self.workflow.process_next()
        self.assertEqual(self.current()["completion"]["status"], "pending")
        self.sources.error = None
        self.make_due()
        self.workflow.process_next()
        self.assertEqual(self.current()["completion"]["completion_id"], body["completion_id"])
        self.assertEqual(self.current()["completion"]["status"], "accepted")

    def test_successful_forwarding_uses_fixed_internal_source_link_and_service_identity(self):
        body = self.accepted()
        client = self.make_client(self.configured_settings(geometry_profile_version="later-profile"))
        workflow = client.app.state.service.capture
        workflow.source_store = self.sources
        job_id = self.current()["geometry_job"]["job_id"]
        requests = []

        def generator(request):
            requests.append(request)
            command = json.loads(request.content)
            self.assertEqual(request.method, "PUT")
            self.assertEqual(str(request.url), f"https://geometry.example/internal/v1/jobs/{job_id}")
            self.assertEqual(request.headers["authorization"], "Bearer outgoing-service-token")
            self.assertEqual(command["source_ref"], {
                "url": f"https://inspector.example/internal/v1/sessions/{self.session['session_id']}/capture-source",
                "manifest_sha256": body["capture_ref"]["manifest_sha256"], "manifest_version_id": "manifest-version-1",
            })
            self.assertEqual(command["processing_profile_version"], "geometry-v1")
            return httpx.Response(201, json={"job_id": job_id, "status": "queued"})

        with httpx.Client(transport=httpx.MockTransport(generator)) as generator_client:
            workflow.process_next(generator_client)
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.current(client)["geometry_job"]["dispatch_status"], "delivered")
        self.assertIsNone(self.current(client)["result"])
        dispatch = client.post(self.base + "/geometry-jobs/dispatch", headers=self.operator)
        self.assertEqual(dispatch.status_code, 200)
        self.assertEqual(dispatch.json()["job_id"], job_id)

    def test_timeout_and_process_restart_reuse_identical_job_and_command_bytes(self):
        self.accepted()
        settings = self.configured_settings()
        first = self.make_client(settings).app.state.service.capture
        requests = []

        def timed_out(request):
            requests.append(request.content)
            raise httpx.ReadTimeout("simulated timeout after the generator accepted the job")

        with httpx.Client(transport=httpx.MockTransport(timed_out)) as generator:
            first.process_next(generator)
        original_id = self.current()["geometry_job"]["job_id"]
        self.assertEqual(self.current()["capture_state"], "sealed")
        restarted = self.make_client(replace(settings, inspector_public_base_url="https://changed.example"))
        workflow = restarted.app.state.service.capture
        self.make_due(workflow)

        def accepted(request):
            requests.append(request.content)
            return httpx.Response(200, json={"job_id": original_id, "status": "ready"})

        with httpx.Client(transport=httpx.MockTransport(accepted)) as generator:
            workflow.process_next(generator)
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(self.current(restarted)["geometry_job"]["dispatch_status"], "delivered")
        self.assertIsNone(self.current(restarted)["result"])
        self.assertEqual(self.current(restarted)["geometry_job"]["geometry_revision"], None)

    def test_generator_rejection_and_redirect_do_not_trigger_duplicate_jobs(self):
        self.accepted()
        workflow = self.make_client(self.configured_settings()).app.state.service.capture
        calls = []

        def rejected(request):
            calls.append(str(request.url))
            return httpx.Response(302, headers={"Location": "https://unrelated.example"})

        with httpx.Client(transport=httpx.MockTransport(rejected)) as generator:
            workflow.process_next(generator)
            self.assertFalse(workflow.process_next(generator))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.current()["geometry_job"]["dispatch_status"], "failed")
        self.assertEqual(self.current()["capture_state"], "sealed")

    def test_rate_limit_retry_after_and_invalid_receipt(self):
        self.accepted()
        workflow = self.make_client(self.configured_settings()).app.state.service.capture
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429, headers={"Retry-After": "120"}))) as generator:
            workflow.process_next(generator)
        with workflow.database.connection() as connection:
            row = connection.execute("SELECT next_attempt_at FROM work_items WHERE kind='dispatch'").fetchone()
            self.assertGreater(row[0], time.time() + 100)
        self.make_due(workflow)
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(201, json={"job_id": "wrong", "status": "queued"}))) as generator:
            workflow.process_next(generator)
        self.assertEqual(self.current()["geometry_job"]["dispatch_error"]["code"], "invalid_geometry_receipt")

    def test_source_access_is_service_only_and_paginates_pinned_files(self):
        self.accepted()
        client = self.make_client(self.configured_settings())
        client.app.state.service.capture.source_store = self.sources
        url = f"/internal/v1/sessions/{self.session['session_id']}/capture-source"
        for credentials in (self.operator, self.device, {}):
            self.assertEqual(client.get(url, headers=credentials).status_code, 401)
        service = {"Authorization": "Bearer incoming-service-token"}
        response = client.get(url, headers=service)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["manifest_version_id"], "manifest-version-1")
        page = client.get(url + "/files", params={"limit": 2}, headers=service).json()
        self.assertEqual(len(page["items"]), 2)
        last = client.get(url + "/files", params={"after": page["next_cursor"]}, headers=service).json()
        self.assertEqual(len(last["items"]), 1)
        self.assertIsNone(last["next_cursor"])
        self.assertEqual(last["items"][0]["version_id"], "version-depth.f32")
        self.assertEqual(client.get(self.base, headers=service).status_code, 401)

    def test_completion_authorization_version_prefix_and_start_gate(self):
        body = self.completion()
        self.assertEqual(self.client.post(self.base + "/complete", json=body, headers=self.operator).status_code, 403)
        for field, value, status in (("storage_id", "other-storage", 403), ("manifest_key", "https://example.test/file", 403),
                                      ("manifest_version_id", "null", 422), ("manifest_sha256", "BAD", 422)):
            changed = body | {"capture_ref": body["capture_ref"] | {field: value}}
            self.assertEqual(self.client.post(self.base + "/complete", json=changed, headers=self.device).status_code, status)
        self.assertEqual(self.client.post(self.base + "/geometry-jobs/dispatch", headers=self.operator).status_code, 409)
        with self.workflow.database.transaction() as connection:
            state = self.current_state_for_update(connection)
            state["capture_state"] = "assigned"
            connection.execute("UPDATE sessions SET data=? WHERE session_id=?", (json.dumps(state), state["session_id"]))
        response = self.client.post(self.base + "/complete", json=body, headers=self.device)
        self.assertEqual(response.json()["code"], "capture_not_started")

    def current_state_for_update(self, connection):
        return self.client.app.state.service._load(connection, "sessions", self.session["session_id"])

    def test_expired_work_lease_is_recovered_after_worker_crash(self):
        self.submit()
        with self.workflow.database.transaction() as connection:
            connection.execute("UPDATE work_items SET status='processing', lease_until=0, lease_token='abandoned'")
        restarted = self.make_client(self.settings).app.state.service.capture
        restarted.source_store = self.sources
        self.assertTrue(restarted.process_next())
        self.assertEqual(self.current()["capture_state"], "sealed")

    def test_background_worker_processes_capture_without_manual_dispatch(self):
        with self.client:
            self.submit()
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                if self.current()["completion"]["status"] == "accepted":
                    break
                time.sleep(0.02)
            self.assertEqual(self.current()["completion"]["status"], "accepted")
        self.assertFalse(self.client.app.state.capture_worker.is_alive())

    def test_rgb_only_is_accepted_but_waits_for_explicit_generator_capability(self):
        self.sources.file_names = ["frame.json", "rgb.jpg"]
        self.accepted()
        client = self.make_client(self.configured_settings())
        workflow = client.app.state.service.capture
        calls = []
        with httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request))) as generator:
            workflow.process_next(generator)
        self.assertEqual(calls, [])
        self.assertEqual(self.current()["geometry_job"]["dispatch_error"]["code"], "geometry_depth_required")
        self.assertEqual(self.current()["completion"]["status"], "accepted")
        enabled = self.make_client(self.configured_settings(geometry_accepts_rgb_only=True))
        workflow = enabled.app.state.service.capture
        enabled.post(self.base + "/geometry-jobs/dispatch", headers=self.operator)

        def accepted(request):
            command = json.loads(request.content)
            return httpx.Response(201, json={"job_id": command["job_id"], "status": "queued"})

        with httpx.Client(transport=httpx.MockTransport(accepted)) as generator:
            workflow.process_next(generator)
        self.assertEqual(self.current()["geometry_job"]["dispatch_status"], "delivered")

    def test_schema_one_migration_preserves_existing_session_and_receipts(self):
        with self.workflow.database.connection() as connection:
            for table in ("work_items", "geometry_jobs", "capture_completions"):
                connection.execute(f"DROP TABLE {table}")
            connection.execute("PRAGMA user_version=1")
        restarted = self.make_client(self.settings)
        self.assertEqual(self.current(restarted)["session_id"], self.session["session_id"])
        replay = restarted.post(self.base + "/capture-start", json=self.start, headers=self.device)
        self.assertEqual(replay.status_code, 200)
        with restarted.app.state.service.database.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)

    def test_real_validator_source_routes_and_generator_handoff_use_same_versions(self):
        # Join the real S3 adapter, completion worker, service download API and
        # generator transport. The fake is only the S3/HTTP network boundary.
        from test_capture_sources import FakeS3
        settings = self.configured_settings(capture_bucket="test-capture-bucket")
        client = self.make_client(settings)
        workflow = client.app.state.service.capture
        s3 = FakeS3()
        workflow.source_store = S3CaptureSourceStore(settings, s3_client=s3)
        observation_id, segment_id, completion_id = str(uuid4()), str(uuid4()), str(uuid4())
        prefix = self.session["storage"]["key_prefix"]
        payloads = {"rgb.jpg": b"RGB transport fixture", "depth.f32": b"\x00\x00\x80\x3f"}

        def descriptor(name):
            return {"name": name, "byte_length": len(payloads[name]), "sha256": hashlib.sha256(payloads[name]).hexdigest()}

        frame = {"schema_version": "0.2", "session_id": self.session["session_id"],
                 "observation_id": observation_id, "frame_id": observation_id, "segment_id": segment_id,
                 "capture_origin": "device", "instruction_id": None, "tracking_state": "normal",
                 "rgb": descriptor("rgb.jpg"), "depth": descriptor("depth.f32"), "confidence": None,
                 "depth_size": [1, 1], "depth_unit": "m", "depth_source": "sceneDepth"}
        payloads["frame.json"] = json.dumps(frame).encode()
        files = []
        for name, content in payloads.items():
            key, version = f"{prefix}observations/{observation_id}/{name}", "pinned-" + name
            s3.objects[key, version] = content
            files.append(descriptor(name) | {"object_key": key, "version_id": version})
        manifest = {"schema_version": "0.1", "format": "capture_manifest_v1",
                    **{name: self.session[name] for name in ("session_id", "inspection_id", "project_id", "room_id", "model_version", "capture_origin", "input_mode")},
                    "capture_closed_at": "2026-10-27T14:00:00Z", "observation_count": 1,
                    "segments": [{"segment_id": segment_id, "created_at": "2026-10-27T13:00:00Z", "units": "m",
                                  "coordinate_convention": "arkit_world_camera_v1", "matrix_layout": "rows_column_vectors"}],
                    "observations": [{"observation_id": observation_id, "segment_id": segment_id, "files": files}]}
        manifest_bytes = json.dumps(manifest).encode()
        key = f"{prefix}manifests/{completion_id}.json"
        s3.objects[key, "pinned-manifest"] = manifest_bytes
        request = {"schema_version": "0.1", "completion_id": completion_id, "capture_ref": {
            "storage_id": "capture-s3", "manifest_key": key, "manifest_version_id": "pinned-manifest",
            "manifest_byte_length": len(manifest_bytes), "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        }}
        self.assertEqual(client.post(self.base + "/complete", json=request, headers=self.device).status_code, 202)
        self.assertTrue(workflow.process_next())
        self.assertEqual(self.current()["completion"]["status"], "accepted")
        # A newer S3 object version is outside the accepted inventory.
        s3.objects[files[0]["object_key"], "later-version"] = b"replacement bytes"

        def generator(request):
            command = json.loads(request.content)
            source_path = command["source_ref"]["url"].removeprefix("https://inspector.example")
            headers = {"Authorization": "Bearer incoming-service-token"}
            metadata = client.get(source_path, headers=headers)
            self.assertEqual(metadata.status_code, 200, metadata.text)
            self.assertEqual(metadata.json()["manifest_version_id"], "pinned-manifest")
            download = client.get(source_path + "/files", headers=headers)
            self.assertEqual(download.status_code, 200, download.text)
            self.assertEqual({item["version_id"] for item in download.json()["items"]}, {file["version_id"] for file in files})
            return httpx.Response(201, json={"job_id": command["job_id"], "status": "queued"})

        with httpx.Client(transport=httpx.MockTransport(generator)) as generator_client:
            self.assertTrue(workflow.process_next(generator_client))
        self.assertEqual(self.current()["geometry_job"]["dispatch_status"], "delivered")


if __name__ == "__main__":
    unittest.main()
