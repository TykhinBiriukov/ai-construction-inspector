"""Scan-vs-BIM stage: rigid registration, semantic matching, observed deviations.

Uses IfcOpenShell geometry and Open3D robust ICP / RaycastingScene queries.
No geometry is fitted per object, no missing surfaces are completed, and no
measurement is published after registration rejection. Windows/doors are deferred.
"""
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from geometry_engine.comparison.bim_reference import load_bim_reference, interior_faces
from geometry_engine.comparison.bim_registration import register_scan
from geometry_engine.common.mesh import read_mesh, write_mesh
from geometry_engine.visualization.scan_bim_viewer import export_scan_bim_viewer


def _hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def _save(path, value):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    temp.replace(path)


@dataclass(frozen=True)
class ScanVsBimSettings:
    tolerance_m: float = .03
    max_match_distance_m: float = .30
    normal_angle_deg: float = 25.
    max_tangential_distance_m: float = .02
    min_observed_area_m2: float = .25
    min_views: int = 2
    seed: int = 7

    def __post_init__(self):
        vals=[self.tolerance_m,self.max_match_distance_m,self.normal_angle_deg,
              self.max_tangential_distance_m,self.min_observed_area_m2]
        if (not np.isfinite(vals).all() or min(vals)<=0 or self.normal_angle_deg>=90
                or self.tolerance_m>=self.max_match_distance_m
                or type(self.min_views) is not int or self.min_views<1
                or type(self.seed) is not int or self.seed<0):
            raise ValueError('Invalid scan-vs-BIM thresholds')


def source_to_z_up(coordinates):
    if coordinates=='arkit':
        # Right-handed rigid basis change: (x,y,z) -> (x,-z,y).
        return np.array([[1.,0,0],[0,0,-1],[0,1,0]])
    if isinstance(coordinates,dict) and coordinates.get('up_axis')=='z' and coordinates.get('length_unit')=='meter':
        return np.eye(3)
    raise ValueError('Expected explicit ARKit or metre Z-up capture coordinates')


def load_semantics(directory, mesh_path, face_count, min_views):
    directory=Path(directory)
    report=json.loads((directory/'report.json').read_text(encoding='utf-8'))
    expected=report.get('source_mesh_sha256',report.get('source_sha256'))
    if expected != _hash(mesh_path):
        raise ValueError('Segmentation does not match source mesh SHA-256')
    names=json.loads((directory/'objects.json').read_text(encoding='utf-8'))['labels']
    if not isinstance(names,list) or len(set(names))!=len(names) or any(not isinstance(x,str) for x in names):
        raise ValueError('Invalid semantic class names')
    with np.load(directory/'labels.npz',allow_pickle=False) as arrays:
        ids,views=arrays['face_class_id'],arrays['face_view_count']
    if (ids.shape!=(face_count,) or views.shape!=ids.shape or not np.issubdtype(ids.dtype,np.integer)
            or not np.issubdtype(views.dtype,np.integer) or (views<0).any()
            or ((ids < -1)|(ids>=len(names))).any()):
        raise ValueError('Invalid per-face semantic evidence')
    wall=(ids==names.index('wall'))&(views>=min_views) if 'wall' in names else np.zeros(face_count,bool)
    floor=(ids==names.index('floor'))&(views>=min_views) if 'floor' in names else np.zeros(face_count,bool)
    return wall,floor,{'directory':str(directory),'min_views':min_views,
        'wall_faces':int(wall.sum()),'floor_faces':int(floor.sum()),
        'source_status':report.get('status'),'method':report.get('method','saved_segmentation'),
        'fingerprints':{name:_hash(directory/name) for name in ('report.json','objects.json','labels.npz')}}


def _weighted_quantile(values,weights,q):
    order=np.argsort(values);values=values[order];weights=weights[order]
    return float(values[min(np.searchsorted(np.cumsum(weights),q*weights.sum()),len(values)-1)])


