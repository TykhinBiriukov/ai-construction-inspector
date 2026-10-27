"""End-to-end geometry labeling checks using saved masks and known RGB-D planes."""

import copy
from dataclasses import replace
import hashlib
import json
import shutil
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import test_mask_projection as projection_fixtures
from geometry_engine.mask_fusion import FusionSettings
from geometry_engine.mesh import read_mesh
from geometry_engine.mesh_segmentation import MeshSegmentationService


class MeshSegmentationTests(unittest.TestCase):
    def setUp(self):
        # Compose the shared RGB-D fixture instead of inheriting its test methods.
        self.fixture = projection_fixtures.MaskProjectionTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.session = self.fixture.session
        second = self.fixture.original / "frames" / "000002"
        shutil.copytree(self.fixture.frame_dir, second)
        shutil.copytree(self.fixture.segmentation / "masks/000001",
                        self.fixture.segmentation / "masks/000002")
        metadata = copy.deepcopy(self.fixture.metadata)
        metadata.update(frame_id="000002", timestamp_s=1.)
        self.fixture.write_json(second / "metadata.json", metadata)
        row = copy.deepcopy(self.fixture.manifest["frames"][0])
        row.update(frame_id="000002", timestamp_s=1.,
                   rgb="original/frames/000002/rgb.png",
                   metadata="original/frames/000002/metadata.json",
                   metadata_sha256=hashlib.sha256((second / "metadata.json").read_bytes()).hexdigest())
        row["detections"][0].update(observation_id="000002:000000", mask="masks/000002/000000.png")
        self.fixture.manifest["frames"].append(row)
        self.fixture.write_json(self.fixture.segmentation / "detections.json", self.fixture.manifest)
        # The artificial wall consists of two faces; real objects use the default 3.
        self.fusion = FusionSettings(min_overlap_faces=1)
        self.service = MeshSegmentationService(self.fixture.settings, self.fusion)

    def load_labels(self, output="mesh_segmentation"):
        with np.load(self.session / output / "labels.npz", allow_pickle=False) as arrays:
            return {name: arrays[name].copy() for name in arrays.files}

    def test_two_views_label_visible_wall_preserving_mesh_and_source_files(self):
        input_paths = [path for stage in ("original", "segmentation", "mesh_preprocessing")
                       for path in (self.session / stage).rglob("*") if path.is_file()]
        before = {path: hashlib.sha256(path.read_bytes()).digest() for path in input_paths}
        with patch("httpx.Client.send", side_effect=AssertionError("No remote model calls")), \
                patch("httpx.AsyncClient.send", side_effect=AssertionError("No remote model calls")):
            report = self.service.segment(self.session)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["processed_frames"], 2)
        self.assertEqual(report["labeled_faces"], 2)
        self.assertEqual(report["total_faces"], 4)
        self.assertEqual(report["candidate_objects"], 1)
        self.assertFalse(report["projection_reused"])
        labels = self.load_labels()
        vertices, _, faces = read_mesh(self.session / "mesh_preprocessing/mesh.ply")
        front = vertices[faces, 2].mean(axis=1) == 2.
        np.testing.assert_array_equal(labels["face_class_id"][front], 0)
        np.testing.assert_array_equal(labels["face_instance_id"][front], 0)
        np.testing.assert_array_equal(labels["face_view_count"][front], 2)
        self.assertTrue(np.all(labels["face_confidence"][front] > 0))
        np.testing.assert_array_equal(labels["face_class_id"][~front], -1)
        np.testing.assert_array_equal(labels["face_instance_id"][~front], -1)
        output = self.session / "mesh_segmentation"
        for name in ("mesh.ply", "viewer.html", "labels.npz", "objects.json", "report.json", "preview.jpg"):
            self.assertGreater((output / name).stat().st_size, 0, name)
        with Image.open(output / "preview.jpg") as preview:
            preview.verify()
        objects = json.loads((output / "objects.json").read_text(encoding="utf-8"))
        self.assertEqual(objects["labels"], ["wall"])
        self.assertEqual(len(objects["objects"]), 1)
        # Read the exported PLY independently: geometry and face order are exact.
        with (output / "mesh.ply").open("rb") as handle:
            header = []
            while (line := handle.readline()) != b"end_header\n":
                self.assertTrue(line, "Truncated PLY header")
                header.append(line)
            storage = "<f8" if b"property double x\n" in header else "<f4"
            exported_vertices = np.fromfile(handle, dtype=[("xyz", storage, 3), ("rgb", "u1", 3)], count=len(vertices))
            exported_faces = np.fromfile(handle, dtype=[("count", "u1"), ("indices", "<i4", 3),
                ("class_id", "<i4"), ("instance_id", "<i4"), ("confidence", "<f4"), ("rgb", "u1", 3)], count=len(faces))
            self.assertEqual(handle.read(), b"")
        np.testing.assert_array_equal(exported_vertices["xyz"], vertices)
        np.testing.assert_array_equal(exported_faces["indices"], faces)
        np.testing.assert_array_equal(exported_faces["class_id"], labels["face_class_id"])
        self.assertEqual({path: hashlib.sha256(path.read_bytes()).digest() for path in input_paths}, before)

    def test_single_view_keeps_all_faces_unknown(self):
        report = self.service.segment(self.session, max_frames=1)
        self.assertEqual(report["processed_frames"], 1)
        self.assertEqual(report["labeled_faces"], 0)
        self.assertEqual(report["candidate_objects"], 0)
        labels = self.load_labels()
        np.testing.assert_array_equal(labels["face_class_id"], -1)
        np.testing.assert_array_equal(labels["face_instance_id"], -1)

    def test_cached_projection_is_reused_without_rendering(self):
        self.service.segment(self.session)
        cached = {path: path.read_bytes() for path in (self.session / "mask_projection").rglob("*") if path.is_file()}
        # Fusion is independently rerunnable, including with changed vote settings.
        second_service = MeshSegmentationService(self.fixture.settings, replace(self.fusion, min_views=1))
        with patch("geometry_engine.mask_projection.render_visibility", side_effect=AssertionError("Unexpected rerender")):
            report = second_service.segment(self.session, self.session / "mesh-second")
        self.assertTrue(report["projection_reused"])
        self.assertEqual(report["labeled_faces"], 2)
        self.assertEqual({path: path.read_bytes() for path in cached}, cached)

    def test_existing_final_stage_is_not_overwritten(self):
        self.service.segment(self.session)
        with patch("geometry_engine.mask_projection.render_visibility", side_effect=AssertionError("Unexpected rerender")):
            with self.assertRaises(FileExistsError):
                self.service.segment(self.session)

    def test_changed_source_or_prepared_mesh_rejects_cached_projection(self):
        self.fixture.run_projection()
        for path in (self.fixture.frame_dir / "rgb.png", self.session / "mesh_preprocessing/mesh.ply"):
            with self.subTest(path=path.name):
                original_bytes = path.read_bytes()
                try:
                    path.write_bytes(original_bytes + b"changed")
                    with self.assertRaisesRegex(ValueError, "changed"):
                        self.service.segment(self.session)
                    self.assertFalse((self.session / "mesh_segmentation").exists())
                finally:
                    path.write_bytes(original_bytes)

    def test_partial_projection_cannot_silently_stand_in_for_full_run(self):
        self.fixture.run_projection(max_frames=1)
        with self.assertRaisesRegex(ValueError, "frame subset"):
            self.service.segment(self.session)
        self.assertFalse((self.session / "mesh_segmentation").exists())
        report = self.service.segment(self.session, max_frames=1)
        self.assertTrue(report["projection_reused"])
        self.assertEqual(report["processed_frames"], 1)

    def test_changed_projection_settings_need_new_projection_stage(self):
        self.fixture.run_projection()
        service = MeshSegmentationService(replace(self.fixture.settings, depth_abs_tolerance_m=.1), self.fusion)
        with self.assertRaisesRegex(ValueError, "settings differ"):
            service.segment(self.session)
        self.assertFalse((self.session / "mesh_segmentation").exists())

    def test_corrupt_sparse_offsets_are_rejected(self):
        _, _, arrays = self.fixture.run_projection()
        arrays["offsets"][1] = arrays["offsets"][-1] + 1
        np.savez_compressed(self.session / "mask_projection/observations.npz", **arrays)
        with self.assertRaisesRegex(ValueError, "cache changed"):
            self.service.segment(self.session)
        self.assertFalse((self.session / "mesh_segmentation").exists())

    def test_shape_valid_but_changed_cached_faces_are_rejected(self):
        _, _, arrays = self.fixture.run_projection()
        arrays["face_ids"][:] = 3
        np.savez_compressed(self.session / "mask_projection/observations.npz", **arrays)
        with self.assertRaisesRegex(ValueError, "cache changed"):
            self.service.segment(self.session)
        self.assertFalse((self.session / "mesh_segmentation").exists())


if __name__ == "__main__":
    unittest.main()
