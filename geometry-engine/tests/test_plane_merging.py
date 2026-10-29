"""Analytic surface grouping and discontinuity cases."""
import unittest

import numpy as np

from geometry_engine.plane_merging import merge_planes
from geometry_engine.wall_plane_detection import WallPlaneSettings, _fit, _plane_record


class PlaneMergingTests(unittest.TestCase):
    def merge(self, patches, **kwargs):
        triangles = []
        for x0, x1, offset in patches:
            z = 0. if callable(offset) else float(offset)
            a,b,c,d = np.array([[x0,0,z],[x1,0,z],[x1,2,z],[x0,2,z]])
            if callable(offset):
                for point in (a,b,c,d):
                    point[2] = offset(point[0])
            triangles.extend([[a,b,c],[a,c,d]])
        triangles = np.asarray(triangles)
        cross = np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
        areas = np.linalg.norm(cross,axis=1)/2
        normals = cross/(2*areas[:,None])
        labels = np.repeat(np.arange(len(patches),dtype=np.int32),2)
        settings = WallPlaneSettings(**kwargs)
        wall = np.ones(len(labels),dtype=bool)
        up = np.array([0.,1.,0.])
        def record(n,d,ids):
            return _plane_record(n,d,ids,triangles,areas,wall,up,settings)
        planes = []
        for i in range(len(patches)):
            ids = np.flatnonzero(labels==i)
            n,d = _fit(triangles,areas,ids)
            planes.append(dict(record(n,d,ids),plane_id=i))
        before = triangles.copy()
        result = merge_planes(planes,labels,triangles,areas,normals,up,settings,fit=_fit,record=record)
        np.testing.assert_array_equal(before,triangles)
        return result

    def test_small_noise_is_refit_and_all_faces_retained(self):
        planes,labels,log = self.merge([(0,1,0),(1,2,.01)])
        self.assertEqual(len(planes),1)
        self.assertTrue(planes[0]['merged'])
        self.assertEqual(planes[0]['source_plane_ids'],[0,1])
        np.testing.assert_array_equal(labels,[0,0,0,0])

    def test_parallel_step_is_not_erased_even_if_residuals_fit(self):
        planes,_,log = self.merge([(0,1,0),(1,2,.04)])
        self.assertEqual(len(planes),2)
        self.assertTrue(any('possible_step' in d.get('reason','') for d in log['decisions']))

    def test_distant_coplanar_patches_do_not_merge(self):
        planes,_,log = self.merge([(0,1,0),(3,4,0)])
        self.assertEqual(len(planes),2)
        self.assertEqual(log['decisions'][0]['reason'],'support_not_adjacent')

    def test_large_region_cannot_hide_small_offset_region(self):
        planes,_,_ = self.merge([(0,10,0),(10,10.2,.08)])
        self.assertEqual(len(planes),2)

    def test_continuous_bent_surface_merges_despite_flatness_warning(self):
        planes, labels, log = self.merge([(0,5,0),(5,10,lambda x: .04*(x-5))])
        self.assertEqual(len(planes), 1)
        self.assertTrue(planes[0]['approximation']['exceeds_flatness_tolerance'])
        self.assertGreater(planes[0]['approximation']['max_vertex_distance_m'], .03)
        np.testing.assert_array_equal(labels, [0,0,0,0])

    def test_slightly_angled_step_stays_separate(self):
        planes, _, log = self.merge([(0,1,0),(1,2,lambda x: .04+.02*(x-1))])
        self.assertEqual(len(planes), 2)
        self.assertIn('possible_step', log['decisions'][0]['reason'])

    def test_overlapping_parallel_layers_stay_separate(self):
        planes, _, _ = self.merge([(0,1,0),(0,1,.04)])
        self.assertEqual(len(planes), 2)

    def test_every_original_group_is_rechecked_after_a_merge(self):
        planes,_,_ = self.merge([(0,1,0),(1,2,.01),(2,3,.04)])
        self.assertEqual(len(planes),2)


if __name__ == '__main__':
    unittest.main()