def compare_surfaces(vertices,faces,reference,wall_mask,floor_mask,settings=None,*,camera_positions=None):
    """Match face centroids to BIM surfaces of the SAME semantic category.

    Unknown classes, furniture, tangential edge matches and disagreeing normals
    are never classified as construction deviations. Sign follows BIM normals.
    """
    import open3d as o3d
    settings=settings or ScanVsBimSettings()
    vertices=np.asarray(vertices,dtype=float);faces=np.asarray(faces)
    if (vertices.ndim!=2 or vertices.shape[1]!=3 or not np.isfinite(vertices).all()
            or faces.ndim!=2 or faces.shape[1]!=3 or not len(faces)
            or not np.issubdtype(faces.dtype,np.integer) or faces.min()<0 or faces.max()>=len(vertices)):
        raise ValueError('Invalid scan geometry')
    for mask in (wall_mask,floor_mask):
        if np.asarray(mask).shape!=(len(faces),) or np.asarray(mask).dtype!=bool:
            raise ValueError('Semantic masks must be boolean per-face arrays')
    if np.any(wall_mask&floor_mask):raise ValueError('Conflicting structural classes')
    triangles=vertices[faces];centers=triangles.mean(axis=1)
    cross=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
    size=np.linalg.norm(cross,axis=1);areas=size*.5;normals=cross/np.maximum(size[:,None],1e-15)
    deviations=np.full(len(faces),np.nan);matched=np.full(len(faces),-1,dtype=np.int32)
    # 0=no structural evidence, 1=matched, 2=not geometrically compatible.
    reasons=np.zeros(len(faces),dtype=np.uint8)
    nearest=np.full((len(faces),3),np.nan)
    element_categories=np.array([e['category'] for e in reference.elements])
    reference_facing=np.ones(len(reference.faces),dtype=bool)
    if camera_positions is not None:
        cameras=np.asarray(camera_positions,dtype=float)
        if cameras.ndim!=2 or cameras.shape[1]!=3 or not len(cameras) or not np.isfinite(cameras).all():
            raise ValueError('Expected finite aligned camera positions')
        rt=reference.vertices[reference.faces];rc=rt.mean(1)
        rn=np.cross(rt[:,1]-rt[:,0],rt[:,2]-rt[:,0]);rn/=np.maximum(np.linalg.norm(rn,axis=1)[:,None],1e-15)
        reference_facing=np.zeros(len(rt),bool)
        for camera in cameras:
            reference_facing |= np.einsum('ij,ij->i',rn,camera-rc)>1e-5
    for category,source_mask in [('wall',wall_mask),('floor',floor_mask)]:
        source_ids=np.flatnonzero(source_mask&(areas>1e-12));reasons[source_ids]=2
        ref_ids=np.flatnonzero((element_categories[reference.face_element_ids]==category)&reference_facing)
        if not len(source_ids) or not len(ref_ids):continue
        ray=o3d.t.geometry.RaycastingScene(nthreads=2)
        ray.add_triangles(o3d.core.Tensor(reference.vertices.astype(np.float32)),
                         o3d.core.Tensor(reference.faces[ref_ids].astype(np.uint32)))
        query=ray.compute_closest_points(o3d.core.Tensor(centers[source_ids].astype(np.float32)),nthreads=2)
        closest=query['points'].numpy().astype(float); rn=query['primitive_normals'].numpy().astype(float)
        delta=centers[source_ids]-closest
        signed=np.einsum('ij,ij->i',delta,rn)
        distance=np.linalg.norm(delta,axis=1)
        tangent=np.linalg.norm(delta-signed[:,None]*rn,axis=1)
        agreement=np.abs(np.einsum('ij,ij->i',normals[source_ids],rn))
        keep=(distance<=settings.max_match_distance_m)&(tangent<=settings.max_tangential_distance_m)&(agreement>=np.cos(np.deg2rad(settings.normal_angle_deg)))
        chosen=source_ids[keep];deviations[chosen]=signed[keep];reasons[chosen]=1
        matched[chosen]=reference.face_element_ids[ref_ids[query['primitive_ids'].numpy()[keep]]]
        nearest[chosen]=closest[keep]
    records=[];rng=np.random.default_rng(settings.seed)
    for element in reference.elements:
        record=dict(element)
        if not element['compare']:
            record.update(status='not_implemented',reason='Boundary verification for doors/windows is a separate stage')
            records.append(record);continue
        identity=element['element_id']; mask=matched==identity
        observed=float(areas[mask].sum());ref_faces=reference.faces[reference.face_element_ids==identity]
        rt=reference.vertices[ref_faces];ra=np.linalg.norm(np.cross(rt[:,1]-rt[:,0],rt[:,2]-rt[:,0]),axis=1)*.5
        record.update(observed_scan_area_m2=observed,compared_faces=int(mask.sum()),
            whole_element_verified=False,unobserved_policy='unknown_not_missing_or_deviating')
        # Coverage is sampled over ALL sides of the IFC solid, not camera visibility.
        n=min(20000,max(500,int(ra.sum()*200)))
        picked=rng.choice(len(rt),n,p=ra/ra.sum());uv=rng.random((n,2));uv[uv.sum(1)>1]=1-uv[uv.sum(1)>1]
        points=rt[picked,0]+uv[:,:1]*(rt[picked,1]-rt[picked,0])+uv[:,1:]*(rt[picked,2]-rt[picked,0])
        support=float(np.mean(cKDTree(nearest[mask]).query(points)[0]<=.08)) if mask.any() else 0.
        record['sampled_surface_support_fraction_all_sides']=support
        if observed<settings.min_observed_area_m2:
            record.update(status='insufficient_evidence',statistics=None)
        else:
            d=deviations[mask];weights=areas[mask]
            p95=_weighted_quantile(abs(d),weights,.95)
            record.update(status='observed_deviation' if p95>settings.tolerance_m else 'observed_within_tolerance',
                statistics={'median_signed_m':_weighted_quantile(d,weights,.5),'p95_absolute_m':p95,
                    'rmse_m':float(np.sqrt(np.average(d*d,weights=weights))),
                    'area_fraction_outside_tolerance':float(weights[abs(d)>settings.tolerance_m].sum()/weights.sum())})
        records.append(record)
    return {'deviations_m':deviations,'element_ids':matched,'face_status':reasons,'elements':records,
            'compared_area_m2':float(areas[matched>=0].sum())}


