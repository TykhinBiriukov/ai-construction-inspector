# AI Inspector — first capture-to-geometry case

Current developer handoff:

- [Cloudflare + Supabase deployment](docs/cloudflare-deployment.md) — permanent HTTPS, no Mac required

- [iPhone API and connection guide](docs/ios-integration.md)
- [Operator / web application guide](docs/web-integration.md)
- [Single-ZIP capture format and upload examples](docs/zip-capture.md)
- `scripts/start-remote-test.ps1 -MockGeometry`: external HTTPS/WSS test environment
- `.venv/bin/python scripts/host_mac.py start`: macOS background Inspector + temporary HTTPS (use `status` / `stop` to manage it)
- `.local/ios-connection.json`: private phone URL/device credentials
- `.local/web-connection.json`: private operator connection settings

The complete implemented first case is: device WebSocket connection -> server
session/folder creation -> device acknowledgement and capture -> signed Supabase
uploads -> source snapshot -> Geometry job -> saved final Geometry response.
`POST /v1/capture-sessions` can start it without requiring a reference BIM upload.
`scripts/simulate_iphone.py` exercises the full network path with synthetic data.
Add `--zip` to upload the entire capture as one ZIP. Individual-photo uploads
remain available. ZIP captures are verified and normalized to the same Geometry
source interface before processing.

This backend receives an opaque BIM file and an inspection prompt, stores an
inspection, creates a capture session, and lets the assigned iPhone retrieve and
acknowledge its session ID. After capture, it receives a Supabase snapshot reference
or versioned S3 manifest, verifies the source, and queues a durable Geometry job.
SQLite persists local development state. With `INSPECTOR_DATABASE_URL`, PostgreSQL
persists state, retry receipts, completions and the delivery queue. Reference BIM
bytes use the local adapter by default or private Supabase Storage when
`INSPECTOR_BIM_STORAGE=supabase`.
The backend consumes Geometry callbacks and polls exact job state as a recovery
path. A verified terminal response completes the geometry-only case; the separate
inspection-analysis result stays null. Mock results are explicitly labelled.

The generator address is intentionally empty by default. With no capture bucket,
received completions wait for storage configuration. With storage configured but
no generator address, verified sources remain queued for dispatch.

## Run locally

Python 3.12 or later is required. From this `ai_inspector` directory in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"

