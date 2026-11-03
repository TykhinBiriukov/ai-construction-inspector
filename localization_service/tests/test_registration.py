import unittest
import numpy as np
from localization_service.registration import fit_room, backproject

ROOM={'length':7.05,'width':5.349,'height':3.45,'door_offset':.2,'door_width':1.2,'door_height':2.1}

def fixture(seed=42):
    rng=np.random.default_rng(seed)
    # Points on the real doorway wall, neighbouring wall and floor, including furniture outliers.
    n=2000
    door=np.column_stack([np.zeros(n),rng.uniform(.1,2.9,n),rng.uniform(-3,0,n)])
    door=door[~((door[:,2]<-.2)&(door[:,2]>-1.4)&(door[:,1]<2.1))]
    adjacent=np.column_stack([rng.uniform(0,3,n),rng.uniform(.1,2.7,n),np.zeros(n)])
    floor=np.column_stack([rng.uniform(0,3,n),np.zeros(n),rng.uniform(-3,0,n)])
    points=np.concatenate([door,adjacent,floor])+rng.normal(0,.008,(len(door)+2*n,3))
    points=np.concatenate([points,rng.uniform([.1,.1,-2.8],[2.8,2.5,-.1],(600,3))])
    cameras=np.array([[1.8,1.5,-.8],[1.5,1.5,-1],[2,1.45,-1.3],[2.2,1.4,-1]])
    pose=np.eye(4);pose[:3,3]=cameras[0]
    # camera -Z points toward -X, camera right points -Z, Y up.
    pose[:3,:3]=[[0,0,1],[0,1,0],[-1,0,0]]
    theta=.73; r=np.array([[np.cos(theta),0,np.sin(theta)],[0,1,0],[-np.sin(theta),0,np.cos(theta)]])
    t=np.array([.6,-1.6,-.4]); transform=np.eye(4);transform[:3,:3]=r;transform[:3,3]=t
    # BIM = T * AR. Generate AR fixture by the inverse transform.
    return (points-t)@r,(cameras-t)@r,np.linalg.inv(transform)@pose,transform

class RegistrationTests(unittest.TestCase):
    def test_known_transform_noisy_partial_room(self):
        pts,cams,pose,truth=fixture()
        result=fit_room(pts,cams,pose,ROOM,4)
        self.assertEqual(result['status'],'localized',result)
        recovered=np.array(result['T_bim_arkit'])
        self.assertLess(np.linalg.norm(recovered[:3,3]-truth[:3,3]),.06)
        self.assertLess(np.linalg.norm(recovered[:3,:3]-truth[:3,:3]),.05)
    def test_no_floor_no_false_success(self):
        pts,cams,pose,truth=fixture()
        bim=pts@truth[:3,:3].T+truth[:3,3]
        result=fit_room(pts[bim[:,1]>.3],cams,pose,ROOM,4)
        self.assertNotEqual(result['status'],'localized')
    def test_no_corner_no_false_success(self):
        pts,cams,pose,truth=fixture()
        bim=pts@truth[:3,:3].T+truth[:3,3]
        result=fit_room(pts[bim[:,2]<-.3],cams,pose,ROOM,4)
        self.assertNotEqual(result['status'],'localized')
    def test_camera_facing_away_rejected(self):
        pts,cams,pose,_=fixture()
        pose=pose.copy();pose[:3,0]*=-1;pose[:3,2]*=-1
        self.assertNotEqual(fit_room(pts,cams,pose,ROOM,4)['status'],'localized')
    def test_four_view_guidance(self):
        pts,cams,pose,_=fixture()
        for n in (1,2,3):self.assertEqual(fit_room(pts,cams,pose,ROOM,n)['status'],'needs_more_evidence')
    def test_projection_axis_and_resolution(self):
        f={'depth_source':'sceneDepth','depth_unit':'m','tracking_state':'normal','depth_size':[2,2],
           'rgb_size':[4,4],'K_rgb':[[4,0,0],[0,4,0],[0,0,1]],'T_world_camera':np.eye(4).tolist()}
        depth=np.full((2,2),2,dtype='<f4').tobytes()
        points=backproject(f,depth,bytes([2,2,2,2]),stride=1)
        np.testing.assert_allclose(points,[[0,0,-2],[1,0,-2],[0,-1,-2],[1,-1,-2]])
        self.assertEqual(len(backproject(f,depth,bytes([0,0,0,0]),stride=1)),0)
        with self.assertRaises(ValueError):backproject(f,depth[:-1])
