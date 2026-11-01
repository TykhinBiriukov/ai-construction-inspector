"""Compare existing segmented door/window instances with IFC element geometry.

Outputs are hypotheses for an agent, never certified defects or absence claims.
No segmentation, local object registration, completion or dimension fitting.
"""
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from scipy.optimize import linear_sum_assignment

from geometry_engine.common.mesh import read_mesh
from geometry_engine.comparison.scan_vs_bim import _hash, _save, load_semantics
from geometry_engine.capture.session_layout import original_directory
from geometry_engine.capture.validation import session_file
from geometry_engine.segmentation.projection_camera import ProjectionCameras
from geometry_engine.common.artifact_paths import artifact_reference, resolve_artifact_reference


@dataclass(frozen=True)
class OpeningComparisonSettings:
    tolerance_m: float = .10
    max_center_distance_m: float = 1.5
    max_normal_distance_m: float = .6
    ambiguity_margin_m: float = .15
    min_surface_area_m2: float = .05
    min_views: int = 2
    max_evidence_frames: int = 4

    def __post_init__(self):
        values=(self.tolerance_m,self.max_center_distance_m,self.max_normal_distance_m,
                self.ambiguity_margin_m,self.min_surface_area_m2)
        if not np.isfinite(values).all() or min(values)<=0:
            raise ValueError('Opening comparison thresholds must be positive and finite')
        if self.tolerance_m>=self.max_center_distance_m:
            raise ValueError('Matching radius must exceed deviation tolerance')
        if type(self.min_views) is not int or self.min_views<2:
            raise ValueError('Confirmed comparison requires at least two views')
        if type(self.max_evidence_frames) is not int or not 1<=self.max_evidence_frames<=12:
            raise ValueError('Invalid evidence frame limit')


def reference_axes(reference):
    axes=np.asarray([reference['u_axis'],reference['v_axis'],reference['normal_axis']],float)
    origin=np.asarray(reference['origin_m'],float)
    bounds=np.asarray(reference['bounds_local_m'],float)
    if (axes.shape!=(3,3) or origin.shape!=(3,) or bounds.shape!=(2,3)
            or not all(np.isfinite(a).all() for a in (axes,origin,bounds))
            or not np.allclose(axes@axes.T,np.eye(3),atol=1e-6) or np.any(bounds[1]<bounds[0])):
        raise ValueError('Invalid oriented IFC element geometry')
    return axes,origin,bounds


def rectangle_world(bounds, axes, origin):
    low,high=np.asarray(bounds)
    depth=(low[2]+high[2])/2
    corners=np.array([[low[0],low[1],depth],[high[0],low[1],depth],
                      [high[0],high[1],depth],[low[0],high[1],depth]])
    return corners@axes+origin


def measure_observation(points, reference):
    """Exact extents of observed vertices in the IFC element's oriented basis."""
    axes,origin,expected=reference_axes(reference)
    local=(np.asarray(points)-origin)@axes.T
    observed=np.array([local.min(axis=0),local.max(axis=0)])
    delta=observed-expected
    centers=observed.mean(axis=0)-expected.mean(axis=0)
    size=observed[1]-observed[0]; target=expected[1]-expected[0]
    metrics={'left':float(delta[0,0]),'right':float(delta[1,0]),
             'bottom':float(delta[0,1]),'top':float(delta[1,1]),
             'horizontal_center':float(centers[0]),'vertical_center':float(centers[1]),
             'normal_center':float(centers[2]),'width':float(size[0]-target[0]),
             'height':float(size[1]-target[1])}
    return {'basis':'ifc_element_local_axes_in_metres',
            'origin_ifc_world_m':origin.tolist(),'u_axis':axes[0].tolist(),
            'v_axis':axes[1].tolist(),'normal_axis':axes[2].tolist(),
            'observed_bounds_local_m':observed.tolist(),'ifc_bounds_local_m':expected.tolist(),
            'observed_width_m':float(size[0]),'observed_height_m':float(size[1]),
            'observed_depth_extent_m':float(size[2]),
            'ifc_width_m':float(target[0]),'ifc_height_m':float(target[1]),
            'deltas_m':metrics,
            'observed_corners_ifc_m':rectangle_world(observed,axes,origin).tolist(),
            'ifc_corners_m':rectangle_world(expected,axes,origin).tolist()}


