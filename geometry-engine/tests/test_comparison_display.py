import unittest
import numpy as np
from geometry_engine.common.surface_geometry import clip_mesh,opening_on_wall,measure_against_display_opening


class DisplayTests(unittest.TestCase):
    def test_window_reference_moves_only_depth_to_interior_wall(self):
        from geometry_engine.comparison.opening_reference import window_reference_at_wall
        ref=dict(origin_m=[0,0,0],u_axis=[1,0,0],v_axis=[0,0,1],normal_axis=[0,1,0],
                 bounds_local_m=[[0,1,.08],[3.331,2.513,.12]],corners_world_m=[],measurement_basis='frame')
        result=window_reference_at_wall(ref,(np.zeros(3),np.array([0,1,0])))
        bounds=np.array(result['bounds_local_m'])
        np.testing.assert_allclose(bounds[:,:2],np.array(ref['bounds_local_m'])[:,:2])
        np.testing.assert_allclose(np.array(result['corners_world_m'])[:,1],0)
        self.assertAlmostEqual(result['reference_projection']['reference_shift_m'],-.1)
        self.assertEqual(ref['bounds_local_m'][0][2],.08)
        self.assertEqual(result['original_ifc_geometry']['bounds_local_m'],ref['bounds_local_m'])

    def test_window_reference_rejects_oblique_host(self):
        from geometry_engine.comparison.opening_reference import window_reference_at_wall
        ref=dict(origin_m=[0,0,0],u_axis=[1,0,0],v_axis=[0,0,1],normal_axis=[0,1,0],
                 bounds_local_m=[[0,0,.08],[3,2,.12]])
        with self.assertRaises(ValueError):
            window_reference_at_wall(ref,(np.zeros(3),np.array([1,1,0])))

    def test_display_metrics_use_opening_plane_not_hidden_leaf_midplane(self):
        m={'u_axis':[1,0,0],'v_axis':[0,0,1],'normal_axis':[0,1,0],
           'origin_ifc_world_m':[0,3.411,0],
           'observed_bounds_local_m':[[0,-.01,-.071],[.94,2.15,-.071]]}
        opening=np.array([[0,3.331,0],[.919,3.331,0],[.919,3.331,2.173],[0,3.331,2.173]])
        r=measure_against_display_opening(m,opening)
        self.assertAlmostEqual(r['deltas_m']['normal_center'],.009)
        self.assertAlmostEqual(r['deltas_m']['width'],.021)
        self.assertAlmostEqual(r['deltas_m']['height'],-.013)
        np.testing.assert_allclose(np.array(r['ifc_corners_m'])[:,1],3.331)
        self.assertEqual(m['observed_bounds_local_m'][0][2],-.071)

    def test_floor_clips_to_wall_intersections_without_moving_interior(self):
        v=np.array([[-1,-1,0],[3,-1,0],[3,3,0],[-1,3,0]],float)
        f=np.array([[0,1,2],[0,2,3]])
        planes=[(np.array(p),np.array(n)) for p,n in [([0,0,0],[1,0,0]),([2,0,0],[-1,0,0]),([0,0,0],[0,1,0]),([0,2,0],[0,-1,0])]]
        points,faces=clip_mesh(v,f,planes)
        np.testing.assert_allclose(points.min(0),[0,0,0])
        np.testing.assert_allclose(points.max(0),[2,2,0])
        t=points[faces]
        self.assertAlmostEqual(np.linalg.norm(np.cross(t[:,1]-t[:,0],t[:,2]-t[:,0]),axis=1).sum()/2,4)
        self.assertEqual(v[0,0],-1)

    def test_opening_display_projects_to_host_without_changing_source(self):
        v=np.array([[0,.1,0],[1,.1,0],[1,.1,2],[0,.1,2]])
        f=np.array([[0,1,2],[0,2,3]])
        points,faces=opening_on_wall(v,f,(np.zeros(3),np.array([0,1,0])))
        np.testing.assert_allclose(points[:,1],0)
        np.testing.assert_allclose(v[:,1],.1)
        np.testing.assert_array_equal(points[:,[0,2]],v[:,[0,2]])
