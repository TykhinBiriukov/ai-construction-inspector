"""Automatic metric scan/BIM registration using Open3D robust point-to-plane ICP.

Inputs are already metres and Z-up. Dominant surface normals provide four yaw
hypotheses; ICP estimates one rigid transform per hypothesis (never scale). A
separate area-sampled evaluation set scores nearest triangle distances, normal
agreement and geometric observability. Competing poses cause rejection.

Uses the Open3D robust-kernel and closest-point APIs documented at:
https://www.open3d.org/docs/release/tutorial/pipelines/robust_kernels.html
https://www.open3d.org/docs/release/tutorial/geometry/distance_queries.html
This is an indoor-room heuristic, not an implementation of 4PlCS.
"""
import numpy as np


def _geometry(vertices, faces):
    vertices, faces = np.asarray(vertices, dtype=float), np.asarray(faces)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all()
            or faces.ndim != 2 or faces.shape[1] != 3 or not len(faces)
            or not np.issubdtype(faces.dtype, np.integer)
            or faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError('Expected finite vertices and valid triangle indices')
    triangle = vertices[faces]
    cross = np.cross(triangle[:, 1]-triangle[:, 0], triangle[:, 2]-triangle[:, 0])
    length = np.linalg.norm(cross, axis=1)
    normals = cross/np.maximum(length[:, None], 1e-15)
    return triangle, normals, length/2


def _sample(triangles, normals, areas, mask, count, rng):
    indices = np.flatnonzero(mask & (areas > 1e-12))
    if not len(indices):
        raise ValueError('No nondegenerate structural surfaces available')
    probability = areas[indices]/areas[indices].sum()
    ids = rng.choice(indices, count, replace=True, p=probability)
    bary = rng.random((count, 2))
    flip = bary.sum(axis=1) > 1
    bary[flip] = 1-bary[flip]
    t = triangles[ids]
    points = t[:, 0]+bary[:, :1]*(t[:, 1]-t[:, 0])+bary[:, 1:]*(t[:, 2]-t[:, 0])
    return points, normals[ids]


def _dominant_yaw(normals, weights):
    keep = np.abs(normals[:, 2]) < .3
    if not keep.any():
        raise ValueError('Registration requires vertical structural surfaces')
    theta = np.arctan2(normals[keep, 1], normals[keep, 0])
    moment = np.sum(weights[keep]*np.exp(4j*theta))
    if abs(moment)/max(weights[keep].sum(), 1e-12) < .35:
        raise ValueError('No dominant orthogonal wall directions for automatic initialization')
    return float(np.angle(moment)/4)


def _pose_distinct(a, b):
    angle = np.arccos(np.clip((np.trace(a[:3, :3].T@b[:3, :3])-1)/2, -1, 1))
    return bool(angle > np.deg2rad(10) or np.linalg.norm(a[:3, 3]-b[:3, 3]) > .3)


def _decide(candidates):
    """Acceptance gates are explicit heuristics, not calibrated probabilities."""
    candidates.sort(key=lambda c: c['score'], reverse=True)
    best = candidates[0]
    if (best['inlier_fraction'] < .60 or best['inlier_rmse_m'] > .065
            or best['normal_alignment_fraction'] < .65):
        return False, 'insufficient_surface_agreement'
    if best['normal_observability_min_eigenvalue'] < .02:
        return False, 'insufficient_independent_surface_directions'
    if best['tilt_deg'] > 8:
        return False, 'gravity_alignment_inconsistent'
    for other in candidates[1:]:
        if not _pose_distinct(np.array(best['transform']), np.array(other['transform'])):
            continue
        if (best['score']-other['score'] < .04
                and other['inlier_fraction'] >= .55):
            return False, 'ambiguous_competing_alignment'
    return True, 'surface_agreement_and_uniqueness_checks_passed'


