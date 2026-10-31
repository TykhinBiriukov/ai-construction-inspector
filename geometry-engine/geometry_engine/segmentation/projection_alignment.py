"""Fit projection cameras to immutable mesh geometry, never reconstruct geometry.

Open3D point-to-plane ICP supplies proposals, which must pass held-out spatial
depth checks. These are consistency checks against a fused mesh, not independent
survey accuracy estimates; that mesh includes observations from these cameras.
"""
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from scipy.ndimage import minimum_filter, maximum_filter

from geometry_engine.common.mesh import read_mesh
from geometry_engine.capture.session_layout import prepare_session, original_directory, session_reference
from geometry_engine.capture.validation import read_object, session_file
from geometry_engine.segmentation.service import _rename
from geometry_engine.segmentation.mask_projection import camera_parameters, load_depth, file_hash, save_json


@dataclass(frozen=True)
class ProjectionAlignmentSettings:
    min_depth_m: float = .15
    max_depth_m: float = 5.
    min_confidence: int = 1
    max_translation_m: float = .20
    max_rotation_deg: float = 3.
    min_coverage: float = .60
    min_agreement: float = .75
    max_median_m: float = .04
    max_p90_m: float = .12
    max_local_regression_m: float = .02
    min_improvement_m: float = .005

    def __post_init__(self):
        for key, value in asdict(self).items():
            if not np.isfinite(value) or value <= 0:
                raise ValueError('Invalid setting: '+key)
        if self.min_depth_m >= self.max_depth_m or self.min_coverage > 1 or self.min_agreement > 1:
            raise ValueError('Invalid depth or fraction limits')
        if type(self.min_confidence) is not int or self.min_confidence not in (1, 2):
            raise ValueError('Expected confidence 1 or 2')


def _rigid(value):
    T=np.asarray(value, dtype=float)
    if (T.shape != (4,4) or not np.isfinite(T).all()
            or not np.allclose(T[3], [0,0,0,1], atol=1e-7)
            or not np.allclose(T[:3,:3].T@T[:3,:3],np.eye(3),atol=1e-5)
            or not np.isclose(np.linalg.det(T[:3,:3]),1,atol=1e-5)):
        raise ValueError('Expected rigid camera transform')
    return T


