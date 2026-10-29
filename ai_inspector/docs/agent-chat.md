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
   characters. Optional `file_ids` selects up to 8 files from this chat workspace; see below. Uploading/selecting IFC does not attach it as the session reference BIM or parse its geometry.
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

## Demo workspace and visual responses

`/ui/demo.html` is the user-facing workspace; Control room remains the operational
console. The demo uses the same operator credential and existing chat API. It shows
files/capture references and tasks linked by session_id to the current chat (its
current session plus the loaded run history), device status, service
configuration and persisted tool results. It does not expose hidden model reasoning
or claim a configured service is running. The conversation snapshot is calculated from these linked sessions, not invented
by the model. Older chat runs outside the loaded history page are not included.
Demo navigation hides the operational Control room and Backlog menu. Attach files
beside the message input; select context or manage Trash in Files. Capture references
are not a full Storage browser. The conversation selector restores prior workspaces. New chat is required to change room context.

Runs now include additive `display_reply` and `artifacts` fields. `reply` remains
unchanged for older clients. The assistant may emit a fenced `inspector-ui` JSON
block with `{ "artifacts": [...] }`. The server validates and extracts up to six
items; malformed blocks remain ordinary text. Supported types:

- `metric`: `title`, `value` (string or finite number), optional `unit`, `source`.
- `bar_chart`: `title`, `labels`, nonnegative finite `values`, optional `unit`,
  `source`; equal-length arrays, up to 20 bars.
- `table`: `title`, `columns`, `rows`, `source`; up to 8 columns and 30 rows,
  matching row widths, string/finite-number/null cells.

The demo renders typed DOM components; it never executes model HTML, JavaScript,
SVG, or embedded URLs. Source text is a model attribution, not independent proof:
visuals are labelled AI summaries and must be checked against original evidence.
No new model tools or measured geometry capability are added by this format.

The demo stores its chat pointer separately from Control room. An unfinished
request's ID and body are retained in sessionStorage for retry in the same tab;
refresh loads server history. Polling shows completed tool results while a run is
active; planned/in-flight tool names are not currently exposed by the API.

## Files, work plans and observations

Apply `scripts/sql/003_workspace.sql` with the DB owner before deploying. Runtime
access is SELECT/INSERT/UPDATE to three private workspace tables, scoped through
the authenticated chat; device credentials cannot access these APIs. Native SQLite
creates the same tables. No model tool can change plan or observation records.

- `GET /v1/chats` lists the latest 100 project conversations.
- `GET /v1/chats/{id}/workspace`: files, plan items, observations and deterministic
  progress counts. Files include uploads, saved Inspector replies/tool results as
  JSON, linked capture/Geometry summaries, and reference BIM. Outputs are not
  independently measured evidence. Up to 200 recent runs supply output listings.
- `POST .../files`: multipart `file_id` UUID + `file`; deployment limit 8 MiB.
  The same ID/content is retryable. Private immutable blobs reuse the project's
  content-addressed Storage store; filenames never become filesystem paths.
- `GET .../files/{file_id}/download`; `POST .../files/{file_id}/delete` or `/restore`.
  Delete moves the chat reference to Trash, not permanent blob deletion; history,
  recorded evidence and already-snapshotted model requests remain. Restoring a
  derived file does not mutate its underlying capture, BIM or agent run.
- Message `file_ids` are validated in this chat and frozen with the request.
  UTF-8 TXT/MD/CSV/JSON/IFC/XML/LOG text gets a bounded excerpt (16k stored, 10k sent
  per file); truncated excerpts are marked. PDFs/images/other opaque formats are
  metadata-only, not claimed as visually analysed. Files and notes are untrusted
  reference data. No markup is executed. An uploaded IFC is not a validated model.
- `POST .../plan/{item_id}`: title, location, reference, deleted, revision (0 for
  creation). Revision protects concurrent edits; archival excludes an item from
  the denominator. Each chat supports at most 200 planned items.
- `POST .../plan-import`: request_id UUID + selected file_id; CSV header `title`
  with optional `location,reference`, or a JSON array of equivalent objects.
  Bounded at 256 KB and 200 items; all rows validate before atomic import.
  Exact request-ID replay does not duplicate items. Imports create no observations.
- `POST .../observations/{observation_id}`: item_id, status (`confirmed`,
  `needs_review`, `not_done`), note, author, timezone-aware observed_at, file_ids.
  Stores evidence name/origin snapshots and operator provenance. IDs are retryable;
  future timestamps are rejected. Direct human observation can be recorded without
  a file; an AI output alone cannot confirm work. Names are self-reported under the
  shared operator credential, not per-person authenticated attestations.
- `POST .../observations/{id}/withdraw`: retains the record while withdrawing its
  effect. Latest observed_at (then record time/ID) wins among active observations.

Progress counts active planned items by their latest nonwithdrawn observation.
No observation means `no_data`, never `not_done`. Percentage is confirmed item
count / active item count with equal weights, not physical/financial progress or
certification. Plan and observation summaries are included as a bounded snapshot
in each new model request; the model may explain them or render widgets but cannot
mark items complete. Files in Trash cannot be newly selected, while old evidence
snapshots remain attributable. Concurrent edits return 409 without overwriting.
