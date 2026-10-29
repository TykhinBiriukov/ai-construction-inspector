> 28 October update: new captures now prepare independently sized Geometry ZIP parts (`supabase_geometry_parts_v1`), gated by server capabilities. See revision 8 of the common contract. Individual-file and old ZIP pending requests remain compatible. The parts contain original frame IDs, exact depth bytes, segment-specific mesh and the Geometry metadata schema. Root-bucket writes and direct Geometry calls are not used. Code is prepared; cloud/device rollout must be reported separately.

# iOS integration — updated 2026-10-28

## Current selected transport

Oleg approved switching to the server's implemented **signed upload plans**. The earlier direct Supabase Auth-user proposal is superseded. The authoritative first-case wire contract is [Inspector integration](inspector-server-integration.md), supplied by the Inspector owner. No device token is committed here.

1. Configure HTTPS Inspector origin, device ID and scoped device token in the app. Token is stored in device-only Keychain, scoped to origin/device. No Supabase login or admin key.
2. Foreground app opens `/v1/devices/{device_id}/events/ws`, sends JSON ping every 15 seconds and answers ping with pong. HTTP assignment reconciliation also runs, with failure backoff. Background suspension is expected.
3. Persist assigned session and immutable context; acknowledge with a stable operation ID. Only a newly `assigned` session opens capture; historical sealed sessions do not.
4. On camera start send `capture-start` with actual device/OS/app metadata. Persist the exact body for retries. Capture one ARFrame per observation, RGB-only or RGB-D. A reset creates a distinct segment. The first server case has `instruction_id=null`.
5. **Superseded on 28 October for new captures:** upload individual observation files, then `capture-manifest.json`, using the already supported `observation_files` and `capture_manifest` plans; complete with `supabase_capture_v1`. This avoids the aggregate ZIP limit. Files upload sequentially and saved object keys resume progress. An old frozen ZIP without a prepared completion request migrates to files, keeping session/inventory/completion IDs and originals; already prepared completion requests retain their transport. Individual cloud objects remain limited to 16 MiB and the manifest to 4 MiB. The following describes the retained legacy ZIP path: on finish freeze inventory and build one ZIP: `capture-manifest.json` at archive root, and `observations/<observation_id>/{frame.json,rgb.jpg,depth.f32?,confidence.u8?}`. No mesh is required for this transport archive. The manifest keeps `format=capture_manifest_supabase_v1`; each file's `object_key` equals the assigned `session.storage.key_prefix` plus its relative path inside ZIP. Byte lengths and hashes describe the original bytes.
6. Request `/upload-plans` with `kind=capture_archive`, the stable completion UUID, and one `capture.zip` object with media type `application/zip`. Upload exact ZIP bytes with the returned method/headers. Complete using `capture_ref.format=supabase_capture_zip_v1`, `storage_id`, returned `archive_key`, `archive_byte_length`, `archive_sha256`. No bucket name/HTTPS URL goes in the key. Persist ZIP, digest, plan request ID and completion before network retries. Pending is not accepted; rejected completion permits a new completion ID on manual retry. Earlier pending per-file submissions keep their previous protocol.
7. Once accepted, return to waiting. Local originals and journal are retained. The server handles Geometry dispatch. The app labels `geometry_mode=mock` explicitly. It does not yet display Geometry artifacts or inspection conclusions.

## Geometry ZIP v1: local export, upload pending server extension

The Inspector owner has now deployed a **different transport ZIP** as described above; that format is implemented for upload. Its published API includes `capture_archive`, `capture.zip`, `application/zip`, and `supabase_capture_zip_v1`. The Geometry-owner archive below remains a separate local export: it contains `capture.json`, `mesh.ply`, and numbered `frames/`.

Oleg confirmed: **Inspector continues to launch Geometry**; the phone does not call `/sessions/extract`. Do not widen Storage policies to allow root writes. Inspector and Geometry owners must agree conversion/consumption of these different archives; receiving Inspector's transport ZIP does not imply Geometry's mesh contract is satisfied.

ZIP layout (no outer directory):

```text
capture.json
mesh.ply
frames/000001/rgb.jpg
frames/000001/metadata.json
frames/000001/depth.bin       # only if available
frames/000001/confidence.bin  # only if available
```

ARKit runs with gravity alignment and mesh reconstruction when supported. Anchors are replaced on update and removed on removal; vertices are transformed into world coordinates and triangle indices rebased. PLY is binary little-endian, XYZ + triangular faces. Export rejects absent/invalid mesh, synthetic captures and mixed segment coordinates. Depth export converts invalid/nonpositive values to zero; original `depth.f32` remains unchanged. JPEG geometry is validated without rotation/resizing. ZIP uses uncompressed standard ZIP entries with CRC32, and rejects sizes requiring ZIP64.

On iPhone 11 RGB capture remains supported; Geometry mesh export requires a compatible LiDAR device. Capture the Geometry fixture in one uninterrupted camera run. No real mesh export is claimed verified until tested on such a device.

## Verification and remaining checks

- Unsigned iOS build, local capture/inventory tests, manifest/completion schema tests and ZIP/PLY/depth fixture checks are run locally (see chat for latest results).
- Authenticated Inspector capabilities and assignments were reachable. Reported `geometry_mode=mock`; the existing assignment was sealed/completed and must not trigger new capture.
- On 2026-10-28 Oleg reported the test run completed normally after the UUID fix and iPhone update. Record this as user-confirmed mobile workflow success. No new server artifact/measurement evidence was supplied with that confirmation; do not infer real reconstruction, BIM alignment, or accuracy. The last independently queried Geometry mode was mock.
- The real receipt-casing bug is fixed: server UUIDs are compared semantically, not as case-sensitive strings; new generated UUIDs are lowercase. After installation, the phone journal confirmed preserved assignment, accepted acknowledgement and registered capture-start.
- Remaining: validate real LiDAR/mesh export and agree Inspector-to-Geometry archive conversion; verify external Geometry/BIM/Meta components separately.
- Native remote APNs remains unavailable on the Personal Team. Foreground WebSocket and optional local notification do not wake a suspended app.