class ScanVsBimService:
    def __init__(self,settings=None):self.settings=settings or ScanVsBimSettings()

    def compare(self,directory,ifc_path,output_dir=None,*,segmentation_dir=None,segmented_only=False,interior_point=None,progress=None):
        session=Path(directory).resolve();ifc_path=Path(ifc_path).resolve()
        output=Path(output_dir).resolve() if output_dir else session/'scan_vs_bim'
        if output.parent!=session:raise ValueError('Output must be directly inside processed session')
        mesh=session/'mesh_preprocessing/mesh.ply'
        segmentation=Path(segmentation_dir).resolve() if segmentation_dir else session/'mesh_segmentation'
        if not segmentation.is_relative_to(session):raise ValueError('Segmentation must be inside session')
        capture=session/'input/capture.json'
        for p in (mesh,ifc_path,capture,segmentation/'labels.npz'):
            if not p.is_file():raise FileNotFoundError(str(p))
        output.mkdir(exist_ok=False)
        report={'schema_version':'1.0','status':'running','session_id':session.name,'stage':'loading',
            'parameters':asdict(self.settings),'source_mesh_sha256':_hash(mesh),'ifc_sha256':_hash(ifc_path),
            'deviation_sign':'Positive follows the outward normal of the matched IFC solid surface; not a global room-in/out sign.',
            'scope':'Observed wall and floor surfaces only. No new BIM reconstruction.',
            'limitations':['Thresholds are heuristics, not calibrated measurement uncertainty.',
                'Alignment is global; a shared rigid displacement is absorbed and cannot be independently measured.',
                'Semantic masks may be wrong. Furniture close and parallel to a wall can still be mislabelled.',
                'Missing or rejected samples are unverified, not proof of an absent element.',
                'Surface support fraction includes all IFC solid sides; it is not a visibility or completion percentage.',
                'Distance is sampled at scan triangle centroids; use a sufficiently dense reconstructed mesh.']}
        def stage(name):
            report['stage']=name;_save(output/'report.json',report)
            if progress:progress(name)
        try:
            stage('loading')
            vertices,colors,faces=read_mesh(mesh)
            report['capture_sha256']=_hash(capture)
            capture_info=json.loads(capture.read_text(encoding='utf-8'))
            coordinate=capture_info['coordinate_system']
            if coordinate=='arkit' and capture_info.get('length_unit')!='meter':
                raise ValueError('ARKit capture must explicitly declare metre units')
            rotation=source_to_z_up(coordinate)
            wall,floor,semantic_report=load_semantics(segmentation,mesh,len(faces),self.settings.min_views)
            report['semantic_evidence']=semantic_report
            if segmented_only:
                with np.load(segmentation/'labels.npz',allow_pickle=False) as labels:
                    keep=(labels['face_class_id']>=0)&(labels['face_view_count']>=self.settings.min_views)
                    kept_classes=labels['face_class_id'][keep]
                source_faces=np.flatnonzero(keep)
                if not len(source_faces):raise ValueError('No confirmed segmented faces to compare')
                used,inverse=np.unique(faces[keep],return_inverse=True)
                report['mesh_filter']={'policy':'confirmed_segmented_faces_only',
                    'source_faces':len(faces),'retained_faces':len(source_faces),
                    'removed_faces':int((~keep).sum()),'geometry_changed':False}
                vertices,colors,faces=vertices[used],colors[used],inverse.reshape(-1,3).astype(np.int32)
                wall,floor=wall[keep],floor[keep]
                write_mesh(output/'segmented_scan.ply',vertices,colors,faces)
                np.savez_compressed(output/'source_mapping.npz',source_face_ids=source_faces,
                                    source_vertex_ids=used,face_class_id=kept_classes)
            reference=load_bim_reference(ifc_path)
            if interior_point is not None:
                point=np.asarray(interior_point,float)
                if point.shape!=(3,) or not np.isfinite(point).all():raise ValueError('Invalid interior point')
                keep=np.zeros(len(reference.faces),bool)
                for e in reference.elements:
                    ids=np.flatnonzero(reference.face_element_ids==e['element_id'])
                    if len(ids):keep[ids]=interior_faces(reference.vertices,reference.faces[ids],e['category'],point)
                reference=replace(reference,faces=reference.faces[keep],face_element_ids=reference.face_element_ids[keep])
                report['interior_point_ifc_m']=point.tolist()
                report['reference_surface_policy']='interior_main_wall_faces_and_floor_top'

            _save(output/'reference_elements.json',reference.elements)
            stage('registration')
            registration=register_scan(vertices@rotation.T,faces,reference.vertices,reference.faces,
                source_wall_mask=wall,source_floor_mask=floor,seed=self.settings.seed)
            transform=np.asarray(registration['transform']);basis=np.eye(4);basis[:3,:3]=rotation
            transform=transform@basis
            moved=vertices@transform[:3,:3].T+transform[:3,3]
            registration['transform_scan_to_ifc']=transform.tolist()
            report['registration']=registration
            _save(output/'registration.json',registration)
            if not registration['accepted']:
                report.update(status='registration_rejected',reason=registration['reason'],elements=[],compared_faces=0)
                deviations=np.full(len(faces),np.nan);matched=np.zeros(len(faces),bool)
            else:
                stage('comparison')
                camera_origins=[];pose_fingerprints={}
                for path in sorted((session/'input/frames').glob('*/metadata.json')):
                    pose_fingerprints[str(path.relative_to(session))]=_hash(path)
                    metadata=json.loads(path.read_text(encoding='utf-8'))
                    pose=np.asarray(metadata['pose']['T_world_from_camera'],dtype=float)
                    if pose.shape!=(4,4) or not np.isfinite(pose).all():raise ValueError('Invalid camera pose')
                    camera_origins.append(pose[:3,3])
                if not camera_origins:raise ValueError('Camera poses are required to select the observed side of IFC surfaces')
                cameras=np.asarray(camera_origins)@transform[:3,:3].T+transform[:3,3]
                report['side_selection']='Only IFC faces oriented towards at least one registered capture camera; this does not certify visibility through furniture.'
                report['pose_fingerprints']=pose_fingerprints
                result=compare_surfaces(moved,faces,reference,wall,floor,self.settings,camera_positions=cameras)
                deviations=result['deviations_m'];matched=result['element_ids']>=0
                np.savez_compressed(output/'deviations.npz',deviations_m=deviations,
                    element_ids=result['element_ids'],face_status=result['face_status'])
                report.update(status='completed',elements=result['elements'],compared_faces=int(matched.sum()),
                    total_faces=len(faces),compared_area_m2=result['compared_area_m2'])
                _save(output/'objects.json',result['elements'])
                write_mesh(output/'aligned_scan.ply',moved,colors,faces)
            stage('export')
            write_mesh(output/'reference.ply',reference.vertices,
                np.tile(np.array([90,190,245,255],dtype=np.uint8),(len(reference.vertices),1)),reference.faces)
            export_scan_bim_viewer(output/'viewer.html',moved,faces,deviations,matched,reference.vertices,reference.faces,report)
            if _hash(mesh)!=report['source_mesh_sha256'] or _hash(ifc_path)!=report['ifc_sha256']:
                raise ValueError('Input geometry changed during comparison')
            for name,expected in semantic_report['fingerprints'].items():
                if _hash(segmentation/name)!=expected:raise ValueError('Segmentation changed during comparison')
            if _hash(capture)!=report['capture_sha256']:raise ValueError('Capture coordinates changed during comparison')
            for name,expected in report.get('pose_fingerprints',{}).items():
                if _hash(session/name)!=expected:raise ValueError('Camera poses changed during comparison')
            report['stage']='finished';_save(output/'report.json',report)
            return report
        except Exception as error:
            report.update(status='failed',error_type=type(error).__name__)
            _save(output/'report.json',report)
            raise


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session',type=Path);parser.add_argument('ifc',type=Path)
    parser.add_argument('--segmentation-dir',type=Path);parser.add_argument('--output',type=Path)
    parser.add_argument('--interior-point',nargs=3,type=float)
    parser.add_argument('--segmented-only',action='store_true',help='Remove unlabelled faces before registration and comparison; export segmented_scan.ply')
    args=parser.parse_args()
    result=ScanVsBimService().compare(args.session,args.ifc,args.output,
        segmentation_dir=args.segmentation_dir,segmented_only=args.segmented_only,interior_point=args.interior_point,progress=lambda stage:print(stage,flush=True))
    print(json.dumps({'status':result['status'],'reason':result.get('reason'),
                      'compared_faces':result.get('compared_faces')},indent=2))


if __name__=='__main__':main()