def match_instances(observations, references, settings):
    """One-to-one assignment, with hard spatial gates and explicit ambiguity.

    Unmatched fragments are retained for review, never merged to fit IFC.
    """
    costs=np.full((len(observations),len(references)),np.inf)
    measures={}
    for i,obs in enumerate(observations):
        if obs['surface_area_m2']<settings.min_surface_area_m2:
            continue
        for j,ref in enumerate(references):
            if ref['geometry_status']!='loaded' or obs['category']!=ref['category']:
                continue
            if '_normal_ifc' in obs and abs(np.asarray(obs['_normal_ifc'])@np.asarray(ref['normal_axis']))<np.cos(np.deg2rad(20)):
                continue
            m=measure_observation(obs['_points'],ref);d=m['deltas_m']
            if '_intrinsic_dimensions' in obs:
                width,height=obs['_intrinsic_dimensions']
                m.update(observed_projected_width_m=m['observed_width_m'],observed_projected_height_m=m['observed_height_m'],
                    observed_width_m=width,observed_height_m=height,
                    observed_projected_corners_ifc_m=m['observed_corners_ifc_m'],
                    observed_corners_ifc_m=np.asarray(obs['_points']).tolist(),
                    basis='intrinsic_contour_dimensions_and_ifc_axis_position')
                d['width']=width-m['ifc_width_m'];d['height']=height-m['ifc_height_m']
            center=np.linalg.norm([d['horizontal_center'],d['vertical_center'],d['normal_center']])
            if center>settings.max_center_distance_m or abs(d['normal_center'])>settings.max_normal_distance_m:
                continue
            # Size is only an association hint; it never changes observed bounds.
            costs[i,j]=center+.25*(abs(d['width'])+abs(d['height']))
            measures[i,j]=m
    assignments={}
    if len(observations) and len(references):
        # Dummy columns ensure impossible pairs cannot consume valid references.
        padded=np.concatenate([np.where(np.isfinite(costs),costs,1e12),
                               np.full((len(observations),len(observations)),1e6)],axis=1)
        ii,jj=linear_sum_assignment(padded)
        for i,j in zip(ii,jj):
            if j>=len(references) or not np.isfinite(costs[i,j]):continue
            alternatives=np.concatenate([np.delete(costs[i],j),np.delete(costs[:,j],i)])
            ambiguous=bool(np.any(alternatives<=costs[i,j]+settings.ambiguity_margin_m))
            assignments[int(i)]=(int(j),ambiguous,measures[i,j])
    return assignments,costs


def extract_instances(vertices, faces, segmentation, min_views):
    objects=json.loads((segmentation/'objects.json').read_text(encoding='utf-8'))
    names=objects['labels']
    with np.load(segmentation/'labels.npz',allow_pickle=False) as labels:
        classes=labels['face_class_id'];instances=labels['face_instance_id'];views=labels['face_view_count']
    if instances.shape!=(len(faces),) or not np.issubdtype(instances.dtype,np.integer) or (instances<-1).any():
        raise ValueError('Invalid existing segmentation instance IDs')
    by_id={}
    for row in objects['objects']:
        identity=row['instance_id']
        if identity in by_id:raise ValueError('Duplicate segmentation instance ID')
        by_id[identity]=row
    results=[];unassigned={}
    for category in ('door','window'):
        if category not in names:continue
        eligible=(classes==names.index(category))&(views>=min_views)
        unassigned[category]=int((eligible&(instances<0)).sum())
        for identity in np.unique(instances[eligible&(instances>=0)]):
            row=by_id.get(int(identity))
            if row is None or row.get('label')!=category:
                raise ValueError('Instance metadata does not match semantic labels')
            ids=np.flatnonzero(eligible&(instances==identity));triangles=vertices[faces[ids]]
            area=np.linalg.norm(np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0]),axis=1).sum()/2
            points=vertices[np.unique(faces[ids])]
            results.append({'instance_id':int(identity),'category':category,'face_count':len(ids),
                'surface_area_m2':float(area),'bounds_ifc_world_m':[points.min(0).tolist(),points.max(0).tolist()],
                'observation_ids':row.get('observation_ids',[]),'_points':points})
    return results,unassigned


