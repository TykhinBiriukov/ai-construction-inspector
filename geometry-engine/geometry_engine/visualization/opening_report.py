"""Portable, offline review of geometry candidates using saved capture evidence.

This exporter does not invoke an agent or confirm defects. Images are copied as
thumbnails so the review directory remains useful independently of the source.
"""
from html import escape
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from geometry_engine.segmentation.mask_projection import camera_parameters
from geometry_engine.segmentation.projection_camera import ProjectionCameras
from geometry_engine.segmentation.service import upright_quarter_turns
from geometry_engine.capture.session_layout import original_directory
from geometry_engine.capture.validation import session_file
from geometry_engine.common.artifact_paths import artifact_reference


STATUS_LABELS = {
    "candidate_deviation": "Candidate deviation: photo review required",
    "no_candidate_deviation": "No tolerance exceedance found in the observed region",
    "unobserved_reference": "Design object not observed: absence is unproven",
    "unmatched_observation": "Observed object without an IFC match",
    "ambiguous_match": "Ambiguous match: review required",
    "reference_unavailable": "Design object geometry unavailable",
}

DELTA_LABELS = {
    "left": "Left edge", "right": "Right edge", "bottom": "Bottom edge", "top": "Top edge",
    "horizontal_center": "Center along wall", "vertical_center": "Vertical center",
    "normal_center": "Center normal to surface", "width": "Width", "height": "Height",
}
SUMMARY_LABELS = {
    "observed_instances": "Objects in segmented mesh", "ifc_objects": "Design doors and windows",
    "matched_pairs": "Matched pairs", "candidate_deviations": "Candidate deviations",
    "ambiguous_matches": "Ambiguous matches",
    "unassigned_semantic_faces": "Labeled triangles without an instance ID", "candidates": "Candidates",
}
REVIEW_LABELS = {
    "observed_mesh_extent_is_not_proven_complete": "The entire door or window is not confirmed to be labeled. Check occluded and missing regions.",
    "verify_same_boundary_definition_leaf_frame_or_opening": "Check that both measurements use the same boundary definition: leaf, frame or opening.",
    "competing_instances_or_references": "Several matches are possible. Select the correct object using photo evidence.",
    "possible_extra_object_or_fragment_or_false_segmentation": "This may be an extra object, a fragment of another object or a segmentation error.",
    "insufficient_segmented_surface_area": "The labeled region is too small for reliable comparison.",
    "not_detected_does_not_mean_absent": "The object is not labeled, which does not prove its absence. Check occlusions and capture quality.",
    "no_linked_photo_evidence": "No linked photo evidence is available for this finding.",
    "other_instances_near_same_reference_may_be_fragments": "Other segmented regions are near this design object. They may be fragments of the same door or window.",
}
LIMITATION_LABELS = {
    "All findings require photo review; no automatic confirmed violation or absence verdict.": "Every finding requires photo review; the module does not automatically confirm defects or missing objects.",
    "Exact bounds of observed segmented vertices may be incomplete or contain outliers.": "Labeled region boundaries may be incomplete or contain outliers.",
    "IFC element geometry is compared, not silently substituted by a structural opening.": "IFC element geometry is compared; it is not silently replaced with structural opening geometry.",
    "Existing instance IDs may represent fragments of the same physical object.": "Different segmentation IDs may represent fragments of one physical object.",
    "Unassigned semantic faces are reported but not silently merged into a new object.": "Labeled triangles without an instance ID are reported separately and are not automatically merged.",
    "Matching is one-to-one with spatial gates; large shifts can remain unmatched.": "Matching is one-to-one. A large displacement may leave an object unmatched.",
    "Spatial matching does not establish wall identity; nearby corner objects require review.": "Proximity does not establish a shared host wall. Objects near corners require review.",
    "Photo visibility of unmatched IFC objects is a geometric candidate, not proof of an unobstructed view.": "A design object projecting into a frame does not prove an unobstructed view.",
}


def _safe_source(value, session, segmentation, *, original=False):
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError("Evidence path must be a nonempty local path")
    root = original_directory(session)
    candidate = Path(value)
    if candidate.is_absolute():
        candidate = candidate.resolve()
        allowed = (root,) if original else (root, session, segmentation)
        if not any(candidate.is_relative_to(base) for base in allowed):
            raise ValueError("Evidence must stay inside the capture/session")
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate
    if original or candidate.as_posix().startswith("original/"):
        return session_file(root, session, str(value))
    # Mask and overlay paths in detections.json are relative to segmentation.
    return session_file(segmentation, segmentation, str(value))


