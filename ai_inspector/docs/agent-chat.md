# Main inspector → tools and specialist inspectors

The main agent is `OrchestratorAgent` (`ChatAgent` remains a compatibility alias). It talks to the user, selects predefined tools or a registered inspector, and explains accepted results. The shared Meta Model API adapter uses `muse-spark-1.3`; configured credentials do not establish live account access. Native HTTP schemas are generated at `/docs` and `/openapi.json`; wire/model definitions live in the linked code below.

## Main inspector and specialist registration

No specialist is registered by default. The orchestrator's direct tools handle session creation/status, device presence and configured drawing/photo comparison; `delegate_inspection` is offered only with registered profiles. A general question need not create capture. `session_create` requires an explicit capture request, reuses an open session, and creates a linked rescan after sealing/cancellation only when requested. Optional capture_task is operator context, not a remote camera command. Device acknowledgement, capture and accepted source remain distinct facts.

[InspectorProfile/InspectorRegistry](../src/ai_inspector/inspectors.py) define trusted local profiles; application composition can pass them to `InspectorService(settings, inspectors=(...))`. For example:

```python
from ai_inspector.inspectors import InspectorProfile
from ai_inspector.service import InspectorService

profile = InspectorProfile(
    inspector_id="evidence_example", version="1",
    description="Review the supplied evidence and state what is missing.",
    instructions="Use only supplied evidence. Never invent measurements.",
    allowed_tools=("session_get", "device_status"), max_steps=6,
)
service = InspectorService(settings, inspectors=(profile,))
```

This is registration syntax, not a shipped inspection capability. IDs are unique; `orchestrator` is reserved. Bump the profile version when changing instructions, responsibility, tools or budget. Models cannot register profiles or supply endpoints. Local profiles can be injected into native create_app or Cloudflare service composition.

Each inspector has its own instructions, tools, bounded loop, history and state through [AgentHarness](../src/ai_inspector/agent_runtime.py). The current specialist allowlist contains read-only session_get/device_status, independently enabled per profile (default none). session_create, nested delegation and arbitrary code/HTTP/SQL are not specialist tools. Budgets count successful model turns; uncertain provider calls can repeat and billing is not exactly once.

Local children persist in parent agent_runs JSON under the parent lease. Pending model messages/tool definitions and concrete tool commands/operation IDs are fixed before IO; completed reports are reused. Local memory includes up to six completed task/report pairs for the same chat and exact profile from the last ten parent runs. Reports are historical evidence; read current facts through tools. Missing/changed saved profiles hold unfinished work until restored. Existing legacy delegations remain readable/resumable through the compatibility path.

Remote profiles below own independent task stores and leases; they do not run under the parent's checkpoint. Both paths share the harness implementation and selected model adapter.

## Remote inspector services — A2A HTTP+JSON polling

