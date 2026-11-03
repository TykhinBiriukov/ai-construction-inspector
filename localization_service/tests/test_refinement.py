import unittest
import numpy as np
from localization_service.refinement import propose

ROOM=dict(length=7.05,width=5.349,height=3.45,door_offset=.2,door_width=1.2,door_height=2.1)


def clouds(angle=1.7, one_wall=False, conflict=False):
    rng=np.random.default_rng(7); result=[]
    for i in range(12):
        x=rng.uniform(.4,6.5,1200); y=rng.uniform(.8,2.2,1200)
        p=np.column_stack([x,y,np.zeros(1200)])
        if not one_wall:
            q=np.column_stack([np.full(1200,7.05),y,rng.uniform(-4.8,-.4,1200)])
            p=np.concatenate([p,q])
        a=np.radians(-angle if conflict and i%2 else angle);c,s=np.cos(a),np.sin(a)
        r=np.array([[c,0,s],[0,1,0],[-s,0,c]])
        result.append(p@r.T+rng.normal(0,.003,p.shape))
    return result


class RefinementTests(unittest.TestCase):
    def test_rigid_correction_supported_by_two_walls(self):
        result=propose(clouds(),ROOM)
        self.assertIsNotNone(result)
        correction,quality=result
        self.assertAlmostEqual(quality['yaw_degrees'],-1.7,delta=.1)
        np.testing.assert_allclose(correction[:3,:3].T@correction[:3,:3],np.eye(3),atol=1e-10)
        self.assertAlmostEqual(np.linalg.det(correction[:3,:3]),1)
        self.assertEqual(correction[1,3],0)
    def test_one_wall_is_insufficient(self):
        self.assertIsNone(propose(clouds(one_wall=True),ROOM))
    def test_disagreeing_frames_are_rejected(self):
        self.assertIsNone(propose(clouds(conflict=True),ROOM))
    def test_large_rotation_is_rejected(self):
        self.assertIsNone(propose(clouds(angle=6),ROOM))
    def test_correct_alignment_stays_fixed(self):
        self.assertIsNone(propose(clouds(angle=0),ROOM))
    def test_too_few_frames_are_rejected(self):
        self.assertIsNone(propose(clouds()[:10],ROOM))