def _picture(path):
    with Image.open(path) as source:
        if source.width * source.height > 25_000_000:
            raise ValueError("Evidence image exceeds pixel limit")
        return source.convert("RGB")


def _thumbnail(image, turns, destination):
    if turns:
        image = Image.fromarray(np.rot90(np.asarray(image), turns).copy())
    image.thumbnail((720, 960), Image.Resampling.LANCZOS)
    image.save(destination, quality=88)


def _numbers(values):
    if not values:
        return "<p>No measurements.</p>"
    return '<pre>' + escape(json.dumps(values, ensure_ascii=False, indent=2, allow_nan=False)) + '</pre>'


def _format_number(value, places=3, *, signed=False, scale=1):
    if type(value) not in (int, float) or not np.isfinite(value):
        return "—"
    return format(value * scale, ("+" if signed else "") + f".{places}f")


def _measurement_table(values):
    if not values:
        return '<p>Dimensions have not been matched to IFC.</p>'
    rows = []
    for field, label in (("width", "Width"), ("height", "Height")):
        rows.append('<tr><td>' + label + '</td><td>' + _format_number(values.get(f"observed_{field}_m")) +
                    '</td><td>' + _format_number(values.get(f"ifc_{field}_m")) + '</td></tr>')
    return ('<p>Measured <strong>labeled region</strong>; its completeness is not yet confirmed.</p>'
            '<table><thead><tr><th>Dimension</th><th>Labeled region, m</th><th>IFC element, m</th></tr></thead><tbody>' +
            ''.join(rows) + '</tbody></table>')


def _delta_table(values, tolerance):
    if not values:
        return '<p>No match: deviations were not calculated.</p>'
    rows = []
    for key, label in DELTA_LABELS.items():
        value = values.get(key, values.get(key + "_m"))
        if value is None:
            continue
        exceeded = type(value) in (int, float) and tolerance is not None and abs(value) > tolerance
        tag = ' class="exceeded"' if exceeded else ''
        rows.append(f'<tr{tag}><td>' + label + '</td><td>' + _format_number(value, 1, signed=True, scale=100) +
                    '</td><td>' + ('Above threshold: review photos' if exceeded else '—') + '</td></tr>')
    return ('<p>Difference = observation minus IFC. Displacement signs follow the local IFC element axes; '
            'a negative size difference means the labeled region is smaller than the design object.</p>'
            '<table><thead><tr><th>Measurement</th><th>Difference, cm</th><th>Review</th></tr></thead><tbody>' +
            ''.join(rows) + '</tbody></table>')


def _summary_table(values):
    rows = ['<tr><td>' + escape(label) + '</td><td>' + escape(str(values[key])) + '</td></tr>'
            for key, label in SUMMARY_LABELS.items() if key in values]
    return '<table><tbody>' + ''.join(rows) + '</tbody></table>'


def _clip_segment(a, b, width, height):
    """Clip a 2D line to the image without clamping an offscreen line onto a border."""
    delta = b - a
    low, high = 0., 1.
    for axis, maximum in ((0, width - 1), (1, height - 1)):
        if abs(delta[axis]) < 1e-12:
            if a[axis] < 0 or a[axis] > maximum:
                return None
            continue
        entry, leave = sorted((-a[axis] / delta[axis], (maximum - a[axis]) / delta[axis]))
        low, high = max(low, entry), min(high, leave)
        if low > high:
            return None
    return (a + low * delta).tolist(), (a + high * delta).tolist()


def projected_box_segments(corners, metadata, coordinates, size, *, camera=None):
    """Project four ordered SCAN-world corners to stored native RGB pixels.

    Clip each edge against a 1 cm camera near plane and the image viewport.
    Projection alone is not an occlusion or edge-visibility test.
    """
    corners = np.asarray(corners, dtype=float)
    if corners.shape != (4, 3) or not np.isfinite(corners).all():
        raise ValueError("Expected four finite ordered box corners")
    K, transform = camera if camera is not None else camera_parameters(metadata, coordinates)
    if (K.shape != (3, 3) or transform.shape != (4, 4)
            or not np.isfinite(K).all() or not np.isfinite(transform).all()):
        raise ValueError("Invalid evidence camera")
    homogeneous = np.column_stack((corners, np.ones(4))) @ np.linalg.inv(transform).T
    if not np.allclose(homogeneous[:, 3], 1):
        raise ValueError("Expected affine camera transform")
    camera = homogeneous[:, :3]
    segments = []
    for index in range(4):
        a, b = camera[index].copy(), camera[(index + 1) % 4].copy()
        if max(a[2], b[2]) < .01:
            continue
        if a[2] < .01:
            a += (b - a) * ((.01 - a[2]) / (b[2] - a[2]))
        if b[2] < .01:
            b += (a - b) * ((.01 - b[2]) / (a[2] - b[2]))
        pa, pb = K @ a, K @ b
        if pa[2] <= 0 or pb[2] <= 0:
            continue
        clipped = _clip_segment(pa[:2] / pa[2], pb[:2] / pb[2], *size)
        if clipped is not None:
            segments.append(clipped)
    return segments