## Localization preparation (28 October, local development)

The iOS client now has capability-gated live_bim_v1 models, single-owner guidance,
registered ARKit segments, bounded pose telemetry, durable early per-observation
uploads and evidence-batch retries. Original files still enter the final ZIP.
Old servers without the capability keep the existing capture flow. Default endpoint
for fresh installs is inspector.campuscompile.eu; saved settings are not overwritten.

The shared contract remains the only protocol document:
[revision 6, section 13](../ai_inspector/docs/iphone-geometry-bim-inspector-api-v0.1.md).
It now includes spatial-segments registration so the server knows an ARKit coordinate
frame before accepting poses. The Inspector owner must implement that endpoint,
evidence-batches, spatial-state and camera.pose handling before advertising readiness.
The app displays text/status only; a live BIM scene, coverage overlay and voice are
not yet implemented. Early HTTP batch acknowledgement means queued, not successful
localization. A dropped service does not delete local images or imply success.

The new localization_service/ is a standalone local HTTP/domain harness with SQLite
and a solver interface. It has no geometric registration implementation yet and is
not deployed. Tests use an explicitly synthetic solver. Real known-door localization,
quality thresholds and visibility/coverage require the matching BIM and LiDAR data.

Checks (from checkout root):

```sh
xcrun swiftc -module-cache-path /tmp/inspector-swift-cache ios/AIInspector/Observation.swift ios/AIInspector/SessionLifecycle.swift ios/AIInspector/GeometryExport.swift ios/AIInspector/SpatialCapture.swift ios/Checks/main.swift -o /tmp/inspector-checks
/tmp/inspector-checks
python3 -m unittest discover -s localization_service/tests -v
```

No real phone installation, real Localization/Inspector exchange or BIM accuracy
claim follows from these local tests and unsigned build.

## Standalone test capture (29 October 2026)

The waiting screen offers **Тестовая съёмка без задания**. The phone generates and retains a random session UUID, captures RGB + frame metadata (and depth/confidence when available), with no assignment, BIM localization, early evidence or Geometry dispatch. Each camera restart remains a separate ARKit segment. After verified upload, a success card shows the session UUID, a copy-ID button with confirmation, and the Storage folder navigation; it also appears when reopening a completed session. Closing/reopening resumes the same local test session; after a successful upload, **Новая тестовая сессия** clears only the pointer, preserving originals.

Uploading still requires the configured Inspector HTTPS endpoint/device token, solely to obtain signed Storage permissions from `POST /v1/standalone-captures/{session_id}/upload-plans`. No Supabase secret is embedded in iOS. The backend must include `standalone_capture.py` and its API route before use. This endpoint accepts the existing `observation_files` and `capture_manifest` request schemas and enforces device authentication and configured file-size limits.

Files are private objects in bucket `inspection-captures`:

- `standalone/{authenticated_project}/{authenticated_device}/{session_uuid}/observations/{frame_uuid}/{sha256}/{filename}`
- `standalone/{authenticated_project}/{authenticated_device}/{session_uuid}/manifests/{sha256}/capture-manifest.json`

The manifest has format `standalone_capture_v1`, carries exact object keys, sizes and SHA-256, and is uploaded last after server-side readback verification of every file. Consumers discover manifests rather than incomplete folders. `inspection_id` and `project_id` in this standalone manifest are the literal marker `standalone`, not Inspector entity IDs; the authenticated storage prefix determines ownership. `model_version=unbound`; camera poses refer only to each local ARKit segment, not BIM. Frame metadata retains the existing schema, intrinsics, transform convention and depth encoding; RGB-only data has no fabricated depth. No Inspector DB session/completion record is created. Retries use immutable content-addressed paths; frozen local inventory survives app restart.

Validation: unsigned iOS build succeeded; local FastAPI checks with a fake Storage adapter passed authentication, namespace, retry, invalid path, file-size and no-session cases. Real phone/Supabase flow is pending deployment and installation.

## Mobile visual refresh (29 October 2026)

Warm ivory/forest-green styling now matches Inspector workspace. Capture uses the available main viewport with shutter, count and finish controls anchored below; a compact plan opens a separate BIM sheet without intentionally restarting ARKit. The sheet supports orthographic plan/3D, presentation-only near-wall cutaway, coverage and photo-marker toggles. Coverage still uses exactly the server-provided tile centers/sizes; no unmeasured coverage is interpolated. The camera indicator uses the validated pose and disappears when stale. Entry selection and the b9217cc capture gate remain intact. Standalone completion preserves copyable session IDs and original data.

The agreed generated room icon is bundled in Assets.xcassets as AppIcon and InspectorMark (opaque 1024px asset). The app includes a confirmed “Выйти из задания” action using the server's device-scoped abort endpoint; cancellation requires server connectivity and retains local originals. Free capture can be closed and resumed separately. No new localization or measurement algorithm is introduced by this design change.

Free capture also offers “Сбросить и начать заново” in the actions menu and finish dialog. After explicit destructive confirmation it stops capture, removes only that session's local directory (photos, metadata and retry inventory), clears its saved pointer, and opens a fresh UUID session immediately. This action sends no upload/cancellation request and does not delete previously uploaded cloud objects. Reset is unavailable while capturing a photo or uploading. Closing without reset continues to preserve the current session for resuming.
