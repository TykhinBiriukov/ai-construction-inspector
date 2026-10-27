"""Optional local transport demo; its PLY output is synthetic, never reconstruction."""

import argparse
import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import IDENTIFIER_PATTERN, Settings
from .errors import APIError
from .service import utc_now
from .storage import canonical_json


logger = logging.getLogger(__name__)
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_FILENAMES = ("frame.json", "rgb.jpg", "depth.f32", "confidence.u8")
_PLY = (
    b"ply\nformat ascii 1.0\ncomment SYNTHETIC MOCK: not reconstructed from capture data\n"
    b"element vertex 3\nproperty float x\nproperty float y\nproperty float z\nend_header\n"
    b"0 0 0\n1 0 0\n0 1 0\n"
)


def _source_invalid() -> APIError:
    return APIError(422, "mock_source_invalid", "Mock Geometry could not verify the accepted source metadata")


def _transport_unavailable(status=None) -> APIError:
    return APIError(
        503, "mock_source_unavailable", "Mock Geometry source access will be retried", retryable=True,
        details={"status_code": status} if status is not None else None,
    )


def _uuid(value) -> bool:
    try:
        return isinstance(value, str) and bool(UUID(value))
    except ValueError:
        return False


def _positive(value) -> bool:
    return type(value) is int and value > 0


