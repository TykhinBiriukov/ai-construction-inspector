# Drawing Analysis Agent

Cloud deployment can use the dedicated `deploy/meta-relay` Cloudflare Worker
for Meta connectivity. Render keeps image/PDF processing, evidence validation
and Supabase persistence; Inspector's task API does not change. Set the Worker's
`META_API_KEY` and a dedicated random `RELAY_TOKEN` as Secrets. On Render set
`MINION_META_PROXY_URL` to its HTTPS origin and `MINION_META_PROXY_TOKEN` to that
relay token. In relay mode the Meta key is not sent by Render and is not required
there. Empty proxy URL preserves direct local Meta access; incomplete relay config
fails closed. `POST /v1/provider-check` uses the configured inference transport.

The Worker only forwards authenticated POST `/v1/chat/completions` to the fixed
Meta endpoint. It streams without parsing/storing image bodies, caps request/
response sizes at22MiB/256KiB, refuses redirects and has a65s upstream deadline.
Observability is disabled; no automatic retries. Deploy with Wrangler using
`deploy/meta-relay/wrangler.jsonc`, then provision Secrets separately. Worker
Free quotas and Render sleep/cold-start limits still apply. To return to direct
Meta, restore its key on Render and remove both proxy settings.

Standalone v0.3.0 service for supplied photographs and 2D drawings. No integration
with Inspector, Localization, BIM or 3D; no file discovery or remote asset fetching.
Meta performs visual analysis; Python validates the report and renders evidence.
Validation is not proof that a visual claim is true.

## Run locally

Python 3.12. From the repository root, install `drawing_photo_service/requirements.lock`
in an isolated environment, then run (`requirements.txt` lists direct dependencies):

```sh
export MINION_DATA_DIR=/absolute/private/path/drawing-photo-data
# Set MINION_SERVICE_TOKEN and MINION_META_API_KEY privately in the environment.
python -m uvicorn drawing_photo_service.app:app --host 127.0.0.1 --port 8080 --workers 1
```

`.env.example` lists settings; `.env` is ignored but is NOT loaded automatically.
Use hosting Secrets or explicitly export values. An empty service token fails
closed. A missing Meta key produces `failed/model_not_configured`, never a fake
analysis. The public `/healthz` endpoint checks process availability only.
OpenAPI at `/docs` describes schemas. All task/evidence routes require
`Authorization: Bearer <MINION_SERVICE_TOKEN>`.

## Task contract

`POST /v1/tasks` accepts JSON:

```json
{
  "request_id": "3abb60ec-828b-4ae7-8c65-652f5a5f15e7",
  "question": "Compare the number of doors on the specified wall.",
  "context": "Caller-supplied scope and document revision; not measured evidence.",
  "assets": [
    {"asset_id": "drawing-1", "kind": "drawing", "mime_type": "application/pdf", "page": 1, "data_base64": "<bytes>"},
    {"asset_id": "photo-1", "kind": "photo", "mime_type": "image/jpeg", "data_base64": "<bytes>"}
  ]
}
```

The caller chooses all input files and PDF pages. PNG/JPEG supported for either
kind; PDF only for drawings, with an explicit one-based page. No DWG/IFC input.
Repeated pages use distinct asset IDs. Original bytes and request are saved
privately. Images are EXIF-oriented for viewing; all evidence coordinates refer
to that oriented image or the rendered selected PDF page: normalized top-left
`[x, y, width, height]`. The response records source SHA256, page and dimensions.

By default processing is synchronous. For orchestration send `Prefer: respond-async`: after validation and reservation the API returns HTTP 202 with `status=running`, `Location` and `Retry-After: 2`. Poll that Location until a terminal status. An exact repeated POST may return HTTP 200 with `running`; this is not completion. The worker is in-process, not a durable queue. `GET /v1/tasks/{request_id}` reopens the saved result.
An exact retry reuses it without another model call; changed content under the
same ID returns 409. Only one task runs at a time; unrelated concurrent submission
returns 409 `service_busy`. A crashed execution becomes `failed/execution_interrupted`
on read/replay, never silently reruns. Failed attempts remain immutable: deliberate
retry requires a new ID. Do not infer completion from HTTP 200; inspect `status`.

