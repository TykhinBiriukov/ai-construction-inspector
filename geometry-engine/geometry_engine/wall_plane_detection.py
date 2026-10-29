"""RANSAC candidates on existing mesh surfaces; no new scene geometry is inferred.

Three-point hypotheses, support/RMSE ranking, adaptive iteration count and final
least-squares fitting adapt Open3D's PointCloudSegmentation.cpp (MIT), commit
1e7b17438687a0b0c1e5a7187321ac7044afe275. See THIRD_PARTY_NOTICES.md.
This CPU NumPy adaptation samples triangle surfaces by area, checks mesh normals,
and maps the result back to unchanged original face indices.
Only faces explicitly labeled as wall participate in sampling, fitting and support.
"""

from dataclasses import dataclass
import math

import numpy as np

from .plane_merging import merge_planes


@dataclass(frozen=True)
class WallPlaneSettings:
    distance_threshold_m: float = .025
    normal_angle_deg: float = 30.
    max_vertical_tilt_deg: float = 12.
    min_support_area_m2: float = 1.
    min_height_m: float = 1.
    min_width_m: float = .5
    max_planes: int = 12
    sample_count: int = 24000
    num_iterations: int = 1500
    seed: int = 17
    ransac_probability: float = .999
    merge_enabled: bool = True
    merge_max_angle_deg: float = 3.
    merge_max_gap_m: float = .10
    merge_distance_m: float = .03
    merge_min_area_fraction: float = .95
    merge_min_contact_length_m: float = .10
    merge_step_offset_m: float = .015

    def __post_init__(self):
        for name in ("distance_threshold_m", "min_support_area_m2", "min_height_m", "min_width_m"):
            value = getattr(self, name)
            if isinstance(value, bool) or not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        for name, maximum in (("normal_angle_deg", 90), ("max_vertical_tilt_deg", 45)):
            value = getattr(self, name)
            if isinstance(value, bool) or not np.isfinite(value) or not 0 < value < maximum:
                raise ValueError(f"Invalid {name}")
        for name, minimum, maximum in (("max_planes", 1, 100), ("sample_count", 3, 500000),
                                      ("num_iterations", 1, 100000), ("seed", 0, 2**32-1)):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"Invalid {name}")
        if not np.isfinite(self.ransac_probability) or not 0 < self.ransac_probability < 1:
            raise ValueError("ransac_probability must be in (0,1)")
        if type(self.merge_enabled) is not bool:
            raise ValueError('merge_enabled must be boolean')
        for name in ('merge_max_gap_m', 'merge_distance_m', 'merge_step_offset_m', 'merge_min_contact_length_m'):
            value = getattr(self, name)
            if isinstance(value, bool) or not np.isfinite(value) or value <= 0:
                raise ValueError(f'Invalid {name}')
        for name in ('merge_max_angle_deg',):
            value = getattr(self, name)
            if isinstance(value, bool) or not np.isfinite(value) or not 0 < value < 90:
                raise ValueError(f'Invalid {name}')
        if isinstance(self.merge_min_area_fraction, bool) or not np.isfinite(self.merge_min_area_fraction) or not 0 < self.merge_min_area_fraction <= 1:
            raise ValueError('merge_min_area_fraction must be in (0,1]')


def _canonical(normal, offset):
    if normal[np.argmax(np.abs(normal))] < 0:
        return -normal, -offset
    return normal, offset


def _dot(points, normal):
    # Explicit three-term dot avoids repeatedly starting a threaded BLAS job.
    return points[..., 0]*normal[0]+points[..., 1]*normal[1]+points[..., 2]*normal[2]


def _fit(triangles, areas, ids):
    points = triangles[ids].reshape(-1, 3)
    weights = np.repeat(areas[ids]/3, 3)
    center = np.average(points, weights=weights, axis=0)
    centered = points-center
    covariance = (centered*weights[:, None]).T@centered/weights.sum()
    values, vectors = np.linalg.eigh(covariance)
    if values[1] <= max(1e-14, values[2]*1e-12):
        return None
    return _canonical(vectors[:, 0], -float(vectors[:, 0]@center))


