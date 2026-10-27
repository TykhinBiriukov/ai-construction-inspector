"""Durable device delivery, resume cursors and foreground WebSocket presence."""

import base64
import binascii
import json
import time
from datetime import datetime, timezone
from uuid import uuid4

from .errors import APIError
from .storage import canonical_json


def emit_session(connection, session: dict, event_type: str = "session.updated") -> None:
    event_id = str(uuid4())
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    event = {"schema_version": "0.1", "event_id": event_id, "type": event_type,
             "session_id": session["session_id"], "created_at": created_at, "session": session}
    connection.execute(
        "INSERT INTO device_events(event_id, project_id, device_id, session_id, type, data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, session["project_id"], session["device_id"], session["session_id"], event_type, canonical_json(event), created_at),
    )


class DeviceEvents:
    def __init__(self, service) -> None:
        self.service = service
        self.database = service.database

    def _device(self, principal, device_id: str) -> None:
        if principal.role != "device" or principal.device_id != device_id:
            raise APIError(403, "forbidden", "Device identity does not match this channel")

    @staticmethod
    def cursor(project: str, device: str, sequence: int) -> str:
        return base64.urlsafe_b64encode(canonical_json({"v": 1, "project": project, "device": device, "sequence": sequence}).encode()).decode()

    def _position(self, principal, device_id: str, cursor: str | None) -> int:
        if cursor is None:
            return 0
        try:
            item = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if (not isinstance(item, dict) or item.get("v") != 1 or item.get("project") != principal.project_id
                    or item.get("device") != device_id or type(item.get("sequence")) is not int
                    or not 0 <= item["sequence"] <= 9223372036854775807 or len(cursor) > 2048):
                raise ValueError
            return item["sequence"]
        except (ValueError, TypeError, binascii.Error, UnicodeDecodeError, RecursionError):
            raise APIError(422, "invalid_cursor", "Invalid device event cursor") from None

    def pull(self, principal, device_id: str, after: str | None, limit: int = 100) -> dict:
        self._device(principal, device_id)
        position = self._position(principal, device_id, after)
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT sequence, data FROM device_events WHERE project_id=? AND device_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (principal.project_id, device_id, position, limit + 1),
            ).fetchall()
        page = rows[:limit]
        events = []
        for row in page:
            event = json.loads(row["data"])
            event["cursor"] = self.cursor(principal.project_id, device_id, row["sequence"])
            events.append(event)
        return {"events": events, "next_cursor": events[-1]["cursor"] if events else self.cursor(principal.project_id, device_id, position),
                "has_more": len(rows) > limit}

    def snapshot(self, principal, device_id: str) -> dict:
        self._device(principal, device_id)
        with self.database.connection() as connection:
            connection.execute("BEGIN")
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM device_events WHERE project_id=? AND device_id=?",
                (principal.project_id, device_id),
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT data FROM sessions WHERE project_id=? AND device_id=? AND storage_ready=1 ORDER BY assignment_order DESC LIMIT 101",
                (principal.project_id, device_id),
            ).fetchall()
        return {"type": "device.snapshot", "schema_version": "0.1", "device_id": device_id,
                "sessions": [json.loads(row["data"]) for row in reversed(rows[:100])], "has_more": len(rows) > 100,
                "resume_cursor": self.cursor(principal.project_id, device_id, sequence)}

    def connected(self, principal, device_id: str) -> str:
        self._device(principal, device_id)
        connection_id = str(uuid4())
        now = time.time()
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO device_connections VALUES (?, ?, ?, ?, ?, NULL)",
                               (connection_id, principal.project_id, device_id, now, now))
        return connection_id

    def heartbeat(self, connection_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("UPDATE device_connections SET last_seen_at=? WHERE connection_id=? AND closed_at IS NULL", (time.time(), connection_id))

    def disconnected(self, connection_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("UPDATE device_connections SET closed_at=? WHERE connection_id=?", (time.time(), connection_id))

    def status(self, principal, device_id: str) -> dict:
        if principal.role != "operator" or device_id != self.service.settings.device_id:
            raise APIError(403, "forbidden", "Device is outside the operator's scope")
        with self.database.connection() as connection:
            rows = connection.execute("SELECT * FROM device_connections WHERE project_id=? AND device_id=?", (principal.project_id, device_id)).fetchall()
        active = sum(row["closed_at"] is None and row["last_seen_at"] > time.time() - 60 for row in rows)
        last_seen = max((row["last_seen_at"] for row in rows), default=None)
        return {"device_id": device_id, "online": bool(active), "connections": active,
                "last_seen_at": datetime.fromtimestamp(last_seen, timezone.utc).isoformat().replace("+00:00", "Z") if last_seen else None}
