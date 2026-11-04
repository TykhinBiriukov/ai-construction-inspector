# Run the prepared Geometry demo

Start here to evaluate the repository. This walkthrough supplies a complete, versioned real-room RGB-D capture and its reference IFC. It checks input identities, runs the selected processing path, writes a report and opens the saved result again. The hosted web demo is a separate installation and is not modified by these commands.

## Quick start (Python 3.11+; no key or pip installation)

Run from the repository root on Linux, macOS or Windows:

```sh
python3 demo/run.py --mode prepared
python3 demo/run.py --verify-output demo-output
```

On Windows use `python` instead of `python3`. The first command downloads the pinned 135 MB archive automatically; allow about 600 MB of free disk for the archive, extraction and outputs. Network access to GitHub Releases is required only for the first download. Repeated runs verify and reuse `.demo-cache`. To run again, choose a new output, for example `--output demo-output-second`; existing results are never overwritten.

Success means exit code **0**, a final JSON line with `status: completed`, and these files:

- `demo-output/index.html`: readable report with stage origins, source photo and numeric comparisons.
- `demo-output/geometry-viewer.html`: self-contained interactive retained scan/IFC view.
- `demo-output/report.json`: machine-readable provenance and completed stages.
- `demo-output/reference-verification.json`: four freshly recomputed IFC/contour dimension checks.
- `demo-output/recorded-review.json`: source-bound historical Inspector/Verifier review.
- `demo-output/output-manifest.json`: hashes used when reopening the output.

Open `index.html` in a browser, or serve it locally:

```sh
python3 -m http.server 8088 --bind 127.0.0.1 --directory demo-output
```

Visit `http://127.0.0.1:8088/`. Stop the optional server with Ctrl+C. The CLI itself terminates; browser access is not needed to verify completion. On a remote VM, use an SSH tunnel to this loopback port, or download the output directory. No public port or cloud deployment is required.

## What each mode executes

| Step | `prepared` | `full` |
|---|---|---|
| Read/validate capture files, hashes, IFC | Now | Now |
| RGB-D reconstruction, SAM, alignment, comparison | Verified retained computation | New Geometry stage execution and live SAM calls |
| Reference-dimension consistency check | Recomputed from retained contours and IFC | New geometric comparison |
| Inspector / Verifier review | Historical completed 11-batch review, source-bound | Not run by this local Geometry CLI; optional full service setup below |
| Muse Spark text summary | Optional `--live-summary` | New call, included |
| Report, viewer, save and reopen | Now | Now |

Prepared mode is a reproducible workflow with explicitly retained stages, not a fresh reconstruction or fresh model inspection. It pins the historical implementation fingerprint, effective parameters, input identities, result manifest, archive and review. The historical runtime Git revision is unavailable; it is not treated as a cache hit for the current implementation. Four successful numerical checks establish consistency, not field accuracy or complete boundary support. The historical review has zero confirmed discrepancy candidates, eight requests for more evidence and three rejected candidates; these are valid review outcomes, not installation failures.

## Add a live Meta summary

Supply a Meta Model API key through `INSPECTOR_META_API_KEY` (also accepts `MODEL_API_KEY` or `META_API_KEY`). Use your environment's secret injection. For an interactive Bash session, input without echoing:

```sh
read -rs -p 'Meta API key: ' INSPECTOR_META_API_KEY; echo
export INSPECTOR_META_API_KEY
python3 demo/run.py --mode prepared --live-summary --output demo-output-live
python3 demo/run.py --verify-output demo-output-live
```

If the environment already contains the key, skip the `read` and `export` lines. The key is never needed by prepared mode without `--live-summary`. A failed provider call fails the run; it does not silently substitute a recorded answer. The new `live-summary.json` identifies the model, response and input hashes. This summary is text-only interpretation of supplied evidence, not the specialist visual Inspector/Verifier workflow.

## Full recomputation on Linux (Python 3.12 recommended)

Full mode uses the same Geometry stage adapter as the production worker, in an isolated local directory. It does not need PostgreSQL, Supabase, a phone, an operator token or the hosted backend. It reads the existing capture, reconstructs it, selects frames, calls SAM 3.1, projects masks, compares with IFC and exports the interactive viewer. Muse Spark then summarizes the new numerical evidence. It never restores a retained reconstruction, segmentation or inspection verdict.

Use **at least 4 GB available RAM and 6 GB disk** for the native installation and run. CPU is sufficient; CUDA is not required. Processing time depends on capture size, selected views and provider response times. Start with prepared mode for quick evaluation; full mode prints stage and frame progress. Meta usage is charged to the supplied account. For the included case, the frame selector controls the number of SAM requests (six concept prompts per selected frame).

On Debian/Ubuntu, install the native runtime libraries. In a root container run:

```sh
apt-get update
apt-get install -y --no-install-recommends libgomp1 libgl1 libegl1 libglib2.0-0 libusb-1.0-0 libidn2-0 libssl3 libcurl4 libjson-c5 python3-venv
```

