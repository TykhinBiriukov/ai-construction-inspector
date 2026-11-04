"""Render README figures from the published evidence, without new inference.

Usage: python render.py --geometry-dir /path/to/geometry-real-room-v1
"""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--geometry-dir", type=Path, required=True)
args = parser.parse_args()
GEO = args.geometry_dir
INK, MUTED, GREEN, TEAL, GOLD = "#213c33", "#64736b", "#376653", "#158b9b", "#b77b35"
BG, LINE, PALE = "#f8faf7", "#dce5dc", "#eaf0e9"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12,
                     "text.color": INK, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED,
                     "svg.fonttype": "none", "savefig.facecolor": BG})
sources = {}


def source(path):
    path = Path(path)
    try:
        key = "geometry-real-room-v1/" + str(path.relative_to(GEO))
    except ValueError:
        key = str(path.relative_to(REPO))
    sources[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return path


def read(path):
    return json.loads(source(path).read_text())


result = read(GEO / "demo/geometry-demo/result.json")
assets = read(GEO / "demo/geometry-demo/assets.json")
door = next(row for row in result["objects"] if row["id"] == "mesh_door_002")
photo = door["photos"][0]
finding = next(row for row in read(GEO / "reports/opening_comparison/findings.json")["findings"]
               if row["object_id"] == door["id"])
review = read(REPO / "docs/evidence/2026-11-04/completed-review.json")
case = next(row for row in review["cases"] if row["context"].get("segment_id") == door["id"])
assert case["status"] == "needs_more_data"
assert review["geometry_job_id"] == result["job_id"]
assert case["context"]["ifc_id"] == door["ifc_guid"]
measurements = finding["measurements"]
observed = np.array(measurements["observed_corners_ifc_m"])
reference = np.array(measurements["ifc_corners_m"])
assert np.isclose(np.linalg.norm(observed[1] - observed[0]), door["dimensions"]["width"]["scan"])
assert np.isclose(np.linalg.norm(observed[2] - observed[1]), door["dimensions"]["height"]["scan"])


def canvas(width, height, eyebrow, title, subtitle):
    fig = plt.figure(figsize=(width / 100, height / 100), dpi=160, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set(xlim=(0, width), ylim=(height, 0)); ax.axis("off")
    text(ax, 34, 30, eyebrow.upper(), 10, GREEN, "bold")
    text(ax, 34, 66, title, 23, INK, "bold")
    text(ax, 34, 99, subtitle, 11.5, MUTED)
    return fig, ax


def text(ax, x, y, value, size=12, color=INK, weight="normal", **kwargs):
    return ax.text(x, y, value, fontsize=size, color=color, fontweight=weight,
                   va="center", linespacing=1.5, **kwargs)


def box(ax, x, y, w, h, color="white", edge=LINE):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=12",
                              facecolor=color, edgecolor=edge, linewidth=1))


def arrow(ax, start, end, color=GREEN, **kwargs):
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=15,
                               color=color, linewidth=1.5, **kwargs))


def photo_panel(fig, bounds, kind):
    path = GEO / "demo/geometry-demo" / assets[photo[kind]]
    image = np.asarray(Image.open(source(path)))
    # RGB and binary mask are native landscape frames; the archived overlay is upright.
    if kind != "overlay_url":
        image = np.rot90(image, 3)
    ax = fig.add_axes(bounds)
    ax.imshow(image, cmap="gray", interpolation="nearest")
    ax.axis("off")
    return ax


def save(fig, name):
    fig.savefig(OUT / (name + ".png"), dpi=160)
    plt.close(fig)


# 1. Four views of the very same real-room door candidate.
fig, ax = canvas(1360, 670, "Recorded real-room case", "From capture to a traceable next action",
                 "One door candidate · original frame → SAM mask → scan/IFC comparison → Inspector–Verifier review")
