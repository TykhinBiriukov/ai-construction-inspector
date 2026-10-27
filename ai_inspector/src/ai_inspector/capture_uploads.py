import json

from .errors import APIError
from .service import require_role
from .storage import canonical_json


class CaptureUploads:
    def __init__(self, service) -> None:
        self.service = service
        self.database = service.database

    def plan(self, principal, session_id: str, request) -> tuple[dict, int]:
        require_role(principal, "device")
        body = request.model_dump(mode="json")
        with self.database.transaction() as connection:
            session = self.service._load(connection, "sessions", session_id)
            self.service._check_access(principal, session)
            if session["storage"].get("provider") != "supabase":
                raise APIError(422, "unsupported_storage", "This upload API currently supports Supabase sessions")
            previous = connection.execute("SELECT request_body FROM upload_requests WHERE session_id=? AND request_id=?", (session_id, str(request.request_id))).fetchone()
            if previous and previous["request_body"] != canonical_json(body):
                raise APIError(409, "idempotency_conflict", "Upload request ID has different content")
            if not previous and session["capture_state"] != "capturing":
                raise APIError(409, "session_closed", "Start capture before requesting uploads; closed sessions reject new plans")
            objects = []
            for item in body["objects"]:
                maximum = (self.service.settings.max_archive_bytes if request.kind == "capture_archive" else
                           self.service.settings.max_manifest_bytes if item["name"].endswith("json") else self.service.settings.max_capture_file_bytes)
                if item["byte_length"] > maximum:
                    raise APIError(413, "capture_file_too_large", "Capture file exceeds the configured limit")
                if request.kind == "capture_archive":
                    key = session["storage"]["key_prefix"] + f"archives/{request.completion_id}.zip"
                elif request.kind == "capture_manifest":
                    key = session["storage"]["key_prefix"] + f"manifests/{request.completion_id}.json"
                else:
                    key = session["storage"]["key_prefix"] + f"observations/{item['observation_id']}/{item['name']}"
                metadata = item | {"object_key": key}
                old = connection.execute("SELECT data FROM upload_objects WHERE session_id=? AND object_key=?", (session_id, key)).fetchone()
                if old and json.loads(old["data"]) != metadata:
                    raise APIError(409, "file_conflict", "An observation file was already declared with different bytes")
                objects.append(metadata)
            if previous is None:
                connection.execute("INSERT INTO upload_requests VALUES (?, ?, ?)", (session_id, str(request.request_id), canonical_json(body)))
                for metadata in objects:
                    connection.execute("INSERT OR IGNORE INTO upload_objects VALUES (?, ?, ?)", (session_id, metadata["object_key"], canonical_json(metadata)))
        store = self.service.capture.store_for(session)
        items = []
        for item in objects:
            if store.object_matches(item["object_key"], item["byte_length"], item["sha256"]):
                access = {"status": "already_uploaded", "upload_url": None, "expires_at": None, "headers": {}, "method": "PUT"}
            else:
                if session["capture_state"] != "capturing":
                    raise APIError(409, "session_closed", "Closed session cannot receive new upload permissions")
                access = {"status": "upload_required", **store.signed_upload(item["object_key"], item["media_type"])}
            items.append(item | access | {"version_id": None})
        return {"schema_version": "0.1", "format": "supabase_upload_plan_v1", "request_id": str(request.request_id),
                "session_id": session_id, "kind": request.kind, "completion_id": str(request.completion_id) if request.completion_id else None,
                "items": items}, 200 if previous else 201
