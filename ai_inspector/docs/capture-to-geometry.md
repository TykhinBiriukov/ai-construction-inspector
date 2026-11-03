# Accepted capture → Geometry handoff

Inspector owns source validation, the durable Geometry operation and verified final result. The phone never calls Geometry directly; an LLM request is not required for capture processing. Native/cloud setup is in [backend README](../README.md) and [deployment](cloudflare-deployment.md); HTTP models are in OpenAPI/[schemas.py](../src/ai_inspector/schemas.py).

## Source and state transitions

The device completes an assigned capture using a supported format from `/v1/capabilities`. [iOS integration](ios-integration.md) describes assigned uploads, independent test exports and reconnect; [ZIP format](zip-capture.md) defines single-archive layout. Supabase per-file, single-ZIP and geometry-parts inputs normalize to accepted source inventory. The versioned S3 path remains supported separately.

The first completion response is 202: the reference is durable but not accepted yet. Same completion ID/content replay returns 200 with that operation's current status; changed content is 409. Another completion cannot overtake a pending one. Permanent input rejection is retained; a corrected set needs a new manifest/completion ID. Temporary storage/service failures retain IDs and pending state.

Validation enforces scope, schema, frame/segment identities, file count/size/hash, archive safety and required RGB/metadata plus conditional depth/confidence. It accepts frame.json 0.1/0.2; source validity does not establish geometric quality, units/alignment across segments, measured coverage or construction compliance. Geometry Engine dispatch requires RGB-D; accepting an RGB-only source does not imply that this consumer supports it.

| State/fact | Meaning |
|---|---|
| submitted / validating | Completion stored; source verification unfinished |
| rejected, capture returns to capturing | Invalid/missing/corrupt input; no Geometry dispatch |
| sealed / queued_geometry | Source accepted and pinned; Geometry result not ready |
| dispatch delivered | Geometry accepted the job; no result implied |
| completed / geometry_only_v1 | Terminal Geometry response reconciled and saved as geometry_result |

Model/room context is copied from server assignment. Opaque BIM intake is not validated IFC/room membership. In Supabase mode, accepted bytes are copied/pinned under server ownership and checked on reuse. In S3 mode, exact versions are pinned. Signed URLs are refreshed for authorized consumers and do not become operation identity or model context.

## Versioned S3 compatibility

S3 completion uses capture_manifest_v1 and exact manifest version/length/SHA-256. The bucket is chosen from configured storage identity, never an arbitrary caller URL/bucket. Manifest prefix/completion/session must match, and every referenced object has its exact version. Multipart boundaries and expiring links are not identity. Trusted full checksums may avoid re-downloading large objects; otherwise hash verification streams the bytes.

Only this S3 path requires INSPECTOR_CAPTURE_BUCKET/AWS versioned-object access. The Supabase formats use backend Supabase credentials and scoped upload plans instead. Do not invent S3 version IDs for Supabase objects or require AWS credentials for a Supabase deployment.

## Geometry service API

Inspector uses the Geometry Engine's existing API: `POST /inspections`, `GET /inspections/{remote_job_id}` and `GET /inspections/{remote_job_id}/result`. There is no selectable protocol or second Geometry API. Inspector retains its own operation/job identity and records the remote job ID after admission.

Configure `INSPECTOR_GEOMETRY_BASE_URL`, the outgoing `INSPECTOR_GEOMETRY_API_TOKEN`, and the Inspector Supabase origin/bucket/server credential. By default Geometry uses that same private Storage. If Geometry has a separate Supabase project, set all three `INSPECTOR_GEOMETRY_SUPABASE_URL`, `INSPECTOR_GEOMETRY_SUPABASE_STORAGE_BUCKET`, and server-only `INSPECTOR_GEOMETRY_SUPABASE_SECRET_KEY`. Only Geometry exports and result manifests use that destination; original capture reads, BIM storage and Inspector metadata remain in the original project. Frozen commands pin the export origin/bucket and refuse a changed destination. No historical data is migrated. Its current API is single-tenant and does not enforce Inspector bearer/project scopes: it must be a trusted service, not a multi-tenant authorization boundary. Inspector permits HTTPS or loopback HTTP and never follows redirects.