xs = [34, 366, 698, 1030]
for i, (x, title) in enumerate(zip(xs, ["RGB-D capture", "SAM 3.1 mask", "Scan vs IFC", "Reviewed next action"])):
    box(ax, x, 132, 296, 464)
    text(ax, x + 18, 158, f"0{i + 1}", 11, GREEN, "bold")
    text(ax, x + 18, 186, title, 15, INK, "bold")
    if i < 3:
        arrow(ax, (x + 300, 342), (x + 328, 342))
for x, kind in zip(xs[:3], ["rgb_url", "mask_url", "overlay_url"]):
    photo_panel(fig, [(x + 19) / 1360, 146 / 670, 258 / 1360, 306 / 670], kind)
text(ax, 52, 548, "RGB view of a calibrated capture", 10.2, MUTED)
text(ax, 52, 572, "Depth + camera pose retained", 10.2, MUTED)
text(ax, 384, 548, "Binary door segmentation", 10.2, MUTED)
text(ax, 384, 572, "Same source frame", 10.2, MUTED)
text(ax, 716, 548, "IFC outline", 10.5, TEAL, "bold")
text(ax, 852, 548, "Scan contour", 10.5, GOLD, "bold")
text(ax, 716, 572, "Width: 919 → 896 mm", 11.5, INK, "bold")
text(ax, 1049, 231, "INSPECTOR → VERIFIER", 10, GREEN, "bold")
text(ax, 1049, 276, "Request targeted\ninspection evidence", 17, INK, "bold")
text(ax, 1049, 348, "Project acceptance limits\nComplete boundary coverage\nMeasurement / alignment proof", 11, MUTED)
ax.plot([1049, 1308], [408, 408], color=LINE)
text(ax, 1049, 440, "TRACEABLE TO", 10, GREEN, "bold")
text(ax, 1049, 487, "Frame  cf50f812…\nObject  mesh_door_002\nReview  6ee881ab…", 11, INK)
text(ax, 1049, 557, "Archived status: needs_more_data", 10.2, MUTED)
text(ax, 34, 631, "Source-linked review keeps the next inspection step explicit. Measurements come from geometry; agents review the evidence.", 11, MUTED)
save(fig, "capture-to-review")


# 2. Model perception versus numerical measurement. The 3D lines use retained coordinates.
fig, ax = canvas(1360, 740, "Geometry Engine", "Meta perception. Metric geometry.",
                 "Original frame → semantic element → measured 3D contour, with every stage tied to the same capture")
for x, w, step, title in [(34, 348, "01 · SENSOR INPUT", "Original RGB-D frame"),
                           (412, 348, "02 · META SAM 3.1", "Identified door"),
                           (790, 536, "03 · NUMERICAL CODE", "3D contour vs IFC")]:
    box(ax, x, 132, w, 532)
    text(ax, x + 20, 160, step, 10.5, GREEN, "bold")
    text(ax, x + 20, 191, title, 15, INK, "bold")
photo_panel(fig, [80 / 1360, 170 / 740, 255 / 1360, 340 / 740], "rgb_url")
photo_panel(fig, [458 / 1360, 170 / 740, 255 / 1360, 340 / 740], "mask_url")
text(ax, 54, 605, "RGB, depth and camera pose", 12, INK, "bold")
text(ax, 54, 636, "Calibrated observations from the phone", 10.8, MUTED)
text(ax, 432, 605, "The model identifies the door", 12, INK, "bold")
text(ax, 432, 636, "Masks feed depth-supported mesh fusion", 10.8, MUTED)
arrow(ax, (386, 380), (408, 380)); arrow(ax, (764, 380), (786, 380))
plot = fig.add_axes([.605, .25, .19, .46], projection="3d", facecolor="white")
for coords, c, style in [(reference, TEAL, "--"), (observed, GOLD, "-")]:
    closed = np.vstack([coords, coords[0]])
    plot.plot(closed[:, 0], closed[:, 1], closed[:, 2], color=c, ls=style, lw=2.4)
