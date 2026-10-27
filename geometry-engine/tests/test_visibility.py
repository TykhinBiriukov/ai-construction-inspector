"""Known geometry and independent ray intersections verify CPU visibility."""

import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation

from geometry_engine.mesh import build_bvh, raycast
from geometry_engine.visibility import render_visibility


class VisibilityTests(unittest.TestCase):
    K = np.array([[4., 0, 3], [0, 4., 3], [0, 0, 1.]])

    @staticmethod
    def plane(z=2.):
        return (np.array([[-2, -2, z], [2, -2, z], [2, 2, z], [-2, 2, z]], dtype=float),
                np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32))

    def render(self, vertices, faces, **kwargs):
        return render_visibility(vertices, faces, self.K, np.eye(4), 7, 7, **kwargs)

    def test_plane_depth_original_face_ids_and_equal_depth_tie(self):
        depth, ids = self.render(*self.plane())
        np.testing.assert_allclose(depth, 2.)
        self.assertEqual(ids[3, 3], 0)
        self.assertEqual(set(np.unique(ids)), {0, 1})

    def test_nearer_triangles_occlude_back_surface_in_both_face_orders(self):
        near, faces = self.plane(1.)
        far, _ = self.plane(3.)
        vertices = np.concatenate([far, near])
        indices = np.concatenate([faces, faces+4])
        for order in (np.arange(4), np.arange(4)[::-1]):
            depth, ids = self.render(vertices, indices[order])
            np.testing.assert_allclose(depth, 1.)
            self.assertTrue(np.all(order[ids] >= 2))

    def test_background_and_triangles_behind_camera_have_no_hit(self):
        vertices, faces = self.plane(-2.)
        depth, ids = self.render(vertices, faces)
        self.assertTrue(np.isinf(depth).all())
        self.assertTrue((ids == -1).all())
        vertices, faces = self.plane()
        depth, ids = self.render(vertices+np.array([50, 0, 0]), faces)
        self.assertTrue(np.isinf(depth).all())
        self.assertTrue((ids == -1).all())

    def test_winding_is_two_sided_and_degenerate_faces_do_not_occlude(self):
        vertices, faces = self.plane()
        normal, ids = self.render(vertices, faces)
        reversed_depth, reversed_ids = self.render(vertices, faces[:, ::-1])
        np.testing.assert_array_equal(reversed_depth, normal)
        np.testing.assert_array_equal(reversed_ids, ids)
        depth, degenerate_ids = self.render(vertices, np.concatenate([[[0, 0, 0]], faces]))
        np.testing.assert_array_equal(depth, normal)
        self.assertTrue((degenerate_ids > 0).all())

    def test_perspective_depth_matches_ray_triangle_intersections(self):
        vertices = np.array([[-2., -2., 1.], [3., -2., 4.], [0., 3., 2.]])
        faces = np.array([[0, 1, 2]], dtype=np.int32)
        depth, ids = self.render(vertices, faces)
        bvh = build_bvh(vertices, faces)
        checked = 0
        for v, u in np.argwhere(ids >= 0):
            ray = np.linalg.solve(self.K, [u, v, 1.])
            hit = raycast(np.zeros(3), ray, vertices, faces, bvh)
            expected_z = hit["distance_m"] / np.linalg.norm(ray)
            self.assertAlmostEqual(depth[v, u], expected_z, places=10)
            checked += 1
        self.assertGreater(checked, 10)

    def test_rigid_world_camera_transform_preserves_depth_and_ids(self):
        camera_vertices, faces = self.plane()
        transform = np.eye(4)
        transform[:3, :3] = Rotation.from_euler("xyz", [17, 41, -28], degrees=True).as_matrix()
        transform[:3, 3] = [2, -5, 4]
        world_vertices = camera_vertices @ transform[:3, :3].T + transform[:3, 3]
        depth, ids = render_visibility(world_vertices, faces, self.K, transform, 7, 7)
        np.testing.assert_allclose(depth, 2., atol=1e-12)
        self.assertTrue((ids >= 0).all())

    def test_near_plane_clips_both_one_and_two_inside_vertices(self):
        faces = np.array([[0, 1, 2]], dtype=np.int32)
        examples = [np.array([[-1., -1., -.5], [1., -1., 2.], [0., 1., 2.]]),
                    np.array([[-1., -1., -.5], [1., -1., -.5], [0., 1., 2.]])]
        for vertices in examples:
            with self.subTest(vertices=vertices.tolist()):
                depth, ids = self.render(vertices, faces, near_m=.1)
                self.assertGreater((ids >= 0).sum(), 0)
                self.assertTrue((depth[ids >= 0] >= .1-1e-12).all())
                self.assertTrue((ids[ids >= 0] == 0).all())
                bvh = build_bvh(vertices, faces)
                for v, u in np.argwhere(ids >= 0):
                    ray = np.linalg.solve(self.K, [u, v, 1.])
                    hit = raycast(np.zeros(3), ray, vertices, faces, bvh)
                    self.assertAlmostEqual(depth[v, u], hit["distance_m"] / np.linalg.norm(ray), places=10)

    def test_small_sample_chunks_give_identical_visible_surface(self):
        vertices, faces = self.plane()
        expected = self.render(vertices, faces)
        with patch("geometry_engine.visibility.MAX_RASTER_SAMPLES", 3):
            actual = self.render(vertices, faces)
        np.testing.assert_array_equal(actual[0], expected[0])
        np.testing.assert_array_equal(actual[1], expected[1])

    def test_empty_mesh_and_intrinsic_skew(self):
        depth, ids = self.render(np.empty((0, 3)), np.empty((0, 3), dtype=np.int32))
        self.assertTrue(np.isinf(depth).all())
        self.assertTrue((ids == -1).all())
        K = self.K.copy()
        K[0, 1] = .75
        vertices, faces = self.plane()
        depth, _ = render_visibility(vertices, faces, K, np.eye(4), 7, 7)
        np.testing.assert_allclose(depth, 2.)

    def test_invalid_camera_image_size_or_geometry_is_rejected(self):
        vertices, faces = self.plane()
        options = dict(vertices=vertices, faces=faces, K=self.K, T_world_from_camera=np.eye(4), width=7, height=7)
        for changes in ({"width": True}, {"height": 0}, {"width": 25_000_001},
                        {"near_m": 0}, {"near_m": float("nan")}, {"K": np.zeros((3, 3))},
                        {"T_world_from_camera": np.diag([2., 1, 1, 1])},
                        {"T_world_from_camera": np.diag([-1., 1, 1, 1])},
                        {"vertices": np.full((4, 3), np.nan)},
                        {"faces": np.array([[0, 1, 99]])}, {"faces": faces.astype(float)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                render_visibility(**{**options, **changes})


if __name__ == "__main__":
    unittest.main()