def write_opening_review(output_dir, report, session, segmentation_dir):
    """Write review.html + evidence/ + evidence_index.json; return artifact paths.

    Report values and filenames are untrusted data. At most six photographs per
    finding are copied. No hidden edges are inferred or drawn by this exporter.
    """
    output, session, segmentation = map(lambda p: Path(p).resolve(),
                                        (output_dir, session, segmentation_dir))
    if not segmentation.is_relative_to(session):
        raise ValueError("Segmentation must stay inside the session")
    cameras = ProjectionCameras(session, segmentation)
    output.mkdir(parents=True, exist_ok=True)
    images = output / "evidence"
    images.mkdir(exist_ok=True)
    coordinates = None
    original = original_directory(session)
    for filename in ("capture.json", "scene.json"):
        if (original / filename).is_file():
            coordinates = json.loads((original / filename).read_text(encoding="utf-8")).get("coordinate_system")
            break
    sections, index = [], []
    tolerance = report.get("parameters", {}).get("tolerance_m")
    if type(tolerance) not in (int, float) or not np.isfinite(tolerance) or tolerance <= 0:
        tolerance = None
    for number, finding in enumerate(report.get("findings", [])):
        status = str(finding.get("status", "unknown"))
        heading = STATUS_LABELS.get(status, status)
        cards = []
        for photo_number, item in enumerate(finding.get("evidence", [])[:6]):
            prefix = f"{number:04d}_{photo_number:02d}"
            record = {"finding_id": finding.get("finding_id"), "frame_id": item.get("frame_id")}
            rgb_path = _safe_source(item.get("rgb"), session, segmentation, original=True)
            record["source_rgb"] = artifact_reference(rgb_path,session)
            photo = _picture(rgb_path)
            turns = 0
            metadata = None
            if item.get("metadata"):
                metadata_path = _safe_source(item["metadata"], session, segmentation, original=True)
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                turns = upright_quarter_turns(metadata, coordinates)
            rgb_name = f"evidence/{prefix}_rgb.jpg"
            _thumbnail(photo, turns, output / rgb_name)
            record["rgb_thumbnail"] = rgb_name
            labeled_name = None
            labeled = photo.copy()
            has_labels = False
            if item.get("mask"):
                mask_path = _safe_source(item["mask"], session, segmentation)
                with Image.open(mask_path) as source:
                    if source.size != photo.size:
                        raise ValueError("Evidence mask and RGB dimensions differ")
                    mask = np.asarray(source.convert("L")) != 0
                pixels = np.asarray(photo).copy()
                pixels[mask] = (pixels[mask] * .55 + np.array([40, 210, 245]) * .45).astype(np.uint8)
                labeled = Image.fromarray(pixels)
                record["source_mask"] = artifact_reference(mask_path,session)
                has_labels = True
            elif item.get("overlay"):
                overlay_path = _safe_source(item["overlay"], session, segmentation)
                labeled = _picture(overlay_path)
                if labeled.size != photo.size:
                    raise ValueError("Evidence overlay and RGB dimensions differ")
                has_labels = True
                record["source_overlay"] = str(overlay_path)
            projected = {}
            notes = []
            for field, key, color in (("predicted_corners_world_m", "ifc", (0, 240, 255)),
                                      ("observed_corners_world_m", "observed", (255, 140, 20))):
                if item.get(field) is None:
                    continue
                if metadata is None or coordinates is None:
                    notes.append(key + ": missing camera metadata or coordinate convention")
                    continue
                try:
                    segments = projected_box_segments(item[field], metadata, coordinates, photo.size,
                        camera=cameras.parameters(metadata, coordinates, item.get('frame_id')))
                except (ValueError, KeyError, np.linalg.LinAlgError) as error:
                    # A missing projection must not imply that an object is absent.
                    notes.append(key + ": projection unavailable (" + type(error).__name__ + ")")
                    continue
                projected[key] = segments
                pen = ImageDraw.Draw(labeled)
                for a, b in segments:
                    pen.line([tuple(a), tuple(b)], fill=color, width=max(2, round(photo.width / 480)))
                has_labels |= bool(segments)
            if has_labels:
                labeled_name = f"evidence/{prefix}_mask.jpg"
                _thumbnail(labeled, turns, output / labeled_name)
            record["projected_box_segments_native_pixels"] = projected
            record["projection_notes"] = notes
            record["mask_thumbnail"] = labeled_name
            record["rotation_ccw_degrees"] = turns * 90
            index.append(record)
            pictures = f'<figure><a href="{rgb_name}"><img src="{rgb_name}" alt="Original photo"></a><figcaption>Photo</figcaption></figure>'
            if labeled_name:
                pictures += f'<figure><a href="{labeled_name}"><img src="{labeled_name}" alt="SAM mask and bounding rectangles"></a><figcaption>SAM mask; cyan contour: IFC; orange contour: observed region. These are candidate bounding rectangles, not verified opening contours. Furniture occlusion is not checked here.</figcaption></figure>'
            cards.append('<div class="photo"><p>Frame ' + escape(str(item.get("frame_id", ""))) +
                         '</p><div class="pair">' + pictures + '</div></div>')
        reasons = ''.join('<li>' + escape(REVIEW_LABELS.get(str(reason), str(reason))) + '</li>'
                          for reason in finding.get("review_reasons", []))
        category = {"door": "Door", "window": "Window", "window_block": "Window assembly"}.get(
            str(finding.get("category", "")), str(finding.get("category", "")))
        group_note = ''
        if finding.get('object_id'):
            group_note = ('<p><strong>Object contour approximated from the segmented mesh.</strong> '
                'Boundary completeness and measurement accuracy require review.</p>'
                '<p>Source regions: ' + escape(', '.join(map(str, finding.get('source_instance_ids', [])))) +
                '.</p>')
        sections.append('<section><h2>' + escape(str(finding.get("finding_id", number))) + ' · ' +
                        escape(category) + '</h2><p class="status">' +
                        escape(heading) + '</p><p>IFC: ' + escape(str(finding.get("ifc_guid") or "—")) +
                        ' · Mesh object: ' + escape(str(finding.get("instance_id", "—"))) +
                        '</p>' + group_note + '<h3>Dimensions</h3>' + _measurement_table(finding.get("measurements")) +
                        '<h3>Deviations</h3>' + _delta_table(finding.get("deltas_m"), tolerance) +
                        '<details><summary>Technical measurement details</summary>' + _numbers(finding.get("measurements")) +
                        _numbers(finding.get("deltas_m")) + '</details>' +
                        '<ul>' + reasons + '</ul>' +
                        (''.join(cards) if cards else '<p>No photo evidence is included in this report. Object absence cannot be confirmed.</p>') + '</section>')
    limitations = ''.join('<li>' + escape(LIMITATION_LABELS.get(str(text), str(text))) + '</li>'
                           for text in report.get("limitations", []))
    document = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Doors and windows: candidate inspection</title><style>
