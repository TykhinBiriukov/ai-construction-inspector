import base64
import binascii
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import BinaryIO, Literal
from uuid import uuid4

from .config import Settings
from .errors import APIError
from .schemas import AcknowledgeRequest, CaptureStartRequest, CreateSessionRequest, InspectionInput
from .storage import Database, LocalBIMStore, canonical_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Principal:
    role: Literal["operator", "device", "geometry"]
    project_id: str
    device_id: str | None = None


def require_role(principal: Principal, role: str) -> None:
    if principal.role != role:
        raise APIError(403, "forbidden", f"This action requires the {role} identity")


class InspectorService:
    def __init__(self, settings: Settings, *, database=None, bim_store=None, model_client=None, inspectors=None,
                 remote_transport=None, secret_resolver=None) -> None:
        self.settings = settings
        if database is not None:
            self.database = database
        elif settings.database_url:
            from .postgres_storage import PostgresDatabase
            self.database = PostgresDatabase(settings.database_url)
        else:
            self.database = Database(settings.data_dir / "inspector.sqlite3")
        if bim_store is not None:
            self.bim_store = bim_store
        elif settings.bim_storage == "supabase":
            from .supabase_bim import SupabaseBIMStore
            self.bim_store = SupabaseBIMStore(settings)
        else:
            self.bim_store = LocalBIMStore(settings.data_dir, settings.max_bim_bytes)
        from .workflow import CaptureWorkflow
        self.capture = CaptureWorkflow(self)
        from .session_folders import SupabaseSessionFolders
        self.session_folders = SupabaseSessionFolders(settings)
        from .device_events import DeviceEvents
        self.device_events = DeviceEvents(self)
        self._geometry_results = None
        if inspectors is None:
            from .a2a_protocol import remote_profiles
            inspectors = remote_profiles(settings.remote_agents_json)
        from .agent import OrchestratorAgent
        self.agent = OrchestratorAgent(self, model_client=model_client, inspectors=inspectors,
                                      remote_transport=remote_transport, secret_resolver=secret_resolver)
        from .workspace import Workspace
        self.workspace = Workspace(self)

    @property
    def geometry_results(self):
        if self._geometry_results is None:
            from .geometry_results import GeometryResults
            self._geometry_results = GeometryResults(self)
        return self._geometry_results

    @staticmethod
    def _load(connection: sqlite3.Connection, table: str, resource_id: str) -> dict:
        # Table and column names are internal constants, never request data.
        if table == "inspections":
            query = "SELECT data FROM inspections WHERE inspection_id=?"
        elif table == "sessions":
            query = "SELECT data FROM sessions WHERE session_id=?"
        else:
            raise ValueError("Unknown resource table")
        row = connection.execute(query, (resource_id,)).fetchone()
        if row is None:
            raise APIError(404, "not_found", "Resource was not found")
        return json.loads(row["data"])

    @staticmethod
    def _check_access(principal: Principal, resource: dict) -> None:
        if resource["project_id"] != principal.project_id:
            raise APIError(403, "forbidden", "Resource belongs to another project")
        if principal.role == "device" and resource.get("device_id") != principal.device_id:
            raise APIError(403, "forbidden", "Session is assigned to another device")

    def create_inspection(
        self, principal: Principal, request: InspectionInput, source: BinaryIO,
        filename: str, media_type: str,
    ) -> tuple[dict, int]:
        require_role(principal, "operator")
        if request.project_id != principal.project_id:
            raise APIError(403, "forbidden", "Project is outside this identity's scope")
        safe_filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        safe_filename = "".join(character for character in safe_filename if ord(character) >= 32)[:255]
        safe_filename = safe_filename or "bim-model"
        if not re.fullmatch(r"[\w.+-]+/[\w.+-]+", media_type, flags=re.ASCII):
            media_type = "application/octet-stream"
        scope = f"inspection:{principal.project_id}"
        with self.bim_store.stage(source) as model:
            body = request.model_dump(mode="json") | {
                "filename": safe_filename, "media_type": media_type,
                "sha256": model.sha256, "byte_length": model.byte_length,
            }
            with self.database.transaction() as connection:
                replay = self.database.replay(connection, scope, str(request.request_id), body)
                if replay is not None:
                    return replay, 200
                inspection_id = str(uuid4())
                inspection = {
                    "schema_version": "0.1", "inspection_id": inspection_id,
                    "project_id": request.project_id, "room_id": request.room_id,
                    "model_version": f"upload-sha256:{model.sha256}",
                    "model_is_synthetic": request.model_is_synthetic,
                    "policy_version": self.settings.policy_version,
                    "prompt": request.prompt, "created_at": utc_now(),
                    "bim_model": {
                        "filename": safe_filename, "media_type": media_type,
                        "byte_length": model.byte_length, "sha256": model.sha256,
                        "status": "stored_unvalidated",
                        "download_url": f"/v1/inspections/{inspection_id}/bim-model",
                    },
                }
                self.bim_store.publish(model)
                connection.execute(
                    "INSERT INTO inspections(inspection_id, project_id, data) VALUES (?, ?, ?)",
                    (inspection_id, request.project_id, canonical_json(inspection)),
                )
                self.database.save_receipt(connection, scope, str(request.request_id), body, inspection)
                return inspection, 201

    def get_inspection(self, principal: Principal, inspection_id: str) -> dict:
        require_role(principal, "operator")
        with self.database.connection() as connection:
            inspection = self._load(connection, "inspections", inspection_id)
            self._check_access(principal, inspection)
            return inspection

    def create_session(self, principal: Principal, inspection_id: str, request: CreateSessionRequest) -> tuple[dict, int]:
        require_role(principal, "operator")
        body = request.model_dump(mode="json")
        scope = f"create-session:{inspection_id}"
        with self.database.transaction() as connection:
            inspection = self._load(connection, "inspections", inspection_id)
            self._check_access(principal, inspection)
            replay = self.database.replay(connection, scope, str(request.request_id), body)
            created = replay is None
            if created:
                session = self._new_session(connection, principal, inspection, request)
                session_id = session["session_id"]
                self.database.save_receipt(connection, scope, str(request.request_id), body, session)
                if not self.settings.supabase_enabled:
                    from .device_events import emit_session
                    emit_session(connection, session, "session.assigned")
            else:
                session_id = replay["session_id"]
        # The ID and request receipt commit before cloud IO, allowing a retry
        # after failure to provision the exact same folder outside the DB lock.
        self._ensure_session_folder(session_id, scope, str(request.request_id))
        with self.database.connection() as connection:
            response = self.database.replay(connection, scope, str(request.request_id), body)
        return response, 201 if created else 200

    def _new_session(self, connection, principal: Principal, inspection: dict, request: CreateSessionRequest) -> dict:
        if request.device_id != self.settings.device_id:
            raise APIError(422, "unknown_device", "Device is not registered for this project")
        if request.room_id != inspection["room_id"]:
            raise APIError(422, "invalid_room", "Room does not match the inspection")
        if request.parent_session_id is not None:
            parent = self._load(connection, "sessions", str(request.parent_session_id))
            self._check_access(principal, parent)
            if parent["model_version"] != inspection["model_version"]:
                raise APIError(422, "invalid_parent_session", "Parent uses a different BIM model version")
        session_id = str(uuid4())
        session = {
            "schema_version": "0.1", "session_id": session_id,
            "inspection_id": inspection["inspection_id"], "project_id": inspection["project_id"],
            "room_id": inspection["room_id"], "model_version": inspection["model_version"],
            "model_is_synthetic": inspection["model_is_synthetic"],
            "policy_version": inspection["policy_version"], "device_id": request.device_id,
            "capture_origin": request.capture_origin, "input_mode": request.input_mode,
            "parent_session_id": str(request.parent_session_id) if request.parent_session_id else None,
            "created_at": utc_now(), "revision": 1,
            "capture_state": "assigned", "processing_state": "idle",
            "storage": {
                "storage_id": self.settings.storage_id,
                "key_prefix": f"projects/{inspection['project_id']}/sessions/{session_id}/",
            },
            "device_acknowledged_at": None,
            "capture_task": {"text": inspection["prompt"], "source": "operator_request"},
            "completion": None, "source": None, "geometry_job": None,
            "display_view_id": None, "result": None, "error": None,
            "geometry_result": None, "processing_scope": "geometry_only_v1",
        }
        if self.settings.supabase_enabled:
            session["storage"].update(
                storage_id="capture-supabase", provider="supabase",
                project_url=self.settings.supabase_url.rstrip("/"),
                bucket=self.settings.supabase_storage_bucket, folder_status="pending",
            )
        # The session row is also its durable assignment; there is no second source of truth.
        connection.execute(
            "INSERT INTO sessions(session_id, inspection_id, project_id, device_id, data, storage_ready) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, inspection["inspection_id"], inspection["project_id"], request.device_id,
             canonical_json(session), 0 if self.settings.supabase_enabled else 1),
        )
        return session

    def quick_session(self, principal: Principal, request) -> tuple[dict, int]:
        require_role(principal, "operator")
        scope = f"quick-session:{principal.project_id}"
        body = request.model_dump(mode="json")
        with self.database.transaction() as connection:
            replay = self.database.replay(connection, scope, str(request.request_id), body)
            created = replay is None
            if created:
                inspection = {
                    "schema_version": "0.1", "inspection_id": str(uuid4()), "project_id": principal.project_id,
                    "room_id": request.room_id, "model_version": "unbound", "model_is_synthetic": False,
                    "policy_version": self.settings.policy_version, "prompt": request.prompt,
                    "created_at": utc_now(), "bim_model": None, "inspection_kind": "capture_only",
                }
                connection.execute("INSERT INTO inspections(inspection_id, project_id, data) VALUES (?, ?, ?)",
                                   (inspection["inspection_id"], principal.project_id, canonical_json(inspection)))
                session = self._new_session(connection, principal, inspection, request)
                self.database.save_receipt(connection, scope, str(request.request_id), body, session)
                if not self.settings.supabase_enabled:
                    from .device_events import emit_session
                    emit_session(connection, session, "session.assigned")
                session_id = session["session_id"]
            else:
                session_id = replay["session_id"]
        self._ensure_session_folder(session_id, scope, str(request.request_id))
        with self.database.connection() as connection:
            result = self.database.replay(connection, scope, str(request.request_id), body)
        return result, 201 if created else 200

    def _ensure_session_folder(self, session_id: str, scope: str, request_id: str) -> None:
        with self.database.connection() as connection:
            session = self._load(connection, "sessions", session_id)
        if session["storage"].get("provider") != "supabase" or session["storage"]["folder_status"] == "ready":
            return
        self.session_folders.ensure(session)
        with self.database.transaction() as connection:
            current = self._load(connection, "sessions", session_id)
            if current["storage"]["folder_status"] == "ready":
                return
            current["storage"]["folder_status"] = "ready"
            current["revision"] += 1
            connection.execute("UPDATE sessions SET data=?, storage_ready=1 WHERE session_id=?", (canonical_json(current), session_id))
            receipt = connection.execute("SELECT response_body FROM receipts WHERE scope=? AND operation_id=?", (scope, request_id)).fetchone()
            original = json.loads(receipt["response_body"])
            original["storage"] = current["storage"]
            original["revision"] = current["revision"]
            connection.execute("UPDATE receipts SET response_body=? WHERE scope=? AND operation_id=?", (canonical_json(original), scope, request_id))
            from .device_events import emit_session
            emit_session(connection, current, "session.assigned")

    def get_session(self, principal: Principal, session_id: str) -> dict:
        with self.database.connection() as connection:
            session = self._load(connection, "sessions", session_id)
            self._check_access(principal, session)
            return session

    def abort_session(self, principal: Principal, session_id: str) -> dict:
        if principal.role not in ("operator", "device"):
            raise APIError(403, "forbidden", "Only operators and the assigned device can cancel a session")
        with self.database.transaction() as connection:
            session = self._load(connection, "sessions", session_id)
            self._check_access(principal, session)
            if session["processing_state"] == "cancelled":
                return session
            if session["processing_state"] in ("completed", "failed"):
                raise APIError(409, "session_terminal", "Finished sessions cannot be aborted")
            error = APIError(409, "session_cancelled", "Session cancelled by " + principal.role).body
            # Revoke leases atomically: an in-flight worker cannot publish a late result.
            connection.execute(
                "UPDATE work_items SET status='cancelled', lease_until=0, lease_token=NULL "
                "WHERE session_id=? AND status IN ('pending', 'processing')", (session_id,),
            )
            if session.get("completion") and session["completion"]["status"] == "pending":
                session["completion"] = {**session["completion"], "status": "rejected", "error": error}
                connection.execute("UPDATE capture_completions SET data=? WHERE session_id=? AND completion_id=?",
                                   (canonical_json(session["completion"]), session_id, session["completion"]["completion_id"]))
            session.update(capture_state="cancelled", processing_state="cancelled", error=error)
            self.capture._save_session(connection, session)
            return session

    def list_assignments(self, principal: Principal, device_id: str, after: str | None, limit: int) -> dict:
        require_role(principal, "device")
        if principal.device_id != device_id:
            raise APIError(403, "forbidden", "Cannot read another device's assignments")
        position = 0
        if after is not None:
            try:
                decoded = json.loads(base64.b64decode(after, altchars=b"-_", validate=True))
                if (
                    not isinstance(decoded, dict)
                    or set(decoded) != {"v", "project", "device", "position"}
                    or decoded["v"] != 1
                    or decoded["project"] != principal.project_id
                    or decoded["device"] != device_id
                    or type(decoded["position"]) is not int
                    or not 0 <= decoded["position"] <= 9223372036854775807
                ):
                    raise ValueError
                position = decoded["position"]
            except (ValueError, TypeError, binascii.Error, UnicodeDecodeError, RecursionError):
                raise APIError(422, "invalid_cursor", "Assignment cursor is invalid for this device") from None
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT assignment_order, data FROM sessions "
                "WHERE project_id=? AND device_id=? AND storage_ready=1 AND assignment_order>? "
                "ORDER BY assignment_order LIMIT ?",
                (principal.project_id, device_id, position, limit + 1),
            ).fetchall()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            cursor = {
                "v": 1, "project": principal.project_id, "device": device_id,
                "position": page[-1]["assignment_order"],
            }
            next_cursor = base64.urlsafe_b64encode(canonical_json(cursor).encode()).decode()
        return {"assignments": [json.loads(row["data"]) for row in page], "next_cursor": next_cursor}

    def acknowledge(self, principal: Principal, session_id: str, request: AcknowledgeRequest) -> tuple[dict, int]:
        return self._device_operation(principal, session_id, request, start_capture=False)

    def start_capture(self, principal: Principal, session_id: str, request: CaptureStartRequest) -> tuple[dict, int]:
        return self._device_operation(principal, session_id, request, start_capture=True)

    def _device_operation(
        self, principal: Principal, session_id: str,
        request: AcknowledgeRequest, *, start_capture: bool,
    ) -> tuple[dict, int]:
        require_role(principal, "device")
        operation = "capture-start" if start_capture else "acknowledge"
        scope = f"{operation}:{session_id}"
        body = request.model_dump(mode="json")
        with self.database.transaction() as connection:
            session = self._load(connection, "sessions", session_id)
            self._check_access(principal, session)
            replay = self.database.replay(connection, scope, str(request.operation_id), body)
            if replay is not None:
                return replay, 200
            if session["storage"].get("folder_status") == "pending":
                raise APIError(409, "session_storage_not_ready", "Session folder is not ready for device operations")
            if session["capture_state"] not in ("assigned", "capturing"):
                raise APIError(409, "session_closed", "Session no longer accepts new device operations")
            receipt = {"session_id": session_id, "operation_id": str(request.operation_id)}
            changed = False
            if start_capture:
                if session["capture_state"] == "assigned":
                    session["capture_state"] = "capturing"
                    changed = True
                receipt["capture_state"] = "capturing"
            else:
                if session["device_acknowledged_at"] is None:
                    session["device_acknowledged_at"] = utc_now()
                    changed = True
                receipt["acknowledged_at"] = session["device_acknowledged_at"]
            if changed:
                session["revision"] += 1
                connection.execute(
                    "UPDATE sessions SET data=? WHERE session_id=?", (canonical_json(session), session_id),
                )
                from .device_events import emit_session
                emit_session(connection, session)
            self.database.save_receipt(connection, scope, str(request.operation_id), body, receipt)
            return receipt, 201
