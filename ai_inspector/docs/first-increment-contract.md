# First increment: intake and session-ID confirmation

Date: 2026-10-27. Branch: `feat/ai-inspector-development`.

This document describes the intake/session foundation. The current backend also
implements [capture completion and generator dispatch](capture-to-geometry.md).

This implementation follows sections 5.2 and 5.7 of the parent repository's
`docs/iphone-geometry-bim-inspector-api-v0.1.md` (revision 5). That document remains
a proposed integration contract. These additions describe this backend's local
first increment; they do not imply agreement or implementation by iOS, Geometry,
or a remote BIM provider.

## New intake endpoints

`POST /v1/inspections` is an operator-only multipart endpoint. Required fields:

| Field | Meaning |
|---|---|
| `schema_version` | `0.1` |
| `request_id` | Client-generated UUID retained across retries |
| `project_id` | Must match the authenticated operator's configured project |
| `room_id` | Caller-provided inspection context, 1–128 safe identifier characters |
| `prompt` | Original user request, nonblank, at most 8,000 characters |
| `bim_model` | Nonempty original file, at most the configured byte limit |
| `model_is_synthetic` | Optional boolean, false by default |

The inspection response includes `inspection_id`, `project_id`, `room_id`,
`model_version`, `model_is_synthetic`, configured `policy_version`, the original
`prompt`, UTC `created_at`, and `bim_model` metadata. The metadata contains original
filename, declared media type, exact byte length/SHA-256, `status=stored_unvalidated`,
and a protected relative download URL. Inspection IDs are server-generated UUIDs.

The local BIM intake adapter stores opaque bytes by hash and owns the immutable
`upload-sha256:{sha256}` intake identifier. This is not proof of a valid IFC/RVT,
normalized model geometry, axes, units, elements, or room membership. A future BIM
adapter must import/register the model before geometry-dependent work. It must
not silently change a model version already pinned to an existing session.

Intake returns 201 on creation and 200 for a successful replay. The idempotency
scope is project + request ID. Validated form metadata, sanitized original filename,
media type, size, and exact file hash define logical request identity; multipart
boundaries do not. Changed content under the same ID returns 409. Existing
inspection data and policy remain unchanged on retry, even after configuration
changes. Failed validation consumes no idempotency key.

`GET /v1/inspections/{inspection_id}` returns persisted metadata.
`GET /v1/inspections/{inspection_id}/bim-model` returns original bytes as an
attachment. Both require the matching project's operator identity. The intake
download endpoint is not the proposed phone-facing BIM display API.

## Receipt-only acknowledgement extension

`POST /v1/sessions/{session_id}/acknowledgements` is device-only:

```json
{"schema_version":"0.1","operation_id":"d7ea3c44-0501-4ae5-ae54-03f36f7903bd"}
```

The app should first persist the assigned session ID locally. The backend receipt:

```json
{
  "session_id":"9c7a7780-7fd0-47a3-acd2-3f6dc35abe6c",
  "operation_id":"d7ea3c44-0501-4ae5-ae54-03f36f7903bd",
  "acknowledged_at":"2026-10-27T14:00:00.000000Z"
}
```

The first acknowledgement saves `device_acknowledged_at` and advances session
revision once, without changing capture or processing state. A new acknowledgement
ID records another receipt with the same first acknowledgement timestamp and no
additional state revision. Replaying a successful operation returns the original
receipt with status 200. Acknowledgement and capture-start have separate operation
ID namespaces. This extension represents the phone's claim that it saved the ID;
the backend cannot independently prove the phone's local disk state.

`capture-start` retains its contract meaning: actual capture has begun. A new
start operation on an already capturing session succeeds with a no-op receipt,
without advancing revision again. Receipt acknowledgement is not a prerequisite.

## Preserved session rules

- Creation uses the original contract body and returns a full session. Project,
  model, synthetic-model flag, and policy are copied from inspection. Only the
  configured device and inspection room are accepted. Synthetic capture requires
  `synthetic_fixture`; recorded real capture retains `capture_origin=device`.
- A session row is its authoritative assignment. Session, assignment, and retry
  receipt commit in one SQLite transaction. Creation receipts replay their
  original response; `GET session` returns current state.
- Optional parent sessions must exist, be accessible in the same project, and
  reference the same model version. This increment does not impose a new parent
  lifecycle rule beyond the current contract's project/model checks.
- Assignment listing uses ordered keyset pagination, maximum 100 items. Cursors
  are opaque to clients and scoped to project/device. Access is always checked
  independently of the cursor. A fresh traversal finds later assignments.
- Start and acknowledgement require the assigned device. New operations on
  submitted/sealed/cancelled sessions are rejected. Previously accepted retries
  return their original receipt before current-state checks.
- Capture and processing states remain separate. The foundation advances
  `assigned` to `capturing`, with processing idle. The capture-completion extension
  adds submitted/validating and sealed/queued_geometry after source verification.
  No endpoint claims measured coverage or a completed analysis.
- Errors use `code`, `message`, `retryable`, `details`. Unsupported schemas and
  malformed fields return 422, missing resources 404, scope failures 403, missing
  authentication 401, request/file limit violations 413, conflicts 409, and
  temporary storage errors 503. Validation details omit submitted field values.

## Local durability and configuration

SQLite schema version 1 introduced inspections, sessions/assignment order, and
operation receipts. Version 2 adds capture completions, geometry jobs and the
durable queue. Version 3 adds the assignment's `storage_ready` flag so incomplete
Supabase folder provisioning is not advertised to the device. Existing sessions
retain their readiness during migration. Version 4 adds device events/connections,
upload declarations and the Geometry notification inbox. Write transactions use `BEGIN IMMEDIATE`; database uniqueness
serializes concurrent duplicate operations. Original BIM bytes are flushed and
published atomically before the inspection transaction commits. A crash between
file publication and DB commit can leave an unreferenced hash blob; it cannot
commit an inspection pointing to a partially published file. Automated orphan
collection and retention are future work.

Local identity configuration provides one project, one phone, and separate
operator/device bearer tokens, plus optional separate generator service tokens.
API handlers enforce role, project, and device
scope; replacing this adapter with an identity provider is a later increment.
Original files, database, and receipts must be retained together across restarts.
`INSPECTOR_DATA_DIR` defaults to `./data`, relative to the server working directory.
