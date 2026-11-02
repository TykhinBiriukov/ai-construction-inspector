"""Reconcile the Geometry Engine /inspections API under Inspector leases."""
import json
import time

from .errors import APIError
from .service import utc_now
from .storage import canonical_json

_TERMINAL = ("ready", "failed", "cancelled")


class GeometryResults:
    def __init__(self, service) -> None:
        self.service = service
        self.database = service.database
        self.settings = service.settings
        self.workflow = service.capture

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

    def _load_poll_context(self, work: dict) -> tuple[dict, dict] | None:
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
            self.service.geometry_engine.require_current_command(command)
            if any(command.get(key) != session.get(key) for key in ("session_id", "project_id")) or (
                    command.get("job_id") != row["job_id"]
                    or command.get("manifest_sha256") != session["source"]["manifest_sha256"]):
                raise APIError(409, "geometry_context_mismatch", "The saved job does not match the accepted source")
            return session, command

    def _requeue(self, connection, work: dict) -> None:
        connection.execute(
            "UPDATE work_items SET status='pending', next_attempt_at=?, lease_until=0, lease_token=NULL, error=NULL "
            "WHERE work_id=? AND lease_token=?",
            (time.time() + getattr(self.settings, "geometry_poll_interval_s", 2.0), work["work_id"], work["lease_token"]),
        )

    def poll(self, work, client=None):
        context = self._load_poll_context(work)
        if context is None:
            return
        session, command = context
        status, result = self.service.geometry_engine.poll(session, command, client)
        with self.database.transaction() as connection:
            if not self.workflow._owns_lease(connection, work):
                return
            session = self.service._load(connection, 'sessions', work['session_id'])
            if not self._active(session, work['operation_id']):
                self.workflow._finish(connection, work)
                return
            job = session['geometry_job']
            # Geometry v1 has no monotonic revision API. These are Inspector-owned
            # acceptance revisions; they are not claimed to be Geometry revisions.
            revision = job.get('revision', 0) + (status != job['status'] or not job.get('revision'))
            job.update(status=status, remote_status=status, revision=revision, result_error=None)
            if status == 'ready':
                private = {'photo_files': result.pop('photo_files'), 'artifacts': result['artifacts']}
                self.database.save_receipt(connection, 'geometry-result-files', work['operation_id'],
                    {'result_manifest_sha256': result['result_manifest_sha256']}, private)
                result['artifacts'] = [{k: v for k, v in artifact.items() if k != 'url'} for artifact in result['artifacts']]
                result.update(job_revision=revision, received_at=utc_now(), revision_origin='inspector_acceptance')
                session.update(geometry_result=result, processing_state='completed', error=None)
                job['geometry_revision'] = result['geometry_revision']
                connection.execute("UPDATE work_items SET next_attempt_at=0 WHERE session_id=? AND kind='geometry-review' AND status='pending'", (work['session_id'],))
            elif status in ('failed', 'cancelled'):
                error = APIError(502, 'geometry_build_failed' if status == 'failed' else 'geometry_job_cancelled',
                                 'Geometry reported a failed or cancelled job').body
                session.update(geometry_result=None, processing_state=status, error=error)
                job['error'] = error
            else:
                session['processing_state'] = 'queued_geometry' if status == 'queued' else 'building_geometry'
            connection.execute('UPDATE geometry_jobs SET data=? WHERE job_id=?', (canonical_json(job), work['operation_id']))
            self.workflow._save_session(connection, session)
            if status in _TERMINAL:
                self.workflow._finish(connection, work)
            else:
                self._requeue(connection, work)