def _support(triangles, normals, eligible, normal, offset, settings):
    # All three vertices must lie in the slab. A crossing triangle is not a wall.
    distance = np.max(np.abs(_dot(triangles, normal)+offset), axis=1)
    orientation = np.abs(_dot(normals, normal)) >= math.cos(math.radians(settings.normal_angle_deg))
    return eligible & orientation & (distance <= settings.distance_threshold_m), distance


def _ransac(points, normals, rng, up, settings):
    """Open3D's hypothesis/score/refine sequence, with normal/vertical guards."""
    best_model, best_mask, best_count, best_rmse = None, None, 0, float("inf")
    limit, iterations = settings.num_iterations, 0
    normal_cos = math.cos(math.radians(settings.normal_angle_deg))
    vertical_sin = math.sin(math.radians(settings.max_vertical_tilt_deg))
    while iterations < limit:
        iterations += 1
        sample = rng.choice(len(points), 3, replace=False)
        a, b, c = points[sample]
        normal = np.cross(b-a, c-a)
        magnitude = float(np.linalg.norm(normal))
        if magnitude < 1e-10:
            continue
        normal /= magnitude
        if abs(float(normal@up)) > vertical_sin:
            continue
        if np.any(np.abs(_dot(normals[sample], normal)) < normal_cos):
            continue
        offset = -float(normal@a)
        distance = np.abs(_dot(points, normal)+offset)
        mask = (distance <= settings.distance_threshold_m) & (np.abs(_dot(normals, normal)) >= normal_cos)
        count = int(mask.sum())
        if count < max(3, best_count):
            continue
        rmse = float(np.sqrt(np.mean(distance[mask]**2)))
        if count > best_count or rmse < best_rmse:
            best_model, best_mask, best_count, best_rmse = (normal, offset), mask, count, rmse
            fraction = count/len(points)
            if fraction >= 1:
                limit = iterations
            else:
                estimate = math.ceil(math.log1p(-settings.ransac_probability)/math.log1p(-fraction**3))
                # Minimum attempts limits premature convergence onto a noisy wall.
                limit = min(limit, max(64, iterations, estimate))
    return best_model, best_mask, iterations


def _plane_record(normal, offset, ids, triangles, areas, wall_mask, up, settings):
    basis_u = np.cross(up, normal)
    basis_u /= np.linalg.norm(basis_u)
    basis_v = np.cross(normal, basis_u)
    if basis_v@up < 0:
        basis_u, basis_v = -basis_u, -basis_v
    origin = -offset*normal
    points = triangles[ids].reshape(-1, 3)
    uv = np.column_stack((_dot(points-origin, basis_u), _dot(points-origin, basis_v)))
    lower, upper = uv.min(axis=0), uv.max(axis=0)
    corners_uv = np.array([[lower[0], lower[1]], [upper[0], lower[1]],
                           [upper[0], upper[1]], [lower[0], upper[1]]])
    corners = origin+corners_uv[:, :1]*basis_u+corners_uv[:, 1:]*basis_v
    area = float(areas[ids].sum())
    semantic_area = float(areas[ids[wall_mask[ids]]].sum())
    centroid_errors = np.abs(_dot(triangles[ids].mean(axis=1), normal)+offset)
    vertex_errors = np.abs(_dot(points, normal)+offset)
    # Area-weighted percentile avoids favoring densely tessellated small patches.
    order = np.argsort(centroid_errors, kind="stable")
    cumulative = np.cumsum(areas[ids][order])
    p95 = float(centroid_errors[order[min(len(order)-1, np.searchsorted(cumulative, .95*area))]])
    return {"normal": normal.tolist(), "offset_m": float(offset),
            "center_world": corners.mean(axis=0).tolist(), "corners_world": corners.tolist(),
            "basis_u": basis_u.tolist(), "basis_v": basis_v.tolist(),
            "support_face_count": len(ids), "support_area_m2": area,
            "rmse_m": float(np.sqrt(np.average(centroid_errors**2, weights=areas[ids]))),
            "p95_distance_m": p95, "max_vertex_distance_m": float(vertex_errors.max()),
            "width_m": float(upper[0]-lower[0]), "height_m": float(upper[1]-lower[1]),
            "semantic_wall_area_m2": semantic_area, "semantic_wall_fraction": semantic_area/area,
            "vertical_tilt_deg": math.degrees(math.asin(min(1., abs(float(normal@up))))),
            "status": "wall_candidate",
            "bounds_kind": "observed_support_rectangle_not_wall_boundary"}


