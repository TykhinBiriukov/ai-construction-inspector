import unittest
import numpy as np
from geometry_engine.common.plane_fitting import fit_frame_plane


class PlaneFittingTests(unittest.TestCase):
    def test_frame_plane_rejects_background_outliers(self):
        rng = np.random.default_rng(42)
        points = np.column_stack((rng.uniform(-1,1,400),rng.uniform(-1,1,400),
                                  2+rng.normal(0,.003,400)))
        background = rng.uniform(-3,3,(100,3))
        normal,offset,stats = fit_frame_plane(np.vstack((points,background)))
        self.assertLess(abs(abs(offset)-2),.01)
        self.assertGreater(abs(normal[2]),.999)
        self.assertGreater(stats['inliers'],390)

    def test_line_is_not_plane_support(self):
        points = np.column_stack((np.linspace(0,1,100),np.zeros(100),np.ones(100)))
        with self.assertRaises(ValueError):
            fit_frame_plane(points)
