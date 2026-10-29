import hmac
import sqlite3
import asyncio
import threading
import json
import time
from pathlib import Path
import anyio
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException

from .backlog import BacklogWrite, list_items, save_item
from .config import Settings
from .agent import CreateChatRequest, ChatMessageRequest
from .errors import APIError
from .schemas import (
    Acknowledgement, AcknowledgeRequest, AssignmentList, CaptureStartReceipt,
    CaptureStartRequest, CreateSessionRequest, ErrorEnvelope, Inspection,
    InspectionInput, Session, CompleteCaptureRequest, CompletionReceipt, UploadPlanRequest, QuickSessionRequest,
)
from .service import InspectorService, Principal, require_role


class IntakeSizeLimit:
    """Bound the HTTP body before multipart spooling, including chunked uploads."""

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/v1/inspections":
            await self.app(scope, receive, send)
            return
        error = APIError(413, "request_too_large", "Upload request exceeds the configured size limit")
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    content_length = int(value)
                    if content_length < 0:
                        raise ValueError
                except ValueError:
                    response = JSONResponse(APIError(400, "invalid_content_length", "Invalid Content-Length").body, 400)
                    await response(scope, receive, send)
                    return
                if content_length > self.max_bytes:
                    await JSONResponse(error.body, 413)(scope, receive, send)
                    return
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    scope["inspector.upload_too_large"] = True
                    # The multipart parser closes partial uploads for this exception type.
                    raise MultiPartException("Upload request exceeds the configured size limit")
            return message

        await self.app(scope, limited_receive, send)


def validation_error(errors: list[dict]) -> APIError:
    return APIError(
        422, "validation_error", "Request validation failed",
        details={"errors": [
            {"location": list(item["loc"]), "message": item["msg"], "type": item["type"]}
            for item in errors
        ]},
    )


