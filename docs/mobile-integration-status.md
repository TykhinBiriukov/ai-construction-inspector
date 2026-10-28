# iOS integration — updated 2026-10-28

## Current selected transport

Oleg approved switching to the server's implemented **signed upload plans**. The earlier direct Supabase Auth-user proposal is superseded. The authoritative first-case wire contract is [Inspector integration](inspector-server-integration.md), supplied by the Inspector owner. No device token is committed here.

1. Configure HTTPS Inspector origin, device ID and scoped device token in the app. Token is stored in device-only Keychain, scoped to origin/device. No Supabase login or admin key.
2. Foreground app opens `/v1/devices/{device_id}/events/ws`, sends JSON ping every 15 seconds and answers ping with pong. HTTP assignment reconciliation also runs, with failure backoff. Background suspension is expected.
3. Persist assigned session and immutable context; acknowledge with a stable operation ID. Only a newly `assigned` session opens capture; historical sealed sessions do not.
4. On camera start send `capture-start` with actual device/OS/app metadata. Persist the exact body for retries. Capture one ARFrame per observation, RGB-only or RGB-D. A reset creates a distinct segment. The first server case has `instruction_id=null`.
5. On finish freeze inventory and build one ZIP: `capture-manifest.json` at archive root, and `observations/<observation_id>/{frame.json,rgb.jpg,depth.f32?,confidence.u8?}`. No mesh is required for this transport archive. The manifest keeps `format=capture_manifest_supabase_v1`; each file's `object_key` equals the assigned `session.storage.key_prefix` plus its relative path inside ZIP. Byte lengths and hashes describe the original bytes.
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
