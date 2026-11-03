"""Deterministic vertical-plane fitting used by semantic mesh contours."""
import numpy as np


def fit_frame_plane(points, *, threshold=.025, seed=0):
    """Deterministic RANSAC plus SVD; reject nonvertical and weak support."""
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 40 or not np.isfinite(points).all():
        raise ValueError('Not enough valid depth samples around the window frame')
    rng = np.random.default_rng(seed)
    best = np.zeros(len(points), dtype=bool)
    for _ in range(1000):
        a, b, c = points[rng.choice(len(points), 3, replace=False)]
        normal = np.cross(b-a, c-a)
        length = np.linalg.norm(normal)
        if length < 1e-8:
            continue
        normal /= length
        if abs(normal[1]) > .25:
            continue
        support = np.abs((points-a) @ normal) <= threshold
        if support.sum() > best.sum():
            best = support
    if best.sum() < 40 or best.mean() < .3:
        raise ValueError('No reliable vertical frame support plane')
    for _ in range(3):
        center = points[best].mean(axis=0)
        _, singular, vh = np.linalg.svd(points[best]-center, full_matrices=False)
        normal = vh[-1]
        if singular[1]/np.sqrt(best.sum()) < .08 or abs(normal[1]) > .25:
            raise ValueError('Frame plane support is collinear or not vertical')
        offset = -float(center @ normal)
        best = np.abs(points @ normal+offset) <= threshold
        if best.sum() < 40:
            raise ValueError('Frame plane lost support during refinement')
    distances = np.abs(points[best] @ normal+offset)
    return normal, offset, {'sample_count':len(points), 'inliers':int(best.sum()),
                            'rmse_m':float(np.sqrt(np.mean(distances**2))),
                            'p95_distance_m':float(np.percentile(distances,95))}
