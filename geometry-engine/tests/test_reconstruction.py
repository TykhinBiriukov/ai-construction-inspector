"""Metric reconstruction and failure behavior using synthetic calibrated RGB-D."""

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from geometry_engine.mesh import read_mesh
from geometry_engine.raw_capture import load_raw_capture, load_rgbd
from geometry_engine.reconstruction import (
    ReconstructionService, ReconstructionSettings, _depth_comparison, _surface,
)


class CaptureFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.session = Path(temporary.name)
        self.original = self.session / "original"
        self.original.mkdir()

    def capture(self, translations=(0., .2), depths=None):
        capture = {"schema_version": "1.0", "coordinate_system": "arkit", "length_unit": "meter",
                   "matrix_storage": "rows", "session_id": self.session.name,
                   "world_alignment": "gravity", "frames_directory": "frames",
                   "frame_count": len(translations)}
        (self.original / "capture.json").write_text(json.dumps(capture), encoding="utf-8")
        for index, x in enumerate(translations):
            name = f"frame-{index}"
            folder = self.original / "frames" / name
            folder.mkdir(parents=True)
            T = np.eye(4)
            T[0, 3] = x
            metadata = {"schema_version": "1.0", "frame_id": name, "timestamp_s": 1.+index,
                        "tracking_state": "normal",
                        "rgb": {"path": "rgb.png", "width": 64, "height": 48},
                        "depth": {"path": "depth.bin", "width": 64, "height": 48,
                                  "dtype": "float32", "byte_order": "little", "unit": "meter"},
                        "confidence": {"path": "confidence.bin", "width": 64, "height": 48, "dtype": "uint8"},
                        "intrinsics": {"K": [[60., 0, 32], [0, 60., 24], [0, 0, 1]], "width": 64, "height": 48},
                        "pose": {"T_world_from_camera": T.tolist()}}
            (folder / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            Image.new("RGB", (64, 48), (64, 128, 192)).save(folder / "rgb.png")
            depth = np.full((48, 64), 2., dtype="<f4") if depths is None else np.asarray(depths[index], dtype="<f4")
            depth.tofile(folder / "depth.bin")
            np.full((48, 64), 2, dtype="u1").tofile(folder / "confidence.bin")
        return load_raw_capture(self.session)


class ReprojectionTests(CaptureFixture):
    def test_translated_cameras_agree_on_analytic_world_plane(self):
        first, second = self.capture()
        _, a, _ = load_rgbd(first)
        _, b, _ = load_rgbd(second)
        world, _ = _surface(first, a)
        _, mask = _surface(second, b)
        result = _depth_comparison(world, second, b, mask, ReconstructionSettings())
        self.assertIsNotNone(result)
        self.assertGreater(result["compared_pixels"], 500)
        self.assertAlmostEqual(result["agreement_fraction"], 1.)
        self.assertLess(result["median_abs_depth_difference_m"], 1e-10)
        np.testing.assert_allclose(world[:, 2], -2)

    def test_incorrect_pose_creates_free_space_conflict(self):
        first, second = self.capture()
        _, a, _ = load_rgbd(first)
        _, b, _ = load_rgbd(second)
        world, _ = _surface(first, a)
        _, mask = _surface(second, b)
        wrong_pose = second.T_world_from_camera_cv.copy()
        wrong_pose[2, 3] = -.3
        wrong = replace(second, T_world_from_camera_cv=wrong_pose)
        result = _depth_comparison(world, wrong, b, mask, ReconstructionSettings())
        self.assertIsNotNone(result)
        self.assertGreater(result["foreground_conflict_fraction"], .99)
        self.assertAlmostEqual(result["median_abs_depth_difference_m"], .3)

    def test_occluded_background_is_not_foreground_conflict(self):
        first, second = self.capture(translations=(0., 0.))
        _, depth, _ = load_rgbd(first)
        world, _ = _surface(first, depth)
        # A newly visible foreground sheet at 1m occludes the old plane at 2m.
        foreground = np.ones_like(depth)
        _, mask = _surface(second, foreground)
        result = _depth_comparison(world, second, foreground, mask, ReconstructionSettings())
        self.assertAlmostEqual(result["possible_occlusion_fraction"], 1.)
        self.assertAlmostEqual(result["foreground_conflict_fraction"], 0.)

    def test_unobserved_region_does_not_prove_camera_inconsistency(self):
        first, second = self.capture()
        _, depth, _ = load_rgbd(first)
        world, _ = _surface(first, depth)
        _, mask = _surface(second, depth)
        far_pose = second.T_world_from_camera_cv.copy()
        far_pose[0, 3] = 100
        far = replace(second, T_world_from_camera_cv=far_pose)
        self.assertIsNone(_depth_comparison(world, far, depth, mask, ReconstructionSettings()))

    def test_invalid_reconstruction_settings_rejected(self):
        for values in ({"voxel_size_m": 0}, {"voxel_size_m": .001}, {"sdf_trunc_m": .001},
                       {"min_depth_m": 6, "max_depth_m": 5}, {"min_confidence": 3},
                       {"min_confidence": True}, {"consistency_neighbors": 0},
                       {"min_valid_fraction": 0}, {"max_depth_m": float("nan")}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ReconstructionSettings(**values)


@unittest.skipUnless(importlib.util.find_spec("open3d") is not None, "Install requirements-reconstruction.txt")
class RealTSDFTests(CaptureFixture):
    def service(self, **settings):
        return ReconstructionService(ReconstructionSettings(voxel_size_m=.04, sdf_trunc_m=.12,
                                                            create_preview=False, **settings))

    def assert_no_artifacts(self):
        self.assertFalse((self.session / "reconstruction").exists())
        self.assertFalse((self.session / "reconstruction.lock").exists())
        self.assertEqual(list(self.session.glob(".reconstruction-*")), [])

    def test_real_tsdf_preserves_meter_scale_arkit_axes_colors_and_inputs(self):
        self.capture()
        before = {p.relative_to(self.original): p.read_bytes() for p in self.original.rglob("*") if p.is_file()}
        report = self.service().reconstruct(self.session)
        output = self.session / "reconstruction"
        vertices, colors, triangles = read_mesh(output / "mesh.ply")
        self.assertGreater(len(vertices), 100)
        self.assertGreater(len(triangles), 100)
        np.testing.assert_allclose(vertices[:, 2], -2., atol=.025)
        np.testing.assert_allclose(np.median(colors[:, :3], axis=0), [64, 128, 192], atol=1)
        self.assertGreater(np.ptp(vertices[:, 0]), 1.8)
        self.assertLess(np.ptp(vertices[:, 0]), 2.5)
        self.assertEqual(report["integrated_frames"], 2)
        self.assertEqual(report["frames"][1]["consistency"], "supported")
        self.assertEqual(report["coordinate_system"], "arkit")
        self.assertEqual(report["unit"], "meter")
        import open3d as o3d
        cloud = o3d.io.read_point_cloud(str(output / "point_cloud.ply"))
        self.assertEqual(len(cloud.points), report["point_count"])
        np.testing.assert_allclose(np.asarray(cloud.points)[:, 2], -2., atol=.025)
        saved = json.loads((output / "report.json").read_text())
        self.assertEqual(saved, json.loads(json.dumps(report)))
        for relative, data in before.items():
            self.assertEqual((self.original / relative).read_bytes(), data)
        self.assertFalse((self.original / "mesh.ply").exists())

    def test_existing_output_is_preserved(self):
        self.capture()
        output = self.session / "reconstruction"
        output.mkdir()
        (output / "marker.txt").write_text("keep")
        with self.assertRaises(FileExistsError):
            self.service().reconstruct(self.session)
        self.assertEqual((output / "marker.txt").read_text(), "keep")
        self.assertFalse((self.session / "reconstruction.lock").exists())

    def test_severe_pose_conflict_with_two_views_is_excluded(self):
        self.capture(translations=(0., .2, .1))
        path = self.original / "frames" / "frame-2" / "metadata.json"
        metadata = json.loads(path.read_text())
        metadata["pose"]["T_world_from_camera"][2][3] = .3
        path.write_text(json.dumps(metadata), encoding="utf-8")
        report = self.service().reconstruct(self.session)
        self.assertEqual(report["integrated_frames"], 2)
        self.assertEqual(report["skipped_frames"], 1)
        self.assertEqual(report["frames"][2]["reason"], "conflict_with_multiple_views")

    def test_long_pause_alone_does_not_exclude_a_consistent_frame(self):
        self.capture()
        path = self.original / "frames" / "frame-1" / "metadata.json"
        metadata = json.loads(path.read_text())
        metadata["timestamp_s"] = 301.
        path.write_text(json.dumps(metadata), encoding="utf-8")
        report = self.service().reconstruct(self.session)
        self.assertEqual(report["integrated_frames"], 2)
        self.assertEqual(report["frames"][1]["consistency"], "supported")

    def test_corrupt_capture_does_not_publish_output(self):
        self.capture()
        (self.original / "frames" / "frame-0" / "depth.bin").write_bytes(b"broken")
        with self.assertRaisesRegex(ValueError, "byte count"):
            self.service().reconstruct(self.session)
        self.assert_no_artifacts()

    def test_invalid_depth_yields_no_output_and_releases_lock(self):
        self.capture(translations=(0.,), depths=[np.zeros((48, 64))])
        with self.assertRaisesRegex(ValueError, "No usable frames"):
            self.service().reconstruct(self.session)
        self.assert_no_artifacts()

    def test_pixel_decode_failure_releases_lock_without_partial_output(self):
        self.capture()
        (self.original / "frames" / "frame-0" / "rgb.png").write_bytes(b"not an image")
        with self.assertRaises(OSError):
            self.service().reconstruct(self.session)
        self.assert_no_artifacts()

    def test_output_cannot_replace_original_or_escape_session(self):
        self.capture()
        for output in (self.original / "result", self.session, self.session.parent / "external"):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, "Output"):
                self.service().reconstruct(self.session, output)
        self.assert_no_artifacts()


if __name__ == "__main__":
    unittest.main()
