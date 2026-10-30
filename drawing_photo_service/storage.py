"""Private task storage. PostgreSQL uses a session lock to fence overlapping pods."""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

# Reserved for this service only; never shares Inspector/Localization locks.
LOCK_ID = 7342109834201


class StorageFull(Exception):
    pass


class SQLiteStore:
    kind = "sqlite"

    def __init__(self, root):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "tasks.sqlite3"
        self.lock = threading.Lock()
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, digest TEXT NOT NULL, request TEXT NOT NULL, response TEXT NOT NULL)")
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, task_id):
        with self.db() as db:
            return db.execute("SELECT * FROM tasks WHERE id=?", (str(task_id),)).fetchone()

    def insert(self, task_id, digest, request, response):
        with self.db() as db:
            db.execute("INSERT INTO tasks VALUES (?,?,?,?)", (str(task_id), digest, request, json.dumps(response)))

    def save(self, task_id, response):
        with self.db() as db:
            db.execute("UPDATE tasks SET response=? WHERE id=?", (json.dumps(response), str(task_id)))


class PostgresLock:
    """Hold a dedicated session connection; writes use it, so a lost owner cannot save."""
    def __init__(self, connect):
        self.connect = connect
        self.local = threading.Lock()
        self.conn = None

    def acquire(self, blocking=False):
        if not self.local.acquire(blocking=blocking):
            return False
        conn = None
        try:
            conn = self.connect()
            if not conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (LOCK_ID,)).fetchone()["acquired"]:
                conn.close()
                self.local.release()
                return False
            self.conn = conn
            return True
        except Exception:
            if conn:
                conn.close()
            self.local.release()
            raise

    def release(self):
        try:
            if self.conn:
                try:
                    self.conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
                finally:
                    self.conn.close()
        finally:
            self.conn = None
            self.local.release()


class PostgresStore:
    kind = "postgres"

    def __init__(self, dsn):
        import psycopg
        from psycopg.rows import dict_row
        u = urlsplit(dsn)
        if u.port == 6543 or parse_qs(u.query).get("sslmode", [None])[0] not in {"require", "verify-ca", "verify-full"}:
            raise ValueError("Use TLS and a direct connection or session pooler, never transaction pooling")
        self.connect = lambda: psycopg.connect(dsn, autocommit=True, connect_timeout=10, row_factory=dict_row,
                                               options="-c statement_timeout=15000")
        self.lock = PostgresLock(self.connect)
        with self.connect() as db:
            db.execute("SELECT id FROM drawing_photo.tasks LIMIT 0")

    def get(self, task_id):
        with self.connect() as db:
            return db.execute("SELECT * FROM drawing_photo.tasks WHERE id=%s", (str(task_id),)).fetchone()

    def insert(self, task_id, digest, request, response):
        used = self.lock.conn.execute("SELECT coalesce(sum(octet_length(request) + greatest(octet_length(response), 2000000)),0) AS size FROM drawing_photo.tasks").fetchone()["size"]
        if used + len(request.encode()) + 2_000_000 > 100_000_000:
            raise StorageFull()
        self.lock.conn.execute("INSERT INTO drawing_photo.tasks (id,digest,request,response) VALUES (%s,%s,%s,%s)",
                               (str(task_id), digest, request, json.dumps(response)))

    def save(self, task_id, response):
        self.lock.conn.execute("UPDATE drawing_photo.tasks SET response=%s WHERE id=%s", (json.dumps(response), str(task_id)))
