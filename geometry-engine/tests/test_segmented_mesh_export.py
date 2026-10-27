"""Segmented PLY geometry and browser payload contracts."""

import base64
import json
from pathlib import Path
import re
import tempfile
import unittest

import numpy as np

from geometry_engine.segmented_mesh_export import UNKNOWN_COLOR, export_segmented_mesh


ENGINE = Path(__file__).resolve().parents[1]


def read_export(path):
    with path.open("rb") as handle:
        header = []
        while True:
            line = handle.readline().decode("ascii").rstrip()
            header.append(line)
            if line == "end_header":
                break
        vertex_count = int(next(line for line in header if line.startswith("element vertex ")).split()[-1])
        face_count = int(next(line for line in header if line.startswith("element face ")).split()[-1])
        storage = "<f8" if "property double x" in header else "<f4"
        vertices = np.fromfile(handle, dtype=[("xyz", storage, 3), ("rgb", "u1", 3)], count=vertex_count)
        faces = np.fromfile(handle, dtype=[("count", "u1"), ("indices", "<i4", 3),
                        ("class_id", "<i4"), ("instance_id", "<i4"),
                        ("confidence", "<f4"), ("rgb", "u1", 3)], count=face_count)
        trailing = handle.read()
    return header, vertices, faces, trailing


class SegmentedMeshExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ENGINE)
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)
        self.vertices = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [1., 1., 0.]])
        self.faces = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int32)
        self.classes = np.array([0, -1], dtype=np.int32)
        self.instances = np.array([5, -1], dtype=np.int32)
        self.confidence = np.array([.8, 0.])

    def export(self, **changes):
        arguments = dict(output_dir=self.output, vertices=self.vertices, faces=self.faces,
                         face_class_ids=self.classes, face_instance_ids=self.instances,
                         face_confidence=self.confidence, labels=["wall"], objects=[{"instance_id": 5}])
        arguments.update(changes)
        return export_segmented_mesh(**arguments)

    def test_ply_preserves_positions_face_order_and_semantic_attributes(self):
        self.export()
        header, vertices, faces, trailing = read_export(self.output / "mesh.ply")
        np.testing.assert_array_equal(vertices["xyz"], self.vertices)
        np.testing.assert_array_equal(faces["indices"], self.faces)
        np.testing.assert_array_equal(faces["class_id"], self.classes)
        np.testing.assert_array_equal(faces["instance_id"], self.instances)
        np.testing.assert_array_equal(faces["confidence"], self.confidence.astype(np.float32))
        np.testing.assert_array_equal(faces["count"], [3, 3])
        self.assertIn("property int class_id", header)
        self.assertIn("property int instance_id", header)
        self.assertEqual(trailing, b"")
        np.testing.assert_array_equal(faces["rgb"][1], UNKNOWN_COLOR)
        # Equal incident areas retain unknown at shared boundary vertices.
        np.testing.assert_array_equal(vertices["rgb"][[1, 2]], np.tile(UNKNOWN_COLOR, (2, 1)))

    def test_double_precision_coordinates_are_not_truncated(self):
        vertices = self.vertices.copy()
        vertices[0, 0] = .123456789012345
        self.export(vertices=vertices)
        header, saved, _, _ = read_export(self.output / "mesh.ply")
        self.assertIn("property double x", header)
        np.testing.assert_array_equal(saved["xyz"], vertices)

    def test_viewer_embeds_exact_face_colors_and_geometry_without_external_requests(self):
        result = self.export()
        self.assertEqual(result, {"mesh": "mesh.ply", "viewer": "viewer.html"})
        html = (self.output / "viewer.html").read_text(encoding="utf-8")
        payload = json.loads(re.search(r'<script id="mesh-data" type="application/json">(.*?)</script>', html, re.S)[1])
        saved_faces = np.frombuffer(base64.b64decode(payload["faces"]), dtype="<u4").reshape(-1, 3)
        np.testing.assert_array_equal(saved_faces, self.faces)
        colors = np.frombuffer(base64.b64decode(payload["class_colors"]), dtype=np.uint8).reshape(-1, 4)
        self.assertEqual(colors[0, 3], 255)
        self.assertEqual(colors[1, 3], 0)
        np.testing.assert_array_equal(colors[1, :3], UNKNOWN_COLOR)
        self.assertEqual(payload["known_face_count"], 1)
        self.assertIn("gl.drawArrays(gl.TRIANGLES", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("fetch(", html)
        self.assertNotRegex(html, r'<script[^>]+src=')

    def test_prompt_text_cannot_inject_html_into_viewer(self):
        injection = '</script><script>alert("unsafe")</script>'
        self.export(labels=[injection])
        html = (self.output / "viewer.html").read_text(encoding="utf-8")
        self.assertNotIn(injection, html)
        payload = json.loads(re.search(r'<script id="mesh-data" type="application/json">(.*?)</script>', html, re.S)[1])
        self.assertEqual(payload["classes"][0]["name"], injection)

    def test_invalid_face_labels_or_geometry_do_not_write_outputs(self):
        cases = [dict(face_class_ids=np.array([1, -1])), dict(face_class_ids=np.array([0., -1.])),
                 dict(face_instance_ids=np.array([5, -2])), dict(face_confidence=np.array([float("nan"), 0.])),
                 dict(face_confidence=np.array([1.2, 0.])), dict(faces=np.array([[0, 1, 7]])),
                 dict(vertices=np.array([[float("nan"), 0., 0.]])), dict(labels=["wall", "wall"])]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.export(**case)
        self.assertFalse((self.output / "mesh.ply").exists())
        self.assertFalse((self.output / "viewer.html").exists())

    def test_existing_files_are_never_overwritten(self):
        (self.output / "viewer.html").write_text("previous", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.export()
        self.assertEqual((self.output / "viewer.html").read_text(encoding="utf-8"), "previous")
        self.assertFalse((self.output / "mesh.ply").exists())

    def test_all_unknown_mesh_exports_without_classes_or_objects(self):
        self.export(face_class_ids=np.array([-1, -1]), face_instance_ids=np.array([-1, -1]),
                    face_confidence=np.zeros(2), labels=[], objects=[])
        _, vertices, faces, _ = read_export(self.output / "mesh.ply")
        np.testing.assert_array_equal(vertices["rgb"], np.tile(UNKNOWN_COLOR, (4, 1)))
        np.testing.assert_array_equal(faces["class_id"], [-1, -1])


if __name__ == "__main__":
    unittest.main()
