import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator
from uuid import uuid4

from .errors import APIError


def canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4):
                raise RuntimeError("Unsupported database schema version")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS inspections (
                    inspection_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    assignment_order INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL UNIQUE,
                    inspection_id TEXT NOT NULL REFERENCES inspections(inspection_id),
                    project_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    storage_ready INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS sessions_by_device
                    ON sessions(project_id, device_id, assignment_order);
                CREATE TABLE IF NOT EXISTS receipts (
                    scope TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    request_body TEXT NOT NULL,
                    response_body TEXT NOT NULL,
                    PRIMARY KEY(scope, operation_id)
                );
                CREATE TABLE IF NOT EXISTS capture_completions (
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    completion_id TEXT NOT NULL,
                    request_body TEXT NOT NULL,
                    data TEXT NOT NULL,
                    inventory TEXT,
                    PRIMARY KEY(session_id, completion_id)
                );
                CREATE TABLE IF NOT EXISTS geometry_jobs (
                    job_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL UNIQUE REFERENCES sessions(session_id),
                    operation_key TEXT NOT NULL UNIQUE,
                    command_body TEXT,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS work_items (
                    work_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    kind TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    lease_until REAL NOT NULL DEFAULT 0,
                    lease_token TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS ready_work ON work_items(status, next_attempt_at, lease_until);
                CREATE TABLE IF NOT EXISTS device_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    project_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_by_device ON device_events(project_id, device_id, sequence);
                CREATE TABLE IF NOT EXISTS device_connections (
                    connection_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    connected_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    closed_at REAL
                );
                CREATE TABLE IF NOT EXISTS upload_requests (
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    request_id TEXT NOT NULL,
                    request_body TEXT NOT NULL,
                    PRIMARY KEY(session_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS upload_objects (
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    object_key TEXT NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY(session_id, object_key)
                );
                CREATE TABLE IF NOT EXISTS geometry_events (
                    source TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    request_body TEXT NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY(source, event_id)
                );
            """)
            connection.execute("BEGIN IMMEDIATE")
            try:
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(sessions)")}
                if "storage_ready" not in columns:
                    connection.execute("ALTER TABLE sessions ADD COLUMN storage_ready INTEGER NOT NULL DEFAULT 1")
                connection.execute("PRAGMA user_version=4")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def replay(connection: sqlite3.Connection, scope: str, operation_id: str, body: dict) -> dict | None:
        row = connection.execute(
            "SELECT request_body, response_body FROM receipts WHERE scope=? AND operation_id=?",
            (scope, operation_id),
        ).fetchone()
        if row is None:
            return None
        if row["request_body"] != canonical_json(body):
            raise APIError(409, "idempotency_conflict", "This operation ID was used with different content")
        return json.loads(row["response_body"])

    @staticmethod
    def save_receipt(connection: sqlite3.Connection, scope: str, operation_id: str, body: dict, response: dict) -> None:
        connection.execute(
            "INSERT INTO receipts VALUES (?, ?, ?, ?)",
            (scope, operation_id, canonical_json(body), canonical_json(response)),
        )


@dataclass
class StagedModel:
    path: Path
    sha256: str
    byte_length: int


class LocalBIMStore:
    """Opaque, immutable BIM intake; normalization and geometry are future adapters."""

    def __init__(self, data_dir: Path, max_bytes: int) -> None:
        self.models_dir = data_dir / "models"
        self.staging_dir = data_dir / "staging"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    @contextmanager
    def stage(self, source: BinaryIO) -> Iterator[StagedModel]:
        path = self.staging_dir / f"{uuid4()}.tmp"
        digest = hashlib.sha256()
        byte_length = 0
        try:
            with path.open("xb") as target:
                while chunk := source.read(1024 * 1024):
                    byte_length += len(chunk)
                    if byte_length > self.max_bytes:
                        raise APIError(413, "bim_too_large", "BIM file exceeds the configured size limit")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if byte_length == 0:
                raise APIError(422, "empty_bim_model", "BIM file cannot be empty")
            yield StagedModel(path, digest.hexdigest(), byte_length)
        finally:
            path.unlink(missing_ok=True)

    def publish(self, model: StagedModel) -> None:
        destination = self.path_for(model.sha256)
        if not destination.exists():
            os.replace(model.path, destination)

    def path_for(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise ValueError("Invalid model content hash")
        return self.models_dir / sha256
