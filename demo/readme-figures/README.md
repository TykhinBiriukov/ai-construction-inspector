# README figure sources

These figures present existing published evidence and the implemented Drawing/Photo workflow. Rendering makes no model calls and does not rerun an experiment. [sources.json](sources.json) records the input hashes and full frame, object, IFC and review identities.

| Figure | Source and interpretation |
|---|---|
| `capture-to-review.png` | One real-room door candidate, `mesh_door_002`: original RGB view, its SAM mask, the archived scan/IFC overlay and a summary of its actual downstream evidence requests. The displayed RGB view is part of an RGB-D observation; depth is not pictured. Cyan is IFC, orange is the observed contour. |
| `geometry-responsibilities.png` | Same frame and mask; the 3D drawing plots `observed_corners_ifc_m` and `ifc_corners_m` from `reports/opening_comparison/findings.json`. Equal spatial scale is preserved. Dimensions are intrinsic contour lengths; partial boundary support remains explicit. |
| `drawing-photo-workflow.png` | Architecture diagram based on [staged.py](../../drawing_photo_service/staged.py): drawing-only checklist extraction, photo assessment against fixed requirements and a fresh review of both originals and the draft. This is the staged path, not the legacy shared-context path. |
| `real-room-results.png` | All five rows with surface statistics in `reports/scan_vs_bim/objects.json`. Bars show P95 absolute deviation after alignment, in millimetres. The laser-derived IFC provenance is team-confirmed in the [case manifest](../../docs/evidence/2026-11-03/geometry-case-manifest.json). These are observed-surface deviations, not whole-element certification or a scanner-accuracy estimate. |
| `controlled-results.png` | The complete 18-case wall experiment in the [controlled benchmark summary](../../docs/evidence/2026-11-03/controlled-benchmark/summary.json): nearest-surface versus camera-side selection, with known signed displacement. Both methods return values for all 18 cases. Numerical zero is displayed as `<0.001 mm`. The [full study](../../docs/evidence/2026-11-03/controlled-comparison.md) includes these wall cases and a separate 26-case camera-pose experiment. |

The linked completed review is the archived 2 November result. Its door-case status is `needs_more_data`; the four-panel figure presents the concrete next action from that record. It is separate from the controlled Inspector–Verifier validation described in the root README.

## Re-render

Use Python 3.12 and the [pinned plotting dependencies](requirements.txt) in a disposable environment. Download and verify `geometry-real-room-v1.zip` using the [data access guide](../../docs/evidence/2026-11-03/data-access.md), then run from the repository root:

```sh
python -m pip install -r demo/readme-figures/requirements.txt
python demo/readme-figures/render.py --geometry-dir /path/to/geometry-real-room-v1
```

The script reads the extracted archive and checked-in JSON, checks object/review bindings and 3D lengths, and writes five PNG figures plus their source manifest here. RGB and mask are rotated together for upright display; the existing overlay is used as published. No source pixels, masks, geometry or archived outcomes are inferred or retouched.
