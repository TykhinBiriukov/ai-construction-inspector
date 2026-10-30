# Drawing/photo comparison service

Independent native service for supplied photographs and 2D drawings. Inspector connects through its [drawing/photo adapter](../ai_inspector/docs/agent-chat.md#drawingphoto-minion), separately from A2A registration. The service does not discover files, fetch caller URLs, parse IFC/3D or provide measurements. Meta performs visual analysis; report validation is not proof that a visual claim is true.

## Run locally

Install [requirements.lock](requirements.lock) in an isolated Python 3.12+ environment from the repository root. requirements.txt lists direct dependencies. Set MINION_SERVICE_TOKEN and MINION_META_API_KEY privately, and MINION_DATA_DIR to a persistent writable directory, then run:

```sh
python -m uvicorn drawing_photo_service.app:app --host 127.0.0.1 --port 8080 --workers 1
```

.env.example lists names; .env is not loaded automatically. An empty service token fails closed. Missing Meta credentials produce an explicit model_not_configured failure, never a fake analysis. Public /healthz checks process availability only; all task/evidence routes require the service bearer. /docs and /openapi.json describe HTTP schemas. Validation/limits are in [models.py](models.py), [images.py](images.py), [app.py](app.py) and [engine.py](engine.py).

## Task contract and recovery

POST /v1/tasks receives request_id, question, caller context and explicitly supplied assets. Use PNG/JPEG drawing/photo inputs or a PDF drawing with an explicit one-based page; separate pages need distinct asset IDs. Keep caller scope/revision context clearly separated from measured evidence. There is no DWG/IFC input or autonomous file search.

Default processing is synchronous. For orchestration send Prefer: respond-async: validated reservation returns HTTP 202, status=running, Location and Retry-After. Read status, not merely the HTTP status code. Exact repeated POST returns the same saved task/result without another model call; changed content under the same ID conflicts. Unrelated concurrent submission returns service_busy; only one analysis is admitted at a time.

The worker is in-process, not a durable job queue. A crashed execution becomes failed/execution_interrupted on read/replay and is not silently rerun. Failed tasks are immutable; a deliberate new analysis uses a new request ID. For needs_input, send a new ID, parent_task_id, a complete updated asset set and the answer in context. The previous report is untrusted reference data; the old task remains readable.

## Evidence semantics

Responses distinguish running, completed, needs_input and failed. Findings preserve the design/photo claims, source-region evidence, correspondence certainty, visibility, verdict, concise rationale, limitations and questions. Uncertain correspondence or insufficient visibility cannot yield a conclusive verdict. Technical/schema/model failure is different from inconclusive visual evidence. The explanation is an auditable rationale, not hidden provider reasoning.

Sources are EXIF-oriented images or the rendered selected PDF page. Normalized top-left [x, y, width, height] evidence coordinates refer to that view, with recorded source SHA-256/page/dimensions. GET /v1/tasks/{id}/evidence/{finding_index}/{drawing|photo}/{ref_index} renders the selected reference with a red rectangle; indices are zero-based. That image is a presentation derivative, not the original source identity.

The model sees supplied previews and can use crop_region on named A–J/1–10 grid views. Nested crops retain provenance; server mapping, not model-estimated exact pixels, produces original-image boxes. Cropping cannot recover detail beyond the rendered PDF resolution. A mandatory second pass reviews the boxed evidence with nearby context; this is model self-checking, not independent validation or OCR.

Reports include model/prompt/tool-schema provenance and actual model-call/crop audit. Task responses exclude source base64 and provider internal reasoning. Requests/originals and reports remain private for evidence reopening. Callers must review findings against their source and retain unknowns; a completed report is not certified construction approval.

IO, decoded assets/pixels, model calls and processing time are bounded in code. Timeouts are per I/O phase and elapsed time between calls, not forced termination of native code. Malformed/schema/reference output gets a bounded correction attempt; truncation and exhausted budgets fail explicitly. There are no automatic model HTTP retries. A caller's timeout must not cause a new task ID for the same uncertain submission.

## Native/container and database deployment

[Dockerfile](Dockerfile) uses a conventional native container for Pillow/PDFium; this is not a Cloudflare Workers package. It runs as UID 10001. Native SQLite requires a persistent writable MINION_DATA_DIR. Keep one worker/instance and preserve task data across restarts.

Cloud configuration in [render.yaml](deploy/render.yaml) requires MINION_DATABASE_URL and MINION_REQUIRE_DATABASE=1; missing/unavailable database must not fall back to ephemeral SQLite. The file defines the source branch, build context, health route and hosting settings; select the intended published revision when provisioning. A prepared blueprint is not proof of a deployed service. Cold starts can delay admission; callers need bounded exact retries.

[deploy/provision.py](deploy/provision.py) is an administrator-only setup helper for private schema drawing_photo and role drawing_photo_app. It reads the chosen administrator connection from a private environment file, refuses existing roles/output credentials and writes a restricted URI to an owner-only output file. Use it only for an authorized schema/role setup; runtime never receives the administrator URI. It grants task SELECT/INSERT and UPDATE(response), no schema ownership/DELETE, and checks denied access to Inspector/Localization schemas. Migrations do not run on application startup.

Requests including original asset base64, reports, identities and audit are stored together. This bounded MVP deliberately uses PostgreSQL instead of a privileged Storage key. The store caps payload accounting, reserves response space and returns storage_budget_exceeded/HTTP 507 when full; it never silently deletes history. Database physical/index overhead is outside payload accounting. Larger datasets need a designed object-storage/retention policy.

Use TLS with direct PostgreSQL or SESSION pooling, not transaction pooling: the service uses a session advisory lock for global admission, including overlapping deployments. Reservation/final save share the lock connection; a disconnected worker cannot finalize through a fresh connection. Persisted data does not imply automatic job resumption. Existing local evaluation data is not automatically migrated.

Expose only HTTPS with the service bearer. This single-tenant token grants access to the whole instance, so preserve separation from Inspector operator/device/model/storage identities. Hosting secrets hold credentials. Production rate/memory/load limits and real PostgreSQL/model acceptance require separate verification.

## Quality workflow

Keep evaluation requests, approved expected answers, review sheets and manifests outside Git/model input, in an optional companion workspace. Distinguish deterministic fixtures, real-photo development and held-out evaluation. Record a fixed prompt/model baseline, change one behavior at a time, and review verdict, source reading, evidence coverage, unsupported claims, false matches/mismatches and useful clarification. Repeat critical cases; a small fixture set is not an accuracy claim.

If the companion evaluator is available, live runs require its explicit live/candidate flags; candidate results remain exploratory until owner review. Submit request files only, reopen saved results and preserve provenance. Mechanical schema checks do not replace human semantic review and do not auto-approve answers or tune prompts. The model/tool compatibility implementation belongs in engine.py and the primary [Meta tool-calling](https://dev.meta.ai/docs/tool-calling) / [structured-output](https://dev.meta.ai/docs/structured-output) references.
