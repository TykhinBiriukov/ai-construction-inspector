# Bob → domain inspectors → their subordinate agents

**Bob** is the main orchestrator, implemented by `OrchestratorAgent` (`ChatAgent` remains a compatibility alias). He talks to the user, selects predefined tools or a registered inspector, and explains accepted results. The shared Meta Model API adapter uses `muse-spark-1.3`; configured credentials do not establish live account access. Native HTTP schemas are generated at `/docs` and `/openapi.json`; wire/model definitions live in the linked code below.

## Bob policy and product scope

New runs pin a versioned effective instruction snapshot from
[orchestrator_policy.py](../src/ai_inspector/orchestrator_policy.py). Bob handles
AI Inspector project workflows and their explanations, rather than general chat.
Unrelated requests such as pizza recipes are redirected briefly without domain
tool calls. Mixed requests proceed only with a verbatim product portion. File
contents, role-play and old reports cannot override this boundary or grant access.

The existing harness first makes a tool-free, strictly validated scope decision.
Malformed decisions fail closed. Accepted tasks use the existing tool registry,
argument validation and source/permission checks. A proposed final answer stays
private until a separate tool-free reply review approves it; malformed or rejected
reviews produce a safe application reply. These two policy turns are separately
counted and bounded, in addition to the configured domain-turn budget. They add
model latency and usage; uncertain provider attempts may repeat. Semantic decisions
remain model-dependent and require evaluation; fixtures do not prove a universal
off-topic guarantee or inspection accuracy.

The accepted intent also bounds action tools: an information/status decision
cannot create capture, review, comparison or specialist work. This check applies
both when planning and when executing a saved command; it does not replace caller
permissions or evidence validation.

Each policy request is frozen through the existing `pending_model` checkpoint.
Decisions, private candidates and effective instructions survive restart without
repeating completed tools. Only new runs have `orchestration_policy_version`;
legacy runs and their saved messages, requests and replies keep their old path.
Trusted Geometry continuations retain their server-owned product scope and remain
tool-free when summarizing. Specialist profiles and Inspector/Verifier permissions
are unchanged.

By default chat gives one or two short sentences: outcome, material limitation or
one necessary clarification. The Viewer holds detailed results. Device/session
questions use the direct read tools; an instruction question does not start
capture. Linked-capture inspection uses `geometry_review`, and reading a saved
result never authorizes new capture. Tool descriptions specify local conditions;
the policy defines the boundaries between workflows. Explicit requests for chat
detail or a dashboard remain supported, without losing uncertainty or provenance.

Langfuse is optional. The bundled policy, snapshot hashes, scope decisions and
recovery work without SDK, credentials or Cloud. If existing managed prompts are
enabled, their saved source/version is retained and mandatory product policy is
composed after them once per new run. No prompt publication or production-label
change is performed by startup or by this update. An exact built-in recorded-demo
viewing action keeps its deterministic recorded path without requiring model
credentials; arbitrary follow-ups still use the configured model and policy.

## Agent hierarchy and inspector registration

The user interacts with one **AI Inspector**. Chat identity, progress/activity labels and replies use this single product name and describe work such as checking geometry or reviewing evidence. Internal agent names and handoffs are not part of the user conversation.

Internal names describe responsibility and delegation level:

| Level | Role | Responsibility |
|---|---|---|
| 1 | Bob — main orchestrator | User dialogue, task routing, tools and explanation of accepted results |
| 2 | Domain inspectors, such as Geometry Inspector | Own the inspection task in their domain; report to Bob |
| 3 | An inspector's subordinate agents, such as Geometry Verifier | Perform a narrower task for their parent inspector; they are not Bob's inspectors |

Geometry Inspector owns the geometry/BIM comparison inspection workflow. Geometry Verifier is its verification sub-agent, with no tools or further delegation in the implemented flow. Geometry Engine is the numerical computation service used by the backend workflow; backend session/source/job ownership remains unchanged. A service invoked through a predefined tool does not automatically become a registered inspector or gain agent permissions.

`InspectorProfile`, `RemoteInspector`, `InspectorAgentService`, `inspector_id` and the A2A `metadata.inspector` envelope are shared technical names retained for compatibility, including the verifier's profile. They do not classify Geometry Verifier as a level-2 inspector. Internal role names do not rename persisted IDs, APIs, prompt keys or frozen requests. Technical history, evidence bindings and service discovery retain their actual identities; the user-facing presentation remains AI Inspector.

