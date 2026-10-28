"""Geometry payload, isolation and input contracts for plane overlays."""

import base64
import copy
import json
from pathlib import Path
import re
import tempfile
import unittest

import numpy as np

from geometry_engine.wall_plane_viewer import export_wall_plane_viewer, plane_color


class WallPlaneViewerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)/"viewer.html"
        self.vertices = np.array([[10., 3., -1.], [12., 3., -1.], [12., 3., 2.], [10., 3., 2.]])
        self.faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
        self.assignments = np.array([0, -1], dtype=np.int32)
        self.planes = [{"plane_id": 0, "normal": [0., 1., 0.], "offset_m": -3.,
                        "center_world": [11., 3., .5], "corners_world": self.vertices.tolist(),
                        "support_face_count": 1, "support_area_m2": 3., "rmse_m": .002,
                        "p95_distance_m": .004, "width_m": 2., "height_m": 3.,
                        "status": "wall_candidate"}]

    def export(self, **changes):
        arguments = dict(output_path=self.output, vertices=self.vertices, faces=self.faces,
                         face_plane_id=self.assignments, planes=self.planes)
        arguments.update(changes)
        return export_wall_plane_viewer(**arguments)

    def payload(self):
        html = self.output.read_text(encoding="utf-8")
        return html, json.loads(re.search(r'<script id="wall-plane-data" type="application/json">(.*?)</script>', html, re.S)[1])

    def test_mesh_and_planes_share_normalization_without_changing_geometry(self):
        originals = self.vertices.copy(), self.faces.copy(), self.assignments.copy(), copy.deepcopy(self.planes)
        self.export()
        html, data = self.payload()
        vertices = np.frombuffer(base64.b64decode(data["positions"]), dtype="<f4").reshape(-1, 3)
        restored = vertices*data["normalization"]["radius_m"]+data["normalization"]["center_world"]
        np.testing.assert_allclose(restored, self.vertices, atol=1e-6)
        np.testing.assert_allclose(data["planes"][0]["corners_normalized"], vertices, atol=1e-7)
        np.testing.assert_array_equal(data["planes"][0]["corners_world"], self.vertices)
        np.testing.assert_array_equal(np.frombuffer(base64.b64decode(data["faces"]), dtype="<u4").reshape(-1, 3), self.faces)
        np.testing.assert_array_equal(self.vertices, originals[0]); np.testing.assert_array_equal(self.faces, originals[1])
        np.testing.assert_array_equal(self.assignments, originals[2]); self.assertEqual(self.planes, originals[3])
        self.assertEqual(data["plane_extent_meaning"], "observed_support_bounds_not_wall_boundaries")
        self.assertIn("gl.depthMask(false)", html)
        self.assertIn("gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA", html)

    def test_support_colors_and_plane_colors_agree_and_unknown_is_preserved(self):
        self.export()
        _, data = self.payload()
        colors = np.frombuffer(base64.b64decode(data["support_colors"]), dtype="u1").reshape(-1, 4)
        np.testing.assert_array_equal(colors[0], [*plane_color(0), 255])
        np.testing.assert_array_equal(data["planes"][0]["color"], colors[0, :3])
        self.assertEqual(colors[1, 3], 0)
        np.testing.assert_array_equal(np.frombuffer(base64.b64decode(data["face_plane_ids"]), dtype="<i4"), [0, -1])

    def test_semantic_colors_remain_separate_from_plane_support(self):
        self.export(semantic_classes=np.array([-1, 0]), labels=["door"], up_axis="y")
        _, data = self.payload()
        self.assertEqual(data["up_axis"], "y")
        colors = np.frombuffer(base64.b64decode(data["semantic_colors"]), dtype="u1").reshape(-1, 4)
        self.assertEqual(colors[0, 3], 255)
        self.assertEqual(colors[1, 3], 0)
        np.testing.assert_array_equal(colors[1, :3], [87, 132, 245])

    def test_empty_plane_result_is_viewable(self):
        self.export(planes=[], face_plane_id=np.array([-1, -1]))
        _, data = self.payload()
        self.assertEqual(data["planes"], [])
        self.assertEqual(data["supported_face_count"], 0)

    def test_x_up_is_supported_with_axis_independent_plane_coordinates(self):
        self.export(up_axis="x")
        _, data = self.payload()
        self.assertEqual(data["up_axis"], "x")
        np.testing.assert_array_equal(data["planes"][0]["corners_world"], self.vertices)

    def test_initial_camera_fits_wall_support_instead_of_distant_mesh_outlier(self):
        self.export(vertices=np.vstack([self.vertices, [100., 100., 100.]]))
        _, data = self.payload()
        camera = data["initial_camera"]
        target = np.asarray(camera["target"])*data["normalization"]["radius_m"]+data["normalization"]["center_world"]
        np.testing.assert_allclose(target, self.vertices.mean(axis=0), atol=1e-10)
        self.assertLess(camera["distance"], .2)

    def test_external_metadata_cannot_escape_json_or_request_remote_files(self):
        injection = '</script><script>alert("unsafe")</script>'
        self.planes[0]["note"] = injection
        self.export(semantic_classes=np.array([0, -1]), labels=[injection])
        html, data = self.payload()
        self.assertNotIn(injection, html)
        self.assertEqual(data["planes"][0]["note"], injection)
        self.assertEqual(data["semantic_labels"], [injection])
        self.assertNotRegex(html, r'<script[^>]+src=')
        self.assertNotIn("fetch(", html)
        self.assertNotIn("https://", html)

    def test_invalid_plane_ids_or_geometry_are_rejected_before_writing(self):
        bad_plane = {**self.planes[0], "offset_m": -4.}
        for changes in (dict(face_plane_id=np.array([7, -1])), dict(face_plane_id=np.array([0., -1.])),
                        dict(planes=[bad_plane]), dict(planes=self.planes*2), dict(up_axis="w"),
                        dict(faces=np.array([[0, 1, 99]])), dict(semantic_classes=np.array([2, -1]), labels=["wall"])):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.export(**changes)
            self.assertFalse(self.output.exists())

    def test_existing_viewer_is_not_overwritten(self):
        self.output.write_text("original", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.export()
        self.assertEqual(self.output.read_text(encoding="utf-8"), "original")


if __name__ == "__main__":
    unittest.main()
