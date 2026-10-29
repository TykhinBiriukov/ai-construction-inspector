# iPhone integration — first capture-to-geometry case

This guide describes the implemented API. No backend or Supabase admin credential
belongs in the iPhone app. The app uses the scoped Inspector device token from
the owner's `.local/ios-connection.json`. That ignored file contains the **current**
HTTPS base URL, WSS device-channel URL, `device_id`, `project_id`, and device token.
Send only that file and this guide to the iOS developer. The separate
`.local/web-connection.json` is for the operator who creates sessions.

## Current testing mode

The remotely accessible development server can run with an explicitly labelled
mock Geometry Service. It checks the source exchange and returns a synthetic PLY;
it does not reconstruct a room. `GET /v1/capabilities` reports `geometry_mode` as
`mock`, `external`, or `unconfigured`. A completed mock case records
`geometry_result.analysis_origin=mock`. The real generator address in `.env`
remains unchanged until its owner supplies it.

The temporary tunnel hostname changes after a new start, and the computer and
tunnel must stay running. Re-export the connection files after changing it.

## 1. Connect before a capture session exists

New session assignments optionally include `capture_task` with `text` (the
original operator request) and `source="operator_request"`. Display it as task
context; receiving it does not mean the camera should start automatically.
Older sessions may have no task. The field is delivered through both snapshot
and `session.assigned` events and persists across reconnect. Web-agent details:
[agent-chat.md](agent-chat.md). This backend increment does not implement the
corresponding iOS UI change.

Open the exact `device_websocket_url` from the connection JSON:

```text
wss://<HOST>/v1/devices/iphone-1/events/ws
Authorization: Bearer <DEVICE_TOKEN>
```

Swift's `URLSessionWebSocketTask` supports a `URLRequest` with this header; see
`examples/InspectorConnection.swift`. Browser clients that cannot set WebSocket
headers can instead send this as the first message within ten seconds:

```json
{"type":"authenticate","token":"<DEVICE_TOKEN>"}
```

Do not put the token in the URL. The device does not create its own capture ID.
The server responds:

```json
{"type":"device.connected","device_id":"iphone-1","schema_version":"0.1","heartbeat_interval_s":15}
```

Then, on a fresh connection:

```json
{"type":"device.snapshot","schema_version":"0.1","device_id":"iphone-1","sessions":[],"has_more":false,"resume_cursor":"OPAQUE_CURSOR"}
```

Sessions created while the phone was offline appear in this snapshot. For more
than 100 existing sessions, retrieve the paginated assignment list as well.
Restore sessions by ID and revision. **Only `capture_state=assigned` starts a new
capture; completed/sealed sessions in a snapshot are history, not new tasks.**

When the operator creates a session, the channel sends:

```json
{
  "type":"events",
  "events":[{
    "schema_version":"0.1","event_id":"EVENT_UUID","type":"session.assigned",
    "session_id":"SESSION_UUID","created_at":"UTC_TIMESTAMP","cursor":"OPAQUE_CURSOR",
    "session":{"session_id":"SESSION_UUID","capture_state":"assigned","revision":2,"storage":{"provider":"supabase","bucket":"inspection-captures","key_prefix":"projects/demo-building/sessions/SESSION_UUID/","folder_status":"ready"}}
  }],
  "next_cursor":"OPAQUE_CURSOR","has_more":false
}
```

The example session is abbreviated; the actual event carries the full session,
including inspection/project/room/model IDs, capture origin and input mode.
Persist the ID and pinned context locally. Never substitute a new local session ID.

Send `{"type":"pong"}` when the server sends `{"type":"ping"}`. A client may
also send `{"type":"ping"}` and receive `pong`. The server closes after 45 seconds
without a client control message. Background iOS execution is not required; reopen
and resynchronize when the app becomes active.

After applying an entire event packet, persist `next_cursor`. Reconnect with
`?after=OPAQUE_CURSOR` or include `after` in the initial authentication message.
Replay uses the same packets/cursors as authenticated
`GET /v1/devices/{device_id}/events?after=...&limit=100`. Deduplicate by event ID
and reject older session revisions. On an invalid cursor, discard it and obtain
a fresh snapshot. This event resume cursor differs from assignment-list pagination.

## 2. Acknowledge and start

Every HTTP call to Inspector uses `Authorization: Bearer <DEVICE_TOKEN>` and JSON
requests use `Content-Type: application/json`.

After saving the ID, POST `/v1/sessions/{session_id}/acknowledgements`:

```json
{"schema_version":"0.1","operation_id":"ACK_UUID"}
```

This confirms receipt and leaves capture `assigned`. When recording actually
starts, POST `/v1/sessions/{session_id}/capture-start`:

```json
{"schema_version":"0.1","operation_id":"START_UUID","device":{"name":"iPhone","os_version":"ACTUAL_OS_VERSION","app_version":"ACTUAL_APP_VERSION"}}
```

Keep the same operation IDs and content on retry. New success is 201, replay is
200, conflicting reuse is 409. `capture-start` is required before upload plans.

