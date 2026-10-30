-- Admin-only migration. The runtime role cannot own/alter tables or use other service schemas.
CREATE SCHEMA drawing_photo;
REVOKE ALL ON SCHEMA drawing_photo FROM PUBLIC;
CREATE TABLE drawing_photo.tasks (
    id TEXT PRIMARY KEY,
    digest TEXT NOT NULL,
    request TEXT NOT NULL,
    response TEXT NOT NULL
);
REVOKE ALL ON drawing_photo.tasks FROM PUBLIC;
GRANT USAGE ON SCHEMA drawing_photo TO drawing_photo_app;
GRANT SELECT, INSERT ON drawing_photo.tasks TO drawing_photo_app;
GRANT UPDATE (response) ON drawing_photo.tasks TO drawing_photo_app;
