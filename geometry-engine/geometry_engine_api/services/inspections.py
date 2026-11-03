"""Durable, race-safe submission of inspection jobs; no pipeline execution."""
import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, noload

from geometry_engine_api.database.models import InspectionJob, ViewerExport
from geometry_engine_api.schemas.inspections import InspectionRequest


PIPELINE_VERSION = 'mesh-contours-v1'


class IdempotencyConflict(ValueError):
    pass


class InspectionNotReady(ValueError):
    pass


def viewer_fingerprint(manifest_key, manifest_sha256, viewer_version, implementation):
    """One export identity for both queued viewers and comparison-only revisions."""
    canonical = json.dumps({'manifest': manifest_key, 'sha256': manifest_sha256,
        'viewer_version': viewer_version, 'implementation': implementation}, sort_keys=True)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


class InspectionService:
    def __init__(self, session: Session):
        self.session = session

    def submit(self, request: InspectionRequest, idempotency_key: str) -> InspectionJob:
        body = request.model_dump(mode='json')
        canonical = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        fingerprint = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        with self.session.begin():
            statement = insert(InspectionJob).values(
                id=uuid4(), **body, idempotency_key=idempotency_key,
                request_hash=fingerprint, pipeline_version=PIPELINE_VERSION,
            ).on_conflict_do_nothing(
                constraint='uq_inspection_jobs_idempotency_key'
            ).returning(InspectionJob)
            job = self.session.scalars(statement).one_or_none()
            if job is None:
                # ON CONFLICT waits for a concurrent insert to commit. Under the
                # default READ COMMITTED isolation, this SELECT sees that record.
                job = self.session.scalars(select(InspectionJob).where(
                    InspectionJob.idempotency_key == idempotency_key)).one()
                if job.request_hash != fingerprint:
                    raise IdempotencyConflict('Idempotency-Key was already used with different request parameters')
        return job

    def get(self, job_id: UUID) -> InspectionJob | None:
        return self.session.get(InspectionJob, job_id)

    def submit_viewer(self, job_id: UUID, *, retry_failed=False) -> ViewerExport | None:
        from geometry_engine.inspection import VIEWER_VERSION, code_fingerprint
        with self.session.begin():
            job = self.session.get(InspectionJob, job_id, options=[noload('*')])
            if job is None:
                return None
            if job.status != 'completed' or not job.result_manifest_key or not job.result_manifest_sha256:
                raise InspectionNotReady('Complete the inspection before generating its viewer')
            implementation = code_fingerprint()
            fingerprint = viewer_fingerprint(job.result_manifest_key, job.result_manifest_sha256,
                                             VIEWER_VERSION, implementation)
            export = self.session.scalar(insert(ViewerExport).values(
                id=uuid4(), job_id=job.id, fingerprint=fingerprint,
                source_manifest_key=job.result_manifest_key,
                source_manifest_sha256=job.result_manifest_sha256,
                viewer_version=VIEWER_VERSION, implementation_sha256=implementation,
            ).on_conflict_do_nothing(constraint='uq_viewer_exports_job_fingerprint').returning(ViewerExport))
            if export is None:
                export = self.session.scalar(select(ViewerExport).where(
                    ViewerExport.job_id == job.id, ViewerExport.fingerprint == fingerprint)
                    .with_for_update().execution_options(populate_existing=True))
                if retry_failed and export.status == 'failed':
                    export.status, export.error_code, export.finished_at = 'queued', None, None
                    export.lease_token, export.worker_id, export.lease_expires_at = None, None, None
                    export.next_attempt_at, export.recovery_attempts = None, 0
                    export.updated_at = self.session.scalar(select(func.clock_timestamp()))
                    for stage in export.stages:
                        if stage.status != 'completed':
                            stage.attempt_base = stage.attempt
                            stage.status, stage.error_code, stage.finished_at = 'queued', None, None
                            stage.lease_token = None
        return export