For a Basic-authenticated deployment, set `INSPECTOR_GEOMETRY_API_USERNAME`; the token is its password. These server-only credentials also authenticate verified same-origin evidence downloads. An empty username retains Bearer authentication. Credentials never follow redirects or enter model context.

The existing capture worker exports the accepted RGB-D source into Geometry's short layout with the same session/observation/segment IDs and byte-identical files. A separate compatibility manifest records the original accepted capture_ref; it does not replace Inspector source identity. The adapter currently supports original frame.json 0.2 in one ARKit segment and a pinned IFC. Older frames, RGB-only or geometry-parts-only inventories and unaligned multiple segments are explicitly unsupported by this Geometry API. No hidden conversion/alignment or invented IFC is performed. S3 versions can be copied into the configured Supabase export while preserving their original provenance.

The concrete export/request is durable before uploads. Each claim copies at most four files, verifies immutable duplicate copies, and checkpoints position. Inspector pins the selected IFC bytes to a hash-based `.ifc` key. `POST /inspections` uses the persisted operation UUID as Idempotency-Key and identical canonical request bytes on retry; its returned job ID is stored separately from Inspector job ID. `GET /inspections/{remote_job_id}` verifies session, exact input keys/parameters and IFC hash. `running/completed` normalize to `processing/ready`; admission alone never establishes a verified result.

For completion, Inspector reads `/inspections/{remote_job_id}/result`, validates compact JSON 2.0, and checks the committed result/stage manifests from the configured Storage by hash. Artifact/preview paths, sizes and hashes must be members of these manifests; preview frame IDs must belong to the submitted observations. Signed file URLs must address this exact Geometry job on the configured origin. Only requested evidence previews are downloaded, with bounded size and hash verification. Large mesh/cloud bytes are not loaded into the model.

Geometry does not report complete used/excluded observation accounting, uncertainty or alignment proof. The saved result explicitly says `observation_accounting=unreported_by_geometry`; empty evidence/excluded arrays do not mean every frame was used. Job/geometry revisions are Inspector acceptance revisions (`revision_origin=inspector_acceptance`), not upstream revision claims. Geometry's algorithm tolerance (default 0.1 m) is not a project acceptance rule. Live/recorded processing and synthetic capture/model provenance remain separate fields.

Expiring URLs and full review checkpoints stay in private receipt namespaces (`geometry-result-files`, `geometry-review-state:<session>`); they do not enter phone events or model input. Existing tables serve SQLite, PostgreSQL and Cloudflare; no new migration is required.

### Compact result 2.0 → agent evidence

The three input fields `session_id`, `capture_manifest_path` and `ifc_object_key` identify a Geometry request, not a completed result. Inspector generates the keys from its pinned source/IFC; a caller's example `reference.ifc` path is not a reason to replace the saved hash-based IFC key. Status/source/result-manifest checks above are still required before agent review. A downloaded JSON attachment alone does not establish these bindings or permission to fetch its URLs.

New reviews pin evidence projection version 2. Existing reviews without this version retain the previous candidate ordering, evidence bytes and case/request IDs on retry.

| Geometry field | Agent interpretation |
|---|---|
| `units`, `difference_convention` | Preserve meters and `scan_minus_ifc`; a negative dimension difference reports a smaller scan value |
| `tolerance` | Preserve as Geometry's processing parameter; project acceptance remains the separate optional caller rule |
| `summary`, `job_id`, `session_id` | Preserve upstream counts/identity in each reference alongside Inspector's source binding; review case counts also include surfaces |
| `objects[].dimensions`, `displacement` | Preserve raw values and supplied metric measurements; null stays unknown, with no invented zero, uncertainty, full extent or alignment |
| `match_status`, `quality` | Preserve correspondence and all flags/issues. Unmatched/ambiguous/unobserved/unavailable and possible duplicate fragments are evidence for review, not automatic defects |
| `surfaces[]` | Review every returned surface, including `observed_within_tolerance`; p95, area fraction and compared area describe the observed subset, while `whole_element_verified=false` precludes a whole-element claim |
| `photos[]` | Privately bind object/frame/role to committed file hashes. Import RGB with its overlay (mask fallback), then another frame, within the six-asset budget. Reference JSON maps imported asset IDs to frame IDs/roles; unimported frame IDs remain in the source snapshot |
| `artifacts` | Supply verified metadata only; IFC/point-cloud/mesh bytes are not claimed to have been read by the model |

