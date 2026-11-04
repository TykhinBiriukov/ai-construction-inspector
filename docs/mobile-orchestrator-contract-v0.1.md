> **Superseded for the current first case:** use [Inspector server integration](inspector-server-integration.md) and [mobile implementation status](mobile-integration-status.md). The following document is an older architecture proposal.

# iPhone ↔ Geometry Service + AI Inspector API — v0.1, for agreement

Date: 2026-10-27. **PROPOSED / NOT AGREED.** This is an interface proposal, not a description of an operational server. Once agreed, both sides implement the same contract.

## Addendum: mobile frame schema 0.2 (27 October 2026)

At Oleg's request, the app now supports phones without LiDAR. This is a local implementation; server acceptance of new frames **still requires agreement**. The v0.1 sections/examples below retain the original RGB-D proposal; new frames use these changes:

- `frame.json.schema_version` = `"0.2"` for new RGB and RGB-D observations. The app reads legacy `0.1` frames. Session manifest version remains `0.1`.
- Without LiDAR: `depth_source: "none"`; `depth`, `depth_size`, `depth_unit`, `depth_to_rgb_mapping`, `confidence` are JSON `null`. Such observations must omit depth/confidence multipart parts. Do not substitute zero or synthetic depth.
- JPEG, RGB dimensions, `K_rgb`, `T_world_camera`, timestamp and session/segment/frame IDs are retained. Position is an ARKit estimate, not BIM alignment. Capture requires normal tracking.
- With LiDAR, depth format is unchanged; frames are not saved until sceneDepth is ready.
- Geometry Service must explicitly distinguish RGB and RGB-D: missing measured depth is neither a measurement nor proof of geometric compliance. RGB-only processing support and unsupported-input responses require agreement.
- Manifest depth/confidence encoding descriptions apply only to frames containing the corresponding file.

## 1. Implementation responsibilities

```text
                     iPhone app
                      ↙     ↘
             HTTPS /         \ HTTPS /
             RGB-D, models    instructions, responses
                  ↕               ↕
          Geometry Service ↔ AI Inspector
                  direct inter-service API
```

**Confirmed architecture:** the phone communicates directly with both services; the services also communicate with each other. AI Inspector is not a mandatory proxy for RGB-D upload or Geometry Service access. The specific routes and synchronization sequence below are proposed for agreement.

- **Geometry Service (G)**: RGB-D intake/storage, file validation, geometry processing, model/alignment/coverage. Also proposed: serving a prepared BIM package; converter ownership still requires agreement.
- **AI Inspector (I)**: instructions, text responses, AI analysis and inspection outcome. Any internal orchestrator is an implementation detail, not the phone's single entry point.
- **iPhone app**: two HTTP clients/adapters, local persistence, a G upload queue and a separate I response queue. No inbound HTTP server is needed on the phone.
- **G ↔ I**: Inspector obtains metadata, files and geometry directly from G; G notifies Inspector of accepted/ready data. The phone does not relay results between microservices.
- Model keys remain on the backend. The phone does not call AI-model providers directly.
- **One observation = one photograph with corresponding depth and metadata**, not a batch.
- PLY as Geometry Service input does not make it mandatory phone output: RGB-D → PLY conversion is proposed in G or its backend adapter. This still requires separate agreement with the Geometry Service owner.

## 2. Implemented and proposed behavior

Already in iOS code: local sessions and observation files, UUIDs, SHA-256, synthetic source and ARKit capture code. The format is written in [`Observation.swift`](../ios/AIInspector/Observation.swift).

**Not implemented:** network client, server uploads, authorization, event reception, BIM import, backend alignment/coverage and voice. Local demo instructions are not AI responses. Real RGB-D acquisition and geometric correctness have not yet been tested on iPhone.

The document below retains the existing `frame.json` format. New network envelopes (project_id, app_version, revisions, receipts, upload state) require implementation. Do not automatically treat the existing `SessionManifest` as a complete HTTP request.

## 3. General rules

