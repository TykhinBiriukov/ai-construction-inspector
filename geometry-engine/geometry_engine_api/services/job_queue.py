"""PostgreSQL work leases with fenced writes and durable stage checkpoints.

Every method opens a short transaction. No database lock is held while geometry,
Storage, or model inference is running. An expired worker cannot publish state.
"""
from dataclasses import dataclass
from datetime import timedelta
import re
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, noload

from geometry_engine_api.database.models import InspectionJob, InspectionStage, ViewerExport


class LeaseLost(RuntimeError):
    pass


class AttemptsExhausted(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkLease:
    id: UUID
    token: UUID
    kind: str
    session_id: str
    parameters: dict
    capture_manifest_path: str
    ifc_object_key: str
    pipeline_version: str
    result_manifest_key: str | None = None
    result_manifest_sha256: str | None = None
    source_job_id: UUID | None = None
    fingerprint: str | None = None
    viewer_version: str | None = None
    requested_implementation: str | None = None

    @property
    def implementation_sha256(self):
        return self.requested_implementation


@dataclass(frozen=True)
class StageState:
    name: str
    status: str
    attempt: int
    fingerprint: str
    artifact_manifest_key: str | None
    artifact_sha256: str | None
    error_code: str | None


def _state(row):
    return StageState(**{name: getattr(row, name) for name in StageState.__dataclass_fields__})


def _code(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9_]{1,64}', value):
        raise ValueError('Use a sanitized error code')
    return value


def _fingerprint(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('Expected a SHA-256 fingerprint')
    return value


class JobQueue:
    def __init__(self, engine, *, lease_seconds=120, max_attempts=3, retry_base_seconds=10):
        if lease_seconds <= 0 or max_attempts < 1 or retry_base_seconds < 0:
            raise ValueError('Invalid queue retry or lease settings')
        self.engine = engine
        self.lease_seconds = float(lease_seconds)
        self.max_attempts = int(max_attempts)
        self.retry_base_seconds = float(retry_base_seconds)

    @staticmethod
    def _model(kind):
        if kind not in ('inspection', 'viewer'):
            raise ValueError('Unknown work kind')
        return InspectionJob if kind == 'inspection' else ViewerExport

    def claim(self, kind='inspection', worker_id='worker'):
        if not worker_id or len(worker_id) > 128:
            raise ValueError('Invalid worker id')
        model = self._model(kind)
        with Session(self.engine, expire_on_commit=False) as session, session.begin():
            now = session.scalar(select(func.clock_timestamp()))
            row = session.scalars(select(model).options(noload('*')).where(
                or_(and_(model.status == 'queued', or_(model.next_attempt_at.is_(None), model.next_attempt_at <= now)),
                    and_(model.status == 'running', or_(model.lease_expires_at.is_(None), model.lease_expires_at <= now)))
            ).order_by(model.created_at, model.id).with_for_update(skip_locked=True).limit(1)).one_or_none()
            if row is None:
                return None
            if row.status == 'running':
                owner = (InspectionStage.job_id == row.id if kind == 'inspection'
                         else InspectionStage.viewer_export_id == row.id)
                active = session.scalar(select(func.count()).select_from(InspectionStage).where(
                    owner, InspectionStage.status == 'running'))
                # A crash before begin_stage (or between stages) must not create
                # an infinite stream of uncounted recoveries.
                if not active:
                    row.recovery_attempts += 1
                    if row.recovery_attempts >= self.max_attempts:
                        self._finish_failed(row, now, 'recovery_attempts_exhausted')
                        return None
            row.status, row.lease_token, row.worker_id = 'running', uuid4(), worker_id
            row.heartbeat_at, row.lease_expires_at = now, now + timedelta(seconds=self.lease_seconds)
            row.started_at = row.started_at or now
            row.updated_at, row.next_attempt_at, row.error_code, row.finished_at = now, None, None, None
            job = row if kind == 'inspection' else session.get(InspectionJob, row.job_id, options=[noload('*')])
            return WorkLease(row.id, row.lease_token, kind, job.session_id, dict(job.parameters),
                job.capture_manifest_path, job.ifc_object_key, job.pipeline_version,
                job.result_manifest_key if kind == 'inspection' else row.source_manifest_key,
                job.result_manifest_sha256 if kind == 'inspection' else row.source_manifest_sha256,
                None if kind == 'inspection' else job.id,
                None if kind == 'inspection' else row.fingerprint,
                None if kind == 'inspection' else row.viewer_version,
                None if kind == 'inspection' else row.implementation_sha256)

    def _locked(self, session, lease):
        model = self._model(lease.kind)
        row = session.scalars(select(model).options(noload('*')).where(
            model.id == lease.id, model.status == 'running', model.lease_token == lease.token,
            model.lease_expires_at > func.clock_timestamp()).with_for_update()).one_or_none()
        if row is None:
            raise LeaseLost('Worker lease expired or was replaced')
        return row

    def heartbeat(self, lease):
        model = self._model(lease.kind)
        with Session(self.engine) as session, session.begin():
            now = session.scalar(select(func.clock_timestamp()))
            count = session.execute(update(model).where(model.id == lease.id, model.status == 'running',
                model.lease_token == lease.token, model.lease_expires_at > now).values(
                    heartbeat_at=now, lease_expires_at=now + timedelta(seconds=self.lease_seconds), updated_at=now)).rowcount
            return count == 1

    @staticmethod
    def _stage_filter(lease):
        return (InspectionStage.job_id == lease.id if lease.kind == 'inspection'
                else InspectionStage.viewer_export_id == lease.id)

    def stages(self, lease):
        with Session(self.engine) as session:
            return [_state(row) for row in session.scalars(select(InspectionStage).options(noload('*')).where(
                self._stage_filter(lease)).order_by(InspectionStage.created_at, InspectionStage.name))]

    def begin_stage(self, lease, name, fingerprint):
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name):
            raise ValueError('Invalid stage name')
        _fingerprint(fingerprint)
        exhausted = False
        with Session(self.engine, expire_on_commit=False) as session, session.begin():
            job = self._locked(session, lease)
            row = session.scalar(select(InspectionStage).options(noload('*')).where(
                self._stage_filter(lease), InspectionStage.name == name))
            if row is None:
                row = InspectionStage(id=uuid4(), name=name, fingerprint=fingerprint, attempt=0, attempt_base=0,
                    **({'job_id': lease.id} if lease.kind == 'inspection' else {'viewer_export_id': lease.id}))
                session.add(row)
            elif row.fingerprint != fingerprint:
                raise ValueError('Stage inputs or settings changed; submit a new inspection')
            elif row.status == 'completed':
                return _state(row)
            elif row.status == 'running' and row.lease_token == lease.token:
                raise ValueError('Stage already started under this worker lease')
            now = session.scalar(select(func.clock_timestamp()))
            if row.attempt - row.attempt_base >= self.max_attempts:
                row.status, row.error_code, row.finished_at = 'failed', 'attempts_exhausted', now
                self._finish_failed(job, now, 'attempts_exhausted')
                exhausted = True
            else:
                row.attempt += 1
                row.status, row.started_at, row.finished_at, row.error_code = 'running', now, None, None
                row.lease_token = lease.token
                row.artifact_manifest_key, row.artifact_sha256 = None, None
                if lease.kind == 'inspection':
                    job.current_stage = name
                job.updated_at = now
            state = _state(row)
        if exhausted:
            raise AttemptsExhausted('Stage attempt limit reached')
        return state

    def complete_stage(self, lease, name, artifact_manifest_key, artifact_sha256):
        _fingerprint(artifact_sha256)
        if not artifact_manifest_key:
            raise ValueError('A published artifact manifest is required')
        with Session(self.engine) as session, session.begin():
            job = self._locked(session, lease)
            row = session.scalar(select(InspectionStage).where(self._stage_filter(lease), InspectionStage.name == name))
            if row is None or row.status != 'running' or row.lease_token != lease.token:
                raise ValueError('Stage is not running')
            row.status, row.artifact_manifest_key, row.artifact_sha256 = 'completed', artifact_manifest_key, artifact_sha256
            row.finished_at = session.scalar(select(func.clock_timestamp()))
            row.error_code, job.updated_at = None, row.finished_at

    @staticmethod
    def _release(row):
        row.lease_token, row.worker_id, row.lease_expires_at = None, None, None

    @classmethod
    def _finish_failed(cls, row, now, error_code):
        row.status, row.error_code, row.finished_at, row.updated_at = 'failed', error_code, now, now
        row.next_attempt_at = None
        cls._release(row)

    def fail_stage(self, lease, name, error_code, *, retryable,
                   artifact_manifest_key=None, artifact_sha256=None):
        _code(error_code)
        if artifact_manifest_key is not None:
            _fingerprint(artifact_sha256)
        with Session(self.engine) as session, session.begin():
            job = self._locked(session, lease)
            row = session.scalar(select(InspectionStage).where(self._stage_filter(lease), InspectionStage.name == name))
            if row is None or row.status != 'running' or row.lease_token != lease.token:
                raise ValueError('Stage is not running')
            now = session.scalar(select(func.clock_timestamp()))
            row.error_code, row.finished_at = error_code, now
            if artifact_manifest_key is not None:
                row.artifact_manifest_key, row.artifact_sha256 = artifact_manifest_key, artifact_sha256
            attempts = row.attempt - row.attempt_base
            if retryable and attempts < self.max_attempts:
                row.status, job.status, job.error_code = 'retry_wait', 'queued', error_code
                delay = self.retry_base_seconds * (2 ** (attempts - 1))
                job.next_attempt_at, job.updated_at = now + timedelta(seconds=delay), now
                self._release(job)
            else:
                row.status = 'failed'
                self._finish_failed(job, now, error_code)
            return row.status

    def complete(self, lease, result_manifest_key, result_manifest_sha256, *, ifc_sha256=None):
        _fingerprint(result_manifest_sha256)
        if ifc_sha256 is not None:
            _fingerprint(ifc_sha256)
        if not result_manifest_key:
            raise ValueError('A published result manifest is required')
        with Session(self.engine) as session, session.begin():
            row = self._locked(session, lease)
            unfinished = session.scalar(select(func.count()).select_from(InspectionStage).where(
                self._stage_filter(lease), InspectionStage.status != 'completed'))
            if unfinished:
                raise ValueError('Cannot complete work with unfinished stages')
            row.status, row.result_manifest_key, row.result_manifest_sha256 = 'completed', result_manifest_key, result_manifest_sha256
            row.finished_at = session.scalar(select(func.clock_timestamp()))
            row.updated_at, row.next_attempt_at, row.error_code = row.finished_at, None, None
            if lease.kind == 'inspection':
                row.current_stage, row.ifc_sha256 = None, ifc_sha256
            self._release(row)

    def fail(self, lease, error_code, *, retryable=False):
        """Bound recovery/upload failures that occur outside a running stage."""
        _code(error_code)
        with Session(self.engine) as session, session.begin():
            row = self._locked(session, lease)
            now = session.scalar(select(func.clock_timestamp()))
            row.recovery_attempts += 1
            if retryable and row.recovery_attempts < self.max_attempts:
                row.status, row.error_code, row.updated_at = 'queued', error_code, now
                delay = self.retry_base_seconds * (2 ** (row.recovery_attempts - 1))
                row.next_attempt_at = now + timedelta(seconds=delay)
                self._release(row)
                return 'retry_wait'
            self._finish_failed(row, now, error_code)
            return 'failed'
