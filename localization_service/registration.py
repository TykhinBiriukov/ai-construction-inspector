"""Experimental gravity-aligned, known-door room registration from genuine RGB-D.

Fits floor and two perpendicular walls. Door-centred first view is an explicit
initialization constraint. Rejects incomplete, inconsistent and ambiguous fits.
No dimensions are supplied by a language model. Thresholds are experimental.
"""
from pathlib import Path
import json
import numpy as np
from localization_service.core import rigid
from localization_service.openings import aperture_points

POLICY = 'known-door-rgbd-experimental-v1'


def backproject(frame, depth_bytes, confidence_bytes=None, stride=3):
    if frame.get('depth_source') != 'sceneDepth' or frame.get('depth_unit') != 'm':
        raise ValueError('Real sceneDepth in metres required')
    if frame.get('tracking_state') != 'normal': raise ValueError('Normal tracking required')
    w,h = frame['depth_size']; rw,rh = frame['rgb_size']
    if min(w,h,rw,rh)<=0 or w*h>1000000: raise ValueError('Invalid depth resolution')
    if len(depth_bytes)!=w*h*4: raise ValueError('Truncated depth')
    depth=np.frombuffer(depth_bytes,dtype='<f4').reshape(h,w)
    k=np.array(frame['K_rgb'], dtype=float)
    if k.shape!=(3,3) or not np.isfinite(k).all() or k[0,0]<=0 or k[1,1]<=0: raise ValueError('Invalid K')
    k=k.copy(); k[0]*=w/rw; k[1]*=h/rh
    v,u=np.mgrid[0:h:stride,0:w:stride]
    d=depth[::stride,::stride]
    valid=np.isfinite(d)&(d>.25)&(d<4.5)
    if confidence_bytes is not None:
        if len(confidence_bytes)!=w*h: raise ValueError('Truncated confidence')
        valid &= np.frombuffer(confidence_bytes,dtype='u1').reshape(h,w)[::stride,::stride]>=1
    # Image pixels: right/down/+depth. ARKit camera: right/up/-Z.
    pixels=np.stack([u[valid],v[valid],np.ones(valid.sum())],axis=1)
    camera=(pixels @ np.linalg.inv(k).T)*d[valid,None]
    camera[:,1:]*=-1
    t=np.array(rigid(frame['T_world_camera']))
    return camera @ t[:3,:3].T+t[:3,3]


def floor_plane(points, cameras):
    # Gravity gives horizontal plane orientation; a real floor must lie below every camera.
    y=points[:,1]
    candidates=y[(y<np.min(cameras[:,1])-.65)&(y>np.max(cameras[:,1])-2.4)]
    if len(candidates)<100: return None
    hist,edges=np.histogram(candidates,bins=np.arange(candidates.min()-.03,candidates.max()+.06,.03))
    for idx in np.argsort(hist)[::-1][:8]:
        center=(edges[idx]+edges[idx+1])/2
        mask=abs(y-center)<.045
        cluster=points[mask]
        if len(cluster)<max(150, .12*len(points)): continue
        # A horizontal slice through walls is not floor evidence. Require a concentrated Y peak.
        nearby=(abs(y-center)<.24)&(abs(y-center)>.075)
        if mask.sum() < 2.5 * nearby.sum() * (.09/.33): continue
        level=float(np.median(cluster[:,1])); span=np.quantile(cluster[:,[0,2]],.9,axis=0)-np.quantile(cluster[:,[0,2]],.1,axis=0)
        if min(span)>.4 and np.quantile(abs(cluster[:,1]-level),.9)<.045:
            return level
    return None


def wall_planes(points, cameras, floor):
    pts=points[(points[:,1]>floor+.2)&(points[:,1]<floor+2.7)]
    rng=np.random.default_rng(7359); planes=[]
    for _ in range(6):
        if len(pts)<180: break
        xy=pts[:,[0,2]]; best=None; count=0
        # Batched 2-D RANSAC (vertical planes), bounded for a laptop.
        pairs=rng.integers(0,len(xy),(240,2))
        for pair in pairs:
            line=xy[pair[1]]-xy[pair[0]]
            if np.linalg.norm(line)<.3:continue
            n=np.array([-line[1],line[0]])/np.linalg.norm(line); d=xy[pair[0]]@n
            mask=abs(xy@n-d)<.045; total=int(mask.sum())
            if total>count: best=mask;count=total
        if best is None or count<180:break
        cluster=pts[best]; coords=cluster[:,[0,2]]; center=np.median(coords,axis=0)
        _,vectors=np.linalg.eigh(np.cov(coords.T)); n=vectors[:,0]; d=float(np.median(coords@n))
        if np.mean(cameras[:,[0,2]]@n-d)<0:n=-n;d=-d
        tangent=np.array([-n[1],n[0]])
        if np.ptp(cluster[:,1])>.65 and np.ptp(coords@tangent)>.55:
            planes.append((n,d,count))
        pts=pts[abs(pts[:,[0,2]]@n-d)>.07]
    return planes