.\.venv\Scripts\python.exe -m uvicorn ai_inspector.main:app --host 127.0.0.1 --port 8000
```

Before the first startup, create a local `.env` from `.env.example` if it does not
already exist, and set distinct random `INSPECTOR_OPERATOR_TOKEN` and
`INSPECTOR_DEVICE_TOKEN` values. This workspace's local `.env` has already been
configured. The file is ignored by Git and is never returned by the API.

Open [interactive API documentation](http://127.0.0.1:8000/docs) and use its
**Authorize** button with the appropriate token. `.env.example` lists all settings;
the local `.env` is loaded automatically from the working directory, and process
environment variables override it. Keep the same tokens across
backend restarts and configure the device token in the iPhone app. There are no
built-in credentials. This first increment has one configured project and device,
with distinct operator/device bearer identities rather than an identity provider.

For a foreground iPhone on the same LAN, bind Uvicorn with `--host 0.0.0.0` and use
the computer's LAN address as the app's `inspector_base_url`. Local HTTP is for
development; the contract's deployed transport is HTTPS/WSS.

## HTTP flow

For the smaller capture-only case, use the operator's JSON
`POST /v1/capture-sessions` with `schema_version`, UUID `request_id` and `device_id`.
The app opens `/v1/devices/{device_id}/events/ws` first and receives the assigned
session. The optional BIM-input flow below remains available.

1. As **operator**, `POST /v1/inspections` using multipart form data:
   `schema_version=0.1`, a UUID `request_id`, `project_id`, `room_id`, `prompt`,
   and file field `bim_model`. Optional `model_is_synthetic` defaults to false.
2. As **operator**, `POST /v1/inspections/{inspection_id}/capture-sessions`:

   ```json
   {
     "schema_version": "0.1",
     "request_id": "36e484a6-c309-4c39-b64d-fdb54a9db175",
     "device_id": "iphone-1",
     "room_id": "room-204",
     "capture_origin": "device",
     "input_mode": "live_capture",
     "parent_session_id": null
   }
   ```

   When the three Supabase settings are configured, creation also materializes
   `projects/{project_id}/sessions/{session_id}/` in the configured bucket with a
   small `.session.json` marker. The response's `storage` includes `provider`,
   `project_url`, `bucket`, `key_prefix`, and `folder_status=ready`; it contains no
   secret key. A failed storage request returns a retryable 503 but retains the
   session ID. Retry the same request ID to finish the same folder. Assignments
   remain hidden from the device until the folder is ready.

3. As **device**, `GET /v1/devices/iphone-1/capture-assignments`. The response's
   `assignments` array contains the generated `session_id` and pinned context.
   Follow non-null `next_cursor` using `?after=...`. Start a new traversal to find
   later assignments; deduplicate locally by `session_id` and persist the ID
   before acknowledgement.
4. As **device**, `POST /v1/sessions/{session_id}/acknowledgements`:

   ```json
   {
     "schema_version": "0.1",
     "operation_id": "d7ea3c44-0501-4ae5-ae54-03f36f7903bd"
   }
   ```

   The receipt contains `session_id`, `operation_id`, and `acknowledged_at`.
   Receiving the session leaves `capture_state=assigned` and
   `processing_state=idle`. The operator can confirm receipt through
   `GET /v1/sessions/{session_id}` and its `device_acknowledged_at` field.
5. When capture actually starts, as **device**, call
   `POST /v1/sessions/{session_id}/capture-start`:

   ```json
   {
     "schema_version": "0.1",
     "operation_id": "05c75182-ad6a-44b0-b823-90bdb5b3c1d7",
     "device": {
       "name": "iPhone",
       "os_version": "your-os-version",
       "app_version": "0.1.0"
     }
   }
   ```

   This advances capture state to `capturing`. A separate receipt acknowledgement
   is optional; capture-start can be sent directly.

6. After uploading all capture objects and the final manifest, as **device**,
   call `POST /v1/sessions/{session_id}/complete`. Send `schema_version`, a persistent
   UUID `completion_id`, and `capture_ref` containing `storage_id`, exact
   `manifest_key`, `manifest_byte_length`, and `manifest_sha256`. S3 additionally
   requires `manifest_version_id`; Supabase uses `format=supabase_capture_v1` with
   no version ID. This is a logical reference, not an expiring download URL.
   The backend returns `202` after saving it, then verifies the exact manifest and
   file versions in a background worker. `GET session` exposes pending, rejected,
   or accepted completion status and the received reference.
7. A valid source seals capture and atomically creates one persistent geometry job
   and delivery operation. The worker sends
   `PUT {INSPECTOR_GEOMETRY_BASE_URL}/internal/v1/jobs/{job_id}` with a stable
   authenticated Inspector source URL. Retries preserve the same job, operation
   key, source versions, and command bytes. An empty generator URL keeps the job
   queued. The operator can bring forward a pending retry through
   `POST /v1/sessions/{session_id}/geometry-jobs/dispatch`; delivery is also automatic.

New operations return `201`; successful retries with the same IDs and content
return `200`. Retain IDs and request bodies on retry. Conflicting ID reuse returns
`409`. Session creation pins project/model/policy from the persisted inspection.

Operators can also retrieve inspection metadata with
`GET /v1/inspections/{inspection_id}` and original BIM bytes with
`GET /v1/inspections/{inspection_id}/bim-model`. These are operator-only endpoints.

## Exercise the handoff

In another terminal from the same directory, the client reads the same `.env`:

```powershell
.\.venv\Scripts\python.exe scripts/smoke_test.py --bim "C:\path\room.ifc" --prompt "Inspect the doorway dimensions."
```

The script acts as the phone over HTTP: it uploads intake, creates a session,
retrieves the assignment, acknowledges it, and checks retry behavior. It leaves
capture in `assigned`; it does not operate an actual iPhone camera.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Integration tests cover persistence across app restart, concurrent session
creation, transactional rollback, pagination, replay/conflicts, project/device
access, closed-session rules, bounded BIM intake, exact S3 versions/checksums,
incomplete capture rejection, generator delivery/timeouts, and background recovery.

## Configure capture completion and generator delivery

For versioned AWS S3, set `INSPECTOR_CAPTURE_BUCKET` and
`INSPECTOR_CAPTURE_REGION`; AWS access uses boto3's normal credential chain. AWS
capture upload-plan issuance is not implemented. For the active Supabase path,
the device uses the implemented `POST /v1/sessions/{id}/upload-plans` endpoint;
details and example requests are in the iPhone guide.

When its API address is available, set `INSPECTOR_GEOMETRY_BASE_URL`,
`INSPECTOR_PUBLIC_BASE_URL` (the Inspector address reachable by the generator), and
distinct `INSPECTOR_GEOMETRY_API_TOKEN` / `INSPECTOR_GEOMETRY_SERVICE_TOKEN` values.
Restart the backend with the same data directory. Existing pending operations
resume; no new capture or session is needed. See `.env.example` for configurable
limits, profile version, and retry timing. Enable RGB-only dispatch explicitly only
after confirming generator support with `INSPECTOR_GEOMETRY_ACCEPTS_RGB_ONLY=true`.

The worker starts with Uvicorn. For maintenance, the same queue can be advanced
with `python -m ai_inspector.worker --once`; work leases prevent competing workers
from applying duplicate results. Transient errors back off and remain pending.
Permanent source errors return capture to `capturing` and require a new
completion ID/manifest. A generator error never reopens a sealed capture.

See [capture-to-generator requests and states](docs/capture-to-geometry.md) for the
full payloads and current integration boundary.

## Supabase session folders

Set `SUPABASE_URL`, `SUPABASE_STORAGE_BUCKET`, and `SUPABASE_SECRET_KEY` together in
the local `.env` or server environment. The key is used only by the backend, via
Supabase's `apikey` header. It is not sent to the phone, recorded in session
metadata, or included in `.env.example`. The adapter uses HTTPS with no redirects
and never overwrites an existing marker. Bucket creation is outside this endpoint;
the configured bucket must already exist.

Supabase stores objects under prefixes, so the marker makes an otherwise empty
session folder visible. Its contents are the session/inspection/project IDs and
creation timestamp. Provisioning happens outside the database write lock, after
the ID and retry receipt commit; an interrupted request can finish the same
folder after restart. A ready folder is never recreated during successful replay.

Supabase's S3 API does not support S3 object versioning. The implemented adapter
uses `capture_manifest_supabase_v1` without S3 versions, verifies exact hashes and
lengths, and pins copies under `accepted/{completion_id}/...` before accepting the
source. Phone grants target only raw observation/manifest paths and never allow
overwrite. Generator download links point to the server-owned accepted copies;
later raw uploads cannot change accepted input. The original manifest bytes and
logical file keys stay unchanged, while private pin keys are held in the accepted
inventory. The existing versioned AWS S3 path remains supported.

## Current boundary

The BIM file is **stored, not parsed or normalized**. Its status is
`stored_unvalidated`; `upload-sha256:...` is an immutable intake version, not a
normalized geometry package. `room_id` is caller-supplied context and is checked
against the inspection, not extracted from the BIM. The prompt is stored as user
input and is not automatically converted into phone instructions.

Remote BIM integration, measured coverage, rendering, and LLM execution belong to
later increments. Source access is available only to the configured Geometry
identity and only for accepted source copies/versions. The first case completes
with `geometry_result` and `processing_scope=geometry_only_v1`, without creating an
inspection verdict. The actual iPhone app still needs to wire these network calls;
this checkout includes a Swift transport reference and remote connection files.

See [the first-increment contract additions](docs/first-increment-contract.md).

## Operator UI and deployment

The service now serves an operator console at `/`: capture assignments, BIM
intake, device status, persistent session history and Geometry results. See
[operator-console.md](docs/operator-console.md) for authentication, supported
operations, local/container launch, verification and the Oracle Always Free
hosting target. LLM execution and BIM comparison remain future work.
