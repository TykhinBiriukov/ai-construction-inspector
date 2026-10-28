# Web agent → iPhone assignment

Implemented first increment, 28 October 2026. The selected provider is **Meta Model
API**, model **`muse-spark-1.3`**, as confirmed by the project owner. This supersedes
the earlier Llama selection. Model availability and account access require a real
authorized request; local fixtures do not establish either.

The operator console at `/` includes **Ask Inspector**. Enter a room identifier
as capture context and send a message. A general question can finish without
capture. For capture the model selects `session_create`, which uses the existing
session/storage/device-event workflow. The phone receives the original task in
`session.capture_task = {text, source: "operator_request"}`. It initiates its own
authenticated connection and acknowledges the session; the agent cannot open
the camera remotely. The actual iPhone UI must display this new optional field.
That application change is not included or verified in this checkout.

## Server configuration

```dotenv
INSPECTOR_META_BASE_URL=https://api.meta.ai/v1
INSPECTOR_META_MODEL=muse-spark-1.3
INSPECTOR_META_API_KEY=
INSPECTOR_META_TIMEOUT_S=30
INSPECTOR_AGENT_MAX_STEPS=6
```

Set the real key only in the ignored server `.env` or process environment. Restart
the backend after configuration changes. Never include the key in the web bundle,
phone settings, chat or logs. `agent_model_configured` in `/v1/capabilities` means
settings are present, not that a live request succeeded. No runtime mock model is
substituted when credentials are missing.

The adapter calls `POST /chat/completions`, returns tool results with the matching
`tool_call_id`, and limits each response to one function call. Protocol sources
checked on 28 October: [Meta Chat Completions](https://dev.meta.ai/docs/protocols/chat-completions),
[Meta tool calling](https://dev.meta.ai/docs/tool-calling).

For Cloudflare, the entry reads the five settings above from Worker bindings;
the SQL adapter targets private chat tables. The chat migration is applied to
the configured Supabase project, and model transport passed real local
Workers/WASM checks with synthetic responses. Publication, the restricted
runtime connection and real Meta access remain unverified. The account owner
can finish using [the deployment handoff](cloudflare-agent-handoff.md) and
[the cloud status](cloudflare-deployment.md#agent-integration-local-preparation).

## Web API

All endpoints require the existing **operator** bearer identity and project
scope. Phones cannot read chats or invoke the model. IDs below are UUIDs.

1. `POST /v1/chats` with `{schema_version: "0.1", request_id, room_id}`.
   Inspector generates `chat_id` and pins the configured device/project and
   caller-selected room. This is not a validated IFC room. Use a new chat for
   different capture context.
2. `POST /v1/chats/{chat_id}/messages` with
   `{schema_version: "0.1", request_id, text}`. Text is nonblank, at most 8000
   characters. This increment accepts text, not BIM attachments.
3. `GET /v1/chats/{chat_id}?limit=30&before=...` returns the session link,
   saved user/assistant runs and nullable `next_before`. Runs within a page are
   chronological; `before` retrieves older runs. Polling does not call Meta.
   The console restores its current chat after reauthentication/reload using a
   local chat ID; tokens and message text are not stored in localStorage.

Create/message requests return **201** on first submission and **200** on replay.
Changed content under an existing request ID returns **409**. A successful HTTP
response confirms persistence; inspect `status`, `reply`, `error`, `session_id`
to determine what happened. Assignment does not mean the phone is online, has
acknowledged, or has started capture.

## Execution, retry and recovery

Tools are `session_create`, `session_get`, and `device_status`. Each takes `{}`;
device, project, room, operation IDs, endpoints and credentials come from server
state. Unknown names or invalid arguments fail before executing the command.
Only a small session status projection is sent to Meta; signed storage links,
artifact URLs and provider error bodies are excluded.

Each message has a durable run: `pending` → `running` → `completed`, `retryable`
or `failed`. History, planned commands, tool results and the reply are saved.
`retryable` includes missing settings, timeouts and temporary service errors.
Resubmit the **same message ID and text** to resume it. Completed/failed runs
replay without another model call. A chat with an unfinished run rejects a new
message with `chat_busy`. A browser timeout does not cancel the run or session.

One leased owner advances a run. Concurrent retries return the current run.
After process death, retry can reclaim it once the lease expires (model timeout
plus 60 seconds from the last checkpoint). Concrete commands and runtime
operation IDs are saved before execution. Uncertain session creation retries use
the existing backend receipt and same session ID. Stale owners cannot publish
model output after replacement. There is no background agent worker in this
increment: resume is driven by resubmitting the saved message. Capture/Geometry
workers remain independent.

`session_create` reuses the current assigned/capturing/submitted session. After
sealing or cancellation, an explicitly requested capture creates a linked
session using the same inspection and original task. Use a new chat for new
task context. The agent does not cancel sessions or add a Geometry dispatch
pipeline. The existing worker starts Geometry only after validating and pinning
complete sources.

## Verification boundary

Local regression checks live outside the checkout in `local-checks/inspector/`,
including model HTTP fixtures, simulated device channels, crash/retry/lease races,
schema validation and API scopes. Browser preview uses a labelled local fixture.
These do not verify live Meta access, a physical iPhone or external Geometry.
The Supabase chat schema/grants were verified separately; deployed Worker access
to those tables still needs verification. Business messages from the phone, BIM tools, automatic model
turns on Geometry completion and inspection verdicts are further increments.
