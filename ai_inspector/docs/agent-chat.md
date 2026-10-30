# Main inspector → tools and specialist inspectors

Chat/tool runtime implemented 28 October 2026; orchestrator, inspector registration
and individual inspector harnesses added 30 October 2026. The selected provider is **Meta Model
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

## Main inspector and specialist registration

`OrchestratorAgent` is the main inspector. It owns the user conversation, clarifies
requests, chooses direct tools or a registered specialist, and presents results.
`InspectorService.agent` uses this role. The former Python name `ChatAgent` remains
an alias; existing chat endpoints, message IDs, session behavior and saved history
are preserved. Historical runs without role metadata are returned as `agent_role:
"chat"`; new runs have `agent_role: "orchestrator"`.

This increment provides the harness and connection mechanism. **No specialist is registered
by default.** With an empty registry the model receives the direct session/device
and drawing/photo tools, plus an empty `registered_inspectors` catalog. The drawing/photo
adapter requires its separate service configuration. It must explain unavailable inspections
or request clarification, rather than claim that another inspector ran.

Register trusted, versioned profiles in the application's server composition:

```python
from ai_inspector.inspectors import InspectorProfile
from ai_inspector.service import InspectorService

service = InspectorService(settings, inspectors=(
    InspectorProfile(
        inspector_id="window_inspector",
        version="1",
        description="Review supplied evidence for window installation questions.",
        instructions="Review only the supplied window evidence. State limitations and missing evidence.",
        allowed_tools=("session_get", "device_status"),
        max_steps=4,
    ),
))
```

This is a registration example, not a shipped or validated window inspection
capability. Native applications can pass this service to `create_app(settings,
service=service)`; Cloudflare composition can pass profiles to its existing
`InspectorService` construction. Registration is application code, not a web/model
API, environment prompt loader or provider selection. Inspector IDs are unique
lowercase identifiers; `orchestrator` is reserved. Increase the profile version
when changing its responsibility, instructions, allowed tools or budget. Profiles
are revalidated at registration, including copies constructed by application code.

When profiles are registered, the main model also receives
`delegate_inspection({inspector_id, task})`. The allowed IDs and descriptions come
from the server registry; backend validation rejects unknown IDs, blank/oversize
tasks and extra arguments. The orchestrator selects by responsibility and supplies
a self-contained task, including relevant prior discussion when needed.

Each registered inspector has its own harness: instructions, allowed tool set,
bounded model/tool loop, conversation history and execution state. The shared
`AgentHarness` engine is used by both the orchestrator and inspector runs; each
instance advances its own state. A specialist can request a permitted tool, read
its result, request another tool and then return a report. The main inspector waits
for that run and continues its own loop; the two step counters are independent.

The implemented inspector tool allowlist is `session_get` and `device_status`,
both read-only and scoped by backend chat/project/device state. A profile opts into
either, both or neither through `allowed_tools` (default empty). Tool names and
arguments are checked before planning, and permissions are checked again before
execution. `max_steps` defaults to 6 and must be between 1 and 32; it bounds
successful model turns, including the final report. Tool errors can be supplied
back to the inspector; a temporary error leaves its exact pending command resumable.
New domain tools require actual backend schemas/handlers and verification.
`session_create`, nested delegation and arbitrary code/HTTP/SQL are not inspector
tools. The main inspector retains capture ownership; Geometry still uses the same
existing validated-source workflow.

An inspector receives its own role prompt, the original message's frozen file
excerpts/selection/preview, the delegated task and a small session projection.
Each run retains its full own message/tool history. New tasks also receive up to
six completed task/report pairs from the same inspector and exact profile within
the last ten parent chat runs, including earlier delegations in the current run.
This memory survives restart and is isolated by chat and profile configuration.
Source child run IDs are retained as `history_run_ids`. Failed/incomplete reports,
other inspectors' histories and other chats are not automatically included.
Prior reports are historical context; current session/device facts require tools.
The inspector does not receive full orchestrator history, other role instructions
or storage credentials. A report remains advisory evidence, not verified dimensions,
validated BIM or construction compliance.

