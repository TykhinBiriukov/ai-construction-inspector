"""CPU OpenMVS photogrammetry. No IFC or synthetic truth enters reconstruction."""
import hashlib,json,os,subprocess,time,struct
from pathlib import Path
import numpy as np
import pycolmap
import trimesh

VERSION='openmvs-2.4.0-800px-7views-fuse3-mesh150k-unblended-v3'


def read_cloud(path):
    """OpenMVS binary PLY includes two variable-length per-vertex view lists."""
    data=Path(path).read_bytes();end=data.index(b'end_header\n')+len(b'end_header\n')
    header=data[:end].decode('ascii');lines=header.splitlines()
    if 'property list uint8 uint32 view_indices' not in lines:
        cloud=trimesh.load(path,process=False)
        return np.asarray(cloud.vertices),np.asarray(cloud.colors)[:,:3]
    properties=[line for line in lines if line.startswith('property ')]
    expected=['property float32 '+x for x in ('x','y','z')]+['property uint8 '+x for x in ('red','green','blue')]+['property float32 '+x for x in ('nx','ny','nz')]+['property list uint8 uint32 view_indices','property list uint8 float32 view_weights']
    if properties!=expected or 'format binary_little_endian 1.0' not in lines:raise ValueError('Unsupported native PLY layout')
    count=int(next(x for x in lines if x.startswith('element vertex ')).split()[-1])
    if not 0<count<=10_000_000:raise ValueError('Invalid dense point count')
    vertices=np.empty((count,3),np.float32);colors=np.empty((count,3),np.uint8);offset=end
    for i in range(count):
        values=struct.unpack_from('<fffBBBfff',data,offset);vertices[i]=values[:3];colors[i]=values[3:6];offset+=27
        for _ in range(2):
            n=data[offset];offset+=1+4*n
    if offset!=len(data) or not np.isfinite(vertices).all():raise ValueError('Invalid dense PLY payload')
    return vertices,colors


def run(model,images,output,stage=lambda value:None,resume=False):
    output=Path(output);output.mkdir(parents=True,exist_ok=resume)
    binary=Path(os.environ['PROGRESS4D_OPENMVS_BIN']).resolve()
    for tool in ('InterfaceCOLMAP','DensifyPointCloud','ReconstructMesh','TextureMesh'):
        if not (binary/tool).is_file():raise ValueError('OpenMVS binary missing: '+tool)
    reuse=resume and all((output/name).is_file() for name in ('dense.ply','mesh.ply','textured.obj','textured.mtl'))
    if not reuse:
        stage('dense_reconstruction')
        pycolmap.undistort_images(output/'colmap',model,images,num_threads=2)
    commands=[('dense_reconstruction','InterfaceCOLMAP',['-i',str(output/'colmap'),'-o','scene.mvs','--image-folder',str(output/'colmap/images')]),
      ('dense_reconstruction','DensifyPointCloud',['-i','scene.mvs','-o','dense.mvs','--resolution-level','0','--max-resolution','800','--min-resolution','640','--number-views','7','--number-views-fuse','3','--tower-mode','0','--estimate-roi','0','--crop-to-roi','0','--remove-dmaps','1']),
      ('surface_reconstruction','ReconstructMesh',['-i','dense.mvs','-o','mesh.mvs','--close-holes','0','--smooth','1','--target-face-num','150000','--crop-to-roi','0']),
      ('surface_texturing','TextureMesh',['-i','dense.mvs','--mesh-file','mesh.ply','--close-holes','0','-o','textured.mvs','--export-type','obj','--resolution-level','0','--max-texture-size','4096','--global-seam-leveling','0','--local-seam-leveling','0'])]
    timings={}
    for name,tool,args in ([] if reuse else commands):
        stage(name);start=time.monotonic()
        with (output/(tool+'.log')).open('wb') as log:
            subprocess.run([str(binary/tool),*args,'--max-threads','2'],cwd=output,stdout=log,stderr=subprocess.STDOUT,timeout=1800,check=True)
        timings[tool]=time.monotonic()-start
    vertices,_=read_cloud(output/'dense.ply')
    mesh=trimesh.load(output/'mesh.ply',process=False)
    result=dict(backend='openmvs-2.4.0-resumed-native-output' if reuse else VERSION,computation='reused_completed_native_files' if reuse else 'computed',points=len(vertices),vertices=len(mesh.vertices),faces=len(mesh.faces),timings_seconds=timings,
        kind='photogrammetric_surface',minimum_consistent_views=3,truth_inputs=False,ifc_used_for_reconstruction=False,
        limitations=['Mesh interpolates observed multi-view samples; gaps and uncertain surfaces are not measured.', 'No synthetic geometry or planned IFC surfaces are used to fill missing regions.'])
    (output/'report.json').write_text(json.dumps(result,indent=2));return result


def export_registered(output,registration,destination,source_name="textured.obj"):
    """Metric transform only after reconstruction; GLB preserves photographic UVs."""
    scene=trimesh.load(Path(output)/source_name,force='scene',process=False)
    tr=registration['transform'];transform=np.eye(4)
    transform[:3,:3]=tr['scale']*np.array(tr['rotation']);transform[:3,3]=tr['translation']
    # IFC Z-up -> glTF Y-up, retaining a reversible documented coordinate frame.
    up=np.array([[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]],float)
    scene.apply_transform(up@transform)
    scene.export(str(destination),file_type='glb')
    return dict(coordinate_frame='ifc_x_z_minus_y_metres',bytes=Path(destination).stat().st_size,
                sha256=hashlib.sha256(Path(destination).read_bytes()).hexdigest(),geometries=len(scene.geometry))
