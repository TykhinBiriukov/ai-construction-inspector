"""CPU visibility of existing mesh triangles in a calibrated camera.

Independent NumPy implementation of near-plane clipping and perspective-correct
z-buffer rasterization. Pixel centers use integer (u, v), matching this package's
existing render_mesh_depth and camera intrinsics. It does not reconstruct geometry.
"""

import numpy as np


MAX_IMAGE_PIXELS = 25_000_000
MAX_RASTER_SAMPLES = 250_000


def _inputs(vertices, faces, K, transform, width, height, near_m):
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces)
    K = np.asarray(K, dtype=np.float64)
    transform = np.asarray(transform, dtype=np.float64)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
        raise ValueError("Expected finite N x 3 mesh vertices")
    if (faces.ndim != 2 or faces.shape[1] != 3 or not np.issubdtype(faces.dtype, np.integer)
            or np.any(faces < 0) or np.any(faces >= len(vertices))
            or len(faces) >= np.iinfo(np.int32).max):
        raise ValueError("Expected valid M x 3 integer triangle indices")
    if (K.shape != (3, 3) or not np.isfinite(K).all() or K[0, 0] <= 0 or K[1, 1] <= 0
            or not np.allclose(K[2], [0, 0, 1], atol=1e-10, rtol=0)
            or not np.isclose(K[1, 0], 0, atol=1e-10, rtol=0)):
        raise ValueError("Expected a calibrated pinhole intrinsic matrix K")
    if (transform.shape != (4, 4) or not np.isfinite(transform).all()
            or not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-8, rtol=0)
            or not np.allclose(transform[:3, :3].T @ transform[:3, :3], np.eye(3), atol=1e-5, rtol=0)
            or not np.isclose(np.linalg.det(transform[:3, :3]), 1, atol=1e-5, rtol=0)):
        raise ValueError("Expected a rigid T_world_from_camera transform")
    if (isinstance(width, (bool, np.bool_)) or isinstance(height, (bool, np.bool_))
            or not isinstance(width, (int, np.integer)) or not isinstance(height, (int, np.integer))
            or width <= 0 or height <= 0 or int(width) * int(height) > MAX_IMAGE_PIXELS):
        raise ValueError("Image dimensions must be positive integers with at most 25 million pixels")
    if not np.isfinite(near_m) or near_m <= 0:
        raise ValueError("near_m must be positive and finite")
    return vertices, faces, K, transform, int(width), int(height)


def _clip_near(triangles, face_ids, near_m):
    """Clip a triangle to camera Z >= near_m, retaining its original face id."""
    inside = triangles[:, :, 2] >= near_m
    count = inside.sum(axis=1)
    parts = [triangles[count == 3]]
    identities = [face_ids[count == 3]]
    for n_inside in (1, 2):
        selected = count == n_inside
        subset = triangles[selected]
        if not len(subset):
            continue
        # Rotate so the unique inside (or outside) vertex comes first.
        pivot = np.argmax(inside[selected] if n_inside == 1 else ~inside[selected], axis=1)
        rows = np.arange(len(subset))
        a = subset[rows, pivot]
        b = subset[rows, (pivot+1) % 3]
        c = subset[rows, (pivot+2) % 3]
        ab = a + ((near_m-a[:, 2]) / (b[:, 2]-a[:, 2]))[:, None] * (b-a)
        ac = a + ((near_m-a[:, 2]) / (c[:, 2]-a[:, 2]))[:, None] * (c-a)
        ab[:, 2] = ac[:, 2] = near_m
        if n_inside == 1:
            parts.append(np.stack((a, ab, ac), axis=1))
            identities.append(face_ids[selected])
        else:
            parts.extend((np.stack((b, c, ac), axis=1), np.stack((b, ac, ab), axis=1)))
            identities.extend((face_ids[selected], face_ids[selected]))
    return np.concatenate(parts), np.concatenate(identities)


