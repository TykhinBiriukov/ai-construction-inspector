# Progress 4D — local photo/IFC service

An asynchronous, Inspector-callable service for a dated sequence of photo surveys:
image-only camera/point reconstruction → metric IFC alignment → CPU multi-view textured
surface → two-view product evidence → saved timeline, activity and conditional
schedule forecasts. The source IFC is never substituted for reconstructed geometry.
No mandatory human review stage is imposed. Unknown and occluded work stay explicit.

## Supported scope

- One rectangular, axis-aligned room per project. Permanent walls/slab provide
  registration geometry; scheduled products are excluded from alignment targets.
- 8–100 overlapping upright photos, ordered along the capture path, from one camera
  with fixed zoom and equal image dimensions. Dates must include a timezone.
- One IFC work schedule with explicit task dates, assigned products and finish-start
  dependencies without lag. Product descriptions should identify intended materials
  and installation criteria. Unsupported references are rejected or inconclusive.
- With OpenMVS enabled, geometry output is a photographic textured GLB surface.
  The GLB axes are IFC X,Z,-Y in metres; point JSON remains IFC X,Y,Z. Mesh gaps
  remain explicit; neither hidden surfaces nor finish thickness are measured.
  Without the native adapter the legacy sparse/stereo point output is available.
- Quantities are counts of explicitly identified IFC products with photo evidence.
  They are not measured areas/volumes. Covered work retains its earlier evidence date.
- Rates require two fully observed counts for the same task. A separate schedule
  scenario retains baseline elapsed duration for directly observed unstarted tasks
  and propagates predecessor delays. With partial observations a separate what-if
  allows the entire baseline duration from the report date/predecessor finish;
  unresolved predecessors are explicitly assumed ready. It is labelled as a conditional scenario,
  not a measured production rate. No work-calendar/resource-leveling claim is made.

## Run locally

Use Python3.12 with the pinned `requirements.lock` in a virtual environment. Native
IfcOpenShell, PyCOLMAP and OpenCV run outside the web request process. No CUDA is
required. Set server-owned environment variables (never put tokens in Git):

- `PROGRESS4D_OPENMVS_BIN`: directory containing official OpenMVS2.4.0 CPU binaries.
  Required for the textured surface path; keep outside the checkout.
- `PROGRESS4D_EVIDENCE_VERSION`: set to `2` for high-resolution independent cards,
  frontal-view ranking and separate installation/material evidence.
- `PROGRESS4D_DATA`: durable data directory outside the code checkout.
- `PROGRESS4D_SERVICE_TOKEN`: dedicated server-to-server bearer token.
- `PROGRESS4D_META_API_KEY`: existing Meta Model API key for image interpretation.
  If absent, geometry can run but product states remain unknown.
- `PROGRESS4D_VIEW_TTL_SECONDS`: project read-link lifetime,900 by default;60–7200.
- `PROGRESS4D_LOCAL_DEMO_PROJECT`: optional single project UUID for token-free
  loopback reads. Leave unset on hosted services. When enabled locally, start
  Uvicorn with `--no-proxy-headers`; write routes still require the service token.
- `PROGRESS4D_CONCURRENCY`:1 by default;2 for a sufficiently provisioned local machine.
- `PROGRESS4D_RECONSTRUCTION_TIMEOUT_SECONDS`:3600 by default,60–7200 allowed.
- `PROGRESS4D_RECONSTRUCTION_CACHE`: optional trusted cache of exact ordered image
  inputs. Reused reconstruction is marked; model judgments are not cached.

From the repository root:

```sh
python -m uvicorn progress4d_service.app:app --host 127.0.0.1 --port 8791 --workers 1
```

Use one API process per data directory. A process lock prevents competing owners.
The SQLite queue survives restarts; shutdown stops this service's child processes,
and interrupted jobs resume as new attempts without deleting prior output.