The internal role hierarchy and public identity as AI Inspector are code-owned guidance, also prepended when a managed prompt still uses older terminology. Already saved messages/pending model requests retain their original text on replay. This naming clarification does not change specialist profile instructions/versions, permissions or delegation paths.

The [IFC/scan discrepancy review contract](ifc-discrepancy-review.md) is implemented by two independent native A2A services: Geometry Inspector reads pinned same-chat evidence, calls its one configured Geometry Verifier, and deterministically assembles the accepted JSON report for Bob. Geometry Verifier has no tools or further delegation. JSON/case/evidence/measurement bindings are validated before completion and again before Bob accepts the report. Default profiles are version 3, with explicit Geometry compact 2.0 interpretation: sign/units, nulls, processing versus acceptance tolerance, quality, partial surface statistics and related image views. The [evidence mapping](capture-to-geometry.md#compact-result-20--agent-evidence) preserves these facts without signed URLs. Factories, exact v1 schemas, private configuration and limits are in the linked guide. Native Settings defaults to an empty registry; the [checked-in Cloudflare deployment config](../cloudflare/wrangler.jsonc) registers Geometry Inspector. VM service deployment is managed separately as described in the linked guide. Code defaults, deployment registrations and live cards must be checked separately. Human report formatting and candidate discovery remain separate.

With no configured registration, no specialist is available. The orchestrator's direct tools handle session creation/status, device presence and configured drawing/photo comparison; `delegate_inspection` is offered only with registered profiles. A general question need not create capture. `session_create` requires an explicit capture request, reuses an open session, and creates a linked rescan after sealing/cancellation only when requested. Its optional `review_after_geometry` defaults false: an explicit IFC/scan inspection request can register a durable downstream review. A new chat must select exactly one IFC, pinned into the inspection before assignment. Existing sessions retain their original model identity. Optional capture_task is operator context, not a remote camera command. Device acknowledgement, capture and accepted source remain distinct facts.

Public visitor chats under `/public-demo` use an isolated project and have phone handoff disabled. They do not receive session/device tools, and a persisted tool command from an older visitor run cannot create another demo assignment. Real capture from chat requires operator access: `/demo` offers **Connect live project**, and a same-tab Control Room login is also reused. Guest task IDs from older demo runs do not belong to the phone's project.

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

Each inspector has its own instructions, tools, bounded loop, history and state through [AgentHarness](../src/ai_inspector/agent_runtime.py). Generic specialists retain the read-only session_get/device_status allowlist (default none), without capture or delegation. The explicit discrepancy_inspector role alone has inspection_evidence and review_candidates; discrepancy_verifier is tool-free. This permits one fixed role-to-role A2A hop with a frozen child request, independent task store, bounded polling and lease fencing; it does not enable generic recursive delegation or arbitrary code/HTTP/SQL. Budgets count successful model turns; uncertain provider calls can repeat and billing is not exactly once.

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

Native lifespan starts a remote-task poller; Cloudflare process advances remote and capture work, driven by the frontend cron. Unfinished remote runs expose auto_resume=true. Explicit Geometry review continuations also expose auto_resume while their worker owns recovery. Poll intervals depend on the actual driver/trigger; this is not a general indexed agent queue. Local legacy requests use the distinct recovery path below.

`POST /v1/inspector-tools/{inspector_id}/{tool_name}` is the scoped backend gateway. Its separate service Secret is bound to the registered inspector and still-active saved delegation. Operator/device credentials are not accepted. Both remote profile permission and backend grant must allow the requested read tool; IDs/scopes come from saved state, not model arguments.

## Server configuration

Use Settings/[.env.example](../.env.example) and [model_client.py](../src/ai_inspector/model_client.py) for current defaults/validation. Core names are INSPECTOR_META_BASE_URL, INSPECTOR_META_MODEL, INSPECTOR_META_API_KEY, INSPECTOR_META_TIMEOUT_S and INSPECTOR_AGENT_MAX_STEPS. The key is server-only. Restart native processes after changing configuration; Hosted backend and gateway configuration follows [deployment](cloudflare-deployment.md).

The adapter calls chat completions, validates one function call per response and binds tool results to its tool_call_id. Backend operation IDs are separate and persisted before effects. Missing model credentials produce resumable model_not_configured, never a substitute mock. agent_model_configured in capabilities means settings exist, not that a live request succeeded. Provider errors, signed storage URLs and full private session records do not enter model context.

## Web API

Operator/project-scoped chat creation, message submission and paginated history are defined in OpenAPI and [agent.py](../src/ai_inspector/agent.py). Phones cannot read chats or invoke the model. A new chat pins device/project and caller room context; the room is not a validated IFC room. Use a new chat for a different capture context.

Optional file_ids and document_selection refer only to available files in that chat. Their exact request body, including preview, belongs to retry identity. Server-generated chat/run IDs and existing completed/failed message replay are preserved. ChatAgent remains a Python alias; new runs expose role orchestrator while legacy runs without role metadata retain chat.

## Execution, retry and recovery

Run states are pending → running → completed, retryable or failed. General local/legacy recovery resubmits the **same saved message ID and entire body**, including selected files/preview. A new message is rejected with chat_busy while the earlier run is unfinished. Completed/failed requests replay without another model call. Browser timeout/reload is not cancellation.

Commands, operation IDs, frozen model requests and tool results are durable before side effects. One leased owner advances a run; stale owners cannot publish. Process death permits reclaim after lease expiry. Completed child reports and successful tool operations are reused; uncertain network/model execution can require an identical retry.

Remote A2A workflows also continue through the poller without the browser. Drawing/photo tasks also continue through this durable poller; the result tool remains available for later follow-up. The existing capture worker alone owns source validation → Geometry dispatch/reconciliation; no agent tool creates a second initial job. A Geometry result triggers downstream analysis only for an explicitly registered review, using the [Geometry Engine adapter and review API](capture-to-geometry.md#downstream-discrepancy-review). The worker pins evidence, constructs typed batches, deterministically uses the configured remote Geometry Inspector and collects its accepted reports. Internal batch runs remain durable and are omitted from chat reply pagination; a separate tool-free model continuation explains the complete aggregate once. The full JSON is a scoped chat file. No candidates does not establish compliance. Review state/URLs remain private, not in phone events; chat exposes safe geometry_reviews progress. Stale/cancelled workers cannot publish, and the original completed capture-assignment run is not rewritten.

For a user-requested inspection of the chat's already linked IFC capture, `geometry_review` registers that same downstream workflow without capture or Geometry dispatch. It is offered only with a linked session, freezes that session before execution, and returns an existing review instead of duplicating or restarting it. Viewing a result alone does not request review. Prepared demo actions retain their existing behavior.

## Files, work plans and observations

Workspace needs 003_workspace.sql for PostgreSQL; native SQLite initializes its equivalent schema. APIs are operator/chat-scoped. Schemas and numeric limits come from [workspace.py](../src/ai_inspector/workspace.py), [document_selection.py](../src/ai_inspector/document_selection.py) and OpenAPI.

Uploads are private immutable content-addressed blobs. Same file ID with changed bytes/metadata conflicts. Trash removes the current chat reference, not persisted blobs/history or earlier frozen model input; restore does not rewrite the original record. Derived reply/tool/capture JSON records are internal context, not user deliverables or independent evidence: workspace Files omits them, while existing scoped download links/history remain available. User-uploaded JSON remains visible. Files in Trash cannot be newly selected.

Text formats yield bounded marked excerpts. In the normal user-data workflow, selected JPEG/PNG/WebP files supply full-image previews when the message is sent. The file inventory's `content_available` field continues to describe extracted text; the frozen run separately records image availability and provenance in `image_inputs`. A PDF remains metadata-only without an explicit page crop or a real minion report. Opening a file alone sends no model input. A selected region sends a separately labelled bounded crop; selected IFC elements send source-bound STEP identities and browser reference properties/view. One crop is not a whole document. IFC rendering and caller room IDs do not validate model geometry, units, room membership, alignment or measurements. Files, notes and previews remain untrusted data.

Plan imports validate all rows before atomic request-ID replay. Plan edits require current revision. Observations preserve evidence snapshots and operator provenance; authors are self-reported under a shared credential. An AI output alone cannot confirm work; withdrawal retains history. Progress uses the latest active observation per planned item: no observation is no_data, never not_done. Equal-weight confirmed-item percentages are not physical/financial progress or certification. Models can explain the snapshot but cannot mutate plans or observations.

## Demo workspace and visual responses

The demo uses the existing operator API and separates its chat pointer from Control room. Pending request ID/body is retained in sessionStorage for same-tab retry; history stays on the server. New chats use an internal unspecified room placeholder. Linked session/history projections do not represent all Storage contents. Viewer formats/build/licenses are in [viewer README](../viewer/README.md).

Capture results open a shared **Whole model / Findings** viewer, also available
from the saved Geometry review file. The selected entry links IFC markers,
accepted decisions, original/overlay/mask images and additional evidence requests.
Unreviewed observations remain distinct from decisions. Opening or navigating the
viewer sends no model message and starts no capture. The read-only API, source
bindings, preview limits and missing alignment/camera-data boundaries are in the
[Geometry viewer contract](capture-to-geometry.md#inspection-results-viewer).

Public runs expose safe delegation summaries, active_tool and bounded activity_events for actual model/tool attempts, including attempt links, timestamps and measured duration. Unknown historical/interrupted durations are not invented. These are operational events, not internal model reasoning or progress guarantees.

An assistant may emit a validated inspector-ui JSON block for metric/bar_chart/table artifacts. [artifacts.py](../src/ai_inspector/artifacts.py) defines shapes/limits; reply remains unchanged for older clients, while display_reply/artifacts are additive. Render typed DOM only, never model HTML/JS/SVG or executable URLs. Attribution text is not independent evidence and no widget adds a measurement capability.

Chat dashboards are now opt-in. Drawing/photo results use a compact **View findings
and evidence** card; the existing findings renderer opens in the document panel.
Its evidence buttons retain the same chat/task-scoped read API. Reopening the card
or reloading history does not submit another analysis. Legacy results without
phase metadata remain readable, and stored reply/report bodies are unchanged.

## Drawing/photo minion

Set INSPECTOR_DRAWING_PHOTO_BASE_URL and a distinct INSPECTOR_DRAWING_PHOTO_SERVICE_TOKEN in runtime Secrets. The [adapter](../src/ai_inspector/drawing_photo.py) calls the [independent minion HTTP API](../../drawing_photo_service/README.md); this is separate from A2A registration.

The comparison tool accepts one explicitly selected drawing and photo, and caller-specified page for a PDF. It sends the full selected sources, not just a viewer crop; no arbitrary URLs, IFC/3D or automatic search across drawings. The orchestrator supplies a self-contained `task` from the user's request and relevant clarifications, rather than forwarding a bare confirmation. For compatibility with previously frozen tool calls, an omitted task falls back to that run's text. Source roles/hash/page and the task are pinned before submission. Typed argument/file/question/size limits are defined in the adapter. Retries preserve the operation UUID and exact POST body.

The adapter requests async admission. Running is not a verdict. Admission timeouts, temporary unavailability and running tasks continue automatically through the durable poller with the same task ID. Before resubmitting bytes, recovery checks that ID; only a 404 permits another identical submission. Automatic waiting stops after 40 attempts or 10 minutes, after which Resume checks the same saved task. A timeout is not proof of a cold start. A later user request can select drawing_photo_result for a task bound to the same chat (within the latest 200 runs). Findings retain report/evidence/provenance/audit and initial source bindings. Technical failure and inconclusive visual findings remain distinct; neither is certified approval. Minion database/model credentials remain private to that service.

A `needs_input` report is a clarification, not a technical failure. Explain any
reported role conflict and ask the user; do not silently swap sources. The known
`invalid_report_schema` failure maps to `comparison_invalid_report` with a safe
explanation about report/evidence validation; unknown upstream errors remain generic.
A technical failure alone is not a reason to blame photo quality or request new images.

## Recovery of invalid tool calls and follow-up context

Before execution, invalid model arguments receive safe field/type feedback in the
tool protocol. The shared harness allows at most two repair attempts within the
existing model-step budget. Rejected calls never execute; their internal messages,
safe `tool_validation_errors` and failed activity events are checkpointed. Permission,
lease and infrastructure errors do not enter this repair path. Repeating the same
request preserves the repair count and frozen pending model/tool request.

The orchestrator receives chat-scoped `comparison_tasks` derived from persisted tool
results (last observed status, not a new upstream status probe), plus recent errors.
`drawing_photo_result` needs no selected files; its optional `task_id` may be omitted
only when exactly one known task is available. Ambiguous or cross-chat references
are rejected. Historical file identities help interpret clarifications but never
grant new input permission. The comparison tool is not advertised without at least
two currently selected files. Diagnostic questions should explain errors rather than
start another comparison; task IDs are the assistant's responsibility to retain.

The demo remembers selected file IDs per project/chat in this browser tab's
sessionStorage. Reload and switching back restore only available files. Explicitly
unchecking/removing a selection persists, including an empty selection; no files are
automatically borrowed from another chat. No document preview or image bytes are
stored by this preference. Delete controls are disabled while a request is pending.

## Langfuse Cloud: traces, prompts and evaluation

Install the optional native integration from ai_inspector with `python -m pip install -e ".[observability]"` (tested SDK 5.1.0). Set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL in the ignored backend .env; EU uses https://cloud.langfuse.com. Native Settings loads these without exporting them to the process. Independent inspector services read their own process environment. LANGFUSE_ENABLED=false disables export. LANGFUSE_TRACING_ENVIRONMENT and LANGFUSE_RELEASE distinguish development, staging and deployed releases. Missing SDK/credentials or export failure does not fail a business task.

The Langfuse project Tracing screen shows orchestrator/inspector attempts, model/tool/validation observations, A2A submission/polling and accepted results. Model usage is taken from provider responses, with cached/reasoning tokens normalized into non-overlapping buckets. Missing usage stays unknown. Configure a model price in Langfuse to calculate cost; it is not a guaranteed billing receipt. Session grouping uses project/chat, independently of capture sessions. Trace context is saved outside immutable request bodies and carried in traceparent headers. Workers restore it after restart. Polling/waiting is distinct from a new model delegation.

Export is metadata-only: IDs, available/selected tool names, status, attempts, durations, usage, model and prompt versions. It does not automatically upload user/model text, document contents, images/base64, signed URLs, credentials or raw errors. Durable operational state, private history, minion audit and the existing UI activity events remain authoritative in their services. Synthetic verification runs have a separate environment/origin; their scores are not measured construction accuracy.

Explicit administration from ai_inspector (with src available on PYTHONPATH if the package is not installed):

```powershell
python -B -m ai_inspector.langfuse_admin check
python -B -m ai_inspector.langfuse_admin publish-prompts
```

Publishing creates missing initial production prompts, preserving existing production versions. When the repository root is on PYTHONPATH and minion dependencies are available, it also publishes inspector/drawing-photo. Set INSPECTOR_LANGFUSE_PROMPTS_ENABLED=true to resolve inspector/orchestrator by INSPECTOR_LANGFUSE_PROMPT_LABEL (default production). New runs save text/version/hash before model IO and reuse them on retry. Failed resolution pins the bundled fallback for that run. Legacy runs do not silently adopt managed prompts. Local/remote InspectorProfile may opt in with both instructions_prompt and immutable instructions_prompt_version; changing these/instructions requires a profile version bump. Base specialist rules, schemas, allowlists and budgets stay in code.

For quality, use [evaluation.py](../src/ai_inspector/evaluation.py): routing_scores computes declared inspector/tool selection, delegation precision/recall, argument validity and limits; run_experiment uses the official SDK and curated local/Langfuse cases. Supply a task function that invokes the application with isolated or explicitly approved dependencies. Expected outputs and realistic coverage must be curated; semantic/evidence quality still requires domain review. Compare score summaries across releases in Langfuse Experiments; assert_no_regression can fail the caller's CI job on an agreed regression threshold. No paid model calls run automatically in CI.

Experts can use the project's annotation queue and Scores interface. To attach external feedback explicitly: `python -B -m ai_inspector.langfuse_admin score --trace-id <32-hex-ID> --name helpful --value 1`. Native-only scores/experiments use API credentials; the product does not expose Langfuse credentials or arbitrary evaluator code as agent tools. Built-in Dashboards provide latency/cost/usage views; custom score widgets and alert thresholds are configured in the Langfuse project. No account login, plugin or coding-assistant access is required for backend tracing.

Cloudflare Python uses a bounded OTLP/HTTP JSON transport instead of native SDK threads. Set the same keys as Worker Secrets and other options as bindings; each fetch/process flushes up to 128 observations with bounded IO, dropping telemetry on failure. Prompt reads use the same frozen-snapshot rule. Native inspector deployments and the separate minion need their own configuration/deployment; putting keys in the local .env does not configure Cloudflare or remote services. There is no telemetry retry that re-executes business actions, and this is not a lossless audit log.

## Verification boundary

Selected JPEG, PNG and WebP attachments now supply actual source-bound full-image
previews to the model, with file IDs, original SHA256, preview SHA256 and the
orientation/resize transform retained in the run's `workspace_context.image_inputs`.
The existing configured demos retain their prior processing path: an exact prepared
task plus the full matching source-hash set is recognized separately. A matching
filename or task alone cannot classify the user's own files as demo inputs.
Each source is limited to 16 MiB, 20 megapixels and one frame; invalid or oversized
images fail before model inference instead of silently sending filenames alone.
Up to eight selected files share the existing request limit. Each preview is at
most 240 KB, with a shared 1,024,000-byte budget across attachment previews so
they fit the bounded A2A envelope together with a document crop and metadata.
Preview pixels are not metric measurements. Explicit document crops
remain separately labelled and are not confused with full-image attachments.
The frozen run stores the actual previews for retries and reopen. A request with
new images cannot call `progress4d_result` to substitute a retained timeline.
Weekly photo comparison is a new visual analysis of those inputs; quantitative
4D reconstruction still uses the separately configured Progress4D survey workflow.

Local fixtures cover model/tool validation, scopes, retries/restart, separate HTTP services/stores, Geometry compact-result import/continuation and cancellation fencing. They do not establish deployed connectivity, live Meta access, a real phone, inspection accuracy or a configured external Geometry service. Capture_task display in the actual phone, business messages and measurement accuracy require separate integration acceptance. Automatic Geometry reviews require the configured Geometry API, shared private Storage, supported capture, pinned IFC and registered Geometry Inspector/Geometry Verifier deployments.

The composer clears when the request is saved for submission; the frozen pending body remains available for recovery. Enter sends; Ctrl+Enter or Command+Enter inserts a newline. IME composition does not submit.

Drawing/photo workflow selection is server-owned: INSPECTOR_DRAWING_PHOTO_WORKFLOW
accepts legacy (default) or staged. Deploy a minion supporting staged before enabling
it. New tool plans pin this value; recovery never reads a changed setting to rewrite
an existing request. Old frozen plans omit the field and retain legacy semantics.
Staged minion phase events are exposed as reading the drawing, checking the photo,
and reviewing findings. Only observed phases are shown; no fabricated progress.
Successful authenticated GET /v1/chats/{chat_id} requests also wake the existing
Cloudflare work poller in the background. Persisted due times and leases still gate
execution; the browser's five-second refresh therefore observes due comparison polls
without waiting for the minute cron. Cron remains the continuation fallback with no
browser. The current observed running phase is also the activity heading.

For staged comparisons, new photos on the same drawing/page automatically reuse the
latest available saved checklist in this chat. The chosen source task is frozen into the
pending command before I/O; retries cannot switch checklists. `checklist_task_id` can
explicitly choose a compatible task in the same chat. `new_checklist=true` is reserved
for a user-requested scope/revision change; a replacement image alone must not set it.
Different drawing identity starts a new checklist. Older tasks without a saved snapshot
remain readable but cannot supply a reusable checklist. Categories and stable requirement
IDs belong to the snapshot; summaries must not merge geometry and material/product
verdicts. UI progress labels reuse as Using saved checklist.

### Reuse a saved Geometry result

Give Bob the capture session UUID in a new chat and optionally select one IFC.
`session_import` now attaches only an already completed Geometry result. It selects
the most recently completed result matching that session and the selected IFC hash;
without a selected IFC it uses the completed job's original reference. This mode
never submits a Geometry job, reconstructs, segments, or silently falls back to raw
capture processing. No matching result produces a clear error without work creation.

The configured Geometry bucket is a trusted single-project source, available only
to operators of `INSPECTOR_PROJECT_ID`. Model-provided endpoints and storage keys
are forbidden. Result metadata, capture identity, IFC hash, committed result/stage
manifests and bounded evidence are checked by the normal Geometry adapter. Raw
RGB-D files and large reconstruction artifacts are not reprocessed. Provenance says
`saved_result`; each use gets a separate local result session bound to the original capture UUID;
old capture sessions and Geometry jobs/results are not changed. A cancelled chat review stays cancelled: start a new
chat for a new test. Active processing must be stopped before switching modes.

`source_validation_progress` is available to the main conversation for progress
explanations. Delegation retains the existing flat A2A session projection and omits
this progress object; task/source bindings and the verifier contract are unchanged.

The queue reads the saved result, prepares evidence, delegates Inspector → Verifier
and posts Bob's continuation automatically. The user supplies neither candidate-batch
JSON nor a notification that Geometry is ready. The UI identifies loading a saved
result and shows reviewed batch counts. New phone capture remains a separate explicit
`session_create` action; previously queued raw imports retain their frozen commands.

Unexpected execution errors checkpoint a safe retryable state and preserve the pending command. Resume continues the same operation. The work panel shows saved-result loading, evidence review counts and last-update time; unknown duration uses an indeterminate indicator, not a fabricated percentage.