def fit_room(points, cameras, first_pose, room, observation_count):
    def more(text, **metrics):
        return {'status':'needs_more_evidence','instruction':text,'quality':dict(policy_version=POLICY,**metrics)}
    if observation_count<4:
        prompts=['Дверь целиком по центру кадра, немного стены вокруг и пол.',
                 'Снимите дверь вместе с ближайшим углом комнаты.',
                 'Сместитесь примерно на полметра в сторону; снимите ту же дверь и угол.',
                 'Широкий ракурс: пол и обе стены, образующие угол.']
        return more(prompts[observation_count],observations=observation_count)
    if len(points)<1000:return more('Недостаточно достоверной глубины. Подойдите ближе и снимите пол и угол.')
    floor=floor_plane(points,cameras)
    if floor is None:return more('Покажите больше пола у двери: не удаётся определить уровень пола.')
    planes=wall_planes(points,cameras,floor)
    if len(planes)<2:return more('Нужны две стены у угла. Снимите угол вместе с полом.')
    L,W,H=room['length'],room['width'],room['height']; off,dw=room['door_offset'],room['door_width']
    first=np.array(first_pose); forward=-first[:3,2]; candidates=[]
    # Which observed wall is the selected doorway? First camera must face it.
    for i,(n,d,_) in enumerate(planes):
        if forward[[0,2]]@n > -.55:continue
        # Rotation maps inward door-wall normal to +X; preserves Y and determinant +1.
        r=np.array([[n[0],0,n[1]],[0,1,0],[-n[1],0,n[0]]])
        for j,(other,od,_) in enumerate(planes):
            if i==j or abs(n@other)>.15:continue
            mapped=r@np.array([other[0],0,other[1]])
            if abs(mapped[2])<.98:continue
            target=0 if mapped[2]<0 else -W
            t=np.array([-d,-floor,(target-od+mapped[0]*d)/mapped[2]])
            camera=cameras@r.T+t
            if np.any(camera[:,0]<-.1) or np.any(camera[:,0]>L+.1) or np.any(camera[:,2]>.1) or np.any(camera[:,2]<-W-.1):continue
            if np.any(camera[:,1]<.55) or np.any(camera[:,1]>min(H-.1,2.5)):continue
            first_camera=r@first[:3,3]+t; direction=r@forward
            if direction[0]>-.4:continue
            distance=-first_camera[0]/direction[0]; aim=first_camera+distance*direction
            if not .25<distance<4.5 or not -off-dw-.3<aim[2]<-off+.3 or not -.2<aim[1]<room['door_height']+.3:continue
            cloud=points@r.T+t
            # Do not penalize measured points beyond an open doorway as wall deviations.
            portal=aperture_points(cloud,room,margin=.10)
            tested=cloud[~portal]
            if len(tested)<600:continue
            distances=np.stack([abs(tested[:,0]),abs(tested[:,0]-L),abs(tested[:,2]),abs(tested[:,2]+W),abs(tested[:,1])],axis=1)
            inside=(tested[:,0]>-.15)&(tested[:,0]<L+.15)&(tested[:,2]<.15)&(tested[:,2]>-W-.15)&(tested[:,1]>-.15)&(tested[:,1]<H+.15)
            best=distances.min(axis=1); inliers=(best<.075)&inside
            overlap=float(inliers.mean())
            counts=(distances<.075).sum(axis=0)
            if overlap<.48 or counts[0]<180 or counts[4]<100 or max(counts[2:4])<180:continue
            rmse=float(np.sqrt(np.mean(best[inliers]**2)))
            # Candidate fit is supported by floor, doorway wall and adjoining wall, not only low median distance.
            transform=np.eye(4);transform[:3,:3]=r;transform[:3,3]=t
            candidates.append({'T_bim_arkit':transform.tolist(),'overlap_fraction':overlap,'residual_rmse_m':rmse,
                               'score':overlap-rmse*2})
    if not candidates:return more('Привязка не подтверждена. Проверьте: первая фотография направлена в центр двери; доснимите пол и соседнюю стену.',planes=len(planes))
    candidates.sort(key=lambda c:c['score'],reverse=True)
    unique=[]
    for candidate in candidates:
        a=np.array(candidate['T_bim_arkit'])
        if not any(np.linalg.norm(a-np.array(b['T_bim_arkit']))<.18 for b in unique):unique.append(candidate)
    if len(unique)>1 and unique[0]['score']-unique[1]['score']<.12:
        return more('Есть несколько подходящих положений. Снимите дверь вместе с более широким участком соседней стены.',candidates=len(unique))
    best=unique[0]
    return {'status':'localized','T_bim_arkit':best['T_bim_arkit'],
            'quality':{'policy_version':POLICY,'overlap_fraction':best['overlap_fraction'],
                       'residual_rmse_m':best['residual_rmse_m'],'candidate_count':len(unique),
                       'initialization':'known door centered in first view; interior side',
                       'accuracy_validated_on_real_room':False}}


class RoomSolver:
    def __init__(self, room):self.room=room
    def solve(self,context,observations):
        if context['model_version']!=self.room['model_version']:raise ValueError('BIM revision mismatch')
        points=[]; cameras=[]; poses=[]; origins=set()
        for observation in observations:
            folder=Path(observation['_folder'])
            frame=json.loads((folder/'frame.json').read_text())
            origins.add(frame['capture_origin']);pose=np.array(rigid(frame['T_world_camera']))
            poses.append(pose);cameras.append(pose[:3,3])
            if frame.get('depth') is not None:
                confidence=(folder/'confidence.u8').read_bytes() if frame.get('confidence') else None
                points.append(backproject(frame,(folder/'depth.f32').read_bytes(),confidence))
        if origins != {'device'}:raise ValueError('Live test rejects synthetic input')
        if not points:return {'status':'needs_more_evidence','instruction':'Нужен iPhone с LiDAR: в снимках нет глубины.'}
        cloud=np.concatenate(points)
        if len(cloud)>20000:cloud=cloud[np.linspace(0,len(cloud)-1,20000).astype(int)]
        result=fit_room(cloud,np.array(cameras),poses[0],self.room,len(observations))
        result['evidence_observation_ids']=[o['observation_id'] for o in observations]
        result['analysis_origin']='live'
        return result
