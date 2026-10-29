"""Conservative grouping and refitting of RANSAC wall candidates.

Only assignments change; vertices are never moved. Tests use actual support
geometry, not the viewer's bounding rectangles. A possible step is preserved,
not classified as noise. This is a geometric heuristic, not multiview evidence.
"""
import math

import numpy as np
from scipy.spatial import cKDTree


def _angle(a, b):
    return math.degrees(math.acos(np.clip(abs(float(a @ b)), 0., 1.)))


def _edges(triangles):
    points, ids = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
    faces = ids.reshape(-1, 3)
    edges = np.sort(np.concatenate((faces[:, [0,1]], faces[:, [1,2]], faces[:, [2,0]])), axis=1)
    edges = np.unique(edges, axis=0)
    # Canonical endpoint coordinates allow matching across independently indexed patches.
    coordinates = np.ascontiguousarray(points[edges].reshape(-1, 6))
    keys = coordinates.view(np.dtype((np.void, coordinates.dtype.itemsize*6))).ravel()
    lengths = np.linalg.norm(points[edges[:,0]]-points[edges[:,1]], axis=1)
    return keys, lengths


def merge_planes(planes, labels, triangles, areas, normals, up, settings, *, fit, record):
    """Return new records, assignments and an auditable merge decision log.

    Every proposed union is checked against EACH original constituent, so a
    large region cannot overwhelm a small one or bridge an incompatible chain.
    Existing support is retained, including the permitted residual outliers.
    """
    original_ids = [np.flatnonzero(labels == i) for i in range(len(planes))]
    original_normals = [np.asarray(p['normal']) for p in planes]
    groups = [[i] for i in range(len(planes))]
    geometry = [np.concatenate((triangles[ids].reshape(-1, 3), triangles[ids].mean(axis=1)))
                for ids in original_ids]
    trees = [cKDTree(points) for points in geometry]
    neighbors = {}
    continuity = {}
    contacts = {}
    edges = [_edges(triangles[ids]) for ids in original_ids]
    for i in range(len(planes)):
        for j in range(i+1, len(planes)):
            _, shared, _ = np.intersect1d(edges[i][0], edges[j][0], return_indices=True)
            contacts[i, j] = float(edges[i][1][shared].sum())
            jumps = []
            for source, target in ((i, j), (j, i)):
                distances, nearest = trees[target].query(geometry[source], distance_upper_bound=settings.merge_max_gap_m)
                valid = np.isfinite(distances)
                delta = geometry[source][valid] - geometry[target][nearest[valid]]
                # Local separation along BOTH normals; tangential gaps are not steps.
                jumps.extend(np.maximum(np.abs(delta @ original_normals[i]),
                                        np.abs(delta @ original_normals[j])).tolist())
            neighbors[i, j] = bool(jumps)
            continuity[i, j] = float(np.quantile(jumps, .90)) if jumps else None
    decisions = []

    def proposal(left, right):
        members = sorted(left+right)
        if any(_angle(original_normals[i], original_normals[j]) > settings.merge_max_angle_deg
               for ii, i in enumerate(members) for j in members[ii+1:]):
            return None, 'angle', None
        if not any(neighbors[min(i,j), max(i,j)] for i in left for j in right):
            return None, 'support_not_adjacent', None
        ids = np.concatenate([original_ids[i] for i in members])
        model = fit(triangles, areas, ids)
        if model is None:
            return None, 'degenerate_fit', None
        n, offset = model
        if abs(float(n @ up)) > math.sin(math.radians(settings.max_vertical_tilt_deg)):
            return None, 'vertical_tilt', None
        fractions = {}
        for i in members:
            part = original_ids[i]
            # Requiring every vertex within tolerance is conservative for large faces.
            errors = np.max(np.abs(triangles[part] @ n + offset), axis=1)
            aligned = np.abs(normals[part] @ n) >= math.cos(math.radians(settings.normal_angle_deg))
            fractions[str(i)] = float(areas[part][(errors <= settings.merge_distance_m) & aligned].sum()/areas[part].sum())
        # Flatness is diagnostic. Grouping instead requires local continuity for
        # every neighboring pair, even when the pair is slightly angled.
        for ii, i in enumerate(members):
            for j in members[ii+1:]:
                jump = continuity[i, j]
                if (jump is not None and jump >= settings.merge_step_offset_m
                        and (contacts[i, j] < settings.merge_min_contact_length_m
                             or jump >= settings.merge_distance_m)):
                    return None, 'local_offset_possible_step_or_scan_error', fractions
        errors = (triangles[ids].mean(axis=1) @ n + offset)**2
        return (model, float(np.average(errors, weights=areas[ids]))), 'accepted', fractions

    # Prefer the best-fitting eligible union, then recompute after every merge.
    while True:
        choices = []
        for i in range(len(groups)):
            for j in range(i+1, len(groups)):
                candidate, reason, fractions = proposal(groups[i], groups[j])
                if candidate is not None:
                    choices.append((candidate[1], i, j, fractions))
        if not choices:
            break
        _, i, j, fractions = min(choices, key=lambda item: item[:3])
        merged = sorted(groups[i]+groups[j])
        decisions.append(dict(source_plane_ids=merged, decision='merged', area_fractions=fractions))
        groups[i] = merged
        del groups[j]
    # Log the final unresolved pairs, including rejected fits and possible steps.
    for i in range(len(groups)):
        for j in range(i+1, len(groups)):
            _, reason, fractions = proposal(groups[i], groups[j])
            decisions.append(dict(source_plane_ids=sorted(groups[i]+groups[j]),
                                  decision='kept_separate', reason=reason, area_fractions=fractions))
    result = []
    new_labels = np.full_like(labels, -1)
    for index, members in enumerate(groups):
        ids = np.concatenate([original_ids[i] for i in members])
        if len(members) > 1:
            n, offset = fit(triangles, areas, ids)
            item = record(n, offset, ids)
        else:
            item = dict(planes[members[0]])
        item.update(plane_id=index, source_plane_ids=members, merged=len(members)>1)
        n = np.asarray(item['normal'])
        offset = item['offset_m']
        deviations = np.max(np.abs(triangles[ids] @ n + offset), axis=1)
        order = np.argsort(deviations)
        cumulative = np.cumsum(areas[ids][order]) / areas[ids].sum()
        p95 = float(deviations[order[min(np.searchsorted(cumulative, .95), len(order)-1)]])
        fractions = {}
        for member in members:
            part = original_ids[member]
            residual = np.max(np.abs(triangles[part] @ n + offset), axis=1)
            fractions[str(member)] = float(areas[part][residual <= settings.merge_distance_m].sum()/areas[part].sum())
        item['approximation'] = dict(
            distance_tolerance_m=settings.merge_distance_m,
            constituent_area_fractions=fractions,
            exceeds_flatness_tolerance=min(fractions.values()) < settings.merge_min_area_fraction,
            area_weighted_p95_max_vertex_distance_m=p95,
            max_vertex_distance_m=float(deviations.max()),
            interpretation='reference_plane_not_a_flattened_or_verified_physical_wall')
        result.append(item)
        new_labels[ids] = index
    return result, new_labels, dict(enabled=True, input_plane_count=len(planes),
        output_plane_count=len(result), decisions=decisions,
        support_policy='retain_original_support_without_moving_or_discarding_faces',
        continuity_pairs=[dict(source_plane_ids=list(pair), normal_separation_p90_m=value, shared_edge_length_m=contacts[pair])
                          for pair, value in continuity.items()],
        step_check='local_separation_and_shared_mesh_edges_geometry_heuristic',
        flatness_policy='report_deviations_without_rejecting_continuous_surface')
