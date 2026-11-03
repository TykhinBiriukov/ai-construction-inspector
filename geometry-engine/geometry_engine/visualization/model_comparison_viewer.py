"""Offline two-model inspection in the already accepted IFC coordinate frame."""
import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from geometry_engine.common.mesh import read_mesh, write_mesh
from geometry_engine.comparison.bim_reference import _triangles, interior_faces
from geometry_engine.segmentation.mask_projection import file_hash, save_json
from geometry_engine.segmentation.service import _rename
from geometry_engine.common.surface_geometry import surface_plane, clip_mesh, opening_on_wall, measure_against_display_opening
from geometry_engine.common.artifact_paths import resolve_artifact_reference


def pack_mesh(vertices,faces):
    v=np.asarray(vertices,float);f=np.asarray(faces)
    if v.ndim!=2 or v.shape[1]!=3 or not np.isfinite(v).all() or f.ndim!=2 or f.shape[1]!=3:
        raise ValueError('Invalid mesh arrays')
    if not np.issubdtype(f.dtype,np.integer) or (f<0).any() or (f>=len(v)).any():raise ValueError('Invalid faces')
    used,inverse=np.unique(f,return_inverse=True)
    return {'positions':base64.b64encode(v[used].astype('<f4').tobytes()).decode(),
            'indices':base64.b64encode(inverse.astype('<u4').tobytes()).decode(),
            'bounds':[v[used].min(0).tolist(),v[used].max(0).tolist()]}