- Two configurable addresses: `geometry_base_url` (G) and `inspector_base_url` (I). Even identical paths are different APIs. Remote services use HTTPS; service owners supply actual addresses.
- `Authorization: Bearer <token>`. Issuance/refresh requires agreement; rights are limited by project and service audience. Client configuration supports separate G/I tokens stored in Keychain, never automatically forwarded across hosts. Inter-service calls use a separate service identity unavailable to the app.
- JSON UTF-8; dates UTC RFC 3339. Frame `timestamp_s` is monotonic ARKit time, **not UTC**.
- The client generates session, segment, observation, response and completion UUIDs. Use the same string UUID in all references/retries. Backend assigns project/room/model IDs; I assigns instruction IDs; the source service assigns event IDs. Cursors/revisions are service/resource-scoped rather than globally numbered.
- `schema_version: "0.1"`. Reject unknown versions with 422. Optional fields may be added; changing existing units/axes/field meanings requires a new version.
- Errors and receipts are JSON. HTTP 2xx does not mean successful inspection.
- For the MVP, one phone writes one session. Multiple phones use separate sessions.
- Agree observation-size, observation-count and request-rate limits before integration. Do not silently crop images or depth.

## 4. MVP workflow

1. The phone obtains a prepared model from **G**, creates one session_id and registers immutable session configuration first with G, then with **I** under the same ID.
2. Register segment_id with G. Capture can be saved locally offline; G upload requires registration there. AI instructions begin after successful I registration.
3. The phone saves an observation and uploads RGB-D directly to **G**. Only G's durable receipt increments the “Sent” counter.
4. **G directly notifies I** of accepted and then ready data. I obtains required images/metadata/geometry from G itself.
5. The phone polls **I** for instructions and **G** for geometry status. These states are independent.
6. When additional capture is requested, the phone uploads a frame to G with instruction_id, then sends I a response with observation_id. I verifies acceptance with G; notifications may arrive late.
7. **I** determines evidence sufficiency and controls instructions; a button/upload alone does not complete a task.
8. The phone requests completion from **I**. I freezes new instructions and coordinates capture sealing directly with G. This is one coordinating operation, not upload proxying through Inspector.

## 5. Phone routes

| Service | Method and path | Purpose | Success |
|---|---|---|---|
| G | `GET /v1/projects/{project_id}/models/{model_version}` | Prepared model and BIM IDs | 200 |
| G | `PUT /v1/sessions/{s}` | Register data session | 201 / 200 on replay |
| I | `PUT /v1/sessions/{s}` | Register inspection for the same session | 201 / 200 on replay |
| G | `PUT /v1/sessions/{s}/segments/{g}` | Coordinate segment | 201 / 200 on retry |
| G | `PUT /v1/sessions/{s}/observations/{o}` | One observation, multipart | 201 / 200 on retry |
| G | `GET /v1/sessions/{s}/uploads?after={cursor}&limit=100` | Reconcile accepted observations | 200 |
| G | `GET /v1/sessions/{s}` | Geometry status and result links | 200 |
| I | `GET /v1/sessions/{s}/events?after={cursor}&limit=100` | Inspection instructions and updates | 200 |
| I | `GET /v1/sessions/{s}/instructions` | Instruction snapshot and I cursor | 200 |
| I | `POST /v1/sessions/{s}/instructions/{i}/responses` | User response | 201 / 200 on retry |
| I | `POST /v1/sessions/{s}/complete` | Coordinated capture completion | 202 / 200 on retry |
| I | `GET /v1/sessions/{s}` | AI inspection status and result | 200 |

`s` = session_id, `g` = segment_id, `o` = observation_id, `i` = instruction_id. Cursors are opaque strings. Examples are schematic: replace `SESSION_UUID` and similar values with actual IDs; zero hashes are placeholders, not test files a server should accept.

### 5.1 Model for display

**G** GET model response:

```json
{
  "schema_version": "0.1",
  "project_id": "demo-building",
  "model_version": "demo-room-v1",
  "room_ids": ["room-204"],
  "is_synthetic": true,
  "units": "m",
  "axes": "right_handed_y_up",
  "asset": {
    "url": "/v1/assets/demo-room-v1.usdz",
    "media_type": "model/vnd.usdz+zip",
    "byte_length": 123456,
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "T_model_asset": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
  "elements": [
    {"element_id":"door-01","ifc_guid":null,"room_id":"room-204","node_name":"door-01","type":"door","label":"Doorway"}
  ]
}
```

