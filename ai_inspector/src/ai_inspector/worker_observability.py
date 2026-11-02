"""Bounded OTLP/HTTP JSON transport for Workers (no SDK background threads)."""
import base64
import json
import time
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4
import httpx
from ai_inspector.observability import Telemetry


class WorkerObservation:
    def __init__(self, owner, values):
        self.owner = owner
        context = values.get("trace_context") or {}
        self.trace_id, self.id = context.get("trace_id", uuid4().hex), uuid4().hex[:16]
        self.span = dict(traceId=self.trace_id, spanId=self.id, name=values["name"], kind=1,
                         startTimeUnixNano=str(time.time_ns()))
        if context.get("parent_span_id"):
            self.span["parentSpanId"] = context["parent_span_id"]
        self.attrs = {"langfuse.observation.type": values.get("as_type", "span"),
                      "langfuse.environment": owner.config.environment, "langfuse.release": owner.config.release}
        self.update(**{k: v for k, v in values.items() if k in ("input", "metadata")})

    def update(self, **values):
        names = {"model": "gen_ai.request.model", "level": "langfuse.observation.level",
                 "status_message": "langfuse.observation.status_message"}
        for key, value in values.items():
            if value is None:
                continue
            if key == "metadata":
                for name, item in value.items():
                    self.attrs["langfuse.observation.metadata." + name] = json.dumps(item, ensure_ascii=False)
                for name, snapshot in value.get('prompt_versions', {}).items():
                    if snapshot.get('source') == 'langfuse':
                        self.attrs['langfuse.observation.prompt.name'] = name
                        self.attrs['langfuse.observation.prompt.version'] = str(snapshot['version'])
            elif key in names:
                self.attrs[names[key]] = str(value)
            elif key in ("input", "output", "usage_details"):
                self.attrs["langfuse.observation." + key] = json.dumps(value, ensure_ascii=False)

    def update_trace(self, *, session_id=None, tags=None):
        self.attrs["session.id"] = session_id or ""
        self.attrs["langfuse.trace.tags"] = tags or []

    def end(self):
        self.span["endTimeUnixNano"] = str(time.time_ns())
        self.span["attributes"] = [{"key": k, "value": {"arrayValue": {"values": [{"stringValue": x} for x in v]}}
                                   if isinstance(v, list) else {"stringValue": v}} for k, v in self.attrs.items()]
        if len(self.owner.spans) < 128:
            self.owner.spans.append(self.span)


class WorkerTelemetry(Telemetry):
    def __init__(self, config, transport):
        self.config, self.transport, self.spans = config, transport, []
        self.client = self if config.configured else None
        self.reason = "ready" if self.client else "disabled"

    def start_observation(self, **values):
        return WorkerObservation(self, values)

    def _request(self, method, path, *, body=None):
        auth = base64.b64encode((self.config.public_key + ":" + self.config.secret_key).encode()).decode()
        with httpx.Client(transport=self.transport, timeout=2, follow_redirects=False) as client:
            with client.stream(method, self.config.base_url.rstrip("/") + path,
                headers={"Authorization": "Basic " + auth, "x-langfuse-ingestion-version": "4"}, json=body) as response:
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 128 * 1024:
                        raise ValueError("Telemetry response exceeds limit")
                if response.status_code != 200:
                    raise ValueError("Telemetry unavailable")
        return json.loads(content or b"{}")

    def get_prompt(self, name, *, version=None, label=None, **_):
        query = "version=" + str(version) if version else "label=" + quote(label or "production", safe="")
        value = self._request("GET", "/api/public/v2/prompts/" + quote(name, safe="") + "?" + query)
        return SimpleNamespace(prompt=value["prompt"], version=value["version"])

    def flush(self):
        spans, self.spans = self.spans, []
        if not self.client or not spans:
            return
        try:
            self._request("POST", "/api/public/otel/v1/traces", body={"resourceSpans": [{
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "inspector-worker"}}]},
                "scopeSpans": [{"scope": {"name": "inspector.observability"}, "spans": spans}]}]})
        except Exception:
            pass  # Never retry business work because telemetry export failed.