def build_model_comparison(session,ifc_path,output_dir=None,comparison_dir=None,opening_dir=None,raw_panel=False):
    import ifcopenshell
    import ifcopenshell.geom
    session=Path(session).resolve();ifc_path=Path(ifc_path).resolve()
    output=Path(output_dir).resolve() if output_dir else session/'model_comparison'
    if output.parent!=session or output.exists():raise ValueError('Use a new output inside the session')
    comparison=Path(opening_dir).resolve() if opening_dir else session/'opening_comparison'
    report=json.loads((comparison/'report.json').read_text(encoding='utf-8'))
    if report.get('status')!='completed':raise ValueError('Completed opening comparison required')
    for filename,expected in report['input_fingerprints'].items():
        if file_hash(resolve_artifact_reference(filename,session,allow_external=True))!=expected:raise ValueError('Comparison input changed: '+filename)
    registration=json.loads(((Path(comparison_dir) if comparison_dir else session/'scan_vs_bim')/'report.json').read_text(encoding='utf-8'))
    if not registration['registration']['accepted'] or file_hash(ifc_path)!=registration['ifc_sha256']:
        raise ValueError('IFC or accepted alignment differs')
    T=np.asarray(report['transform_scan_to_ifc'],float)
    if T.shape!=(4,4) or not np.allclose(T[:3,:3].T@T[:3,:3],np.eye(3),atol=1e-5):raise ValueError('Rigid alignment required')
    findings=json.loads((comparison/'findings.json').read_text(encoding='utf-8'))['findings']
    v,_,f=read_mesh(session/'mesh_preprocessing/mesh.ply');v=v@T[:3,:3].T+T[:3,3]
    objects=json.loads((session/'mesh_segmentation/objects.json').read_text(encoding='utf-8'))
    with np.load(session/'mesh_segmentation/labels.npz',allow_pickle=False) as labels:
        ids=labels['face_instance_id'];classes=labels['face_class_id']
    layers=[];exports={'ifc':[], 'scan':[]}
    names={'wall':'Wall','floor':'Floor','ceiling':'Ceiling','door':'Door','window':'Window','unknown':'Unlabeled geometry'}
    def add(side,identity,category,title,vertices,faces,**extra):
        if not len(faces):return
        layer=dict(side=side,id=identity,category=category,title=title,visible=category not in ('ceiling','unknown'),
                   **pack_mesh(vertices,faces));layer.update(extra);layers.append(layer)
        if side=='ifc':
            if not extra.get('default_hidden') and category not in ('unknown','contour'):
                exports[side].append((vertices,faces))
        elif category not in ('unknown','contour','opening'):exports[side].append((vertices,faces))
    model=ifcopenshell.open(str(ifc_path));settings=ifcopenshell.geom.settings();settings.set('use-world-coords',True)
    settings.set('convert-back-units',False);settings.set('disable-opening-subtractions',False)
    displayed_openings={}
    wall_planes={}
    interior=registration.get('interior_point_ifc_m')
    if interior is not None:
        for wall in model.by_type('IfcWall'):
            a,b=_triangles(ifcopenshell.geom.create_shape(settings,wall))
            b=b[interior_faces(a,b,'wall',interior)]
            if len(b):wall_planes[wall.GlobalId]=surface_plane(a,b)
    for typ,category in [('IfcWall','wall'),('IfcSlab','floor'),('IfcDoor','door'),('IfcWindow','window')]:
        for element in model.by_type(typ):
            a,b=_triangles(ifcopenshell.geom.create_shape(settings,element))
            kind=category
            if typ=='IfcSlab' and getattr(element,'PredefinedType',None) not in ('FLOOR','BASESLAB'):kind='ceiling'
            if registration.get('interior_point_ifc_m') is not None and kind in ('wall','floor'):
                b=b[interior_faces(a,b,kind,registration['interior_point_ifc_m'])]
            if wall_planes and kind in ('wall','floor'):
                a,b=clip_mesh(a,b,list(wall_planes.values()))
            extra={}
            if wall_planes and kind in ('door','window'):
                # Keep actual leaf/frame unchanged, but show its architectural opening
                # on the room surface as a separately labelled display layer.
                for rel in getattr(element,'FillsVoids',()):
                    opening=rel.RelatingOpeningElement
                    hosts=[r.RelatingBuildingElement.GlobalId for r in getattr(opening,'VoidsElements',())]
                    host=next((h for h in hosts if h in wall_planes),None)
                    if host:
                        # Frame width/height stay as authored; only its reference
                        # depth is represented on the room's inner wall surface.
                        ov,of=(a,b) if kind=='window' else _triangles(ifcopenshell.geom.create_shape(settings,opening))
                        ov,of=opening_on_wall(ov,of,wall_planes[host])
                        ov,of=clip_mesh(ov,of,list(wall_planes.values()))
                        add('ifc',element.GlobalId+'-opening','opening','Opening: '+names[kind],ov,of,
                            opening_kind=kind,display_only=True,host_wall=host,
                            reference_basis='interior_wall_surface')
                        displayed_openings[element.GlobalId]=ov[np.unique(of)]
                        extra=dict(visible=False,default_hidden=True)
            title=names[kind]+' · '+(element.Name or element.GlobalId)
            if extra:title+=' · original IFC position'
            add('ifc',element.GlobalId,kind,title,a,b,**extra)
    boundary_stage=resolve_artifact_reference(report['boundary_stage'],session) if report.get('boundary_stage') else None
    boundary_data=json.loads((boundary_stage/'objects.json').read_text(encoding='utf-8')) if boundary_stage else None
    mesh_contours=boundary_data is not None and boundary_data.get('boundary_method')=='semantic_mesh_contour_approximation'
    for obj in objects['objects']:
        category=obj['label']
        if mesh_contours and category in ('window','door'):continue
        finding=next((x for x in findings if x.get('instance_id')==obj['instance_id'] and x['category']==category),None)
        add('scan',str(obj['instance_id']),category,names.get(category,category)+f' #{obj["instance_id"]}',v,f[ids==obj['instance_id']],
            uncertain=bool(category=='door' and (not finding or not finding.get('ifc_guid'))))
    included_window_faces=np.zeros(len(f),dtype=bool)
    included_object_faces=np.zeros(len(f),dtype=bool)
    if mesh_contours:
        for obj in boundary_data['objects']:
            artifact=(boundary_stage/obj['artifact']).resolve()
            if not artifact.is_relative_to(boundary_stage.resolve()):raise ValueError('Invalid contour artifact path')
            if obj.get('artifact_sha256') and file_hash(artifact)!=obj['artifact_sha256']:raise ValueError('Contour artifact changed')
            with np.load(artifact,allow_pickle=False) as arrays:
                face_ids=arrays['face_ids'];wall_edges=arrays['wall_interface_edges']
            included_object_faces[face_ids]=True
            identity=obj['object_id'];category=obj['category'];uncertain=obj['status']!='accepted'
            if category=='window':included_window_faces[face_ids]=True
            add('scan',identity,category,names[category]+' · '+identity,v,f[face_ids],uncertain=uncertain)
            corners=obj.get('candidate_corners_world_m')
            if corners is not None:
                corners=np.asarray(corners)@T[:3,:3].T+T[:3,3]
                layers.append(dict(side='scan',id=identity+'-approx',owner=identity,category='contour',
                    title='Approximation: '+identity,visible=True,lines=corners.tolist(),
                    display_color=[1.,.65,.05] if uncertain else [.1,1.,.4],
                    bounds=[corners.min(0).tolist(),corners.max(0).tolist()]))
                add('scan',identity+'-surface','opening','Candidate plane: '+identity,corners,
                    np.array([[0,1,2],[0,2,3]]),visible=False,default_hidden=True,uncertain=uncertain,opening_kind=category)
            if len(wall_edges):
                segments=v[wall_edges]
                layers.append(dict(side='scan',id=identity+'-wall-interface',owner=identity,category='contour',
                    title='Wall-interface edges: '+identity,visible=True,line_segments=segments.tolist(),
                    display_color=[.1,1.,.3],bounds=[segments.min((0,1)).tolist(),segments.max((0,1)).tolist()]))
    window_ids=[x['instance_id'] for x in objects['objects'] if x['label']=='window']
    excluded_window_faces=np.isin(ids,window_ids)&~included_window_faces if mesh_contours else np.zeros(len(f),bool)
    add('scan','window-rejected','unknown','Rejected window-mask fragments',v,f[excluded_window_faces],uncertain=True)
    unused=((classes<0)|(ids<0))&~included_object_faces
    add('scan','unknown','unknown',names['unknown'],v,f[unused])
    for finding in findings:
        if mesh_contours and finding.get('ifc_guid') in displayed_openings:
            finding['display_ifc_id']=finding['ifc_guid']+'-opening'
        if finding.get('measurements') and finding.get('ifc_guid') in displayed_openings and not mesh_contours:
            finding['original_element_measurements']=finding['measurements']
            finding['measurements']=measure_against_display_opening(finding['measurements'],displayed_openings[finding['ifc_guid']])
            finding['deltas_m']=finding['measurements']['deltas_m']
            finding['display_ifc_id']=finding['ifc_guid']+'-opening'
            finding['status']='needs_review_boundary_definitions_differ'
    # Display both measured and reference rectangles separately from actual meshes.
    for finding in findings:
        measurement=finding.get('measurements')
        if not measurement:continue
        for side,key in [('scan','observed_corners_ifc_m'),('ifc','ifc_corners_m')]:
            corners=np.asarray(measurement[key]);owner=(finding.get('object_id') or str(finding['instance_id'])) if side=='scan' else finding.get('display_ifc_id',finding['ifc_guid'])
            layers.append(dict(side=side,id='contour-'+finding['finding_id'],owner=owner,category='contour',
                title='Contour: '+finding['finding_id'],visible=True,lines=corners.tolist(),
                bounds=[corners.min(0).tolist(),corners.max(0).tolist()]))
    low=np.min([x['bounds'][0] for x in layers if x['category']!='unknown'],axis=0)
    high=np.max([x['bounds'][1] for x in layers if x['category']!='unknown'],axis=0)
    # Evidence is linked in the existing report, never silently treated as confirmed geometry.
    summary=[{k:x.get(k) for k in ('finding_id','object_id','instance_id','category','ifc_guid','status','measurements','deltas_m','source_instance_ids','excluded_parts','original_element_measurements','display_ifc_id','measurement_status','quality')} for x in findings]
    payload=dict(schema_version='1.0',unit='meter',up_axis='z',bounds=[low.tolist(),high.tolist()],layers=layers,findings=summary,
        source_ifc=str(ifc_path),source_session=str(session),transform_scan_to_ifc=T.tolist(),
        limitations=['One rigid translation and rotation aligns the entire scan; individual objects are not fitted to IFC.',
            'Scan walls use the actual mesh: holes and surface irregularities are preserved.',
            'The IFC shell is clipped to the interior room planes. Display openings use those surfaces; original leaves and frames can be enabled separately.',
            'An unmatched door may be a segmentation error rather than an extra physical door.',
            'Local scan inconsistencies are preserved. Displayed deviations are candidates, not confirmed defects.'])
    if mesh_contours:
        payload['limitations']=[
            'Contours come only from the existing segmented mesh; no VLM or manual image annotations are used.',
            'Green segments are shared object/wall triangle edges. Orange rectangles approximate the segmented contour.',
            'Incomplete boundaries are candidates; reported sizes do not establish a complete physical window or opening.',
            'One rigid translation and rotation aligns the scan to IFC; individual objects are not adjusted to match IFC.',
            'Interior IFC surfaces and original leaves/frames use different references; the report records the comparison basis.']
    if raw_panel:
        source=session/'input/mesh.ply'
        rv,rgba,rf=read_mesh(source)
        used=np.unique(rf)
        rv=rv@T[:3,:3].T+T[:3,3]
        payload['raw_mesh']={**pack_mesh(rv,rf),'colors':base64.b64encode(rgba[used,:3].astype('uint8').tobytes()).decode(),
            'category':'raw','side':'raw','id':'raw','source_sha256':file_hash(source)}
    with TemporaryDirectory(prefix='.model-comparison-',dir=session) as tmp:
        stage=Path(tmp)/'result';stage.mkdir()
        for side,parts in exports.items():
            vv=[];ff=[];offset=0
            for a,b in parts:
                used,index=np.unique(b,return_inverse=True);vv.append(a[used]);ff.append(index.reshape(-1,3)+offset);offset+=len(used)
            points=np.concatenate(vv);triangles=np.concatenate(ff)
            write_mesh(stage/(side+'_model.ply'),points,np.full((len(points),4),200,dtype=np.uint8),triangles)
        template=Path(__file__).with_name('model_comparison_viewer.html').read_text(encoding='utf-8')
        if mesh_contours:
            template=template.replace('Mesh contours; boundary completeness requires review.',
                'Contours are approximated from the mesh. Green lines show class interfaces; orange lines show candidate contours. Dimension completeness is unverified.')
        encoded=json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':')).replace('<','\\u003c')
        (stage/'viewer.html').write_text(template.replace('../opening_comparison/review.html','../'+comparison.name+'/review.html').replace('__DATA__',encoded),encoding='utf-8')
        save_json(stage/'report.json',{'findings':summary,'limitations':payload['limitations'],'transform_scan_to_ifc':T.tolist(),
            'measurement_basis':'observed_region_vs_displayed_opening_not_confirmed_opening_boundaries',
            'source_ifc_sha256':file_hash(ifc_path),'source_opening_report_sha256':file_hash(comparison/'report.json'),
            'layers':[{k:x[k] for k in ('side','id','category','title')} for x in layers]})
        _rename(stage,output)
    return output/'viewer.html'


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('session');p.add_argument('ifc');p.add_argument('--output')
    a=p.parse_args();print(build_model_comparison(a.session,a.ifc,a.output))
