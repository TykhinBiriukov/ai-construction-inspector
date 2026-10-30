"""Deployable inspector service: A2A HTTP+JSON admission, task store and worker."""

import asyncio
import hmac
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid5

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, SecretStr, field_validator
from starlette.exceptions import HTTPException

from .a2a_protocol import EXTENSION, MAX_BODY, VERSION, SendRequest, ServiceHTTP, bearer_value, decode_json, digest, service_url
from .agent import NoArguments, TOOLS
from .agent_runtime import AgentHarness
from .agent_task_store import AgentTaskStore
from .config import Settings
from .errors import APIError
from .inspectors import INSPECTOR_PROMPT, InspectorProfile
from .model_client import MetaModelClient
from .schemas import APIModel, Identifier
from .service import utc_now
from .storage import canonical_json


class AgentCaller(APIModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    client_id: Identifier
    project_id: Identifier
    token: SecretStr

    @field_validator("token")
    @classmethod
    def valid_token(cls, value):
        bearer_value(value.get_secret_value())
        return value


class BackendToolClient(ServiceHTTP):
    def execute(self, inspector_id, binding, name, operation_id):
        if name not in ("session_get", "device_status"):
            raise APIError(502, "inspector_tool_forbidden", "The inspector selected an unavailable tool")
        body = {key: binding[key] for key in ("project_id", "chat_id", "parent_run_id", "delegation_run_id", "operation_id")}
        body["tool_operation_id"] = operation_id
        return self.request("POST", f"/v1/inspector-tools/{inspector_id}/{name}", body=canonical_json(body)), None


class InspectorAgentService:
    def __init__(self, settings, *, profile, database_path, public_url, callers, model_client=None, tool_client=None):
        self.profile = InspectorProfile.model_validate(profile.model_dump(warnings=False))
        self.settings = settings
        self.public_url = service_url(public_url)
        self.callers = tuple(AgentCaller.model_validate(item.model_dump()) for item in callers)
        tokens = [item.token.get_secret_value() for item in self.callers]
        if not tokens or len(set(tokens)) != len(tokens) or len({item.client_id for item in self.callers}) != len(self.callers):
            raise ValueError("Inspector callers need unique client IDs and credentials")
        private = {settings.operator_token, settings.device_token, settings.meta_api_key}
        if any(token in private for token in tokens):
            raise ValueError("Inspector credentials must differ from operator, device and model credentials")
        if profile.allowed_tools and tool_client is None:
            raise ValueError("An inspector with backend tools requires a configured tool client")
        if tool_client and tool_client.token in private | set(tokens):
            raise ValueError("Backend tools require a separate service credential")
        self.store = AgentTaskStore(database_path)
        self.model = model_client if model_client is not None else MetaModelClient(settings)
        self.tool_client = tool_client

    def authenticate(self, authorization):
        if isinstance(authorization, str) and authorization.startswith("Bearer "):
            supplied = authorization[7:].encode("utf-8")
            for caller in self.callers:
                if hmac.compare_digest(supplied, caller.token.get_secret_value().encode("ascii")):
                    return caller
        raise APIError(401, "unauthorized", "A valid inspector service credential is required")

    def card(self):
        profile = self.profile
        return {"name": profile.inspector_id, "description": profile.description, "version": profile.version,
                "supportedInterfaces": [{"url": self.public_url, "protocolBinding": "HTTP+JSON", "protocolVersion": VERSION}],
                "capabilities": {"streaming": False, "pushNotifications": False, "extendedAgentCard": False,
                                 "extensions": [{"uri": EXTENSION, "required": True,
                                                 "description": "Versioned inspection context, durable operation IDs and nonblocking polling"}]},
                "securitySchemes": {"serviceBearer": {"httpAuthSecurityScheme": {"scheme": "bearer"}}},
                "securityRequirements": [{"schemes": {"serviceBearer": {"list": []}}}],
                "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"],
                "skills": [{"id": profile.inspector_id, "name": profile.inspector_id,
                            "description": profile.description, "tags": ["construction", "inspection"]}]}

    def submit(self, caller, raw):
        try:
            request = SendRequest.model_validate(raw)
        except (ValueError, TypeError):
            raise APIError(400, "invalid_argument", "Invalid nonblocking inspection delegation request") from None
        data = request.metadata.inspector.model_dump(mode="json")
        if (data["project_id"] != caller.project_id or data["context"].get("project_id") != caller.project_id):
            raise APIError(403, "forbidden", "The task is outside this caller's project")
        if str(request.message.messageId) != data["operation_id"] or request.message.taskId:
            raise APIError(400, "invalid_argument", "A new task requires its stable operation ID as messageId")
        body = canonical_json(raw)
        task_text = request.message.parts[0].text
        if not task_text.strip():
            raise APIError(400, "invalid_argument", "The delegated task cannot be blank")

        def make_state(task_id, context_id, previous):
            if data["inspector_id"] != self.profile.inspector_id or data["inspector_version"] != self.profile.version:
                raise APIError(409, "inspector_version_mismatch", "The requested inspector profile is not served here")
            if request.message.contextId and str(request.message.contextId) != context_id:
                raise APIError(400, "invalid_argument", "The context does not match this inspection")
            history = []
            for prior in previous:
                history.extend([{"role": "user", "content": prior["task"]}, {"role": "assistant", "content": prior["reply"]}])
            now = utc_now()
            return {"run_id": task_id, "context_id": context_id, "task": task_text, "binding": data,
                    "request_sha256": digest(raw), "wire_message": raw["message"], "profile": self.profile.model_dump(mode="json"),
                    "status": "pending", "reply": None, "error": None, "created_at": now, "updated_at": now,
                    "messages": [{"role": "developer", "content": INSPECTOR_PROMPT + "\nSpecialty instructions:\n"
                                  + self.profile.instructions + "\nServer context: " + canonical_json(data["context"])},
                                 {"role": "user", "content": data["reference"]}, {"role": "user", "content": task_text}],
                    "history": history, "history_run_ids": [item["run_id"] for item in previous],
                    "steps": 0, "pending_model": None, "pending_tool": None, "tool_results": [], "retry_failures": 0}
        return self.store.admit(caller.client_id, caller.project_id, data["operation_id"], body,
                                self.profile.model_dump(mode="json"), make_state)

    @staticmethod
    def public_task(state, history_length=0):
        wire_state = {"pending": "TASK_STATE_SUBMITTED", "running": "TASK_STATE_WORKING", "retryable": "TASK_STATE_WORKING",
                      "completed": "TASK_STATE_COMPLETED", "failed": "TASK_STATE_FAILED", "cancelled": "TASK_STATE_CANCELED"}[state["status"]]
        identity = {key: state["binding"][key] for key in ("operation_id", "project_id", "chat_id", "parent_run_id",
                    "delegation_run_id", "inspector_id", "inspector_version")}
        identity["request_sha256"] = state["request_sha256"]
        result = {"id": state["run_id"], "contextId": state["context_id"],
                  "status": {"state": wire_state, "timestamp": state["updated_at"]}, "metadata": {"inspector": identity}}
        if state["status"] == "completed":
            result["artifacts"] = [{"artifactId": str(uuid5(UUID(state["run_id"]), "report")), "name": "Inspection report",
                                    "parts": [{"text": state["reply"]}]}]
        if history_length is None or history_length > 0:
            history = [state["wire_message"]]
            if state["status"] == "completed":
                history.append({"messageId": str(uuid5(UUID(state["run_id"]), "reply")), "role": "ROLE_AGENT",
                                "taskId": state["run_id"], "contextId": state["context_id"], "parts": [{"text": state["reply"]}]})
            result["history"] = history if history_length is None else history[-history_length:]
        return result

    def process_next(self):
        lease_seconds = self.settings.meta_timeout_s + 60
        claimed = self.store.claim(lease_seconds)
        if claimed is None:
            return False
        state, token = claimed
        next_attempt_at = 0
        try:
            if self.profile != InspectorProfile.model_validate_json(canonical_json(state["profile"])):
                raise APIError(503, "inspector_unavailable", "Restore the saved inspector profile to resume this task", retryable=True)
            def checkpoint(**_):
                self.store.save(state, token, lease_seconds)
            def prepare_model():
                messages = state["messages"]
                return [messages[0]] + state["history"] + messages[1:], [item for item in TOOLS if item["function"]["name"] in self.profile.allowed_tools]
            def plan_tool(call):
                name = call["function"]["name"]
                if name not in self.profile.allowed_tools:
                    raise APIError(502, "inspector_tool_forbidden", "The inspector selected an unallowed tool")
                try:
                    NoArguments.model_validate_json(call["function"]["arguments"])
                except ValueError:
                    raise APIError(502, "invalid_tool_arguments", "The inspector supplied invalid tool arguments") from None
                return {"name": name, "tool_call_id": call["id"], "operation_id": str(uuid5(UUID(state["run_id"]), f"tool:{state['steps']}"))}
            def execute_tool(plan):
                if plan["name"] not in self.profile.allowed_tools:
                    raise APIError(502, "inspector_tool_forbidden", "The inspector selected an unallowed tool")
                return self.tool_client.execute(self.profile.inspector_id, state["binding"], plan["name"], plan["operation_id"])
            AgentHarness(state, model=self.model, max_steps=self.profile.max_steps, checkpoint=checkpoint,
                         prepare_model=prepare_model, plan_tool=plan_tool, execute_tool=execute_tool).advance()
        except APIError as error:
            if error.body["code"] == "agent_lease_lost":
                return True
            state.update(status="retryable" if error.body["retryable"] else "failed", error=error.body, updated_at=utc_now())
            if error.body["retryable"]:
                if error.body["code"] not in ("model_not_configured", "inspector_unavailable"):
                    state["retry_failures"] += 1
                if state["retry_failures"] >= 12:
                    state.update(status="failed", error=APIError(502, "agent_retry_limit", "The inspector reached its retry limit").body)
                next_attempt_at = time.time() + (30 if state["retry_failures"] == 0 else min(60, 2 ** state["retry_failures"]))
        except Exception:
            state["retry_failures"] += 1
            state.update(status="failed" if state["retry_failures"] >= 12 else "retryable",
                         error=APIError(503, "inspector_worker_unavailable", "Inspector execution could not advance", retryable=True).body,
                         updated_at=utc_now())
            next_attempt_at = time.time() + min(60, 2 ** min(6, state["retry_failures"]))
        try:
            self.store.save(state, token, lease_seconds, release=True, next_attempt_at=next_attempt_at)
        except APIError as error:
            if error.body["code"] != "agent_lease_lost":
                raise
        return True

    def run(self, stop):
        while not stop.is_set():
            try:
                processed = self.process_next()
            except Exception:
                processed = False  # Keep request/provider data out of diagnostics; leases recover after expiry.
            if not processed:
                stop.wait(self.settings.worker_interval_s)


