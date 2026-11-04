"""Public browser access to owned chats in the real project and capture queue."""
import json
import re
import threading
import time
from collections import deque
from . import browser_session
from .errors import APIError

COOKIE = "__Host-inspector-public"
_lock = threading.Lock()
_calls = deque()


def visitor(settings, value):
    if browser_session.verify(settings.operator_token + ":public", value):
        return value.split(".")[1]
    return None


def owned_chats(service, principal):
    with service.database.connection() as connection:
        rows = connection.execute("SELECT data FROM chats WHERE project_id=?", (principal.project_id,)).fetchall()
    return [chat for row in rows if (chat := json.loads(row["data"])).get("public_owner") == principal.browser_id]


def owned_sessions(service, principal):
    return {sid for chat in owned_chats(service, principal)
            for sid in [chat.get("session_id"), *chat.get("public_sessions", [])] if sid}


def authorize(service, principal, request):
    if not principal.browser_id:
        return
    path, method = request.url.path, request.method
    allowed = method in ("GET", "HEAD") and (path in (
        "/v1/operator/browser-session", "/v1/operator/overview", "/v1/capabilities",
        "/v1/demo/config", "/v1/voice/config", "/v1/chats",
        f"/v1/devices/{service.settings.device_id}/status", "/v1/demo/scenarios")
        or path.startswith("/v1/demo/scenarios/"))
    if path == "/v1/chats" and method == "POST":
        if len(owned_chats(service, principal)) >= 20:
            raise APIError(429, "demo_limit", "This browser has reached its conversation limit")
        allowed = True
    if path == "/v1/voice/transcribe" and method == "POST":
        allowed = True
    match = re.fullmatch(r"/v1/chats/([0-9a-f-]{36})(?:/.*)?", path)
    if match and method in ("GET", "HEAD", "POST"):
        with service.database.connection() as connection:
            chat = service.agent._chat(connection, principal, match[1])
        if request.query_params.get("session_id") not in (None, chat.get("session_id")):
            raise APIError(403, "forbidden", "Capture is outside this conversation")
        allowed = True
    match = re.fullmatch(r"/v1/sessions/([0-9a-f-]{36})(/abort)?", path)
    if match and (method in ("GET", "HEAD") and not match[2] or method == "POST" and match[2]):
        allowed = match[1] in owned_sessions(service, principal)
    match = re.fullmatch(r"/v1/inspections/([0-9a-f-]{36})(/bim)?", path)
    if match and method in ("GET", "HEAD"):
        allowed = any(service.get_session(principal, sid)["inspection_id"] == match[1]
                      for sid in owned_sessions(service, principal))
    if not allowed:
        raise APIError(403, "forbidden", "This action is outside this browser's conversations")
    if method == "POST" and (path.endswith("/messages") or path == "/v1/voice/transcribe"):
        with _lock:
            now = time.monotonic()
            while _calls and _calls[0] < now - 3600:
                _calls.popleft()
            if len(_calls) >= 180:
                raise APIError(429, "demo_limit", "Demo usage limit reached. Please try again later")
            _calls.append(now)
