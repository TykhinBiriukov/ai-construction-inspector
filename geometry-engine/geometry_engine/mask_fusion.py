"""Fuse projected observations on existing mesh faces with conservative labels.

Region IoU/recall and overlap graph merging adapt Open3DIS (BSD-3-Clause):
bc2a3b3ee7161f5754ad7a7fe77202e9a66adc8e, clustering_utils.py and clustering.py.
See THIRD_PARTY_NOTICES.md. This CPU adaptation uses surface area instead of
point counts and prevents same-frame instances joining through a graph chain.
It does not implement Open3DIS's learned features or full proposal pipeline.
"""

from collections import defaultdict
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FusionSettings:
    min_views: int = 2
    min_class_purity: float = 0.65
    instance_overlap: float = 0.3
    min_overlap_faces: int = 3

    def __post_init__(self):
        if type(self.min_views) is not int or self.min_views < 1:
            raise ValueError("min_views must be a positive integer")
        if type(self.min_overlap_faces) is not int or self.min_overlap_faces < 1:
            raise ValueError("min_overlap_faces must be a positive integer")
        if not np.isfinite(self.min_class_purity) or not .5 < self.min_class_purity <= 1:
            raise ValueError("min_class_purity must be greater than .5 and at most 1")
        if not np.isfinite(self.instance_overlap) or not 0 < self.instance_overlap <= 1:
            raise ValueError("instance_overlap must be in (0, 1]")


def _reduce_max(ids, weights):
    """Keep one strongest vote for each face; input order cannot add evidence."""
    order = np.argsort(ids, kind="stable")
    ids, weights = ids[order], weights[order]
    unique, first = np.unique(ids, return_index=True)
    return unique, np.maximum.reduceat(weights, first) if len(first) else weights


def _observations(observations, count):
    clean, seen = [], set()
    for observation in observations:
        identity, frame, label = (observation.get(key) for key in ("observation_id", "frame_id", "label"))
        if any(not isinstance(value, str) or not value for value in (identity, frame, label)) or identity in seen:
            raise ValueError("Each observation needs a unique observation_id, frame_id and label")
        seen.add(identity)
        score = float(observation.get("score", 0))
        ids = np.asarray(observation.get("face_ids", []))
        weights = np.asarray(observation.get("face_weights", np.ones(len(ids))), dtype=float)
        if (ids.ndim != 1 or (ids.size and not np.issubdtype(ids.dtype, np.integer))
                or weights.shape != ids.shape or not np.isfinite(weights).all()
                or np.any((weights < 0) | (weights > 1)) or np.any((ids < 0) | (ids >= count))
                or not np.isfinite(score) or not 0 <= score <= 1):
            raise ValueError(f"Invalid projected observation: {identity}")
        ids, weights = _reduce_max(ids.astype(np.int32), weights)
        positive = (weights > 0) & (score > 0)
        clean.append({"observation_id": identity, "frame_id": frame, "label": label,
                      "score": score, "face_ids": ids[positive], "face_weights": weights[positive]})
    return sorted(clean, key=lambda row: row["observation_id"])


def region_relation(a_ids, a_weights, b_ids, b_weights, face_areas):
    """Area-weighted IoU and two directional recalls on sorted unique face IDs.

    Adaptation of Open3DIS compute_relation_matrix_self_mem: weighted surface
    intersection replaces a binary point-mask dot product; union and directional
    denominators retain its IoU/precision/recall interpretation.
    """
    shared, ai, bi = np.intersect1d(a_ids, b_ids, assume_unique=True, return_indices=True)
    intersection = float(np.sum(face_areas[shared] * np.minimum(a_weights[ai], b_weights[bi])))
    a_mass = float(np.sum(face_areas[a_ids] * a_weights))
    b_mass = float(np.sum(face_areas[b_ids] * b_weights))
    union = a_mass + b_mass - intersection
    return (intersection / union if union > 0 else 0.,
            intersection / a_mass if a_mass > 0 else 0.,
            intersection / b_mass if b_mass > 0 else 0., len(shared))