def create_inspector_app(service, *, background_worker=True):
    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()
        thread = threading.Thread(target=service.run, args=(stop,), name="inspector-agent-worker", daemon=True)
        if background_worker:
            thread.start()
        try:
            yield
        finally:
            stop.set()
            if background_worker:
                await asyncio.to_thread(thread.join, 5)

    app = FastAPI(title="Inspector agent — A2A polling", version=VERSION, lifespan=lifespan)
    app.state.service = service

    @app.middleware("http")
    async def protocol_headers(request, call_next):
        response = await call_next(request)
        response.headers.update({"Cache-Control": "private, no-store", "A2A-Version": VERSION, "X-Content-Type-Options": "nosniff"})
        return response

    @app.exception_handler(APIError)
    async def api_error(request, error):
        reason = {"task_not_found": "TASK_NOT_FOUND", "task_not_cancelable": "TASK_NOT_CANCELABLE",
                  "unsupported_operation": "UNSUPPORTED_OPERATION"}.get(error.body["code"], error.body["code"].upper())
        return JSONResponse({"error": {"code": error.status_code, "message": error.body["message"], "details": [
            {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason, "domain": "a2a-protocol.org"}]}},
                            error.status_code, headers={"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return await api_error(request, APIError(400, "invalid_argument", "Invalid inspector service request"))

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return await api_error(request, APIError(error.status_code, "http_error", "Inspector service endpoint was not found or is unavailable"))

    def caller(request):
        if request.headers.get("a2a-version", VERSION) != VERSION:
            raise APIError(400, "version_not_supported", "This service supports A2A 1.0")
        identity = service.authenticate(request.headers.get("authorization"))
        if EXTENSION not in {item.strip() for item in request.headers.get("a2a-extensions", "").split(",")}:
            raise APIError(400, "extension_support_required", "This service requires the inspection delegation extension")
        return identity

    @app.get("/.well-known/agent-card.json")
    def card():
        return service.card()

    @app.post("/message:send")
    async def send(request: Request):
        identity = caller(request)
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_BODY:
                raise APIError(413, "request_too_large", "Inspector task request exceeds its size limit")
        try:
            raw = decode_json(content)
        except (ValueError, UnicodeError, RecursionError):
            raise APIError(400, "invalid_argument", "Invalid inspector task JSON") from None
        state = service.submit(identity, raw)
        return {"task": service.public_task(state, SendRequest.model_validate(raw).configuration.historyLength)}

    @app.get("/tasks/{task_id}")
    def get_task(request: Request, task_id: UUID, historyLength: int | None = Query(default=None, ge=0, le=20)):
        identity = caller(request)
        return service.public_task(service.store.get(identity.client_id, identity.project_id, str(task_id)), historyLength)

    @app.get("/tasks")
    def list_tasks(request: Request, pageSize: int = Query(default=30, ge=1, le=100), pageToken: int = Query(default=0, ge=0), contextId: UUID | None = None):
        identity = caller(request)
        rows = service.store.list(identity.client_id, identity.project_id, after=pageToken, limit=pageSize, context_id=str(contextId) if contextId else None)
        return {"tasks": [service.public_task(json.loads(row["data"])) for row in rows[:pageSize]],
                "nextPageToken": str(rows[pageSize - 1]["sequence"]) if len(rows) > pageSize else ""}

    @app.post("/tasks/{task_id}:cancel")
    def cancel_task(request: Request, task_id: UUID):
        identity = caller(request)
        return service.public_task(service.store.cancel(identity.client_id, identity.project_id, str(task_id)))

    @app.post("/message:stream")
    @app.post("/tasks/{task_id}:subscribe")
    def unsupported(request: Request, task_id: str = ""):
        caller(request)
        raise APIError(501, "unsupported_operation", "This inspector service supports nonblocking task polling")
    return app


def create_app_from_env():
    """uvicorn ai_inspector.a2a_service:create_app_from_env --factory ..."""
    try:
        profile = InspectorProfile.model_validate_json(os.environ["INSPECTOR_AGENT_PROFILE_JSON"])
        callers = [AgentCaller.model_validate(item) for item in json.loads(os.environ["INSPECTOR_AGENT_CALLERS_JSON"])]
        settings = Settings(data_dir=Path(os.environ.get("INSPECTOR_AGENT_DATA_DIR", "agent-data")),
                            operator_token="unused-a2a-operator", device_token="unused-a2a-device",
                            meta_base_url=os.environ.get("INSPECTOR_META_BASE_URL", "https://api.meta.ai/v1"),
                            meta_model=os.environ.get("INSPECTOR_META_MODEL", "muse-spark-1.3"),
                            meta_api_key=os.environ.get("INSPECTOR_META_API_KEY", ""),
                            meta_timeout_s=float(os.environ.get("INSPECTOR_META_TIMEOUT_S", "30")))
        tools = None
        if profile.allowed_tools:
            tools = BackendToolClient(os.environ["INSPECTOR_AGENT_BACKEND_URL"], os.environ["INSPECTOR_AGENT_BACKEND_TOKEN"])
        service = InspectorAgentService(settings, profile=profile, database_path=settings.data_dir / "tasks.sqlite3",
                                        public_url=os.environ["INSPECTOR_AGENT_PUBLIC_URL"], callers=callers, tool_client=tools)
    except (ValueError, KeyError, TypeError):
        raise ValueError("Invalid inspector service environment; check profile, callers, URLs and server-only credentials") from None
    return create_inspector_app(service)