def load_boundary_observations(session, directory, transform, hashes, include_candidates=False):
    """Only accepted multi-view contours become measurements; rejected ones stay explicit."""
    directory = Path(directory).resolve()
    if directory.parent != session:
        raise ValueError('Boundary stage must be inside the processed session')
    if (directory/'.running').exists():
        raise ValueError('Boundary stage is still running')
    path = session_file(directory,directory,'objects.json')
    report_path = session_file(directory,directory,'report.json')
    data = json.loads(path.read_text(encoding='utf-8'))
    stage_report = json.loads(report_path.read_text(encoding='utf-8'))
    if stage_report.get('status') != 'completed':
        raise ValueError('Boundary stage did not complete successfully')
    if any(stage_report.get(key) != data.get(key) for key in ('input_fingerprints','parameters','model')):
        raise ValueError('Boundary objects differ from completed stage report')
    if data.get('coordinate_system') != 'arkit' or data.get('unit') != 'meter':
        raise ValueError('Unsupported boundary coordinate system or units')
    hashes[report_path] = _hash(report_path)
    if data.get('status') != 'completed' or not data.get('input_fingerprints'):
        raise ValueError('Completed fingerprinted boundary stage required')
    hashes[path] = _hash(path)
    for filename, expected in data['input_fingerprints'].items():
        source = resolve_artifact_reference(filename,session)
        if not source.is_relative_to(session) or _hash(source) != expected:
            raise ValueError('Boundary input changed or outside session')
        hashes[source] = expected
    required = [session/'mesh_preprocessing/mesh.ply', session/'mesh_segmentation/labels.npz']
    bound_paths={resolve_artifact_reference(p,session) for p in data['input_fingerprints']}
    if any(p not in bound_paths for p in required):
        raise ValueError('Boundary stage must bind current mesh and segmentation')
    accepted, rejected, consumed, names = [], [], set(), set()
    for row in data['objects']:
        category=row['category']; target=row['measurement_target']; identity=row['object_id']
        if row.get('artifact_sha256'):
            artifact=session_file(directory,directory,row['artifact'])
            if _hash(artifact)!=row['artifact_sha256']:raise ValueError('Mesh contour artifact changed')
            hashes[artifact]=row['artifact_sha256']
        if category not in ('door','window') or target != {'door':'door_opening','window':'outer_frame'}[category]:
            raise ValueError('Unsupported measured boundary definition')
        members=set(row['source_instance_ids'])
        if identity in names or (row['status']=='accepted' and consumed.intersection(members)):
            raise ValueError('Empty or overlapping boundary groups')
        names.add(identity)
        candidate=bool(include_candidates and row.get('candidate_corners_world_m') is not None
                       and 'implausible_rectangle' not in row.get('quality',{}).get('reasons',[]))
        if row['status']=='accepted' or candidate:consumed.update(members)
        if row['status'] != 'accepted' and not candidate:
            rejected.append({'object_id':identity,'category':category,'status':'boundary_not_verified',
                             'source_instance_ids':sorted(members),'quality':row.get('quality',{})})
            continue
        corners=np.asarray(row['corners_world_m'] if row['status']=='accepted' else row['candidate_corners_world_m'],float)
        if corners.shape != (4,3) or not np.isfinite(corners).all():
            raise ValueError('Invalid automatic boundary corners')
        points=corners@transform[:3,:3].T+transform[:3,3]
        area=np.linalg.norm(np.cross(points[1]-points[0],points[3]-points[0]))
        accepted.append({'instance_id':min(members) if members else None,'object_id':identity,'category':category,
                         'measurement_target':target,'measurement_status':
                         ('mesh_contour_candidate' if candidate and row['status']!='accepted' else
                          'mesh_wall_interface_supported' if data.get('boundary_method')=='semantic_mesh_contour_approximation' else 'multiview_verified_contour'),
                         'source_instance_ids':sorted(members),'face_count':0,'surface_area_m2':float(area),
                         'observation_ids':row.get('observation_ids',[]), 'quality':row.get('quality',{}),
                         'bounds_ifc_world_m':[points.min(0).tolist(),points.max(0).tolist()], '_points':points,
                         '_intrinsic_dimensions':(float(np.linalg.norm(points[1]-points[0])),float(np.linalg.norm(points[2]-points[1]))),
                         **({'_normal_ifc':np.asarray(row['plane']['normal'])@transform[:3,:3].T} if row.get('plane') else {})})
    return accepted,rejected