def fuse_observations(faces, face_areas, observations, settings=None):
    """Return semantic labels and candidate instance IDs indexed by mesh face.

    Class evidence is capped at one maximum score*weight vote per frame/face/
    class. Scores here measure weighted agreement, not calibrated probabilities.
    No evidence propagates to unobserved faces, and no geometry is modified.
    """
    settings = settings or FusionSettings()
    faces, areas = np.asarray(faces), np.asarray(face_areas, dtype=float)
    if (faces.ndim != 2 or faces.shape[1] != 3 or not np.issubdtype(faces.dtype, np.integer)
            or np.any(faces < 0) or areas.shape != (len(faces),)
            or not np.isfinite(areas).all() or np.any(areas <= 0)):
        raise ValueError("Expected triangle indices and positive finite area per face")
    rows = _observations(observations, len(faces))
    labels = sorted({row["label"] for row in rows})
    classes = {label: index for index, label in enumerate(labels)}
    class_id = np.full(len(faces), -1, dtype=np.int32)
    instance_id = np.full(len(faces), -1, dtype=np.int32)
    confidence = np.zeros(len(faces), dtype=np.float32)
    view_count = np.zeros(len(faces), dtype=np.int32)
    result = {"face_class_id": class_id, "face_instance_id": instance_id,
              "face_confidence": confidence, "face_view_count": view_count,
              "labels": labels, "objects": [],
              "observation_instance_ids": {row["observation_id"]: -1 for row in rows},
              "class_conflict_count": 0, "insufficient_views_count": 0}
    if not rows or not len(faces):
        return result

    votes = np.zeros((len(labels), len(faces)), dtype=np.float64)
    views = np.zeros((len(labels), len(faces)), dtype=np.int32)
    groups = defaultdict(list)
    for row in rows:
        groups[row["frame_id"], row["label"]].append(row)
    for (_, label), group in groups.items():
        ids, weight = _reduce_max(np.concatenate([r["face_ids"] for r in group]),
                                 np.concatenate([r["face_weights"] * r["score"] for r in group]))
        votes[classes[label], ids] += weight
        views[classes[label], ids] += 1
    winner = votes.argmax(axis=0)
    indices = np.arange(len(faces))
    best = votes[winner, indices]
    total = votes.sum(axis=0)
    purity = np.divide(best, total, out=np.zeros(len(faces)), where=total > 0)
    view_count[:] = views[winner, indices]
    accepted = (view_count >= settings.min_views) & (purity >= settings.min_class_purity) & (best > 0)
    class_id[accepted] = winner[accepted]
    mean_strength = np.divide(best, view_count, out=np.zeros(len(faces)), where=view_count > 0)
    confidence[accepted] = (purity * mean_strength)[accepted]
    result["class_conflict_count"] = int(((total > 0) & (purity < settings.min_class_purity)).sum())
    result["insufficient_views_count"] = int(((total > 0) & (view_count < settings.min_views)).sum())

    # Only semantically accepted faces participate in cross-view associations.
    projected = []
    for row in rows:
        keep = class_id[row["face_ids"]] == classes[row["label"]]
        projected.append((row["face_ids"][keep], row["face_weights"][keep]))
    edges = []
    for a in range(len(rows)):
        if not len(projected[a][0]):
            continue
        for b in range(a+1, len(rows)):
            if rows[a]["label"] != rows[b]["label"] or rows[a]["frame_id"] == rows[b]["frame_id"]:
                continue
            iou, recall_a, recall_b, shared = region_relation(*projected[a], *projected[b], areas)
            # The high containment alternative follows Open3DIS's recall >= .9.
            if shared >= settings.min_overlap_faces and (iou >= settings.instance_overlap or max(recall_a, recall_b) >= .9):
                edges.append((-iou, -max(recall_a, recall_b), a, b))

    parents = list(range(len(rows)))
    frames = [{row["frame_id"]} for row in rows]
    def root(index):
        while index != parents[index]:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    for _, _, a, b in sorted(edges):
        a, b = root(a), root(b)
        if a == b or frames[a] & frames[b]:
            continue
        if b < a:
            a, b = b, a
        parents[b] = a
        frames[a] |= frames[b]
    clusters = defaultdict(list)
    for i in range(len(rows)):
        clusters[root(i)].append(i)

    best_instance = np.full(len(faces), -1, dtype=np.int32)
    top, instance_total = np.zeros(len(faces)), np.zeros(len(faces))
    candidate_groups = []
    for members in clusters.values():
        if len({rows[i]["frame_id"] for i in members}) < settings.min_views:
            continue
        ids = np.concatenate([projected[i][0] for i in members])
        weight = np.concatenate([projected[i][1] * rows[i]["score"] for i in members])
        unique, reverse = np.unique(ids, return_inverse=True)
        strength = np.bincount(reverse, weights=weight, minlength=len(unique))
        support = np.bincount(reverse, minlength=len(unique))
        supported = support >= settings.min_views
        unique, strength = unique[supported], strength[supported]
        group_id = len(candidate_groups)
        candidate_groups.append(members)
        stronger = strength > top[unique]
        instance_total[unique] += strength
        top[unique] = np.where(stronger, strength, top[unique])
        best_instance[unique[stronger]] = group_id
    agreement = np.divide(top, instance_total, out=np.zeros(len(faces)), where=instance_total > 0)
    for group_id, members in enumerate(candidate_groups):
        assigned = np.flatnonzero((best_instance == group_id) & (agreement >= settings.min_class_purity))
        if not len(assigned):
            continue
        identity = len(result["objects"])
        instance_id[assigned] = identity
        label = rows[members[0]]["label"]
        observation_ids = [rows[i]["observation_id"] for i in members]
        for oid in observation_ids:
            result["observation_instance_ids"][oid] = identity
        result["objects"].append({"instance_id": identity, "class_id": classes[label], "label": label,
                                  "status": "candidate", "face_count": len(assigned),
                                  "surface_area_m2": float(areas[assigned].sum()),
                                  "view_count": len({rows[i]["frame_id"] for i in members}),
                                  "observation_ids": observation_ids,
                                  "mean_confidence": float(np.average(confidence[assigned], weights=areas[assigned]))})
    return result