We propose USDZ prepared by the backend or a separate converter. Importing arbitrary RVT/IFC files on the phone is outside the MVP. `T_model_asset` transforms asset points into the BIM model coordinate system; meters and axes must be explicitly normalized. `node_name` must exist in the asset; if an element has multiple parts, group them under that node. The application's current generated scene is a separate demo, not this package yet.

The asset URL is a relative path on the Geometry Service API or a signed HTTPS URL. Do not forward the application's bearer token to a third-party host. The backend determines signed URL expiry; when it expires, the client repeats GET model. `model_version` is immutable; new geometry or IDs require a new version.

### 5.2 Session

The same JSON is sent to **G** PUT session and **I** PUT session:

```json
{
  "schema_version": "0.1",
  "session_id": "SESSION_UUID",
  "project_id": "demo-building",
  "room_id": "room-204",
  "model_version": "demo-room-v1",
  "model_is_synthetic": true,
  "capture_origin": "device",
  "input_mode": "live_capture",
  "created_at": "2026-10-27T10:00:00Z",
  "device": "iPhone",
  "os_version": "26.6.1",
  "app_version": "0.1.0",
  "parent_session_id": null
}
```

`capture_origin`: `device | synthetic`; `input_mode`: `live_capture | recorded_capture | synthetic_fixture`. Replay does not change the provenance of real data to synthetic; original frame timestamps are preserved. A session does not mix sources. `model_is_synthetic` is independent of the capture source: the phone can currently capture while displaying a synthetic room, but this is not a verified alignment.

Registration receipts: G — `{"session_id":"SESSION_UUID","capture_state":"open","revision":1}`; I — `{"session_id":"SESSION_UUID","state":"capturing","revision":1}`. These are abbreviated receipts; see §5.7 for complete states.

**G is the source of truth for session configuration and evidence; I is the source of truth for instructions and inspection results.** During registration, I reads the configuration from G using the interservice GET (§5.8) and checks every field for equality. Session unknown to G → 409 `geometry_session_missing`; different parameters → 409 `session_config_mismatch`; G unavailable → 503. I does not create a separate session_id.

Path and body IDs must match. Repeating the same fields returns 200; different content for the same ID returns 409 `idempotency_conflict`. PUT is not used for arbitrary updates. The client preserves the original body and two independent registration flags. If G accepts registration but I is unavailable, data in G is not rolled back: the client retries registration with I using the same ID; the UI shows "Inspector not connected". G does not require successful registration with I to accept RGB-D.

The parent_session_id field links follow-up capture to a previous session in the same project and model. Both services check access to the parent. Authorized services retrieve configuration through fixed backend addresses, not an arbitrary URL in a client request.

### 5.3 Coordinate segments

**G** PUT segment, JSON:

```json
{
  "schema_version": "0.1",
  "session_id": "SESSION_UUID",
  "segment_id": "SEGMENT_UUID",
  "created_at": "2026-10-27T10:00:01Z",
  "units": "m",
  "coordinate_convention": "arkit_world_camera_v1",
  "matrix_layout": "rows_column_vectors"
}
```

Response: `{"session_id":"SESSION_UUID","segment_id":"SEGMENT_UUID"}`. Register a segment **before** uploading its first frame. After an ARKit restart/reset or unverified relocalization, create a new segment_id. The current client creates a new segment each time the camera starts. Do not assume a shared coordinate system across segments. The geometry backend aligns them separately if possible. Repeating the same values returns 200; different content for the same ID returns 409.

### 5.4 Observation and binary formats

**G** PUT observation: `Content-Type: multipart/form-data; boundary=...`.

| Part name | filename | Content-Type | Required |
|---|---|---|---|
| `metadata` | `frame.json` | `application/json` | Yes |
| `rgb` | `rgb.jpg` | `image/jpeg` | Yes |
| `depth` | `depth.f32` | `application/octet-stream` | Yes |
| `confidence` | `confidence.u8` | `application/octet-stream` | Only when metadata.confidence != null |

Each set comes from **one ARFrame**. An independently captured photograph must not be paired with an arbitrary depth frame.