On a non-root VM, prefix both commands with `sudo`. Then, from the repository root:

```sh
python3 -m venv /tmp/inspector-demo-env
/tmp/inspector-demo-env/bin/python -m pip install './geometry-engine[scan-vs-bim,segmentation]'
/tmp/inspector-demo-env/bin/python demo/run.py --mode full --output demo-output-full
/tmp/inspector-demo-env/bin/python demo/run.py --verify-output demo-output-full
```

The Meta key must already be in the environment, as above. Both prepared and full can run in the same checkout and share the verified download cache. Full output includes `geometry/` with every stage report and `geometry/model_comparison/viewer.html`. Inspect `report.json`: all Geometry stages must be present and completed, segmentation must be `live_meta`, registration must be accepted, and the final run status must be `completed`. Numerical findings can differ from the historical run because provider outputs can change. Missing data or rejected registration are reported as a failed stage, not success.

A failed run retains its logs/artifacts for diagnosis. Retry an incomplete computation in a new output directory; full mode reruns its stages rather than resuming a partial computation. Do not remove or rewrite the source capture to make a failed stage pass.

### Finish an already completed computation

An earlier version could finish all Geometry stages and the live Meta summary, then stop with `Persisted report differs on readback`: Python tuple settings were compared directly with JSON arrays. This version fixes that comparison. If you already have that specific failed output, finish it without repeating model calls:

```sh
python3 demo/run.py --finalize-output demo-output-full
python3 demo/run.py --verify-output demo-output-full
```

This recovery requires all computation stages to be completed, matching capture/IFC identities, retained stage artifacts, accepted registration and a consistent model summary. It rejects partially computed runs. It preserves `pre-finalization-report.json`, creates the report/index/output manifest through the same finalization code as a new run, and records `finalization_recovery` with zero new model calls. The original computation time stays in `total_seconds`; recovery is not a fresh recomputation. Python 3.11+ is sufficient for this step; no native packages or key are needed. Success and browser/reopening checks are the same as above.

### Docker alternative

Build from the repository root. These images do not include credentials or download data during the build:

```sh
docker build --target prepared -f demo/Dockerfile -t inspector-demo:prepared .
docker run --rm -v "$PWD/.demo-cache:/cache" -v "$PWD/demo-output-docker:/out" inspector-demo:prepared --cache /cache --output /out/run
```

For full recomputation:

```sh
docker build --target full -f demo/Dockerfile -t inspector-demo:full .
docker run --rm --cpus=1 --memory=4g -e INSPECTOR_META_API_KEY -v "$PWD/.demo-cache:/cache" -v "$PWD/demo-output-full-docker:/out" inspector-demo:full --mode full --cache /cache --output /out/run
```

Docker commands above use Bash syntax. Forward `MODEL_API_KEY` instead if that is the variable you configured. Host output is under the mounted directory's `run/` child. The additional child is intentional: the CLI requires a new output directory.

## Bring your own capture

Use full mode with both `--capture /path/to/capture` and `--ifc /path/to/reference.ifc`. The capture directory must contain `capture-manifest.json` (`standalone_capture_v1`) and `observations/<observation_id>/` with the original `frame.json`, `rgb.jpg`, `depth.f32` and any declared `confidence.u8`. Preserve file sizes/hashes, IDs, camera matrices, depth units in metres, and one ARKit coordinate segment. The IFC must describe supported room geometry in the same workflow scope. An arbitrary JPEG is not a calibrated RGB-D capture.

```sh
/tmp/inspector-demo-env/bin/python demo/run.py --mode full --capture /data/capture --ifc /data/reference.ifc --output demo-output-own
```

Your source files stay unchanged. All derived outputs go into the new output directory. See [Geometry input contracts](../geometry-engine/README.md) and [full service setup](../docs/reproducible-setup.md) for phone capture, persistent services and specialist A2A review. Running those services is optional and separate from this self-contained Geometry walkthrough.

## Offline package / troubleshooting

To avoid a download during execution, download [the pinned release ZIP](https://github.com/TykhinBiriukov/ai-construction-inspector/releases/download/evidence-2026-11-03/geometry-real-room-v1.zip) in advance and add `--archive /path/to/geometry-real-room-v1.zip`. The runner verifies its size and SHA256 against the [committed manifest](../docs/evidence/2026-11-03/geometry-case-manifest.json). Do not extract it manually or set `INSPECTOR_SAVED_DEMO_DIR`; this CLI configures its own data paths.

- Download/network error: check GitHub access or use `--archive`. No cloud storage credentials are needed.
- Changed extracted package: use a fresh `--cache` directory or restore the original package. Hash checks are never skipped.
- Missing Meta key: configure the environment or use prepared mode without `--live-summary`.
- HTTP 401/403/429: check Meta access/quota. The error is retained; there is no recorded fallback.
- Output exists: use a new `--output`, or `--verify-output` to reopen it.
- Native import failure: use the Linux dependencies above or the full Docker image.

The original web demo, production deployment settings and private datasets are not modified by this workflow.
