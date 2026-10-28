"""Durable capture completion and Inspector -> Geometry Service handoff."""

import json
import logging
import random
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from uuid import uuid4

import httpx

from .errors import APIError
from .schemas import CompleteCaptureRequest
from .service import Principal, require_role, utc_now
from .storage import canonical_json


logger = logging.getLogger(__name__)


class CaptureWorkflow:
    def __init__(self, service) -> None:
        self.service = service
        self.database = service.database
        self.settings = service.settings
        self._source_store = None
        self._supabase_source_store = None

    @property
    def source_store(self):
        if self._source_store is None:
            from .capture_sources import S3CaptureSourceStore
            self._source_store = S3CaptureSourceStore(self.settings)
        return self._source_store

    @source_store.setter
    def source_store(self, value):
        self._source_store = value

    def store_for(self, session: dict):
        if session["storage"].get("provider") == "supabase":
            if self._supabase_source_store is None:
                from .supabase_captures import SupabaseCaptureSourceStore
                self._supabase_source_store = SupabaseCaptureSourceStore(self.settings)
            return self._supabase_source_store
        return self.source_store

    def complete(self, principal: Principal, session_id: str, request: CompleteCaptureRequest) -> tuple[dict, int]:
        require_role(principal, "device")
        body = request.model_dump(mode="json")
        completion_id = str(request.completion_id)
        with self.database.transaction() as connection:
            session = self.service._load(connection, "sessions", session_id)
            self.service._check_access(principal, session)
            previous = connection.execute(
                "SELECT request_body, data FROM capture_completions WHERE session_id=? AND completion_id=?",
                (session_id, completion_id),
            ).fetchone()
            if previous is not None:
                if previous["request_body"] != canonical_json(body):
                    raise APIError(409, "idempotency_conflict", "Completion ID was used with different content")
                return {"session_id": session_id, "completion": json.loads(previous["data"])}, 200
            if session["capture_state"] == "submitted":
                raise APIError(409, "completion_in_progress", "Another completion is still being validated")
            if session["capture_state"] in ("sealed", "cancelled"):
                raise APIError(409, "session_closed", "Capture session is closed")
            if session["capture_state"] != "capturing":
                raise APIError(409, "capture_not_started", "Confirm capture-start before submitting completion")
            capture_ref = body["capture_ref"]
            is_archive = capture_ref.get("format") == "supabase_capture_zip_v1"
            if session["storage"].get("provider") == "supabase":
                if capture_ref.get("format") not in ("supabase_capture_v1", "supabase_capture_zip_v1"):
                    raise APIError(422, "invalid_capture_format", "Supabase capture requires its manifest or ZIP reference format")
            elif capture_ref.get("format") in ("supabase_capture_v1", "supabase_capture_zip_v1"):
                raise APIError(422, "invalid_capture_format", "This session requires exact S3 object versions")
            expected_key = (f"{session['storage']['key_prefix']}archives/{completion_id}.zip" if is_archive
                            else f"{session['storage']['key_prefix']}manifests/{completion_id}.json")
            if capture_ref["storage_id"] != session["storage"]["storage_id"]:
                raise APIError(403, "capture_source_forbidden", "Storage is not assigned to this session")
            if capture_ref["archive_key" if is_archive else "manifest_key"] != expected_key:
                raise APIError(403, "capture_source_forbidden", "Manifest key does not match this session and completion")
            if is_archive and capture_ref["archive_byte_length"] > self.settings.max_archive_bytes:
                raise APIError(413, "archive_too_large", "Capture ZIP exceeds the configured size limit")
            if not is_archive and capture_ref["manifest_byte_length"] > self.settings.max_manifest_bytes:
                raise APIError(413, "manifest_too_large", "Manifest exceeds the configured size limit")
            completion = {"completion_id": completion_id, "status": "pending", "error": None}
            connection.execute(
                "INSERT INTO capture_completions VALUES (?, ?, ?, ?, NULL)",
                (session_id, completion_id, canonical_json(body), canonical_json(completion)),
            )
            connection.execute(
                "INSERT INTO work_items(work_id, session_id, kind, operation_id) VALUES (?, ?, 'validate', ?)",
                (f"validate:{session_id}:{completion_id}", session_id, completion_id),
            )
            session.update(
                capture_state="submitted", processing_state="validating", completion=completion, error=None,
                source={"status": "pending", "capture_ref": capture_ref,
                        "manifest_sha256": capture_ref.get("manifest_sha256"), "observation_count": None,
                        "validation_error": None},
            )
            self._save_session(connection, session)
            return {"session_id": session_id, "completion": completion}, 202

    def request_dispatch(self, principal: Principal, session_id: str) -> tuple[dict, int]:
        require_role(principal, "operator")
        with self.database.transaction() as connection:
            session = self.service._load(connection, "sessions", session_id)
            self.service._check_access(principal, session)
            if session["processing_state"] == "cancelled":
                raise APIError(409, "session_closed", "Cancelled session cannot dispatch Geometry")
            job = session["geometry_job"]
            if not job or session["source"]["status"] != "accepted":
                raise APIError(409, "source_not_accepted", "Capture source must pass validation before dispatch")
            if job["dispatch_status"] == "delivered":
                return job, 200
            if job["dispatch_status"] == "failed":
                raise APIError(409, "geometry_dispatch_failed", "Generator rejected this job", details={"job": job})
            # Advance a pending retry only. An active worker retains its lease.
            connection.execute(
                "UPDATE work_items SET next_attempt_at=0 WHERE work_id=? AND status='pending'",
                (f"dispatch:{job['job_id']}",),
            )
            return job, 202

    def _save_session(self, connection, session: dict) -> None:
        session["revision"] += 1
        connection.execute("UPDATE sessions SET data=? WHERE session_id=?", (canonical_json(session), session["session_id"]))
        from .device_events import emit_session
        emit_session(connection, session)

    def _claim(self) -> dict | None:
        now = time.time()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM work_items WHERE (status='pending' AND next_attempt_at<=?) "
                "OR (status='processing' AND lease_until<=?) ORDER BY next_attempt_at, rowid LIMIT 1",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            work = dict(row)
            work["lease_token"] = str(uuid4())
            work["attempts"] += 1
            connection.execute(
                "UPDATE work_items SET status='processing', attempts=?, lease_token=?, lease_until=? WHERE work_id=?",
                (work["attempts"], work["lease_token"], now + 120, work["work_id"]),
            )
            return work

    @staticmethod
    def _owns_lease(connection, work: dict) -> bool:
        return connection.execute(
            "SELECT 1 FROM work_items WHERE work_id=? AND status='processing' AND lease_token=?",
            (work["work_id"], work["lease_token"]),
        ).fetchone() is not None

    @staticmethod
    def _finish(connection, work: dict, status: str = "done", error: dict | None = None) -> None:
        connection.execute(
            "UPDATE work_items SET status=?, lease_until=0, lease_token=NULL, error=? WHERE work_id=? AND lease_token=?",
            (status, canonical_json(error) if error else None, work["work_id"], work["lease_token"]),
        )

    def process_next(self, client: httpx.Client | None = None, *, heartbeat_enabled=True) -> bool:
        work = self._claim()
        if work is None:
            return False
        stop_heartbeat = threading.Event()
        heartbeat = threading.Thread(target=self._renew_lease, args=(work, stop_heartbeat), daemon=True)
        if heartbeat_enabled:
            heartbeat.start()
        try:
            if work["kind"] == "validate":
                self._validate(work)
            elif work["kind"] == "dispatch":
                self._dispatch(work, client)
            elif work["kind"] == "geometry-poll":
                self.service.geometry_results.poll(work, client)
            else:
                raise APIError(500, "unknown_work_type", "Unsupported queued operation")
        except APIError as error:
            self._record_error(work, error)
        except Exception:
            # Persist a safe diagnostic without logging signed URLs, file bytes or SDK credentials.
            logger.error("Capture worker operation failed: kind=%s operation_id=%s", work["kind"], work["operation_id"])
            self._record_error(work, APIError(503, "worker_unavailable", "Worker operation will be retried", retryable=True))
        finally:
            stop_heartbeat.set()
            if heartbeat_enabled:
                heartbeat.join(1)
        return True

    def _renew_lease(self, work: dict, stop: threading.Event) -> None:
        # Large accepted captures may take longer to verify than one lease.
        while not stop.wait(30):
            try:
                with self.database.transaction() as connection:
                    changed = connection.execute(
                        "UPDATE work_items SET lease_until=? WHERE work_id=? AND status='processing' AND lease_token=?",
                        (time.time() + 120, work["work_id"], work["lease_token"]),
                    ).rowcount
                if not changed:
                    return
            except Exception:
                logger.warning("Capture worker lease renewal will be retried: operation_id=%s", work["operation_id"])

    def _validate(self, work: dict) -> None:
        with self.database.connection() as connection:
            session = self.service._load(connection, "sessions", work["session_id"])
            row = connection.execute(
                "SELECT request_body FROM capture_completions WHERE session_id=? AND completion_id=?",
                (work["session_id"], work["operation_id"]),
            ).fetchone()
            body = json.loads(row["request_body"])
        inventory = self.store_for(session).validate(session, body["capture_ref"])
        with self.database.transaction() as connection:
            if not self._owns_lease(connection, work):
                return
            session = self.service._load(connection, "sessions", work["session_id"])
            if session["capture_state"] != "submitted" or session["completion"]["completion_id"] != work["operation_id"]:
                self._finish(connection, work)
                return
            conflicts = []
            for item in inventory["files"]:
                declared = connection.execute("SELECT data FROM upload_objects WHERE session_id=? AND object_key=?",
                                              (session["session_id"], item["object_key"])).fetchone()
                if declared:
                    metadata = json.loads(declared["data"])
                    if any(metadata[field] != item[field] for field in ("byte_length", "sha256")):
                        conflicts.append(item["object_key"])
            if conflicts:
                raise APIError(422, "capture_source_conflict", "Capture files differ from previously declared observation bytes",
                               details={"conflicting_files": conflicts})
            completion = {"completion_id": work["operation_id"], "status": "accepted", "error": None}
            connection.execute(
                "UPDATE capture_completions SET data=?, inventory=? WHERE session_id=? AND completion_id=?",
                (canonical_json(completion), canonical_json(inventory), session["session_id"], work["operation_id"]),
            )
            job_id = str(uuid4())
            operation_key = str(uuid4())
            job = {"job_id": job_id, "status": "queued", "geometry_revision": None,
                   "dispatch_status": "pending", "dispatch_error": None,
                   "processing_profile_version": self.settings.geometry_profile_version}
            connection.execute(
                "INSERT INTO geometry_jobs VALUES (?, ?, ?, NULL, ?)",
                (job_id, session["session_id"], operation_key, canonical_json(job)),
            )
            connection.execute(
                "INSERT INTO work_items(work_id, session_id, kind, operation_id) VALUES (?, ?, 'dispatch', ?)",
                (f"dispatch:{job_id}", session["session_id"], job_id),
            )
            session.update(
                completion=completion, capture_state="sealed", processing_state="queued_geometry", geometry_job=job,
                source={"status": "accepted", "capture_ref": inventory.get("pinned_capture_ref", body["capture_ref"]),
                        "manifest_sha256": inventory.get("pinned_capture_ref", body["capture_ref"])["manifest_sha256"],
                        "observation_count": inventory["observation_count"], "validation_error": None},
            )
            self._save_session(connection, session)
            self._finish(connection, work)

    def _command(self, work: dict) -> dict:
        if not self.settings.geometry_base_url:
            raise APIError(503, "geometry_not_configured", "Generator address is not configured", retryable=True)
        if not self.settings.inspector_public_base_url:
            raise APIError(503, "inspector_url_not_configured", "Inspector service URL is not configured", retryable=True)
        if not self.settings.geometry_api_token or not self.settings.geometry_service_token:
            raise APIError(503, "geometry_credentials_not_configured", "Generator service identities are not configured", retryable=True)
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM geometry_jobs WHERE job_id=?", (work["operation_id"],)).fetchone()
            if row["command_body"] is not None:
                return json.loads(row["command_body"])
            session = self.service._load(connection, "sessions", work["session_id"])
            source_row = connection.execute(
                "SELECT inventory FROM capture_completions WHERE session_id=? AND completion_id=?",
                (session["session_id"], session["completion"]["completion_id"]),
            ).fetchone()
            inventory = json.loads(source_row["inventory"])
            if (not self.settings.geometry_accepts_rgb_only
                    and not any(item["name"] == "depth.f32" for item in inventory["files"])):
                raise APIError(503, "geometry_depth_required", "Generator RGB-only support has not been enabled", retryable=True)
            capture_ref = session["source"]["capture_ref"]
            command = {
                "schema_version": "0.1", "job_id": row["job_id"], "operation_key": row["operation_key"],
                "session_id": session["session_id"], "inspection_id": session["inspection_id"],
                "project_id": session["project_id"], "room_id": session["room_id"],
                "model_version": session["model_version"],
                "processing_profile_version": json.loads(row["data"])["processing_profile_version"],
                "source_ref": {
                    "url": f"{self.settings.inspector_public_base_url.rstrip('/')}/internal/v1/sessions/{session['session_id']}/capture-source",
                    "manifest_sha256": capture_ref["manifest_sha256"],
                },
            }
            if "manifest_version_id" in capture_ref:
                command["source_ref"]["manifest_version_id"] = capture_ref["manifest_version_id"]
            else:
                command["source_ref"].update(format="supabase_capture_v1", snapshot_id=capture_ref["snapshot_id"])
            connection.execute("UPDATE geometry_jobs SET command_body=? WHERE job_id=?", (canonical_json(command), row["job_id"]))
            return command

    def _dispatch(self, work: dict, client: httpx.Client | None) -> None:
        with self.database.transaction() as connection:
            if not self._owns_lease(connection, work):
                return
            current = self.service._load(connection, "sessions", work["session_id"])
            if (current["capture_state"] != "sealed" or current["processing_state"] == "cancelled"
                    or not current["geometry_job"] or current["geometry_job"]["job_id"] != work["operation_id"]):
                self._finish(connection, work)
                return
        command = self._command(work)
        owns_client = client is None
        client = client or httpx.Client(timeout=self.settings.geometry_timeout_s, follow_redirects=False)
        try:
            response = client.put(
                f"{self.settings.geometry_base_url.rstrip('/')}/internal/v1/jobs/{command['job_id']}",
                content=canonical_json(command).encode("utf-8"),
                headers={"Authorization": f"Bearer {self.settings.geometry_api_token}", "Content-Type": "application/json"},
                timeout=self.settings.geometry_timeout_s, follow_redirects=False,
            )
        except httpx.TransportError:
            raise APIError(503, "geometry_unavailable", "Generator request will be retried with the same job ID", retryable=True) from None
        finally:
            if owns_client:
                client.close()
        if response.status_code not in (200, 201):
            retryable = response.status_code in (401, 403, 408, 425, 429) or response.status_code >= 500
            details = {"status_code": response.status_code}
            retry_after = response.headers.get("Retry-After")
            if retry_after and retryable:
                try:
                    delay = float(retry_after)
                except ValueError:
                    try:
                        delay = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        delay = 0
                details["retry_after_s"] = max(0, min(delay, 3600))
            raise APIError(503 if retryable else 502, "geometry_request_rejected", "Generator did not accept the job", retryable=retryable, details=details)
        try:
            receipt = response.json()
            if (not isinstance(receipt, dict) or receipt.get("job_id") != command["job_id"]
                    or receipt.get("status") not in ("queued", "fetching", "processing", "ready", "failed", "cancelled")):
                raise ValueError
            for key in ("session_id", "project_id"):
                if key in receipt and receipt[key] != command[key]:
                    raise ValueError
        except (ValueError, TypeError):
            raise APIError(503, "invalid_geometry_receipt", "Generator receipt could not be verified", retryable=True) from None
        with self.database.transaction() as connection:
            if not self._owns_lease(connection, work):
                return
            session = self.service._load(connection, "sessions", work["session_id"])
            job = session["geometry_job"]
            if (job is None or job["job_id"] != command["job_id"] or session["capture_state"] != "sealed"
                    or session["processing_state"] == "cancelled"):
                self._finish(connection, work)
                return
            remote_status = receipt["status"]
            # A submission receipt does not validate geometry output. Ready results
            # will be reconciled by the later result-consumption integration.
            local_status = "queued" if remote_status in ("ready", "failed", "cancelled") else remote_status
            job.update(status=local_status, remote_status=remote_status, dispatch_status="delivered", dispatch_error=None)
            session["processing_state"] = "building_geometry" if remote_status in ("fetching", "processing") else "queued_geometry"
            connection.execute("UPDATE geometry_jobs SET data=? WHERE job_id=?", (canonical_json(job), command["job_id"]))
            self._save_session(connection, session)
            self._finish(connection, work)
            connection.execute(
                "INSERT OR IGNORE INTO work_items(work_id, session_id, kind, operation_id) VALUES (?, ?, 'geometry-poll', ?)",
                (f"poll:{command['job_id']}", session["session_id"], command["job_id"]),
            )

    def _record_error(self, work: dict, error: APIError) -> None:
        with self.database.transaction() as connection:
            if not self._owns_lease(connection, work):
                return
            session = self.service._load(connection, "sessions", work["session_id"])
            if work["kind"] == "validate":
                if (session["capture_state"] != "submitted" or not session["completion"]
                        or session["completion"]["completion_id"] != work["operation_id"]):
                    self._finish(connection, work)
                    return
            elif (not session["geometry_job"] or session["geometry_job"]["job_id"] != work["operation_id"]
                    or session["capture_state"] != "sealed" or session["processing_state"] == "cancelled"):
                self._finish(connection, work)
                return
            if error.body["retryable"]:
                delay = random.uniform(0.8, 1.2) * min(30, 2 ** min(work["attempts"], 5))
                delay = max(delay, error.body["details"].get("retry_after_s", 0))
                connection.execute(
                    "UPDATE work_items SET status='pending', next_attempt_at=?, lease_until=0, lease_token=NULL, error=? "
                    "WHERE work_id=? AND lease_token=?",
                    (time.time() + delay, canonical_json(error.body), work["work_id"], work["lease_token"]),
                )
            else:
                self._finish(connection, work, "failed", error.body)
            if work["kind"] == "validate":
                if not error.body["retryable"]:
                    completion = {"completion_id": work["operation_id"], "status": "rejected", "error": error.body}
                    connection.execute(
                        "UPDATE capture_completions SET data=? WHERE session_id=? AND completion_id=?",
                        (canonical_json(completion), work["session_id"], work["operation_id"]),
                    )
                    session.update(completion=completion, capture_state="capturing", processing_state="idle")
                    session["source"]["status"] = "rejected"
                session["source"]["validation_error"] = error.body
            else:
                job = session["geometry_job"]
                job["result_error" if work["kind"] == "geometry-poll" else "dispatch_error"] = error.body
                if not error.body["retryable"]:
                    job["status"] = "failed"
                    if work["kind"] == "dispatch":
                        job["dispatch_status"] = "failed"
                    session.update(processing_state="failed", error=error.body)
                connection.execute("UPDATE geometry_jobs SET data=? WHERE job_id=?", (canonical_json(job), work["operation_id"]))
            self._save_session(connection, session)

    def source_access(self, principal: Principal, session_id: str, *, after: int = 0, limit: int = 100, files_only: bool = False) -> dict:
        require_role(principal, "geometry")
        with self.database.connection() as connection:
            session = self.service._load(connection, "sessions", session_id)
            self.service._check_access(principal, session)
            if session["capture_state"] != "sealed" or not session["source"] or session["source"]["status"] != "accepted" or not session["geometry_job"]:
                raise APIError(409, "source_not_accepted", "This session has no accepted source for a geometry job")
            row = connection.execute(
                "SELECT inventory FROM capture_completions WHERE session_id=? AND completion_id=?",
                (session_id, session["completion"]["completion_id"]),
            ).fetchone()
            inventory = json.loads(row["inventory"])
        capture_ref = session["source"]["capture_ref"]
        page = inventory["files"][after:after + limit] if files_only else []
        access = self.store_for(session).download_access(capture_ref, page)
        if files_only:
            position = after + len(page)
            return {"items": access["items"], "next_cursor": str(position) if position < len(inventory["files"]) else None}
        return {
            "schema_version": "0.1", "session_id": session_id, "job_id": session["geometry_job"]["job_id"],
            **capture_ref, "manifest_url": access["manifest_url"], "expires_at": access["expires_at"],
            "files_url": f"{self.settings.inspector_public_base_url.rstrip('/')}/internal/v1/sessions/{session_id}/capture-source/files",
        }

    def run(self, stop: threading.Event) -> None:
        with httpx.Client(timeout=self.settings.geometry_timeout_s, follow_redirects=False) as client:
            while not stop.is_set():
                try:
                    processed = self.process_next(client)
                except Exception:
                    logger.error("Capture queue is temporarily unavailable")
                    processed = False
                if not processed:
                    stop.wait(self.settings.worker_interval_s)