The parent persists the inspector's profile and initial state before starting it.
Child state includes `messages`, bounded prior `history`, `steps`, `pending_model`,
`pending_tool`, tool results, activity events, status and report. Before each model
request, both harness roles freeze the exact messages and tool definitions in
`pending_model`. Before executing a tool, they persist its concrete command and
server-generated operation ID. Child checkpoints live in the existing parent JSON
record in `agent_runs`, under the same lease and project scope; no SQL migration
or second dispatch queue is required. Timeout/restart reuses child/operation IDs,
model request bodies, completed tool results and unfinished commands.
Uncertain provider responses may require another model request: there is no claim
of exactly-once provider execution or billing. A completed child report is reused
without another specialist call, including after a crash before the parent consumes
it. An unfinished delegation waits with retryable `inspector_unavailable` if its
saved profile is missing or changed; restore the exact saved profile to resume.
Stale owners cannot save child model/tool results or advance the parent. Old
completed single-response delegations remain replayable; old unfinished records
gain loop state without changing their saved initial messages and empty tool set.

The local profile path above remains supported. Remote inspectors use the A2A
service path below: they own their checkpoints and worker independently of the
orchestrator. Both paths keep the selected Meta/Muse Spark adapter.

Runs expose additive `delegations` summaries (identity, task, status, report, safe
error, timestamps, allowed tools, own step limit/count, history source IDs, pending
tool name, tool results and activity events). Internal prompts and raw model
messages/history are excluded. The
existing `tool_results`/activity trail records `delegate_inspection`, then the main
inspector receives the report and continues its bounded turn. `agent_max_steps`
limits successful orchestrator model turns; each selected inspector has its own
`max_steps` loop budget. A failed inspector run becomes a tool error for the main
inspector; a retryable inspector error keeps the parent resumable.

## Remote inspector services — A2A HTTP+JSON polling