def register_scan(source_vertices, source_faces, reference_vertices, reference_faces, *,
                  source_wall_mask=None, source_floor_mask=None, seed=7):
    """Return a serializable registration result, including rejected hypotheses.

    Face masks must index source_faces. Without labels, candidate surfaces are
    selected by normals only; this cannot reliably remove furniture. Refuse to
    publish measurements from a result whose accepted flag is false.
    """
    import open3d as o3d
    st, sn, sa = _geometry(source_vertices, source_faces)
    rt, rn, ra = _geometry(reference_vertices, reference_faces)
    rng = np.random.default_rng(seed)
    count = len(source_faces)
    supplied = source_wall_mask is not None or source_floor_mask is not None
    def mask(value, fallback):
        if value is None:
            return fallback if not supplied else np.zeros(count, dtype=bool)
        result = np.asarray(value)
        if result.dtype != bool or result.shape != (count,):
            raise ValueError('Structural source masks must be boolean arrays indexed by face')
        return result
    wall_mask = mask(source_wall_mask, np.abs(sn[:, 2]) < .3)
    floor_mask = mask(source_floor_mask, (np.abs(sn[:, 2]) > .9)
                      & (st.mean(axis=1)[:, 2] < np.quantile(st.mean(axis=1)[:, 2], .2)))
    structural = wall_mask | floor_mask
    base = {'method':'dominant_normals_multistart_robust_point_to_plane_icp',
            'coordinate_system':'Z_up_metres', 'scale':1., 'semantic_source_masks_used':supplied,
            'evaluation':'separate area-sampled surface points, not independent acquisition data',
            'thresholds':{'distance_m':.08,'min_inlier_fraction':.60,'max_inlier_rmse_m':.065,
                          'min_normal_alignment_fraction':.65,'min_observability_eigenvalue':.02,
                          'competing_score_margin':.04,'max_gravity_tilt_deg':8},
            'limitations':['Single predominantly orthogonal room; full building registration is unsupported.',
                'No semantic labels means furniture may influence registration.',
                'Global alignment can absorb a uniform offset; no per-object refitting is performed.']}
    def failure(reason):
        return {**base,'accepted':False,'reason':reason,'transform':np.eye(4).tolist(),'candidates':[]}
    if not wall_mask.any() or not floor_mask.any():
        return failure('walls_and_floor_required_for_automatic_initialization')
    try:
        source_yaw = _dominant_yaw(sn[wall_mask], sa[wall_mask])
        reference_yaw = _dominant_yaw(rn, ra)
    except ValueError as error:
        return failure(str(error))
    train, train_normals = _sample(st,sn,sa,structural,5000,rng)
    held, held_normals = _sample(st,sn,sa,structural,6000,rng)
    target, target_normals = _sample(rt,rn,ra,ra>0,16000,rng)
    floor_points, _ = _sample(st,sn,sa,floor_mask,1000,rng)
    source_floor = float(np.median(floor_points[:, 2]))
    centers = rt.mean(axis=1)
    # IFC normals orient solid surfaces. Among upward horizontal faces, the
    # lowest large level is the top of the floor rather than slab underside.
    upward = (rn[:,2]>.9)&(ra>1e-8)
    if not upward.any():
        return failure('reference_has_no_upward_floor_surface')
    levels = centers[upward,2]
    lowest = levels.min()
    floor_levels = upward & (centers[:,2] < lowest+.05)
    target_floor = float(np.average(centers[floor_levels,2],weights=ra[floor_levels]))
    source_cloud=o3d.geometry.PointCloud(o3d.utility.Vector3dVector(train))
    source_cloud.normals=o3d.utility.Vector3dVector(train_normals)
    target_cloud=o3d.geometry.PointCloud(o3d.utility.Vector3dVector(target))
    target_cloud.normals=o3d.utility.Vector3dVector(target_normals)
    ray_scene=o3d.t.geometry.RaycastingScene(nthreads=2)
    ray_scene.add_triangles(o3d.core.Tensor(np.asarray(reference_vertices,dtype=np.float32)),
                           o3d.core.Tensor(np.asarray(reference_faces,dtype=np.uint32)))
    target_center=(np.quantile(target[:,:2],.005,axis=0)+np.quantile(target[:,:2],.995,axis=0))/2
    candidates=[]
    for k in range(4):
        angle=reference_yaw-source_yaw+k*np.pi/2
        c,s=np.cos(angle),np.sin(angle)
        rotation=np.array([[c,-s,0],[s,c,0],[0,0,1.]])
        rotated=train@rotation.T
        source_center=(np.quantile(rotated[:,:2],.005,axis=0)+np.quantile(rotated[:,:2],.995,axis=0))/2
        transform=np.eye(4);transform[:3,:3]=rotation
        transform[:2,3]=target_center-source_center
        transform[2,3]=target_floor-source_floor
        for distance in (.35,.18,.10):
            loss=o3d.pipelines.registration.TukeyLoss(k=distance*.7)
            estimate=o3d.pipelines.registration.TransformationEstimationPointToPlane(loss)
            fit=o3d.pipelines.registration.registration_icp(source_cloud,target_cloud,distance,transform,
                estimate,o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=35))
            transform=np.asarray(fit.transformation)
        moved=held@transform[:3,:3].T+transform[:3,3]
        closest=ray_scene.compute_closest_points(o3d.core.Tensor(moved.astype(np.float32)),nthreads=2)
        nearby=closest['points'].numpy();normal=closest['primitive_normals'].numpy()
        distances=np.linalg.norm(moved-nearby,axis=1)
        alignment=np.abs(np.einsum('ij,ij->i',held_normals@transform[:3,:3].T,normal))
        inliers=(distances<=.08)&(alignment>=np.cos(np.deg2rad(25)))
        fraction=float(inliers.mean())
        rmse=float(np.sqrt(np.mean(distances[inliers]**2))) if inliers.any() else 1.
        covariance=normal[inliers].T@normal[inliers]/max(int(inliers.sum()),1)
        observable=float(np.linalg.eigvalsh(covariance).min())
        candidate={'seed_yaw_deg':float(np.rad2deg(angle)%360),'transform':transform.tolist(),
            'inlier_fraction':fraction,'inlier_rmse_m':rmse,
            'normal_alignment_fraction':float((alignment>=np.cos(np.deg2rad(25))).mean()),
            'distance_p50_m':float(np.median(distances)),
            'distance_p95_m':float(np.percentile(distances,95)),
            'normal_observability_min_eigenvalue':observable,
            'tilt_deg':float(np.rad2deg(np.arccos(np.clip(transform[2,2],-1,1)))),
            'score':fraction-.2*float(np.mean(np.minimum(distances,.2))/.2)}
        candidates.append(candidate)
    accepted,reason=_decide(candidates)
    return {**base,'accepted':accepted,'reason':reason,'transform':candidates[0]['transform'],
            'candidates':candidates,'sample_counts':{'training':len(train),'evaluation':len(held),'reference':len(target)}}