The implemented profile is **A2A 1.0** with required extension `urn:ai-inspector:delegation:v1`: nonblocking admission, polling, listing and task cancellation. Streaming/SSE, webhooks, gRPC/JSON-RPC and interactive messages to an existing task are not enabled. References: [pinned A2A specification](https://a2a-protocol.org/v1.0.0/specification/), [protocol definitions](https://github.com/a2aproject/A2A/blob/v1.0.0/specification/a2a.proto), and implemented [wire validation/client](../src/ai_inspector/a2a_protocol.py).

### Register a deployed inspector with the orchestrator

Set `INSPECTOR_REMOTE_AGENTS_JSON` (default `[]`) on the orchestrator:

```json
[
  {
    "inspector_id": "evidence_example",
    "version": "1",
    "description": "Review supplied evidence and missing information.",
    "base_url": "https://inspector-service.example",
    "token_env": "EVIDENCE_TASK_TOKEN",
    "tool_token_env": "EVIDENCE_TOOL_TOKEN",
    "allowed_tools": ["session_get", "device_status"]
  }
]
```

References name server Secrets; they are not credential values. Native Settings resolves them from the process/ignored backend .env (process wins); Cloudflare resolves Worker bindings. Task and backend-tool credentials must differ from each other, operator/device/model/storage credentials and credentials of other registrations. With no backend tools, omit tool_token_env and grant an empty allowed_tools list.

Only server configuration chooses URLs. HTTPS is required except loopback HTTP; redirects are refused and IO bodies are bounded. Models receive IDs/descriptions, not URLs or keys. Endpoint/profile changes hold unfinished work; restoring the saved registration resumes it. Secret rotation under the same reference is supported.

### Run an inspector as a separate service

The native ASGI factory is `ai_inspector.a2a_service:create_app_from_env`. Configure its process environment:

| Setting | Purpose |
|---|---|
| INSPECTOR_AGENT_PROFILE_JSON | InspectorProfile: ID/version, responsibility/instructions, allowed tools and max_steps |
| INSPECTOR_AGENT_CALLERS_JSON | Secret array of client_id, project_id and token; caller token matches the orchestrator's task credential |
| INSPECTOR_AGENT_PUBLIC_URL | Service's HTTPS base URL; loopback HTTP for local use |
| INSPECTOR_AGENT_DATA_DIR | Persistent directory for tasks.sqlite3 |
| INSPECTOR_META_BASE_URL / MODEL / API_KEY / TIMEOUT_S | Same named model settings as the orchestrator; each full name begins INSPECTOR_META_ |
| INSPECTOR_AGENT_BACKEND_URL / BACKEND_TOKEN | Backend URL and separate tool Secret, required when backend tools are enabled |

The profile JSON has the same fields as the local example. Callers are project-bound; store the actual caller JSON/key in deployment Secrets. This factory reads process environment, not the backend's .env automatically, and does not expose operator routes.

From ai_inspector, after installing the backend and setting the environment:

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn ai_inspector.a2a_service:create_app_from_env --factory --host 127.0.0.1 --port 8100
```

Deploy behind HTTPS with a persistent SQLite volume, one inspector deployment per volume. An ephemeral Cloudflare filesystem is not an inspector task store. A standalone PostgreSQL task-store adapter is not implemented. The orchestrator's existing SQLite/PostgreSQL/Workers storage is unchanged; remote registration needs no new shared migration.

### Wire contract and recovery

The public Agent Card is `GET /.well-known/agent-card.json`. Task routes require the dedicated caller bearer plus `A2A-Version: 1.0` and `A2A-Extensions: urn:ai-inspector:delegation:v1`:

- `POST /message:send` accepts the validated SendRequest and returns HTTP 200 with a task. messageId equals the domain operation UUID, role is ROLE_USER, the message has one text part and returnImmediately must be true.
- metadata.inspector binds schema/profile version, operation/project/chat/parent/delegation IDs, the small server context and frozen reference. Reference images must be inline PNG/JPEG/WebP previews; arbitrary remote image URLs are not fetched.
- `GET /tasks/{id}` reads status/artifacts and optional bounded public history; it never advances a model. `GET /tasks` provides caller-scoped pagination/context filtering.
- `POST /tasks/{id}:cancel` invalidates that inspector task's lease. It neither aborts capture nor cancels a Geometry job.

Admission persists before responding: exact caller/operation/body replay returns the same server-generated task; changed content conflicts. Context is scoped to caller/project/chat/profile. Service memory uses up to six completed pairs from the last 50 context tasks; full private checkpoint/history stays in its store.

Model/tool commands checkpoint before IO. Independent leases fence stale or cancelled owners. Transient errors back off with a bounded retry budget; missing configuration/changed profile waits for restoration. A completed report is a single text Artifact. The orchestrator verifies task/context identity, delegation bindings and frozen-request SHA-256, including source fingerprint when available, before accepting it. Raw HTTP/provider errors and secrets are excluded.

Native lifespan starts a remote-task poller; Cloudflare process advances remote and capture work, driven by the frontend cron. Unfinished remote runs expose auto_resume=true. Poll intervals depend on the actual driver/trigger; this is not a general indexed agent queue or an automatic Geometry continuation. Local legacy requests and minion follow-up use the distinct paths below.

`POST /v1/inspector-tools/{inspector_id}/{tool_name}` is the scoped backend gateway. Its separate service Secret is bound to the registered inspector and still-active saved delegation. Operator/device credentials are not accepted. Both remote profile permission and backend grant must allow the requested read tool; IDs/scopes come from saved state, not model arguments.

## Server configuration

Use Settings/[.env.example](../.env.example) and [model_client.py](../src/ai_inspector/model_client.py) for current defaults/validation. Core names are INSPECTOR_META_BASE_URL, INSPECTOR_META_MODEL, INSPECTOR_META_API_KEY, INSPECTOR_META_TIMEOUT_S and INSPECTOR_AGENT_MAX_STEPS. The key is server-only. Restart native processes after changing configuration; Worker configuration follows [deployment](cloudflare-deployment.md).

The adapter calls chat completions, validates one function call per response and binds tool results to its tool_call_id. Backend operation IDs are separate and persisted before effects. Missing model credentials produce resumable model_not_configured, never a substitute mock. agent_model_configured in capabilities means settings exist, not that a live request succeeded. Provider errors, signed storage URLs and full private session records do not enter model context.

## Web API

Operator/project-scoped chat creation, message submission and paginated history are defined in OpenAPI and [agent.py](../src/ai_inspector/agent.py). Phones cannot read chats or invoke the model. A new chat pins device/project and caller room context; the room is not a validated IFC room. Use a new chat for a different capture context.

Optional file_ids and document_selection refer only to available files in that chat. Their exact request body, including preview, belongs to retry identity. Server-generated chat/run IDs and existing completed/failed message replay are preserved. ChatAgent remains a Python alias; new runs expose role orchestrator while legacy runs without role metadata retain chat.

## Execution, retry and recovery

Run states are pending → running → completed, retryable or failed. General local/legacy recovery resubmits the **same saved message ID and entire body**, including selected files/preview. A new message is rejected with chat_busy while the earlier run is unfinished. Completed/failed requests replay without another model call. Browser timeout/reload is not cancellation.

Commands, operation IDs, frozen model requests and tool results are durable before side effects. One leased owner advances a run; stale owners cannot publish. Process death permits reclaim after lease expiry. Completed child reports and successful tool operations are reused; uncertain network/model execution can require an identical retry.

Remote A2A workflows also continue through the poller without the browser. Drawing/photo tasks use a separate later-message result tool. The existing capture worker alone owns source validation → Geometry dispatch/reconciliation; no agent tool creates a second initial job. A completed Geometry result does not automatically start a new model turn.

## Files, work plans and observations

Workspace needs 003_workspace.sql for PostgreSQL; native SQLite initializes its equivalent schema. APIs are operator/chat-scoped. Schemas and numeric limits come from [workspace.py](../src/ai_inspector/workspace.py), [document_selection.py](../src/ai_inspector/document_selection.py) and OpenAPI.

Uploads are private immutable content-addressed blobs. Same file ID with changed bytes/metadata conflicts. Trash removes the current chat reference, not persisted blobs/history or earlier frozen model input; restore does not rewrite the original record. Derived reply/tool/capture files are context, not independent evidence. Files in Trash cannot be newly selected.

Text formats yield bounded marked excerpts; opaque PDF/images are metadata-only without a sent preview or a real minion report. Opening a file alone sends no model input. A selected region sends a bounded crop; selected IFC elements send source-bound STEP identities and browser reference properties/view. One crop is not a whole document. IFC rendering and caller room IDs do not validate model geometry, units, room membership, alignment or measurements. Files, notes and previews remain untrusted data.

Plan imports validate all rows before atomic request-ID replay. Plan edits require current revision. Observations preserve evidence snapshots and operator provenance; authors are self-reported under a shared credential. An AI output alone cannot confirm work; withdrawal retains history. Progress uses the latest active observation per planned item: no observation is no_data, never not_done. Equal-weight confirmed-item percentages are not physical/financial progress or certification. Models can explain the snapshot but cannot mutate plans or observations.

## Demo workspace and visual responses

The demo uses the existing operator API and separates its chat pointer from Control room. Pending request ID/body is retained in sessionStorage for same-tab retry; history stays on the server. New chats use an internal unspecified room placeholder. Linked session/history projections do not represent all Storage contents. Viewer formats/build/licenses are in [viewer README](../viewer/README.md).

Public runs expose safe delegation summaries, active_tool and bounded activity_events for actual model/tool attempts, including attempt links, timestamps and measured duration. Unknown historical/interrupted durations are not invented. These are operational events, not internal model reasoning or progress guarantees.

An assistant may emit a validated inspector-ui JSON block for metric/bar_chart/table artifacts. [artifacts.py](../src/ai_inspector/artifacts.py) defines shapes/limits; reply remains unchanged for older clients, while display_reply/artifacts are additive. Render typed DOM only, never model HTML/JS/SVG or executable URLs. Attribution text is not independent evidence and no widget adds a measurement capability.

## Drawing/photo minion

Set INSPECTOR_DRAWING_PHOTO_BASE_URL and a distinct INSPECTOR_DRAWING_PHOTO_SERVICE_TOKEN in runtime Secrets. The [adapter](../src/ai_inspector/drawing_photo.py) calls the [independent minion HTTP API](../../drawing_photo_service/README.md); this is separate from A2A registration.

The comparison tool accepts one explicitly selected drawing and photo, and caller-specified page for a PDF. It sends the full selected sources, not just a viewer crop; no arbitrary URLs, IFC/3D or automatic search across drawings. Source roles/hash/page and the current user question are pinned before submission. Typed argument/file/question/size limits are defined in the adapter. Retries preserve the operation UUID and exact POST body.

The adapter requests async admission. Running is not a verdict: a later user request can select drawing_photo_result for a task bound to the same chat (within the latest 200 runs). This is not autonomous background polling. Timeout/busy leaves the same Inspector request retryable; cold starts may require its exact retry. Findings retain report/evidence/provenance/audit and initial source bindings. Technical failure and inconclusive visual findings remain distinct; neither is certified approval. Minion database/model credentials remain private to that service.

## Verification boundary

Local fixtures cover model/tool validation, scopes, retries/restart, separate HTTP services/stores and cancellation fencing. They do not establish deployed connectivity, live Meta access, a real phone, inspection accuracy or a configured external Geometry service. Capture_task display in the actual phone, business messages, BIM measurements and automatic Geometry continuation require separate implementation/integration acceptance.
