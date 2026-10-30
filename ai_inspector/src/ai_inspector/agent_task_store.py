"""Independent durable inspector tasks on a persistent native SQLite volume."""

import json
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from .errors import APIError
from .service import utc_now
from .storage import Database, canonical_json


class AgentTaskStore(Database):
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
                raise RuntimeError("Unsupported inspector task database version")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS inspector_tasks (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL UNIQUE,
                    client_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    context_id TEXT NOT NULL,
                    request_body TEXT NOT NULL,
                    data TEXT NOT NULL,
                    lease_token TEXT,
                    lease_until REAL NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    runnable INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(client_id, operation_id)
                );
                CREATE INDEX IF NOT EXISTS due_inspector_tasks ON inspector_tasks(runnable, next_attempt_at, lease_until);
                CREATE INDEX IF NOT EXISTS inspector_context ON inspector_tasks(client_id, context_id, sequence);
                PRAGMA user_version=1;
            """)

    def admit(self, client_id, project_id, operation_id, body, profile, make_state):
        with self.transaction() as connection:
            row = connection.execute("SELECT request_body, data FROM inspector_tasks WHERE client_id=? AND operation_id=?",
                                     (client_id, operation_id)).fetchone()
            if row:
                if row["request_body"] != body:
                    raise APIError(409, "idempotency_conflict", "This operation ID was used with different content")
                return json.loads(row["data"])
            context = str(uuid5(NAMESPACE_URL, canonical_json({"client_id": client_id, "project_id": project_id,
                              "chat_id": json.loads(body)["metadata"]["inspector"]["chat_id"], "profile": profile})))
            rows = connection.execute("SELECT data FROM inspector_tasks WHERE client_id=? AND context_id=? ORDER BY sequence DESC LIMIT 50",
                                      (client_id, context)).fetchall()
            previous = [json.loads(item["data"]) for item in reversed(rows)]
            previous = [item for item in previous if item["status"] == "completed"][-6:]
            state = make_state(str(uuid4()), context, previous)
            connection.execute("INSERT INTO inspector_tasks(task_id,client_id,project_id,operation_id,context_id,request_body,data) VALUES(?,?,?,?,?,?,?)",
                               (state["run_id"], client_id, project_id, operation_id, context, body, canonical_json(state)))
            return state

    def get(self, client_id, project_id, task_id):
        with self.connection() as connection:
            row = connection.execute("SELECT data FROM inspector_tasks WHERE task_id=? AND client_id=? AND project_id=?",
                                     (task_id, client_id, project_id)).fetchone()
        if not row:
            raise APIError(404, "task_not_found", "Task was not found")
        return json.loads(row["data"])

    def claim(self, lease_seconds):
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM inspector_tasks WHERE runnable=1 AND next_attempt_at<=? AND lease_until<=? ORDER BY next_attempt_at,sequence LIMIT 1",
                                     (time.time(), time.time())).fetchone()
            if not row:
                return None
            token = str(uuid4())
            connection.execute("UPDATE inspector_tasks SET lease_token=?,lease_until=? WHERE task_id=?",
                               (token, time.time() + lease_seconds, row["task_id"]))
            return json.loads(row["data"]), token

    def save(self, state, token, lease_seconds, *, release=False, next_attempt_at=0):
        with self.transaction() as connection:
            updated = connection.execute("UPDATE inspector_tasks SET data=?,lease_token=?,lease_until=?,next_attempt_at=?,runnable=? WHERE task_id=? AND lease_token=?",
                         (canonical_json(state), None if release else token, 0 if release else time.time() + lease_seconds,
                          next_attempt_at, int(state["status"] not in ("completed", "failed", "cancelled")), state["run_id"], token))
            if updated.rowcount != 1:
                raise APIError(409, "agent_lease_lost", "This inspector task is no longer owned by this worker", retryable=True)

    def cancel(self, client_id, project_id, task_id):
        with self.transaction() as connection:
            row = connection.execute("SELECT data FROM inspector_tasks WHERE task_id=? AND client_id=? AND project_id=?",
                                     (task_id, client_id, project_id)).fetchone()
            if not row:
                raise APIError(404, "task_not_found", "Task was not found")
            state = json.loads(row["data"])
            if state["status"] == "cancelled":
                return state
            if state["status"] in ("completed", "failed"):
                raise APIError(409, "task_not_cancelable", "A terminal inspector task cannot be cancelled")
            state.update(status="cancelled", error=None, updated_at=utc_now())
            connection.execute("UPDATE inspector_tasks SET data=?,runnable=0,lease_token=NULL,lease_until=0 WHERE task_id=?",
                               (canonical_json(state), task_id))
            return state

    def list(self, client_id, project_id, *, after=0, limit=30, context_id=None):
        with self.connection() as connection:
            return connection.execute("SELECT sequence,data FROM inspector_tasks WHERE client_id=? AND project_id=? AND sequence>? AND (? IS NULL OR context_id=?) ORDER BY sequence LIMIT ?",
                                      (client_id, project_id, after, context_id, context_id, limit + 1)).fetchall()
