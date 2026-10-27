"""Durable Geometry Service notifications and verified job reconciliation."""

import ipaddress
import json
import re
import time
from urllib.parse import urlsplit

import httpx

from .errors import APIError
from .service import require_role, utc_now
from .storage import canonical_json


_STATUSES = ("queued", "fetching", "processing", "ready", "failed", "cancelled")
_TERMINAL = ("ready", "failed", "cancelled")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MEDIA_TYPE = re.compile(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+\Z")


def _positive(value) -> bool:
    return type(value) is int and value > 0


def _text(value, limit=256) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _event_error(message: str) -> APIError:
    return APIError(422, "invalid_geometry_event", message)


def _result_error() -> APIError:
    return APIError(
        503, "invalid_geometry_result", "Geometry Service job response could not be verified", retryable=True,
    )


def _artifact_url(value) -> bool:
    if not isinstance(value, str) or len(value) > 16384 or any(ord(character) < 32 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
            return False
        parsed.port
        if parsed.scheme == "https":
            return True
        if parsed.scheme != "http":
            return False
        if parsed.hostname == "localhost":
            return True
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


class GeometryResults:
    def __init__(self, service) -> None:
        self.service = service
        self.database = service.database
        self.settings = service.settings
        self.workflow = service.capture

    @staticmethod
    def _validate_event(event: dict) -> None:
        if not isinstance(event, dict) or event.get("schema_version") != "0.1":
            raise _event_error("Unsupported geometry event schema")
        if event.get("type") not in ("geometry.updated", "geometry.ready", "geometry.failed"):
            raise _event_error("Unsupported geometry event type")
        for field in ("event_id", "project_id", "session_id", "job_id"):
            if not _text(event.get(field)):
                raise _event_error(f"Geometry event requires {field}")
        digest = event.get("manifest_sha256")
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise _event_error("Geometry event requires the accepted source SHA-256")
        if not _positive(event.get("job_revision")):
            raise _event_error("Geometry event requires a positive job_revision")
        if event["type"] == "geometry.updated" and event.get("status") not in _STATUSES:
            raise _event_error("Geometry update requires a known job status")
        if event["type"] == "geometry.ready" and not _positive(event.get("geometry_revision")):
            raise _event_error("Geometry ready event requires a positive geometry_revision")
        if event["type"] == "geometry.failed":
            if not isinstance(event.get("error"), dict) or not event["error"] or event.get("geometry_revision") is not None:
                raise _event_error("Geometry failure requires an error and null geometry_revision")
        # Refuse non-JSON extension values before persisting an inbox record.
        try:
            json.dumps(event, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            raise _event_error("Geometry event must contain finite JSON values") from None

    def _context(self, connection, session_id: str, job_id: str) -> tuple[dict, dict]:
        session = self.service._load(connection, "sessions", session_id)
        row = connection.execute("SELECT * FROM geometry_jobs WHERE job_id=?", (job_id,)).fetchone()
        if (
            row is None or row["session_id"] != session_id
            or not session.get("geometry_job") or session["geometry_job"]["job_id"] != job_id
            or not session.get("source") or session["source"]["status"] != "accepted"
        ):
            raise APIError(409, "geometry_context_mismatch", "Geometry job does not match the accepted capture context")
        return session, dict(row)

    @staticmethod
    def _active(session: dict, job_id: str) -> bool:
        job = session.get("geometry_job")
        return bool(
            job and job.get("job_id") == job_id and session.get("capture_state") == "sealed"
            and session.get("processing_state") not in ("completed", "failed", "cancelled")
            and job.get("status") not in _TERMINAL
            and session.get("processing_scope", "geometry_only_v1") == "geometry_only_v1"
            and session.get("source") and session["source"].get("status") == "accepted"
        )

    @staticmethod
    def _enqueue(connection, session_id: str, job_id: str) -> None:
        connection.execute(
            "INSERT INTO work_items(work_id, session_id, kind, operation_id) VALUES (?, ?, 'geometry-poll', ?) "
            "ON CONFLICT(work_id) DO UPDATE SET status='pending', next_attempt_at=0, error=NULL, "
            "lease_until=0, lease_token=NULL WHERE work_items.status!='processing'",
            (f"poll:{job_id}", session_id, job_id),
        )

    def receive(self, principal, event: dict) -> tuple[dict, int]:
        require_role(principal, "geometry")
        self._validate_event(event)
        if event["project_id"] != principal.project_id:
            raise APIError(403, "forbidden", "Geometry event belongs to another project")
        body = canonical_json(event)
        with self.database.transaction() as connection:
            # Check the authenticated context even when replaying an old receipt.
            session, row = self._context(connection, event["session_id"], event["job_id"])
            self.service._check_access(principal, session)
            if event["manifest_sha256"] != session["source"]["manifest_sha256"]:
                raise APIError(409, "geometry_context_mismatch", "Geometry event refers to a different capture source")
            previous = connection.execute(
                "SELECT request_body, data FROM geometry_events WHERE source='geometry' AND event_id=?",
                (event["event_id"],),
            ).fetchone()
            if previous is not None:
                if previous["request_body"] != body:
                    raise APIError(409, "idempotency_conflict", "Geometry event ID was used with different content")
                return json.loads(previous["data"]), 200
            receipt = {"event_id": event["event_id"], "status": "accepted"}
            connection.execute(
                "INSERT INTO geometry_events(source, event_id, request_body, data) VALUES ('geometry', ?, ?, ?)",
                (event["event_id"], body, canonical_json(receipt)),
            )
            # Notifications are hints. Only GET job may advance the workflow.
            if self._active(session, row["job_id"]):
                self._enqueue(connection, session["session_id"], row["job_id"])
            return receipt, 202

    def _load_poll_context(self, work: dict) -> tuple[dict, dict, dict] | None:
        with self.database.transaction() as connection:
            if not self.workflow._owns_lease(connection, work):
                return None
            session = self.service._load(connection, "sessions", work["session_id"])
            if not self._active(session, work["operation_id"]):
                self.workflow._finish(connection, work)
                return None
            session, row = self._context(connection, work["session_id"], work["operation_id"])
            if row["command_body"] is None:
                raise APIError(503, "geometry_job_not_dispatched", "Geometry job submission is pending", retryable=True)
            command = json.loads(row["command_body"])
            completion = connection.execute(
                "SELECT inventory FROM capture_completions WHERE session_id=? AND completion_id=?",
                (session["session_id"], session["completion"]["completion_id"]),
            ).fetchone()
            if completion is None or completion["inventory"] is None:
                raise _result_error()
            return session, command, json.loads(completion["inventory"])

    def _fetch(self, job_id: str, client: httpx.Client | None) -> dict:
        if not self.settings.geometry_base_url or not self.settings.geometry_api_token:
            raise APIError(503, "geometry_not_configured", "Geometry Service is not configured", retryable=True)
        owns_client = client is None
        client = client or httpx.Client(timeout=self.settings.geometry_timeout_s, follow_redirects=False)
        try:
            response = client.get(
                f"{self.settings.geometry_base_url.rstrip('/')}/internal/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {self.settings.geometry_api_token}"},
                timeout=self.settings.geometry_timeout_s, follow_redirects=False,
            )
        except httpx.TransportError:
            raise APIError(503, "geometry_unavailable", "Geometry job status will be retried", retryable=True) from None
        finally:
            if owns_client:
                client.close()
        if response.status_code != 200:
            raise APIError(
                503, "geometry_result_unavailable", "Geometry job status is temporarily unavailable", retryable=True,
                details={"status_code": response.status_code},
            )
        if len(response.content) > self.settings.max_manifest_bytes:
            raise _result_error()
        try:
            result = response.json()
        except (ValueError, TypeError, RecursionError):
            raise _result_error() from None
        if not isinstance(result, dict):
            raise _result_error()
        return result

    @staticmethod
    def _validate_job(result: dict, session: dict, command: dict) -> int:
        for field in ("job_id", "session_id", "project_id"):
            if result.get(field) != command[field]:
                raise _result_error()
        for field in ("inspection_id", "room_id", "model_version", "processing_profile_version", "operation_key"):
            if field in result and result[field] != command[field]:
                raise _result_error()
        if "schema_version" in result and result["schema_version"] != "0.1":
            raise _result_error()
        source_ref = result.get("source_ref")
        source_hashes = []
        if "manifest_sha256" in result:
            source_hashes.append(result["manifest_sha256"])
        if source_ref is not None:
            if not isinstance(source_ref, dict):
                raise _result_error()
            if "manifest_sha256" in source_ref:
                source_hashes.append(source_ref["manifest_sha256"])
            for field in ("manifest_version_id", "snapshot_id", "url"):
                if field in source_ref and source_ref[field] != command["source_ref"].get(field):
                    raise _result_error()
        if not source_hashes or any(value != session["source"]["manifest_sha256"] for value in source_hashes):
            raise _result_error()
        revision = result.get("revision", result.get("job_revision"))
        if not _positive(revision) or result.get("status") not in _STATUSES:
            raise _result_error()
        if "revision" in result and "job_revision" in result and result["revision"] != result["job_revision"]:
            raise _result_error()
        if result.get("analysis_origin", "live_model") not in ("live_model", "recorded_model", "mock"):
            raise _result_error()
        return revision

    @staticmethod
    def _ready_result(result: dict, session: dict, inventory: dict, revision: int) -> dict:
        if not _positive(result.get("geometry_revision")):
            raise _result_error()
        accepted = {item["observation_id"] for item in inventory.get("observations", [])}
        accepted.update(item["observation_id"] for item in inventory["files"])
        evidence = result.get("evidence_observation_ids")
        excluded = result.get("excluded_observations")
        if not isinstance(evidence, list) or not evidence or not isinstance(excluded, list):
            raise _result_error()
        evidence_ids = set()
        for observation_id in evidence:
            if not _text(observation_id) or observation_id not in accepted or observation_id in evidence_ids:
                raise _result_error()
            evidence_ids.add(observation_id)
        excluded_ids = set()
        normalized_excluded = []
        for item in excluded:
            if not isinstance(item, dict):
                raise _result_error()
            observation_id, reason = item.get("observation_id"), item.get("reason")
            if (
                not _text(observation_id) or observation_id not in accepted
                or observation_id in evidence_ids or observation_id in excluded_ids or not _text(reason, 8000)
            ):
                raise _result_error()
            excluded_ids.add(observation_id)
            normalized_excluded.append({"observation_id": observation_id, "reason": reason})
        if evidence_ids | excluded_ids != accepted:
            raise _result_error()
        artifacts = result.get("artifacts")
        if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 1000:
            raise _result_error()
        normalized_artifacts = []
        artifact_ids = set()
        for item in artifacts:
            if not isinstance(item, dict):
                raise _result_error()
            artifact_id, media_type = item.get("artifact_id"), item.get("media_type")
            digest = item.get("sha256")
            if (
                not _text(artifact_id) or artifact_id in artifact_ids
                or item.get("kind") not in ("point_cloud", "reconstructed_model")
                or not _artifact_url(item.get("url"))
                or not isinstance(media_type, str) or not _MEDIA_TYPE.fullmatch(media_type)
                or not _positive(item.get("byte_length"))
                or not isinstance(digest, str) or not _HASH.fullmatch(digest)
            ):
                raise _result_error()
            artifact_ids.add(artifact_id)
            normalized_artifacts.append({field: item[field] for field in (
                "artifact_id", "kind", "url", "media_type", "byte_length", "sha256",
            )})
            if "is_synthetic" in item:
                if type(item["is_synthetic"]) is not bool:
                    raise _result_error()
                normalized_artifacts[-1]["is_synthetic"] = item["is_synthetic"]
        return {
            "job_id": result["job_id"], "session_id": session["session_id"],
            "project_id": session["project_id"], "model_version": session["model_version"],
            "manifest_sha256": session["source"]["manifest_sha256"],
            "job_revision": revision, "geometry_revision": result["geometry_revision"],
            "evidence_observation_ids": evidence, "excluded_observations": normalized_excluded,
            "artifacts": normalized_artifacts, "received_at": utc_now(), "processing_scope": "geometry_only_v1",
            "analysis_origin": result.get("analysis_origin", "live_model"),
        }

    def _requeue(self, connection, work: dict) -> None:
        connection.execute(
            "UPDATE work_items SET status='pending', next_attempt_at=?, lease_until=0, lease_token=NULL, error=NULL "
            "WHERE work_id=? AND lease_token=?",
            (time.time() + getattr(self.settings, "geometry_poll_interval_s", 2.0), work["work_id"], work["lease_token"]),
        )

    def poll(self, work: dict, client: httpx.Client | None = None) -> None:
        context = self._load_poll_context(work)
        if context is None:
            return
        session, command, inventory = context
        result = self._fetch(work["operation_id"], client)
        revision = self._validate_job(result, session, command)
        with self.database.transaction() as connection:
            if not self.workflow._owns_lease(connection, work):
                return
            session = self.service._load(connection, "sessions", work["session_id"])
            if not self._active(session, work["operation_id"]):
                self.workflow._finish(connection, work)
                return
            job = session["geometry_job"]
            current_revision = job.get("revision", 0)
            if revision < current_revision:
                self._requeue(connection, work)
                return
            if revision == current_revision:
                if result["status"] != job["status"]:
                    raise _result_error()
                self._requeue(connection, work)
                return
            status = result["status"]
            geometry_result = self._ready_result(result, session, inventory, revision) if status == "ready" else None
            if status == "failed" and (not isinstance(result.get("error"), dict) or not result["error"]):
                raise _result_error()
            job.update(status=status, remote_status=status, revision=revision, result_error=None)
            session["processing_scope"] = "geometry_only_v1"
            if status == "ready":
                job["geometry_revision"] = result["geometry_revision"]
                session.update(geometry_result=geometry_result, processing_state="completed", error=None)
            elif status in ("failed", "cancelled"):
                error = APIError(
                    502, "geometry_build_failed" if status == "failed" else "geometry_job_cancelled",
                    "Geometry Service reported a failed job" if status == "failed" else "Geometry Service cancelled the job",
                ).body
                job["error"] = error
                session.update(geometry_result=None, processing_state=status, error=error)
            else:
                session["processing_state"] = "queued_geometry" if status == "queued" else "building_geometry"
            connection.execute("UPDATE geometry_jobs SET data=? WHERE job_id=?", (canonical_json(job), work["operation_id"]))
            self.workflow._save_session(connection, session)
            if status in _TERMINAL:
                self.workflow._finish(connection, work)
            else:
                self._requeue(connection, work)
