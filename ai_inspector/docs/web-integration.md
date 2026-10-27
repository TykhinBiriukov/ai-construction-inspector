# Operator / web application connection

Use `.local/web-connection.json` for the current `inspector_base_url`, scoped
operator token and device ID. This is a private operator configuration; the
separate iOS config contains only the device identity. API documentation is at
`<BASE_URL>/docs` and `<BASE_URL>/openapi.json`.

## Start the first capture case

The phone may already be connected, or it can connect later. Create a server-owned
session without requiring a reference BIM upload:

```javascript
const response = await fetch(`${config.inspector_base_url}/v1/capture-sessions`, {
  method: "POST",
  headers: {
    "Authorization": `Bearer ${config.operator_token}`,
    "Content-Type": "application/json"
  },
  body: JSON.stringify({
    schema_version: "0.1",
    request_id: crypto.randomUUID(),
    device_id: config.device_id,
    room_id: "room-204",
    capture_origin: "device",
    input_mode: "live_capture",
    prompt: "Record the selected room."
  })
});
const session = await response.json();
```

Persist `request_id` while the operation is pending and reuse it after a timeout.
201 creates, 200 replays, 409 reports conflicting reuse. The first-case inspection
has `inspection_kind=capture_only`, `model_version=unbound`, and no reference BIM.
The existing BIM intake plus inspection-specific session endpoint remain available.

The backend creates the Supabase session folder before advertising the assignment
to the device. The phone then acknowledges, captures and uploads through the API
documented in `ios-integration.md`.

## Monitor

| Request | Purpose |
|---|---|
| `GET /v1/devices/iphone-1/status` | Current authenticated foreground channel presence |
| `GET /v1/sessions/{session_id}` | Session acknowledgement, capture/source/job states and final geometry response |
| `GET /v1/capabilities` | API limits and explicit generator test/real/unconfigured mode |

The first case completes when `processing_state=completed`,
`processing_scope=geometry_only_v1`, and `geometry_result` is present. A mock result
is explicitly labelled. Failure is a terminal `failed` or `cancelled` state with
saved error. No inspection verdict or BIM comparison is implied.

For a browser app hosted on a different origin, set its exact origin in
`INSPECTOR_CORS_ORIGINS` (comma-separated) before starting the server. Native iOS
requests do not use browser CORS. Do not place an operator token in a public web
bundle: keep it in the authenticated operator environment or web backend.

## Run or stop the remote test environment

```powershell
.\scripts\start-remote-test.ps1 -MockGeometry
.\.venv\Scripts\python.exe scripts/simulate_iphone.py
.\scripts\stop-remote-test.ps1
```

The start script launches hidden backend/mock/tunnel helpers, uses the verified
official `tools/cloudflared.exe`, and writes private role-specific connection
files. `-MockGeometry` changes process configuration only; it does not overwrite
the real generator address in `.env`. Without that flag, the configured real
Geometry Service is used; an empty address leaves verified work queued.

For real G, provide its base URL and the two distinct service tokens. The backend
submits `PUT /internal/v1/jobs/{job_id}`, polls `GET` for the same job and accepts
durable notifications at `POST /internal/v1/geometry-events`. G obtains source
access through the fixed Inspector service routes; the phone never calls G.

Temporary Cloudflare URLs change when the tunnel restarts, so create new test
sessions after changing the public origin. For a permanent deployment use a stable
HTTPS hostname. The test URL exists only while this computer and tunnel run.