body{margin:0;background:#101722;color:#e9eef5;font:16px system-ui,sans-serif}main{max-width:1200px;margin:auto;padding:24px}
section{background:#1b2737;border:1px solid #40516a;border-radius:12px;padding:20px;margin:24px 0}
h1,h2,h3{line-height:1.3}.status{color:#ffd27a;font-weight:600}.pair{display:flex;gap:16px;flex-wrap:wrap}
figure{margin:0;flex:1;min-width:230px}img{max-width:100%;max-height:560px;object-fit:contain;background:#111}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#101722;padding:12px}a{color:#88caff}.photo{margin:20px 0}
li{margin:8px 0}figcaption{color:#b6c5d7}h2,p{overflow-wrap:anywhere}
table{border-collapse:collapse;width:100%;margin:12px 0}th,td{padding:10px;text-align:left;border-bottom:1px solid #40516a}
th{color:#b6c5d7}.exceeded{color:#ffd27a}details{margin:18px 0;color:#b6c5d7}summary{cursor:pointer}
</style><main><h1>Doors and windows: candidates for review</h1>
<p>These are geometric observations, not confirmed construction defects. A labeled region may differ in size from the complete object. No agent has reviewed the photos yet.</p>
<p><a href="report.json">JSON report</a> · <a href="evidence_index.json">Photo sources</a></p>'''
    document += '<h2>Summary</h2>' + _summary_table(report.get("summary", {}))
    if tolerance is not None:
        document += '<p>Candidate deviation threshold: ' + _format_number(tolerance, 1, scale=100) + ' cm.</p>'
    document += ''.join(sections)
    document += '<h2>Limitations</h2><ul>' + limitations + '</ul></main></html>'
    (output / "review.html").write_text(document, encoding="utf-8")
    (output / "evidence_index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return {"review": "review.html", "evidence_index": "evidence_index.json"}