plot.view_init(elev=13, azim=-72)
plot.set_xlim(.05, 1.25); plot.set_ylim(3.1, 3.65); plot.set_zlim(-.08, 2.38)
plot.set_box_aspect((1.2, .55, 2.46)); plot.set_axis_off()
text(ax, 1085, 269, "WIDTH", 10, MUTED, "bold")
text(ax, 1085, 300, f"{measurements['observed_width_m'] * 1000:.0f} mm", 23, GOLD, "bold")
text(ax, 1085, 332, "IFC 919 mm · Δ −23 mm", 10.3, MUTED)
text(ax, 1085, 393, "HEIGHT", 10, MUTED, "bold")
text(ax, 1085, 424, f"{measurements['observed_height_m'] * 1000:.0f} mm", 23, GOLD, "bold")
text(ax, 1085, 456, "IFC 2173 mm · Δ −14 mm", 10.3, MUTED)
text(ax, 824, 558, "— Observed contour", 10.5, GOLD, "bold")
text(ax, 1085, 558, "- - IFC reference", 10.5, TEAL, "bold")
text(ax, 810, 605, "Lengths computed from retained 3D corners", 12, INK, "bold")
text(ax, 810, 636, "IFC coordinates · equal scale · no per-object refit", 10.8, MUTED)
text(ax, 34, 703, "Door candidate mesh_door_002 · partial boundary support retained · dimensions are evidence for review, not a compliance verdict.", 11, MUTED)
save(fig, "geometry-responsibilities")


# 3. Staged drawing/photo architecture; no invented drawing or result is pictured.
source(REPO / "drawing_photo_service/staged.py")
fig, ax = canvas(1360, 560, "Drawing / Photo · staged workflow", "Fixed requirements. Fresh evidence review.",
                 "Muse Spark 1.3 works in three separate contexts; requirement identities stay fixed across the assessment and review.")
nodes = [(34, "INPUT", "Drawing / PDF", "Design source\nSelected page + task"),
         (366, "CONTEXT 1", "Fixed checklist", "Drawing-only extraction\nStable requirement IDs"),
         (698, "CONTEXT 2", "Photo assessment", "Current photo + checklist\nDraft findings + evidence"),
         (1030, "CONTEXT 3", "Fresh review", "Both originals + checklist\nReview and revise the draft")]
for x, label, title, detail in nodes:
    box(ax, x, 170, 296, 205, PALE if label == "INPUT" else "white")
    text(ax, x + 20, 198, label, 10, GREEN, "bold")
    text(ax, x + 20, 240, title, 17, INK, "bold")
    text(ax, x + 20, 308, detail, 12, MUTED)
    if x != 1030:
        arrow(ax, (x + 300, 273), (x + 328, 273))
ax.plot([514, 514, 1178, 1178], [166, 135, 135, 166], color=LINE, lw=1.8)
text(ax, 846, 135, "Same requirements", 10.5, GREEN, bbox={"facecolor": BG, "edgecolor": "none", "pad": 5}, ha="center")
box(ax, 366, 409, 628, 56, PALE, PALE)
text(ax, 680, 438, "New photograph → reuse a compatible checklist", 12, GREEN, "bold", ha="center")
arrow(ax, (846, 410), (846, 379))
text(ax, 1030, 423, "Reviewed findings", 12, INK, "bold")
text(ax, 1030, 450, "Source regions + explicit verdicts", 11, MUTED)
arrow(ax, (1178, 379), (1178, 400))
text(ax, 34, 512, "Code preserves checklist identity, validates output schemas and maps evidence regions back to the original source pixels.", 11, MUTED)
save(fig, "drawing-photo-workflow")


# 5a. Real-room observed surfaces; no accuracy or compliance aggregation.
surface_rows = [row for row in read(GEO / "reports/scan_vs_bim/objects.json") if "statistics" in row]
assert len(surface_rows) == 5
fig, ax = canvas(1360, 570, "Measured results · real room", "Observed surfaces against the laser-derived IFC",
                 "P95 absolute surface deviation after rigid alignment · all five reported surfaces · millimetres")
