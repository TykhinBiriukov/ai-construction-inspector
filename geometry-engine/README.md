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
  pyproject.toml              # Dependencies and package metadata
  geometry_engine/
    pipeline.py              # Stage orchestration
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
  archive/                   # Inactive source snapshots; excluded from Git
  data/
    raw/<session_id>/original/   # Original capture files
    processed/<session_id>/      # Derived stage outputs
    references/                 # IFC design files
```

Original captures and generated results are local artifacts, excluded from Git. A stage stores its own output rather than duplicating the entire session. Reusable package metadata in `geometry_engine.egg-info` is generated during installation.

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

The current HTTP process endpoint runs steps 2-9 with `measure_openings=true`. Comparison and the combined viewer are separate operations; they are not automatically appended to `PipelineService` yet. Without `measure_openings`, the process endpoint runs the shorter segmentation path and omits projection alignment and contour extraction.

The old VLM boundary-selection, numbered image-line, manual-window, wall-plane experiment, photo-texturing and camera-refinement branches are archived. They are not imported by the current processing modules.

## Start the service

```powershell
python main.py
```

The service listens on `http://127.0.0.1:8000`. Open `http://127.0.0.1:8000/docs` for Swagger.

## Inspection submission API

These two endpoints are the initial durable admission layer. They do not download captures, run SAM or execute the pipeline. A future worker will consume the saved `queued` jobs. The existing `/sessions/*` endpoints remain available for manually running the processing stages.

### Submit a job

```http
POST /inspections
Content-Type: application/json
Idempotency-Key: demo-inspection-001
```

```json
{
  "session_id": "FULL_SESSION_ID",
  "capture_manifest_path": "standalone/demo-building/iphone-1/FULL_SESSION_ID/manifests/HASH/capture-manifest.json",
  "ifc_object_key": "references/room.ifc",
  "parameters": {
    "tolerance_m": 0.1,
    "include_boundary_candidates": false,
    "interior_point_ifc_m": null
  }
}
```

Inputs are relative storage object keys, not download URLs or uploaded files. Supply versioned/immutable keys for reproducible input selection. File existence and content hashes will be verified during download, not by this initial request handler. The IFC hash remains null until the file is fetched and verified.

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

Returns `200` with the committed job state, or `404` for an unknown UUID (`422` for a malformed UUID). Responses disable caching. No worker is implemented yet, so newly submitted jobs remain queued.

The `inspection_jobs` table stores input references, request hash, idempotency key, pipeline version, normalized parameters, status, current stage, timestamps and future result/error references. Large capture files and meshes are not stored in PostgreSQL. Keys and request hashes are internal and are not included in HTTP responses.

### Download a capture

`POST /sessions/extract` supports a session/project request:

```json
{
  "session_id": "FULL_SESSION_ID",
  "project_id": "PROJECT_ID"
}
```

For an explicit storage manifest, use:

```json
{
  "session_id": "FULL_SESSION_ID",
  "manifest_path": "standalone/PROJECT/DEVICE/FULL_SESSION_ID/manifests/HASH/capture-manifest.json"
}
```

Manifest references, file lengths and SHA-256 checksums are verified. The endpoint downloads and extracts capture data; it does not run reconstruction.

### Process a downloaded capture

`POST /sessions/process`:

```json
{
  "session_id": "FULL_SESSION_ID",
  "measure_openings": true
}
```

This is synchronous. The response arrives after processing finishes. Inspect `data/processed/<session_id>/pipeline.json` for status, current stage, completed stages and failure type. A client timeout does not establish whether the server stopped processing.

Existing results are protected from accidental overwrites. A repeated run returns 409. Full-pipeline automatic resume is not implemented; individual stage commands can reuse supported saved results.

### Compare with IFC

Place the design in `data/references/room.ifc`, then call `POST /sessions/compare-bim`:

```json
{
  "session_id": "FULL_SESSION_ID",
  "ifc_file": "room.ifc",
  "compare_openings": true,
  "use_opening_boundaries": true
}
```

`use_opening_boundaries` reads the local `mesh_boundaries` stage; the name does not refer to the archived VLM implementation. `measure_openings=true` on this endpoint runs that local stage before comparison. `resume_boundaries` is valid only with `measure_openings=true`.

The HTTP comparison uses accepted contour results. To inspect incomplete contour candidates explicitly, use the comparison CLI flag below. They remain unverified and must not be presented as confirmed measurements.

## Run stages separately

Run commands from the `geometry-engine` directory. Define:

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

The tests use synthetic geometry and mocked remote services. They do not send capture photos to Meta or download sessions. Existing optional-dependency tests may be skipped if their dependency is unavailable. `tests/check_comparison_runtime.cjs` checks viewer JavaScript against a mock DOM/WebGL interface; it is not a browser rendering test.

PostgreSQL integration tests are opt-in via `TEST_DATABASE_URL`. They create and drop a uniquely named test schema, leaving application tables unchanged. They verify actual migrations, committed persistence, normalized idempotency, conflicts and concurrent submission. Use a local development database with schema-creation privileges. Without this variable, those tests are skipped.

## Third-party code and archived experiments

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for upstream references and license notices. These notices remain applicable to archived code when that code is redistributed.

The ZIP snapshots in `archive/` preserve retired source and integration points. They are not part of the installed package or the active tests. Raw captures, credentials and processed session data are not included in those source archives.
