import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from ai_inspector.api import create_app
from ai_inspector.config import Settings
from ai_inspector.session_folders import SupabaseSessionFolders


class SessionFolderTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / ".test-data"
        root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(
            Path(self.temporary.name), "operator-test", "device-test",
            supabase_url="https://test-project.supabase.co", supabase_storage_bucket="inspection-captures",
            supabase_secret_key="sb_secret_test_only",
        )
        self.operator = {"Authorization": "Bearer operator-test"}
        self.device = {"Authorization": "Bearer device-test"}
        self.objects = {}
        self.requests = []
        self.lock = threading.Lock()
        self.fail = False
        self.timeout_after_upload = False
        self.client = self.make_client()
        intake = self.client.post(
            "/v1/inspections", headers=self.operator,
            data={"schema_version": "0.1", "request_id": str(uuid4()), "project_id": "demo-building",
                  "room_id": "room-204", "prompt": "Transport test"},
            files={"bim_model": ("room.ifc", b"BIM transport fixture", "application/octet-stream")},
        )
        self.assertEqual(intake.status_code, 201, intake.text)
        self.inspection = intake.json()
        self.url = f"/v1/inspections/{self.inspection['inspection_id']}/capture-sessions"
        self.body = {"schema_version": "0.1", "request_id": str(uuid4()), "device_id": "iphone-1",
                     "room_id": "room-204", "capture_origin": "device", "input_mode": "live_capture"}

    def make_client(self):
        client = TestClient(create_app(self.settings))
        self.addCleanup(client.close)
        storage = httpx.Client(transport=httpx.MockTransport(self.storage_request))
        self.addCleanup(storage.close)
        client.app.state.service.session_folders = SupabaseSessionFolders(self.settings, storage)
        return client

    def storage_request(self, request):
        with self.lock:
            self.requests.append((request.method, str(request.url)))
            self.assertEqual(request.headers["apikey"], self.settings.supabase_secret_key)
            self.assertNotIn("authorization", request.headers)
            if self.fail:
                return httpx.Response(401, json={"message": "secret should not be reflected: " + self.settings.supabase_secret_key})
            key = request.url.path.split("/storage/v1/object/", 1)[1]
            if request.method == "GET":
                return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.headers["x-upsert"], "false")
            if key in self.objects:
                return httpx.Response(409, json={"code": "Duplicate"})
            self.objects[key] = request.content
            if self.timeout_after_upload:
                raise httpx.ReadTimeout("Simulated timeout containing " + self.settings.supabase_secret_key)
            return httpx.Response(200, json={"Key": key})

    def create_session(self, client=None):
        return (client or self.client).post(self.url, json=self.body, headers=self.operator)

    def test_creation_materializes_session_folder_and_returns_safe_storage_metadata(self):
        response = self.create_session()
        self.assertEqual(response.status_code, 201, response.text)
        session = response.json()
        prefix = f"projects/demo-building/sessions/{session['session_id']}/"
        self.assertEqual(session["storage"], {
            "storage_id": "capture-supabase", "provider": "supabase", "project_url": self.settings.supabase_url,
            "bucket": "inspection-captures", "key_prefix": prefix, "folder_status": "ready",
        })
        self.assertEqual(list(self.objects), ["inspection-captures/" + prefix + ".session.json"])
        self.assertEqual(json.loads(next(iter(self.objects.values())))["session_id"], session["session_id"])
        self.assertNotIn(self.settings.supabase_secret_key, response.text)
        assignments = self.client.get("/v1/devices/iphone-1/capture-assignments", headers=self.device)
        self.assertEqual(assignments.json()["assignments"], [session])
        replay = self.create_session()
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), session)
        self.assertEqual(len(self.requests), 1)

    def test_failed_storage_retains_id_and_hides_assignment_until_retry(self):
        self.fail = True
        response = self.create_session()
        self.assertEqual(response.status_code, 503, response.text)
        session_id = response.json()["details"]["session_id"]
        self.assertNotIn(self.settings.supabase_secret_key, response.text)
        assignments = self.client.get("/v1/devices/iphone-1/capture-assignments", headers=self.device).json()
        self.assertEqual(assignments["assignments"], [])
        operation = {"schema_version": "0.1", "operation_id": str(uuid4())}
        acknowledgement = self.client.post(f"/v1/sessions/{session_id}/acknowledgements", headers=self.device, json=operation)
        self.assertEqual(acknowledgement.json()["code"], "session_storage_not_ready")
        self.fail = False
        restarted = self.make_client()
        replay = self.create_session(restarted)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["session_id"], session_id)
        self.assertEqual(replay.json()["storage"]["folder_status"], "ready")
        with restarted.app.state.service.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)

    def test_timeout_after_cloud_write_recovers_same_marker_after_restart(self):
        self.timeout_after_upload = True
        response = self.create_session()
        self.assertEqual(response.status_code, 503)
        session_id = response.json()["details"]["session_id"]
        self.assertNotIn(self.settings.supabase_secret_key, response.text)
        self.timeout_after_upload = False
        replay = self.create_session(self.make_client())
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["session_id"], session_id)
        self.assertEqual(len(self.objects), 1)
        self.assertEqual([method for method, _ in self.requests], ["POST", "POST", "GET"])

    def test_existing_marker_must_match_session_and_is_never_overwritten(self):
        self.fail = True
        failed = self.create_session()
        session_id = failed.json()["details"]["session_id"]
        key = f"inspection-captures/projects/demo-building/sessions/{session_id}/.session.json"
        self.objects[key] = b"foreign marker contents"
        self.fail = False
        response = self.create_session()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "session_folder_conflict")
        self.assertEqual(self.objects[key], b"foreign marker contents")

    def test_concurrent_retries_materialize_one_folder_for_one_session(self):
        with ThreadPoolExecutor(max_workers=5) as executor:
            responses = list(executor.map(lambda _: self.create_session(), range(6)))
        self.assertEqual(sorted(response.status_code for response in responses), [200] * 5 + [201])
        self.assertEqual(len({response.json()["session_id"] for response in responses}), 1)
        self.assertTrue(all(response.json()["storage"]["folder_status"] == "ready" for response in responses))
        self.assertEqual(len(self.objects), 1)

    def test_conflicting_request_does_not_create_another_folder(self):
        self.assertEqual(self.create_session().status_code, 201)
        changed = self.body | {"room_id": "changed-room"}
        response = self.client.post(self.url, json=changed, headers=self.operator)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.requests), 1)

    def test_database_receipt_failure_performs_no_cloud_write(self):
        import sqlite3
        database = self.client.app.state.service.database
        with patch.object(database, "save_receipt", side_effect=sqlite3.OperationalError("simulated failure")):
            response = self.create_session()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.objects, {})

    def test_cloud_redirect_is_not_followed(self):
        calls = []

        def redirect(request):
            calls.append(str(request.url))
            return httpx.Response(307, headers={"Location": "https://unrelated.example"})

        with httpx.Client(transport=httpx.MockTransport(redirect)) as cloud:
            self.client.app.state.service.session_folders = SupabaseSessionFolders(self.settings, cloud)
            response = self.create_session()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(calls), 1)

    def test_supabase_completion_does_not_claim_s3_version_guarantees(self):
        session = self.create_session().json()
        base = f"/v1/sessions/{session['session_id']}"
        self.client.post(base + "/capture-start", headers=self.device, json={
            "schema_version": "0.1", "operation_id": str(uuid4()),
            "device": {"name": "test", "os_version": "test", "app_version": "test"},
        }).raise_for_status()
        completion_id = str(uuid4())
        response = self.client.post(base + "/complete", headers=self.device, json={
            "schema_version": "0.1", "completion_id": completion_id, "capture_ref": {
                "storage_id": "capture-supabase", "manifest_key": session["storage"]["key_prefix"] + f"manifests/{completion_id}.json",
                "manifest_version_id": "not-an-s3-version", "manifest_byte_length": 1, "manifest_sha256": "a" * 64,
            },
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_capture_format")

    def test_dotenv_loads_without_mutating_environment_and_process_values_win(self):
        path = Path(self.temporary.name) / "settings.env"
        path.write_text(
            "INSPECTOR_OPERATOR_TOKEN=file-operator\nINSPECTOR_DEVICE_TOKEN=file-device\n"
            "SUPABASE_URL=https://env-project.supabase.co\nSUPABASE_STORAGE_BUCKET=inspection-captures\n"
            "SUPABASE_SECRET_KEY=sb_secret_env_test\n", encoding="utf-8",
        )
        with patch.dict("os.environ", {"INSPECTOR_OPERATOR_TOKEN": "process-operator"}, clear=True):
            settings = Settings.from_env(path)
            import os
            self.assertNotIn("SUPABASE_SECRET_KEY", os.environ)
        self.assertEqual(settings.operator_token, "process-operator")
        self.assertEqual(settings.device_token, "file-device")
        self.assertEqual(settings.supabase_secret_key, "sb_secret_env_test")
        self.assertNotIn("sb_secret_env_test", repr(settings))
        self.assertNotIn("process-operator", repr(settings))

    def test_partial_and_insecure_supabase_configuration_rejected(self):
        for overrides in ({"supabase_secret_key": ""}, {"supabase_url": "http://test-project.supabase.co"},
                          {"supabase_storage_bucket": "../another-bucket"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                replace(self.settings, **overrides)


if __name__ == "__main__":
    unittest.main()
