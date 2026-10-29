"""Raw-device-wire fixtures. Generated images/depth are synthetic, never real scans."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
import numpy as np
from PIL import Image
from localization_service.lab_server import Lab
from localization_service.registration import fit_room
from test_registration import ROOM


def look_at(camera,target):
    forward=np.array(target)-camera;forward/=np.linalg.norm(forward)
    right=np.cross(forward,[0,1,0]);right/=np.linalg.norm(right)
    up=np.cross(right,forward)
    t=np.eye(4);t[:3,:3]=np.column_stack([right,up,-forward]);t[:3,3]=camera
    return t


def fixture(sid,segment,camera,target,world_transform):
    w,h=192,144; k=np.array([[150,0,w/2],[0,150,h/2],[0,0,1]])
    pose=look_at(np.array(camera,dtype=float),target)
    v,u=np.mgrid[0:h,0:w]; rays=np.stack([(u-w/2)/150,-(v-h/2)/150,-np.ones((h,w))],axis=-1)
    directions=rays@pose[:3,:3].T;origin=pose[:3,3];distance=np.full((h,w),np.inf)
    # Analytic plane ray intersections, open doorway plus a wall beyond it.
    for axis,position in [(0,0),(0,ROOM['length']),(1,0),(2,0),(2,-ROOM['width']),(0,-1)]:
        with np.errstate(divide='ignore',invalid='ignore'): t=(position-origin[axis])/directions[:,:,axis]
        with np.errstate(invalid="ignore"):points=origin+directions*t[:,:,None]
        valid=(t>.25)&(t<4.5)&(points[:,:,1]>=0)&(points[:,:,1]<ROOM['height'])
        if axis==0 and position==0:
            valid &= ~((points[:,:,2]<-.2)&(points[:,:,2]>-1.4)&(points[:,:,1]<2.1))
        if axis==0 and position==-1:
            valid &= (points[:,:,2]<-.2)&(points[:,:,2]>-1.4)&(points[:,:,1]<2.1)
        distance=np.minimum(distance,np.where(valid,t,np.inf))
    distance[~np.isfinite(distance)]=0
    depth=distance.astype('<f4').tobytes();confidence=bytes([2])*(w*h)
    image=io.BytesIO();Image.new('RGB',(w,h),(80,100,120)).save(image,format='JPEG');rgb=image.getvalue()
    oid=str(uuid4())
    def ref(name,data):return {'name':name,'byte_length':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    frame={'schema_version':'0.2','session_id':sid,'segment_id':segment,'observation_id':oid,'frame_id':oid,
           'capture_origin':'device', # Device-format fixture; isolated test result below is explicitly synthetic.
           'tracking_state':'normal','depth_source':'sceneDepth','depth_unit':'m','image_orientation':'native_buffer_unrotated_no_exif_rotation',
           'source_to_stored_image':[[1,0,0],[0,1,0],[0,0,1]],'depth_size':[w,h],'rgb_size':[w,h],
           'K_rgb':k.tolist(),'T_world_camera':(np.linalg.inv(world_transform)@pose).tolist(),
           'rgb':ref('rgb.jpg',rgb),'depth':ref('depth.f32',depth),'confidence':ref('confidence.u8',confidence)}
    files={'frame.json':json.dumps(frame).encode(),'rgb.jpg':rgb,'depth.f32':depth,'confidence.u8':confidence}
    return {'segment_id':segment,'files':{name:base64.b64encode(data).decode() for name,data in files.items()}},frame


class LabTests(unittest.TestCase):
    def test_end_to_end_raw_depth_and_pose(self):
        room=dict(ROOM,model_version='fixture',room_id='fixture',entry_element_id='door',bim_frame_id='fixture-y-up')
        with tempfile.TemporaryDirectory() as tmp:
            lab=Lab(tmp,room,'test-only-pairing')
            real_solve=lab.solver.solve
            def synthetic_solve(context,observations):
                result=real_solve(context,observations);result['analysis_origin']='synthetic';return result
            lab.solver.solve=synthetic_solve
            state=lab.request('POST',['test','sessions'],{'operation_id':str(uuid4())});sid=state['session_id'];segment=str(uuid4())
            lab.request('POST',['test','sessions',sid,'start'],{'segment_id':segment})
            theta=.55;t=np.eye(4);t[:3,:3]=[[np.cos(theta),0,np.sin(theta)],[0,1,0],[-np.sin(theta),0,np.cos(theta)]];t[:3,3]=[.4,-1.5,.3]
            views=[([1.8,1.4,-.8],[0,1,-.8]),([1.5,1.4,-.8],[0,.7,-.15]),([2,1.4,-1.2],[0,.65,-.2]),([2,1.35,-1.1],[.1,.1,-.3])]
            for camera,target in views:
                body,frame=fixture(sid,segment,camera,target,t)
                state=lab.request('POST',['test','sessions',sid,'observations'],body)
            self.assertEqual(state['guidance']['phase'],'inspecting',state.get('localization'))
            solved=np.array(state['alignments'][0]['T_bim_arkit'])
            self.assertLess(np.linalg.norm(solved-t),.08)
            self.assertEqual(state['alignments'][0]['analysis_origin'],'synthetic')
            self.assertEqual(state['observation_count'],4)
            self.assertEqual(lab.request('POST',['test','sessions',sid,'observations'],body)['observation_count'],4)
            pose={'session_id':sid,'segment_id':segment,'sequence':1,'tracking_state':'normal','T_arkit_camera':frame['T_world_camera']}
            state=lab.request('POST',['test','sessions',sid,'pose'],pose)
            np.testing.assert_allclose(np.array(state['latest_pose']['T_bim_camera'])[:3,3],views[-1][0],atol=.06)
            # Persistent state + raw file preservation, not just HTTP success.
            reopened=Lab(tmp,room,'test-only-pairing').request('GET',['test','sessions',sid],{})
            self.assertEqual(reopened['observation_count'],4)
            self.assertEqual(len(list(Path(tmp).glob('*/observations/*/depth.f32'))),4)
            bad=dict(body,files=dict(body['files'],**{'depth.f32':base64.b64encode(b'bad').decode()}))
            with self.assertRaises(ValueError):lab.request('POST',['test','sessions',sid,'observations'],bad)