For `needs_input`, send a NEW request ID with `parent_task_id` referencing that
task, a complete updated asset set and the answer in `context`. The prior report
is supplied as untrusted context; the original result remains available. This is
an explicit new analysis, not an autonomous search or a conversation with users.

## Result and evidence

Task statuses: `running`, `completed`, `needs_input`, `failed`.
Each report finding includes:

- criterion, design claim and photo claim, each with source-region evidence;
- correspondence (`supported`/`uncertain`) and its basis;
- visibility (`sufficient`/`insufficient`), verdict (`match`/`mismatch`/`inconclusive`);
- concise evidence-grounded justification and limitations.

`needs_input` has concrete questions. Uncertain correspondence or insufficient
visibility cannot have a conclusive verdict. The server validates IDs, source
roles and coordinate bounds. It does not validate the semantic content of pixels.
The explanation is an auditable rationale, not provider hidden chain-of-thought.
The orchestrator must inspect evidence before relying on a finding.

`GET /v1/tasks/{id}/evidence/{finding_index}/{drawing|photo}/{ref_index}` returns
the referenced image/page with a red rectangle (zero-based indices). It is a
presentation derivative; the stored source hash identifies original bytes.

Provenance includes model, inference mode, prompt version/hash and tool schema
hash. The audit records actual model calls and crop requests. Source base64 and
provider internal reasoning are not exposed in task responses. Originals remain in the configured private database for evidence reopening.

## Tools and limits

All supplied images/pages enter the first model request as previews. The model uses `crop_region` to magnify a rectangle of grid cells in a named view.
All views have columns A-J and rows 1-10. A new crop gets its own view ID.
Model evidence references use view ID + inclusive top-left/bottom-right cells;
the server resolves exact integer crop bounds to original normalized coordinates.
No decimal coordinate arithmetic is delegated to the model.
Cells are inclusive and snapped to integer pixel bounds with outward rounding. Public report evidence
remains the same original-coordinate format. Source pixels are never altered; the
grid and margins exist only in inference previews. Nested crops preserve provenance.
A second pass for reports with findings shows the exact evidence rectangle in red with nearby context to the model for
review/correction. This self-check is not independent proof of visual correctness. No web,
filesystem, arbitrary URL, BIM, measurement or generation tools. No OCR yet.
PDF rendering uses PDFium, serialized across threads. Full previews are at most
1600 px on the longest edge; PDF pages render at most 2400 px, scale at most 2.
Cropping does not recover detail beyond that PDF rendering resolution.

Up to 8 assets; 6 MB decoded per asset, 20 MB combined base64, 22 MB HTTP body;
16 million pixels per raster image and 24 million pixels combined. PDF maximum
200 pages. Up to 9 model calls per task and a 240-second processing budget;
individual HTTP timeout up to 60 seconds. Meta reasoning effort is explicitly
`medium`, completion budget 12000 tokens. JSON-schema constrained output is enabled.
A malformed report gets at most one schema/reference correction; truncation
(`model_output_truncated`) fails explicitly instead of accepting partial JSON. Network timeouts are checked by httpx
per I/O phase and elapsed time between calls, not by forcibly killing native code.
No automatic HTTP retries. Numeric provider usage/finish reasons and schema error
locations are audited without raw source payloads or internal model reasoning. Malformed output and exhausted budgets produce a
technical failure, never a guessed inspection result.

A valid `needs_input` report with empty findings returns immediately: there are no
evidence rectangles to review. If supplied drawing/photo roles appear inconsistent
with the pixels, the model must request role confirmation rather than swap sources.
Explicit criterion labels are preserved when findings exist; they do not prevent
a clarification. Without labels the model may choose criteria within the user's task.
Reference validation records distinct `wrong_evidence_role`, `unknown_evidence_view`,
`too_many_evidence_regions` and `criterion_identity_mismatch` issues with locations
and bounded repair guidance. Raw provider output/reasoning is not persisted.

## Deployable packaging

Build with context `drawing_photo_service/`:

```sh
docker build -t drawing-photo-minion:0.1 drawing_photo_service
docker run --rm -p 127.0.0.1:8080:8080 \
  --env MINION_SERVICE_TOKEN --env MINION_META_API_KEY \
  --mount type=volume,src=drawing-photo-data,dst=/data \
  drawing-photo-minion:0.1
```

