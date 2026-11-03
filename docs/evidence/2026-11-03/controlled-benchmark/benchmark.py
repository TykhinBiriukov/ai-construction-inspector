import os
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import json,time,hashlib,platform,sys
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import open3d as o3d
from scipy.spatial.transform import Rotation
from scipy.ndimage import maximum_filter,minimum_filter
import geometry_engine.segmentation.projection_alignment as pa
import geometry_engine.comparison.scan_vs_bim as sb
OUT=Path('/work');root=Path(__file__).resolve().parent
original_choose=pa.choose_camera
record={}
def observe(*args,**kwargs):
 status,reasons=original_choose(*args,**kwargs);record['status']=status;record['reasons']=reasons
 return 'accepted_refined',[]
pa.choose_camera=observe

def errors(T,truth):
 if T is None:return None
 T=np.array(T);return {'translation_mm':float(np.linalg.norm(T[:3,3]-truth[:3,3])*1000),'rotation_deg':float(np.degrees(Rotation.from_matrix(T[:3,:3]@truth[:3,:3].T).magnitude()))}
def scene_geometry(rng,plane=False):
 w=rng.uniform(1.8,2.4);top=-2.;bottom=rng.uniform(1.0,1.4);back=rng.uniform(2.6,3.4);front=.4
 v=np.array([[-w,top,back],[w,top,back],[w,bottom,back],[-w,bottom,back],[-w,bottom,front],[w,bottom,front],[-w,top,front],[w,top,front]],np.float32)
 f=np.array([[0,1,2],[0,2,3]] if plane else [[0,1,2],[0,2,3],[3,2,5],[3,5,4],[1,7,5],[1,5,2],[0,3,4],[0,4,6]],np.uint32)
 scene=o3d.t.geometry.RaycastingScene(nthreads=1);scene.add_triangles(o3d.core.Tensor(v),o3d.core.Tensor(f));return scene,v,f
cases=[]
for kind,count in [('moderate',12),('clean',4),('large_drift',4),('plane_normal',4),('plane_tangential',2)]:
 for idx in range(count):cases.append((kind,idx))
rows=[];shape=(96,128);K=np.array([[70.,0,63.5],[0,70.,47.5],[0,0,1.]])
for number,(kind,idx) in enumerate(cases):
 rng=np.random.default_rng(20261103+number);scene,v,f=scene_geometry(rng,kind.startswith('plane'))
 truth=np.eye(4);truth[:3,3]=rng.uniform([-.10,-.04,-.04],[.10,.04,.04]);depth,_=pa._render(scene,pa._rays(K,shape),truth,o3d)
 valid=np.isfinite(depth)&(depth>.15)&(depth<5);depth=np.where(valid,depth,0.);depth[valid]+=rng.normal(0,.002,int(valid.sum()));valid&=rng.random(shape)>.05
 valid&=(maximum_filter(depth,3)-minimum_filter(depth,3))<.10
 raw=truth.copy();direction=rng.normal(size=3);direction/=np.linalg.norm(direction);axis=rng.normal(size=3);axis/=np.linalg.norm(axis)
 if kind=='moderate':raw[:3,3]+=direction*rng.uniform(.04,.10);raw[:3,:3]=Rotation.from_rotvec(axis*np.radians(rng.uniform(0,1.5))).as_matrix()
 elif kind=='large_drift':raw[:3,3]+=direction*.30;raw[:3,:3]=Rotation.from_rotvec(axis*np.radians(6.)).as_matrix()
 elif kind=='plane_normal':raw[2,3]+=.08
 elif kind=='plane_tangential':raw[0,3]+=.12
 start=time.perf_counter();result=pa.align_frame(scene,K,raw,depth,valid,o3d);elapsed=time.perf_counter()-start
 proposal=np.array(result['T_world_from_camera_cv']);status=record['status'];selected=None if status=='rejected' else proposal if status=='accepted_refined' else raw
 row={'case_id':f'{kind}-{idx:02d}','stratum':kind,'seed':20261103+number,'status':status,'reasons':record['reasons'],'true_pose':truth.tolist(),'raw_pose':raw.tolist(),'proposal_pose':proposal.tolist(),'selected_pose':None if selected is None else selected.tolist(),'raw_error':errors(raw,truth),'ungated_error':errors(proposal,truth),'selected_error':errors(selected,truth),'valid_pixels':int(valid.sum()),'elapsed_seconds':elapsed,'observable':result['observable'],'mesh_sha256':hashlib.sha256(v.tobytes()+f.tobytes()).hexdigest()}
 rows.append(row);print(row['case_id'],status,flush=True)
wall=[]
for thickness in [.12,.20,.30]:
 for offset in [-.02,-.06,-.10,-.14,-.18,-.22]:
  inner=np.array([[0.,0,0],[0,1,0],[0,1,1],[0,0,1]]);vertices=np.vstack([inner,inner+[-thickness,0,0]]);faces=np.array([[0,1,2],[0,2,3],[4,6,5],[4,7,6]],np.int32)
  ref=SimpleNamespace(vertices=vertices,faces=faces,face_element_ids=np.zeros(4,dtype=np.int32),elements=[{'element_id':0,'category':'wall','compare':True,'guid':'wall','ifc_class':'IfcWall','name':'wall'}])
  scan=inner+[offset,0,0];sf=faces[:2];measure={}
  for name,camera in [('nearest_surface',None),('camera_side',np.array([[1.,.5,.5]]))]:
   result=sb.compare_surfaces(scan,sf,ref,np.ones(2,bool),np.zeros(2,bool),sb.ScanVsBimSettings(min_observed_area_m2=.001),camera_positions=camera)
   values=result['deviations_m'];measure[name]={'signed_offset_m':float(np.mean(values)) if np.isfinite(values).all() else None,'absolute_error_mm':float(abs(np.mean(values)-offset)*1000) if np.isfinite(values).all() else None}
  wall.append({'thickness_m':thickness,'true_offset_m':offset,**measure})
metadata={'execution':'isolated existing GCP VM, network-disabled Docker, 1 CPU / 2GB limit','source_revision':'a7217c7fa3e66f4b98d289954a7b42bbd214e386','python':platform.python_version(),'open3d':o3d.__version__,'numpy':np.__version__,'source_files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(pa.__file__),Path(sb.__file__)]},'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'protocol_sha256':hashlib.sha256((root/'protocol.txt').read_bytes()).hexdigest()}
(OUT/'results.json').write_text(json.dumps({'metadata':metadata,'pose_cases':rows,'wall_cases':wall},indent=2,allow_nan=False)+'\n');print('COMPLETED',len(rows),len(wall),flush=True)
