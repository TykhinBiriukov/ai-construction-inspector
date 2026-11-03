import base64
import unittest
import numpy as np
from geometry_engine.visualization.model_comparison_viewer import pack_mesh


class ModelComparisonTests(unittest.TestCase):
    def test_compaction_preserves_triangle_coordinates_and_scale(self):
        vertices=np.array([[99.,99.,99.],[1.25,2.,3.],[2.,2.,3.],[1.,4.,3.]])
        faces=np.array([[3,1,2],[2,1,3]])
        packed=pack_mesh(vertices,faces)
        points=np.frombuffer(base64.b64decode(packed['positions']),dtype='<f4').reshape(-1,3)
        indices=np.frombuffer(base64.b64decode(packed['indices']),dtype='<u4').reshape(-1,3)
        np.testing.assert_allclose(points[indices],vertices[faces])
        np.testing.assert_allclose(packed['bounds'],[[1,2,3],[2,4,3]])

    def test_rejects_invalid_indices(self):
        with self.assertRaises(ValueError):
            pack_mesh(np.zeros((3,3)),np.array([[0,1,3]]))

    def test_rejects_nonfinite_geometry(self):
        with self.assertRaises(ValueError):
            pack_mesh(np.full((3,3),np.nan),np.array([[0,1,2]]))
