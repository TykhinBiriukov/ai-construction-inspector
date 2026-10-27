import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from uuid import uuid4

import httpx

from ai_inspector.config import Settings
from ai_inspector.errors import APIError
from ai_inspector.geometry_results import GeometryResults
from ai_inspector.service import InspectorService, Principal
from ai_inspector.storage import canonical_json


class GeometryResultTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parents[1] / ".test-data"
        test_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=test_root)
        self.assertTrue(Path(self.temporary.name).resolve().is_relative_to(test_root.resolve()))
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(
            Path(self.temporary.name), "operator-token", "device-token",
            geometry_base_url="https://geometry.example", inspector_public_base_url="https://inspector.example",
            geometry_api_token="outgoing-geometry-token", geometry_service_token="incoming-geometry-token",
        )
        self.service = InspectorService(self.settings)
        self.database = self.service.database
        self.results = GeometryResults(self.service)
        self.session_id, self.inspection_id, self.job_id, self.completion_id = (str(uuid4()) for _ in range(4))
        self.observation_ids = [str(uuid4()), str(uuid4())]
        self.job = {
            "job_id": self.job_id, "status": "processing", "geometry_revision": None,
            "dispatch_status": "delivered", "dispatch_error": None, "revision": 1,
            "processing_profile_version": "geometry-v1",
        }
        capture_ref = {
            "storage_id": "capture-s3", "manifest_key": f"projects/demo-building/sessions/{self.session_id}/manifests/{self.completion_id}.json",
            "manifest_version_id": "exact-manifest-version", "manifest_byte_length": 512, "manifest_sha256": "a" * 64,
        }
        self.session = {
            "schema_version": "0.1", "session_id": self.session_id, "inspection_id": self.inspection_id,
            "project_id": "demo-building", "room_id": "room-204", "model_version": "model-v1",
            "policy_version": "policy-v1", "device_id": "iphone-1", "model_is_synthetic": False,
            "capture_origin": "device", "input_mode": "live_capture", "created_at": "2026-10-27T12:00:00Z",
            "parent_session_id": None, "device_acknowledged_at": None, "display_view_id": None,
            "capture_state": "sealed", "processing_state": "building_geometry", "processing_scope": "geometry_only_v1",
            "revision": 4, "geometry_job": self.job, "geometry_result": None, "result": None, "error": None,
            "completion": {"completion_id": self.completion_id, "status": "accepted", "error": None},
            "source": {"status": "accepted", "manifest_sha256": "a" * 64, "capture_ref": capture_ref},
            "storage": {"storage_id": "capture-s3", "key_prefix": f"projects/demo-building/sessions/{self.session_id}/"},
        }
        self.command = {
            "schema_version": "0.1", "job_id": self.job_id, "session_id": self.session_id,
            "project_id": "demo-building", "inspection_id": self.inspection_id, "room_id": "room-204",
            "model_version": "model-v1", "processing_profile_version": "geometry-v1", "operation_key": str(uuid4()),
            "source_ref": {
                "url": f"https://inspector.example/internal/v1/sessions/{self.session_id}/capture-source",
                "manifest_sha256": "a" * 64, "manifest_version_id": "exact-manifest-version",
            },
        }
        inventory = {
            "observations": [{"observation_id": item} for item in self.observation_ids],
            "files": [{"observation_id": item, "name": "depth.f32"} for item in self.observation_ids],
            "observation_count": 2, "excluded_observations": [],
        }
        with self.database.transaction() as connection:
            # This fixture also runs while the parent adds the migration itself.
            connection.execute(
                "CREATE TABLE IF NOT EXISTS geometry_events(source TEXT, event_id TEXT, request_body TEXT, data TEXT, PRIMARY KEY(source,event_id))",
            )
            connection.execute("INSERT INTO inspections VALUES (?, ?, ?)", (self.inspection_id, "demo-building", "{}"))
            connection.execute(
                "INSERT INTO sessions(session_id,inspection_id,project_id,device_id,data) VALUES (?, ?, ?, ?, ?)",
                (self.session_id, self.inspection_id, "demo-building", "iphone-1", canonical_json(self.session)),
            )
            connection.execute(
                "INSERT INTO capture_completions VALUES (?, ?, ?, ?, ?)",
                (self.session_id, self.completion_id, "{}", canonical_json(self.session["completion"]), canonical_json(inventory)),
            )
            connection.execute(
                "INSERT INTO geometry_jobs VALUES (?, ?, ?, ?, ?)",
                (self.job_id, self.session_id, self.command["operation_key"], canonical_json(self.command), canonical_json(self.job)),
            )
            connection.execute(
                "INSERT INTO work_items(work_id,session_id,kind,operation_id,status,attempts,lease_until,lease_token) "
                "VALUES (?, ?, 'geometry-poll', ?, 'processing', 1, ?, 'test-lease')",
                (f"poll:{self.job_id}", self.session_id, self.job_id, time.time() + 120),
            )
        self.principal = Principal("geometry", "demo-building")
        self.work = self.current_work()

    def current(self):
        with self.database.connection() as connection:
            return self.service._load(connection, "sessions", self.session_id)

    def current_work(self):
        with self.database.connection() as connection:
            return dict(connection.execute("SELECT * FROM work_items WHERE work_id=?", (f"poll:{self.job_id}",)).fetchone())

    def modify_session(self, mutate):
        with self.database.transaction() as connection:
            session = self.service._load(connection, "sessions", self.session_id)
            mutate(session)
            connection.execute("UPDATE sessions SET data=? WHERE session_id=?", (canonical_json(session), self.session_id))

    def event(self, **overrides):
        return {
            "schema_version": "0.1", "event_id": str(uuid4()), "type": "geometry.ready",
            "project_id": "demo-building", "session_id": self.session_id, "job_id": self.job_id,
            "job_revision": 2, "manifest_sha256": "a" * 64, "geometry_revision": 1,
            "created_at": "2026-10-27T12:03:00Z", "error": None,
        } | overrides

    def ready(self, **overrides):
        return {
            **{field: self.command[field] for field in (
                "job_id", "session_id", "project_id", "inspection_id", "room_id", "model_version", "processing_profile_version",
            )},
            "manifest_sha256": "a" * 64, "status": "ready", "revision": 2, "geometry_revision": 1,
            "evidence_observation_ids": list(self.observation_ids), "excluded_observations": [],
            "artifacts": [{
                "artifact_id": "point-cloud-1", "kind": "point_cloud", "url": "https://geometry.example/assets/cloud.ply",
                "media_type": "application/octet-stream", "byte_length": 1234, "sha256": "b" * 64,
            }], "error": None,
        } | overrides

    def poll_response(self, result, *, before_response=None, status=200, headers=None):
        requests = []

        def response(request):
            requests.append(request)
            if before_response:
                before_response()
            return httpx.Response(status, json=result, headers=headers)

        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            self.results.poll(self.work, client)
        return requests

    def assert_api_error(self, callback, status=503, code="invalid_geometry_result"):
        with self.assertRaises(APIError) as caught:
            callback()
        self.assertEqual(caught.exception.status_code, status)
        self.assertEqual(caught.exception.body["code"], code)
        return caught.exception.body

    def reset_lease(self):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE work_items SET status='processing', lease_token='test-lease', lease_until=? WHERE work_id=?",
                (time.time() + 120, self.work["work_id"]),
            )

    def test_webhook_is_durable_idempotent_and_does_not_apply_unverified_ready(self):
        event = self.event()
        receipt, status = self.results.receive(self.principal, event)
        self.assertEqual(status, 202)
        self.assertEqual(receipt, {"event_id": event["event_id"], "status": "accepted"})
        replay, status = GeometryResults(self.service).receive(self.principal, copy.deepcopy(event))
        self.assertEqual((replay, status), (receipt, 200))
        self.assertIsNone(self.current()["geometry_result"])
        self.assertEqual(self.current()["geometry_job"]["status"], "processing")
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM geometry_events").fetchone()[0], 1)
        self.assert_api_error(
            lambda: self.results.receive(self.principal, event | {"geometry_revision": 2}), 409, "idempotency_conflict",
        )

    def test_webhook_advances_pending_poll_without_duplicating_work_or_stealing_lease(self):
        self.results.receive(self.principal, self.event())
        self.assertEqual(self.current_work()["lease_token"], "test-lease")
        with self.database.transaction() as connection:
            connection.execute("UPDATE work_items SET status='done', lease_token=NULL, next_attempt_at=9999999999")
        self.results.receive(self.principal, self.event())
        work = self.current_work()
        self.assertEqual((work["status"], work["next_attempt_at"]), ("pending", 0))
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM work_items").fetchone()[0], 1)

    def test_webhook_validates_identity_source_and_required_fields(self):
        self.assert_api_error(lambda: self.results.receive(Principal("device", "demo-building", "iphone-1"), self.event()), 403, "forbidden")
        self.assert_api_error(lambda: self.results.receive(self.principal, self.event(project_id="other-project")), 403, "forbidden")
        self.assert_api_error(lambda: self.results.receive(self.principal, self.event(job_id=str(uuid4()))), 409, "geometry_context_mismatch")
        self.assert_api_error(lambda: self.results.receive(self.principal, self.event(manifest_sha256="c" * 64)), 409, "geometry_context_mismatch")
        for overrides in (
            {"schema_version": "0.2"}, {"event_id": " "}, {"job_revision": True},
            {"geometry_revision": 0}, {"type": "geometry.updated"},
            {"type": "geometry.failed", "error": None, "geometry_revision": None},
            {"extra": float("nan")},
        ):
            with self.subTest(overrides=overrides):
                self.assert_api_error(lambda: self.results.receive(self.principal, self.event(**overrides)), 422, "invalid_geometry_event")

    def test_ready_poll_persists_geometry_completion_without_inspection_finding(self):
        requests = self.poll_response(self.ready())
        self.assertEqual(len(requests), 1, "Geometry assets must not be downloaded")
        self.assertEqual(requests[0].method, "GET")
        self.assertEqual(requests[0].url.path, f"/internal/v1/jobs/{self.job_id}")
        self.assertEqual(requests[0].headers["authorization"], "Bearer outgoing-geometry-token")
        session = self.current()
        self.assertEqual(session["processing_state"], "completed")
        self.assertEqual(session["processing_scope"], "geometry_only_v1")
        self.assertEqual(session["geometry_job"]["status"], "ready")
        self.assertEqual(session["geometry_job"]["revision"], 2)
        self.assertEqual(session["geometry_job"]["geometry_revision"], 1)
        self.assertEqual(session["geometry_result"]["evidence_observation_ids"], self.observation_ids)
        self.assertEqual(session["geometry_result"]["analysis_origin"], "live_model")
        self.assertIsNone(session["result"])
        self.assertEqual(self.current_work()["status"], "done")

    def test_geometry_result_preserves_mock_and_synthetic_artifact_labels(self):
        result = self.ready(analysis_origin="mock")
        result["artifacts"][0]["is_synthetic"] = True
        self.poll_response(result)
        saved = self.current()["geometry_result"]
        self.assertEqual(saved["analysis_origin"], "mock")
        self.assertTrue(saved["artifacts"][0]["is_synthetic"])

    def test_geometry_completion_never_overwrites_existing_inspection_result(self):
        self.modify_session(lambda session: session.update(result={"outcome": "needs_more_data"}))
        self.poll_response(self.ready())
        self.assertEqual(self.current()["result"], {"outcome": "needs_more_data"})

    def test_ready_can_explicitly_exclude_an_accepted_observation(self):
        result = self.ready(
            evidence_observation_ids=self.observation_ids[:1],
            excluded_observations=[{"observation_id": self.observation_ids[1], "reason": "Depth unavailable"}],
        )
        self.poll_response(result)
        self.assertEqual(self.current()["geometry_result"]["excluded_observations"], result["excluded_observations"])

    def test_nonterminal_poll_requeues_same_work_and_monotonic_revision(self):
        result = self.ready(status="processing", revision=3, geometry_revision=None)
        started = time.time()
        self.poll_response(result)
        session = self.current()
        self.assertEqual((session["geometry_job"]["revision"], session["processing_state"]), (3, "building_geometry"))
        self.assertIsNone(session["geometry_result"])
        pending = self.current_work()
        self.assertEqual(pending["status"], "pending")
        self.assertGreaterEqual(pending["next_attempt_at"], started + 1)
        self.reset_lease()
        self.poll_response(self.ready(revision=2))
        self.assertEqual(self.current()["geometry_job"]["revision"], 3)
        self.assertIsNone(self.current()["geometry_result"])
        self.assertEqual(self.current_work()["status"], "pending")

    def test_equal_revision_does_not_repeat_state_changes(self):
        self.poll_response(self.ready(status="processing", revision=1, geometry_revision=None))
        self.assertEqual(self.current()["revision"], 4)
        self.assertEqual(self.current_work()["status"], "pending")
        self.reset_lease()
        self.assert_api_error(lambda: self.poll_response(self.ready(revision=1)))

    def test_wrong_job_source_model_and_profile_are_not_applied(self):
        for overrides in (
            {"job_id": str(uuid4())}, {"session_id": str(uuid4())}, {"project_id": "other-project"},
            {"manifest_sha256": "c" * 64}, {"model_version": "other-model"},
            {"processing_profile_version": "other-profile"}, {"revision": 0},
            {"revision": True}, {"status": "invented"}, {"analysis_origin": "unverified"},
            {"source_ref": {"manifest_sha256": "a" * 64, "manifest_version_id": "new-current-version"}},
        ):
            with self.subTest(overrides=overrides):
                self.assert_api_error(lambda: self.poll_response(self.ready(**overrides)))
                self.assertIsNone(self.current()["geometry_result"])

    def test_unlisted_duplicate_or_silently_omitted_evidence_is_rejected(self):
        for overrides in (
            {"evidence_observation_ids": [str(uuid4())]},
            {"evidence_observation_ids": self.observation_ids * 2},
            {"evidence_observation_ids": self.observation_ids[:1]},
            {"evidence_observation_ids": []},
            {"excluded_observations": [{"observation_id": self.observation_ids[0], "reason": "Also used"}]},
            {"excluded_observations": [{"observation_id": str(uuid4()), "reason": "Unknown"}]},
        ):
            with self.subTest(overrides=overrides):
                self.assert_api_error(lambda: self.poll_response(self.ready(**overrides)))

    def test_artifact_metadata_and_https_urls_are_validated(self):
        for overrides in (
            {"kind": "script"}, {"sha256": "A" * 64}, {"byte_length": True},
            {"media_type": "invalid"}, {"url": "file:///private/model.ply"},
            {"url": "http://public.example/model.ply"}, {"url": "https://user:secret@example.com/model.ply"},
            {"url": "https://example.com:invalid/model.ply"}, {"artifact_id": " "},
        ):
            with self.subTest(overrides=overrides):
                result = self.ready()
                result["artifacts"][0].update(overrides)
                error = self.assert_api_error(lambda: self.poll_response(result))
                self.assertNotIn("secret", repr(error))
                self.assertNotIn("public.example", repr(error))

    def test_loopback_http_artifact_is_allowed_for_local_development(self):
        result = self.ready()
        result["artifacts"][0]["url"] = "http://127.0.0.1:8123/assets/mock.ply"
        self.poll_response(result)
        self.assertEqual(self.current()["geometry_job"]["status"], "ready")

    def test_supabase_snapshot_reference_does_not_require_s3_version_id(self):
        command = copy.deepcopy(self.command)
        del command["source_ref"]["manifest_version_id"]
        command["source_ref"]["snapshot_id"] = str(uuid4())
        with self.database.transaction() as connection:
            connection.execute("UPDATE geometry_jobs SET command_body=?", (canonical_json(command),))
        self.poll_response(self.ready(source_ref=copy.deepcopy(command["source_ref"])))
        self.assertEqual(self.current()["processing_state"], "completed")

    def test_terminal_failure_uses_safe_error_without_provider_secrets(self):
        self.poll_response(self.ready(status="failed", geometry_revision=None, error={"message": "secret https://private.example/token"}))
        session = self.current()
        self.assertEqual(session["processing_state"], "failed")
        self.assertEqual(session["error"]["code"], "geometry_build_failed")
        self.assertNotIn("private.example", repr(session))
        self.assertIsNone(session["geometry_result"])
        self.assertEqual(self.current_work()["status"], "done")

    def test_remote_cancellation_is_terminal(self):
        self.poll_response(self.ready(status="cancelled", geometry_revision=None))
        self.assertEqual(self.current()["processing_state"], "cancelled")
        self.assertEqual(self.current_work()["status"], "done")

    def test_cancelled_session_and_stale_lease_skip_provider_request(self):
        self.modify_session(lambda session: session.update(processing_state="cancelled"))
        self.assertEqual(self.poll_response(self.ready()), [])
        self.assertEqual(self.current()["processing_state"], "cancelled")
        self.reset_lease()
        self.modify_session(lambda session: session.update(processing_state="building_geometry"))
        self.work["lease_token"] = "stale-lease"
        self.assertEqual(self.poll_response(self.ready()), [])
        self.assertIsNone(self.current()["geometry_result"])

    def test_cancellation_during_get_fences_late_ready_response(self):
        self.poll_response(
            self.ready(), before_response=lambda: self.modify_session(lambda session: session.update(processing_state="cancelled")),
        )
        self.assertEqual(self.current()["processing_state"], "cancelled")
        self.assertIsNone(self.current()["geometry_result"])
        self.assertEqual(self.current_work()["status"], "done")

    def test_late_callback_is_recorded_but_does_not_reactivate_cancelled_work(self):
        self.modify_session(lambda session: session.update(processing_state="cancelled"))
        with self.database.transaction() as connection:
            connection.execute("UPDATE work_items SET status='done', lease_token=NULL")
        self.assertEqual(self.results.receive(self.principal, self.event())[1], 202)
        self.assertEqual(self.current_work()["status"], "done")

    def test_http_error_and_redirect_are_retryable_without_following_or_exposing_body(self):
        for status in (301, 401, 404, 429, 503):
            with self.subTest(status=status):
                error = self.assert_api_error(
                    lambda: self.poll_response({"secret": "provider-internal"}, status=status, headers={"Location": "https://other.example"}),
                    503, "geometry_result_unavailable",
                )
                self.assertTrue(error["retryable"])
                self.assertEqual(error["details"], {"status_code": status})
                self.assertNotIn("provider-internal", repr(error))

    def test_missing_configuration_and_transport_timeout_are_retryable(self):
        settings = self.results.settings
        self.results.settings = type("EmptyGeometryConfig", (), {"geometry_base_url": "", "geometry_api_token": ""})()
        self.assert_api_error(lambda: self.results.poll(self.work), 503, "geometry_not_configured")
        self.results.settings = settings

        def timeout(request):
            raise httpx.ReadTimeout("secret URL in provider exception")

        with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
            error = self.assert_api_error(lambda: self.results.poll(self.work, client), 503, "geometry_unavailable")
        self.assertNotIn("secret", repr(error))


if __name__ == "__main__":
    unittest.main()