Container runs as UID 10001. Local development defaults to SQLite in
`MINION_DATA_DIR`; mounted directories must be writable by that UID. Cloud hosting
sets `MINION_REQUIRE_DATABASE=1` and `MINION_DATABASE_URL` and never falls back to
SQLite if the database configuration is missing or unavailable.

### Supabase persistence

The admin-only helper `deploy/provision.py` creates private schema `drawing_photo`
and restricted login `drawing_photo_app` in the existing PostgreSQL database:

```sh
python -m drawing_photo_service.deploy.provision \
  --admin-env /private/path/to/admin.env \
  --output /private/path/to/minion-runtime.json
```

Run only after approval for creating this role/schema. The helper reads the
administrator connection from `INSPECTOR_DATABASE_URL`, refuses existing roles or
credential files, and saves the new scoped URI in a mode0600 file. The runtime must
never receive the administrator URI. It gets only SELECT/INSERT and UPDATE(response)
on `drawing_photo.tasks`, no schema ownership or DELETE. The helper verifies denied
access to Inspector and Localization schemas. Migration is not run on app startup.

Requests (including original file base64), reports, source identities and audit are
stored together in PostgreSQL. This deliberately bounded MVP uses Postgres rather
than adding a privileged Storage API key. Admission caps stored UTF-8 request/report
payload at 100MB, reserving at least 2MB for each response; this excludes database
indexes/physical overhead. A full budget returns HTTP507, never silently deletes
history. Larger datasets need an explicitly designed object-storage/retention path.

Use TLS and the SESSION pooler on 5432 (or direct PostgreSQL), never transaction
pooling on 6543. A session advisory lock admits one analysis globally, including
overlapping containers during deployment. Reservation and final save use the lock
connection; a disconnected worker cannot write a stale result through a fresh
connection. A crashed task becomes execution_interrupted on later read, not an
automatic model retry. Durable data is not a durable job queue. Keep one worker and
one instance; callers poll async tasks and explicitly create a new ID to retry a
failed task. No existing local evaluation data is migrated automatically.

### Render configuration

`deploy/render.yaml` is the prepared Free Docker blueprint (select this custom path
when creating a Blueprint). It specifies the isolated branch, component build
context, HTTP8080, health `/healthz` and the three runtime secrets. No paid disk or
Render database is requested. Credentials must be entered in hosting Secrets only.
Free services sleep after 15 minutes idle and may take about a minute to wake;
Inspector must tolerate cold-start delay before submission. Saved tasks/evidence
remain in Supabase across restarts. Native Pillow/PDFium dependencies require a
conventional container; this is not packaged for Cloudflare Workers.

The service token grants access to the entire single-tenant minion instance.
Expose only through HTTPS with Bearer authentication. Only one inference is admitted;
request rate limiting and memory testing on maximum-size inputs remain production
requirements. Meta usage is separate from hosting. No deployment or real PostgreSQL
integration is established merely by this configuration.

## Quality workflow

Keep evaluation materials outside the repository in the companion workspace's
`local-checks/inspector/drawing-photo/`. Candidate answers require owner approval.
Never include expected answers, review sheets or evaluation manifests in model
inputs. Distinguish deterministic illustrated fixtures, real-photo development
cases, and held-out real-photo evaluation.

Before tuning, record a baseline with the fixed prompt and model. Change one
prompt/tool behavior at a time and compare errors. Evaluate final verdict,
source reading, evidence coverage, unsupported statements, false matches,
false mismatches and useful clarification. Repeat critical cases for stability.
Mechanical schema checks cannot replace human semantic review. Two cases are
a starting point, not an accuracy estimate or release acceptance threshold.

The local runner `evaluate.py` requires explicit `--run-live`; unapproved candidates
additionally require `--allow-candidates` and remain labelled exploratory.
It submits only request files, records full reports/provenance, reopens persisted
results, and leaves semantic review explicitly pending. It does not auto-approve
answers or auto-tune prompts.

Meta compatibility checked 30 October 2026: only `tool_choice="auto"` is supported.
The final review therefore sends an empty tools list with auto, not `none`.
See https://dev.meta.ai/docs/tool-calling and https://dev.meta.ai/docs/structured-output.
