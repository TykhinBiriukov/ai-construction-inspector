"""Durable, race-safe submission of inspection jobs; no pipeline execution."""
import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from geometry_engine_api.database.models import InspectionJob
from geometry_engine_api.schemas.inspections import InspectionRequest


PIPELINE_VERSION = 'mesh-contours-v1'


class IdempotencyConflict(ValueError):
    pass


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
