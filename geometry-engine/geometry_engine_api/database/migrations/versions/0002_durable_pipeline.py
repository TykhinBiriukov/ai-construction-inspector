"""Durable workers, stage checkpoints, viewer exports, and SAM cache index."""
from alembic import op
import sqlalchemy as sa

revision = '0002_durable_pipeline'
down_revision = '0001_inspection_jobs'
branch_labels = None
depends_on = None


def lease_columns():
    return [sa.Column('lease_token', sa.Uuid(), nullable=True),
            sa.Column('worker_id', sa.String(128), nullable=True),
            sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('recovery_attempts', sa.Integer(), server_default='0', nullable=False)]


def upgrade():
    for column in lease_columns():
        op.add_column('inspection_jobs', column)
    op.add_column('inspection_jobs', sa.Column('result_manifest_sha256', sa.String(64), nullable=True))
    op.create_table('viewer_exports',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('job_id', sa.Uuid(), sa.ForeignKey('inspection_jobs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('source_manifest_key', sa.String(1024), nullable=False),
        sa.Column('viewer_version', sa.String(64), nullable=False),
        sa.Column('implementation_sha256', sa.String(64), nullable=False),
        sa.Column('status', sa.String(16), server_default='queued', nullable=False),
        sa.Column('result_manifest_key', sa.String(1024), nullable=True),
        sa.Column('result_manifest_sha256', sa.String(64), nullable=True),
        sa.Column('source_manifest_sha256', sa.String(64), nullable=False),
        sa.Column('error_code', sa.String(64), nullable=True),
        *lease_columns(),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('job_id', 'fingerprint', name='uq_viewer_exports_job_fingerprint'),
        sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'failed', 'cancelled')", name='ck_viewer_exports_status'))
    op.create_index('ix_viewer_exports_status_created_at', 'viewer_exports', ['status', 'created_at'])
    op.create_table('inspection_stages',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('job_id', sa.Uuid(), sa.ForeignKey('inspection_jobs.id', ondelete='CASCADE'), nullable=True),
        sa.Column('viewer_export_id', sa.Uuid(), sa.ForeignKey('viewer_exports.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('status', sa.String(16), server_default='queued', nullable=False),
        sa.Column('attempt', sa.Integer(), server_default='0', nullable=False),
        sa.Column('attempt_base', sa.Integer(), server_default='0', nullable=False),
        sa.Column('lease_token', sa.Uuid(), nullable=True),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('artifact_manifest_key', sa.String(1024), nullable=True),
        sa.Column('artifact_sha256', sa.String(64), nullable=True),
        sa.Column('error_code', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('job_id', 'name', name='uq_inspection_stages_job_name'),
        sa.UniqueConstraint('viewer_export_id', 'name', name='uq_inspection_stages_viewer_name'),
        sa.CheckConstraint('(job_id IS NULL) <> (viewer_export_id IS NULL)', name='ck_inspection_stages_owner'),
        sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'retry_wait', 'failed')", name='ck_inspection_stages_status'),
        sa.CheckConstraint('attempt >= attempt_base AND attempt_base >= 0', name='ck_inspection_stages_attempt'))
    op.create_table('segmentation_cache',
        sa.Column('request_hash', sa.String(64), primary_key=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('lease_token', sa.Uuid(), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('artifact_key', sa.String(1024), nullable=True),
        sa.Column('artifact_sha256', sa.String(64), nullable=True),
        sa.Column('artifact_size', sa.BigInteger(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('running', 'completed')", name='ck_segmentation_cache_status'))
    # Internal worker tables must not become readable through Supabase's public
    # Data API default grants. The server connects as the migration owner or a
    # dedicated BYPASSRLS role; browser roles receive no access policies here.
    for table in ('inspection_jobs', 'inspection_stages', 'viewer_exports', 'segmentation_cache'):
        op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))


def downgrade():
    op.execute(sa.text('ALTER TABLE "inspection_jobs" DISABLE ROW LEVEL SECURITY'))
    op.drop_table('segmentation_cache')
    op.drop_table('inspection_stages')
    op.drop_table('viewer_exports')
    op.drop_column('inspection_jobs', 'result_manifest_sha256')
    for column in reversed(lease_columns()):
        op.drop_column('inspection_jobs', column.name)