## 3. Save observations and request upload permissions

For **one ZIP containing the whole capture**, use the alternative protocol in
[zip-capture.md](zip-capture.md). It uses `kind=capture_archive`, a raw
`application/zip` signed upload, and `format=supabase_capture_zip_v1` on completion.
The following individual-photo workflow remains supported.

Each ARKit reset uses a new client-generated `segment_id` within the same session.
Each observation gets a UUID. Store `frame.json`, original `rgb.jpg`, and depth /
confidence files when available. File hashes are lowercase SHA-256 of the exact
stored bytes. Preserve original RGB/depth alignment and poses from the same frame.
Use frame.json 0.2 (legacy RGB-D 0.1 remains supported).

For RGB-only 0.2: `depth_source="none"`; `depth`, `depth_size`, `depth_unit`,
`depth_to_rgb_mapping`, and `confidence` must all be explicit null. No empty depth
files. Real captures use `capture_origin=device`, `tracking_state=normal`; the
test fixture's synthetic origin/tracking must not be used for real measurements.
For this first case `instruction_id=null`.

POST `/v1/sessions/{session_id}/upload-plans`:

```json
{
  "schema_version":"0.1","request_id":"UPLOAD_REQUEST_UUID","kind":"observation_files","completion_id":null,
  "objects":[
    {"observation_id":"OBSERVATION_UUID","name":"frame.json","media_type":"application/json","byte_length":2048,"sha256":"ACTUAL_SHA256"},
    {"observation_id":"OBSERVATION_UUID","name":"rgb.jpg","media_type":"image/jpeg","byte_length":123456,"sha256":"ACTUAL_SHA256"}
  ]
}
```

Send at most 50 objects per request. Allowed names/media types:

| Name | Media type | Required |
|---|---|---|
| `frame.json` | `application/json` | Yes |
| `rgb.jpg` | `image/jpeg` | Yes |
| `depth.f32` | `application/octet-stream` | When frame.depth is non-null |
| `confidence.u8` | `application/octet-stream` | When frame.confidence is non-null |

The backend returns `format=supabase_upload_plan_v1` and an item for each object.
For `status=upload_required`, PUT **raw file bytes** to its `upload_url` using the
exact `headers` and `method` returned. Use a fresh request to that URL, with **no
Inspector Authorization header, Supabase admin key, or API token added**. Supabase
signed URLs grant only that path and prohibit overwrite. `version_id` is null,
because these are Supabase objects, not versioned S3 objects.

For `already_uploaded`, retain `object_key`, hash and size; no upload is needed.
A timeout or duplicate-object response can be resolved by repeating the same
upload-plan request: the backend checks actual bytes and returns existing status.
Renew permissions through the same request ID; never change an observation's bytes
under its existing ID. JSON bodies and binary bytes must be retained offline.

## 4. Upload the final manifest last

Use the exact pinned context from the assignment. Supabase manifest contents:

```json
{
  "schema_version":"0.1","format":"capture_manifest_supabase_v1",
  "session_id":"SESSION_UUID","inspection_id":"INSPECTION_UUID","project_id":"demo-building",
  "room_id":"room-204","model_version":"unbound","capture_origin":"device","input_mode":"live_capture",
  "capture_closed_at":"2026-10-27T14:00:00Z","observation_count":1,
  "segments":[{"segment_id":"SEGMENT_UUID","created_at":"2026-10-27T13:59:00Z","units":"m","coordinate_convention":"arkit_world_camera_v1","matrix_layout":"rows_column_vectors"}],
  "observations":[{"observation_id":"OBSERVATION_UUID","segment_id":"SEGMENT_UUID","files":[
    {"name":"frame.json","object_key":"RETURNED_FRAME_KEY","byte_length":2048,"sha256":"ACTUAL_FRAME_SHA256"},
    {"name":"rgb.jpg","object_key":"RETURNED_RGB_KEY","byte_length":123456,"sha256":"ACTUAL_RGB_SHA256"}
  ]}]
}
```

All placeholders require real values. Copy returned object keys, do not invent
paths. Include every recorded observation intended for processing, no duplicates.
Include depth/confidence refs when present; omit S3 `version_id` entirely.

Create a UUID `completion_id` and exact final UTF-8 manifest bytes. Get another
upload plan with `kind=capture_manifest`, that completion ID, and one object:
`observation_id=null`, `name=capture-manifest.json`, `media_type=application/json`,
and the manifest's real byte length/hash. Upload the exact bytes to its signed URL.

## 5. Notify completion

POST `/v1/sessions/{session_id}/complete`:

```json
{
  "schema_version":"0.1","completion_id":"COMPLETION_UUID",
  "capture_ref":{
    "format":"supabase_capture_v1","storage_id":"capture-supabase",
    "manifest_key":"RETURNED_MANIFEST_OBJECT_KEY","manifest_byte_length":4096,"manifest_sha256":"ACTUAL_MANIFEST_SHA256"
  }
}
```

