# Geometry workflow evolution and measured tradeoffs

This study reconstructs the team's implementation history and analyzes retained evidence from the real-room capture `2b6b4560-c2d5-482d-880b-21f34502b9f2`. It separates source-code evolution, a paired projection diagnostic comparison, and the numerical choices behind the current pipeline. It does not claim that every later algorithm is more accurate than every earlier one.

## 1. What we tried, and what remains in Git

| Date | Approach and evidence | Development of the workflow |
|---|---|---|
| 27 Oct | [Frame selection and Meta SAM 3.1](https://github.com/TykhinBiriukov/ai-construction-inspector/commit/8ba7f838e7776fae71f49ca8e22771f3c35727ac) | Select distinct views; retain masks, overlays and reports; resume completed frames. Semantic interpretation becomes an explicit persisted stage. |
| 27 Oct | [Mask projection and multiview fusion](https://github.com/TykhinBiriukov/ai-construction-inspector/commit/785f88c2149207b1bcc23a54f621c1e13a51a10d) | Transfer SAM observations onto visible, depth-supported mesh faces and combine views. A per-image mask is no longer treated as a physical object by itself. |
| 28 Oct | [RANSAC wall-plane experiments](https://github.com/TykhinBiriukov/ai-construction-inspector/commit/047511194a4693911f72b4e7970d59fae859727a) | Fit candidate planes on SAM wall-labelled geometry, using area sampling, normal filtering and original-face support. The implementation is retained in Git history. |
| 29 Oct | [Raw RGB-D TSDF reconstruction and plane grouping](https://github.com/TykhinBiriukov/ai-construction-inspector/commit/53d7fabca37a441de3e19da71226533958de0c38) | Reconstruct from recorded depth and camera poses; add plane merging, capture manifests, previews and a complete processing path. |
| 31 Oct | [Projection correction, local mesh contours and IFC comparison](https://github.com/TykhinBiriukov/ai-construction-inspector/commit/891763079e6c9c4a0e1dc490e0fcf2c0edeb5809) | Preserve the reconstructed mesh, refine only projection cameras when supported, reject unsuitable views, derive local opening candidates and compare against a pinned IFC. Retire the standalone wall-plane experimental modules. |
| 1 Nov | [Durable workers and reusable SAM inference](https://github.com/TykhinBiriukov/ai-construction-inspector/commit/ceec86e6b934940202d05f8cb9520fc8d6cdd0b3) | Retain stage checkpoints, identity-bound SAM results, retries and compact evidence exports. Subsequent comparison changes can reuse expensive semantic computation. |

These are changes to different layers, not six competing algorithms for one identical task. In particular, TSDF reconstruction, semantic segmentation, plane fitting and opening comparison solve different problems. The current system still uses plane/normal and registration machinery internally; retiring the old wall-plane exploration does not mean abandoning all plane-based geometry.

The [method notices](../../../geometry-engine/THIRD_PARTY_NOTICES.md) also mention ICP/pose-graph camera refinement and archived experiments; the Geometry README mentions VLM boundary selection, numbered image lines, manual windows and photo texturing. Their named ZIP snapshots are absent from this checkout. We preserve these as documented historical directions, not recovered executable baselines or measured failures. The inspected Git history does preserve the RANSAC and early TSDF implementations above.

No common-input independent labels or retained benchmark results for old RANSAC planes versus current opening contours were found in the evidence inspected for this study. We therefore do not assign an accuracy or speed advantage to that change.

## 2. Paired projection comparison on the real capture

### Question and baseline

Does the retained projection-correction/gating stage improve depth-to-mesh consistency compared with using the original camera poses for those same observations?

The [earlier mask-projection implementation](https://github.com/TykhinBiriukov/ai-construction-inspector/blob/53d7fabca37a441de3e19da71226533958de0c38/geometry-engine/geometry_engine/mask_projection.py) reads the original camera transform. The [later stage](../../../geometry-engine/geometry_engine/segmentation/projection_alignment.py) evaluates a bounded correction against the fixed reconstructed mesh, retaining the original pose if adequate and rejecting a view if neither choice is supported. The saved `before` branch represents the original-pose choice. This analysis **does not execute the full historical checkout**: other historical preprocessing and thresholds may differ.

### Protocol and execution

The [protocol](workflow-comparison/protocol.txt) was fixed before aggregation. A standalone [analysis script](workflow-comparison/analyze.py) processed all 77 retained diagnostic rows on the existing GCP `e2-standard-2` VM, in an isolated temporary directory with Linux x86-64 / Python 3.11.2. It used only Python's standard library, made no model calls and did not change services, source geometry or databases. Heavy processing was not run on the local Mac.

Inputs and script hashes are pinned in [metrics.json](workflow-comparison/metrics.json). The [77 complete diagnostic rows](workflow-comparison/cameras.json), rejected cases and all per-frame outcomes are included. To recompute the aggregation in any disposable environment:

```sh
cd docs/evidence/2026-11-03/workflow-comparison
python3 analyze.py
```

The command writes `metrics.json` in that directory; retain the checked-in result if comparing executions. Platform/Python fields may differ on another host. No provider key is needed.

Each metric uses reserved validation pixels (`held_out` in the source record), rather than the pixels used to fit that correction. However, the same capture contributed to the fused mesh: these are **not independent ground-truth observations or held-out scenes**. Values are internal consistency diagnostics, not physical measurement error. Frames are weighted equally; a mean of frame-level P90 values is not a pooled P90 over all points.

### Results

The current policy retains 50 original poses, accepts 18 refined poses and rejects 9 views: **68/77 retained (88.3%)**. The principal comparison below uses exactly the same 68 retained frames on both sides, so its numerical change is not obtained by comparing all 77 old views with only the easier 68 new views.

| Metric on the same 68 retained frames | Original poses | Selected poses | Change |
|---|---:|---:|---:|
| Mean of per-frame median absolute depth residual | 10.99 mm | 7.93 mm | 27.8% lower |
| Mean of per-frame P90 absolute depth residual | 35.61 mm | 29.98 mm | 15.8% lower |
| Mean pixel agreement fraction | 96.63% | 97.61% | +0.98 percentage points |

Agreement uses the implementation's depth-dependent threshold `abs(rendered_depth - observed_depth) < 0.05 + 0.01 * observed_depth`, in metres. It is not an accuracy probability or BIM acceptance rate.

| Cohort | Frames | Mean frame-median residual before | After correction / selected output |
|---|---:|---:|---:|
| All raw observations | 77 | 13.90 mm | No aggregate selected error is assigned to rejected views |
| Accepted refinement subset | 18 | 18.20 mm | 6.64 mm |
| Rejected subset | 9 | 35.85 mm | No accepted output; candidate corrections had 7.64 mm mean residual |

The rejected subset is important: an improved average alone did not make a proposal acceptable. Other checks can reject local regressions, poor coverage, excessive corrections or underconstrained geometry. These nine rows remain visible instead of receiving a zero error or disappearing from the coverage denominator.

**Interpretation:** the retained refinement mechanism improves the recorded projection consistency while preserving an explicit refusal path. Selection gates themselves require improvement, so this conditional result is partly expected by construction; it is not an unbiased estimate of generalization. It does not establish improved SAM mask IoU, object dimensions, inspection accuracy or real-site performance. The 77 correlated frames come from one development capture; no independent-trial confidence interval is claimed.

## 3. Controlled numerical comparisons

The [known-ground-truth study](controlled-comparison.md) adds 26 pose cases and 18 surface-displacement cases, run using the pinned Geometry implementation in an isolated GCP container. It compares original poses, ungated proposals and the current policy, and isolates the effect of camera-side surface selection. All cases, coverage tradeoffs and failures are included with four figures. Historical SAM/cache records remain provenance, not quality evidence.

## 4. Drawing/photo and remaining comparisons

The [drawing/photo comparison](README.md#2-drawingphoto-from-joint-context-to-frozen-requirements) retains the actual shared-history and staged runs, plus the later frozen-checklist/replacement sequence. Staged processing made requirement separation and reuse explicit, but was slower in the single same-input comparison, and its edited-gap example did not obtain the expected mismatch. Those observations remain published alongside the Geometry gains.

For an accuracy comparison of old wall planes and current contours, first define a common measurable target (for example, opening width/height on independently measured objects), preserve the historical versions and obtain independent ground truth with uncertainty. Report absolute error, missing/false detections, abstention, coverage and wall time on the same inputs, including failure cases. More mesh points, fewer unmatched candidates or smaller residual after fitting to IFC are not sufficient accuracy targets. Such a new benchmark is not part of the results above.