plot = fig.add_axes([.15, .25, .65, .48], facecolor=BG)
values = [row["statistics"]["p95_absolute_m"] * 1000 for row in surface_rows]
names = ["Wall 01", "Wall 02 · door", "Wall 03", "Wall 04 · window", "Floor"]
plot.barh(names, values, color=GREEN, height=.48)
plot.invert_yaxis(); plot.set_xlim(0, 112); plot.set_xticks([0, 25, 50, 75, 100])
plot.set_xlabel("P95 absolute deviation (mm)", labelpad=12)
plot.set_axisbelow(True); plot.grid(axis="x", color=LINE)
plot.tick_params(axis="both", length=0, pad=10)
for spine in plot.spines.values(): spine.set_visible(False)
for i, value in enumerate(values):
    plot.text(value + 2, i, f"{value:.1f}", va="center", color=INK, weight="bold", fontsize=13)
text(ax, 1140, 205, "99", 36, GREEN, "bold")
text(ax, 1140, 245, "RGB-D frames", 11, MUTED)
text(ax, 1140, 325, "5", 36, GREEN, "bold")
text(ax, 1140, 365, "Observed surfaces", 11, MUTED)
text(ax, 34, 521, "Measured on observed portions. Whole-element verification and hidden surfaces are outside this result's scope.", 11, MUTED)
save(fig, "real-room-results")


# 5b. The complete controlled wall experiment; separate from the real-room metric.
summary = read(REPO / "docs/evidence/2026-11-03/controlled-benchmark/summary.json")
wall = summary["wall"]
assert wall["nearest_surface"]["n"] == wall["camera_side"]["n"] == 18
fig, ax = canvas(1360, 570, "Measured results · controlled geometry", "Selecting the observed side of the wall",
                 "18 predefined two-sided-wall cases · generated geometry with known displacement · same cases for both methods")
plot = fig.add_axes([.20, .31, .54, .39], facecolor=BG)
keys = ["nearest_surface", "camera_side"]
labels = ["Nearest surface", "Camera-side selection"]
vals = [wall[key]["mean_absolute_error_mm"] for key in keys]
plot.barh(labels, vals, color=["#9ca99f", GREEN], height=.42)
plot.invert_yaxis(); plot.set_ylim(1.65, -.65); plot.set_xlim(0, 102); plot.set_xticks([0, 20, 40, 60, 80, 100])
plot.set_xlabel("Mean absolute signed-offset error (mm)", labelpad=14)
plot.grid(axis="x", color=LINE); plot.set_axisbelow(True); plot.tick_params(length=0, pad=10)
for spine in plot.spines.values(): spine.set_visible(False)
for i, value in enumerate(vals):
    label = "<0.001 mm" if value < .001 else f"{value:.2f} mm"
    plot.text(value + 2, i, label, va="center", color=INK, weight="bold", fontsize=13)
box(ax, 1070, 163, 256, 252, PALE, PALE)
text(ax, 1092, 194, "ERRORS ABOVE 1 mm", 10, GREEN, "bold")
text(ax, 1092, 242, f"{wall['nearest_surface']['above_1mm']}/18", 28, MUTED, "bold")
text(ax, 1092, 277, "Nearest surface", 11, MUTED)
text(ax, 1092, 332, f"{wall['camera_side']['above_1mm']}/18", 28, GREEN, "bold")
text(ax, 1092, 371, "Camera-side selection", 11, GREEN, "bold")
text(ax, 34, 480, "Known numerical answers make the surface-selection rule directly testable across all 18 cases.", 12, INK, "bold")
text(ax, 34, 525, "Analytic geometry validation: numerical zero in this experiment does not represent physical scanner accuracy.", 11, MUTED)
save(fig, "controlled-results")

(OUT / "sources.json").write_text(json.dumps({
    "kind": "readme_figures_from_retained_evidence",
    "geometry_archive": "https://github.com/TykhinBiriukov/ai-construction-inspector/releases/download/evidence-2026-11-03/geometry-real-room-v1.zip",
    "source_frame_id": photo["frame_id"], "object_id": door["id"], "ifc_guid": door["ifc_guid"],
    "geometry_job_id": result["job_id"], "review_case_id": case["case_id"],
    "new_model_calls": 0, "sha256": sources,
}, indent=2) + "\n")
print("Rendered five figures; verified matching object, review, source frame and retained 3D lengths.")
