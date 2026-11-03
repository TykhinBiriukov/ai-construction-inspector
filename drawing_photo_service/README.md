# Drawing Analysis Agent

Independent native service for supplied photographs and 2D drawings. Inspector connects through its [drawing/photo adapter](../ai_inspector/docs/agent-chat.md#drawingphoto-minion), separately from A2A registration. The service does not discover files, fetch caller URLs, parse IFC/3D or provide measurements. Meta performs visual analysis; report validation is not proof that a visual claim is true.

## Run locally

Install [requirements.lock](requirements.lock) in an isolated Python 3.12+ environment from the repository root. requirements.txt lists direct dependencies. Set MINION_SERVICE_TOKEN and MINION_META_API_KEY privately, and MINION_DATA_DIR to a persistent writable directory, then run:

```sh
python -m uvicorn drawing_photo_service.app:app --host 127.0.0.1 --port 8080 --workers 1
```

.env.example lists names; .env is not loaded automatically. An empty service token fails closed. Missing Meta credentials produce an explicit model_not_configured failure, never a fake analysis. Public /healthz checks process availability only; all task/evidence routes require the service bearer. /docs and /openapi.json describe HTTP schemas. Validation/limits are in [models.py](models.py), [images.py](images.py), [app.py](app.py) and [engine.py](engine.py).

Optional Langfuse integration shares the backend's [observability adapter](../ai_inspector/src/ai_inspector/observability.py). For local minion runs, include ai_inspector/src on PYTHONPATH and install requirements-observability.txt in addition to the existing requirements.lock. Docker builds now use the repository root as context (`docker build -f drawing_photo_service/Dockerfile .`), copying only the service and shared adapter; Dockerfile.dockerignore excludes local credentials/data. The Render blueprint uses the same context. This changes the build input, not the deployed service automatically.

Set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL and LANGFUSE_TRACING_ENVIRONMENT as service environment variables. No .env is loaded by this service. Inspector sends traceparent with the immutable task submission; the service persists it outside request identity and traces analysis, crop_region and evidence-review calls. It never exports images, source text, generated reports or raw errors automatically. Internal prompt snapshots/trace state are excluded from task API responses. In legacy mode, INSPECTOR_LANGFUSE_PROMPTS_ENABLED=true resolves inspector/drawing-photo once before model IO; its actual version/hash is retained in provenance, and a failed lookup pins the bundled prompt. Staged mode retains its three distinct bundled phase prompts and freezes their text/version/hash privately before model IO; the legacy managed prompt never replaces them. Its model/crop calls share the task trace. Existing task replay never starts a fresh analysis. See the [shared guide](../ai_inspector/docs/agent-chat.md#langfuse-cloud-traces-prompts-and-evaluation) for prompt publishing, scores and datasets.

## Optional Meta relay

The [dedicated Cloudflare Worker](deploy/meta-relay/worker.mjs) forwards authenticated POST /v1/chat/completions only to the fixed Meta endpoint. Render retains image/PDF processing, evidence validation and task persistence. Configure META_API_KEY and a separate RELAY_TOKEN as Worker Secrets; set MINION_META_PROXY_URL to that HTTPS origin and MINION_META_PROXY_TOKEN to the relay token on the service. In relay mode the service does not send or require a local Meta key. An empty proxy URL preserves direct Meta access; incomplete relay configuration fails closed. Do not reuse MINION_SERVICE_TOKEN for relay access.

The relay bounds request/response streams to 22 MiB/256 KiB, refuses redirects, applies a 65-second upstream deadline and performs no automatic retries or body logging. [Wrangler configuration](deploy/meta-relay/wrangler.jsonc) is an optional deployment helper, not a service provisioned by startup. Authenticated POST /v1/provider-check sends a fixed small model request through the configured inference transport; unlike GET /healthz, it invokes the provider and may incur usage. It is an explicit diagnostic, never an automatic health check.

## Task contract and recovery

POST /v1/tasks receives request_id, question, caller context and explicitly supplied assets. Use PNG/JPEG drawing/photo inputs or a PDF drawing with an explicit one-based page; separate pages need distinct asset IDs. Keep caller scope/revision context clearly separated from measured evidence. There is no DWG/IFC input or autonomous file search.

Default processing is synchronous. For orchestration send Prefer: respond-async: validated reservation returns HTTP 202, status=running, Location and Retry-After. Read status, not merely the HTTP status code. Exact repeated POST returns the same saved task/result without another model call; changed content under the same ID conflicts. Unrelated concurrent submission returns service_busy; only one analysis is admitted at a time.

The worker is in-process, not a durable job queue. A crashed execution becomes failed/execution_interrupted on read/replay and is not silently rerun. Failed tasks are immutable; a deliberate new analysis uses a new request ID. For needs_input, send a new ID, parent_task_id, a complete updated asset set and the answer in context. The previous report is untrusted reference data; the old task remains readable.

## Selectable analysis workflows

POST /v1/tasks accepts workflow=legacy (default) or staged. Omitted/explicit legacy preserves historical request digests, including requests accepted before the workflow field existed. Changing workflow requires a new request ID. There is no silent fallback after an analysis failure.

- Legacy uses joint drawing/photo analysis and an evidence review in the same history. A role-conflict clarification with no findings returns directly without forcing an evidence pass.
- Staged reads a drawing-only checklist, checks the current photo against that checklist, then reviews both sources in a fresh context. Each phase has its own instructions/history on the same configured model; this is not independent-model verification. Crop access is enforced by source role.

Checklists contain at most 12 atomic requirements with stable R01-style IDs and separate geometry, material, product, measurement, installation and performance categories. Both later phases must preserve every ID, category, criterion and drawing claim exactly. An unsupported requirement becomes inconclusive with an explanation; changing requirements requires an explicitly requested new checklist. Geometric likeness cannot establish material/product identity or numerical measurements. Uncertain visibility/correspondence cannot establish absence or compliance.

Both modes retain the shared 240-second/9-call maximum. Staged permits at most three calls per phase, including crop requests and one schema repair. The last attempt disallows tools. Phase model histories receive only their scoped image views and typed handoff, with no earlier assistant conversation. Staged clarification follow-ups must resupply the clarified task/scope; automatic parent-report injection remains limited to legacy.

Staged tasks save a checklist snapshot with its drawing identity, task/context, named drawing views and content-derived checklist_id. A replacement photo can pass checklist_task_id referencing a saved task. The service validates the snapshot hash, original drawing SHA-256/page/dimensions and view bounds before admission. Reuse skips drawing analysis and retains the original requirements; it does not retain observations from older photos. Inspector automatically selects a compatible saved checklist in the same chat. New requirements use a fresh checklist through the Inspector adapter's new_checklist option.

Staged responses expose the last observed phase (drawing_checklist, checklist_reuse, photo_inspection or review); only status establishes completion. Original-coordinate evidence routes remain compatible with both modes. Checklist/progress/audit/provenance are saved with the task. A saved snapshot does not turn an interrupted in-process task into a resumable worker.

## Evidence semantics

Responses distinguish running, completed, needs_input and failed. Findings preserve the design/photo claims, source-region evidence, correspondence certainty, visibility, verdict, concise rationale, limitations and questions. Uncertain correspondence or insufficient visibility cannot yield a conclusive verdict. Technical/schema/model failure is different from inconclusive visual evidence. The explanation is an auditable rationale, not hidden provider reasoning.

Sources are EXIF-oriented images or the rendered selected PDF page. Normalized top-left [x, y, width, height] evidence coordinates refer to that view, with recorded source SHA-256/page/dimensions. GET /v1/tasks/{id}/evidence/{finding_index}/{drawing|photo}/{ref_index} renders the selected reference with a red rectangle; indices are zero-based. That image is a presentation derivative, not the original source identity.

The model sees supplied previews and can use crop_region on named A–J/1–10 grid views. Nested crops retain provenance; server mapping, not model-estimated exact pixels, produces original-image boxes. Cropping cannot recover detail beyond the rendered PDF resolution. Legacy reports with findings receive an evidence-box review; a clarification with no findings returns directly. Staged review sees both sources and generated views in its fresh context. Both are model self-checking, not independent validation or OCR.

Reports include model/prompt/tool-schema provenance and actual model-call/crop audit. Task responses exclude source base64 and provider internal reasoning. Requests/originals and reports remain private for evidence reopening. Callers must review findings against their source and retain unknowns; a completed report is not certified construction approval.

IO, decoded assets/pixels, model calls and processing time are bounded in code. Timeouts are per I/O phase and elapsed time between calls, not forced termination of native code. Malformed/schema/reference output gets a bounded correction attempt; truncation and exhausted budgets fail explicitly. There are no automatic model HTTP retries. A caller's timeout must not cause a new task ID for the same uncertain submission.

## Native/container and database deployment

[Dockerfile](Dockerfile) uses a conventional native container for Pillow/PDFium; this is not a Cloudflare Workers package. It runs as UID 10001. Native SQLite requires a persistent writable MINION_DATA_DIR. Keep one worker/instance and preserve task data across restarts.

Cloud configuration in [render.yaml](deploy/render.yaml) names the service drawing-analysis-agent and selects feat/ai-inspector-development with repository-root Docker context. It requires MINION_DATABASE_URL and MINION_REQUIRE_DATABASE=1; missing/unavailable database must not fall back to ephemeral SQLite. Existing Render services must separately adopt the branch/build configuration; changing this file does not update live settings. A prepared blueprint is not proof of a deployed service. Cold starts can delay admission; callers need bounded exact retries.

[deploy/provision.py](deploy/provision.py) is an administrator-only setup helper for private schema drawing_photo and role drawing_photo_app. It reads the chosen administrator connection from a private environment file, refuses existing roles/output credentials and writes a restricted URI to an owner-only output file. Use it only for an authorized schema/role setup; runtime never receives the administrator URI. It grants task SELECT/INSERT and UPDATE(response), no schema ownership/DELETE, and checks denied access to Inspector/Localization schemas. Migrations do not run on application startup.

Requests including original asset base64, reports, identities and audit are stored together. This bounded MVP deliberately uses PostgreSQL instead of a privileged Storage key. The store caps payload accounting, reserves response space and returns storage_budget_exceeded/HTTP 507 when full; it never silently deletes history. Database physical/index overhead is outside payload accounting. Larger datasets need a designed object-storage/retention policy.

Use TLS with direct PostgreSQL or SESSION pooling, not transaction pooling: the service uses a session advisory lock for global admission, including overlapping deployments. Reservation/final save share the lock connection; a disconnected worker cannot finalize through a fresh connection. Persisted data does not imply automatic job resumption. Existing local evaluation data is not automatically migrated.

Expose only HTTPS with the service bearer. This single-tenant token grants access to the whole instance, so preserve separation from Inspector operator/device/model/storage identities. Hosting secrets hold credentials. Production rate/memory/load limits and real PostgreSQL/model acceptance require separate verification.

## Quality workflow

Keep evaluation requests, approved expected answers, review sheets and manifests outside Git/model input, in an optional companion workspace. Distinguish deterministic fixtures, real-photo development and held-out evaluation. Record a fixed prompt/model baseline, change one behavior at a time, and review verdict, source reading, evidence coverage, unsupported claims, false matches/mismatches and useful clarification. Repeat critical cases; a small fixture set is not an accuracy claim.

If the companion evaluator is available, live runs require its explicit live/candidate flags; candidate results remain exploratory until owner review. Submit request files only, reopen saved results and preserve provenance. Mechanical schema checks do not replace human semantic review and do not auto-approve answers or tune prompts. The model/tool compatibility implementation belongs in engine.py and the primary [Meta tool-calling](https://dev.meta.ai/docs/tool-calling) / [structured-output](https://dev.meta.ai/docs/structured-output) references.
