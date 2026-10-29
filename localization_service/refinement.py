"""Bounded, one-shot rigid refinement; never deforms BIM or relaxes coverage.

A dominant wall proposes yaw and its normal translation. Independent frames
must support that wall AND improve a perpendicular wall. Ambiguous evidence
keeps the original alignment. This remains an experimental room estimator.
"""
import json
from pathlib import Path
import numpy as np
from localization_service.registration import backproject
from localization_service.openings import aperture_points


def wall_points(points, room, axis, plane):
    other = 2 if axis == 0 else 0
    lo, hi = (-room['width'], 0) if other == 2 else (0, room['length'])
    mask = ((points[:,1] > .7) & (points[:,1] < min(2.3, room['height']-.2)) &
            (points[:,other] > lo+.3) & (points[:,other] < hi-.3) &
            (abs(points[:,axis]-plane) < .30))
    return points[mask & ~aperture_points(points, room, margin=.10)]


def fit_plane(points, axis):
    if len(points) < 200: return None
    other = 2 if axis == 0 else 0
    x, y = points[:,other], points[:,axis]
    rng = np.random.default_rng(42); best = None; count = 0
    for _ in range(160):
        a,b = rng.choice(len(points), 2, replace=False)
        if abs(x[a]-x[b]) < 2: continue
        slope = (y[b]-y[a])/(x[b]-x[a]); offset = y[a]-slope*x[a]
        if abs(slope) > np.tan(np.radians(3)): continue
        mask = abs(y-slope*x-offset) < .025
        if mask.sum() > count: best, count = mask, int(mask.sum())
    if best is None or count < max(200, .45*len(points)): return None
    if np.ptp(x[best]) < 2.5: return None
    slope,offset = np.linalg.lstsq(np.column_stack([x[best],np.ones(count)]),y[best],rcond=None)[0]
    return float(slope),float(offset)


def propose(clouds, room):
    """Clouds already in the original BIM frame; returns correction or None."""
    if len(clouds) < 12: return None
    train = np.concatenate(clouds[::2]); held = np.concatenate(clouds[1::2])
    surfaces = [(0,0),(0,room['length']),(2,0),(2,-room['width'])]
    candidates = []
    for axis,plane in surfaces:
        a = wall_points(train,room,axis,plane); b = wall_points(held,room,axis,plane)
        fit = fit_plane(a,axis); check = fit_plane(b,axis)
        if fit is None or check is None: continue
        slope,offset = fit
        if abs(np.arctan(slope)-np.arctan(check[0])) > np.radians(.3): continue
        angle = np.arctan(slope)*(1 if axis==2 else -1)
        if not np.radians(.3) <= abs(angle) <= np.radians(3): continue
        co,si = np.cos(angle),np.sin(angle)
        correction = np.eye(4); correction[:3,:3] = [[co,0,si],[0,1,0],[-si,0,co]]
        correction[axis,3] = plane-offset*co
        if abs(correction[axis,3]) > .08: continue
        evidence = []; rejected = False
        # Fixed point membership prevents selecting different points after the fit.
        for ax,pl in surfaces:
            q = wall_points(held,room,ax,pl)
            if len(q)<200: continue
            old = abs(q[:,ax]-pl)
            new = abs((q@correction[:3,:3].T+correction[:3,3])[:,ax]-pl)
            before = float(np.mean(np.minimum(old,.15)))
            after = float(np.mean(np.minimum(new,.15)))
            if after > before+.01: rejected=True; break
            evidence.append({'axis':ax,'plane':pl,'before_m':before,'after_m':after,
                             'inlier_fraction':float(np.mean(new<=.06))})
        improved = [e for e in evidence if e['before_m']-e['after_m']>.015 and e['inlier_fraction']>.55]
        if rejected or {e['axis'] for e in improved}!={0,2}: continue
        # A single photo of a foreground plane must not validate a wall.
        supported = []
        for e in improved:
            views = 0
            for cloud in clouds[1::2]:
                q = wall_points(cloud,room,e['axis'],e['plane'])
                if len(q)<80: continue
                new = abs((q@correction[:3,:3].T+correction[:3,3])[:,e['axis']]-e['plane'])
                if np.mean(new<=.06)>.55: views += 1
            if views>=2: supported.append(e)
        improved = supported
        if {e['axis'] for e in improved}!={0,2}: continue
        # Proposal wall must improve too, not just incidental surfaces.
        if not any(e['axis']==axis and e['plane']==plane for e in improved): continue
        score = sum(e['before_m']-e['after_m'] for e in evidence)
        candidates.append((score,correction,dict(policy='held-out-rigid-v1',yaw_degrees=float(np.degrees(angle)),
                            validation_surfaces=evidence,frame_count=len(clouds))))
    if not candidates: return None
    candidates.sort(key=lambda x:x[0],reverse=True)
    best=candidates[0]
    for _,other,_ in candidates[1:]:
        relative = other@np.linalg.inv(best[1])
        if abs(np.arctan2(relative[0,2],relative[0,0]))>np.radians(.5) or np.linalg.norm(relative[:3,3])>.04:
            return None
    return best[1],best[2]


def refine(room, alignment, observations):
    if alignment.get('refinement') or len(observations)<12 or len(observations)%4: return None
    # One accepted correction per segment; bounded CPU and memory on free hosting.
    if len(observations)>48: return None
    transform=np.array(alignment['T_bim_arkit']); clouds=[]; cameras=[]
    for item in observations:
        folder=Path(item['_folder']); frame=json.loads((folder/'frame.json').read_text())
        if frame.get('capture_origin')!='device' or not frame.get('depth') or not frame.get('confidence'): return None
        confidence=np.frombuffer((folder/'confidence.u8').read_bytes(),dtype='u1').copy()
        confidence[confidence!=2]=0
        points=backproject(frame,(folder/'depth.f32').read_bytes(),confidence.tobytes(),stride=4)
        clouds.append(points@transform[:3,:3].T+transform[:3,3])
        cameras.append((transform@np.array(frame['T_world_camera']))[:3,3])
    result=propose(clouds,room)
    if result is None:return None
    correction,quality=result
    cameras=np.array(cameras)@correction[:3,:3].T+correction[:3,3]
    if (np.any(cameras[:,0]<-.15) or np.any(cameras[:,0]>room['length']+.15) or
        np.any(cameras[:,2]>.15) or np.any(cameras[:,2]<-room['width']-.15)):
        return None
    return dict(alignment,T_bim_arkit=(correction@transform).tolist(),refinement=quality,
                evidence_observation_ids=[o['observation_id'] for o in observations],
                initial_T_bim_arkit=alignment['T_bim_arkit'])
