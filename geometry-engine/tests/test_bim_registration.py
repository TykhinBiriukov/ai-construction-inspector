import unittest
import numpy as np
from geometry_engine.comparison.bim_registration import _decide, _geometry, _sample, _dominant_yaw, register_scan


class RegistrationTests(unittest.TestCase):
    def room(self, opening):
        vertices=[];faces=[];labels=[]
        def quad(points,label):
            offset=len(vertices);vertices.extend(points)
            faces.extend([[offset,offset+1,offset+2],[offset,offset+2,offset+3]])
            labels.extend([label,label])
        quad([[0,0,0],[5,0,0],[5,3,0],[0,3,0]],'floor')
        quad([[0,0,0],[5,0,0],[5,0,2.7],[0,0,2.7]],'wall')
        quad([[0,3,0],[0,3,2.7],[5,3,2.7],[5,3,0]],'wall')
        quad([[0,0,0],[0,0,2.7],[0,3,2.7],[0,3,0]],'wall')
        if opening:
            quad([[5,0,0],[5,3,0],[5,3,.7],[5,0,.7]],'wall')
            quad([[5,0,2.4],[5,3,2.4],[5,3,2.7],[5,0,2.7]],'wall')
        else:
            quad([[5,0,0],[5,3,0],[5,3,2.7],[5,0,2.7]],'wall')
        v=np.array(vertices,dtype=float);f=np.array(faces);labels=np.array(labels)
        c,s=np.cos(.4),np.sin(.4);rotation=np.array([[c,-s,0],[s,c,0],[0,0,1]])
        source=v@rotation.T+[1,2,.3]
        result=register_scan(source,f,v,f,source_wall_mask=labels=='wall',source_floor_mask=labels=='floor')
        return source,v,result

    def test_real_icp_recovers_asymmetric_room_without_scaling(self):
        source,target,result=self.room(opening=True)
        self.assertTrue(result['accepted'],result['reason'])
        t=np.array(result['transform']);moved=source@t[:3,:3].T+t[:3,3]
        self.assertLess(np.linalg.norm(moved-target,axis=1).max(),.005)
        np.testing.assert_allclose(t[:3,:3].T@t[:3,:3],np.eye(3),atol=1e-6)
        self.assertAlmostEqual(np.linalg.det(t[:3,:3]),1,places=6)

    def test_real_icp_rejects_rectangular_room_symmetry(self):
        _,_,result=self.room(opening=False)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['reason'],'ambiguous_competing_alignment')

    def candidate(self,angle,score=.95):
        a=np.deg2rad(angle);t=np.eye(4);t[:2,:2]=[[np.cos(a),-np.sin(a)],[np.sin(a),np.cos(a)]]
        return {'transform':t.tolist(),'score':score,'inlier_fraction':.95,'inlier_rmse_m':.01,
                'normal_alignment_fraction':.99,'normal_observability_min_eigenvalue':.15,'tilt_deg':0.}

    def test_symmetric_room_must_not_arbitrarily_pick_pose(self):
        self.assertEqual(_decide([self.candidate(0),self.candidate(180,.94)]),
                         (False,'ambiguous_competing_alignment'))

    def test_two_seeds_converging_to_same_pose_are_not_ambiguity(self):
        self.assertTrue(_decide([self.candidate(0),self.candidate(1,.94)])[0])

    def test_single_plane_is_unobservable(self):
        c=self.candidate(0);c['normal_observability_min_eigenvalue']=0
        self.assertFalse(_decide([c])[0])

    def test_high_residual_is_rejected(self):
        c=self.candidate(0);c['inlier_rmse_m']=.075
        self.assertFalse(_decide([c])[0])

    def test_normal_direction_is_independent_of_face_winding(self):
        n=np.array([[1.,0,0],[-1.,0,0],[0,1.,0],[0,-1.,0]])
        self.assertAlmostEqual(_dominant_yaw(n,np.ones(4)),0)

    def test_surface_samples_stay_on_metric_triangle(self):
        v=np.array([[0.,0,2],[3,0,2],[0,4,2]]);f=np.array([[0,1,2]])
        t,n,a=_geometry(v,f);p,_=_sample(t,n,a,np.array([True]),500,np.random.default_rng(1))
        self.assertTrue(np.allclose(p[:,2],2))
        self.assertTrue(np.all(p[:,0]/3+p[:,1]/4<=1.00001))

    def test_missing_floor_rejects_with_no_plausible_transform(self):
        v=np.array([[0.,0,0],[2,0,0],[2,0,2],[0,0,2]]);f=np.array([[0,1,2],[0,2,3]])
        result=register_scan(v,f,v,f,source_wall_mask=np.ones(2,dtype=bool),source_floor_mask=np.zeros(2,dtype=bool))
        self.assertFalse(result['accepted']);self.assertEqual(result['candidates'],[])


if __name__=='__main__':unittest.main()
