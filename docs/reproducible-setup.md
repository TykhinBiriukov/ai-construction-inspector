# Reproducible setup

This guide separates a local chat/drawing demonstration from the optional services needed for RGB-D inspection, independent evidence review, construction monitoring and phone capture. Start only the profiles you need. A fresh clone contains application code, dependency manifests and the compact [evidence records](evidence/2026-11-03/README.md). The versioned Geometry release adds one real-room capture, IFC and completed outputs. Credentials, provisioned cloud resources and the other private development datasets are not included.

These instructions were checked against source and component documentation. They are not a record of a fresh installation or a successful live Meta, phone or full-stack run. Keep the exact commit, dependency versions, input hashes and service configuration with your own run record.

For saved-result inspection, start with the [case package and verification instructions](evidence/2026-11-03/README.md#inspect-and-reproduce); no service startup or model call is needed. The profiles below are for running the software or producing a new analysis.

## 1. Source, environments and ports

For a new clone:

```powershell
git clone --branch main --single-branch https://github.com/TykhinBiriukov/ai-construction-inspector.git
Set-Location ai-construction-inspector
git rev-parse HEAD
git status --short
```

Record the full commit printed by `git rev-parse HEAD`; `main` can advance. To reproduce someone else's run, use their recorded commit and inputs. An existing checkout need not be cloned again or reset.

Use Python 3.12 for the examples. Keep separate environments: the services pin different native and Python dependency versions. From the repository root, establish these paths in each terminal you use:

```powershell
$repoRoot = (Get-Location).Path
$runtimeRoot = Join-Path (Split-Path $repoRoot -Parent) 'inspector-runtime'
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
```

The examples keep environments and persistent data outside the checkout. Reuse their paths on restart. Do not delete a database to recover from a failed request.

| Profile | Local address | Environment / state | Additional requirements |
| --- | --- | --- | --- |
| Inspector chat and web workspace | `127.0.0.1:8000` | Inspector Python environment; SQLite and immutable blobs | Dedicated operator/device tokens; Meta key for chat |
| Drawing/photo analysis | `127.0.0.1:8080` | Separate Python environment; private task store | Service token; Meta key; supplied drawing and photo |
| Geometry Engine | `127.0.0.1:8001` | Separate Python environment; PostgreSQL; Storage; local work/cache | RGB-D capture, IFC, Meta SAM access; separate worker |
| Geometry Inspector / Verifier | `127.0.0.1:8101` / `:8102` | Inspector package; two independent persistent task directories | Three distinct inter-service credentials; Meta access in both services |
| Progress4D | `127.0.0.1:8791` | Separate Python/native environment; SQLite and survey artifacts | Photo surveys + scheduled IFC; optional OpenMVS binaries |
| Localization deployment profile | port `8788` | Separate environment; restricted PostgreSQL schema | Restricted room IFC; service and session credentials; hosted HTTPS for Inspector |
| iPhone capture | Device application | macOS/Xcode build; physical iPhone | Reachable Inspector HTTPS endpoint; LiDAR device for RGB-D |

The local examples bind to loopback; the Localization deployment profile below is an explicit exception. A browser, phone or service on another machine cannot use this computer's `127.0.0.1`. Hosting, HTTPS, networking and secrets are separate configuration steps, described in the component deployment guides. Each new terminal needs the path definitions above, starting from the repository root; PowerShell variables are not shared between terminals.

## 2. Inspector: local chat and document workspace

Install the checked-in dependency lock and the editable package:

```powershell
py -3.12 -m venv "$runtimeRoot\inspector-env"
$inspectorPython = Join-Path $runtimeRoot 'inspector-env\Scripts\python.exe'
& $inspectorPython -m pip install -r "$repoRoot\ai_inspector\requirements.lock"
& $inspectorPython -m pip install --no-deps -e "$repoRoot\ai_inspector"
Set-Location "$repoRoot\ai_inspector"
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item .env.example .env }
```

Create a Meta Model API key in the [Meta developer dashboard](https://dev.meta.ai/) under **API keys → Create API key**, following the [official quickstart](https://dev.meta.ai/docs/quickstart). Confirm that the account has access to `muse-spark-1.3` and, if you select the Geometry profile, `sam-3.1`. Each service's configuration table below names the variable that receives this server-side key.

Edit the ignored `ai_inspector/.env` privately. Replace the example operator/device tokens with different random values and set:

| Setting | Value / purpose |
| --- | --- |
| `INSPECTOR_OPERATOR_TOKEN` | Private web-operator credential |
| `INSPECTOR_DEVICE_TOKEN` | Different phone credential; keep it separate from the operator token |
| `INSPECTOR_PROJECT_ID`, `INSPECTOR_DEVICE_ID` | Stable local scope, for example `demo-building` / `iphone-1` |
| `INSPECTOR_DATA_DIR` | Absolute persistent path, for example the `inspector-data` subdirectory of your runtime directory |
| `INSPECTOR_META_API_KEY` | Your server-side Meta Model API key |
| `INSPECTOR_META_BASE_URL`, `INSPECTOR_META_MODEL` | Keep the example defaults `https://api.meta.ai/v1` / `muse-spark-1.3` |
| `INSPECTOR_REMOTE_AGENTS_JSON` | Keep `[]` until you configure the A2A profile below |

Local SQLite and local BIM storage require no external database. Leave optional service URLs/storage/database settings unset until those services are ready. Settings read `.env` from the working directory; process environment overrides it. A `.env` in the repository root does not configure this launch. After adding any optional profile below, restart Inspector from `ai_inspector/` with the same persistent data directory so it reloads the changed settings.

```powershell
& $inspectorPython -m uvicorn ai_inspector.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open [the chat workspace](http://127.0.0.1:8000/ui/demo.html) and authenticate with the operator token. The [operational console](http://127.0.0.1:8000/), [OpenAPI UI](http://127.0.0.1:8000/docs) and [capabilities](http://127.0.0.1:8000/v1/capabilities) expose the running instance. `GET /health` executes a database query; it does not call Meta. `agent_model_configured=true` reports nonempty configuration, not a successful model request.

Submitting a chat message invokes the configured model and may consume API usage. File rendering alone does not send a document to the model. See [backend setup](../ai_inspector/README.md), [agent contract](../ai_inspector/docs/agent-chat.md) and [web integration](../ai_inspector/docs/web-integration.md).

## 3. Drawing/photo profile: a complete local visual workflow

Use a second terminal at the repository root, with the same `$repoRoot` and `$runtimeRoot` definitions:

```powershell
py -3.12 -m venv "$runtimeRoot\drawing-env"
$drawingPython = Join-Path $runtimeRoot 'drawing-env\Scripts\python.exe'
& $drawingPython -m pip install -r "$repoRoot\drawing_photo_service\requirements.lock"
Set-Location $repoRoot
$env:PYTHONPATH = Join-Path $repoRoot 'ai_inspector\src'
$env:MINION_DATA_DIR = Join-Path $runtimeRoot 'drawing-data'
$env:MINION_SERVICE_TOKEN = '<dedicated drawing-service token>'
$env:MINION_META_API_KEY = '<your Meta Model API key>'
& $drawingPython -m uvicorn drawing_photo_service.app:app --host 127.0.0.1 --port 8080 --workers 1
```

Replace angle-bracket values privately before running. `drawing_photo_service` does **not** load `.env` automatically. Its source imports the shared Inspector observability adapter even when telemetry is disabled, so `PYTHONPATH` above is required. Do not install the complete Inspector dependency set into this environment merely to make that import work. Optional Langfuse export additionally uses `drawing_photo_service/requirements-observability.txt` and its own service environment settings.

Add these values to the Inspector's `.env`, then restart the Inspector with the same data directory:

```dotenv
INSPECTOR_DRAWING_PHOTO_BASE_URL=http://127.0.0.1:8080
INSPECTOR_DRAWING_PHOTO_SERVICE_TOKEN=<same drawing-service token>
INSPECTOR_DRAWING_PHOTO_WORKFLOW=staged
```

`staged` selects drawing checklist → photo inspection → evidence review. `legacy` remains available for joint comparison. The workflow is frozen for each submitted task; changing configuration does not rewrite existing tasks.

To exercise the path, upload and select one PNG/JPEG drawing (or a PDF with an explicit page) and one PNG/JPEG photo in the chat. Describe the comparison task and identify the drawing page if applicable. The service receives the selected sources and task; it does not search folders or accept IFC/DWG as a drawing. For another photo of the same drawing, keep the drawing selected and explicitly request the next comparison; compatible staged checklists can be reused.

Check [process health](http://127.0.0.1:8080/healthz) and [schemas](http://127.0.0.1:8080/docs) before a real request. `/healthz` makes no model call. The authenticated `POST /v1/provider-check` is an optional **live** provider diagnostic and may incur usage. A successful task should retain source bindings, checklist/phase information for staged mode, findings and evidence references. `needs_input`, inconclusive findings and technical failure are different outcomes.

Keep one process per local task store. The minion's analysis worker is in-process: interruption is recorded as failure and does not silently resume analysis. Inspector polls admitted tasks durably; that does not make the minion itself a restartable queue. Details: [drawing/photo service](../drawing_photo_service/README.md).

## 4. Geometry profile: RGB-D reconstruction and IFC comparison

Geometry needs a running PostgreSQL server/database and private Supabase Storage. Its repository component does not provide a PostgreSQL Compose file or create a database role automatically. Configure these prerequisites separately using [Geometry setup](../geometry-engine/README.md#local-postgresql).

```powershell
py -3.12 -m venv "$runtimeRoot\geometry-env"
$geometryPython = Join-Path $runtimeRoot 'geometry-env\Scripts\python.exe'
& $geometryPython -m pip install -e "$repoRoot\geometry-engine[all]"
Set-Location "$repoRoot\geometry-engine"
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item .env.example .env }
```

Set the private Geometry `.env`:

| Setting | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL URI for the dedicated Geometry database identity; migrations require appropriate DDL rights |
| `SUPABASE_URL`, `SUPABASE_STORAGE_BUCKET`, `SUPABASE_SECRET_KEY` | Geometry input/export/result Storage and its server-only credential |
| `MODEL_API_KEY` | Meta Model API credential with `sam-3.1` access |
| `GEOMETRY_DATA_DIR` | Absolute persistent work/cache path outside the checkout |
| `GEOMETRY_PUBLIC_BASE_URL` | `http://127.0.0.1:8001` for a same-machine setup; reachable origin for remote consumers |

Run migrations, then the API on **8001** to avoid Inspector's port 8000:

```powershell
& $geometryPython -m geometry_engine_api.database.migrate
& $geometryPython -m uvicorn geometry_engine_api.app:app --host 127.0.0.1 --port 8001
```

In another terminal, repeat the path definitions from section 1, then start the worker with the same environment and private Geometry configuration:

```powershell
Set-Location "$repoRoot\geometry-engine"
& "$runtimeRoot\geometry-env\Scripts\python.exe" -m geometry_engine_api.worker
```

Keep the API and worker running together. The explicit migration step above is required because importing the API does not apply migrations. [Geometry OpenAPI](http://127.0.0.1:8001/docs) being available does not prove database, Storage or SAM access.

Configure Inspector's `INSPECTOR_GEOMETRY_BASE_URL=http://127.0.0.1:8001`, its outgoing `INSPECTOR_GEOMETRY_API_TOKEN`, and the required Storage bindings from the [capture-to-Geometry contract](../ai_inspector/docs/capture-to-geometry.md#geometry-service-api). The adapter requires an outgoing credential; the local Geometry application itself does not enforce it. Keep this single-tenant API in a trusted network. A hosted authentication gateway must enforce the corresponding credential; optional `INSPECTOR_GEOMETRY_API_USERNAME` selects Basic authentication instead of Bearer.

Inspector's original capture/BIM Storage and Geometry's export/result Storage can differ. Use the three `INSPECTOR_GEOMETRY_SUPABASE_*` settings together only for a separate Geometry destination. Retain the original Inspector Storage configuration and frozen export destination on retries; the detailed contract owns these settings.

Required inputs are an accepted complete RGB-D source with original `frame.json` schema 0.2, depth/confidence/calibration/camera poses in one ARKit segment, and a pinned IFC. RGB-only observations, geometry-parts-only inventories and unaligned multiple segments do not satisfy this adapter. A random folder of images or a caller-supplied room name is insufficient.

For a direct component run, use the [submission example](../geometry-engine/README.md#submit-a-job), immutable Storage keys and a stable `Idempotency-Key`. HTTP 202 means queued; poll the job until committed completion, then fetch `/inspections/{job_id}/result`. The compact result has schema 2.0. Viewer export is a separate request and does not rerun reconstruction. To reproduce a saved-result demo, the completed source/result manifests and matching IFC identity must already exist in the configured Storage; cloning the code does not provide them.

## 5. Independent Geometry review agents

This profile adds model review after numerical Geometry processing. Geometry Inspector and Geometry Verifier run as separate services using the Inspector package. Install the optional evidence dependency in the Inspector environment for image preparation:

```powershell
& "$runtimeRoot\inspector-env\Scripts\python.exe" -m pip install -e "$repoRoot\ai_inspector[evidence]"
```

Use three independent random secrets and the **same project ID** across caller registrations:

| Credential | Held by / matched against |
| --- | --- |
| Bob → Geometry Inspector task token | Backend `INSPECTOR_TASK_TOKEN`; Geometry Inspector caller entry `token` |
| Geometry Inspector → backend evidence token | Backend `INSPECTOR_TOOL_TOKEN`; Geometry Inspector `INSPECTOR_AGENT_BACKEND_TOKEN` |
| Geometry Inspector → Verifier task token | Geometry Inspector `INSPECTOR_VERIFIER_TOKEN`; Verifier caller entry `token` |

Each service reads **process environment**, not the backend `.env`. Supply `INSPECTOR_META_API_KEY` to both, retaining the `muse-spark-1.3` model and Meta endpoint. Give them different `INSPECTOR_AGENT_DATA_DIR` paths, for example `geometry-inspector-data` and `geometry-verifier-data` under the runtime directory. Set `INSPECTOR_AGENT_PUBLIC_URL` to the corresponding loopback address. For both role-specific factories, the default profile version is `3`.

Verifier's private `INSPECTOR_AGENT_CALLERS_JSON` has this shape:

```json
[{"client_id":"discrepancy_inspector","project_id":"demo-building","token":"<verifier task token>"}]
```

Geometry Inspector's private caller list uses `client_id: "bob"`, the same project, and the Bob→Inspector task token. It also needs `INSPECTOR_AGENT_BACKEND_URL=http://127.0.0.1:8000`, its backend evidence token, `INSPECTOR_VERIFIER_TOKEN`, and:

```json
{"inspector_id":"discrepancy_verifier","kind":"discrepancy_verifier","version":"3","description":"Geometry Verifier","base_url":"http://127.0.0.1:8102","token_env":"INSPECTOR_VERIFIER_TOKEN"}
```

Store that object in its `INSPECTOR_VERIFIER_JSON` environment variable. Register **only Geometry Inspector** in the backend's `INSPECTOR_REMOTE_AGENTS_JSON`:

```json
[{"inspector_id":"discrepancy_inspector","kind":"discrepancy_inspector","version":"3","description":"Geometry Inspector","base_url":"http://127.0.0.1:8101","token_env":"INSPECTOR_TASK_TOKEN","tool_token_env":"INSPECTOR_TOOL_TOKEN","allowed_tools":["inspection_evidence"]}]
```

Supply the referenced task/tool secrets privately to the backend and set `INSPECTOR_GEOMETRY_REVIEW_INSPECTOR_ID=discrepancy_inspector`. Run these commands in **separate terminals with their own role configuration** and the path definitions from section 1:

```powershell
# Verifier terminal
& "$runtimeRoot\inspector-env\Scripts\python.exe" -m uvicorn ai_inspector.discrepancy_agents:create_verifier_app_from_env --factory --host 127.0.0.1 --port 8102
```

```powershell
# Geometry Inspector terminal
& "$runtimeRoot\inspector-env\Scripts\python.exe" -m uvicorn ai_inspector.discrepancy_agents:create_inspector_app_from_env --factory --host 127.0.0.1 --port 8101
```

Inspect each `/.well-known/agent-card.json` and `/contracts`; compare service IDs, profile version, caller scopes and both registrations. Cards do not prove a successful authenticated delegation or model response. Explicitly request an IFC/scan inspection with one selected IFC to register downstream review; Geometry completion alone does not start an unsolicited review. The full [A2A configuration and contracts](../ai_inspector/docs/ifc-discrepancy-review.md#13-настройка-и-использование) cover task headers, budgets, evidence limits and retry identities.

## 6. Progress4D: dated photo surveys and construction monitoring

Use its own Python 3.12 environment and [requirements.lock](../progress4d_service/requirements.lock). Native PyCOLMAP, IfcOpenShell and OpenCV availability depends on the operating system. The documented textured-surface development environment used official **macOS arm64 OpenMVS 2.4.0** binaries; a Windows surface run has not been established. The current native adapter expects executable names without `.exe`. Do not describe installing the Python lock on Windows as a verified textured-mesh setup.

```powershell
py -3.12 -m venv "$runtimeRoot\progress-env"
$progressPython = Join-Path $runtimeRoot 'progress-env\Scripts\python.exe'
& $progressPython -m pip install -r "$repoRoot\progress4d_service\requirements.lock"
Set-Location $repoRoot
$env:PROGRESS4D_DATA = Join-Path $runtimeRoot 'progress-data'
$env:PROGRESS4D_SERVICE_TOKEN = '<dedicated progress-service token>'
$env:PROGRESS4D_META_API_KEY = '<your Meta Model API key>'
$env:PROGRESS4D_EVIDENCE_VERSION = '2'
& $progressPython -m uvicorn progress4d_service.app:app --host 127.0.0.1 --port 8791 --workers 1
```

This launch uses sparse/stereo output unless `PROGRESS4D_OPENMVS_BIN` points to compatible native binaries. For the textured GLB path, follow [native reconstruction setup and licensing](../progress4d_service/README.md#native-surface-reconstruction). The service reads process environment. Without its Meta key, geometric processing may run but product states remain unknown. Use one process per persistent data directory.

Prepare one rectangular axis-aligned room, 8–100 overlapping upright photos per dated survey, and an IFC work schedule with dated tasks, assigned products and finish-start dependencies. A capture manifest contains timezone-qualified `captured_at`, `zone_id`, `source_kind` (or `synthetic`) and ordered photo file/hash entries. Keep one camera, fixed zoom and equal image dimensions. Keep the service running and open a separate CLI terminal at the repository root. Repeat the path definitions from section 1, then submit using the same service token:

```powershell
$progressPython = Join-Path $runtimeRoot 'progress-env\Scripts\python.exe'
$env:PROGRESS4D_SERVICE_TOKEN = '<same progress-service token>'
& $progressPython -m progress4d_service.cli --project '<project UUID>' submit --ifc 'C:\private\project.ifc' --manifest 'C:\private\week-01\capture-manifest.json'
& $progressPython -m progress4d_service.cli --project '<project UUID>' status --job '<returned job UUID>'
& $progressPython -m progress4d_service.cli --project '<project UUID>' viewer
```

Submission starts real reconstruction/model work. `/healthz` reports availability; authenticated `/contracts` describes the capture/schema contract. Neither establishes reconstruction quality. Preserve synthetic/recorded/site provenance, unknown/occluded states and conditional forecast labels.

To expose an existing project in chat, configure the native Inspector's `INSPECTOR_PROGRESS4D_BASE_URL`, matching `INSPECTOR_PROGRESS4D_SERVICE_TOKEN`, and `INSPECTOR_PROGRESS4D_CHAT_PROJECTS_JSON` mapping a chat UUID to the Progress4D project UUID. The `progress4d_result` tool reads saved results; it does not upload or reconstruct a new survey. The current Cloudflare entry point does not pass through these settings. See [Progress4D service](../progress4d_service/README.md) and [Inspector integration](../ai_inspector/docs/progress4d.md). Private weekly demo images/results are not included in a fresh clone.

## 7. Localization and iPhone capture

Localization has distinct development and deployment entry points. The standard-library `localization_service.server` harness on 8787 has no default solver; it is not the production phone path. The `lab_server` experiment on 8788 and the PostgreSQL-backed `cloud_server` on 8788 also have different authentication and input flows.

For the deployment profile, install [Localization requirements](../localization_service/requirements.txt) in another environment. A separately prepared restricted `localization` database schema/role is required; application startup does not provision it. Configure `LOCALIZATION_DATABASE_URL`, a distinct `LOCALIZATION_SERVICE_TOKEN` of at least 32 non-whitespace ASCII characters, `LOCALIZATION_DATA_DIR` and `PORT=8788`, then run `python -m localization_service.cloud_server` from the repository root using that environment. This entry point binds `0.0.0.0`; use a private host/container network and the deployment guide's HTTPS arrangement rather than treating it as a loopback-only example.

Inspector uses `INSPECTOR_LOCALIZATION_BASE_URL` and the matching `INSPECTOR_LOCALIZATION_SERVICE_TOKEN`. The service provisions session-specific phone credentials from a pinned IFC. Its importer supports one axis-aligned rectangular room, four walls, one floor, one space and metre units, with supported door/window openings. Whole buildings, rotated/nonrectangular rooms and general BIM registration are outside this implementation. Do not substitute a synthetic solver result for live localization. Details and the separate LAN experiment: [Localization](../localization_service/README.md), [deployment and dynamic BIM handoff](../localization_service/deploy/README.md).

For the phone, use **macOS and Xcode**, open [AIInspector.xcodeproj](../ios/AIInspector.xcodeproj), configure signing, and install on a physical compatible iPhone. The app targets iOS 17; RGB-only devices do not supply the RGB-D inputs required by Geometry. Simulator execution cannot validate ARKit/LiDAR. Enter the reachable Inspector HTTPS origin, device ID and device token in app settings; the token belongs in Keychain. Never place Meta/Storage/server credentials in the phone app. See [iPhone setup](../ios/README.md), [server integration](inspector-server-integration.md) and [mobile status](mobile-integration-status.md).

## 8. Verify and retain the run

Complete one task in the selected profile and retain its request/job ID, input hashes, source label, model/prompt provenance and final result alongside the recorded commit. Health endpoints and advertised capabilities are startup checks; a completed task demonstrates that particular workflow and supplied inputs. Field accuracy and construction acceptance require separate validation, as described in the [project verification boundaries](../README.md#verification-and-current-boundaries).

After a timeout, retry with the original request ID and complete body; retain persistent directories and server operation IDs across restarts. Do not relabel saved or synthetic evidence as a live capture. Component guides own deployment-specific migrations and acceptance checks. Additional development checks and private datasets in the optional companion workspace are not required to start these services and are not included in a plain clone.
