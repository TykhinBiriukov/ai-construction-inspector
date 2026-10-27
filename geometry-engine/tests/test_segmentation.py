"""Segmentation stage contracts, using real images and a deterministic backend.

These tests exercise storage and geometry alignment, not SAM model accuracy.
"""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from geometry_engine.sam_backend import Detection
from geometry_engine.segmentation import SegmentationService, SegmentationSettings


ENGINE = Path(__file__).resolve().parents[1]


class FixtureBackend:
    model_info = {"name": "test-double", "implementation": "test-only"}

    def __init__(self, detections=(), fail_on=None):
        self.detections = list(detections)
        self.fail_on = fail_on
        self.calls = []

    def predict(self, image, prompts):
        self.calls.append((np.array(image), tuple(prompts)))
        if self.fail_on == len(self.calls):
            raise RuntimeError("Simulated upstream failure")
        return self.detections


class SegmentationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ENGINE)
        self.addCleanup(temporary.cleanup)
        self.session = Path(temporary.name)
        self.original = self.session / "original"
        self.original.mkdir()
        self.selected = self.session / "selected_frames" / "selected_frames.json"
        self.selected.parent.mkdir()
        self.rows = []
        self.pixels = np.zeros((30, 40, 3), dtype=np.uint8)
        self.pixels[:, :, 0] = np.arange(40, dtype=np.uint8)[None, :] * 5
        self.pixels[:, :, 1] = np.arange(30, dtype=np.uint8)[:, None] * 7
        self.pixels[:, :, 2] = 80
        self.frame("000001")

    def write_json(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def save_selection(self):
        self.write_json(self.selected, {
            "schema_version": "1.0", "path_base": "session_directory",
            "session_id": self.session.name, "selected_count": len(self.rows),
            "frames": self.rows,
        })

    def frame(self, identity):
        directory = self.original / "frames" / identity
        directory.mkdir(parents=True)
        Image.fromarray(self.pixels).save(directory / "rgb.png")
        metadata = {
            "frame_id": identity, "timestamp_s": float(len(self.rows)),
            "rgb": {"path": "rgb.png", "width": 40, "height": 30},
            "intrinsics": {"K": [[30, 0, 20], [0, 30, 15], [0, 0, 1]]},
            "pose": {"T_world_from_camera": np.eye(4).tolist()},
        }
        self.write_json(directory / "metadata.json", metadata)
        self.rows.append({
            "frame_id": identity, "timestamp_s": metadata["timestamp_s"], "selected": True,
            "rgb": (directory / "rgb.png").relative_to(self.session).as_posix(),
            "metadata": (directory / "metadata.json").relative_to(self.session).as_posix(),
        })
        self.save_selection()
        return directory

    @staticmethod
    def mask():
        mask = np.zeros((30, 40), dtype=bool)
        mask[5:15, 8:20] = True
        return mask

    def result(self, directory="segmentation"):
        return json.loads((self.session / directory / "detections.json").read_text(encoding="utf-8"))

    def original_snapshot(self):
        return {p.relative_to(self.original).as_posix(): p.read_bytes()
                for p in self.original.rglob("*") if p.is_file()}

    def test_full_resolution_mask_pixels_and_sources_are_preserved(self):
        source_before = self.original_snapshot()
        selected_before = self.selected.read_bytes()
        mask = self.mask()
        backend = FixtureBackend([Detection("window", 0.9, mask)])
        report = SegmentationService(backend).segment(self.session)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(len(backend.calls), 1)
        np.testing.assert_array_equal(backend.calls[0][0], self.pixels)
        self.assertEqual(backend.calls[0][1], ("wall", "window", "door"))
        manifest = self.result()
        self.assertEqual(manifest["source_path_base"], "session_directory")
        self.assertEqual(manifest["artifact_path_base"], "segmentation_directory")
        self.assertEqual(manifest["bbox_convention"], "xyxy_pixels_max_exclusive")
        self.assertEqual(manifest["instance_scope"], "per_frame_observation")
        frame = manifest["frames"][0]
        self.assertEqual(frame["frame_id"], "000001")
        self.assertEqual((frame["width"], frame["height"]), (40, 30))
        self.assertEqual(frame["rgb"], self.rows[0]["rgb"])
        self.assertEqual(frame["metadata"], self.rows[0]["metadata"])
        detection = frame["detections"][0]
        self.assertEqual(detection["label"], "window")
        self.assertAlmostEqual(detection["score"], 0.9)
        self.assertEqual(detection["area_pixels"], 120)
        self.assertEqual(detection["bbox_xyxy"], [8, 5, 20, 15])
        with Image.open(self.session / "segmentation" / detection["mask"]) as saved:
            self.assertEqual(saved.size, (40, 30))
            np.testing.assert_array_equal(np.asarray(saved), mask.astype(np.uint8) * 255)
        with Image.open(self.session / "segmentation" / "overlays" / "000001.jpg") as overlay:
            self.assertEqual(overlay.size, (40, 30))
        self.assertEqual(self.original_snapshot(), source_before)
        self.assertEqual(self.selected.read_bytes(), selected_before)

    def test_low_confidence_empty_and_tiny_masks_are_filtered(self):
        small = np.zeros((30, 40), dtype=bool)
        small[0, 0] = True
        backend = FixtureBackend([
            Detection("wall", 0.4, self.mask()),
            Detection("window", 0.99, np.zeros((30, 40), dtype=bool)),
            Detection("door", 0.99, small),
            Detection("window", 0.8, self.mask()),
        ])
        SegmentationService(backend).segment(self.session)
        detections = self.result()["frames"][0]["detections"]
        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0]["label"], "window")
        self.assertEqual(detections[0]["score"], 0.8)

    def test_no_matches_is_a_completed_frame(self):
        report = SegmentationService(FixtureBackend()).segment(self.session)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(len(self.result()["frames"]), 1)
        self.assertEqual(self.result()["frames"][0]["detections"], [])

    def test_invalid_backend_mask_shape_or_values_never_publish_result(self):
        invalid_masks = [np.zeros((15, 20), dtype=bool), np.full((30, 40), 0.5)]
        for index, mask in enumerate(invalid_masks):
            with self.subTest(index=index):
                output = self.session / f"segmentation-invalid-{index}"
                service = SegmentationService(FixtureBackend([Detection("wall", 0.9, mask)]))
                with self.assertRaises((ValueError, RuntimeError)):
                    service.segment(self.session, output)
                self.assertFalse(output.exists())

    def test_upstream_failure_after_first_frame_leaves_no_complete_output(self):
        self.frame("000002")
        backend = FixtureBackend([Detection("wall", 0.9, self.mask())], fail_on=2)
        before = self.original_snapshot()
        with self.assertRaisesRegex(RuntimeError, "upstream failure"):
            SegmentationService(backend).segment(self.session)
        self.assertEqual(len(backend.calls), 2)
        self.assertFalse((self.session / "segmentation").exists())
        self.assertEqual(self.original_snapshot(), before)

    def test_existing_output_is_not_overwritten_or_sent_to_backend(self):
        output = self.session / "segmentation"
        output.mkdir()
        sentinel = output / "keep.txt"
        sentinel.write_text("Previous result", encoding="utf-8")
        backend = FixtureBackend()
        with self.assertRaises(FileExistsError):
            SegmentationService(backend).segment(self.session)
        self.assertEqual(backend.calls, [])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "Previous result")

    def test_output_cannot_be_original_or_outside_session(self):
        for output in (self.original / "segmentation", self.session.parent / "outside-segmentation-test"):
            with self.subTest(output=output):
                backend = FixtureBackend()
                with self.assertRaises(ValueError):
                    SegmentationService(backend).segment(self.session, output)
                self.assertEqual(backend.calls, [])

    def test_duplicate_frame_ids_are_rejected_before_any_remote_call(self):
        self.rows.append(dict(self.rows[0]))
        self.save_selection()
        backend = FixtureBackend()
        with self.assertRaises(ValueError):
            SegmentationService(backend).segment(self.session)
        self.assertEqual(backend.calls, [])

    def test_frame_ids_cannot_collide_on_windows(self):
        self.frame("000002")
        for row, identity in zip(self.rows, ("frame", "FRAME")):
            row["frame_id"] = identity
            metadata_path = self.session / row["metadata"]
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["frame_id"] = identity
            self.write_json(metadata_path, metadata)
        self.save_selection()
        backend = FixtureBackend([Detection("wall", 0.9, self.mask())])
        with self.assertRaises(ValueError):
            SegmentationService(backend).segment(self.session)
        self.assertEqual(backend.calls, [])
        self.assertFalse((self.session / "segmentation").exists())

    def test_windows_reserved_frame_names_are_rejected_before_remote_call(self):
        metadata_path = self.session / self.rows[0]["metadata"]
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        for identity in ("CON", "con", "PRN", "AUX", "NUL", "COM1", "LPT9"):
            with self.subTest(identity=identity):
                self.rows[0]["frame_id"] = identity
                self.write_json(metadata_path, {**metadata, "frame_id": identity})
                self.save_selection()
                backend = FixtureBackend([Detection("wall", 0.9, self.mask())])
                with self.assertRaises(ValueError):
                    SegmentationService(backend).segment(self.session)
                self.assertEqual(backend.calls, [])

    def test_invalid_timestamp_is_rejected_before_remote_call(self):
        for timestamp in (float("nan"), float("inf"), "1.0", True):
            with self.subTest(timestamp=timestamp):
                self.rows[0]["timestamp_s"] = timestamp
                self.save_selection()
                backend = FixtureBackend()
                with self.assertRaises(ValueError):
                    SegmentationService(backend).segment(self.session)
                self.assertEqual(backend.calls, [])

    def test_traversal_in_rgb_path_is_rejected_before_remote_call(self):
        self.rows[0]["rgb"] = "../outside.png"
        self.save_selection()
        backend = FixtureBackend()
        with self.assertRaises(ValueError):
            SegmentationService(backend).segment(self.session)
        self.assertEqual(backend.calls, [])

    def test_manifest_contract_and_count_are_checked(self):
        valid = json.loads(self.selected.read_text(encoding="utf-8"))
        for field, value in (("schema_version", "99"), ("path_base", "original"), ("selected_count", 99)):
            with self.subTest(field=field):
                self.write_json(self.selected, {**valid, field: value})
                backend = FixtureBackend()
                with self.assertRaises(ValueError):
                    SegmentationService(backend).segment(self.session)
                self.assertEqual(backend.calls, [])

    def test_metadata_identity_and_image_size_are_checked(self):
        path = self.session / self.rows[0]["metadata"]
        metadata = json.loads(path.read_text(encoding="utf-8"))
        for updated in ({**metadata, "frame_id": "wrong"},
                        {**metadata, "rgb": {**metadata["rgb"], "width": 50}}):
            with self.subTest(metadata=updated):
                self.write_json(path, updated)
                backend = FixtureBackend()
                with self.assertRaises(ValueError):
                    SegmentationService(backend).segment(self.session)
                self.assertEqual(backend.calls, [])

    def test_frame_limit_and_custom_prompts_control_remote_work(self):
        self.frame("000002")
        backend = FixtureBackend()
        settings = SegmentationSettings(prompts=("window", "chair"), min_mask_pixels=1)
        SegmentationService(backend, settings).segment(self.session, max_frames=1)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.calls[0][1], ("window", "chair"))
        self.assertEqual(len(self.result()["frames"]), 1)

    def test_invalid_frame_limits_do_not_make_remote_calls(self):
        for limit in (0, -1, 1.5, True):
            with self.subTest(limit=limit):
                backend = FixtureBackend()
                with self.assertRaises(ValueError):
                    SegmentationService(backend).segment(self.session, max_frames=limit)
                self.assertEqual(backend.calls, [])

    def test_settings_reject_invalid_prompts_and_thresholds(self):
        invalid = [
            {"prompts": ()}, {"prompts": ("wall", "wall")},
            {"prompts": ("wall, window",)}, {"prompts": (" window ",)},
            {"prompts": ("",)}, {"prompts": "window"},
            {"prompts": ("a" * 121,)}, {"prompts": tuple(str(i) for i in range(33))},
            {"min_confidence": -0.1}, {"min_confidence": 1.1},
            {"min_confidence": float("nan")}, {"min_confidence": float("inf")},
            {"min_mask_pixels": 0}, {"min_mask_pixels": -1},
            {"min_mask_pixels": 1.5}, {"min_mask_pixels": True},
        ]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SegmentationSettings(**kwargs)

    def test_resume_only_sends_missing_frames_and_keeps_previous_artifacts(self):
        self.frame("000002")
        first = FixtureBackend([Detection("window", .9, self.mask())])
        SegmentationService(first).segment(self.session, max_frames=1)
        output = self.session / "segmentation"
        old_frame = self.result()["frames"][0]
        old_mask = (output / old_frame["detections"][0]["mask"]).read_bytes()
        old_overlay = (output / old_frame["overlay"]).read_bytes()
        remaining = FixtureBackend([Detection("door", .8, self.mask())])
        report = SegmentationService(remaining).segment(self.session, resume=True)
        self.assertEqual(len(remaining.calls), 1)
        self.assertEqual(report["reused_count"], 1)
        self.assertEqual(report["newly_processed_count"], 1)
        self.assertEqual(report["processed_count"], 2)
        self.assertEqual(report["class_counts"], {"window": 1, "door": 1})
        self.assertEqual(self.result()["frames"][0], old_frame)
        self.assertEqual((output / old_frame["detections"][0]["mask"]).read_bytes(), old_mask)
        self.assertEqual((output / old_frame["overlay"]).read_bytes(), old_overlay)
        again = FixtureBackend()
        result = SegmentationService(again).segment(self.session, resume=True)
        self.assertEqual(again.calls, [])
        self.assertEqual(result["newly_processed_count"], 0)

    def test_resume_failure_keeps_existing_batch_unchanged(self):
        self.frame("000002")
        SegmentationService(FixtureBackend()).segment(self.session, max_frames=1)
        output = self.session / "segmentation"
        before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
        failing = FixtureBackend(fail_on=1)
        with self.assertRaises(RuntimeError):
            SegmentationService(failing).segment(self.session, resume=True)
        self.assertEqual(before, {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()})

    def test_resume_rejects_changed_settings_or_input_before_remote_calls(self):
        self.frame("000002")
        SegmentationService(FixtureBackend()).segment(self.session, max_frames=1)
        backend = FixtureBackend()
        with self.assertRaisesRegex(ValueError, "settings"):
            SegmentationService(backend, SegmentationSettings(min_confidence=.8)).segment(self.session, resume=True)
        rgb = self.session / self.rows[0]["rgb"]
        rgb.write_bytes(rgb.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "source data changed"):
            SegmentationService(backend).segment(self.session, resume=True)
        self.assertEqual(backend.calls, [])

    def test_resume_does_not_drop_previously_completed_frames_when_limit_is_lowered(self):
        self.frame("000002")
        SegmentationService(FixtureBackend()).segment(self.session)
        backend = FixtureBackend()
        with self.assertRaisesRegex(ValueError, "do not lower"):
            SegmentationService(backend).segment(self.session, max_frames=1, resume=True)
        self.assertEqual(backend.calls, [])

    def test_resume_skips_frames_with_no_detections_and_rejects_missing_masks(self):
        self.frame("000002")
        SegmentationService(FixtureBackend()).segment(self.session, max_frames=1)
        backend = FixtureBackend([Detection("wall", .9, self.mask())])
        SegmentationService(backend).segment(self.session, resume=True)
        self.assertEqual(len(backend.calls), 1)
        row = self.result()["frames"][1]
        (self.session / "segmentation" / row["detections"][0]["mask"]).unlink()
        with self.assertRaises(ValueError):
            SegmentationService(FixtureBackend()).segment(self.session, resume=True)


if __name__ == "__main__":
    unittest.main()
