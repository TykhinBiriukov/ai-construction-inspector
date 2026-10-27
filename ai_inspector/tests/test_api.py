import base64
import hashlib
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from ai_inspector.api import create_app
from ai_inspector.config import Settings


class SessionAPITests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parents[1] / ".test-data"
        test_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=test_root)
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(Path(self.temporary.name), "test-operator-token", "test-device-token")
        self.operator = {"Authorization": "Bearer test-operator-token"}
        self.device = {"Authorization": "Bearer test-device-token"}
        self.client = self.make_client()

    def make_client(self, settings=None):
        client = TestClient(create_app(settings or self.settings))
        self.addCleanup(client.close)
        return client

    def intake_body(self, **overrides):
        return {
            "schema_version": "0.1", "request_id": str(uuid4()),
            "project_id": "demo-building", "room_id": "room-204",
            "prompt": "Inspect the doorway dimensions.",
        } | overrides

    def upload(self, body=None, content=b"opaque BIM test bytes", client=None, filename="room.ifc"):
        return (client or self.client).post(
            "/v1/inspections", data=body or self.intake_body(), headers=self.operator,
            files={"bim_model": (filename, content, "application/octet-stream")},
        )

    def create_inspection(self, **kwargs):
        response = self.upload(**kwargs)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def session_body(self, **overrides):
        return {
            "schema_version": "0.1", "request_id": str(uuid4()),
            "device_id": "iphone-1", "room_id": "room-204",
            "capture_origin": "device", "input_mode": "live_capture",
            "parent_session_id": None,
        } | overrides

    def session_url(self, inspection):
        return f"/v1/inspections/{inspection['inspection_id']}/capture-sessions"

    def create_session(self, inspection=None, body=None):
        inspection = inspection or self.create_inspection()
        response = self.client.post(self.session_url(inspection), json=body or self.session_body(), headers=self.operator)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def start_body(self, **overrides):
        return {
            "schema_version": "0.1", "operation_id": str(uuid4()),
            "device": {"name": "iPhone", "os_version": "test-os", "app_version": "0.1.0"},
        } | overrides

    def assert_error(self, response, status, code=None):
        self.assertEqual(response.status_code, status, response.text)
        body = response.json()
        self.assertEqual(set(body), {"code", "message", "retryable", "details"})
        if code:
            self.assertEqual(body["code"], code)

    def test_full_intake_assignment_acknowledgement_and_capture_start(self):
        content = b"BIM bytes are stored without reserialization\x00\xff"
        inspection = self.create_inspection(content=content)
        self.assertEqual(inspection["bim_model"]["status"], "stored_unvalidated")
        self.assertEqual(inspection["bim_model"]["sha256"], hashlib.sha256(content).hexdigest())
        download = self.client.get(inspection["bim_model"]["download_url"], headers=self.operator)
        self.assertEqual(download.content, content)
        self.assertEqual(download.headers["cache-control"], "private, no-store")

        session = self.create_session(inspection)
        for name in ("project_id", "room_id", "model_version", "policy_version", "model_is_synthetic"):
            self.assertEqual(session[name], inspection[name])
        self.assertEqual(session["revision"], 1)
        self.assertTrue(session["created_at"].endswith("Z"))
        for name in ("completion", "source", "geometry_job", "display_view_id", "result", "error"):
            self.assertIsNone(session[name])
        assignments = self.client.get("/v1/devices/iphone-1/capture-assignments", headers=self.device).json()
        self.assertEqual(assignments["assignments"], [session])
        self.assertIsNone(assignments["next_cursor"])

        session_url = f"/v1/sessions/{session['session_id']}"
        acknowledged = self.client.post(
            session_url + "/acknowledgements",
            json={"schema_version": "0.1", "operation_id": str(uuid4())}, headers=self.device,
        )
        self.assertEqual(acknowledged.status_code, 201, acknowledged.text)
        current = self.client.get(session_url, headers=self.operator).json()
        self.assertEqual(current["capture_state"], "assigned")
        self.assertEqual(current["processing_state"], "idle")
        self.assertEqual(current["revision"], 2)
        self.assertEqual(current["device_acknowledged_at"], acknowledged.json()["acknowledged_at"])
        started = self.client.post(session_url + "/capture-start", json=self.start_body(), headers=self.device)
        self.assertEqual(started.status_code, 201, started.text)
        self.assertEqual(started.json()["capture_state"], "capturing")
        current = self.client.get(session_url, headers=self.device).json()
        self.assertEqual(current["revision"], 3)
        self.assertEqual(current["processing_state"], "idle")
        self.assertIsNone(current["result"])

    def test_intake_idempotency_and_conflicting_file_or_prompt(self):
        body = self.intake_body()
        first = self.upload(body)
        self.assertEqual(first.status_code, 201)
        replay = self.upload(body, client=self.make_client())
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.json(), replay.json())
        self.assert_error(self.upload(body | {"prompt": "A changed request"}), 409, "idempotency_conflict")
        self.assert_error(self.upload(body, content=b"changed bytes"), 409, "idempotency_conflict")
        self.assertEqual(len(list((self.settings.data_dir / "models").iterdir())), 1)
        self.assertEqual(list((self.settings.data_dir / "staging").iterdir()), [])

    def test_sessions_and_operation_receipts_survive_restart(self):
        inspection = self.create_inspection()
        body = self.session_body()
        session = self.create_session(inspection, body)
        base = f"/v1/sessions/{session['session_id']}"
        ack_body = {"schema_version": "0.1", "operation_id": str(uuid4())}
        start_body = self.start_body()
        ack = self.client.post(base + "/acknowledgements", json=ack_body, headers=self.device)
        start = self.client.post(base + "/capture-start", json=start_body, headers=self.device)
        self.client.close()
        restarted = self.make_client()
        self.assertEqual(restarted.get(f"/v1/inspections/{inspection['inspection_id']}", headers=self.operator).json(), inspection)
        replay = restarted.post(self.session_url(inspection), json=body, headers=self.operator)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), session)
        for suffix, payload, original in (
            ("/acknowledgements", ack_body, ack), ("/capture-start", start_body, start),
        ):
            result = restarted.post(base + suffix, json=payload, headers=self.device)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json(), original.json())
        current = restarted.get(base, headers=self.device).json()
        self.assertEqual((current["capture_state"], current["revision"]), ("capturing", 3))
        assignments = restarted.get("/v1/devices/iphone-1/capture-assignments", headers=self.device).json()
        self.assertEqual(len(assignments["assignments"]), 1)

    def test_concurrent_creation_produces_one_session(self):
        inspection = self.create_inspection()
        body = self.session_body()
        with ThreadPoolExecutor(max_workers=6) as executor:
            responses = list(executor.map(
                lambda _: self.client.post(self.session_url(inspection), json=body, headers=self.operator), range(8),
            ))
        self.assertEqual(sorted(item.status_code for item in responses), [200] * 7 + [201])
        self.assertEqual(len({item.json()["session_id"] for item in responses}), 1)

    def test_conflicting_session_and_start_operations(self):
        inspection = self.create_inspection()
        body = self.session_body()
        session = self.create_session(inspection, body)
        self.assert_error(
            self.client.post(self.session_url(inspection), json=body | {"room_id": "another-room"}, headers=self.operator),
            409, "idempotency_conflict",
        )
        url = f"/v1/sessions/{session['session_id']}/capture-start"
        start = self.start_body()
        self.assertEqual(self.client.post(url, json=start, headers=self.device).status_code, 201)
        changed = start | {"device": start["device"] | {"app_version": "0.2.0"}}
        self.assert_error(self.client.post(url, json=changed, headers=self.device), 409, "idempotency_conflict")

    def test_new_operations_do_not_repeat_state_changes(self):
        session = self.create_session()
        base = f"/v1/sessions/{session['session_id']}"
        for _ in range(2):
            self.assertEqual(self.client.post(
                base + "/acknowledgements", headers=self.device,
                json={"schema_version": "0.1", "operation_id": str(uuid4())},
            ).status_code, 201)
        for _ in range(2):
            self.assertEqual(self.client.post(base + "/capture-start", json=self.start_body(), headers=self.device).status_code, 201)
        self.assertEqual(self.client.get(base, headers=self.operator).json()["revision"], 3)

    def test_authorization_and_project_device_scope(self):
        inspection = self.create_inspection()
        session = self.create_session(inspection)
        url = f"/v1/sessions/{session['session_id']}"
        self.assert_error(self.client.get(url), 401, "unauthorized")
        self.assert_error(self.client.get(url, headers={"Authorization": "Bearer wrong"}), 401)
        self.assert_error(self.client.get(f"/v1/inspections/{inspection['inspection_id']}", headers=self.device), 403)
        self.assert_error(self.client.get(inspection["bim_model"]["download_url"], headers=self.device), 403)
        self.assert_error(self.client.post(self.session_url(inspection), json=self.session_body(), headers=self.device), 403)
        self.assert_error(self.client.post(url + "/capture-start", json=self.start_body(), headers=self.operator), 403)
        self.assert_error(self.client.get("/v1/devices/other-device/capture-assignments", headers=self.device), 403)
        for settings in (replace(self.settings, project_id="other-project"), replace(self.settings, device_id="other-phone")):
            with self.subTest(project=settings.project_id, device=settings.device_id):
                other = self.make_client(settings)
                self.assert_error(other.get(url, headers=self.device), 403)
                self.assert_error(other.post(url + "/capture-start", json=self.start_body(), headers=self.device), 403)
        other_project = self.make_client(replace(self.settings, project_id="other-project"))
        self.assert_error(other_project.get(f"/v1/inspections/{inspection['inspection_id']}", headers=self.operator), 403)

    def test_assignment_pagination_is_not_an_event_cursor(self):
        inspection = self.create_inspection()
        expected = [self.create_session(inspection)["session_id"] for _ in range(3)]
        url = "/v1/devices/iphone-1/capture-assignments"
        first = self.client.get(url, params={"limit": 2}, headers=self.device).json()
        self.assertEqual([item["session_id"] for item in first["assignments"]], expected[:2])
        self.assertIsNotNone(first["next_cursor"])
        second = self.client.get(url, params={"limit": 2, "after": first["next_cursor"]}, headers=self.device).json()
        self.assertEqual([item["session_id"] for item in second["assignments"]], expected[2:])
        self.assertIsNone(second["next_cursor"])
        newest = self.create_session(inspection)["session_id"]
        fresh = self.client.get(url, headers=self.device).json()
        self.assertEqual([item["session_id"] for item in fresh["assignments"]], expected + [newest])

    def test_invalid_and_foreign_cursors(self):
        url = "/v1/devices/iphone-1/capture-assignments"
        foreign = {"v": 1, "project": "another-project", "device": "iphone-1", "position": 0}
        foreign_cursor = base64.urlsafe_b64encode(json.dumps(foreign).encode()).decode()
        for cursor in ("not-a-cursor", "", foreign_cursor, base64.b64encode(b"null").decode()):
            self.assert_error(self.client.get(url, params={"after": cursor}, headers=self.device), 422, "invalid_cursor")
        for limit in (0, 101):
            self.assert_error(self.client.get(url, params={"limit": limit}, headers=self.device), 422)

    def test_request_validation_errors_use_contract_envelope(self):
        inspection = self.create_inspection()
        for overrides in (
            {"schema_version": "0.2"}, {"request_id": "invalid"}, {"unknown": True},
            {"capture_origin": "synthetic", "input_mode": "live_capture"},
        ):
            with self.subTest(overrides=overrides):
                self.assert_error(self.client.post(
                    self.session_url(inspection), json=self.session_body(**overrides), headers=self.operator,
                ), 422, "validation_error")
        for overrides in ({"schema_version": "0.2"}, {"project_id": "../escape"}, {"prompt": "  "}):
            self.assert_error(self.upload(self.intake_body(**overrides)), 422, "validation_error")
        self.assert_error(self.client.get("/v1/sessions/invalid", headers=self.device), 422)
        self.assert_error(self.client.get("/missing"), 404)
        self.assert_error(self.client.post("/health"), 405)

    def test_invalid_room_device_and_parent(self):
        inspection = self.create_inspection()
        for overrides, status, code in (
            ({"room_id": "room-999"}, 422, "invalid_room"),
            ({"device_id": "unregistered"}, 422, "unknown_device"),
            ({"parent_session_id": str(uuid4())}, 404, "not_found"),
        ):
            self.assert_error(self.client.post(
                self.session_url(inspection), json=self.session_body(**overrides), headers=self.operator,
            ), status, code)
        parent = self.create_session(inspection)
        child = self.create_session(inspection, self.session_body(parent_session_id=parent["session_id"]))
        self.assertEqual(child["parent_session_id"], parent["session_id"])
        different_model = self.create_inspection(content=b"a different BIM model")
        self.assert_error(self.client.post(
            self.session_url(different_model), json=self.session_body(parent_session_id=parent["session_id"]), headers=self.operator,
        ), 422, "invalid_parent_session")

    def test_synthetic_model_is_independent_of_capture_origin(self):
        inspection = self.create_inspection(body=self.intake_body(model_is_synthetic="true"))
        session = self.create_session(inspection)
        self.assertTrue(session["model_is_synthetic"])
        self.assertEqual(session["capture_origin"], "device")
        recorded = self.create_session(inspection, self.session_body(input_mode="recorded_capture"))
        self.assertEqual(recorded["capture_origin"], "device")

    def test_upload_limits_empty_files_and_safe_original_filename(self):
        self.assert_error(self.upload(content=b""), 422, "empty_bim_model")
        limited = self.make_client(replace(self.settings, max_bim_bytes=8))
        self.assert_error(self.upload(content=b"123456789", client=limited), 413, "bim_too_large")
        inspection = self.create_inspection(filename="../../private/model.ifc")
        self.assertEqual(inspection["bim_model"]["filename"], "model.ifc")
        self.assertEqual(list((self.settings.data_dir / "staging").iterdir()), [])

    def test_total_body_limit_covers_content_length_and_chunked_uploads(self):
        limited = self.make_client(replace(self.settings, max_bim_bytes=8))
        body = b"x" * (64 * 1024 + 9)
        response = limited.post("/v1/inspections", content=body, headers=self.operator)
        self.assert_error(response, 413, "request_too_large")
        response = limited.post(
            "/v1/inspections", content=iter([body]),
            headers=self.operator | {"Content-Type": "multipart/form-data; boundary=boundary"},
        )
        self.assert_error(response, 413, "request_too_large")

    def test_receipts_remain_replayable_after_session_is_closed(self):
        session = self.create_session()
        base = f"/v1/sessions/{session['session_id']}"
        start = self.start_body()
        ack = {"schema_version": "0.1", "operation_id": str(uuid4())}
        original_ack = self.client.post(base + "/acknowledgements", json=ack, headers=self.device)
        original_start = self.client.post(base + "/capture-start", json=start, headers=self.device)
        service = self.client.app.state.service
        for state in ("submitted", "sealed", "cancelled"):
            with service.database.transaction() as connection:
                current = service._load(connection, "sessions", session["session_id"])
                current["capture_state"] = state
                connection.execute("UPDATE sessions SET data=? WHERE session_id=?", (json.dumps(current), session["session_id"]))
            for suffix, body, original in (
                ("/capture-start", start, original_start), ("/acknowledgements", ack, original_ack),
            ):
                replay = self.client.post(base + suffix, json=body, headers=self.device)
                self.assertEqual(replay.status_code, 200)
                self.assertEqual(replay.json(), original.json())
                self.assert_error(self.client.post(
                    base + suffix, json=body | {"operation_id": str(uuid4())}, headers=self.device,
                ), 409, "session_closed")

    def test_failed_receipt_transaction_does_not_leave_assignment(self):
        inspection = self.create_inspection()
        body = self.session_body()
        database = self.client.app.state.service.database
        with patch.object(database, "save_receipt", side_effect=sqlite3.OperationalError("simulated failure")):
            response = self.client.post(self.session_url(inspection), json=body, headers=self.operator)
        self.assert_error(response, 503, "storage_unavailable")
        self.assertTrue(response.json()["retryable"])
        assignments = self.client.get("/v1/devices/iphone-1/capture-assignments", headers=self.device).json()
        self.assertEqual(assignments["assignments"], [])
        self.assertEqual(self.client.post(self.session_url(inspection), json=body, headers=self.operator).status_code, 201)

    def test_health_and_openapi(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        self.assertIn("/v1/inspections", schema["paths"])
        self.assertIn("/v1/sessions/{session_id}/acknowledgements", schema["paths"])


if __name__ == "__main__":
    unittest.main()
