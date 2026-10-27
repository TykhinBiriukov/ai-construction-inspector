"""Create a private Supabase folder marker for a durable capture-session ID."""

from urllib.parse import quote
from uuid import UUID

import httpx

from .errors import APIError
from .storage import canonical_json


class SupabaseSessionFolders:
    def __init__(self, settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client

    @staticmethod
    def marker_body(session: dict) -> bytes:
        return canonical_json({
            "schema_version": "0.1", "session_id": session["session_id"],
            "inspection_id": session["inspection_id"], "project_id": session["project_id"],
            "created_at": session["created_at"],
        }).encode("utf-8")

    @staticmethod
    def unavailable(session_id: str, status_code: int | None = None) -> APIError:
        details = {"session_id": session_id}
        if status_code is not None:
            details["storage_status_code"] = status_code
        return APIError(503, "session_folder_unavailable", "Session folder could not be confirmed; retry with the same request ID",
                        retryable=True, details=details)

    def ensure(self, session: dict) -> None:
        storage = session["storage"]
        session_id = str(UUID(session["session_id"]))
        expected_prefix = f"projects/{session['project_id']}/sessions/{session_id}/"
        if storage["key_prefix"] != expected_prefix:
            raise APIError(409, "session_folder_conflict", "Session folder prefix does not match its identity")
        if (not self.settings.supabase_enabled
                or storage["project_url"] != self.settings.supabase_url.rstrip("/")
                or storage["bucket"] != self.settings.supabase_storage_bucket):
            raise APIError(503, "session_storage_configuration_changed", "Restore the storage configuration assigned to this session", retryable=True)
        marker_key = expected_prefix + ".session.json"
        object_path = quote(storage["bucket"], safe="") + "/" + quote(marker_key, safe="/")
        url = storage["project_url"] + "/storage/v1/object/" + object_path
        headers = {
            "apikey": self.settings.supabase_secret_key,
            "Content-Type": "application/json", "x-upsert": "false", "Cache-Control": "max-age=0",
        }
        payload = self.marker_body(session)
        owns_client = self.client is None
        client = self.client or httpx.Client(timeout=15, follow_redirects=False)
        try:
            response = client.post(url, headers=headers, content=payload, timeout=15, follow_redirects=False)
            if response.status_code in (200, 201):
                try:
                    receipt = response.json()
                    if receipt.get("Key") != f"{storage['bucket']}/{marker_key}":
                        raise ValueError
                except (ValueError, AttributeError):
                    raise self.unavailable(session_id) from None
                return
            if response.status_code in (400, 409):
                # A timeout can follow a successful POST. Only an identical marker
                # proves that this existing object belongs to the same session.
                existing = client.get(url, headers={"apikey": self.settings.supabase_secret_key}, timeout=15, follow_redirects=False)
                if existing.status_code == 200:
                    if existing.content != payload:
                        raise APIError(409, "session_folder_conflict", "An incompatible session marker already exists")
                    return
                raise self.unavailable(session_id, existing.status_code)
            raise self.unavailable(session_id, response.status_code)
        except httpx.TransportError:
            raise self.unavailable(session_id) from None
        finally:
            if owns_client:
                client.close()