The vision adapter uses the existing [Meta image-input API](https://dev.meta.ai/docs/image-understanding).
No new model service is introduced. Calls have bounded timeouts, validation and one
retry; invalid/missing outputs stay uncertain. Claims contradicted by reconstructed
foreground or a directly observed covering product are downgraded to occluded.

## API contract

`GET /contracts` returns the current schema and capture requirements. All mutation
routes require the dedicated service token; project read grants cannot mutate data.

| Method and path | Result |
| --- | --- |
| `PUT /v1/projects/{project}/assets/{sha256}` | Raw content-addressed upload, maximum25MiB |
| `POST /v1/projects/{project}/jobs` | Validated immutable survey request,202 |
| `GET /v1/projects/{project}/jobs/{job}` | Durable status, current stage and result |
| `GET /v1/projects/{project}/timeline` | Chronological surveys, baseline and report |
| `GET /v1/projects/{project}/report` | Downloadable project JSON report |
| `POST /v1/projects/{project}/view-session` | 15-minute project-scoped read link |
| `GET /v1/projects/{project}/jobs/{job}/artifacts/{name}` | Scoped cloud/evidence/report |

IFC baselines and surveys cannot be silently rewritten. Repeating an identical
request ID is idempotent; conflicting bodies, duplicate timestamps or a second zone
return409. A new baseline uses a new project ID. Input hashes and implementation
hashes provide provenance; synthetic/site/recorded source labels remain in history.

`client.py` is the server-side adapter. `cli.py` uploads a capture manifest using
that same contract; it does not bypass the worker:

```sh
python -m progress4d_service.cli --project PROJECT_UUID submit \
  --ifc project.ifc --manifest week-01/capture-manifest.json
python -m progress4d_service.cli --project PROJECT_UUID status --job JOB_UUID
python -m progress4d_service.cli --project PROJECT_UUID viewer
```

A manifest contains `captured_at`, `zone_id`, `synthetic` (or `source_kind`) and an
ordered `photos` array of `{ "file": "photo-000.jpg", "sha256": "..." }`.
The viewer supports date selection/playback, textured surfaces, optional
project boundaries, observed/past/unknown counts, activity, forecasts, evidence
cards and report download. Project boundaries are labelled design geometry.

This is a separately hosted computation service. Inspector can embed the same
viewer through its authenticated, same-origin read bridge; see
[Inspector integration](../ai_inspector/docs/progress4d.md). Hosting and data transfer
are separate operational steps; checking in the adapter does not deploy the service.

An explicitly attached `synthetic-scene.glb` may be used for demo visualization
when photographic reconstruction is inconclusive. Its result metadata must retain
`visualization.kind=synthetic_scene` and `analysis_evidence=false`; it does not
change job status or establish completion of work.

## Evaluation boundary

Synthetic photographs come from a consistent Blender scene based on the supplied
room IFC: six normal weeks plus delayed, rework and insufficient-coverage cases.
Ground-truth geometry, camera poses and visibility labels live in evaluator-only
folders outside the service. Runtime registration uses only reconstructed image
tracks and permanent IFC geometry. Evaluator transforms never enter inference.

Numerical validation covers registration, point accuracy, product-state precision,
visibility, false completion and negative cases. Report synthetic results and
abstentions separately; passing these checks does not establish real-site accuracy.

## Native surface reconstruction

The local adapter invokes unmodified OpenMVS2.4.0 executables as subprocesses:
COLMAP undistortion, depth maps, multi-view fusion (three consistent views), mesh
reconstruction and photographic texturing. IFC contributes only the final metric
registration, never replacement surfaces. Hole closing and artificial tower points
are disabled. Global/local seam leveling are disabled to avoid the macOS2.4.0
texture corruption documented in [upstream issue1251](https://github.com/cdcseacave/openMVS/issues/1251).

Local tested package: [official macOS arm64 release](https://github.com/cdcseacave/openMVS/releases/tag/v2.4.0),
`OpenMVS_macOS_arm64.zip`, SHA256
`3d4c616c97031b1ab6e2eecb0ddd5614fb99513c0782a32c9350602faf38799b`.
OpenMVS uses [AGPLv3](https://github.com/cdcseacave/openMVS/blob/v2.4.0/LICENSE);
its binaries are not vendored in this service. The viewer bundle uses Three0.186.1
(MIT, included notice); rebuild with `node progress4d_service/web/build-surface.mjs`
using the existing viewer dependencies.

Capture should include overlapping frontal passes along the work wall, downward
floor views and frontal doorway views, in addition to the room overview. Added
views improve observable coverage but cannot establish hidden construction.
The development evaluation thresholds and results live outside the checkout in
`local-checks/inspector/progress4d/v2/`; this is not real-site validation.
