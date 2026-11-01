"""Durable inspection work, stage checkpoints, and inference cache index."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class InspectionJob(Base):
    __tablename__ = 'inspection_jobs'
    __table_args__ = (
        UniqueConstraint('idempotency_key', name='uq_inspection_jobs_idempotency_key'),
        CheckConstraint("status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
                        name='ck_inspection_jobs_status'),
        Index('ix_inspection_jobs_status_created_at', 'status', 'created_at'),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64))
    capture_manifest_path: Mapped[str] = mapped_column(String(1024))
    ifc_object_key: Mapped[str] = mapped_column(String(1024))
    ifc_sha256: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), server_default='queued')
    current_stage: Mapped[str | None] = mapped_column(String(64))
    pipeline_version: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict] = mapped_column(JSONB)
    result_manifest_key: Mapped[str | None] = mapped_column(String(1024))
    result_manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    worker_id: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_attempts: Mapped[int] = mapped_column(Integer, server_default='0')
    stages: Mapped[list['InspectionStage']] = relationship(
        back_populates='job', order_by='InspectionStage.created_at', lazy='selectin')
    viewer_exports: Mapped[list['ViewerExport']] = relationship(
        back_populates='job', order_by='ViewerExport.created_at', lazy='selectin')


class ViewerExport(Base):
    __tablename__ = 'viewer_exports'
    __table_args__ = (
        UniqueConstraint('job_id', 'fingerprint', name='uq_viewer_exports_job_fingerprint'),
        CheckConstraint("status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
                        name='ck_viewer_exports_status'),
        Index('ix_viewer_exports_status_created_at', 'status', 'created_at'),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey('inspection_jobs.id', ondelete='CASCADE'))
    fingerprint: Mapped[str] = mapped_column(String(64))
    source_manifest_key: Mapped[str] = mapped_column(String(1024))
    viewer_version: Mapped[str] = mapped_column(String(64))
    implementation_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), server_default='queued')
    result_manifest_key: Mapped[str | None] = mapped_column(String(1024))
    result_manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    source_manifest_sha256: Mapped[str] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    worker_id: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_attempts: Mapped[int] = mapped_column(Integer, server_default='0')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job: Mapped[InspectionJob] = relationship(back_populates='viewer_exports', lazy='raise')
    stages: Mapped[list['InspectionStage']] = relationship(
        back_populates='viewer_export', order_by='InspectionStage.created_at', lazy='selectin')


class InspectionStage(Base):
    __tablename__ = 'inspection_stages'
    __table_args__ = (
        UniqueConstraint('job_id', 'name', name='uq_inspection_stages_job_name'),
        UniqueConstraint('viewer_export_id', 'name', name='uq_inspection_stages_viewer_name'),
        CheckConstraint('(job_id IS NULL) <> (viewer_export_id IS NULL)', name='ck_inspection_stages_owner'),
        CheckConstraint("status IN ('queued', 'running', 'completed', 'retry_wait', 'failed')", name='ck_inspection_stages_status'),
        CheckConstraint('attempt >= attempt_base AND attempt_base >= 0', name='ck_inspection_stages_attempt'),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey('inspection_jobs.id', ondelete='CASCADE'))
    viewer_export_id: Mapped[UUID | None] = mapped_column(ForeignKey('viewer_exports.id', ondelete='CASCADE'))
    name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), server_default='queued')
    attempt: Mapped[int] = mapped_column(Integer, server_default='0')
    attempt_base: Mapped[int] = mapped_column(Integer, server_default='0')
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    fingerprint: Mapped[str] = mapped_column(String(64))
    artifact_manifest_key: Mapped[str | None] = mapped_column(String(1024))
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job: Mapped[InspectionJob | None] = relationship(back_populates='stages', lazy='raise')
    viewer_export: Mapped[ViewerExport | None] = relationship(back_populates='stages', lazy='raise')


class SegmentationCacheEntry(Base):
    __tablename__ = 'segmentation_cache'
    __table_args__ = (
        CheckConstraint("status IN ('running', 'completed')", name='ck_segmentation_cache_status'),
    )
    request_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    artifact_key: Mapped[str | None] = mapped_column(String(1024))
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_size: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