- RGB: JPEG from the original `capturedImage`, without rotation, crop, resizing or EXIF rotation. Rotating the screen preview does not change files or intrinsics.
- Depth: unsmoothed `sceneDepth`, float32 little-endian, metres, row-major, no row padding; exactly `width × height × 4` bytes. NaN/Infinity/≤0 values are preserved but excluded from geometry.
- Confidence: uint8 row-major, same resolution, exactly `width × height` bytes; 0/1/2 = low/medium/high. If no map is available, use `confidence: null` and omit the multipart part.
- Without depth or normal tracking, the client does not create a real geometry observation. A synthetic frame uses `tracking_state: synthetic`, `depth_source: synthetic_plane`.
- SHA-256 is lowercase hex over the exact file bytes. `byte_length` excludes multipart headers. Filenames are fixed, with no arbitrary paths.

Example metadata using the current Swift field names:

```json
{
  "schema_version": "0.1",
  "observation_id": "OBSERVATION_UUID",
  "frame_id": "OBSERVATION_UUID",
  "session_id": "SESSION_UUID",
  "segment_id": "SEGMENT_UUID",
  "instruction_id": null,
  "capture_origin": "device",
  "timestamp_s": 15423.234,
  "saved_at": "2026-10-27T10:00:05Z",
  "rgb_size": [1920,1440],
  "depth_size": [256,192],
  "K_rgb": [[1400,0,960],[0,1400,720],[0,0,1]],
  "T_world_camera": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
  "tracking_state": "normal",
  "depth_source": "sceneDepth",
  "depth_unit": "m",
  "image_orientation": "native_buffer_unrotated_no_exif_rotation",
  "source_to_stored_image": [[1,0,0],[0,1,0],[0,0,1]],
  "depth_to_rgb_mapping": "Unrotated aligned buffers; pixel origin top-left, u right/v down. Scale K_rgb rows 0/1 by depthWidth/rgbWidth and depthHeight/rgbHeight. Device reprojection validation pending.",
  "rgb": {"name":"rgb.jpg","byte_length":123456,"sha256":"0000000000000000000000000000000000000000000000000000000000000000"},
  "depth": {"name":"depth.f32","byte_length":196608,"sha256":"0000000000000000000000000000000000000000000000000000000000000000"},
  "confidence": {"name":"confidence.u8","byte_length":49152,"sha256":"0000000000000000000000000000000000000000000000000000000000000000"}
}
```

Dimensions, intrinsics, and pose in the example are illustrative, not measurement results. Geometry Service validates JPEG dimensions, binary sizes, hashes, matrix shape/finiteness, positive focal lengths, rigid transform validity, and segment/session membership. In v0.1, `frame_id == observation_id`. `instruction_id` provides capture context, not proof that pixels belong to a specific BIM object. G stores instruction_id as a reference without making uploads depend on I's availability or determining whether the instruction is current. A late frame with an old instruction remains evidence; I checks instruction/session membership and state transitions when processing the response.

**G** returns a receipt **after durably storing and validating the entire set**:

```json
{
  "session_id": "SESSION_UUID",
  "observation_id": "OBSERVATION_UUID",
  "status": "accepted",
  "metadata_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "accepted_at": "2026-10-27T10:00:06Z"
}
```

`metadata_sha256` is computed over the exact bytes of the `metadata` multipart part. The client resends the stored frame.json without generating new dates or formatting. The multipart boundary may change. Matching IDs and metadata/file hashes → 200; a changed set with the same ID → 409. A partially received set is not visible in uploads and does not emit an interservice observation.accepted event. G accepting a frame does not mean geometry processing or inspection is complete.

### 5.5 Upload reconciliation

**G** GET uploads: `{"items":[{"observation_id":"OBSERVATION_UUID","metadata_sha256":"HASH"}],"next_cursor":null}`.

Pages are sorted by server acceptance order. `next_cursor: null` means the end of the current list. The client compares IDs **and** metadata hashes; a matching ID alone is insufficient. A network timeout after PUT does not prove the record was lost: repeat the same PUT or reconcile against uploads. Do not change the ID for a retry.

### 5.6 Events and instructions

**I** serves events and instructions. For the MVP, use foreground polling; we propose a 2-second interval while the screen is active. Back off on errors, without parallel polling requests. Background push/WebSocket is not required for the first integration.

The first GET events without `after` returns events from the start of available history. Response:

```json
{
  "events": [{
    "event_id": "evt-17",
    "type": "instruction.created",
    "session_id": "SESSION_UUID",
    "created_at": "2026-10-27T10:01:00Z",
    "instruction": {
      "instruction_id": "ins-3",
      "revision": 1,
      "session_id": "SESSION_UUID",
      "model_version": "demo-room-v1",
      "target_element_ids": ["door-01"],
      "text": "Capture the right edge of the opening from another angle.",
      "language": "ru",
      "state": "pending",
      "target_region": null,
      "audio": null
    }
  }],
  "next_cursor": "cursor-17",
  "has_more": false
}
```

- Delivery is at-least-once; the client deduplicates by event_id and saves the cursor **after** applying events. If has_more is true, continue immediately; otherwise wait until the next poll.
- MVP types: `instruction.created`, `instruction.updated`, `instruction.cancelled` — each contains the full instruction object; `session.updated` contains the state object from **I** GET session. Unknown types may be logged and skipped without breaking polling.
- Instruction `revision` increases monotonically. The client does not apply an older or equal revision over a newer one. States: `pending`, `collecting`, `satisfied`, `cannot_complete`, `cancelled`.
- An expired cursor returns 410 `cursor_expired`. GET instructions returns `{"instructions":[...],"resume_cursor":"..."}`: a complete current instruction snapshot, including closed instructions, and a cursor from the same atomic server point. The client replaces its local list, requests **I** GET session, and resumes events from resume_cursor.
- In v0.1, `target_region` and `audio` are always null. Agree their non-null schemas separately; text remains the primary representation. Do not derive spatial coordinates by parsing text.

**I** POST response, JSON:

```json
{
  "schema_version": "0.1",
  "response_id": "RESPONSE_UUID",
  "instruction_revision": 1,
  "action": "observations_submitted",
  "text": null,
  "observation_ids": ["OBSERVATION_UUID"],
  "input_mode": "button",
  "audio": null
}
```

I checks accepted observations directly with G if they are not yet present in its local event projection. It does not rely solely on the phone's claim. Unknown ID → 409 `evidence_not_accepted`; G unavailable → 503. The client retains the response in a separate queue and retries without uploading files again. If geometry is still processing, the response may be accepted while the instruction remains collecting.

Actions: `acknowledged`, `observations_submitted`, `cannot_complete`, `message`. observations_submitted requires a nonempty list of **accepted** observations from this session linked to instruction_id; the other actions require an empty list. message requires nonempty text; for cannot_complete, text may contain a reason. MVP input_mode: `button | typed`; `voice` is a future extension.

Response: `{"response_id":"RESPONSE_UUID","status":"accepted"}`. This acknowledges the response, not the sufficiency of evidence. If the instruction revision has changed, return 409 `stale_instruction` with the current instruction in details. A new intentional submission after a conflict uses a new response_id. A successful retry with the same response_id/body returns the original receipt **before** checking the current revision; a different body for the same ID returns 409.

### 5.7 Completion and two independent states

**I** POST complete, JSON:

```json
{
  "schema_version": "0.1",
  "completion_id": "COMPLETION_UUID",
  "observations": [
    {"observation_id":"OBSERVATION_UUID","metadata_sha256":"0000000000000000000000000000000000000000000000000000000000000000"}
  ]
}
```

Before requesting completion, the client stops capture, freezes a nonempty inventory without duplicates, and waits for all G receipts. Active pending/collecting instructions in I → 409 `instructions_pending`. Repeating an accepted completion_id/body returns the receipt for the same operation; a changed body for the same ID → 409. A different completion_id while a closing operation is unfinished → 409 `completion_in_progress`.

**Proposed interservice closing protocol:**

1. I atomically stores the completion request, sets state=closing and completion.status=pending, and stops creating or changing capture instructions. It returns 202 after persisting the operation: `{"session_id":"SESSION_UUID","completion":{"completion_id":"COMPLETION_UUID","status":"pending","error":null}}`. Retries return 200 with the current state of the same operation, not new processing jobs.
2. I calls G's internal seal endpoint (§5.8) with the same completion_id and inventory. G atomically checks the exact set of IDs/hashes and stops accepting new IDs. This prevents a race between reconciliation and upload.
3. On an inventory mismatch, G returns 409 and leaves the set open. I sets completion.status=rejected with details, returns to capturing, and publishes session.updated. The client corrects the inventory/uploads missing data and creates a **new** completion_id. Repeating the old request returns the same rejected operation without restarting it.
4. If G times out or is unavailable, I remains closing and retries seal with the same ID. It must not assume the set is open or return to capturing while the outcome is unknown: G may already have sealed it successfully. The operation state is durable and survives an I restart.
5. Once seal is confirmed, I sets completion.status=sealed and state=waiting_geometry. G builds final geometry from the frozen set. I then transitions to analyzing and completed (or failed), binding the analysis to a specific geometry_revision.