def detect_wall_planes(vertices, faces, wall_mask=None, *, up_axis="z", settings=None, progress=None):
    """Return candidate planes and per-original-face IDs; never change the mesh.

    A SAM wall mask is mandatory. Unlabeled and other-class faces never enter
    sampling, least-squares refinement or final assignments, including coplanar
    neighbors. Rectangles visualize masked support; holes may lie inside them.
    """
    settings = settings or WallPlaneSettings()
    vertices, faces = np.asarray(vertices, dtype=float), np.asarray(faces)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all()
            or faces.ndim != 2 or faces.shape[1] != 3 or not np.issubdtype(faces.dtype, np.integer)
            or np.any(faces < 0) or np.any(faces >= len(vertices))):
        raise ValueError("Expected finite vertices and valid triangle indices")
    if up_axis not in ("x", "y", "z"):
        raise ValueError("up_axis must be x, y or z")
    if wall_mask is None:
        raise ValueError("SAM wall_mask is required; plane fitting only accepts wall-labeled faces")
    else:
        wall_mask = np.asarray(wall_mask)
        if wall_mask.shape != (len(faces),) or wall_mask.dtype != np.bool_:
            raise ValueError("wall_mask must be one boolean per mesh face")
    up = np.eye(3)[("x", "y", "z").index(up_axis)]
    triangles = vertices[faces]
    cross = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    double_area = np.linalg.norm(cross, axis=1)
    areas = double_area*.5
    normals = np.divide(cross, double_area[:, None], out=np.zeros_like(cross), where=double_area[:, None] > 0)
    eligible_angle = min(90., settings.max_vertical_tilt_deg+settings.normal_angle_deg)
    eligible = wall_mask & (areas > 1e-12) & (np.abs(_dot(normals, up)) <= math.sin(math.radians(eligible_angle)))
    labels = np.full(len(faces), -1, dtype=np.int32)
    diagnostics = {"eligible_face_count": int(eligible.sum()), "sample_count": 0,
                   "ransac_iterations": 0, "candidate_attempts": 0, "rejected_candidates": 0,
                   "assigned_face_count": 0, "unassigned_face_count": len(faces), "assigned_area_m2": 0.,
                   "up_axis": up_axis, "sam_used_as": "hard_wall_face_filter",
                   "sam_wall_face_count": int(wall_mask.sum()),
                   "sampling": "uniform_surface_area", "geometry_changed": False,
                   "implementation": "numpy_adaptation_of_open3d_0.19.0_segment_plane"}
    if not eligible.any():
        return {"planes": [], "face_plane_id": labels, "diagnostics": diagnostics}
    rng = np.random.default_rng(settings.seed)
    eligible_ids = np.flatnonzero(eligible)
    probabilities = areas[eligible_ids]/areas[eligible_ids].sum()
    sample_faces = rng.choice(eligible_ids, settings.sample_count, p=probabilities, replace=True)
    bary = rng.random((settings.sample_count, 2))
    outside = bary.sum(axis=1) > 1
    bary[outside] = 1-bary[outside]
    selected_triangles = triangles[sample_faces]
    points = selected_triangles[:, 0]+bary[:, :1]*(selected_triangles[:, 1]-selected_triangles[:, 0])+bary[:, 1:]*(selected_triangles[:, 2]-selected_triangles[:, 0])
    sample_normals = normals[sample_faces]
    diagnostics["sample_count"] = len(points)
    active_samples = np.ones(len(points), dtype=bool)
    remaining = eligible.copy()
    models = []
    vertical_sin = math.sin(math.radians(settings.max_vertical_tilt_deg))
    for attempt in range(settings.max_planes*4):
        if len(models) >= settings.max_planes or int(active_samples.sum()) < 3:
            break
        active = np.flatnonzero(active_samples)
        model, sample_inliers, iterations = _ransac(points[active], sample_normals[active], rng, up, settings)
        diagnostics["ransac_iterations"] += iterations
        diagnostics["candidate_attempts"] += 1
        if model is None:
            break
        # Remove rejected proposals from the sample pool as well, so small
        # furniture panels cannot keep the same dominant proposal forever.
        active_samples[active[sample_inliers]] = False
        normal, offset = model
        inliers, _ = _support(triangles, normals, remaining, normal, offset, settings)
        for _ in range(4):
            ids = np.flatnonzero(inliers)
            if not len(ids):
                break
            refined = _fit(triangles, areas, ids)
            if refined is None or abs(float(refined[0]@up)) > vertical_sin:
                break
            normal, offset = refined
            updated, _ = _support(triangles, normals, remaining, normal, offset, settings)
            stable = np.array_equal(inliers, updated)
            inliers = updated
            if stable:
                break
        ids = np.flatnonzero(inliers)
        if not len(ids):
            diagnostics["rejected_candidates"] += 1
            continue
        record = _plane_record(normal, offset, ids, triangles, areas, wall_mask, up, settings)
        if (record["support_area_m2"] < settings.min_support_area_m2 or record["height_m"] < settings.min_height_m
                or record["width_m"] < settings.min_width_m):
            diagnostics["rejected_candidates"] += 1
            continue
        normal, offset = _canonical(normal, offset)
        models.append((normal, offset))
        remaining[inliers] = False
        active_samples[inliers[sample_faces]] = False
        if progress:
            progress(f"Plane candidate {len(models)}: {record['support_area_m2']:.2f} m2, RMSE {record['rmse_m']*1000:.1f} mm")

    # Remove duplicate hypotheses within a fraction of the distance tolerance.
    unique_models = []
    for normal, offset in models:
        if any(abs(float(normal@other)) > math.cos(math.radians(2)) and
               abs(offset-(other_offset if normal@other >= 0 else -other_offset)) < .25*settings.distance_threshold_m
               for other, other_offset in unique_models):
            continue
        unique_models.append((normal, offset))
    best_distance = np.full(len(faces), np.inf)
    for index, (normal, offset) in enumerate(unique_models):
        inliers, distance = _support(triangles, normals, eligible, normal, offset, settings)
        replace = inliers & (distance < best_distance)
        best_distance[replace], labels[replace] = distance[replace], index
    planes = []
    for index, (normal, offset) in enumerate(unique_models):
        ids = np.flatnonzero(labels == index)
        if not len(ids):
            continue
        record = _plane_record(normal, offset, ids, triangles, areas, wall_mask, up, settings)
        if (record["support_area_m2"] < settings.min_support_area_m2 or record["height_m"] < settings.min_height_m
                or record["width_m"] < settings.min_width_m):
            labels[ids] = -1
            continue
        record["plane_id"] = len(planes)
        labels[ids] = len(planes)
        planes.append(record)
    if settings.merge_enabled:
        planes, labels, merging = merge_planes(
            planes, labels, triangles, areas, normals, up, settings, fit=_fit,
            record=lambda n, d, ids: _plane_record(n, d, ids, triangles, areas, wall_mask, up, settings))
        diagnostics['merging'] = merging
        if progress:
            progress(f"Plane merging: {merging['input_plane_count']} -> {merging['output_plane_count']}")
    else:
        diagnostics['merging'] = {'enabled': False}
    diagnostics["assigned_face_count"] = int((labels >= 0).sum())
    diagnostics["unassigned_face_count"] = int((labels < 0).sum())
    diagnostics["assigned_area_m2"] = float(areas[labels >= 0].sum())
    return {"planes": planes, "face_plane_id": labels, "diagnostics": diagnostics}
