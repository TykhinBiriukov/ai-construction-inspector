import unittest
import numpy as np
from localization_service.coverage import surface_tiles

class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.room=dict(length=7.05,width=5.349,height=3.45,door_offset=.2,door_width=1.2,door_height=2.1)
    def test_only_measured_wall_tile(self):
        points=np.array([[.01,1.21,-2.21],[.02,1.22,-2.22],[.01,1.23,-2.23]])
        tiles=surface_tiles(points,np.array([2,1.5,-2]),self.room)
        self.assertEqual(len(tiles),1)
        self.assertAlmostEqual(next(iter(tiles.values()))['center'][0],.012)
    def test_foreground_object_does_not_paint_wall_behind_it(self):
        points=np.array([[1,1.21,-2.21],[1,1.22,-2.22],[1,1.23,-2.23]])
        self.assertEqual(surface_tiles(points,np.array([2,1.5,-2]),self.room),{})
    def test_door_opening_is_not_a_wall(self):
        points=np.array([[.01,1.21,-.81],[.02,1.22,-.82],[.01,1.23,-.83]])
        self.assertEqual(surface_tiles(points,np.array([2,1.5,-.8]),self.room),{})
    def test_sparse_or_backside_points_not_covered(self):
        p=np.array([[.01,1.21,-2.21],[.02,1.22,-2.22],[.01,1.23,-2.23]])
        self.assertEqual(surface_tiles(p[:1],np.array([2,1.5,-2]),self.room),{})
        self.assertEqual(surface_tiles(p,np.array([-2,1.5,-2]),self.room),{})
