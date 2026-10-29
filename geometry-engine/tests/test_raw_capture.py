"""Raw capture contract, coordinate conventions, and corrupt-input handling."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from geometry_engine.raw_capture import load_raw_capture, load_rgbd


class RawCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.session = Path(temporary.name)
        self.part = self.session / "original" / "parts" / "capture-0001"
        self.base = self.part / "frames" / "frame-a"
        self.base.mkdir(parents=True)
        self.capture = {"schema_version": "1.0", "coordinate_system": "arkit", "length_unit": "meter",
                        "matrix_storage": "rows", "session_id": "session-01", "project_id": "project",
                        "world_alignment": "gravity", "frames_directory": "frames", "frame_count": 1}
        self.metadata = {"schema_version": "1.0", "frame_id": "frame-a", "timestamp_s": 12.5,
                         "tracking_state": "normal",
                         "rgb": {"path": "rgb.png", "width": 8, "height": 6},
                         "depth": {"path": "depth.bin", "width": 4, "height": 3, "dtype": "float32",
                                   "byte_order": "little", "unit": "meter"},
                         "confidence": {"path": "confidence.bin", "width": 4, "height": 3, "dtype": "uint8"},
                         "intrinsics": {"width": 8, "height": 6, "K": [[8, 0, 4], [0, 8, 2], [0, 0, 1]]},
                         "pose": {"T_world_from_camera": np.eye(4).tolist()}}
        Image.new("RGB", (8, 6), (60, 100, 200)).save(self.base / "rgb.png")
        np.full((3, 4), 2., dtype="<f4").tofile(self.base / "depth.bin")
        np.full((3, 4), 2, dtype="u1").tofile(self.base / "confidence.bin")
        self.save()

    def save(self):
        (self.part / "capture.json").write_text(json.dumps(self.capture), encoding="utf-8")
        (self.base / "metadata.json").write_text(json.dumps(self.metadata), encoding="utf-8")

    def test_backprojection_scales_intrinsics_and_uses_native_arkit_pose(self):
        self.metadata["pose"]["T_world_from_camera"][0][3] = 3
        self.metadata["pose"]["T_world_from_camera"][3][3] = 1.000000119
        self.save()
        frame, = load_raw_capture(self.session)
        self.assertEqual(frame.session_id, "session-01")
        np.testing.assert_allclose(frame.K_depth, [[4, 0, 2], [0, 4, 1], [0, 0, 1]])
        # Depth-pixel (u=3,v=2,z=2): right/down/forward = (.5,.5,2).
        camera = np.linalg.inv(frame.K_depth) @ [3*2, 2*2, 2]
        world = frame.T_world_from_camera_cv @ np.r_[camera, 1]
        np.testing.assert_allclose(world, [3.5, -.5, -2, 1])
        rgb, depth, stats = load_rgbd(frame)
        self.assertEqual(rgb.shape, (3, 4, 3))
        np.testing.assert_array_equal(rgb[0, 0], [60, 100, 200])
        self.assertEqual(depth.dtype, np.float32)
        self.assertEqual(stats["valid_pixels"], 12)
        self.assertFalse((self.part / "mesh.ply").exists())

    def test_invalid_depth_and_low_confidence_are_zero_not_fabricated(self):
        depth = np.array([[np.nan, np.inf, -1, 0], [.1, 8, 2, 3], [1, 1, 1, 1]], dtype="<f4")
        confidence = np.full((3, 4), 2, dtype="u1")
        confidence[1, 2] = 0
        depth.tofile(self.base / "depth.bin")
        confidence.tofile(self.base / "confidence.bin")
        before = (self.base / "depth.bin").read_bytes()
        _, cleaned, stats = load_rgbd(load_raw_capture(self.session)[0])
        np.testing.assert_array_equal(cleaned, [[0, 0, 0, 0], [0, 0, 0, 3], [1, 1, 1, 1]])
        self.assertEqual(stats["valid_pixels"], 5)
        self.assertEqual(before, (self.base / "depth.bin").read_bytes())

    def test_missing_confidence_is_reported(self):
        del self.metadata["confidence"]
        self.save()
        _, _, stats = load_rgbd(load_raw_capture(self.session)[0])
        self.assertFalse(stats["confidence_available"])

    def test_rejects_wrong_depth_units_and_truncated_buffer(self):
        self.metadata["depth"]["unit"] = "millimeter"
        self.save()
        with self.assertRaisesRegex(ValueError, "meters"):
            load_raw_capture(self.session)
        self.metadata["depth"]["unit"] = "meter"
        self.save()
        (self.base / "depth.bin").write_bytes(b"\x00" * 4)
        with self.assertRaisesRegex(ValueError, "byte count"):
            load_raw_capture(self.session)

    def test_rejects_nonrigid_pose_and_invalid_intrinsics(self):
        self.metadata["pose"]["T_world_from_camera"][0][0] = 2
        self.save()
        with self.assertRaisesRegex(ValueError, "rigid"):
            load_raw_capture(self.session)
        self.metadata["pose"]["T_world_from_camera"] = np.eye(4).tolist()
        self.metadata["intrinsics"]["K"][0][0] = -1
        self.save()
        with self.assertRaisesRegex(ValueError, "intrinsics"):
            load_raw_capture(self.session)

    def test_rejects_paths_that_escape_original(self):
        self.metadata["depth"]["path"] = "../../../../../outside.bin"
        self.save()
        with self.assertRaisesRegex(ValueError, "inside original"):
            load_raw_capture(self.session)

    def test_rejects_image_dimensions_and_corrupt_confidence(self):
        frame, = load_raw_capture(self.session)
        Image.new("RGB", (4, 6)).save(self.base / "rgb.png")
        with self.assertRaisesRegex(ValueError, "RGB dimensions"):
            load_rgbd(frame)
        Image.new("RGB", (8, 6)).save(self.base / "rgb.png")
        np.full((3, 4), 255, dtype="u1").tofile(self.base / "confidence.bin")
        with self.assertRaisesRegex(ValueError, "confidence values"):
            load_rgbd(frame)

    def test_flat_capture_and_timestamp_order(self):
        original = self.session / "original"
        # Move only the temporary test fixture to exercise the flat contract.
        self.part.rename(original / "flat")
        (original / "flat" / "frames").rename(original / "frames")
        (original / "flat" / "capture.json").rename(original / "capture.json")
        frames = load_raw_capture(self.session)
        self.assertEqual([frame.frame_id for frame in frames], ["frame-a"])

    def test_duplicate_timestamps_and_mixed_session_parts_rejected(self):
        import shutil
        second = self.part.parent / "capture-0002"
        shutil.copytree(self.part, second)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_raw_capture(self.session)
        capture = dict(self.capture, session_id="another-session")
        (second / "capture.json").write_text(json.dumps(capture), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            load_raw_capture(self.session)

    def test_rejects_missing_frame_metadata_and_wrong_count(self):
        self.capture["frame_count"] = 2
        self.save()
        with self.assertRaisesRegex(ValueError, "frame_count"):
            load_raw_capture(self.session)
        self.capture["frame_count"] = 1
        self.save()
        (self.base / "metadata.json").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            load_raw_capture(self.session)


if __name__ == "__main__":
    unittest.main()
