# Accepted capture → Geometry handoff

Inspector owns source validation, the durable Geometry operation and verified final result. The phone never calls Geometry directly; an LLM request is not required for capture processing. Native/cloud setup is in [backend README](../README.md) and [deployment](cloudflare-deployment.md); HTTP models are in OpenAPI/[schemas.py](../src/ai_inspector/schemas.py).

## Source and state transitions

The device completes an assigned capture using a supported format from `/v1/capabilities`. [iOS integration](ios-integration.md) describes assigned uploads, independent test exports and reconnect; [ZIP format](zip-capture.md) defines single-archive layout. Supabase per-file, single-ZIP and geometry-parts inputs normalize to accepted source inventory. The versioned S3 path remains supported separately.

The first completion response is 202: the reference is durable but not accepted yet. Same completion ID/content replay returns 200 with that operation's current status; changed content is 409. Another completion cannot overtake a pending one. Permanent input rejection is retained; a corrected set needs a new manifest/completion ID. Temporary storage/service failures retain IDs and pending state.

Validation enforces scope, schema, frame/segment identities, file count/size/hash, archive safety and required RGB/metadata plus conditional depth/confidence. It accepts frame.json 0.1/0.2; source validity does not establish geometric quality, units/alignment across segments, measured coverage or construction compliance. RGB-only dispatch needs the configured consumer's explicit support.

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

## Geometry service contract

Inspector persists job/operation IDs and the concrete request before:

```text
PUT {INSPECTOR_GEOMETRY_BASE_URL}/internal/v1/jobs/{job_id}
```

The body binds session/inspection/project/room/model/profile and source_ref containing the protected Inspector source endpoint plus exact manifest identity. Consumers use their dedicated Geometry identity:

- `GET /internal/v1/sessions/{session_id}/capture-source` returns the authorized exact manifest reference and file-list address.
- `GET .../capture-source/files` supplies cursor-paginated short-lived links to accepted files/versions only.

Download directly from the selected storage using returned links. Never re-discover a mutable folder or substitute a newer manifest. Supabase and versioned S3 retain their distinct provenance. Numbered Geometry parts/ARKit segment support must be checked against the actual consumer; backend upload acceptance does not certify external interoperability.

`POST /v1/sessions/{id}/geometry-jobs/dispatch` only accelerates an existing pending delivery; it does not create another processing pipeline. An agent tool must reuse this same operation. Transport timeouts/429/transient failures preserve job ID, operation key and exact request; redirects are refused and retry delay is bounded by the adapter policy. Missing service configuration leaves work pending, never a fake success.

Callbacks enter the durable authenticated inbox as hints. Inspector reads/reconciles the exact job through the Geometry status API and checks source/result bindings before accepting ready/failed. Polling recovers lost callbacks. A ready admission receipt is not a verified result. [geometry_results.py](../src/ai_inspector/geometry_results.py) defines accepted result validation; [workflow.py](../src/ai_inspector/workflow.py) defines source/dispatch transitions.

## Ownership and recovery

Durable completions/jobs/work leases survive restart. Native API startup starts the capture worker; [worker.py](../src/ai_inspector/worker.py) provides an optional bounded/manual driver. Cloudflare uses the existing bounded process/cron. Delivery and model/remote-inspector recovery are different loops; Geometry completion does not currently start an agent continuation.

Offline is not cancel. Abort invalidates Inspector's work leases and preserves data, but cannot revoke issued upload URLs or terminate an already dispatched external job. A stale worker cannot publish after lease replacement/cancellation. A later rescan creates a linked session; it does not mutate the sealed source/job.

Configure Geometry base URL, Inspector public origin and distinct directional service tokens for the intended deployment. Get setting names from [.env.example](../.env.example) and current limits/mode from capabilities. Mock output has explicit analysis_origin=mock; external/unconfigured flags alone do not establish a successful reconstruction. Keep deployment/test observations in dated reports, not as timeless environment facts in this contract.
