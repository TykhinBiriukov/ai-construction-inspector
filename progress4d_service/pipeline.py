"""Local end-to-end photo/IFC pipeline. Failure gates retain explicit uncertainty."""
import argparse
import json
import hashlib
import os
from pathlib import Path
import numpy as np
import pycolmap
from .cache import reconstruct_cached
from .registration import register
from .dense import run as dense
from .perception import make_cards,classify
from .results import cloud_from_ply


def run(images,ifc,output,resume=False):
    output=Path(output);output.mkdir(parents=True,exist_ok=resume)
    def stage(name):
        (output/'stage.json').write_text(json.dumps({'stage':name}))
    implementation=hashlib.sha256(b''.join(Path(__file__).with_name(name).read_bytes() for name in
        ['pipeline.py','reconstruct.py','registration.py','dense.py','surface.py','perception.py','evidence.py'])).hexdigest()
    result=dict(status='inconclusive',processing_complete=False,analysis_scope='single_room_observed_geometry_and_product_progress',artifacts=[],geometric_quality='unverified',
                implementation_sha256=implementation,ifc_sha256=hashlib.sha256(Path(ifc).read_bytes()).hexdigest())
    stage('reconstruction')
    if resume:
        from .cache import key_for
        if json.loads((output/'reconstruction/input-inventory.json').read_text())!=key_for(images)[1]:raise ValueError('Resume input provenance mismatch')
        sparse=json.loads((output/'reconstruction/report.json').read_text());sparse['computation']='reused_exact_input_sfm'
    else:sparse=reconstruct_cached(images,output/'reconstruction')
    result['reconstruction']=sparse
    if sparse['status']!='sparse_reconstructed':
        result['reason']='Insufficient image reconstruction';return save(result,output)
    model=output/'reconstruction'/'sparse'/sparse['selected_model']
    stage('registration')
    try:registration=register(model,ifc)
    except ValueError as exc:registration=dict(status='inconclusive',reason=str(exc))
    result['registration']=registration
    if registration['status']!='registered':
        result['reason']='IFC alignment unsupported or ambiguous';return save(result,output)
    if os.environ.get('PROGRESS4D_OPENMVS_BIN'):
        from . import surface
        density=surface.run(model,images,(output/'surface').resolve(),stage,resume=resume);result['dense']=density
        result['surface']=surface.export_registered(output/'surface',registration,output/'surface.glb')
        vertices,colors=surface.read_cloud(output/'surface/dense.ply')
        stride=max(1,int(np.ceil(len(vertices)/20000)))
        cloud=dict(points=np.column_stack([vertices[::stride],colors[::stride]]).tolist(),total_points=len(vertices))
    else:
        stage('dense_reconstruction');density=dense(model,images,output/'dense');result['dense']=density
        cloud=cloud_from_ply(output/'dense'/'observed-stereo.ply',limit=20000)
    sparse_model=pycolmap.Reconstruction(str(model))
    stable=np.array([[*p.xyz,*p.color] for p in sparse_model.points3D.values() if p.error<1.5 and p.track.length()>=4])
    if len(stable):
        stride=max(1,int(np.ceil(len(stable)/10000)))
        cloud['points'].extend(stable[::stride].tolist())
        cloud['total_points']+=len(stable)
    tr=registration['transform'];points=np.array(cloud['points'])
    if len(points):
        points[:,:3]=tr['scale']*points[:,:3]@np.array(tr['rotation']).T+tr['translation']
        # Registered room clip removes external/invalid stereo outliers from display.
        bounds=np.array(registration['room_bounds_m']);keep=np.all((points[:,:3]>=bounds[0]-.2)&(points[:,:3]<=bounds[1]+.2),axis=1)
        points=points[keep]
    cloud.update(points=points.tolist(),coordinate_frame='ifc_world_metres',metric_scale_known=True,
                 kind='observed_multiview_and_sparse_points' if 'surface' in result else 'observed_sparse_and_stereo_points',room_bounds_m=registration['room_bounds_m'],displayed_points=len(points))
    (output/'cloud.json').write_text(json.dumps(cloud,separators=(',',':')))
    result['geometric_quality']='registered_experimental_surfaces'
    stage('visual_product_analysis')
    detailed=os.environ.get('PROGRESS4D_EVIDENCE_VERSION')=='2'
    inventory,cards=make_cards(ifc,model,images,registration,output/'evidence',high_resolution=detailed)
    if detailed:
        from .evidence import classify as classify_detailed
        result['perception']=classify_detailed(inventory,cards,output/'evidence')
    else:result['perception']=classify(inventory,cards,output/'evidence')
    interpreted=any(p['state'] in ('present','absent','occluded') for p in result['perception']['products'])
    result.update(status='complete' if result['perception']['mode']=='live_meta' and interpreted and density['points']>=5000 else 'inconclusive',
                  processing_complete=True,
                  limitations=[('Photogrammetric textured surface; gaps and hidden surfaces remain unmeasured.' if 'surface' in result else 'Observed point clouds, not watertight meshes. Hidden surfaces are not reconstructed.'),
                               'Fine-layer thickness and installed volumes are not measured.',
                               'Unsupported geometry or visual evidence remains unknown.'])
    stage('complete');return save(result,output)


def save(result,output):
    result['processing_complete']=True
    (output/'report.json').write_text(json.dumps(result,indent=2));return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('images');p.add_argument('output');p.add_argument('--ifc',required=True);p.add_argument('--resume',action='store_true');a=p.parse_args()
    print(json.dumps(run(a.images,a.ifc,a.output,resume=a.resume)))