def _partition(shape):
    y,x=np.indices(shape)
    # Entire 16x16 blocks held out, not adjacent alternating pixels.
    held=(x//16+y//16)%2==1
    sectors=(np.minimum(y*3//shape[0],2)*3+np.minimum(x*3//shape[1],2))
    return held,sectors


def depth_metrics(depth, rendered, valid, selection, sectors):
    eligible=valid&selection
    comparable=eligible&np.isfinite(rendered)&(rendered>0)
    count=int(comparable.sum())
    residual=np.abs(rendered[comparable]-depth[comparable])
    metrics=dict(samples=count,coverage=count/max(1,int(eligible.sum())),
        median_m=float(np.median(residual)) if count else None,
        p90_m=float(np.quantile(residual,.9)) if count else None,
        agreement=float(np.mean(residual<.05+.01*depth[comparable])) if count else 0.,tiles=[])
    for tile in range(9):
        sel=eligible&(sectors==tile);hit=comparable&sel;n=int(hit.sum())
        if int(sel.sum())<80:continue
        metrics['tiles'].append(dict(tile=tile,samples=n,coverage=n/max(1,int(sel.sum())),
            median_m=float(np.median(abs(rendered[hit]-depth[hit]))) if n else None,
            agreement=float(np.mean(abs(rendered[hit]-depth[hit])<.05+.01*depth[hit])) if n else 0.))
    return metrics


def _adequate(m,s):
    if (m['samples']<500 or m['coverage']<s.min_coverage or m['median_m'] is None
            or m['median_m']>s.max_median_m or m['p90_m']>s.max_p90_m or m['agreement']<s.min_agreement):return False
    # Do not let a large correctly aligned wall hide a bad region.
    return len(m['tiles'])>=3 and all(t['samples']>=40 and t['coverage']>=.40
        and t['median_m'] is not None and t['median_m']<=.075 and t['agreement']>=.55 for t in m['tiles'])


def choose_camera(before, after, translation, rotation, observable, settings=None):
    """Gate proposal with independent pixels; reject fallback unless raw also passes."""
    s=settings or ProjectionAlignmentSettings()
    raw_ok=all(_adequate(before[k],s) for k in ('train','held_out'))
    reasons=[]
    if translation>s.max_translation_m or rotation>s.max_rotation_deg:reasons.append('correction_exceeds_bound')
    if not observable:reasons.append('underconstrained_geometry')
    if not all(_adequate(after[k],s) for k in ('train','held_out')):reasons.append('depth_validation_failed')
    for key in ('train','held_out'):
        a,b=before[key],after[key]
        if b['coverage']<a['coverage']-.03:reasons.append('coverage_decreased')
        old={x['tile']:x for x in a['tiles']}
        for tile in b['tiles']:
            prior=old.get(tile['tile'])
            if prior and prior['samples']>=80 and tile['samples']>=80:
                if tile['median_m']>prior['median_m']+s.max_local_regression_m or tile['agreement']<prior['agreement']-.10:
                    reasons.append('local_depth_regression')
    a,b=before['held_out']['median_m'],after['held_out']['median_m']
    if a is None or b is None or a-b<s.min_improvement_m:reasons.append('insufficient_held_out_improvement')
    if not reasons:return 'accepted_refined',[]
    return ('accepted_raw' if raw_ok else 'rejected'),sorted(set(reasons))


def _rays(K,shape):
    y,x=np.indices(shape)
    return np.stack(((x-K[0,2])/K[0,0],(y-K[1,2])/K[1,1],np.ones(shape)),axis=-1)


def _render(scene,rays,T,o3d):
    directions=rays@T[:3,:3].T
    origins=np.broadcast_to(T[:3,3],directions.shape)
    result=scene.cast_rays(o3d.core.Tensor(np.concatenate((origins,directions),axis=-1).astype('float32')))
    # Non-normalized ray direction z=1 makes t_hit camera-Z, not range.
    return result['t_hit'].numpy(),result['primitive_normals'].numpy()


def _observable(points,normals):
    if len(points)<100:return False,[]
    p=points-points.mean(0);scale=max(.1,float(np.sqrt(np.mean(np.sum(p*p,axis=1)))))
    J=np.column_stack((np.cross(p/scale,normals),normals))
    eig=np.linalg.eigvalsh(J.T@J/max(1,len(J)))
    return bool(eig[0]>.0005 and eig[0]/max(eig[-1],1e-12)>.001),eig.tolist()


def align_frame(scene,K,raw,depth,valid,o3d,settings=None):
    s=settings or ProjectionAlignmentSettings();raw=_rigid(raw)
    rays=_rays(K,depth.shape);held,sectors=_partition(depth.shape)
    initial,_=_render(scene,rays,raw,o3d)
    def metrics(rendered):return {name:depth_metrics(depth,rendered,valid,selection,sectors)
        for name,selection in [('train',~held),('held_out',held)]}
    before=metrics(initial);T=raw.copy();reg=o3d.pipelines.registration
    source=o3d.geometry.PointCloud(o3d.utility.Vector3dVector((rays*depth[...,None])[valid&~held]))
    observable=False;eigenvalues=[]
    if len(source.points)>=500:
        for distance in (.15,.08,.05):
            rendered,normals=_render(scene,rays,T,o3d)
            visible=valid&~held&np.isfinite(rendered)&(rendered<s.max_depth_m)
            # Fit only surfaces seen from this camera; never hidden back surfaces.
            visible&=abs(rendered-depth)<.25
            points=(rays[visible]*rendered[visible,None])@T[:3,:3].T+T[:3,3]
            target=o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
            target.normals=o3d.utility.Vector3dVector(normals[visible])
            observable,eigenvalues=_observable(points,normals[visible])
            if not observable:break
            fit=reg.registration_icp(source.voxel_down_sample(.025),target.voxel_down_sample(.025),distance,T,
                reg.TransformationEstimationPointToPlane(reg.TukeyLoss(k=distance)),
                reg.ICPConvergenceCriteria(max_iteration=25))
            T=_rigid(fit.transformation)
    final,_=_render(scene,rays,T,o3d);after=metrics(final)
    translation=float(np.linalg.norm(T[:3,3]-raw[:3,3]))
    rotation=float(np.degrees(np.arccos(np.clip((np.trace(T[:3,:3]@raw[:3,:3].T)-1)/2,-1,1))))
    status,reasons=choose_camera(before,after,translation,rotation,observable,s)
    result=dict(status=status,reasons=reasons,before=before,proposal=after,correction_translation_m=translation,
        correction_rotation_deg=rotation,observable=observable,information_eigenvalues=eigenvalues)
    if status!='rejected':result['T_world_from_camera_cv']=(T if status=='accepted_refined' else raw).tolist()
    return result


class ProjectionAlignmentService:
    def __init__(self,settings=None):self.settings=settings or ProjectionAlignmentSettings()

    def align(self,directory,output_dir=None,*,segmentation_dir=None,mesh_dir=None,max_frames=None,progress=None):
        import open3d as o3d
        session=prepare_session(directory);original=original_directory(session)
        output=Path(output_dir).resolve() if output_dir else session/'projection_alignment'
        if output.parent!=session or output.exists():raise ValueError('Use a new stage inside session')
        prepared=Path(mesh_dir).resolve() if mesh_dir else session/'mesh_preprocessing'
        segmentation=Path(segmentation_dir).resolve() if segmentation_dir else session/'segmentation'
        if not prepared.is_relative_to(session) or not segmentation.is_relative_to(session):raise ValueError('Stages must be inside session')
        mesh=prepared/'mesh.ply';v,_,f=read_mesh(mesh)
        capture_path=original/('scene.json' if (original/'scene.json').exists() else 'capture.json')
        capture=read_object(capture_path)
        coordinates=capture.get('coordinate_system')
        unit=capture.get('length_unit') if coordinates=='arkit' else coordinates.get('length_unit') if isinstance(coordinates,dict) else None
        if unit!='meter':raise ValueError('Projection mesh must be in metres')
        manifest=read_object(segmentation/'detections.json')
        if manifest.get('schema_version')!='1.0' or manifest.get('source_path_base')!='session_directory':
            raise ValueError('Unsupported segmentation manifest')
        if max_frames is not None and (type(max_frames) is not int or max_frames<1):raise ValueError('Invalid max_frames')
        frames=manifest['frames'][:max_frames]
        if not frames:raise ValueError('No selected frames')
        scene=o3d.t.geometry.RaycastingScene(nthreads=2)
        scene.add_triangles(o3d.core.Tensor(v.astype('float32')),o3d.core.Tensor(f.astype('uint32')))
        fingerprints={}
        def track(path):fingerprints[session_reference(path,session)]=file_hash(path)
        for p in [mesh,capture_path,segmentation/'detections.json']:track(p)
        rows=[];seen=set()
        for index,frame in enumerate(frames):
            identity=frame['frame_id']
            if identity in seen:raise ValueError('Duplicate frame ID')
            seen.add(identity)
            path=session_file(original,session,frame['metadata']);metadata=read_object(path);track(path)
            if file_hash(path)!=frame['metadata_sha256']:raise ValueError('Segmentation metadata changed')
            if metadata.get('frame_id')!=identity:raise ValueError('Frame ID differs from metadata')
            rgb=session_file(original,path.parent,metadata['rgb']['path'])
            if file_hash(rgb)!=frame['rgb_sha256']:raise ValueError('Segmentation RGB changed')
            K,T=camera_parameters(metadata,capture['coordinate_system'])
            width,height=metadata['depth']['width'],metadata['depth']['height']
            size=(width,height)
            K=np.diag([width/metadata['rgb']['width'],height/metadata['rgb']['height'],1.])@K
            depth,confidence,_=load_depth(original,path,metadata,size)
            for name in ['rgb','depth','confidence']:
                if metadata.get(name):track(session_file(original,path.parent,metadata[name]['path']))
            valid=np.isfinite(depth)&(depth>=self.settings.min_depth_m)&(depth<=self.settings.max_depth_m)
            if confidence is not None:valid&=confidence>=self.settings.min_confidence
            # Exclude depth discontinuities from fitting and validation.
            valid&=(maximum_filter(depth,3)-minimum_filter(depth,3))<.10
            safe=np.where(valid,depth,0.)
            row=align_frame(scene,K,T,safe,valid,o3d,self.settings);row['frame_id']=identity
            rows.append(row)
            if progress:progress(f'Projection camera {index+1}/{len(frames)}: {row["status"]}')
        for ref,digest in fingerprints.items():
            if file_hash(session_file(session,session,ref))!=digest:raise ValueError('Input changed during alignment')
        with TemporaryDirectory(prefix='.projection-alignment-',dir=session) as tmp:
            stage=Path(tmp)/'result';stage.mkdir()
            save_json(stage/'cameras.json',dict(schema_version='1.0',unit='meter',camera_axes='right_down_forward',frames=rows))
            report=dict(status='completed',settings=asdict(self.settings),mesh_sha256=file_hash(mesh),
                cameras_sha256=file_hash(stage/'cameras.json'),input_fingerprints=fingerprints,
                counts={k:sum(r['status']==k for r in rows) for k in ['accepted_raw','accepted_refined','rejected']},
                limitations=['Geometry and original cameras are immutable; poses affect mask projection only.',
                    'Held-out pixels do not provide independent acquisition ground truth: mesh includes these frames.',
                    'A fused mesh can contain contradictory surfaces; passing these checks does not certify dimensions.',
                    'Rejected frames must be skipped; projection cannot silently fall back to raw cameras.'])
            save_json(stage/'report.json',report);_rename(stage,output)
        return report


def load_projection_cameras(stage,session,mesh_path):
    stage=Path(stage).resolve();session=Path(session).resolve()
    if not stage.is_relative_to(session):raise ValueError('Projection stage outside session')
    report=read_object(stage/'report.json')
    if report.get('status')!='completed' or report.get('mesh_sha256')!=file_hash(mesh_path):raise ValueError('Projection mesh differs')
    if report.get('cameras_sha256')!=file_hash(stage/'cameras.json'):raise ValueError('Projection cameras changed')
    for ref,digest in report['input_fingerprints'].items():
        if file_hash(session_file(session,session,ref))!=digest:raise ValueError('Projection input changed: '+ref)
    data=read_object(stage/'cameras.json')
    if data.get('schema_version')!='1.0' or data.get('unit')!='meter' or data.get('camera_axes')!='right_down_forward':raise ValueError('Unsupported projection cameras')
    result={}
    for row in data['frames']:
        if row['frame_id'] in result or row['status'] not in ('accepted_raw','accepted_refined','rejected'):raise ValueError('Invalid camera record')
        if row['status']!='rejected':_rigid(row['T_world_from_camera_cv'])
        elif 'T_world_from_camera_cv' in row:raise ValueError('Rejected camera must not supply pose')
        result[row['frame_id']]=row
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('session');p.add_argument('--output');p.add_argument('--max-frames',type=int)
    a=p.parse_args();print(json.dumps(ProjectionAlignmentService().align(a.session,a.output,max_frames=a.max_frames,progress=print)['counts']))
