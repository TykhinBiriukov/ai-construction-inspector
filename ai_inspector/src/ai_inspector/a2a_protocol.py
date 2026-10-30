"""Pinned A2A 1.0 HTTP+JSON polling profile and bounded service transport."""

import hashlib
import json
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import ConfigDict, Field, field_validator, model_validator

from .errors import APIError
from .inspectors import InspectorID
from .schemas import APIModel, Identifier
from .storage import canonical_json


VERSION = "1.0"
EXTENSION = "urn:ai-inspector:delegation:v1"
MAX_BODY = 2 * 1024 * 1024
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}


def decode_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("Duplicate JSON member")
            result[key] = item
        return result
    def constant(_):
        raise ValueError("Nonfinite JSON value")
    return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)


def service_url(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("https", "http") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
                or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))
                or any(c in value for c in ("\\", "\r", "\n"))):
            raise ValueError
        parsed.port
    except ValueError:
        raise ValueError("Agent service URLs require HTTPS; HTTP is allowed on loopback only") from None
    return value.rstrip("/")


def bearer_value(value):
    if not isinstance(value, str) or not value or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError("A nonblank ASCII service credential is required")
    return value


class RemoteInspector(APIModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    transport: Literal["a2a"] = "a2a"
    inspector_id: InspectorID
    version: Annotated[str, Field(min_length=1, max_length=64)]
    description: Annotated[str, Field(min_length=1, max_length=1000)]
    base_url: Annotated[str, Field(max_length=2000)]
    token_env: Annotated[str, Field(pattern=r"^[A-Z_][A-Z0-9_]{0,95}$")]
    tool_token_env: Annotated[str, Field(pattern=r"^[A-Z_][A-Z0-9_]{0,95}$")] | None = None
    allowed_tools: tuple[Literal["session_get", "device_status"], ...] = ()
    timeout_s: Annotated[float, Field(ge=1, le=30, allow_inf_nan=False)] = 10.0
    poll_interval_s: Annotated[float, Field(ge=1, le=60, allow_inf_nan=False)] = 2.0

    _url = field_validator("base_url")(service_url)

    @model_validator(mode="after")
    def tool_grant(self):
        if self.allowed_tools and (not self.tool_token_env or self.tool_token_env == self.token_env):
            raise ValueError("Backend tools require a separate credential reference")
        return self

    @field_validator("version", "description")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Agent registration cannot be blank")
        return value

    @field_validator("allowed_tools")
    @classmethod
    def unique_tools(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Agent tools must be unique")
        return value


def remote_profiles(value):
    try:
        values = json.loads(value)
        if not isinstance(values, list) or len(values) > 32:
            raise ValueError
        return tuple(RemoteInspector.model_validate_json(canonical_json(item)) for item in values)
    except (ValueError, TypeError):
        raise ValueError("Invalid INSPECTOR_REMOTE_AGENTS_JSON configuration") from None


class TextPart(APIModel):
    text: Annotated[str, Field(min_length=1, max_length=8000)]


class WireMessage(APIModel):
    messageId: UUID
    role: Literal["ROLE_USER"]
    parts: Annotated[list[TextPart], Field(min_length=1, max_length=1)]
    contextId: UUID | None = None
    taskId: UUID | None = None


class SendConfiguration(APIModel):
    returnImmediately: Literal[True]
    historyLength: Annotated[int, Field(ge=0, le=20)] = 0
    acceptedOutputModes: list[Literal["text/plain"]] = Field(default_factory=lambda: ["text/plain"])


class DelegationInput(APIModel):
    schema_version: Literal["0.1"]
    inspector_id: InspectorID
    inspector_version: Annotated[str, Field(min_length=1, max_length=64)]
    operation_id: UUID
    project_id: Identifier
    chat_id: UUID
    parent_run_id: UUID
    delegation_run_id: UUID
    context: dict
    reference: str | list[dict]

    @field_validator("reference")
    @classmethod
    def reference_content(cls, value):
        if isinstance(value, str):
            return value
        if not 1 <= len(value) <= 2:
            raise ValueError("Invalid reference preview")
        for item in value:
            if item.get("type") == "text" and set(item) == {"type", "text"} and isinstance(item["text"], str):
                continue
            if item.get("type") == "image_url" and set(item) == {"type", "image_url"}:
                link = item["image_url"]
                if (isinstance(link, dict) and set(link) == {"url"} and isinstance(link["url"], str)
                        and link["url"].startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,"))):
                    continue
            raise ValueError("Only supplied text and inline image previews are supported")
        return value

    @field_validator("context")
    @classmethod
    def safe_context(cls, value):
        if set(value) - {"project_id", "device_id", "room_id", "session", "source_fingerprint"}:
            raise ValueError("Unsupported server context")
        session = value.get("session")
        if session is not None:
            allowed = {"session_id", "inspection_id", "device_id", "room_id", "parent_session_id", "capture_state",
                       "processing_state", "device_acknowledged_at", "revision", "capture_origin", "input_mode",
                       "model_is_synthetic", "processing_scope", "source_accepted", "geometry_result_available", "geometry_analysis_origin"}
            if not isinstance(session, dict) or set(session) - allowed or any(isinstance(v, (dict, list)) for v in session.values()):
                raise ValueError("Unsupported session projection")
        return value


class SendMetadata(APIModel):
    inspector: DelegationInput


class SendRequest(APIModel):
    message: WireMessage
    configuration: SendConfiguration
    metadata: SendMetadata


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class ServiceHTTP:
    def __init__(self, base_url, token, *, timeout=10.0, transport=None):
        self.base_url = service_url(base_url)
        self.token = bearer_value(token)
        self.timeout = timeout
        self.transport = transport

    def request(self, method, path, *, body=None):
        if not path.startswith("/") or ".." in path or "://" in path:
            raise ValueError("Service paths must be predefined relative paths")
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=False, transport=self.transport) as client:
                with client.stream(method, self.base_url + path,
                                   headers={"Authorization": "Bearer " + self.token, "A2A-Version": VERSION,
                                            "Content-Type": "application/a2a+json", "A2A-Extensions": EXTENSION},
                                   content=body.encode("utf-8") if body is not None else None) as response:
                    if response.status_code != 200:
                        retryable = response.status_code in (408, 429) or response.status_code >= 500
                        raise APIError(503 if retryable else 502, "remote_agent_unavailable",
                                       "The configured inspector service request was unsuccessful", retryable=retryable)
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_BODY:
                            raise APIError(502, "invalid_remote_agent_output", "The inspector service response exceeded its limit")
            value = decode_json(content)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except httpx.HTTPError:
            raise APIError(503, "remote_agent_unavailable", "The inspector service timed out or could not connect", retryable=True) from None
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise APIError(502, "invalid_remote_agent_output", "The inspector service returned an invalid response") from None


class RemoteInspectorClient(ServiceHTTP):
    def check_card(self, profile):
        card = self.request("GET", "/.well-known/agent-card.json")
        try:
            if not isinstance(card.get("skills"), list) or not isinstance(card.get("supportedInterfaces"), list):
                raise ValueError
            extensions = card["capabilities"]["extensions"]
            if not isinstance(extensions, list) or not all(isinstance(item, dict) for item in card["skills"] + card["supportedInterfaces"] + extensions):
                raise ValueError
            valid_extension = any(item.get("uri") == EXTENSION for item in extensions)
            supported_extensions = all(not item.get("required") or item.get("uri") == EXTENSION for item in extensions)
            valid = (card.get("version") == profile.version and valid_extension and supported_extensions
                     and any(item.get("id") == profile.inspector_id for item in card["skills"])
                     and any(item.get("url", "").rstrip("/") == self.base_url and item.get("protocolBinding") == "HTTP+JSON"
                             and item.get("protocolVersion") == VERSION for item in card["supportedInterfaces"]))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise APIError(502, "invalid_remote_agent_output", "The inspector service returned an invalid Agent Card") from None
        if not valid:
            raise APIError(503, "inspector_unavailable", "The registered inspector service identity or version does not match", retryable=True)

    def task(self, child):
        if child.get("remote_task_id"):
            value = self.request("GET", "/tasks/" + str(UUID(child["remote_task_id"])) + "?historyLength=0")
        else:
            value = self.request("POST", "/message:send", body=child["remote_request"]).get("task")
        try:
            if not isinstance(value, dict):
                raise ValueError
            task_id, context_id = str(UUID(value["id"])), str(UUID(value["contextId"]))
            if child.get("remote_task_id") and (child["remote_task_id"] != task_id or child["remote_context_id"] != context_id):
                raise ValueError
            identity = value["metadata"]["inspector"]
            expected = json.loads(child["remote_request"])["metadata"]["inspector"]
            for key in ("operation_id", "project_id", "chat_id", "parent_run_id", "delegation_run_id", "inspector_id", "inspector_version"):
                if identity[key] != expected[key]:
                    raise ValueError
            if identity["request_sha256"] != hashlib.sha256(child["remote_request"].encode("utf-8")).hexdigest():
                raise ValueError
            state = value["status"]["state"]
            if state not in TERMINAL | {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_AUTH_REQUIRED", "TASK_STATE_INPUT_REQUIRED"}:
                raise ValueError
            reply = None
            if state == "TASK_STATE_COMPLETED":
                artifacts = value["artifacts"]
                if len(artifacts) != 1 or len(artifacts[0]["parts"]) != 1:
                    raise ValueError
                reply = artifacts[0]["parts"][0]["text"]
                if not isinstance(reply, str) or not reply.strip() or len(reply) > 16000:
                    raise ValueError
            return task_id, context_id, state, reply
        except (ValueError, TypeError, KeyError, AttributeError):
            raise APIError(502, "invalid_remote_agent_output", "The inspector task identity or result is invalid") from None