RGB evidence must address its declared submitted frame's committed `input/frames/<frame_id>/rgb.jpg`. Older accepted photo receipts remain usable, with an explicitly unknown role/frame when that association was not saved. Signed links remain private, including during refresh. The complete sanitized compact result is retained under `geometry` in the final review JSON, even if a verifier cites no measurements; Bob's bounded summary includes upstream counts, surface coverage flags and the distinction between processing and acceptance tolerance.

### Historical saved commands

The previous Geometry API, inbound source/event endpoints, service identity and bundled mock server have been removed. Previously frozen commands for that API are not converted or replayed: the worker records `geometry_contract_retired` without network IO or a new Geometry job. Command bytes, accepted source, completed results, receipts and historical event rows are retained. A command that was never frozen or sent can use the current API after its accepted source and IFC pass the current adapter requirements. No database reset or destructive migration is performed.

## Ownership and recovery

Durable completions/jobs/work leases survive restart. Native API startup starts the capture worker; [worker.py](../src/ai_inspector/worker.py) provides an optional bounded/manual driver. Cloudflare uses the existing bounded process/cron. Delivery and model/remote-inspector recovery are different loops; Geometry completion starts a continuation only for the explicitly registered downstream review described below.

Offline is not cancel. Abort invalidates Inspector's work leases and preserves data, but cannot revoke issued upload URLs or terminate an already dispatched external job. A stale worker cannot publish after lease replacement/cancellation. A later rescan creates a linked session; it does not mutate the sealed source/job.

Configure the Geometry base URL and outgoing API credential for the intended deployment. Get setting names from [.env.example](../.env.example) and current limits/mode from capabilities. Historical mock output retains its explicit analysis_origin=mock label; external/unconfigured flags alone do not establish a successful reconstruction. Keep deployment/test observations in dated reports, not as timeless environment facts in this contract.

## Downstream discrepancy review

The [IFC/scan candidate review agents](ifc-discrepancy-review.md) consume supplied candidate batches. Inspector now prepares those batches automatically from the accepted compact result, pins bounded evidence and continues through the existing A2A services when review was requested. This conversion is not a new detection/measurement algorithm. Supplied numbers retain unreported algorithm version/uncertainty/extent/alignment rather than invented certification. Unknown metric quality cannot confirm a numerical violation. Human report formatting and geometric accuracy validation remain separate.

Set `INSPECTOR_GEOMETRY_REVIEW_INSPECTOR_ID` to a registered remote `discrepancy_inspector`; deploy both Geometry Inspector and Geometry Verifier from code supporting `source.origin=geometry` and IFC IDs containing `$`. The orchestrator's `session_create` has optional `review_after_geometry` (default false), used only for an explicit inspection request. A new chat must select exactly one IFC; the session pins its bytes before capture assignment. Existing IFC-bound sessions may be reused; a capture-only session is not silently rebound to another model.

For an already linked session, an operator can register review before or after Geometry completion:

```text
POST /v1/chats/{chat_id}/geometry-review
{"schema_version":"0.1","request_id":"<UUID>","session_id":"<UUID>","task":"Inspect the captured door against IFC","acceptance_tolerance_m":null}
```