def render_visibility(vertices, faces, K, T_world_from_camera, width, height, *, near_m=0.05):
    """Return (camera-Z depth in metres, original face index) for every pixel.

    The camera axes must be right/down/forward. Empty pixels have depth=inf and
    face_id=-1. Both triangle windings are visible. Equal-depth ties use the lower
    original face index. Raster work arrays hold at most MAX_RASTER_SAMPLES
    bounding-box samples, including when one triangle fills the whole image.
    """
    vertices, faces, K, transform, width, height = _inputs(
        vertices, faces, K, T_world_from_camera, width, height, near_m)
    depth = np.full((height, width), np.inf)
    missing = np.iinfo(np.int32).max
    face_buffer = np.full((height, width), missing, dtype=np.int32)
    camera = (vertices-transform[:3, 3]) @ transform[:3, :3]
    triangles, face_ids = _clip_near(camera[faces], np.arange(len(faces), dtype=np.int32), near_m)
    if not len(triangles):
        face_buffer.fill(-1)
        return depth, face_buffer

    projected = triangles @ K.T
    uv = projected[:, :, :2] / projected[:, :, 2, None]
    if not np.isfinite(uv).all():
        raise ValueError("Projected coordinates are not finite")
    # Clamp floating-point bounds before converting, avoiding integer overflow
    # for near-clipped triangles extending far beyond the image.
    low_float, high_float = uv.min(axis=1), uv.max(axis=1)
    in_view = ((high_float[:, 0] >= 0) & (high_float[:, 1] >= 0)
               & (low_float[:, 0] <= width-1) & (low_float[:, 1] <= height-1))
    triangles, uv, face_ids = triangles[in_view], uv[in_view], face_ids[in_view]
    lower = np.ceil(np.clip(low_float[in_view], [0, 0], [width-1, height-1])).astype(np.int64)
    upper = np.floor(np.clip(high_float[in_view], [0, 0], [width-1, height-1])).astype(np.int64)
    x0, y0 = uv[:, 0, 0], uv[:, 0, 1]
    x1, y1 = uv[:, 1, 0], uv[:, 1, 1]
    x2, y2 = uv[:, 2, 0], uv[:, 2, 1]
    denominator = (y1-y2)*(x0-x2) + (x2-x1)*(y0-y2)
    keep = np.all(lower <= upper, axis=1) & (np.abs(denominator) > 1e-12)
    lower, upper, triangles, face_ids = lower[keep], upper[keep], triangles[keep], face_ids[keep]
    x0, y0, x1, y1, x2, y2, denominator = (
        values[keep] for values in (x0, y0, x1, y1, x2, y2, denominator))
    box_width = upper[:, 0]-lower[:, 0]+1
    sample_counts = box_width * (upper[:, 1]-lower[:, 1]+1)
    ends = np.cumsum(sample_counts, dtype=np.int64)
    starts = ends-sample_counts
    inverse_z = 1 / triangles[:, :, 2]
    flat_depth, flat_faces = depth.ravel(), face_buffer.ravel()
    total = int(ends[-1]) if len(ends) else 0
    for start in range(0, total, MAX_RASTER_SAMPLES):
        samples = np.arange(start, min(start+MAX_RASTER_SAMPLES, total), dtype=np.int64)
        identities = np.searchsorted(ends, samples, side="right")
        offsets = samples-starts[identities]
        u = lower[identities, 0] + offsets % box_width[identities]
        v = lower[identities, 1] + offsets // box_width[identities]
        a = ((y1[identities]-y2[identities])*(u-x2[identities])
             + (x2[identities]-x1[identities])*(v-y2[identities])) / denominator[identities]
        b = ((y2[identities]-y0[identities])*(u-x2[identities])
             + (x0[identities]-x2[identities])*(v-y2[identities])) / denominator[identities]
        inside = (a >= -1e-9) & (b >= -1e-9) & (a+b <= 1+1e-9)
        # Interpolate reciprocal Z, not Z. This form also gives exact constant
        # depth on frontoparallel triangles, stabilizing equal-depth ties.
        inv = (inverse_z[identities, 2]
               + a*(inverse_z[identities, 0]-inverse_z[identities, 2])
               + b*(inverse_z[identities, 1]-inverse_z[identities, 2]))
        inside &= np.isfinite(inv) & (inv > 0)
        pixels = (v[inside]*width+u[inside]).astype(np.int64)
        distances = 1 / inv[inside]
        original_ids = face_ids[identities[inside]]
        previous = flat_depth[pixels]
        np.minimum.at(flat_depth, pixels, distances)
        # A closer hit invalidates the face id stored by earlier sample chunks.
        flat_faces[pixels[distances < previous]] = missing
        winning = distances == flat_depth[pixels]
        np.minimum.at(flat_faces, pixels[winning], original_ids[winning])
    face_buffer[face_buffer == missing] = -1
    return depth, face_buffer
