"""Optional Langfuse boundary shared by the backend and native comparison service.

No operational state is stored in Langfuse. Explicit observations never capture
function arguments, HTTP headers, exception text, documents or media automatically.
"""
import hashlib
import json
import logging
import math
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlsplit

import httpx

_active = ContextVar("inspector_observation", default=None)
_TRACE = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-0[01]\Z")


@dataclass(frozen=True)
class ObservabilityConfig:
    public_key: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    base_url: str = "https://cloud.langfuse.com"
    enabled: bool = True
    environment: str = "development"
    release: str = ""
    prompts_enabled: bool = False
    prompt_label: str = "production"

    def __post_init__(self):
        u = urlsplit(self.base_url)
        if (u.scheme not in ("https", "http") or not u.hostname or u.username or u.password
                or u.query or u.fragment or u.path not in ("", "/")
                or (u.scheme == "http" and u.hostname not in ("localhost", "127.0.0.1", "::1"))):
            raise ValueError("LANGFUSE_BASE_URL requires an HTTPS origin (loopback HTTP allowed)")
        u.port
        for value in (self.public_key, self.secret_key):
            if value and (not value.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in value)):
                raise ValueError("Invalid Langfuse credential format")
        for value in (self.environment, self.release, self.prompt_label):
            if len(value) > 128 or any(ord(c) < 32 for c in value):
                raise ValueError("Invalid Langfuse environment, release or label")

    @property
    def configured(self):
        return self.enabled and bool(self.public_key and self.secret_key)

    @classmethod
    def from_mapping(cls, values):
        def flag(name, default):
            value = values.get(name, str(default))
            if not isinstance(value, str):
                raise ValueError("Invalid " + name)
            value = value.lower()
            if value not in ("true", "false"):
                raise ValueError("Invalid " + name)
            return value == "true"
        return cls(public_key=values.get("LANGFUSE_PUBLIC_KEY", ""),
                   secret_key=values.get("LANGFUSE_SECRET_KEY", ""),
                   base_url=values.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
                   enabled=flag("LANGFUSE_ENABLED", True),
                   environment=values.get("LANGFUSE_TRACING_ENVIRONMENT", "development"),
                   release=values.get("LANGFUSE_RELEASE", ""),
                   prompts_enabled=flag("INSPECTOR_LANGFUSE_PROMPTS_ENABLED", False),
                   prompt_label=values.get("INSPECTOR_LANGFUSE_PROMPT_LABEL", "production"))


def trace_context(value):
    match = _TRACE.fullmatch(value or "") if isinstance(value, str) else None
    if not match or int(match[1], 16) == 0 or int(match[2], 16) == 0:
        return None
    return {"trace_id": match[1], "parent_span_id": match[2]}


def current_headers():
    active = _active.get()
    if not active or not active[1].trace_id or not active[1].id:
        return {}
    return {"traceparent": f"00-{active[1].trace_id}-{active[1].id}-01"}


def record_generation(*, model, usage=None, finish_reason=None):
    """Provider diagnostics go to the active generation, never into model messages."""
    active = _active.get()
    if active:
        active[1].update(model=model, usage_details=normalize_usage(usage),
                         metadata={"finish_reason": finish_reason} if finish_reason in
                         ("stop", "tool_calls", "length", "content_filter") else {})


def normalize_usage(value):
    if not isinstance(value, dict):
        return None
    def count(n):
        return n if type(n) is int and 0 <= n <= 10**10 else None
    p, c = count(value.get("prompt_tokens")), count(value.get("completion_tokens"))
    if p is None or c is None:
        return None
    cached = count((value.get("prompt_tokens_details") or {}).get("cached_tokens")) if isinstance(value.get("prompt_tokens_details", {}), dict) else None
    reasoning = count((value.get("completion_tokens_details") or {}).get("reasoning_tokens")) if isinstance(value.get("completion_tokens_details", {}), dict) else None
    result = {"input": p - min(p, cached or 0), "output": c - min(c, reasoning or 0)}
    if cached:
        result["input_cached_tokens"] = min(p, cached)
    if reasoning:
        result["output_reasoning_tokens"] = min(c, reasoning)
    return result


class Observation:
    def __init__(self, raw=None):
        self.raw = raw
        self.trace_id = getattr(raw, "trace_id", None)
        self.id = getattr(raw, "id", None)

    def update(self, **values):
        if self.raw:
            try:
                self.raw.update(**values)
            except Exception:
                pass  # Telemetry must not change a business outcome.


