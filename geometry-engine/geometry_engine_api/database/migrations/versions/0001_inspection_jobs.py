"""Persist inspection submissions and enforce idempotency in PostgreSQL."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = '0001_inspection_jobs'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'inspection_jobs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.String(64), nullable=False),
        sa.Column('capture_manifest_path', sa.String(1024), nullable=False),
        sa.Column('ifc_object_key', sa.String(1024), nullable=False),
        sa.Column('ifc_sha256', sa.String(64), nullable=True),
        sa.Column('idempotency_key', sa.String(128), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('status', sa.String(16), server_default='queued', nullable=False),
        sa.Column('current_stage', sa.String(64), nullable=True),
        sa.Column('pipeline_version', sa.String(64), nullable=False),
        sa.Column('parameters', JSONB(), nullable=False),
        sa.Column('result_manifest_key', sa.String(1024), nullable=True),
        sa.Column('error_code', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_inspection_jobs_idempotency_key'),
        sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
                           name='ck_inspection_jobs_status'),
    )
    op.create_index('ix_inspection_jobs_status_created_at', 'inspection_jobs', ['status', 'created_at'])


def downgrade():
    op.drop_index('ix_inspection_jobs_status_created_at', table_name='inspection_jobs')
    op.drop_table('inspection_jobs')