202 means the completion message was durably saved, not that geometry is ready.
Replay returns 200 with that operation's current status. The backend verifies the
source and pins the exact accepted bytes in a separate server-owned Supabase
snapshot. Existing phone upload grants cannot modify the accepted copy. The original
manifest hash and logical file keys remain intact; G receives download URLs for
the verified copies. No S3 version IDs are invented.

## 6. Observe the first-case result

`session.updated` events carry the latest full session. HTTP fallback is
`GET /v1/sessions/{session_id}`. Typical states:

| capture_state | processing_state | Meaning |
|---|---|---|
| `assigned` | `idle` | Session delivered, capture has not started |
| `capturing` | `idle` | Recording / uploads |
| `submitted` | `validating` | Completion received, checking storage |
| `sealed` | `queued_geometry` | Source pinned, generator job waiting |
| `sealed` | `building_geometry` | Generator processing |
| `sealed` | `completed` | First case done; server saved verified Geometry response |
| `sealed` | `failed` / `cancelled` | Terminal generator failure/cancellation |

On completion, read `geometry_result`, `geometry_job`, and `processing_scope`.
`processing_scope=geometry_only_v1`; the separate inspection-analysis `result`
remains null. A mock result has `analysis_origin=mock` and synthetic artifact
metadata. Artifact URLs are service-facing; the app does not receive G credentials.

If source validation rejects the set, capture returns to `capturing`; correct the
files and create a **new** completion ID/manifest. Temporary storage/generator
failures remain queued and are retried. A sealed session never reopens for new photos.

## Failure and reconnect handling

API errors have `code`, `message`, `retryable`, `details`. 401/403 requires fixing
identity; 409 requires inspecting the code; 422 requires fixing the request;
transient failures retry the same IDs/body with backoff. Preserve local capture
files until the application has observed the accepted source and required result.

The operator can check `/v1/devices/{device_id}/status` for channel presence. If a
WebSocket closes, reconnect with the last applied event cursor and exponential
backoff. Snapshot/replay survives backend restart. The app itself initiates the
network connection; it does not need an inbound HTTP server or a public phone IP.

## Reference implementation and public documentation

- `examples/InspectorConnection.swift`: small URLSession transport helper; integrate
  capture generation/state handling in the actual app. It is a reference example,
  not an Xcode project validated on this Windows host.
- `../../local-checks/inspector/scripts/simulate_iphone.py`: owner-side synthetic full-case network test.
- API schema: `<BASE_URL>/openapi.json`; interactive HTTP docs: `<BASE_URL>/docs`.
- [Apple URLSessionWebSocketTask](https://developer.apple.com/documentation/foundation/urlsessionwebsockettask)
- [Supabase signed upload URLs](https://supabase.com/docs/reference/swift/storage-from-uploadtosignedurl)

Codex verification tests, diagnostic scripts and their temporary data live
outside this checkout in the workspace's local-checks/inspector/ directory.
These optional local files are not part of the distributed backend or its
runtime. Run the local suite with ../../local-checks/inspector/run-tests.ps1.

## Independent test captures (29 October 2026)

`POST /v1/standalone-captures/{session_id}/upload-plans` accepts a client-generated UUID and the existing `UploadPlanRequest`, limited to `observation_files` and `capture_manifest`. Requires the device bearer identity. It does not create/assign an Inspector session, invoke localization, accept a normal completion or enqueue Geometry. It uses the existing Supabase credentials only on the backend.

Private bucket objects live under `standalone/{authenticated_project}/{authenticated_device}/{session_id}/`. Observation paths are `observations/{observation_id}/{sha256}/{name}`; manifests are `manifests/{sha256}/capture-manifest.json`. The response uses `supabase_upload_plan_v1`; repeated requests return `already_uploaded` only after matching stored length and SHA-256. Size limits and allowed names/media types are the same as normal per-file capture uploads. Content addressing and non-upsert signed URLs make retries immutable without Inspector database records.

The iPhone uploads a `standalone_capture_v1` manifest last, after checking all uploaded files via renewed plans. It contains the normal capture inventory with exact `object_key`, length and digest. Manifest `inspection_id`/`project_id` values are `standalone` markers, not Inspector entities; ownership comes from the authenticated prefix. Consumers should discover manifests, not treat partial upload folders as complete. This is a test-data export, not an accepted inspection; authorized devices are responsible for publishing an accurate inventory. RGB/depth/pose formats are unchanged and coordinates are local to each ARKit segment.

### Device cancellation

The assigned device may call `POST /v1/sessions/{session_id}/abort` with its existing bearer token. Project/device ownership is checked before mutation, as with other session actions; operator cancellation remains supported. Cancellation is idempotent, invalidates pending worker leases, rejects pending completion, and retains capture files. Completed/failed sessions retain the existing terminal-state rejection. The phone stops capture and clears its active assignment only after a matching `capture_state=cancelled` response; failure leaves a retryable visible assignment. This does not revoke a signed upload URL already issued or terminate an external job already dispatched.
