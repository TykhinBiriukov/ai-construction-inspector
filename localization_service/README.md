# Localization Service

Cloud deployment preparation and persistence: [deploy/README.md](deploy/README.md).
The sections below describe the original harness and LAN prototype; the deployed
cloud path is not yet verified.

## Original development foundation

This component owns localization guidance and spatial calculations. Inspector owns
inspection guidance and the durable cross-service control handoff. Existing Geometry
and final capture ZIP are unchanged. Canonical proposed protocol:
[shared contract §13](../ai_inspector/docs/iphone-geometry-bim-inspector-api-v0.1.md).

Implemented now: internal HTTP harness, SQLite state, known-door initial guidance,
independent ARKit segments, idempotent evidence batches, explicit instruction owner,
rigid transform validation/composition, stale-pose hiding and tracking-loss recovery.
No dependencies beyond Python standard library. Tested with the existing system Python on this Mac.

**Production integration not implemented:** general BIM parsing/registration, validated
quality policy, coverage, production service hosting, Inspector adapter/callbacks.
A restricted real-data solver and LAN test are now available in lab_server.py; see below.
In the original server.py harness the default solver is absent: evidence produces `blocked`, never a fabricated
alignment. Synthetic solver exists only in unit tests and labels its results.
Do not advertise live localization capability on the production Inspector yet.

## Local use

From repository root, provide a dedicated random service token through environment
`LOCALIZATION_SERVICE_TOKEN` (at least 24 characters; do not commit or reuse device
credentials), then run `python3 -m localization_service.server`. Binds only loopback
127.0.0.1:8787. Private state is in `.local/localization/state.sqlite3`; override with
LOCALIZATION_DATA_DIR. No server was deployed by this change.

All calls require Authorization Bearer with that service token:
- GET /health: reports solver readiness (false by default).
- POST /internal/sessions/{UUID}: JSON context (pinned model/room/door/frame, or null).
- POST /internal/sessions/{UUID}/start: segment_id.
- POST /internal/sessions/{UUID}/evidence: batch_id, segment_id, observations.
- POST /internal/sessions/{UUID}/pose: camera.pose fields from the shared contract.
- GET /internal/sessions/{UUID}: spatial snapshot.
- POST /internal/sessions/{UUID}/finish: empty object.

This is a development service-to-service adapter, not a phone-facing API. Inspector
must verify/pin files before supplying source_verified references. This flag is a
trust boundary, not a replacement for validating actual bytes. The service currently
accepts references, not downloads. Inspector integrates validated service results
into its own spatial-state, merges/resumes its instruction and assigns authoritative
control_revision. Local revisions are not cross-service concurrency tokens.

Next: implement a solver adapter with solve(context, verified_observations), returning
localized with T_bim_arkit/evidence IDs/analysis_origin, needs_more_evidence with an
instruction, or explicit unsupported/failure. Select thresholds against real BIM and
LiDAR data. No caller-facing endpoint accepts an arbitrary alignment as verified.

Tests: `python3 -m unittest discover -s localization_service/tests -v`.

## Real Wi-Fi experiment: known door, four views

`lab_server.py` and the iOS Settings → Wi-Fi LiDAR test provide a separate, explicitly
labelled local experiment. It does not use/modify Inspector sessions, Supabase or the
Geometry ZIP flow. The user authorized this path while the Inspector API is pending.

Implemented for this experiment:
- Restricted IFC importer: four axis-aligned walls, one floor, one space, one opening
  and door on the min-X wall. Metres required; unsupported layouts fail explicitly.
  Original IFC is never rewritten. IFC Z-up maps to BIM Y-up by (x,z,-y).
- Real LiDAR backprojection from original depth/confidence and K_rgb/T_world_camera.
- Gravity-aligned floor and wall RANSAC, rigid alignment candidates and rejection of
  insufficient/ambiguous evidence. Initial condition: **first photo centres the known
  door from inside**. Door dimensions are an approximate prior, not a precision check.
- Four initial observations; additional views if floor/corner/overlap is insufficient.
- Live pose updates, top-down room outline, camera heading and saved-photo positions.
  Photo points are not a wall coverage heatmap. No compliance decision is made.
- Original bytes on phone (Documents/localization-lab) and Mac (data directory),
  hash/size/JPEG validation, persistent session state, retry of exact observation IDs.

This is an experimental specialized solver, not general automatic scan-to-BIM.
Synthetic fixtures recover a known transform and reject missing floor/corner. These
checks do **not** establish real-room accuracy. Plane-fit RMSE is not global pose
accuracy. Furniture, partial occlusion, a different room, incorrect initial aim,
nonrectangular geometry or uncertain door dimensions can still cause failure or
wrong association; verify the displayed camera against real landmarks. Thresholds
remain experimental until a real capture is evaluated.

Start (Python 3.12 with requirements installed; a compatible existing runtime was
reused on Oleg's Mac, no new large stack installed):

```sh
python -m localization_service.lab_server --ifc /absolute/path/room.ifc \
  --data .local/live-localization --host YOUR_WIFI_IP --allow-network YOUR_WIFI_SUBNET
```

Port 8788, bound to the specified interface. Only clients in the supplied subnet are
accepted, plus a separate random code in data/pairing-code.txt (0600). Not exposed
via an internet tunnel. The iOS URL uses the Mac's `.local` hostname and HTTP local
network allowance; Inspector HTTPS remains unchanged. Pairing code goes in Keychain,
not Git/Info.plist/logs. The service must keep running and both devices need network
connectivity; campus/guest Wi-Fi may isolate clients despite the same network name.

All `/test/*` endpoints require X-Localization-Code. POST test/sessions uses a stable
operation_id. POST test/sessions/{s}/start registers segment_id. POST observations
accepts base64 exact frame.json/rgb.jpg/depth.f32/confidence.u8, bounded to 16 MiB.
POST pose returns BIM camera pose; GET session returns current instructions/result.
This lab transport is explicitly separate from the production shared API contract.

Sources for coordinate/import implementation:
- https://developer.apple.com/documentation/arkit/displaying-a-point-cloud-using-scene-depth
- https://docs.ifcopenshell.org/ifcopenshell-python/geometry_processing.html
- https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nsallowslocalnetworking

The local `/test/config` also provides triangulated IFC walls, slab and door as
`meshes` (vertices/normals/triangle indices). Vertices use the same right-handed,
metre, Y-up BIM frame as registration. The iOS lab renders these with SceneKit,
with camera pose directly from `T_bim_camera` and local forward -Z. Orange dots
are observation camera locations, not measured surface coverage. This replaces
the former 2D canvas whose screen-Z mapping reflected the top view.

Measured coverage in the Wi-Fi lab uses 10 cm wall/floor tiles, >=3 high-confidence
LiDAR endpoints per saved observation within 6 cm of the pinned rectangular BIM
surface and incidence cosine >=0.3. Door openings/boundary-crossing tiles are
excluded. Coverage records retain observation IDs, are rebuilt after alignment
changes, and disappear without alignment. This is measured support, not image
quality, defect assessment, or hidden-surface coverage. Geometry is out of the
current test scope; production Inspector integration remains separate.