After seal, new observations/segments in G → 409 `session_closed`; an exact retry of an already accepted observation is allowed. A geometry error does not automatically reopen the sealed set. Agree backend processing retry details separately; new data after closure is submitted in a follow-up session.

**G GET session** — geometry:

```json
{
  "session_id": "SESSION_UUID",
  "revision": 8,
  "capture_state": "open",
  "processing_state": "processing",
  "accepted_observation_count": 12,
  "completion_id": null,
  "geometry_revision": 3,
  "evidence_observation_ids": ["OBSERVATION_UUID"],
  "artifacts": [],
  "alignment": null,
  "coverage": null,
  "error": null
}
```

`capture_state`: open | sealed; `processing_state`: idle | processing | ready | failed. `revision` is G's state version; `geometry_revision` is the nullable number of the latest **ready**, immutable result. While the next result is being built, the previous one may remain available; its evidence_observation_ids explicitly identify the frames used. When geometry_revision=null, evidence/artifacts lists are empty. Geometry ready for a subset does not mean all 12 accepted frames are ready. Final geometry after seal must explicitly cover the entire inventory or contain an error; do not hide excluded/unprocessed frames.

`artifacts`: a list of `{artifact_id, kind, url, media_type, byte_length, sha256}`, where kind = point_cloud | reconstructed_model; media_type specifies the file type (agree a format, such as PLY for point_cloud). All entries belong to the specified geometry_revision. A URL is a G path or a signed HTTPS URL. No artifacts is explicitly represented by an empty list. Alignment and coverage are null for now (§7).

**I GET session / session.updated payload** — inspection:

```json
{
  "session_id": "SESSION_UUID",
  "revision": 5,
  "state": "closing",
  "completion": {"completion_id":"COMPLETION_UUID","status":"pending","error":null},
  "geometry_revision": null,
  "result": null,
  "error": null
}
```

`state`: capturing | closing | waiting_geometry | analyzing | completed | failed. `completion` is null before the first operation; afterward it represents the latest operation with status pending | rejected | sealed. Closing rejection belongs in completion.error; analysis failure belongs in the top-level error. Operations are deduplicated and retain their own state; retrying an old completion_id returns that specific operation's state: `{"session_id":"...","completion":{...}}`. Always retrieve the current session state through GET session. I's `geometry_revision` is the geometry version used by the current/completed analysis; it need not equal G's newest version. The client stores each service's revisions separately.

Before closing, I may analyze evidence and issue instructions while remaining in capturing. After closure, no new capture instructions are created in this session. If final analysis requires more capture, result.outcome=needs_more_data; the user creates a new session in G and I with parent_session_id, and I creates instructions for the new session.

For I completed, the result field is:

```json
{
  "outcome": "needs_more_data",
  "analysis_origin": "mock",
  "summary": "Insufficient evidence for the right side of the opening.",
  "evidence_observation_ids": ["OBSERVATION_UUID"],
  "report_url": null
}
```

`outcome`: issues_found | no_issues_detected | needs_more_data; `analysis_origin`: live_model | recorded_model | mock. Evidence IDs belong to this session. `no_issues_detected` is the result of the current analysis, not certified acceptance. report_url is a nullable HTTPS URL for an existing report. Errors use the format in §8. Upload, geometry readiness, and inspection outcome are three separate UI states.

### 5.8 Direct microservice communication (MVP proposal)

These routes are called by **services**, not the phone. For the MVP, we propose HTTP plus a durable notification queue; an event broker is optional. Backend owners agree the specific transport. Interservice authorization checks sender identity and project access.

| Receiving service | Internal route | Caller / purpose |
|---|---|---|
| G | `GET /internal/v1/sessions/{s}/registration` | I: original PUT session configuration, unchanged |
| G | `GET /internal/v1/sessions/{s}/observations/{o}` | I: exact frame.json and access to source files |
| G | `GET /internal/v1/sessions/{s}/observations/{o}/files/{name}` | I: rgb.jpg, depth.f32, confidence.u8, or frame.json; other names are forbidden |
| G | `POST /internal/v1/sessions/{s}/seal` | I: completion_id + inventory from POST complete; atomically seal the set |
| I | `POST /internal/v1/geometry-events` | G: notify about acceptance/geometry/errors |

