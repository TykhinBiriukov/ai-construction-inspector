-- Run once as database administrator. The runtime receives only localization_app.
CREATE SCHEMA IF NOT EXISTS localization;
CREATE TABLE IF NOT EXISTS localization.sessions (
  id TEXT PRIMARY KEY, operation TEXT UNIQUE, state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS localization.models (
  sha256 TEXT PRIMARY KEY, content BYTEA NOT NULL
);
CREATE TABLE IF NOT EXISTS localization.files (
  session_id TEXT NOT NULL, observation_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (name IN ('frame.json','rgb.jpg','depth.f32','confidence.u8')),
  sha256 TEXT NOT NULL, byte_length BIGINT NOT NULL,
  content BYTEA,
  PRIMARY KEY(session_id,observation_id,name)
);
REVOKE ALL ON SCHEMA localization FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA localization FROM PUBLIC;
GRANT USAGE ON SCHEMA localization TO localization_app;
GRANT SELECT,INSERT,UPDATE ON localization.sessions TO localization_app;
GRANT SELECT,INSERT ON localization.files TO localization_app;
GRANT SELECT ON localization.models TO localization_app;
