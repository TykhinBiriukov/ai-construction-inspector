-- Apply with the administrative database connection before deploying the runtime.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
SELECT pg_advisory_xact_lock(7341202601);
CREATE TABLE IF NOT EXISTS inspector.backlog_items (
    item_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS backlog_by_project ON inspector.backlog_items(project_id, updated_at);
REVOKE ALL ON inspector.backlog_items FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON inspector.backlog_items TO inspector_app;
COMMIT;
