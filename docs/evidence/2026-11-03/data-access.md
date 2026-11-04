# Download evidence and reproduce results

The repository and [evidence release](https://github.com/TykhinBiriukov/ai-construction-inspector/releases/tag/evidence-2026-11-03) are public. Reviewers can download the files without a GitHub account, Supabase access or service credentials. Anonymous HTTP access to both assets was verified on 3 November 2026; the experiment archive was downloaded in full and its SHA256 checked.

Git contains the study, protocols, reproducible scripts, figures, compact summaries and package manifests. The Release contains source data and full detailed records. These are the same measurements previously committed to Git: moving them did not change or rerun an experiment. Older commits still retain their historical files.

| Download | Contents | Integrity manifest |
|---|---|---|
| [Real-room capture and Geometry outputs — 135 MB](https://github.com/TykhinBiriukov/ai-construction-inspector/releases/download/evidence-2026-11-03/geometry-real-room-v1.zip) | 99 RGB-D observations, IFC, point cloud, meshes, stage manifests and demo evidence | [Geometry package manifest](geometry-case-manifest.json) |
| [Detailed experiment records — 348 KB](https://github.com/TykhinBiriukov/ai-construction-inspector/releases/download/evidence-2026-11-03/experiment-records-v1.zip) | 26 original JSON files: all controlled cases, per-frame diagnostics, complete drawing/photo runs and numerical Geometry reports | [Experiment package manifest](experiment-data-manifest.json) |

Both archives include `SHA256SUMS`. Compare the downloaded ZIP's SHA256 with the appropriate manifest, extract it, and run `sha256sum -c SHA256SUMS` from its extracted root on Linux, or `shasum -a 256 -c SHA256SUMS` on macOS. The SHA256 for `experiment-records-v1.zip` is `f7f02aa4630a84953a9436097c48857792f9d1fa34f99354d444c39e9b3da3cf`.

## Experiment records

Paths below are relative to the extracted `experiment-records-v1/` directory and preserve the original repository layout.

| File | What to inspect |
|---|---|
| `controlled-benchmark/results.json` | All 26 camera-pose cases and 18 wall-displacement cases, ground-truth/input/output transforms, errors and execution identities |
| `controlled-benchmark/summary.json` | Original per-stratum aggregation; also available as a [compact summary in Git](controlled-benchmark/summary.json) |
| `workflow-comparison/cameras.json` | All 77 original projection diagnostic rows, including rejected frames |
| `workflow-comparison/metrics.json` | Full computed metrics and per-frame outcomes; [compact projection summary in Git](workflow-comparison/summary.json) |
| `workflow-comparison/history.json` | Retained job/stage history |
| `workflow-comparison/sam-first.json`, `sam-final.json`, `sam-artifact-comparison.json` | Model/cache provenance and complete artifact comparisons; not a model-quality benchmark |

## Geometry reports

The experiment archive includes `geometry-result.json`, `geometry-run-history.json` and the original `geometry-reports/` directory. For example, `geometry-reports/reconstruction.json` contains the reconstruction counts and frame details; `segmentation-first-run.json` and `segmentation.json` retain model provenance; `mesh_segmentation.json` contains label coverage; `scan_vs_bim.json` contains registration and surface comparisons. All original report filenames are preserved.

The larger real-room archive contains the same primary report evidence plus actual capture/model bytes. See the [case guide](README.md#inspect-and-reproduce) for the chain from frame IDs to RGB/depth, masks, meshes and IFC.

## Drawing and progress

The experiment archive contains `drawing-workflow-comparison.json` (full same-input legacy/staged runs), `drawing-frozen-checklist.json` (three complete checklist/replacement analyses), and `progress4d-evaluation.json` (six dated evaluation records). The compact [Progress4D evaluation](progress4d-evaluation.json) remains in Git. Original private drawing/photo source images and the full Progress4D surveys are not added by this packaging change.

## Reproduce the projection summary

From a clone, download the small experiment archive into a disposable directory, then place the unchanged script and protocol beside its input records:

```sh
repo_root="$PWD"
evidence_dir=$(mktemp -d)
curl -fL -o "$evidence_dir/experiment-records-v1.zip" \
  https://github.com/TykhinBiriukov/ai-construction-inspector/releases/download/evidence-2026-11-03/experiment-records-v1.zip
# Verify the archive SHA256 against experiment-data-manifest.json before extraction.
unzip -q "$evidence_dir/experiment-records-v1.zip" -d "$evidence_dir"
cp "$repo_root/docs/evidence/2026-11-03/workflow-comparison/analyze.py" \
   "$repo_root/docs/evidence/2026-11-03/workflow-comparison/protocol.txt" \
   "$evidence_dir/experiment-records-v1/workflow-comparison/"
cd "$evidence_dir/experiment-records-v1/workflow-comparison"
cp metrics.json metrics.archived.json
python3 analyze.py
```

Only the standard library is required. This recomputes aggregation from retained diagnostics, not Geometry or Meta inference. Platform metadata can differ; compare the numerical fields and input identities with `metrics.archived.json`.

## Reproduce the figures

Keep using the extracted records from the preceding section. Copy `controlled-benchmark/plot_results.py` from Git into the extracted `controlled-benchmark/` directory. In a disposable Python environment with the [pinned plotting dependencies](controlled-benchmark/plot-requirements.txt), mount that directory at `/work` and the extracted `workflow-comparison/` directory at `/historical`, then run `python /work/plot_results.py`. It reads `results.json` and `/historical/metrics.json`, and writes the figures and summary under `/work`.

To rerun the numerical algorithms rather than just plot saved results, follow the [controlled comparison execution record](controlled-comparison.md#reproduction-and-execution-record): copy `benchmark.py` and `protocol.txt` from Git into a separate disposable directory mounted at `/work`, using the pinned Geometry source/dependencies. The runner generates its controlled geometry from the recorded seeds. No new provider calls or credentials are needed for these numerical fixtures. Keep archived outputs unchanged when comparing a rerun.