I also calls G GET uploads and GET session from §5 with service authorization to reconcile data and obtain results. Registration data is returned in exactly the §5.2 format. GET observation returns `{"metadata":{...frame.json...},"metadata_sha256":"...","files":[{"name":"rgb.jpg","url":"/internal/v1/.../files/rgb.jpg"}]}`; files includes all stored files, including frame.json. To verify metadata_sha256, use the **original frame.json bytes** from the file endpoint, not reserialized metadata. G checks scope on every request; knowing a UUID does not make data public. I obtains geometry files through artifact URLs from G GET session.

Seal returns 200 `{"session_id":"SESSION_UUID","completion_id":"COMPLETION_UUID","capture_state":"sealed"}` only after a durable commit. An exact retry returns the same result even after subsequent processing failures; an inventory mismatch → 409 `inventory_mismatch`, and the set remains open. If already sealed by another completion_id → 409 `session_closed`; I must reconcile the state and show a conflict instead of retrying indefinitely. A successful seal guarantees that G queues final processing durably so it can be recovered.

G → I example:

```json
{
  "schema_version": "0.1",
  "event_id": "geometry-evt-17",
  "type": "observation.accepted",
  "project_id": "demo-building",
  "session_id": "SESSION_UUID",
  "model_version": "demo-room-v1",
  "source_revision": 8,
  "created_at": "2026-10-27T10:00:06Z",
  "observation_id": "OBSERVATION_UUID",
  "metadata_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "geometry_revision": null,
  "completion_id": null,
  "error": null
}
```

Types: observation.accepted (observation_id/hash required), geometry.ready (geometry_revision required; observation_id/hash=null), geometry.failed (error required; observation_id/hash=null). On failure, geometry_revision = null; the event does not denote a ready result. For final processing after seal, completion_id is required; for intermediate processing, it is null. source_revision is G's state version at event time, **not** I's revision. I reads evidence lists and artifact links from G; the historical geometry_revision in an event may already have been superseded in current state — I uses an explicitly selected current version without presenting it as the old one.

I returns 202 `{"event_id":"geometry-evt-17","status":"queued"}` after a durable inbox write; an exact retry returns 200, and different content for the same event_id returns 409. G records the obligation to send the event together with acceptance of the frame/result (outbox or equivalent), and retries until acknowledged. If the session is not yet registered with I, I retains the notification until registration and does not start analysis before verifying configuration and permissions.

Events may be duplicated and arrive out of order. Do not discard observation.accepted merely because a higher source_revision has already been seen: it represents another frame. I deduplicates event_id and observation_id/hash, and reconciles geometry state with G. After registration/restart, I reconciles GET uploads and GET session to recover missed notifications. The phone does not participate in recovery or transfer files between services.

## 6. Coordinates — mandatory agreement point

- All JSON matrices are **arrays of rows**, multiplied by column vectors. Swift SIMD stores columns: app serialization explicitly reorders indices.
- `T_world_camera` is 4×4, **camera → current segment world**, not world → camera. Metres, right-handed; world Y is up, the camera looks along −Z, with X right and Y up.
- `K_rgb` is the 3×3 intrinsics matrix for the saved original RGB, not the screen preview.
- Proposed pixel convention: origin at top left, u right, v down. `K_depth = diag(depthWidth/rgbWidth, depthHeight/rgbHeight, 1) × K_rgb`.
- For pixel (u,v) at depth d: `p_optical = d × inverse(K_depth) × [u,v,1]`; then `p_camera = diag(1,-1,-1) × p_optical`; then `p_world = T_world_camera × [p_camera,1]`.
- This is a proposed convention. Pixel-center convention, RGB/depth correspondence and axis signs **must be checked on real capture data** before freezing v1. Synthetic tests do not establish LiDAR quality.
- The backend transforms a point into BIM through a separately computed `T_bim_world`. Do not rewrite source poses/measurements into BIM or force measured defects onto designed surfaces.
- Current local-file `depth_to_rgb_mapping`/`coordinates` fields are explanatory; the server uses the agreed v0.1 schema rather than interpreting arbitrary prose as an algorithm.