class Telemetry:
    def __init__(self, config=None, *, client=None):
        self.config = config or ObservabilityConfig(enabled=False)
        self.client = client
        self.reason = "disabled" if not self.config.configured else "sdk_unavailable"
        if client is None and self.config.configured:
            try:
                from langfuse import Langfuse
                from opentelemetry.sdk.trace import TracerProvider
                # The SDK can log raw upstream failures. Keep them out of app logs.
                logger = logging.getLogger("langfuse")
                logger.addHandler(logging.NullHandler())
                logger.propagate = False
                self.client = Langfuse(public_key=self.config.public_key, secret_key=self.config.secret_key,
                    base_url=self.config.base_url, environment=self.config.environment,
                    release=self.config.release or None, timeout=2, flush_at=32, flush_interval=2,
                    httpx_client=httpx.Client(timeout=2, follow_redirects=False),
                    additional_headers={"x-langfuse-ingestion-version": "4"}, tracer_provider=TracerProvider())
            except Exception:
                self.client = None
        if self.client:
            self.reason = "ready"

    @contextmanager
    def scope(self, state, name, kind="agent", *, metadata=None, input=None):
        raw = None
        active = _active.get()
        if self.client:
            context = state.get("_trace_context")
            if not context:
                context = trace_context(current_headers().get("traceparent"))
                if not context and hasattr(self.client, 'start_as_current_observation'):
                    try:
                        from opentelemetry.trace import get_current_span
                        sdk_context = get_current_span().get_span_context()
                        if sdk_context.is_valid:
                            context = {'trace_id': format(sdk_context.trace_id, '032x'),
                                       'parent_span_id': format(sdk_context.span_id, '016x')}
                    except Exception:
                        pass
                if not context:
                    seed = "inspector:" + str(state.get("run_id", state.get("task_id", "")))
                    context = {"trace_id": hashlib.sha256(seed.encode()).hexdigest()[:32]}
                state["_trace_context"] = context
            identity = dict(active[2]) if active else {}
            binding = state.get("binding", {})
            for key in ("run_id", "parent_run_id", "operation_id", "chat_id", "project_id", "inspector_id", "inspector_version"):
                value = state.get(key, binding.get(key))
                if isinstance(value, str):
                    identity[key] = value[:200]
            identity["origin"] = state.get("analysis_origin", identity.get("origin", "live"))
            try:
                values = dict(name=name, as_type=kind, trace_context=context,
                              metadata=identity | (metadata or {}), input=input)
                if state.get('prompt_snapshots'):
                    values['metadata']['prompt_versions'] = {n: {k: v for k, v in s.items() if k != 'text'}
                                                            for n, s in state['prompt_snapshots'].items()}
                if hasattr(self.client, 'start_as_current_observation'):
                    from langfuse import propagate_attributes
                    session = (identity['project_id'] + '/' + identity['chat_id']
                               if identity.get('project_id') and identity.get('chat_id') else None)
                    prompt = next(({'name': n, 'version': s['version']} for n, s in state.get('prompt_snapshots', {}).items()
                                   if s.get('source') == 'langfuse'), None)
                    with propagate_attributes(session_id=session, tags=[identity['origin'], self.config.environment], prompt=prompt):
                        raw = self.client.start_observation(**values)
                else:
                    raw = self.client.start_observation(**values)
                if hasattr(raw, 'update_trace') and identity.get("project_id") and identity.get("chat_id"):
                    raw.update_trace(session_id=identity["project_id"] + "/" + identity["chat_id"],
                                     tags=[identity["origin"], self.config.environment])
            except Exception:
                raw = None
        else:
            identity = {}
        observation = Observation(raw)
        token = _active.set((self, observation, identity))
        try:
            yield observation
        except BaseException as error:
            code = getattr(error, "body", {}).get("code", getattr(error, "code", "execution_interrupted"))
            observation.update(level="DEFAULT" if code == "remote_task_pending" else "ERROR",
                               status_message=code, metadata={"outcome": "waiting" if code == "remote_task_pending" else "failed"})
            raise
        finally:
            _active.reset(token)
            if raw:
                try:
                    raw.end()
                except Exception:
                    pass

    def step(self, state, event, *, input=None):
        # A new short-lived span per attempt; no in-memory span survives a wait.
        parent = trace_context(current_headers().get("traceparent"))
        child = {"run_id": state["run_id"], "_trace_context": parent or state.get("_trace_context", {}),
                 "prompt_snapshots": state.get("prompt_snapshots", {})}
        return self.scope(child, event["name"], "generation" if event["kind"] == "model" else "tool",
                          metadata={key: event.get(key) for key in ("event_id", "operation_id", "attempt", "retry_of")}, input=input)

    def resolve_prompt(self, state, name, fallback, *, version=None, fetch=True):
        snapshots = state.setdefault("prompt_snapshots", {})
        if name not in snapshots:
            text, resolved_version, source = fallback, None, "bundled"
            if fetch and self.client and self.config.prompts_enabled:
                try:
                    prompt = self.client.get_prompt(name, version=version,
                        label=None if version else self.config.prompt_label, type="text",
                        max_retries=0, fetch_timeout_seconds=2)
                    if isinstance(prompt.prompt, str) and 0 < len(prompt.prompt) <= 16000:
                        text, resolved_version, source = prompt.prompt, prompt.version, "langfuse"
                except Exception:
                    pass
            snapshots[name] = {"text": text, "version": resolved_version, "source": source,
                               "sha256": hashlib.sha256(text.encode()).hexdigest()}
        return snapshots[name]["text"]

    def flush(self):
        if self.client:
            try:
                self.client.flush()
            except Exception:
                pass


@lru_cache(maxsize=8)
def telemetry(config):
    return Telemetry(config)
