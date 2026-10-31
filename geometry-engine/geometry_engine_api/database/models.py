"""Persisted inspection requests. Stage execution will be added separately."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
