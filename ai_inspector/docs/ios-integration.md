# iPhone integration

Use the configured HTTPS origin, device ID and scoped **device** bearer token. Only the operator uses the separate operator identity. No Supabase administrator key, backend database credential or model API key belongs on the phone. Private connection files exported by a test host are optional conveniences, not checked-in configuration or proof that the host is running.

HTTP schemas/limits come from `<BASE_URL>/openapi.json`, `/docs` and `/v1/capabilities`. [schemas.py](../src/ai_inspector/schemas.py) defines the implemented wire models. This guide records lifecycle, retry and source rules; the broader [team API proposal](../../docs/iphone-geometry-bim-inspector-api-v0.1.md) remains a draft.

## Connect and restore assignment

The phone initiates `wss://<HOST>/v1/devices/{device_id}/events/ws` with `Authorization: Bearer <DEVICE_TOKEN>`. [InspectorConnection.swift](../examples/InspectorConnection.swift) is a URLSession reference, not a complete capture app or proof of device integration.

Clients unable to set WebSocket headers may send `{"type":"authenticate","token":"<DEVICE_TOKEN>"}` as the first message within the server authentication deadline. Never put the token in the URL. The server sends device.connected and then a snapshot/replay. Follow the advertised heartbeat interval, answer server ping with `{"type":"pong"}`, and send control messages within the inactivity deadline. Business comments/instructions are not arbitrary messages on this control channel.

Persist assigned session ID, model/room context and revision locally before acknowledging. Inspector generates capture session IDs. Only an assigned session is a new capture task; restore capturing work rather than create a replacement. Sealed/completed sessions in snapshots are history. Optional capture_task.text with source=operator_request contains the original operator request; it is not permission to start the camera remotely. Older sessions may omit it. Display in the actual phone app requires that client's implementation.

Apply a whole event packet before saving its next_cursor. Reconnect using that event cursor, deduplicate event IDs and reject older session revisions. Invalid cursors require a fresh snapshot. For paginated assignments follow returned cursors; those differ from event-resume cursors. Sessions created while offline appear after reconnect. Offline/background/restart is not cancellation and does not require a new session.

## Acknowledge and start

After durable local receipt, call `POST /v1/sessions/{session_id}/acknowledgements` with a retained operation_id. This confirms only receipt: it does not start capture or change capture/processing state. The first acknowledgement records device_acknowledged_at and advances revision once. A later acknowledgement ID returns the same first timestamp without another session revision; exact successful replay returns the original receipt. Backend receipt cannot independently prove phone disk state.

Call `POST .../capture-start` only when recording actually starts. Its operation-ID namespace is separate from acknowledgement; acknowledgement is not a prerequisite, but capture-start is required before upload plans. A start on an already capturing session is a no-op success without another revision.

New operation success is 201, exact replay 200, changed content under the same ID 409. Accepted retries replay before terminal-state checks; new start/ack operations after submitted/sealed/cancelled are rejected. Keep the same ID and exact body after uncertain network outcomes.

## Capture and source formats

Read supported formats and limits from capabilities. Normal assigned-session formats include:

| Format | Input |
|---|---|
| supabase_capture_v1 | Individual files plus final capture_manifest_supabase_v1 |
| supabase_capture_zip_v1 | One complete ZIP; [exact layout/key rules](zip-capture.md) |
| supabase_geometry_parts_v1 | Numbered independent geometry_archive ZIP parts plus final index manifest |
| versioned_s3_v1 | Versioned S3 manifest/files; [Geometry/source contract](capture-to-geometry.md) |

Each ARKit reset starts a new local segment_id inside the same server session. Preserve frame IDs, original poses/RGB-depth alignment, exact file bytes and SHA-256; segments are not automatically aligned. New frame.json uses 0.2, with legacy RGB-D 0.1 supported. For RGB-only, depth_source=none and depth-related fields/confidence are explicit null, not empty files. Real captures retain device provenance; synthetic fixtures must not masquerade as real measurements. Until business instruction history exists, instruction_id is null.

For individual files, request `POST .../upload-plans` with retained request_id and kind=observation_files. Allowed frame/RGB/depth/confidence names and media types are validated by the backend; use returned keys, method, headers and size limits. PUT raw bytes to upload_url without the Inspector Authorization header. Never derive an object key from a sample or silently overwrite changed bytes. Renew expired permissions using the same file identity; already_uploaded is valid only after length/hash match.

For a final capture manifest use kind=capture_manifest and the completion_id it belongs to. Upload it last, after all referenced files are present. Copy the assignment context and exact returned logical object keys; Supabase has no fictitious S3 version IDs. Do not substitute a relative ZIP member path for a bucket-root object_key, or standalone export keys for assigned-session keys.

For geometry parts use kind=geometry_archive, the same completion_id and validated part identity. Each part stays within the instance's archive limit. Upload all independent ZIPs, then the exact index manifest, and complete with supabase_geometry_parts_v1. The request/index/part shapes are in [schemas.py](../src/ai_inspector/schemas.py), [upload planning](../src/ai_inspector/capture_uploads.py) and [parts validation](../src/ai_inspector/geometry_parts.py); do not concatenate parts into the legacy single-ZIP format. Backend intake support does not establish that the external Geometry consumer supports the format.

## Complete and observe

Seal the local capture set, finish uploads and POST `.../complete` with a stable completion_id and exact manifest/archive reference. HTTP 202 means durable receipt, not validated source. Exact replay returns the current status of that operation; changed content conflicts and another completion is blocked while one is pending.

Source validation verifies assigned scope, inventory, hashes, frame/segment metadata and safe archive structure before sealing. Missing/corrupt input is rejected without dispatch, returning capture to capturing; a corrected set needs a new manifest/completion ID. Transient failures preserve the operation and retry. Keep local files until the accepted source and required result have been observed.

A sealed session does not reopen for new photos; an explicitly requested rescan gets a new linked server session. Observe session events/GET for acknowledgement, capture state, source and processing state. Accepted source, Geometry admission and verified result are separate facts. completed with geometry_only_v1 means the saved verified Geometry response, not construction approval. Mock origin must remain visible; configuration alone is not reconstruction.

## Cancellation and failures

The assigned device or matching operator may POST `.../abort`. Ownership is checked; cancellation is idempotent, invalidates Inspector work leases, rejects pending completion and retains files/history. Completed/failed sessions reject a new abort. Issued upload URLs and dispatched external jobs cannot be recalled. Clear the phone's active assignment only after a matching cancelled response; failure leaves a visible retryable assignment.

401/403 requires identity/scope correction; inspect the code for 409/422 rather than blindly retrying. Transient errors use backoff with the same IDs/body. Preserve cursor, local pending operations, files, upload receipts and manifest bytes across app restart. Actual iOS background/ARKit behavior needs device verification; the backend does not promise continuous background capture.

## Independent test captures

`POST /v1/standalone-captures/{session_id}/upload-plans` is a distinct device-scoped export path that accepts a client-generated UUID, observation_files and capture_manifest only. It creates no Inspector session, assignment, accepted normal completion, Localization operation or Geometry job.

Objects are scoped to standalone/{project}/{device}/{session_id}/, using content-addressed observation/manifests keys and immutable upload plans. Upload standalone_capture_v1 manifest last, after verifying all referenced uploads. Its standalone inspection/project markers are not Inspector entities; ownership comes from the authenticated prefix. Discover a completed manifest instead of assuming a partial folder is complete. Incorporating this data into an inspection requires a separately implemented validated import.

Native/cloud setup and optional test tools are in [backend README](../README.md). Operator intake and reference-BIM semantics are in [web integration](web-integration.md).