def _signed_download(value) -> bool:
    if not isinstance(value, str) or len(value) > 16384 or any(ord(character) < 32 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        parsed.port
        if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
            return False
        if parsed.scheme == "https":
            return True
        if parsed.scheme != "http":
            return False
        return parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


class MockGeometry:
    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.http_client = http_client
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = settings.data_dir / "mock-geometry.sqlite3"
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS mock_geometry_jobs (
                    job_id TEXT PRIMARY KEY,
                    operation_key TEXT NOT NULL UNIQUE,
                    command_body TEXT NOT NULL,
                    data TEXT NOT NULL,
                    artifact BLOB,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    lease_until REAL NOT NULL DEFAULT 0,
                    lease_token TEXT,
                    callback_status TEXT NOT NULL DEFAULT 'none',
                    callback_next_attempt_at REAL NOT NULL DEFAULT 0,
                    callback_attempts INTEGER NOT NULL DEFAULT 0,
                    callback_error TEXT
                )
            """)

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _validate_command(self, job_id: str, command: dict) -> None:
        invalid = APIError(422, "invalid_geometry_command", "Mock Geometry requires a valid versioned job command")
        if not isinstance(command, dict) or command.get("schema_version") != "0.1" or command.get("job_id") != job_id:
            raise invalid
        for field in ("job_id", "session_id", "inspection_id"):
            if not _uuid(command.get(field)):
                raise invalid
        for field in ("project_id", "room_id"):
            if not isinstance(command.get(field), str) or not re.fullmatch(IDENTIFIER_PATTERN, command[field]):
                raise invalid
        if command["project_id"] != self.settings.project_id:
            raise APIError(403, "forbidden", "Mock Geometry job belongs to another project")
        for field in ("model_version", "processing_profile_version", "operation_key"):
            if not isinstance(command.get(field), str) or not command[field].strip() or len(command[field]) > 256:
                raise invalid
        if not self.settings.inspector_public_base_url:
            raise APIError(503, "mock_inspector_not_configured", "Set the Inspector URL for the mock transport demo", retryable=True)
        source = command.get("source_ref")
        expected = f"{self.settings.inspector_public_base_url.rstrip('/')}/internal/v1/sessions/{command['session_id']}/capture-source"
        if not isinstance(source, dict) or source.get("url") != expected:
            raise APIError(422, "invalid_source_url", "Mock Geometry only reads the configured Inspector session source route")
        if not isinstance(source.get("manifest_sha256"), str) or not _SHA.fullmatch(source["manifest_sha256"]):
            raise invalid
        for field in ("manifest_version_id", "snapshot_id"):
            if field in source and (not isinstance(source[field], str) or not source[field].strip() or source[field] == "null"):
                raise invalid
        try:
            json.dumps(command, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            raise invalid from None

    def submit(self, job_id: str, command: dict) -> tuple[dict, int]:
        self._validate_command(job_id, command)
        body = canonical_json(command)
        with self._transaction() as connection:
            previous = connection.execute("SELECT * FROM mock_geometry_jobs WHERE job_id=?", (job_id,)).fetchone()
            if previous is not None:
                if previous["command_body"] != body:
                    raise APIError(409, "idempotency_conflict", "Mock job ID was used with different inputs")
                return json.loads(previous["data"]), 200
            duplicate = connection.execute(
                "SELECT 1 FROM mock_geometry_jobs WHERE operation_key=?", (command["operation_key"],),
            ).fetchone()
            if duplicate:
                raise APIError(409, "idempotency_conflict", "Mock operation key already belongs to another job")
            job = {field: command[field] for field in (
                "job_id", "session_id", "inspection_id", "project_id", "room_id", "model_version", "processing_profile_version", "operation_key",
            )}
            job.update(
                schema_version="0.1", source_ref=command["source_ref"],
                manifest_sha256=command["source_ref"]["manifest_sha256"], status="queued", revision=1,
                geometry_revision=None, analysis_origin="mock", error=None, created_at=utc_now(),
            )
            connection.execute(
                "INSERT INTO mock_geometry_jobs(job_id,operation_key,command_body,data,status) VALUES (?, ?, ?, ?, 'queued')",
                (job_id, command["operation_key"], body, canonical_json(job)),
            )
            return job, 201

    def get(self, job_id: str) -> dict:
        with self._connection() as connection:
            row = connection.execute("SELECT data FROM mock_geometry_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise APIError(404, "not_found", "Mock Geometry job was not found")
        return json.loads(row["data"])

    def artifact(self, job_id: str) -> bytes:
        with self._connection() as connection:
            row = connection.execute("SELECT status,artifact FROM mock_geometry_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None or row["status"] != "ready" or row["artifact"] is None:
            raise APIError(404, "not_found", "Synthetic mock artifact is not ready")
        return bytes(row["artifact"])

    def _claim(self) -> dict | None:
        now = time.time()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM mock_geometry_jobs WHERE status IN ('queued','fetching') "
                "AND next_attempt_at<=? AND lease_until<=? ORDER BY rowid LIMIT 1", (now, now),
            ).fetchone()
            if row is None:
                return None
            work = dict(row)
            job = json.loads(work["data"])
            if job["status"] != "fetching":
                job.update(status="fetching", revision=job["revision"] + 1)
            work["lease_token"] = str(uuid4())
            work["attempts"] += 1
            connection.execute(
                "UPDATE mock_geometry_jobs SET status='fetching', data=?, attempts=?, lease_until=?, lease_token=? WHERE job_id=?",
                (canonical_json(job), work["attempts"], now + 120, work["lease_token"], work["job_id"]),
            )
            return work

    def _http(self, client: httpx.Client, method: str, url: str, *, inspector=False, body=None) -> httpx.Response:
        headers = {}
        if inspector:
            if not self.settings.geometry_service_token:
                raise _transport_unavailable()
            headers["Authorization"] = f"Bearer {self.settings.geometry_service_token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        # A standalone Request plus auth=None excludes the client's default
        # Authorization headers/auth when downloading a signed storage URL.
        request = httpx.Request(method, url, headers=headers, content=canonical_json(body).encode() if body is not None else None)
        request.extensions["timeout"] = httpx.Timeout(self.settings.geometry_timeout_s).as_dict()
        try:
            response = client.send(request, auth=None, follow_redirects=False)
        except httpx.TransportError:
            raise _transport_unavailable() from None
        if response.status_code not in ((200, 202) if method == "POST" else (200,)):
            raise _transport_unavailable(response.status_code)
        return response

    def _json(self, response: httpx.Response) -> dict:
        if len(response.content) > self.settings.max_manifest_bytes:
            raise _source_invalid()
        try:
            value = response.json()
        except (ValueError, TypeError, RecursionError):
            raise _source_invalid() from None
        if not isinstance(value, dict):
            raise _source_invalid()
        return value

    def _source_inventory(self, command: dict, client: httpx.Client) -> list[str]:
        source_ref = command["source_ref"]
        configured_source = f"{self.settings.inspector_public_base_url.rstrip('/')}/internal/v1/sessions/{command['session_id']}/capture-source"
        if source_ref["url"] != configured_source:
            raise APIError(503, "mock_inspector_configuration_changed", "Restore the Inspector URL assigned to this mock job", retryable=True)
        access = self._json(self._http(client, "GET", source_ref["url"], inspector=True))
        if (
            access.get("session_id") != command["session_id"] or access.get("job_id") != command["job_id"]
            or access.get("manifest_sha256") != source_ref["manifest_sha256"]
            or not _positive(access.get("manifest_byte_length"))
            or access["manifest_byte_length"] > self.settings.max_manifest_bytes
            or not _signed_download(access.get("manifest_url"))
        ):
            raise _source_invalid()
        for field in ("manifest_version_id", "snapshot_id"):
            if field in source_ref and access.get(field) != source_ref[field]:
                raise _source_invalid()
        expected_files_url = source_ref["url"] + "/files"
        if access.get("files_url") != expected_files_url:
            raise _source_invalid()
        manifest_response = self._http(client, "GET", access["manifest_url"])
        if len(manifest_response.content) != access["manifest_byte_length"] or hashlib.sha256(manifest_response.content).hexdigest() != source_ref["manifest_sha256"]:
            raise _source_invalid()
        manifest = self._json(manifest_response)
        if manifest.get("schema_version") != "0.1" or manifest.get("format") not in ("capture_manifest_v1", "capture_manifest_supabase_v1"):
            raise _source_invalid()
        for field in ("session_id", "inspection_id", "project_id", "room_id", "model_version"):
            if manifest.get(field) != command[field]:
                raise _source_invalid()
        observations = manifest.get("observations")
        if not isinstance(observations, list) or not 1 <= len(observations) <= self.settings.max_capture_files:
            raise _source_invalid()
        if type(manifest.get("observation_count")) is not int or manifest["observation_count"] != len(observations):
            raise _source_invalid()
        evidence = []
        evidence_ids = set()
        expected_files = {}
        prefix = f"projects/{command['project_id']}/sessions/{command['session_id']}/observations/"
        for observation in observations:
            if not isinstance(observation, dict) or not _uuid(observation.get("observation_id")):
                raise _source_invalid()
            observation_id = observation["observation_id"]
            if observation_id in evidence_ids:
                raise _source_invalid()
            evidence.append(observation_id)
            evidence_ids.add(observation_id)
            files = observation.get("files")
            if not isinstance(files, list) or not 2 <= len(files) <= 4:
                raise _source_invalid()
            names = []
            for item in files:
                if not isinstance(item, dict) or item.get("name") not in _FILENAMES:
                    raise _source_invalid()
                name = item["name"]
                key = item.get("object_key")
                if name in names or key != f"{prefix}{observation_id}/{name}":
                    raise _source_invalid()
                digest = item.get("sha256")
                if not _positive(item.get("byte_length")) or not isinstance(digest, str) or not _SHA.fullmatch(digest):
                    raise _source_invalid()
                names.append(name)
                expected_files[(observation_id, name)] = item
            if not {"frame.json", "rgb.jpg"}.issubset(names) or len(expected_files) > self.settings.max_capture_files:
                raise _source_invalid()
        seen_files = set()
        seen_cursors = set()
        cursor = None
        while True:
            url = expected_files_url
            if cursor is not None:
                url += "?" + str(httpx.QueryParams({"after": cursor, "limit": 100}))
            page = self._json(self._http(client, "GET", url, inspector=True))
            items = page.get("items")
            if not isinstance(items, list) or len(items) > 100:
                raise _source_invalid()
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("observation_id"), str) or not isinstance(item.get("name"), str):
                    raise _source_invalid()
                identity = item["observation_id"], item["name"]
                if identity not in expected_files or identity in seen_files:
                    raise _source_invalid()
                expected = expected_files[identity]
                if not _positive(item.get("byte_length")) or any(item.get(field) != expected[field] for field in ("object_key", "byte_length", "sha256")):
                    raise _source_invalid()
                if expected.get("version_id") is not None and item.get("version_id") != expected["version_id"]:
                    raise _source_invalid()
                if not _signed_download(item.get("download_url")):
                    raise _source_invalid()
                seen_files.add(identity)
            cursor = page.get("next_cursor")
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor or len(cursor) > 2048 or cursor in seen_cursors or not items:
                raise _source_invalid()
            seen_cursors.add(cursor)
        if seen_files != set(expected_files):
            raise _source_invalid()
        return evidence

    def _ready(self, work: dict, evidence: list[str]) -> None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT data FROM mock_geometry_jobs WHERE job_id=? AND lease_token=? AND status='fetching'",
                (work["job_id"], work["lease_token"]),
            ).fetchone()
            if row is None:
                return
            job = json.loads(row["data"])
            origin = (self.settings.geometry_base_url or "http://127.0.0.1:8001").rstrip("/")
            job.update(
                status="ready", revision=job["revision"] + 1, geometry_revision=1,
                evidence_observation_ids=evidence, excluded_observations=[], analysis_origin="mock",
                artifacts=[{
                    "artifact_id": f"mock-{work['job_id']}-point-cloud", "kind": "point_cloud",
                    "url": f"{origin}/internal/v1/artifacts/{work['job_id']}.ply",
                    "media_type": "application/octet-stream", "byte_length": len(_PLY),
                    "sha256": hashlib.sha256(_PLY).hexdigest(), "is_synthetic": True,
                }], error=None, completed_at=utc_now(),
            )
            connection.execute(
                "UPDATE mock_geometry_jobs SET status='ready',data=?,artifact=?,lease_until=0,lease_token=NULL,callback_status='pending',callback_next_attempt_at=0 WHERE job_id=?",
                (canonical_json(job), _PLY, work["job_id"]),
            )

    def _record_error(self, work: dict, error: APIError) -> None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT data FROM mock_geometry_jobs WHERE job_id=? AND lease_token=? AND status='fetching'",
                (work["job_id"], work["lease_token"]),
            ).fetchone()
            if row is None:
                return
            if error.body["retryable"]:
                connection.execute(
                    "UPDATE mock_geometry_jobs SET lease_until=0,lease_token=NULL,next_attempt_at=? WHERE job_id=?",
                    (time.time() + min(30, 2 ** min(work["attempts"], 5)), work["job_id"]),
                )
            else:
                job = json.loads(row["data"])
                job.update(status="failed", revision=job["revision"] + 1, geometry_revision=None, error=error.body)
                connection.execute(
                    "UPDATE mock_geometry_jobs SET status='failed',data=?,lease_until=0,lease_token=NULL,callback_status='pending',callback_next_attempt_at=0 WHERE job_id=?",
                    (canonical_json(job), work["job_id"]),
                )

    def _callback(self, client: httpx.Client) -> bool:
        now = time.time()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM mock_geometry_jobs WHERE callback_status='pending' AND callback_next_attempt_at<=? ORDER BY rowid LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "UPDATE mock_geometry_jobs SET callback_next_attempt_at=?,callback_attempts=callback_attempts+1 WHERE job_id=?",
                (now + 120, row["job_id"]),
            )
            job = json.loads(row["data"])
        event = {
            "schema_version": "0.1", "event_id": f"mock-{job['job_id']}-r{job['revision']}",
            "type": "geometry.ready" if job["status"] == "ready" else "geometry.failed",
            "project_id": job["project_id"], "session_id": job["session_id"], "job_id": job["job_id"],
            "job_revision": job["revision"], "manifest_sha256": job["manifest_sha256"],
            "geometry_revision": job["geometry_revision"], "error": job["error"],
            "created_at": job.get("completed_at", job["created_at"]),
        }
        try:
            self._http(
                client, "POST", self.settings.inspector_public_base_url.rstrip("/") + "/internal/v1/geometry-events",
                inspector=True, body=event,
            )
        except APIError as error:
            with self._transaction() as connection:
                connection.execute(
                    "UPDATE mock_geometry_jobs SET callback_next_attempt_at=?,callback_error=? WHERE job_id=?",
                    (time.time() + min(30, 2 ** min(row["callback_attempts"] + 1, 5)), canonical_json(error.body), row["job_id"]),
                )
        else:
            with self._transaction() as connection:
                connection.execute(
                    "UPDATE mock_geometry_jobs SET callback_status='done',callback_error=NULL WHERE job_id=?", (row["job_id"],),
                )
        return True

    def process_next(self, client: httpx.Client | None = None) -> bool:
        client = client or self.http_client
        owns_client = client is None
        client = client or httpx.Client(timeout=self.settings.geometry_timeout_s, follow_redirects=False)
        try:
            work = self._claim()
            if work is None:
                return self._callback(client)
            try:
                evidence = self._source_inventory(json.loads(work["command_body"]), client)
                self._ready(work, evidence)
            except APIError as error:
                self._record_error(work, error)
            except Exception:
                self._record_error(work, _transport_unavailable())
            self._callback(client)
            return True
        finally:
            if owns_client:
                client.close()

    def run(self, stop: threading.Event) -> None:
        with httpx.Client(timeout=self.settings.geometry_timeout_s, follow_redirects=False) as client:
            while not stop.is_set():
                try:
                    processed = self.process_next(self.http_client or client)
                except Exception:
                    logger.error("Mock Geometry queue will be retried")
                    processed = False
                if not processed:
                    stop.wait(self.settings.worker_interval_s)


def create_mock_app(settings: Settings | None = None, http_client: httpx.Client | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if not settings.geometry_api_token or not settings.geometry_service_token:
        raise ValueError("Mock Geometry requires the configured incoming and outgoing service tokens")
    service = MockGeometry(settings, http_client)

    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()
        worker = threading.Thread(target=service.run, args=(stop,), name="mock-geometry-worker", daemon=True)
        worker.start()
        try:
            yield
        finally:
            stop.set()
            await asyncio.to_thread(worker.join, 5)

    app = FastAPI(
        title="Synthetic Mock Geometry — transport demo only", version="0.1.0", lifespan=lifespan,
        description="Generates a fixed synthetic PLY. It performs no reconstruction and makes no inspection finding.",
    )
    app.state.mock_geometry = service
    bearer = HTTPBearer(auto_error=False)

    def authenticate(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is None or not hmac.compare_digest(
            credentials.credentials.encode(), settings.geometry_api_token.encode("ascii"),
        ):
            raise APIError(401, "unauthorized", "A valid Geometry Service token is required")

    @app.middleware("http")
    async def mock_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Geometry-Origin"] = "mock"
        return response

    @app.exception_handler(APIError)
    async def api_error(request: Request, error: APIError):
        return JSONResponse(error.body, error.status_code, headers={"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return JSONResponse(APIError(422, "invalid_geometry_command", "Mock Geometry request validation failed").body, 422)

    @app.get("/health")
    def health():
        return {"status": "ok", "analysis_origin": "mock", "is_synthetic": True}

    @app.put("/internal/v1/jobs/{job_id}", dependencies=[Depends(authenticate)])
    def submit(job_id: UUID, command: dict):
        result, status = service.submit(str(job_id), command)
        return JSONResponse(result, status)

    @app.get("/internal/v1/jobs/{job_id}", dependencies=[Depends(authenticate)])
    def get_job(job_id: UUID):
        return service.get(str(job_id))

    @app.get("/internal/v1/artifacts/{job_id}.ply", dependencies=[Depends(authenticate)])
    def artifact(job_id: UUID):
        return Response(
            service.artifact(str(job_id)), media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="synthetic-mock-{job_id}.ply"'},
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    settings = Settings.from_env()
    if not settings.geometry_base_url:
        settings = replace(settings, geometry_base_url=f"http://127.0.0.1:{args.port}")
    import uvicorn

    uvicorn.run(create_mock_app(settings), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