class OpeningComparisonService:
    def __init__(self, settings=None):self.settings=settings or OpeningComparisonSettings()

    def compare(self,directory,ifc_path,output_dir=None,*,segmentation_dir=None,comparison_dir=None,boundaries_dir=None,include_boundary_candidates=False,progress=None):
        from geometry_engine.comparison.opening_reference import load_opening_references
        from geometry_engine.visualization.opening_report import write_opening_review
        session=Path(directory).resolve();ifc=Path(ifc_path).resolve()
        segmentation=Path(segmentation_dir).resolve() if segmentation_dir else session/'mesh_segmentation'
        comparison=Path(comparison_dir).resolve() if comparison_dir else session/'scan_vs_bim'
        output=Path(output_dir).resolve() if output_dir else session/'opening_comparison'
        if (output.parent!=session or output in (segmentation,comparison,session/'input',session/'mesh_preprocessing')
                or not segmentation.is_relative_to(session) or not comparison.is_relative_to(session)):
            raise ValueError('Opening stages must stay inside processed session')
        if output.exists():raise FileExistsError(output)
        mesh=session/'mesh_preprocessing/mesh.ply';capture=session/'input/capture.json'
        previous=json.loads((comparison/'report.json').read_text(encoding='utf-8'))
        registration=previous.get('registration',{})
        if previous.get('status')!='completed' or not registration.get('accepted'):
            raise ValueError('An accepted completed global scan-versus-BIM alignment is required')
        if previous.get('source_mesh_sha256')!=_hash(mesh) or previous.get('ifc_sha256')!=_hash(ifc):
            raise ValueError('Alignment belongs to different mesh or IFC')
        if previous.get('capture_sha256')!=_hash(capture):raise ValueError('Capture changed since alignment')
        transform=np.asarray(registration.get('transform_scan_to_ifc'),float)
        if (transform.shape!=(4,4) or not np.isfinite(transform).all()
                or not np.allclose(transform[3],[0,0,0,1])
                or not np.allclose(transform[:3,:3].T@transform[:3,:3],np.eye(3),atol=1e-5)
                or not np.isclose(np.linalg.det(transform[:3,:3]),1,atol=1e-5)):
            raise ValueError('Expected a rigid global scan-to-IFC transform without scale')
        vertices,_,faces=read_mesh(mesh)
        _,_,semantic=load_semantics(segmentation,mesh,len(faces),self.settings.min_views)
        for name,expected in semantic['fingerprints'].items():
            if previous.get('semantic_evidence',{}).get('fingerprints',{}).get(name)!=expected:
                raise ValueError('Segmentation changed since alignment; rerun scan-versus-BIM first')
        hashes={mesh:_hash(mesh),capture:_hash(capture),ifc:_hash(ifc),comparison/'report.json':_hash(comparison/'report.json')}
        hashes.update({segmentation/name:expected for name,expected in semantic['fingerprints'].items()})
        for relative,expected in previous.get('pose_fingerprints',{}).items():
            path=session_file(session,session,relative)
            if _hash(path)!=expected:raise ValueError('Camera poses changed since alignment')
            hashes[path]=expected
        if progress:progress('reading_existing_instances')
        moved=vertices@transform[:3,:3].T+transform[:3,3]
        observations,unassigned=extract_instances(moved,faces,segmentation,self.settings.min_views)
        unverified_boundaries=[]
        if boundaries_dir is not None and segmentation != session/'mesh_segmentation':
            raise ValueError('Automatic boundaries require the bound mesh_segmentation stage')
        if boundaries_dir is not None:
            from geometry_engine.comparison.opening_reference import load_boundary_references
            observations,unverified_boundaries=load_boundary_observations(session,boundaries_dir,transform,hashes,include_boundary_candidates)
            references=load_boundary_references(ifc,previous.get('interior_point_ifc_m'))
        else:
            references=load_opening_references(ifc)
        assignments,costs=match_instances(observations,references,self.settings)
        findings=[];matched=set()
        for i,obs in enumerate(observations):
            finding={k:v for k,v in obs.items() if not k.startswith('_')}
            finding.update(finding_id=f'instance-{obs["instance_id"]}',ifc_guid=None,measurements=None,deltas_m=None,
                           review_required=True,review_reasons=['observed_mesh_extent_is_not_proven_complete',
                           'verify_same_boundary_definition_leaf_frame_or_opening'],agent_verdict='pending')
            if obs.get('object_id'):
                finding['finding_id']=obs['object_id']
                finding['review_reasons'].append('contour_requires_metric_verification')
            if boundaries_dir is not None:
                finding['review_reasons']=['absolute_accuracy_unverified','ifc_boundary_definition_requires_review']
            if i in assignments:
                j,ambiguous,measurement=assignments[i];matched.add(j)
                finding.update(ifc_guid=references[j]['guid'],ifc_name=references[j].get('name',''),
                               host_wall_guid=references[j].get('host_wall_guid'),
                               measurement_basis=references[j].get('measurement_basis','ifc_element_geometry'),
                               measurements=measurement,deltas_m=measurement['deltas_m'])
                exceed=[k for k,v in measurement['deltas_m'].items() if abs(v)>self.settings.tolerance_m]
                finding['exceeds_tolerance']=exceed
                finding['status']='ambiguous_match' if ambiguous else ('candidate_deviation' if exceed else 'no_candidate_deviation')
                finding['match_cost']=float(costs[i,j])
                if ambiguous:finding['review_reasons'].append('competing_instances_or_references')
                if np.isfinite(costs[:,j]).sum()>1:
                    finding['review_reasons'].append('other_instances_near_same_reference_may_be_fragments')
            else:
                finding['status']='unmatched_observation'
                finding['review_reasons'].append('possible_extra_object_or_fragment_or_false_segmentation')
                if obs['surface_area_m2']<self.settings.min_surface_area_m2:
                    finding['review_reasons'].append('insufficient_segmented_surface_area')
            finding['candidate_ifc_guids']=[references[j]['guid'] for j in np.flatnonzero(np.isfinite(costs[i]))]
            findings.append(finding)
        for j,ref in enumerate(references):
            if j in matched:continue
            findings.append({'finding_id':f'ifc-{ref["guid"]}','ifc_guid':ref['guid'],'ifc_name':ref.get('name',''),
                'instance_id':None,'category':ref['category'],'status':
                ('reference_unavailable' if ref['geometry_status']!='loaded' else
                 'ambiguous_match' if np.isfinite(costs[:,j]).any() else 'unobserved_reference'),
                'review_required':True,'agent_verdict':'pending','measurements':None,'deltas_m':None,
                'review_reasons':['not_detected_does_not_mean_absent'],'observation_ids':[]})
        if progress:progress('collecting_existing_photo_evidence')
        self._evidence(session,segmentation,findings,references,transform,hashes)
        report={'schema_version':'1.0','status':'completed','session_id':session.name,'parameters':asdict(self.settings),
                'agent_review':{'executed':False,'allowed_verdicts':['pending','supported_by_photos','dismissed','not_verifiable'],
                    'instructions':['Inspect linked RGB, SAM masks and projected bounds before accepting any finding.',
                        'For occluded or partial objects use not_verifiable; do not infer hidden dimensions.',
                        'Unmatched observations may be fragments or false labels, not necessarily extra objects.',
                        'Unobserved IFC elements are not proven absent; check actual visible surface evidence.',
                        'Record verdict, explanation and frame IDs; preserve original numeric measurements.']},
                'boundary_stage':artifact_reference(boundaries_dir,session) if boundaries_dir is not None else None,
                'includes_unverified_contour_candidates':include_boundary_candidates,
                'unverified_boundaries':unverified_boundaries,
                'alignment_report':artifact_reference(comparison/'report.json',session),'transform_scan_to_ifc':transform.tolist(),
                'summary':{'observed_instances':len(observations),'ifc_objects':len(references),
                    'matched_pairs':len(assignments),'candidate_deviations':sum(f['status']=='candidate_deviation' for f in findings),
                    'ambiguous_matches':sum(f['status']=='ambiguous_match' for f in findings),
                    'unassigned_semantic_faces':unassigned},
                'limitations':['All findings require photo review; no automatic confirmed violation or absence verdict.',
                    'Exact bounds of observed segmented vertices may be incomplete or contain outliers.',
                    'IFC element geometry is compared, not silently substituted by a structural opening.',
                    'Existing instance IDs may represent fragments of the same physical object.',
                    'Unassigned semantic faces are reported but not silently merged into a new object.',
                    'Matching is one-to-one with spatial gates; large shifts can remain unmatched.',
                    'Spatial matching does not establish wall identity; nearby corner objects require review.',
                    'Photo visibility of unmatched IFC objects is a geometric candidate, not proof of an unobstructed view.']}
        if boundaries_dir is not None:
            report['limitations'] = [
                ('Explicitly requested contour hypotheses included; their extents are not confirmed full-object dimensions.'
                 if include_boundary_candidates else 'Only supported contours were measured; rejected boundaries have no dimensions.'),
                'Door targets are IFC filled openings; window frame bounds use the interior wall surface when an interior seed is available.',
                'IFC window geometry may include sills or different frame definitions; review correspondence.',
                'Global alignment and sensor uncertainty remain; no certified absence or violation verdict.']
        with TemporaryDirectory(prefix='.opening-comparison-',dir=session) as tmp:
            staging=Path(tmp)/'result';staging.mkdir()
            report['findings']=findings
            report['artifacts']=write_opening_review(staging,report,session,segmentation)
            for finding in findings:
                for item in finding.get('evidence',[]):
                    for name in ('rgb','metadata','mask'):
                        if item.get(name):item[name]=artifact_reference(item[name],session)
            _save(staging/'reference_objects.json',{'schema_version':'1.0','objects':references})
            report['artifacts'].update(findings='findings.json',reference_objects='reference_objects.json')
            _save(staging/'findings.json',{'schema_version':'1.0','findings':findings})
            report.pop('findings')
            report['input_fingerprints']={artifact_reference(p,session):h for p,h in hashes.items()}
            for path,expected in hashes.items():
                if _hash(path)!=expected:raise ValueError('Input changed during opening comparison')
            _save(staging/'report.json',report)
            staging.rename(output)
        if progress:progress('completed')
        return report

    def _evidence(self,session,segmentation,findings,references,transform,hashes):
        """Trace instance observation IDs back to original SAM masks and RGB."""
        mesh_report=json.loads((segmentation/'report.json').read_text(encoding='utf-8'))
        projection=session_file(session,session,mesh_report['projection']+'/observations.json').parent
        projection_report=projection/'report.json'
        for name in ('report.json','observations.json','observations.npz'):
            path=projection/name
            if mesh_report.get('projection_fingerprints',{}).get(name)!=_hash(path):
                raise ValueError('Projection evidence changed since mesh segmentation')
            hashes[path]=_hash(path)
        manifest_path=projection/'observations.json'
        expected=mesh_report.get('projection_fingerprints',{}).get('observations.json')
        if expected!=_hash(manifest_path):raise ValueError('Projection observation evidence changed')
        hashes[manifest_path]=expected
        cameras=ProjectionCameras(session,segmentation)
        hashes.update({Path(path):digest for path,digest in cameras.fingerprints.items()})
        data=json.loads(manifest_path.read_text(encoding='utf-8'))
        rows={r['observation_id']:r for r in data['observations']};frames={f['frame_id']:f for f in data['frames']}
        source_hashes=json.loads(projection_report.read_text(encoding='utf-8'))['input_fingerprints']
        original=original_directory(session);inverse=np.linalg.inv(transform)
        reference_by_guid={r['guid']:r for r in references}
        def checked(relative,source=False):
            path=session_file(original if source else session,session,relative)
            if source_hashes.get(relative)!=_hash(path):raise ValueError('Photo or mask evidence changed')
            hashes[path]=_hash(path)
            return str(path)
        for finding in findings:
            available=[rows[x] for x in finding['observation_ids'] if x in rows]
            available.sort(key=lambda r:(-r['face_count'],-r['score'],r['frame_id']))
            selected=[];seen=set()
            for row in available:
                if row['frame_id'] in seen:continue
                seen.add(row['frame_id']);selected.append((frames[row['frame_id']],row))
                if len(selected)>=self.settings.max_evidence_frames:break
            ref=reference_by_guid.get(finding['ifc_guid']);predicted=None;observed=None
            if ref and ref['geometry_status']=='loaded':
                axes,origin,bounds=reference_axes(ref)
                predicted=rectangle_world(bounds,axes,origin)@inverse[:3,:3].T+inverse[:3,3]
            if finding['measurements']:
                observed=np.asarray(finding['measurements']['observed_corners_ifc_m'])@inverse[:3,:3].T+inverse[:3,3]
            if not selected and predicted is not None:
                # Select camera frusta covering the expected center. No occlusion claim.
                coord=json.loads((session/'input/capture.json').read_text())['coordinate_system']
                ranked=[]
                for frame in frames.values():
                    path=checked(frame['metadata'],True);meta=json.loads(Path(path).read_text())
                    try:
                        K,pose=cameras.parameters(meta,coord,frame['frame_id'])
                    except ValueError:
                        # Rejected alignment is not a reason to try the raw camera.
                        continue
                    camera=(predicted.mean(0)-pose[:3,3])@pose[:3,:3]
                    if camera[2]<=.1:continue
                    pixel=K@camera;pixel=pixel[:2]/pixel[2]
                    w,h=meta['rgb']['width'],meta['rgb']['height']
                    if 0<=pixel[0]<w and 0<=pixel[1]<h:ranked.append((float(np.linalg.norm(camera)),frame))
                selected=[(f,None) for _,f in sorted(ranked,key=lambda x:x[0])[:self.settings.max_evidence_frames]]
            evidence=[]
            for frame,row in selected:
                item={'frame_id':frame['frame_id'],'rgb':checked(frame['rgb'],True),
                      'metadata':checked(frame['metadata'],True)}
                if row:item['mask']=checked(row['mask'])
                if predicted is not None:item['predicted_corners_world_m']=predicted.tolist()
                if observed is not None:item['observed_corners_world_m']=observed.tolist()
                evidence.append(item)
            finding['evidence']=evidence
            if not evidence:finding['review_reasons'].append('no_linked_photo_evidence')


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session',type=Path);parser.add_argument('ifc',type=Path)
    parser.add_argument('--output',type=Path);parser.add_argument('--segmentation-dir',type=Path)
    parser.add_argument('--boundaries-dir',type=Path);parser.add_argument('--comparison-dir',type=Path);parser.add_argument('--tolerance-m',type=float,default=.10)
    parser.add_argument('--include-boundary-candidates',action='store_true',help='Analyze explicitly unverified mesh contour hypotheses')
    args=parser.parse_args()
    result=OpeningComparisonService(OpeningComparisonSettings(tolerance_m=args.tolerance_m)).compare(
        args.session,args.ifc,args.output,segmentation_dir=args.segmentation_dir,
        comparison_dir=args.comparison_dir,boundaries_dir=args.boundaries_dir,include_boundary_candidates=args.include_boundary_candidates,progress=lambda stage:print(stage,flush=True))
    print(json.dumps(result['summary'],indent=2))


if __name__=='__main__':main()