One immutable request per chat/session is allowed. Identical replay returns current state; changed content conflicts. An optional positive `acceptance_tolerance_m` (at most 1 m) is an explicit caller-provided acceptance rule; omitting it keeps acceptance unknown. It does not change Geometry's saved processing parameters. Never infer a project rule from Geometry's default tolerance.

The capture queue waits without model calls, imports a bounded evidence subset, and prepares one case per batch (within existing 12-case/6-asset limits). It handles up to 120 candidates per review and accounts for all accepted batches; larger scopes fail explicitly for splitting. Unmatched/unobserved/ambiguous objects are candidates, not absence/compliance verdicts. Aggregate surface metrics remain scoped statistics. No candidates yields a qualified summary, not a compliance certificate.

Each internal batch deterministically delegates through the registered Geometry Inspector → Geometry Verifier chain; its accepted report is checked again by Bob's backend. Internal batch runs remain durable but do not create intermediate chat replies. A separate continuation summarizes all accepted batches once, with the complete JSON available as `geometry-review-report.json` in chat Files. Pending active user messages delay admission without changing the frozen request. A changed chat session or cancelled review cannot publish its old continuation. Missing configuration is visible and retryable; profile changes hold the saved workflow until restoration.

`GET /v1/chats/{chat_id}` includes safe `geometry_reviews` status/count/file references. `POST /v1/chats/{chat_id}/geometry-review/{review_id}/cancel` fences that review's local executions and future publication; it does not claim to cancel an already running external Geometry or verifier task. Capture abort cancels its pending review wait. Native capture workers and Cloudflare process/cron drive the same queue without a browser. Unrequested Geometry jobs never trigger a model automatically.

## Inspection results viewer

The workspace opens one viewer from capture tasks, chat result cards and the saved
`geometry-review-report.json`. **Whole model** shows the model with selectable
review markers; **Findings** keeps the selected entry, explanation and evidence
together. Model, Photos and Next steps share that selection. Reviewed decisions
and all Geometry observations have separate lists; unmatched objects, processing
tolerance and partial surface statistics do not become inspection verdicts.

Operator-authenticated, read-only routes:

- `GET /v1/chats/{chat_id}/geometry-view?session_id=<UUID>` returns the current or
  historically linked session's accepted source projection, all object/surface
  observations, available model descriptors and server-accepted case decisions.
  It preserves original case IDs, frame/role mappings, quality flags, reported
  units/sign and separate processing/acceptance tolerance. Cancelled reviews do
  not present their partial decisions as a published result.
- `GET /v1/chats/{chat_id}/geometry-view/{session_id}/images/{image_id}` returns an
  image from the accepted private photo receipt. IDs bind stage/path/hash, never
  an arbitrary URL. Length/hash checks and the existing identity-preserving link
  refresh apply. Original, overlay and mask remain views of the same frame;
  legacy images with missing associations are explicitly unreported.
- `GET /v1/chats/{chat_id}/geometry-view/{session_id}/models/{artifact_id}` serves
  only `ifc`, `mesh`, `segmented_mesh` or `point_cloud`. IFC comes from the pinned
  Inspector model store. Scan artifacts retain their accepted Geometry identity
  across link refresh. Previews are bounded by the smaller of 32 MiB and
  `max_capture_file_bytes`; oversized models fail explicitly without upstream IO.

These routes enforce operator/project/chat/session/source/model/job bindings and
never dispatch work or call a model. Signed URLs and service credentials remain
server-only. Requests and unavailable evidence have visible retry states. Closing
the viewer or switching chats invalidates pending display loads and releases
image URLs and 3D resources without cancelling the underlying inspection.

IFC markers/focus use exact GlobalIds resolved in the loaded reference model.
An unresolved or ambiguous binding never creates a guessed marker. Scan meshes
and point clouds render as separate sources: compact 2.0 supplies neither the
scan-to-IFC transform nor per-segment mesh membership, so overlay and scan-element
focus are not asserted. The Next steps view displays accepted `data_requests`;
it does not infer calibrated camera positions or automatically request capture.
Photos remain accessible when a model is missing, too large or fails to render.
