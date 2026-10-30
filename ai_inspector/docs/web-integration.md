# Operator and web integration

Use the configured backend base URL and the **operator** bearer identity. Keep credentials out of public bundles, URLs and source control. The device token has a different scope and cannot access operator chat/workspace/backlog APIs. Full request/response schemas are at `<BASE_URL>/openapi.json` and `/docs`; this guide records behavior that clients must preserve.

## Console and login

`/` is the operational Control room; `/ui/demo.html` is the document/chat workspace. Both use the same backend API. The operational UI supports capture creation, session/status browsing, source/result inspection, export, reference BIM upload and abort. The demo supports files, IFC/PDF/image viewing, document selections, agent conversation and activity. See [viewer build](../viewer/README.md) and [agent behavior](agent-chat.md).

Operator login is stored in `sessionStorage` for the site/tab. Reload retains it; Lock workspace/logout and HTTP 401 clear it. Storage-blocking browsers require manual login. Closing the browser is not a promised persistence boundary. Shared operator credentials do not authenticate individual observation authors.

Native FastAPI serves the UI from the Python package; Cloudflare uses the JavaScript assets gateway. A separate-origin browser client needs exact `INSPECTOR_CORS_ORIGINS`. Native phone requests are not governed by browser CORS.

## Intake and capture creation

Use `POST /v1/capture-sessions` for capture without reference BIM. Inspector creates the inspection/session IDs, assigns the configured device and pins provenance. For a reference workflow, upload through `POST /v1/inspections` and create its capture session. These multipart/JSON schemas are in OpenAPI, not duplicated here.

BIM intake stores opaque bytes by hash with `status=stored_unvalidated` and immutable `upload-sha256:...` identity. It does not validate IFC/RVT, choose a real room, normalize geometry or prove dimensions. A future parser must not silently replace a model already pinned to sessions. Uploading/selecting an IFC in chat is a different action from attaching reference BIM to an inspection.

For intake idempotency, validated metadata, sanitized filename, media type, byte length and exact hash define identity; multipart boundaries do not. Failed validation does not consume the key. Successful replay retains the original inspection/model/policy even if server configuration changes.

Persist each request ID and its exact contents until confirmed. Creation returns 201, exact successful replay 200, and changed content under the same key 409. An uncertain timeout requires the same operation, not a new UUID. Console mutation forms retain frozen pending requests in memory, lock their inputs and allow retry/discard; reconcile the server list before closing a pending tab. Chat has its own durable retry behavior described in [agent-chat](agent-chat.md#execution-retry-and-recovery).

A session is advertised to the phone only after its storage folder is ready. Creation may retain the session/operation ID while storage is temporarily unavailable; retry completes the same assignment. Online, assignment, acknowledgement, camera start, accepted source and Geometry completion are distinct facts. Offline does not cancel a session. Phone behavior is in [iOS integration](ios-integration.md).

## Status, cancellation and interpretation

Read device channel presence, session state and capabilities from the running API. Operator overview is project-scoped and paginated; follow returned cursors instead of deriving them. Device channel event cursors and overview/assignment pagination are different namespaces.

`POST /v1/sessions/{session_id}/abort` accepts either the matching operator or assigned device identity. It is idempotent after cancellation, invalidates Inspector work leases, rejects pending completion and preserves uploaded files/history. Completed/failed sessions reject a new abort. Issued upload URLs and already dispatched external Geometry work are not recalled. The phone should act on a confirmed matching cancelled response. This endpoint does not restart the server or cancel an arbitrary agent task.

`processing_state=completed` with `processing_scope=geometry_only_v1` means a verified Geometry result, not BIM compliance. Preserve `analysis_origin` and synthetic/mock provenance. Configuration flags, rendered IFC, AI summaries and capture counters do not establish measurements, coverage or construction completion.

## Files, observations and backlog

Chat files/selections and progress semantics are in [agent-chat](agent-chat.md#files-work-plans-and-observations). API schemas define upload, import and observation fields; source code enforces concurrent revision checks and immutable retry identity.

Backlog is a separate project-scoped manual handoff board with independent Web/Backend status, contract notes and a self-reported editor. It is not automatic Git/CI tracking. Saves are full-record updates using the last read revision; stale writes return 409. Device credentials cannot access it. Apply `002_backlog.sql` separately for PostgreSQL; workspace features require `003_workspace.sql`. See [deployment database setup](cloudflare-deployment.md#database-and-secrets).

For native/container startup use [backend README](../README.md). Cloud publication and verification use [the deployment guide](cloudflare-deployment.md). No workstation configuration or historical test run establishes the current deployment's state.