## 7. Alignment, coverage, and voice: next stage

These do not block the first RGB-D transfer and text interaction loop. Non-null payloads for these capabilities **have not yet been agreed**.

Future alignment requires at least model_version, segment_id, alignment_id, revision, status, and T_bim_world. Coverage requires model_version, revision, evidence references, and an explicit distinction between unknown and measured. Until valid data is agreed and received, the phone displays the model separately, without camera position, coverage percentage, or spatial guidance. Geometry Service owns the calculations; the phone retrieves them directly from G. AI Inspector retrieves the same results through interservice requests.

Voice can initially use on-device speech synthesis of existing text without API changes. Audio transfer/recognition and input_mode=voice are a separate extension. Voice failure must not block text.

## 8. Errors, retries and preservation

```json
{
  "code": "inventory_mismatch",
  "message": "Not all observations have been accepted",
  "retryable": false,
  "details": {"missing_observation_ids":["OBSERVATION_UUID"],"extra_observation_ids":[],"hash_mismatches":[]}
}
```

| HTTP | Client behavior |
|---|---|
| 400 / 422 | Correct the request/data; do not automatically retry unchanged |
| 401 / 403 | Stop sending, retain the queue; restore access |
| 404 | Check project/session/model/instruction; display the error |
| 409 | Inspect code/details; reconcile state, do not blindly change IDs |
| 410 | Restore the instruction snapshot and cursor |
| 413 | Set too large; do not change RGB/depth without updating metadata and an explicit decision |
| 429 | Retry after Retry-After |
| 5xx / network / timeout | Retry the same operation with the same IDs and bytes, using backoff with jitter |

Proposed backoff: 1, 2, 4, 8… up to 30 seconds; honor Retry-After if longer. Agree specific timeouts/limits with the backend. Persist the queue before network operations, restore it after restart, and distinguish saved/accepted/processed. Do not delete local files after a single network request; agree retention separately. Do not write frame payloads to server/client debug logs.

## 9. Items for teammate confirmation

- [ ] Direct phone APIs to G and I; G ↔ I without the phone. Route owners and interservice authorization are agreed.
- [ ] One observation = one RGB-D frame; the backend generates PLY, or a simple export requirement is agreed separately.
- [ ] Accept JPEG + float32 LE depth + uint8 confidence + JSON; nullable confidence.
- [ ] Accept matrix directions, units, and segments; jointly validate a real example.
- [ ] Accept multipart, idempotency, and G durable acknowledgements; shared session registration and coordinated closure through I → G seal.
- [ ] Accept polling, revisions, and the text instruction/response format.
- [ ] Assign an owner for USDZ preparation and the element ID table.
- [ ] Provide two base URLs, client token issuance methods, limits, and test servers.
- [ ] Agree G → I notifications, inbox/outbox, and recovery without the phone.
- [ ] Agree whether alignment and coverage are needed and their formats for the next stage.

## 10. Acceptance checks for the first integration

1. The backend reads real JPEG/depth/confidence, validates hashes, sizes, and intrinsics, and reconstructs points without implicit axis changes.
2. Overlapping real frames are checked geometrically; measured error is recorded, not a promise of accuracy.
3. Interrupt the connection during upload and after server acceptance: retries do not duplicate observations; an incomplete set is not accepted.
4. Application restart preserves IDs/the queue; a new ARKit segment is not mixed with the old one.
5. A text instruction leads to an additional observation_id and response; duplicates/old revisions do not corrupt the UI.
6. An incorrect inventory produces a rejected completion without sealing G; a correct seal closes the set once. A timeout after seal and an I restart do not reopen it.
7. When I is unavailable, the phone still uploads to G; after recovery, I obtains data directly and issues instructions. Events before I registration, duplicates, and reverse delivery order do not lose evidence.
8. Mock/synthetic/recorded modes are clearly visible; successful upload is not displayed as a successful inspection.

## 11. Schema change, 2026-10-27

This revision replaces the original single-entry-point design through the Orchestrator API. The filename is retained so previously shared links continue to work. The frame.json format and schema_version 0.1 are unchanged; transport had not yet been implemented/agreed. The topology with two direct phone connections is confirmed; registration details, internal notifications, and seal are proposals for teammates, not an approved implementation.
