"""Known geometric scenes exercise plane accuracy and incomplete support."""

from dataclasses import replace
import unittest

import numpy as np

from geometry_engine.wall_plane_detection import WallPlaneSettings, detect_wall_planes


def panel(origin, u, v, columns=8, rows=7, noise=0., seed=1):
    origin, u, v = (np.asarray(value, dtype=float) for value in (origin, u, v))
    normal = np.cross(u, v)
    normal /= np.linalg.norm(normal)
    vertices = np.array([origin+x*u+y*v for y in np.linspace(0, 1, rows+1)
                         for x in np.linspace(0, 1, columns+1)])
    vertices += np.random.default_rng(seed).normal(0, noise, (len(vertices), 1))*normal
    faces = []
    for y in range(rows):
        for x in range(columns):
            a = y*(columns+1)+x
            faces.extend([[a, a+1, a+columns+2], [a, a+columns+2, a+columns+1]])
    return vertices, np.asarray(faces, dtype=np.int32)


def combine(*parts):
    vertices, faces, offset = [], [], 0
    for v, f in parts:
        vertices.append(v)
        faces.append(f+offset)
        offset += len(v)
    return np.concatenate(vertices), np.concatenate(faces)


class WallPlaneDetectionTests(unittest.TestCase):
    def setUp(self):
        self.settings = WallPlaneSettings(sample_count=1500, num_iterations=350, max_planes=6, seed=11)

    def detect(self, vertices, faces, mask=None, **kwargs):
        before_v, before_f = vertices.copy(), faces.copy()
        if mask is None:
            mask = np.ones(len(faces), dtype=bool)
        result = detect_wall_planes(vertices, faces, mask, settings=self.settings, **kwargs)
        np.testing.assert_array_equal(vertices, before_v)
        np.testing.assert_array_equal(faces, before_f)
        for p in result["planes"]:
            corners = np.asarray(p["corners_world"])
            np.testing.assert_allclose(corners@p["normal"]+p["offset_m"], 0, atol=1e-12)
            self.assertLessEqual(p["max_vertex_distance_m"], self.settings.distance_threshold_m+1e-12)
            self.assertEqual(p["bounds_kind"], "observed_support_rectangle_not_wall_boundary")
        return result

    def test_noisy_perpendicular_walls_are_found_and_floor_excluded(self):
        wall_a = panel([0, 0, 0], [0, 4, 0], [0, 0, 2.7], noise=.002)
        wall_b = panel([0, 4, 0], [3, 0, 0], [0, 0, 2.7], noise=.002, seed=2)
        floor = panel([0, 0, 0], [3, 0, 0], [0, 4, 0])
        vertices, faces = combine(wall_a, wall_b, floor)
        mask = np.zeros(len(faces), dtype=bool)
        mask[:len(wall_a[1])+len(wall_b[1])] = True
        result = self.detect(vertices, faces, mask)
        self.assertEqual(len(result["planes"]), 2)
        normals = np.abs([p["normal"] for p in result["planes"]])
        self.assertGreater(np.max(normals[:, 0]), .999)
        self.assertGreater(np.max(normals[:, 1]), .999)
        self.assertTrue(np.all(result["face_plane_id"][-len(floor[1]):] == -1))
        self.assertTrue(np.all(result["face_plane_id"][:len(wall_a[1])+len(wall_b[1])] >= 0))
        self.assertEqual({p["status"] for p in result["planes"]}, {"wall_candidate"})

    def test_unlabeled_curtain_and_coplanar_neighbors_cannot_change_wall(self):
        wall = panel([0, 0, 0], [0, 3, 0], [0, 0, 2.5], noise=.002)
        curtain = panel([.01, 0, 0], [.04, 4, 0], [0, 0, 2.8], columns=30, rows=20)
        neighbor = panel([0, 3, 0], [0, 5, 0], [0, 0, 2.5])
        expected = self.detect(*wall)
        vertices, faces = combine(wall, curtain, neighbor)
        mask = np.zeros(len(faces), dtype=bool)
        mask[:len(wall[1])] = True
        actual = self.detect(vertices, faces, mask)
        self.assertEqual(actual["planes"], expected["planes"])
        np.testing.assert_array_equal(actual["face_plane_id"][:len(wall[1])], expected["face_plane_id"])
        np.testing.assert_array_equal(actual["face_plane_id"][len(wall[1]):], -1)
        self.assertEqual(actual["diagnostics"]["sam_used_as"], "hard_wall_face_filter")

    def test_missing_mask_is_rejected_and_empty_mask_never_falls_back_to_full_mesh(self):
        vertices, faces = panel([0, 0, 0], [0, 3, 0], [0, 0, 2.5])
        with self.assertRaisesRegex(ValueError, "wall_mask is required"):
            detect_wall_planes(vertices, faces, settings=self.settings)
        result = self.detect(vertices, faces, np.zeros(len(faces), dtype=bool))
        self.assertEqual(result["planes"], [])
        np.testing.assert_array_equal(result["face_plane_id"], -1)
        self.assertEqual(result["diagnostics"]["sample_count"], 0)

    def test_disconnected_fragments_share_plane_but_gap_has_no_new_faces(self):
        vertices, faces = combine(panel([0, 0, 0], [0, 1, 0], [0, 0, 2.5]),
                                  panel([0, 3, 0], [0, 1, 0], [0, 0, 2.5]))
        result = self.detect(vertices, faces)
        self.assertEqual(len(result["planes"]), 1)
        self.assertTrue(np.all(result["face_plane_id"] == 0))
        p = result["planes"][0]
        self.assertAlmostEqual(p["support_area_m2"], 5.)
        self.assertAlmostEqual(p["width_m"]*p["height_m"], 10.)
        self.assertEqual(len(result["face_plane_id"]), len(faces))

    def test_nearby_parallel_surfaces_stay_distinct(self):
        vertices, faces = combine(panel([0, 0, 0], [0, 3, 0], [0, 0, 2.5]),
                                  panel([.12, 0, 0], [0, 3, 0], [0, 0, 2.5]))
        result = self.detect(vertices, faces)
        self.assertEqual(len(result["planes"]), 2)
        offsets = sorted(p["offset_m"] for p in result["planes"])
        np.testing.assert_allclose(offsets, [-.12, 0], atol=1e-10)
        self.assertNotEqual(result["face_plane_id"][0], result["face_plane_id"][-1])

    def test_non_axis_aligned_wall_is_not_forced_to_manhattan_axes(self):
        angle = np.deg2rad(31.)
        width = np.array([np.cos(angle), np.sin(angle), 0])*3
        vertices, faces = panel([1, -2, .2], width, [0, 0, 2.5])
        result = self.detect(vertices, faces)
        self.assertEqual(len(result["planes"]), 1)
        expected = np.cross(width, [0, 0, 1])
        expected /= np.linalg.norm(expected)
        self.assertAlmostEqual(abs(np.dot(result["planes"][0]["normal"], expected)), 1.)

    def test_reversed_winding_and_y_up_have_same_support(self):
        vertices, faces = panel([0, 0, 0], [0, 3, 0], [0, 0, 2.5])
        faces[::2] = faces[::2, ::-1]
        original = self.detect(vertices, faces)
        transformed = vertices[:, [0, 2, 1]]
        alternate = self.detect(transformed, faces, up_axis="y")
        np.testing.assert_array_equal(original["face_plane_id"], alternate["face_plane_id"])
        self.assertAlmostEqual(original["planes"][0]["height_m"], alternate["planes"][0]["height_m"])

    def test_small_furniture_and_horizontal_surfaces_are_not_wall_candidates(self):
        vertices, faces = combine(panel([0, 0, 0], [0, .4, 0], [0, 0, .6]),
                                  panel([0, 0, 2.7], [3, 0, 0], [0, 4, 0]))
        result = self.detect(vertices, faces, np.ones(len(faces), dtype=bool))
        self.assertEqual(result["planes"], [])
        self.assertTrue(np.all(result["face_plane_id"] == -1))

    def test_area_sampling_does_not_prefer_tiny_densely_tessellated_panel(self):
        large = panel([0, 0, 0], [0, 4, 0], [0, 0, 2.5], columns=1, rows=1)
        tiny = panel([1, 0, 0], [0, .2, 0], [0, 0, .3], columns=50, rows=40)
        vertices, faces = combine(large, tiny)
        result = self.detect(vertices, faces)
        self.assertEqual(len(result["planes"]), 1)
        np.testing.assert_allclose(result["planes"][0]["normal"], [1, 0, 0], atol=1e-12)
        self.assertAlmostEqual(result["planes"][0]["offset_m"], 0.)
        self.assertTrue(np.all(result["face_plane_id"][:2] == 0))
        self.assertTrue(np.all(result["face_plane_id"][2:] == -1))

    def test_deterministic_seed_and_input_preservation(self):
        vertices, faces = combine(panel([0, 0, 0], [0, 4, 0], [0, 0, 2.5], noise=.003),
                                  panel([0, 4, 0], [3, 0, 0], [0, 0, 2.5], noise=.003))
        first, second = self.detect(vertices, faces), self.detect(vertices, faces)
        self.assertEqual(first["planes"], second["planes"])
        np.testing.assert_array_equal(first["face_plane_id"], second["face_plane_id"])

    def test_degenerate_and_empty_meshes_return_no_planes(self):
        for vertices, faces in ((np.empty((0, 3)), np.empty((0, 3), dtype=int)),
                                (np.array([[0., 0, 0], [1., 0, 0], [2., 0, 0]]), np.array([[0, 1, 2]]))):
            result = self.detect(vertices, faces)
            self.assertEqual(result["planes"], [])
            self.assertTrue(np.all(result["face_plane_id"] == -1))

    def test_invalid_settings_and_mesh_are_rejected(self):
        for values in ({"distance_threshold_m": 0}, {"normal_angle_deg": 90}, {"sample_count": 2},
                       {"max_planes": True}, {"seed": -1}, {"ransac_probability": 1}, {"min_height_m": np.nan}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                WallPlaneSettings(**values)
        vertices, faces = panel([0, 0, 0], [0, 3, 0], [0, 0, 2.5])
        with self.assertRaises(ValueError):
            self.detect(vertices, faces, np.ones(len(faces), dtype=int))
        with self.assertRaises(ValueError):
            self.detect(vertices, faces, up_axis="unknown")
        with self.assertRaises(ValueError):
            self.detect(vertices, faces.astype(float))


if __name__ == "__main__":
    unittest.main()