def create_app(settings: Settings | None = None, *, service=None, background_worker=True, static_ui=True, websocket_poll_interval=0.25) -> FastAPI:
    settings = settings or Settings.from_env()
    service = service or InspectorService(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if not background_worker:
            yield
            return
        stop = threading.Event()
        thread = threading.Thread(target=service.capture.run, args=(stop,), name="capture-worker", daemon=True)
        application.state.capture_worker = thread
        thread.start()
        try:
            yield
        finally:
            stop.set()
            await asyncio.to_thread(thread.join, 5)

    errors = {status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 413, 422, 503)}
    app = FastAPI(
        title="AI Inspector — session foundation", version="0.1.0",
        description="Persistent iPhone handoff, exact-version S3 capture completion, and queued Geometry Service dispatch.",
        responses=errors,
        lifespan=lifespan,
    )
    app.state.service = service
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
    app.add_middleware(IntakeSizeLimit, max_bytes=settings.max_bim_bytes + 64 * 1024)
    bearer = HTTPBearer(auto_error=False)

    def authenticate_token(token: str | None) -> Principal:
        if isinstance(token, str):
            supplied = token.encode("utf-8")
            if hmac.compare_digest(supplied, settings.operator_token.encode("ascii")):
                return Principal("operator", settings.project_id)
            if hmac.compare_digest(supplied, settings.device_token.encode("ascii")):
                return Principal("device", settings.project_id, settings.device_id)
        raise APIError(401, "unauthorized", "A valid bearer token is required")

    def authenticate(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Principal:
        return authenticate_token(credentials.credentials if credentials else None)

    def operator(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
        require_role(principal, "operator")
        return principal

    def geometry_identity(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Principal:
        token = settings.geometry_service_token
        if token and credentials is not None and hmac.compare_digest(credentials.credentials.encode("utf-8"), token.encode("ascii")):
            return Principal("geometry", settings.project_id)
        raise APIError(401, "unauthorized", "A valid Geometry Service bearer token is required")

    @app.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path == "/" or request.url.path.startswith("/ui/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' blob:; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            )
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(APIError)
    async def handle_api_error(request: Request, exception: APIError):
        headers = {"WWW-Authenticate": "Bearer"} if exception.status_code == 401 else None
        return JSONResponse(exception.body, exception.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exception: RequestValidationError):
        error = validation_error(exception.errors())
        return JSONResponse(error.body, error.status_code)

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exception: HTTPException):
        if request.scope.get("inspector.upload_too_large"):
            error = APIError(413, "request_too_large", "Upload request exceeds the configured size limit")
            return JSONResponse(error.body, 413)
        codes = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 413: "request_too_large"}
        error = APIError(exception.status_code, codes.get(exception.status_code, "http_error"), str(exception.detail))
        return JSONResponse(error.body, exception.status_code, headers=exception.headers)

    @app.exception_handler(sqlite3.OperationalError)
    async def handle_database_error(request: Request, exception: sqlite3.OperationalError):
        error = APIError(503, "storage_unavailable", "Database is temporarily unavailable", retryable=True)
        return JSONResponse(error.body, 503, headers={"Retry-After": "1"})

    @app.exception_handler(OSError)
    async def handle_storage_error(request: Request, exception: OSError):
        error = APIError(503, "storage_unavailable", "File storage is temporarily unavailable", retryable=True)
        return JSONResponse(error.body, 503, headers={"Retry-After": "1"})

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exception: Exception):
        error = APIError(500, "internal_error", "An unexpected server error occurred")
        return JSONResponse(error.body, 500, headers={"Cache-Control": "private, no-store"})

    @app.get("/health", tags=["operations"])
    def health():
        with service.database.connection() as connection:
            connection.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def operator_ui():
        return FileResponse(Path(__file__).parent / "web" / "index.html")

    if static_ui:
        app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "web"), name="operator-ui")

    @app.get("/v1/operator/backlog", tags=["operations"])
    def backlog_list(principal: Annotated[Principal, Depends(operator)]):
        return list_items(service.database, principal.project_id)

    @app.post("/v1/operator/backlog/{item_id}", tags=["operations"])
    def backlog_save(item_id: UUID, body: BacklogWrite, principal: Annotated[Principal, Depends(operator)]):
        return save_item(service.database, principal.project_id, item_id, body)

    @app.get("/v1/operator/overview", tags=["operations"])
    def operator_overview(
        principal: Annotated[Principal, Depends(operator)],
        before: Annotated[int | None, Query(ge=1, le=9223372036854775807)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ):
        # Include pending storage folders and terminal sessions, unlike device assignments.
        with service.database.connection() as connection:
            rows = connection.execute(
                "SELECT assignment_order, data FROM sessions WHERE project_id=? "
                "AND (CAST(? AS BIGINT) IS NULL OR assignment_order<?) ORDER BY assignment_order DESC LIMIT ?",
                (principal.project_id, before, before, limit + 1),
            ).fetchall()
            inspections = connection.execute(
                "SELECT data FROM inspections WHERE project_id=? ORDER BY rowid DESC LIMIT 100",
                (principal.project_id,),
            ).fetchall()
        return {
            "project_id": principal.project_id, "device_id": settings.device_id,
            "max_bim_bytes": settings.max_bim_bytes,
            "supabase_configured": settings.supabase_enabled,
            "sessions": [json.loads(row["data"]) for row in rows[:limit]],
            "next_before": rows[limit - 1]["assignment_order"] if len(rows) > limit else None,
            "inspections": [json.loads(row["data"]) for row in inspections],
        }

    @app.get("/v1/capabilities", tags=["operations"])
    def capabilities():
        return {"schema_version": "0.1", "workflow": "capture_to_geometry_v1", "device_websocket": True,
                "capture_formats": ["supabase_capture_v1", "supabase_capture_zip_v1", "supabase_geometry_parts_v1", "versioned_s3_v1"],
                "max_archive_bytes": settings.max_archive_bytes,
                "max_unpacked_archive_bytes": settings.max_unpacked_archive_bytes,
                "max_capture_file_bytes": settings.max_capture_file_bytes, "max_manifest_bytes": settings.max_manifest_bytes,
                "geometry_configured": bool(settings.geometry_base_url),
                "geometry_mode": settings.geometry_mode if settings.geometry_base_url else "unconfigured",
                "agent_chat": True, "agent_model_configured": bool(settings.meta_base_url and settings.meta_model and settings.meta_api_key)}

    @app.post("/v1/chats", status_code=201, tags=["agent chat"])
    def create_chat(request: CreateChatRequest, response: Response, principal: Annotated[Principal, Depends(operator)]):
        result, response.status_code = service.agent.create_chat(principal, request)
        return result

    @app.get("/v1/chats/{chat_id}", tags=["agent chat"])
    def get_chat(chat_id: UUID, principal: Annotated[Principal, Depends(operator)],
                 before: Annotated[int | None, Query(ge=1, le=9223372036854775807)] = None,
                 limit: Annotated[int, Query(ge=1, le=100)] = 30):
        return service.agent.get_chat(principal, str(chat_id), before, limit)

    @app.post("/v1/chats/{chat_id}/messages", status_code=201, tags=["agent chat"])
    def chat_message(chat_id: UUID, request: ChatMessageRequest, response: Response,
                     principal: Annotated[Principal, Depends(operator)]):
        result, response.status_code = service.agent.submit(principal, str(chat_id), request)
        return result

    @app.post("/v1/inspections", response_model=Inspection, status_code=201, tags=["inspection intake"])
    async def create_inspection(
        response: Response,
        principal: Annotated[Principal, Depends(operator)],
        schema_version: Annotated[str, Form()],
        request_id: Annotated[UUID, Form()],
        project_id: Annotated[str, Form()],
        room_id: Annotated[str, Form()],
        prompt: Annotated[str, Form(min_length=1, max_length=8000)],
        bim_model: Annotated[UploadFile, File()],
        model_is_synthetic: Annotated[bool, Form()] = False,
    ):
        try:
            metadata = InspectionInput(
                schema_version=schema_version, request_id=request_id,
                project_id=project_id, room_id=room_id, prompt=prompt,
                model_is_synthetic=model_is_synthetic,
            )
        except ValidationError as exception:
            raise validation_error(exception.errors()) from None
        result, response.status_code = await run_in_threadpool(
            service.create_inspection, principal, metadata, bim_model.file,
            bim_model.filename or "bim-model", bim_model.content_type or "application/octet-stream",
        )
        return result

    @app.get("/v1/inspections/{inspection_id}", response_model=Inspection, tags=["inspection intake"])
    def get_inspection(inspection_id: UUID, principal: Annotated[Principal, Depends(operator)]):
        return service.get_inspection(principal, str(inspection_id))

    @app.get("/v1/inspections/{inspection_id}/bim-model", response_class=FileResponse, tags=["inspection intake"])
    def get_bim_model(inspection_id: UUID, principal: Annotated[Principal, Depends(operator)]):
        inspection = service.get_inspection(principal, str(inspection_id))
        if inspection["bim_model"] is None:
            raise APIError(404, "bim_model_not_uploaded", "Capture-only session has no reference BIM upload")
        path = service.bim_store.path_for(inspection["bim_model"]["sha256"])
        if not path.is_file():
            raise APIError(503, "model_unavailable", "Stored BIM model is temporarily unavailable", retryable=True)
        return FileResponse(path, filename=inspection["bim_model"]["filename"], media_type="application/octet-stream")

    @app.post(
        "/v1/inspections/{inspection_id}/capture-sessions", response_model=Session,
        status_code=201, tags=["capture sessions"],
    )
    def create_session(
        inspection_id: UUID, body: CreateSessionRequest, response: Response,
        principal: Annotated[Principal, Depends(operator)],
    ):
        result, response.status_code = service.create_session(principal, str(inspection_id), body)
        return result

    @app.post("/v1/capture-sessions", response_model=Session, status_code=201, tags=["capture sessions"])
    def quick_session(body: QuickSessionRequest, response: Response, principal: Annotated[Principal, Depends(operator)]):
        result, response.status_code = service.quick_session(principal, body)
        return result

    @app.get("/v1/devices/{device_id}/capture-assignments", response_model=AssignmentList, tags=["iPhone handoff"])
    def get_assignments(
        device_id: str, principal: Annotated[Principal, Depends(authenticate)],
        after: Annotated[str | None, Query(max_length=2048)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ):
        return service.list_assignments(principal, device_id, after, limit)

    @app.get("/v1/sessions/{session_id}", response_model=Session, tags=["capture sessions"])
    def get_session(session_id: UUID, principal: Annotated[Principal, Depends(authenticate)]):
        return service.get_session(principal, str(session_id))

    @app.post("/v1/sessions/{session_id}/localization", tags=["iPhone handoff"])
    def prepare_localization(session_id: UUID, principal: Annotated[Principal, Depends(authenticate)]):
        from .localization import prepare_localization
        return prepare_localization(service, principal, str(session_id))

    @app.post("/v1/sessions/{session_id}/abort", response_model=Session, tags=["capture sessions"])
    def abort_session(session_id: UUID, principal: Annotated[Principal, Depends(operator)]):
        return service.abort_session(principal, str(session_id))

    @app.post("/v1/sessions/{session_id}/upload-plans", status_code=201, tags=["capture completion"])
    def upload_plan(session_id: UUID, body: UploadPlanRequest, response: Response, principal: Annotated[Principal, Depends(authenticate)]):
        from .capture_uploads import CaptureUploads
        result, response.status_code = CaptureUploads(service).plan(principal, str(session_id), body)
        return result

    @app.post(
        "/v1/sessions/{session_id}/acknowledgements", response_model=Acknowledgement,
        status_code=201, tags=["iPhone handoff"],
    )
    def acknowledge(
        session_id: UUID, body: AcknowledgeRequest, response: Response,
        principal: Annotated[Principal, Depends(authenticate)],
    ):
        result, response.status_code = service.acknowledge(principal, str(session_id), body)
        return result

    @app.post(
        "/v1/sessions/{session_id}/capture-start", response_model=CaptureStartReceipt,
        status_code=201, tags=["iPhone handoff"],
    )
    def start_capture(
        session_id: UUID, body: CaptureStartRequest, response: Response,
        principal: Annotated[Principal, Depends(authenticate)],
    ):
        result, response.status_code = service.start_capture(principal, str(session_id), body)
        return result

    @app.post(
        "/v1/sessions/{session_id}/complete", response_model=CompletionReceipt,
        status_code=202, responses={200: {"model": CompletionReceipt}}, tags=["capture completion"],
    )
    def complete_capture(
        session_id: UUID, body: CompleteCaptureRequest, response: Response,
        principal: Annotated[Principal, Depends(authenticate)],
    ):
        result, response.status_code = service.capture.complete(principal, str(session_id), body)
        return result

    @app.post("/v1/sessions/{session_id}/geometry-jobs/dispatch", status_code=202, tags=["geometry handoff"])
    def request_geometry_dispatch(
        session_id: UUID, response: Response, principal: Annotated[Principal, Depends(operator)],
    ):
        result, response.status_code = service.capture.request_dispatch(principal, str(session_id))
        return result

    @app.get("/internal/v1/sessions/{session_id}/capture-source", tags=["geometry handoff"])
    def get_capture_source(session_id: UUID, principal: Annotated[Principal, Depends(geometry_identity)]):
        return service.capture.source_access(principal, str(session_id))

    @app.get("/internal/v1/sessions/{session_id}/capture-source/files", tags=["geometry handoff"])
    def get_capture_source_files(
        session_id: UUID, principal: Annotated[Principal, Depends(geometry_identity)],
        after: Annotated[str | None, Query(pattern=r"^[0-9]{1,12}$")] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ):
        return service.capture.source_access(principal, str(session_id), after=int(after or "0"), limit=limit, files_only=True)

    @app.post("/internal/v1/geometry-events", status_code=202, tags=["geometry handoff"])
    def geometry_event(body: dict, response: Response, principal: Annotated[Principal, Depends(geometry_identity)]):
        result, response.status_code = service.geometry_results.receive(principal, body)
        return result

    @app.get("/v1/devices/{device_id}/status", tags=["iPhone handoff"])
    def device_status(device_id: str, principal: Annotated[Principal, Depends(operator)]):
        return service.device_events.status(principal, device_id)

    @app.get("/v1/devices/{device_id}/events", tags=["iPhone handoff"])
    def device_events(device_id: str, principal: Annotated[Principal, Depends(authenticate)],
                      after: Annotated[str | None, Query(max_length=2048)] = None,
                      limit: Annotated[int, Query(ge=1, le=100)] = 100):
        return service.device_events.pull(principal, device_id, after, limit)

    @app.websocket("/v1/devices/{device_id}/events/ws")
    async def device_websocket(websocket: WebSocket, device_id: str):
        await websocket.accept()
        connection_id = None
        try:
            if "token" in websocket.query_params:
                raise APIError(401, "unauthorized", "Send credentials in Authorization or the first authentication message")
            header = websocket.headers.get("authorization", "")
            after = websocket.query_params.get("after")
            if header:
                scheme, _, token = header.partition(" ")
                principal = authenticate_token(token if scheme.lower() == "bearer" else None)
            else:
                raw = await asyncio.wait_for(websocket.receive_text(), 10)
                if len(raw) > 4096:
                    raise APIError(401, "unauthorized", "Invalid authentication message")
                initial = json.loads(raw)
                if not isinstance(initial, dict) or initial.get("type") != "authenticate":
                    raise APIError(401, "unauthorized", "First message must authenticate")
                principal = authenticate_token(initial.get("token"))
                after = initial.get("after", after)
            connection_id = await run_in_threadpool(service.device_events.connected, principal, device_id)
            await websocket.send_json({"type": "device.connected", "device_id": device_id, "schema_version": "0.1", "heartbeat_interval_s": 15})
            if after is None:
                snapshot = await run_in_threadpool(service.device_events.snapshot, principal, device_id)
                await websocket.send_json(snapshot)
                after = snapshot["resume_cursor"]
            last_ping, last_seen = time.monotonic(), time.monotonic()
            while True:
                page = await run_in_threadpool(service.device_events.pull, principal, device_id, after)
                if page["events"]:
                    await asyncio.wait_for(websocket.send_json({"type": "events", **page}), 10)
                    after = page["next_cursor"]
                    if page["has_more"]:
                        continue
                if time.monotonic() - last_ping >= 15:
                    await websocket.send_json({"type": "ping"})
                    last_ping = time.monotonic()
                if time.monotonic() - last_seen > 45:
                    await websocket.close(code=4408, reason="Heartbeat timeout")
                    break
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), websocket_poll_interval)
                except asyncio.TimeoutError:
                    continue
                if len(raw) > 4096:
                    raise APIError(422, "invalid_message", "Device channel accepts small JSON control messages")
                message = json.loads(raw)
                if not isinstance(message, dict) or message.get("type") not in ("ping", "pong"):
                    raise APIError(422, "invalid_message", "Use HTTP endpoints for session acknowledgements and uploads")
                last_seen = time.monotonic()
                await run_in_threadpool(service.device_events.heartbeat, connection_id)
                if message["type"] == "ping":
                    await websocket.send_json({"type": "pong"})
        except APIError as error:
            await websocket.send_json({"type": "stream.error", "error": error.body})
            await websocket.close(code=4401 if error.status_code in (401, 403) else 4400)
        except (ValueError, TypeError, asyncio.TimeoutError):
            await websocket.close(code=4401, reason="Invalid authentication or control message")
        except WebSocketDisconnect:
            pass
        finally:
            if connection_id:
                with anyio.CancelScope(shield=True):
                    await run_in_threadpool(service.device_events.disconnected, connection_id)

    return app