Implemented 30 October 2026. The wire profile is pinned to **A2A 1.0**, with the
required application extension `urn:ai-inspector:delegation:v1`. It supports
nonblocking task admission, status polling, listing and cancellation. SSE,
webhooks, gRPC and JSON-RPC are not enabled. The Agent Card advertises these
limits; this is an inspection delegation profile, not an unrestricted chat API.
Reference: [A2A 1.0 specification](https://a2a-protocol.org/v1.0.0/specification/)
and [canonical protocol definitions](https://github.com/a2aproject/A2A/blob/v1.0.0/specification/a2a.proto).

The orchestrator uses the same `delegate_inspection` tool. For a remote registry
entry it persists the exact outbound body before HTTP, then invokes the fixed
service address. It stores a remote task/context link and accepted report, without
executing or copying the remote harness's private checkpoint. The service owns its
model loop, tool commands/results, history, retries and task worker.

### Register a deployed inspector with the orchestrator

Set `INSPECTOR_REMOTE_AGENTS_JSON` on the orchestrator; the default is `[]`.
Example connection configuration (the example does not implement window analysis):

```json
[
  {
    "inspector_id": "window_inspector",
    "version": "1",
    "description": "Review supplied window installation evidence.",
    "base_url": "https://window-inspector.example",
    "token_env": "WINDOW_INSPECTOR_TOKEN",
    "tool_token_env": "WINDOW_INSPECTOR_TOOL_TOKEN",
    "allowed_tools": ["session_get", "device_status"]
  }
]
```

The two named credentials are separate server Secrets, never values inside this
JSON. Native configuration reads them from the process environment or ignored server
`.env` (process values win); Cloudflare reads
them from Worker bindings. They must differ from operator/device/model/storage
credentials and from each other, including credentials of other inspector registrations.
Credential references must also be distinct. `allowed_tools` is the backend grant; the remote
profile must independently permit a tool before requesting it. With no backend
tools, omit `tool_token_env` and use `allowed_tools: []`.

Only server configuration selects endpoints. HTTPS is required except for local
loopback HTTP. Redirects are not followed, response size is bounded to 2 MiB and
provider/HTTP error bodies are never copied into reports. The model sees inspector
IDs/descriptions, not service addresses or credentials. Version/address changes
hold unfinished work until the saved registration is restored; Secret rotation
under the same configured reference is supported.

### Run an inspector as a separate service

The deployable ASGI factory is `ai_inspector.a2a_service:create_app_from_env`.
Set these variables on the inspector process:

| Setting | Meaning |
|---|---|
| `INSPECTOR_AGENT_PROFILE_JSON` | InspectorProfile: ID, version, description, instructions, allowed_tools and max_steps |
| `INSPECTOR_AGENT_CALLERS_JSON` | Secret JSON array of `{client_id, project_id, token}`; use the value of the orchestrator's task credential |
| `INSPECTOR_AGENT_PUBLIC_URL` | This service's configured HTTPS base URL; loopback HTTP is allowed for local checks |
| `INSPECTOR_AGENT_DATA_DIR` | Persistent directory containing this service's `tasks.sqlite3` |
| `INSPECTOR_META_BASE_URL`, `INSPECTOR_META_MODEL`, `INSPECTOR_META_API_KEY`, `INSPECTOR_META_TIMEOUT_S` | Model adapter settings; the key remains a server Secret |
| `INSPECTOR_AGENT_BACKEND_URL` | Existing Inspector backend URL, required when backend tools are enabled |
| `INSPECTOR_AGENT_BACKEND_TOKEN` | Secret matching the orchestrator's separate tool credential, required with backend tools |

For example, the profile JSON can be:

```json
{
  "inspector_id": "window_inspector",
  "version": "1",
  "description": "Review supplied window installation evidence.",
  "instructions": "Review only supplied evidence; state limitations and missing evidence. Never invent measurements.",
  "allowed_tools": ["session_get", "device_status"],
  "max_steps": 6
}
```

Configure callers using the real service credential in the deployment's Secret
storage. A caller is bound to one project. Do not put actual caller JSON, API keys
or tool credentials in source control. The factory reads process environment and
does not automatically load the backend's `.env` or expose its operator routes.

From the backend directory, a local service can run as:

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn ai_inspector.a2a_service:create_app_from_env --factory --host 127.0.0.1 --port 8100
```

Deploy this native ASGI process behind an HTTPS ingress with a persistent volume.
The first inspector task store is native SQLite with atomic admission/leases;
run one inspector deployment per persistent SQLite volume. A standalone inspector must not use an
ephemeral Cloudflare filesystem for task persistence. A PostgreSQL task-store
adapter for horizontally deployed inspector services is a further increment.
The orchestrator's existing SQLite/PostgreSQL/Workers persistence is unchanged;
no new shared SQL migration is needed for remote delegation.

### Wire contract and recovery

The inspector exposes `GET /.well-known/agent-card.json` publicly. All task routes
require the dedicated caller bearer, `A2A-Version: 1.0`, and
`A2A-Extensions: urn:ai-inspector:delegation:v1`:

- `POST /message:send` accepts `{message, configuration, metadata}` and returns
  HTTP 200 with `{task: ...}`. `message` has a UUID `messageId`, `role: ROLE_USER`
  and one text part. `configuration.returnImmediately` must be `true`.
- `metadata.inspector` pins schema version, inspector ID/version, operation ID,
  project/chat/parent/delegation IDs, a small server context and frozen user
  reference content. Only supplied text and inline PNG/JPEG/WebP previews are
  accepted; the service does not fetch caller-supplied image URLs.
- `GET /tasks/{id}` reads status/artifacts and optional bounded public message
  history (`historyLength=0` excludes it). This never advances the model.
- `GET /tasks` lists caller-scoped tasks with `pageSize`, `pageToken` and optional
  `contextId`. `POST /tasks/{id}:cancel` cancels this agent task and fences its
  worker; it does not abort capture or cancel a Geometry job.

The domain operation ID is also the message ID. Admission commits before returning;
duplicate caller/operation/body returns the same task, while changed content is a
409 conflict. The service generates task IDs and a caller/project/chat/profile
context ID. New tasks receive up to six completed task/report pairs from their
last 50 context tasks; full private message/tool history remains in the task store.
New-task admission is supported; interactive messages to an existing `taskId`
and blocking/streaming execution are outside this polling profile.

Both services retain their own leases. A model/tool request is saved before IO;
crash recovery retains its IDs and exact request. Transient service/model errors
back off, with a limit of 12 transient inspector execution failures. Missing model
configuration or a changed saved profile waits for restoration without consuming
that retry budget. Uncertain model requests may repeat; provider execution/billing
is not claimed to be exactly once. Cancellation invalidates the task lease so a
late model/tool result cannot publish a report.

Completed reports use one text Artifact. The orchestrator checks task/context
identity, all delegation binding IDs and the frozen request SHA-256 before accepting
it. Captured source identity is fingerprinted when available. It does not accept
a result for another task, project, session context or changed input.

Native backend lifespan starts a remote-task poller in addition to the existing
capture worker. A delegated chat run reports `auto_resume: true` while unfinished;
it resumes without a browser or resubmission. Only remote workflows gain this
driver; ordinary legacy runs keep their existing explicit resume flow. Cloudflare
`process()` now advances capture and remote agent work; the existing frontend
minute cron can drive it after deployment. Actual timing depends on that trigger,
not just a registry's `poll_interval_s`. No cron/account/deployment was changed by
this increment. The poller uses existing run JSON/lease fields and project-scoped
SQL; an indexed general agent queue remains a scaling increment.

The backend's `POST /v1/inspector-tools/{inspector_id}/{tool_name}` is a dedicated
service boundary. The separate tool credential is bound to the registered inspector,
project and still-active saved delegation. Operator/device credentials do not work
there. Device/session/operation identities come from that persisted grant, not
model arguments; the two implemented tools are read-only status projections.

Local checks cover two independent apps/databases, a real loopback TCP run with
both background workers, lost admission replies, restart, duplicate admission,
scopes, frozen input/result identity and cancellation fencing. Workers Fetch GET
and model transport are checked separately. These checks do not establish public
service deployment, live Meta access, inspection accuracy or live Hyperdrive access.

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

Direct tools are `session_create`, `session_get`, and `device_status`. Each takes `{}`;
device, project, room, operation IDs, endpoints and credentials come from server
state. Unknown names or invalid arguments fail before executing the command.
Only a small session status projection is sent to Meta; signed storage links,
artifact URLs and provider error bodies are excluded.
`delegate_inspection` is additionally available only when server profiles are registered,
as described above; it takes a registered inspector ID and a task.

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
model output after replacement. General legacy run recovery uses resubmitting the
saved message; registered remote workflows also have the automatic poller described
above. Capture/Geometry workers remain independent.

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
turns on Geometry completion and validated specialist inspection capabilities are
further increments. Local delegation fixtures cover two illustrative roles; neither
is enabled in the product registry or evidence of live inspection accuracy.

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
refresh loads server history. Polling shows completed tool results, persisted activity
events and the nullable `active_tool` name while a run is active. Planned command
arguments and internal model messages remain private.

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

### Project document workspace and selections

The demo centers an IFC/PDF/image viewer with a resizable Files rail and Inspector
conversation panel. Panel widths and UI text scale are local browser preferences.
`Dialogue:` selects chat scope. Activity shows actual agent/tool events; backend
service configuration and conversation snapshot are omitted from this user view.
Work-plan data is retained but its controls remain hidden in the demo.

Opening a file does not send it to the model. Selecting a drawing region or IFC
element attaches a visible context chip to the next message. One drawing region or up to 20 IFC elements per
message; use Multi-select or Shift-click to add/remove elements, clear with ×. PDF/image selection contains normalized `[x,y,width,height]`
(top-left origin, current page), file ID, and a bounded JPEG preview. IFC selection
contains STEP express_id, GlobalId, basic properties and the highlighted model view.
The server binds the IFC STEP identity to the stored source and rejects mismatches.
Preview/properties are browser-provided reference data, not trusted measurements.
Image previews go to the existing Meta model only when the user sends the message.
No external viewer service receives files. Imported files remain private in Storage.

`POST /v1/chats/{chat_id}/messages` accepts optional `document_selection`:
`file_id`, `kind` (`region` or `ifc`), `label`, optional `page`, `bbox`, `express_id`,
`global_id`, `properties`, optional `elements` (up to 20 IFC identity/property records), `image_data` (JPEG data URL, <=350000 characters).
The file must also be in `file_ids` and available to this chat/operator. Selection
and preview freeze with the durable request for exact retries; public run history
contains reference metadata but not the image payload. No DB migration is required.
Unselected PDF/image files still supply metadata only. A crop is not the whole page.
One model-rendered view is not a measurement, a scan, or a compliance determination.

Viewer sources: `ai_inspector/viewer/`. Build with `npm ci --ignore-scripts && npm run build`
from that directory; dependencies are pinned by package-lock.json. GitHub Actions
builds automatically for viewer changes. Generated assets are same-origin in
`web/vendor/`; the Python runtime excludes vendor assets. CSP permits same-origin
workers and WASM compilation only; it does not allow arbitrary JS eval or remote CDNs.
Supported: IFC displayable geometry, PDF pages, PNG/JPEG/WebP. Native DWG/RVT are not
supported; export to PDF/IFC. IFC rendering is capped at 1.5 million vertices. Existing
8 MiB upload limits apply. The browser may reject more complex or malformed models.

The demo shows Capture Devices as an always-open list (currently the single
configured device); multi-device backend registration is not implemented by this UI.
No last-seen or connection counters are displayed. The Room input is removed from
the composer; new chats use internal room_id `unspecified`, not a claimed real room.
File context chips can be removed independently of deleting the stored file. Clearing
a document selection also unchecks its file for the next message. Upload success
does not produce a persistent banner.

Activity is a compact current-action button opening a modal timeline of loaded chat
runs and completed tools, without raw JSON. Public runs expose `active_tool` as only
the pending validated tool name, or null. The UI polls at its existing interval; short
tool calls may only appear in completed history. No invented progress or internal
reasoning is shown. Reduced-motion preferences disable decorative animations.

Activity events are persisted with each run (`activity_events`, up to 64 entries).
Each model/tool attempt has event_id, operation_id, attempt/retry_of, status,
started_at, finished_at and measured duration_ms. Tool results bind by event_id.
Retryable failures become waiting; a resumed orphaned running event becomes
interrupted with unknown duration. These are operational events, not model reasoning.
The demo shows the latest four steps beside the document and full history in a modal.
Only fresh running events animate actual tool links and the run's selected document
context. Polling is every five seconds; quick calls may first appear completed.
Historical calls without timestamps have no invented duration. Result links open
available selected files/regions/elements or capture/device context.

## Drawing/photo minion (30 October 2026)

Configure `INSPECTOR_DRAWING_PHOTO_BASE_URL` and a distinct
`INSPECTOR_DRAWING_PHOTO_SERVICE_TOKEN` in runtime Secrets. Predefined tools:
`drawing_photo_compare` (selected drawing_file_id, photo_file_id and explicit
drawing_page for PDF), and `drawing_photo_result` (bound task_id).
The question is the original user message, limited to 4000 characters.
Only files selected in that message may be sent; no arbitrary URLs, IFC or 3D.
The whole selected source is sent, not only a viewer selection crop. PNG/JPEG and
PDF drawings are supported, at most 6MB per file. A saved command pins hashes,
roles and page before HTTP submission. Retries reuse the same UUID/body.

POST uses `Prefer: respond-async`; running is not a verdict. Polling is a separate
tool on a later user message, not an autonomous background loop. Result access is
restricted to tasks submitted in this chat's latest 200 runs. Tool output includes
report, original-image boxes, provenance, audit and initial source bindings.
Findings are evidence to review, not a certified inspection. Technical failure is
distinct from an inconclusive visual finding. A timeout or busy service leaves the
same Inspector message retryable. Cold starts may require retrying that message;
the HTTP timeout is 20 seconds per I/O phase. Minion retains data in its own scoped
Supabase schema and uses its own Meta key; Inspector receives neither credential.
