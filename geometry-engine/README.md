# Geometry Engine

Geometry Engine reconstructs a metric mesh from phone RGB-D captures, labels its surfaces with hosted Meta SAM, approximates visible door/window contours and compares the observed geometry with an IFC design. The scan is not reshaped to fit the design.

## Install on Windows

Run these commands from `geometry-engine`:

```powershell
python -m venv myenv
.\myenv\Scripts\Activate.ps1
python -m pip install -e ".[all]"
Copy-Item .env.example .env
```

If the environment already exists, activate it and install the project. Dependencies are defined in `pyproject.toml`. The `server`, `reconstruction`, `segmentation` and `scan-vs-bim` extras can also be installed separately. Hosted SAM needs no local model weights or NVIDIA GPU. Reconstruction and geometric comparison run locally on the CPU.

Set these variables in `.env`:

```dotenv
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_STORAGE_BUCKET=inspection-captures
SUPABASE_SECRET_KEY=YOUR_SERVER_SECRET_KEY
MODEL_API_KEY=YOUR_META_API_KEY
```

`GEOMETRY_DATA_DIR` optionally overrides the default `data` directory. Keep real credentials out of Git. Storage credentials are needed for downloads; `MODEL_API_KEY` is needed for SAM requests. Comparing existing stage results does not require model inference.

## Local PostgreSQL

Install and start [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/) with Linux containers. From `geometry-engine`, use the PostgreSQL settings in `.env` and run:

```powershell
docker compose up -d --wait db
docker compose ps
```

The database listens on `127.0.0.1`, using `POSTGRES_PORT` from `.env` (default `5432`). Database name, username and password are `POSTGRES_DB`, `POSTGRES_USER` and `POSTGRES_PASSWORD` from `.env`. Only these database settings are passed to the container; Supabase and Meta credentials are not passed through.

Check SQL access:

```powershell
docker compose exec db psql -U geometry_engine -d geometry_engine -c "SELECT 1;"
```

This SQL command uses the default database and username; adjust them if you change those settings in `.env`.

View logs or stop the container:

```powershell
docker compose logs --tail 50 db
docker compose stop db
```

Database files persist in the named `postgres_data` volume across container recreation. `docker compose down` removes the container but keeps that volume. Adding `--volumes` would also delete database contents. Credentials initialize a new volume; editing them in `.env` does not change credentials inside an existing database.

The API can run in `myenv` and connect to this database. If port `5432` is occupied by another project, set `POSTGRES_PORT=5433` in `.env` before starting this container. `DATABASE_URL`, when nonempty, overrides the separate connection settings; otherwise `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER` and `POSTGRES_PASSWORD` are used.

Run `python main.py` to apply Alembic migrations and start the local API. It fails before serving requests if migrations fail. `alembic upgrade head` or `python -m geometry_engine_api.database.migrate` can also apply them explicitly. For deployment, run migrations once as a release step, then start API replicas with `python -m uvicorn geometry_engine_api.app:app --host 0.0.0.0 --port 8000`. Importing the app does not connect to the database or run migrations. PostgreSQL advisory transaction locks serialize concurrent migration runners.

Official references: [PostgreSQL image](https://hub.docker.com/_/postgres), [Compose startup](https://docs.docker.com/reference/cli/docker/compose/up/).

## Project structure

```text
geometry-engine/
  main.py                    # Start the HTTP service
  worker.py                  # Execute queued inspections and viewer exports
  pyproject.toml              # Dependencies and package metadata
  geometry_engine/
    pipeline.py              # Stage orchestration
    inspection.py            # Durable worker stage adapters and dependencies
    capture/                 # Raw capture, paths and validation
    reconstruction/          # Point cloud, mesh and preprocessing
    segmentation/            # Frame selection, SAM, mask projection and fusion
    openings/                # Door and window contours
    comparison/              # IFC registration and deviations
    visualization/           # Viewers, reports and HTML templates
    common/                  # Mesh operations and geometric primitives
  geometry_engine_api/       # Thin HTTP endpoints and dependency providers
  scripts/                   # Commands for individual stages
  tests/                     # Active automated tests
  data/
    raw/<session_id>/original/   # Original capture files
    processed/<session_id>/      # Derived stage outputs
    references/                 # IFC design files
    upload/<session_id>/        # Prepared short-layout folders for Storage upload
    work/                      # Temporary isolated worker workspaces
    viewer_cache/              # Downloaded generated viewers; disposable cache
    artifact_cache/            # Verified Storage manifests and bundles
    result_cache/              # Files served by expiring result links
```

CLI captures/results remain local, excluded from Git. The durable worker publishes results to Supabase Storage and removes its temporary workspace afterwards. A stage stores its own output rather than duplicating the entire session. Reusable package metadata in `geometry_engine.egg-info` is generated during installation.

## Processing chain

The current measurement path is:

1. Download the capture and extract it into `data/raw/<session_id>/original`.
2. Reconstruct a point cloud and mesh from RGB-D frames using original ARKit camera poses.
3. Prepare the normalized `input` snapshot and validate its files and calibration.
4. Clean the mesh without creating an idealized room model.
5. Select sharp, distinct frames.
6. Request hosted Meta SAM masks for `wall`, `floor`, `ceiling`, `window`, `window frame` and `door`.
7. Align projection cameras to the fixed mesh. This does not move mesh vertices.
8. Project masks onto visible, depth-supported triangles, fuse observations and associate object fragments.
9. Fit candidate door/window contours from the segmented mesh and its wall interfaces.
10. Register the scan to IFC with a rigid transform and compare observed structural surfaces.
11. Match opening candidates to IFC and calculate dimensions and position differences.
12. Export the comparison viewer with an optional synchronized original RGB mesh panel.

`POST /inspections` queues the complete chain through opening comparison (steps 1-11). A worker executes it. `POST /inspections/{job_id}/viewer` separately queues step 12 from the committed results.

The old VLM boundary-selection, numbered image-line, manual-window, wall-plane experiment, photo-texturing and camera-refinement branches are archived. They are not imported by the current processing modules.

## Start the service

```powershell
python main.py
```

The service listens on `http://127.0.0.1:8000`. Open `http://127.0.0.1:8000/docs` for Swagger.

In a second terminal, from the same directory/environment, start the worker:

```powershell
python worker.py
```

`python worker.py --once` handles at most one ready inspection or viewer export. The API applies migrations through `main.py`; workers assume migrations have already been applied. Deployment can use `python -m geometry_engine_api.worker` after its separate migration step. One worker handles one job at a time; multiple workers use PostgreSQL claims to avoid duplicate ownership. RabbitMQ is not required for this implementation.

## Inspection submission API

Submission persists work and immediately returns `202`; it does not run the pipeline in the HTTP request. The worker consumes queued jobs, downloads their inputs and executes each stage. Individual stage commands remain available for local debugging.

### Submit a job

```http
POST /inspections
Content-Type: application/json
Idempotency-Key: demo-inspection-001
```

```json
{
  "session_id": "FULL_SESSION_ID",
  "capture_manifest_path": "FULL_SESSION_ID/manifests/MANIFEST_HASH/capture-manifest.json",
  "ifc_object_key": "FULL_SESSION_ID/reference.ifc",
  "parameters": {
    "tolerance_m": 0.1,
    "include_boundary_candidates": true,
    "interior_point_ifc_m": null
  }
}
```

Inputs are relative storage object keys, not download URLs or uploaded files. Supply versioned/immutable keys for reproducible input selection. File existence and content hashes will be verified during download, not by this initial request handler. The IFC hash remains null until the file is fetched and verified.

The preferred capture layout is now `<session_id>/...` inside `inspection-captures`. Existing phone-generated `standalone/<project>/<device>/<session_id>/...` manifests remain supported by the downloader. Short-layout observations can use `<session_id>/observations/<observation_id>/<filename>`; file lengths and SHA-256 checks are still enforced when downloaded.

To prepare an existing raw capture for upload:

```powershell
python -m scripts.prepare_capture_upload --session data/raw/FULL_SESSION_ID
```

This creates `data/upload/FULL_SESSION_ID` with rewritten manifest object keys, a newly hashed manifest directory and byte-identical observation files. It verifies every copied file and leaves the original capture unchanged. Upload that session directory to the root of `inspection-captures`, preserving its nested folders. The command prints the exact `capture_manifest_path` for the request. Existing export folders are not overwritten. Use a new idempotency key when changing paths on an inspection request.

The API normalizes parameters, hashes the request and inserts a job in one database transaction. A unique constraint and PostgreSQL `INSERT ... ON CONFLICT` protect against simultaneous duplicate requests. The transaction commits before returning `202 Accepted`, with the job UUID, `status: queued`, timestamps and `status_url`. The `Location` header points to the same status endpoint.

The response includes these fields (along with input references and timestamps):

```json
{
  "job_id": "d7f4eab6-2e8a-41d2-b665-247828b5bac4",
  "session_id": "FULL_SESSION_ID",
  "status": "queued",
  "current_stage": null,
  "status_url": "/inspections/d7f4eab6-2e8a-41d2-b665-247828b5bac4"
}
```

- Same key and normalized request: return the existing job with its current state.
- Same key and different parameters: `409 Conflict`, without modifying the original job.
- A deliberately new run: use a new key.
- Missing/invalid key, malformed storage path or unsupported parameters: `422`.
- Unavailable database: `503` with a retry hint. Retry using the same key.

Idempotency keys are currently globally unique for this single-tenant API. Authentication and tenant scoping are separate future integration work.

### Read job state

```http
GET /inspections/JOB_UUID
```

Returns `200` with the committed job state, or `404` for an unknown UUID (`422` for a malformed UUID). Responses disable caching. `current_stage`, `heartbeat_at`, `stages`, `next_attempt_at` and `error_code` show whether work is running, waiting for retry, completed or failed. Every stage includes its attempt count and artifact manifest reference. `viewer_exports` lists independent export jobs.

The four internal tables are `inspection_jobs`, `inspection_stages`, `viewer_exports` and `segmentation_cache`. Large capture files and meshes are stored in Storage. Migrations enable RLS without browser/client policies on these tables; API/worker connections must use the table-owning server database role or an explicitly configured privileged role. This remains a single-tenant server API: application authentication and per-user authorization must be integrated before exposing it to untrusted users.

### Durable results and recovery

```text
inspection-captures/
  SESSION_ID/
    manifests/HASH/capture-manifest.json
    observations/...
    reference.ifc
    results/JOB_ID/
      download/attempt_001/...
      reconstruction/attempt_001/...
      input/attempt_001/...
      validation/attempt_001/...
      mesh_preprocessing/attempt_001/...
      selected_frames/attempt_001/...
      segmentation/attempt_001/...
      projection_alignment/attempt_001/...
      mesh_segmentation/attempt_001/...
      mesh_boundaries/attempt_001/...
      scan_vs_bim/attempt_001/...
      opening_comparison/attempt_001/...
      viewer/EXPORT_ID/attempt_001/...
      result-manifests/SHA256.json
  cache/sam/v1/REQUEST_HASH/...
```

Stage files are bundled into ZIPs and split into 8 MiB Storage objects. An immutable manifest maps the logical files (for example `reconstruction/mesh.ply`) to their bundle and records SHA-256/size. This avoids uploading every small mask individually and handles meshes larger than a bucket's per-object limit. Original capture assets and identical files from previous stages are referenced, not uploaded again. The IFC is snapshotted during download so later edits to the source object cannot change this job's comparison.

The worker checks uploaded bytes before committing a stage as completed. Database pointers identify the successful attempt; partial uploads and old attempts never become a successful checkpoint. Retry restores and verifies completed stages into a fresh local workspace, reusing already downloaded identical files. A changed source object or corrupt artifact fails integrity validation instead of silently producing a different comparison.

Temporary network failures, rate limits and server errors use up to three stage attempts with exponential delays (10 and 20 seconds by default). Invalid capture data, missing access and rejected geometric alignment are terminal failures. Rejected stage reports and sanitized error diagnostics are uploaded where possible and linked from the failed stage. If Storage itself is unavailable, the database still records the error code. Worker termination is recovered after the lease expires; expired workers cannot commit results. Heartbeats are independent of long geometry/model operations.

Each stage fingerprint includes its effective settings, dependency artifact hashes and geometry implementation/runtime versions. Changed settings/code require a new inspection rather than mixing old and new stages. A new `Idempotency-Key` creates a new job and retains the previous results. Completed SAM inference can still be reused across these jobs when its own request identity matches.

### SAM inference cache

The worker computes keys locally from the exact upright RGB image sent to SAM, the individual prompt, effective model/decoder identity, preprocessing version and `SAM_CACHE_EPOCH`. It checks all selected keys in one database query, prefetches existing masks with bounded concurrency, and then processes local files. It does not query Storage for each pixel or triangle.

Each successful prompt is cached independently, including a verified empty result. A failure halfway through a photograph or session does not discard completed requests. Masks are cached before confidence/area filtering; changing these local filters can reuse inference. Camera positions, IFC and comparison settings do not invalidate unchanged 2D model inputs; their geometry-dependent stages are recomputed. Concurrent requests for the same key use a separate fenced lease.

The first run with a new request identity fills the cache. Subsequent runs reuse it. Old filtered/manual segmentation folders are not silently treated as raw provider responses. Set `SAM_CACHE_EPOCH=2` (or another new value) when intentionally invalidating cached results, for example after a provider changes the model behind the same model name. No original data or saved result is deleted.

### Fetch the agent result

```http
GET /inspections/JOB_UUID/result
```

Returns the compact `schema_version: "2.0"` contract. All lengths are in metres; differences are scan minus IFC. `objects[]` contains the scan object ID, IFC GUID, match status, width/height (`ifc`, `scan`, `difference`), displacement (`along_wall`, `vertical`, `normal_to_wall`), factual quality flags and related `photos[]` (`rgb_url`, `mask_url`, `overlay_url`). Unknown measurements remain null; an unobserved IFC object is not declared absent. Signed displacement uses the matched IFC opening's local horizontal/normal axes; positive vertical is upward.

`surfaces[]` preserves compact wall/floor comparison metrics: compared area, p95 absolute deviation, fraction outside tolerance and whole-element verification flag. `artifacts` contains four download URLs: point cloud, mesh, segmented mesh and IFC. The response omits agent instructions/verdicts, camera metadata, corner coordinates, algorithm parameters, raw reports, duplicate JSON links and per-file technical metadata. Source reports and viewer measurements are unchanged. HTML/viewer generation is not required.

`match_status: "ifc_reference_unavailable"` means the IFC element has no usable reference geometry, so it was not compared. It is counted separately in `summary.unavailable_ifc`, never as a successful match or a missing object in the scan.

Links are signed by this API and expire after one hour; `expires_in=60..86400` optionally changes the lifetime. Files are read from committed Supabase artifacts and served through the API's verified local cache, since stored bundles are not standalone image/mesh URLs. A link is bound to the job, result version, stage and file; an expired or modified link returns 403. Request the result again for fresh links. The JSON response uses `Cache-Control: no-store`.

Unknown jobs return 404. Queued/running/failed jobs return 409 with their status and status URL. Unavailable Storage returns 503; incomplete or corrupt committed artifacts return 409. Retrieval never starts reconstruction, SAM or another comparison. Reports/manifests are cached; large meshes are downloaded only when their links are used, with verified bundles reused between file requests.

Set `GEOMETRY_PUBLIC_BASE_URL` to an externally reachable API URL when the reviewing agent runs elsewhere. Otherwise links use the request's base URL (localhost for local development). `RESULT_URL_SIGNING_SECRET` optionally supplies a separate signing secret; by default a purpose-specific key is derived from the server Storage secret. Neither secret appears in responses. Access control remains the existing single-tenant API policy.

### Generate and open the latest comparison viewer

After the inspection is completed:

```http
POST /inspections/JOB_UUID/viewer
```

No body is required. This returns `202` and an export ID. Poll `GET /inspections/JOB_UUID`; a completed entry under `viewer_exports` has `viewer_url`. Opening it serves HTML and relative report/photo assets through this API. Supabase Storage itself serves HTML as plain text, so its object URL is not the interactive page URL.

The exporter restores saved results and generates the IFC/segmented scan comparison with animated side-by-side/overlay movement, object toggles, saved measurements, photo evidence and the synchronized original RGB mesh panel. It does not rerun SAM, registration or measurement. Matching completed exports are reused. A failed export can be explicitly retried with the same POST and body `{"retry_failed": true}`; it does not reset the completed inspection.

To recalculate opening measurements from an existing completed inspection without rerunning reconstruction or SAM:

```powershell
python -m scripts.recompare_inspection JOB_UUID
```

This creates a separate completed inspection revision and viewer, referencing the original geometry/segmentation artifacts. The source job is preserved. Fitted contour measurements retain quality warnings; verified identical local files are reused before downloading missing inputs. The command prints the new job ID and viewer URL, and keeps the working files under `data/recomparisons/`.

For the latest measurement convention, upload the exported interior-surface `reference.ifc`. If using the original solid IFC, supply a known room interior point in IFC metres via `interior_point_ifc_m` to select its interior surfaces. Fitted door/window contours are compared by default; incomplete wall-interface support remains an explicit quality warning beside the measurements. Impossible or missing contours still have no measurements. `include_boundary_candidates=false` is an optional strict mode which excludes incomplete-interface contours; it is not the normal comparison mode.

## Run stages separately

Run commands from the `geometry-engine` directory. Every session command requires an explicit session path; there is no hard-coded demo capture. Define:

```powershell
$s = "data/processed/FULL_SESSION_ID"
```

### Reconstruction

```powershell
python -m scripts.run_reconstruction --session $s
```

Outputs include `reconstruction/mesh.ply`, the point cloud, camera poses and a stage report. Original camera poses are always used. Old refinement directories are ignored.

### Prepare the input snapshot

After reconstruction, the full pipeline prepares `input` automatically. For a manual stage-by-stage run:

```powershell
python -c "from geometry_engine.pipeline import prepare_input; prepare_input('$s')"
```

### Validation, mesh preprocessing and frame selection

```powershell
python -m scripts.run_geometry --session $s
```

Use `--frames-only` to select frames without repeating validation and mesh cleaning. The command reads the derived `input` snapshot when present.

### Hosted SAM segmentation

```powershell
python -m scripts.run_segmentation --session $s
```

For an incomplete segmentation run, use the same settings and `--resume`. This reuses completed frame results inside that stage; it is not a global content-addressed inference cache. Changing input images or parameters may require a new output directory.

### Projection alignment and mesh segmentation

```powershell
python -m geometry_engine.segmentation.projection_alignment $s
python -m scripts.run_mesh_segmentation --session $s --alignment-dir "$s/projection_alignment" --min-depth-agreement 0.6
```

Both commands operate on the saved mesh and masks. Projection alignment only affects which camera is used to project labels. It does not reconstruct or reshape the scene.

### Mesh opening contours

```powershell
python -m scripts.run_mesh_boundaries $s
```

Outputs: `mesh_boundaries/objects.json`, `report.json` and NPZ evidence. Direct wall/object edges and object rims are tracked separately. A rectangular contour is confirmed only when the required boundaries have sufficient support. Otherwise it remains a candidate.

### Surface and opening comparison

Use a new output directory when results already exist:

```powershell
python -m geometry_engine.comparison.scan_vs_bim $s data/references/room.ifc --segmented-only --output "$s/scan_vs_bim"
python -m geometry_engine.comparison.opening_comparison $s data/references/room.ifc --comparison-dir "$s/scan_vs_bim" --boundaries-dir "$s/mesh_boundaries" --include-boundary-candidates --output "$s/opening_comparison"
```

The registration stage can reject an ambiguous or weak alignment. Do not interpret deviation measurements unless registration is accepted. For a known room interior point in IFC coordinates, pass `--interior-point X Y Z` to the surface comparison command. This selects interior reference surfaces; it does not move the scan.

### Combined viewer

After accepted registration and opening comparison:

```powershell
python -c "from geometry_engine.visualization.model_comparison_viewer import build_model_comparison; print(build_model_comparison('$s', 'data/references/room.ifc', comparison_dir='$s/scan_vs_bim', opening_dir='$s/opening_comparison', raw_panel=True))"
```

Open `model_comparison/viewer.html`. IFC and segmented scan can slide between side-by-side and overlay views. The separate RGB mesh shares camera controls and height clipping. Elements can be hidden individually. The viewer exports the displayed IFC and scan geometry as PLY files.

## Interpret the results

- Gray or unverified surfaces have insufficient comparison evidence. They are not zero-deviation measurements.
- An unlabeled or unmatched object is not automatically missing or extra. Occlusion, fragmented instances and false positives can explain it.
- Door/window width and height come from the candidate contour in its own fitted plane. Position differences use the IFC reference axes.
- With an interior room point, window frame bounds and door-opening references are represented at the interior host-wall surface. Authored width/height remain unchanged. Original IFC geometry is retained as provenance.
- Moving the reference convention to the interior surface does not change captured geometry or prove a measured deviation.
- Candidate rectangles do not prove that all physical edges were observed. Reports and photo evidence retain this limitation.
- The system compares observations with the design; it does not infer hidden construction as verified geometry.

All active UI and report text is English. Historic output directories and archived snapshots are not automatically migrated when source templates change.

## Tests

```powershell
python -m unittest discover -s tests -q
```

The tests use synthetic geometry and mocked remote services. They do not send capture photos to Meta or download sessions. Temporary fixtures use the system temporary directory, not the repository. Existing optional-dependency tests may be skipped if their dependency is unavailable. `tests/check_comparison_runtime.cjs` checks viewer JavaScript against a mock DOM/WebGL interface; it is not a browser rendering test.

PostgreSQL integration tests are opt-in via `TEST_DATABASE_URL`. They create and drop a uniquely named test schema, leaving application tables unchanged. They verify actual migrations, committed persistence, normalized idempotency, conflicts and concurrent submission. Use a local development database with schema-creation privileges. Without this variable, those tests are skipped.

## Third-party code and archived experiments

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for upstream references and license notices. These notices remain applicable to archived code when that code is redistributed.

Retired implementations remain in Git history. Some existing workspaces may also have ignored ZIP snapshots under `archive/`; those snapshots are not included in this checkout, the installed package or the active tests. Raw captures, credentials and processed session data remain outside Git.
