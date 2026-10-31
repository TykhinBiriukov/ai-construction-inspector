"""Known RGB-D geometry checks for lifting masks onto existing mesh faces."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from geometry_engine.segmentation.mask_projection import MaskProjectionService, ProjectionSettings
from geometry_engine.common.mesh import write_mesh
from geometry_engine.reconstruction.preprocessing import preprocess_mesh


ENGINE = Path(__file__).resolve().parents[1]


class MaskProjectionTests(unittest.TestCase):
    def test_rejected_projection_camera_cannot_label_even_matching_depth(self):
        stage=self.session/'projection_alignment';stage.mkdir()
        for name in ('report.json','cameras.json'):
            self.write_json(stage/name,{})
        with patch('geometry_engine.segmentation.projection_alignment.load_projection_cameras',return_value={
                '000001':{'status':'rejected'}}):
            _,data,arrays=self.run_projection(alignment_dir=stage)
        self.assertEqual(len(arrays['face_ids']),0)
        self.assertIn('camera_alignment_unconfirmed',data['observations'][0]['rejection_reasons'])
        self.assertEqual(data['frames'][0]['camera_status'],'rejected')

    def test_projection_camera_changes_visibility_not_mesh_or_metadata(self):
        stage=self.session/'projection_alignment';stage.mkdir()
        for name in ('report.json','cameras.json'):
            self.write_json(stage/name,{})
        mesh=self.session/'mesh_preprocessing/mesh.ply';meta=self.frame_dir/'metadata.json'
        originals=[p.read_bytes() for p in (mesh,meta)]
        # Camera moves away from surface: observed depth 2m no longer agrees with 3m rendering.
        transform=np.eye(4);transform[2,3]=-1
        with patch('geometry_engine.segmentation.projection_alignment.load_projection_cameras',return_value={
                '000001':{'status':'accepted_refined','T_world_from_camera_cv':transform.tolist()}}):
            _,data,arrays=self.run_projection(alignment_dir=stage)
        self.assertEqual(len(arrays['face_ids']),0)
        self.assertEqual(data['frames'][0]['T_world_from_camera_cv'],transform.tolist())
        self.assertEqual(originals,[p.read_bytes() for p in (mesh,meta)])

    def test_strict_agreement_rejects_small_door_scrap_but_keeps_missing_glass(self):
        settings=replace(self.settings,min_comparable_depth_agreement=.6)
        depth=np.full((12,16),2500,dtype=np.uint16);depth[:1,:]=2000
        self.save_depth(depth)
        _,bad,_=self.run_projection(output='conflicting',settings=settings)
        self.assertIn('depth_inconsistent_with_mesh',bad['observations'][0]['rejection_reasons'])
        depth[1:,:]=0;self.save_depth(depth)
        _,partial,_=self.run_projection(output='missing',settings=replace(settings,min_face_fraction=.01))
        self.assertNotIn('depth_inconsistent_with_mesh',partial['observations'][0]['rejection_reasons'])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ENGINE)
        self.addCleanup(temporary.cleanup)
        self.session = Path(temporary.name)
        self.original = self.session / "original"
        self.frame_dir = self.original / "frames" / "000001"
        self.frame_dir.mkdir(parents=True)
        self.segmentation = self.session / "segmentation"
        (self.segmentation / "masks" / "000001").mkdir(parents=True)
        self.settings = ProjectionSettings(mask_erode_pixels=0, raster_scale=1)
        self.metadata = {
            "frame_id": "000001", "timestamp_s": 0.,
            "rgb": {"path": "rgb.png", "width": 16, "height": 12},
            "depth": {"path": "depth.png", "width": 16, "height": 12,
                      "encoding": "uint16_png", "scale_to_meters": .001, "invalid_value": 0},
            "confidence": {"path": "confidence.png", "width": 16, "height": 12, "encoding": "uint8_png"},
            "intrinsics": {"K": [[8., 0., 7.5], [0., 8., 5.5], [0., 0., 1.]], "width": 16, "height": 12},
            "pose": {"T_world_from_camera": np.eye(4).tolist()},
        }
        Image.new("RGB", (16, 12), (80, 90, 100)).save(self.frame_dir / "rgb.png")
        self.save_depth(np.full((12, 16), 2000, dtype=np.uint16))
        Image.fromarray(np.full((12, 16), 2, dtype=np.uint8)).save(self.frame_dir / "confidence.png")
        Image.fromarray(np.full((12, 16), 255, dtype=np.uint8)).save(self.segmentation / "masks/000001/000000.png")
        self.write_json(self.original / "scene.json", {
            "schema_version": "1.0", "scene_id": self.session.name, "mesh": "mesh.ply",
            "coordinate_system": {"camera_axes": {"x": "right", "y": "down", "z": "forward"},
                                  "length_unit": "meter"},
        })
        self.vertices = np.array([[-2, -2, 2], [2, -2, 2], [2, 2, 2], [-2, 2, 2],
                                  [-4, -4, 4], [4, -4, 4], [4, 4, 4], [-4, 4, 4]], dtype=float)
        self.faces = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]], dtype=np.int32)
        write_mesh(self.original / "mesh.ply", self.vertices, np.full((8, 4), 255, dtype=np.uint8), self.faces)
        preprocess_mesh(self.original / "mesh.ply", self.session / "mesh_preprocessing")
        self.save_metadata()
        self.write_json(self.segmentation / "report.json", {"schema_version": "1.0", "status": "completed", "model": "sam-3.1"})

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")

    def save_depth(self, depth):
        Image.fromarray(depth).save(self.frame_dir / "depth.png")

    def save_metadata(self):
        self.write_json(self.frame_dir / "metadata.json", self.metadata)
        row = {
            "frame_id": "000001", "timestamp_s": 0., "width": 16, "height": 12,
            "rgb": "original/frames/000001/rgb.png", "metadata": "original/frames/000001/metadata.json",
            "rgb_sha256": hashlib.sha256((self.frame_dir / "rgb.png").read_bytes()).hexdigest(),
            "metadata_sha256": hashlib.sha256((self.frame_dir / "metadata.json").read_bytes()).hexdigest(),
            "detections": [{"observation_id": "000001:000000", "label": "wall", "score": .9,
                            "mask": "masks/000001/000000.png", "area_pixels": 192,
                            "bbox_xyxy": [0, 0, 16, 12]}],
        }
        self.manifest = {"schema_version": "1.0", "session_id": self.session.name,
                         "source_path_base": "session_directory", "artifact_path_base": "segmentation_directory",
                         "mask_values": [0, 255], "bbox_convention": "xyxy_pixels_max_exclusive",
                         "instance_scope": "per_frame_observation", "frames": [row]}
        self.write_json(self.segmentation / "detections.json", self.manifest)

    def run_projection(self, *, output="mask_projection", settings=None, **kwargs):
        destination = self.session / output
        report = MaskProjectionService(settings or self.settings).project(self.session, destination, **kwargs)
        observations = json.loads((destination / "observations.json").read_text(encoding="utf-8"))
        arrays = None
        for path in destination.glob("*.npz"):
            with np.load(path) as values:
                if {"offsets", "face_ids", "face_weights"}.issubset(values.files):
                    arrays = {name: values[name].copy() for name in values.files}
                    break
        self.assertIsNotNone(arrays, "Expected sparse observation-to-face correspondence")
        return report, observations, arrays

    def test_only_nearest_depth_supported_faces_are_projected(self):
        report, data, arrays = self.run_projection()
        self.assertEqual(report["status"], "completed")
        row = data["observations"][0]
        self.assertGreater(row["supported_pixels"], 0)
        self.assertGreater(row["face_count"], 0)
        self.assertEqual(row["observation_id"], "000001:000000")
        with np.load(self.session / "mesh_preprocessing/geometry.npz") as geometry:
            triangle_z = geometry["vertices"][geometry["faces"]][:, :, 2].mean(axis=1)
        self.assertTrue(np.all(triangle_z[arrays["face_ids"]] == 2.))
        self.assertTrue(np.all(arrays["face_weights"] > 0))
        self.assertEqual(arrays["offsets"].tolist(), [0, len(arrays["face_ids"])])

    def test_inconsistent_depth_does_not_label_occluded_or_front_faces(self):
        self.save_depth(np.full((12, 16), 4000, dtype=np.uint16))
        _, data, arrays = self.run_projection()
        self.assertEqual(data["observations"][0]["supported_pixels"], 0)
        self.assertEqual(len(arrays["face_ids"]), 0)

    def test_partial_depth_kept_without_whole_mask_fraction_gate(self):
        depth = np.zeros((12, 16), dtype=np.uint16)
        depth[:2, :] = 2000
        self.save_depth(depth)
        # Low per-face threshold isolates the whole-observation gate here.
        settings = replace(self.settings, min_face_fraction=.01)
        _, data, arrays = self.run_projection(settings=settings)
        self.assertLess(data["observations"][0]["support_fraction"], .2)
        self.assertGreater(len(arrays["face_ids"]), 0)
        _, _, strict = self.run_projection(output="strict", settings=replace(settings, min_observation_support=.2))
        self.assertEqual(len(strict["face_ids"]), 0)

    def test_conflicting_door_mask_does_not_label_small_accidental_remnant(self):
        # A wrong camera/mesh alignment leaves a few depth coincidences near
        # a doorway's lower edge. Those pixels must not become a new door.
        depth = np.full((12, 16), 1700, dtype=np.uint16)
        depth[-1, :8] = 2000
        self.save_depth(depth)
        self.manifest["frames"][0]["detections"][0]["label"] = "door"
        self.write_json(self.segmentation / "detections.json", self.manifest)
        settings = replace(self.settings, min_face_fraction=.01)
        report, data, arrays = self.run_projection(settings=settings)
        row = data["observations"][0]
        self.assertGreater(row["candidate_face_count"], 0)
        self.assertEqual(row["supported_pixels"], 8)
        self.assertEqual(row["comparable_depth_pixels"], 192)
        self.assertAlmostEqual(row["comparable_depth_agreement"], 8/192)
        self.assertEqual(row["rejection_reasons"], ["depth_inconsistent_with_mesh"])
        self.assertEqual(row["status"], "unconfirmed")
        self.assertEqual(report["depth_inconsistent_observations"], 1)
        self.assertEqual(len(arrays["face_ids"]), 0)
        # The explicit switch exists for diagnostics; the default is guarded.
        _, _, unguarded = self.run_projection(output="unguarded", settings=replace(
            settings, min_comparable_depth_agreement=0))
        self.assertGreater(len(unguarded["face_ids"]), 0)

    def test_window_with_sparse_but_consistent_depth_keeps_frame_evidence(self):
        # Same small support fraction as the bad door, but glass has no depth
        # instead of contradictory measurements. Retain actual frame surfaces.
        depth = np.zeros((12, 16), dtype=np.uint16)
        depth[-1, :8] = 2000
        self.save_depth(depth)
        self.manifest["frames"][0]["detections"][0]["label"] = "window"
        self.write_json(self.segmentation / "detections.json", self.manifest)
        _, data, arrays = self.run_projection(settings=replace(self.settings, min_face_fraction=.01))
        row = data["observations"][0]
        self.assertEqual(row["supported_pixels"], 8)
        self.assertEqual(row["comparable_depth_pixels"], 8)
        self.assertEqual(row["comparable_depth_agreement"], 1.)
        self.assertEqual(row["invalid_depth_pixels"], 184)
        self.assertEqual(row["depth_mismatch_pixels"], 0)
        self.assertEqual(row["quality"], "partial_depth_support")
        self.assertEqual(row["rejection_reasons"], [])
        self.assertGreater(len(arrays["face_ids"]), 0)

    def test_rejection_counters_separate_missing_confidence_and_conflicting_depth(self):
        depth = np.full((12, 16), 2000, dtype=np.uint16)
        depth[:, :4] = 0
        depth[:, 8:12] = 1500
        self.save_depth(depth)
        confidence = np.full((12, 16), 2, dtype=np.uint8)
        confidence[:, :8] = 0  # Invalid depth takes priority over confidence.
        Image.fromarray(confidence).save(self.frame_dir / "confidence.png")
        _, data, _ = self.run_projection(settings=replace(self.settings, min_face_fraction=.01))
        row = data["observations"][0]
        for name in ("invalid_depth_pixels", "low_confidence_pixels", "depth_mismatch_pixels", "supported_pixels"):
            self.assertEqual(row[name], 48, name)
        self.assertEqual(row["comparable_depth_pixels"], 96)
        self.assertEqual(row["depth_rejected_pixels"], 144)
        self.assertEqual(sum(row[name] for name in ("no_mesh_pixels", "invalid_depth_pixels",
                         "low_confidence_pixels", "depth_mismatch_pixels", "supported_pixels")), row["mask_pixels"])

    def test_comparable_agreement_setting_rejects_invalid_values(self):
        for value in (-.01, 1.01, float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(self.settings, min_comparable_depth_agreement=value)

    def test_missing_depth_retains_unconfirmed_observation_without_faces(self):
        self.metadata["depth"] = self.metadata["confidence"] = None
        self.save_metadata()
        _, data, arrays = self.run_projection()
        self.assertEqual(len(data["observations"]), 1)
        self.assertEqual(data["observations"][0]["supported_pixels"], 0)
        self.assertEqual(data["observations"][0]["face_count"], 0)
        self.assertEqual(len(arrays["face_ids"]), 0)

    def test_low_confidence_depth_is_not_accepted(self):
        Image.fromarray(np.zeros((12, 16), dtype=np.uint8)).save(self.frame_dir / "confidence.png")
        _, data, arrays = self.run_projection()
        self.assertEqual(data["observations"][0]["supported_pixels"], 0)
        self.assertEqual(len(arrays["face_ids"]), 0)

    def test_mask_in_mesh_hole_remains_unconfirmed(self):
        # A tiny central surface leaves the image's top-left region uncovered.
        vertices = np.array([[-.2, -.2, 2.], [.2, -.2, 2.], [.2, .2, 2.], [-.2, .2, 2.]])
        faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
        write_mesh(self.original / "mesh.ply", vertices, np.full((4, 4), 255, dtype=np.uint8), faces)
        prepared = self.session / "mesh-small"
        preprocess_mesh(self.original / "mesh.ply", prepared)
        mask = np.zeros((12, 16), dtype=np.uint8)
        mask[:3, :3] = 255
        Image.fromarray(mask).save(self.segmentation / "masks/000001/000000.png")
        _, data, arrays = self.run_projection(mesh_dir=prepared)
        observation = data["observations"][0]
        self.assertEqual(observation["supported_pixels"], 0)
        self.assertEqual(observation["no_mesh_pixels"], 9)
        self.assertEqual(len(arrays["face_ids"]), 0)

    def test_mismatched_sensor_aspect_ratio_requires_explicit_registration(self):
        self.metadata["depth"].update(width=8, height=8)
        self.metadata["confidence"] = None
        self.save_depth(np.full((8, 8), 2000, dtype=np.uint16))
        self.save_metadata()
        with self.assertRaisesRegex(ValueError, "registration"):
            self.run_projection()

    def test_prepared_mesh_from_another_source_is_rejected(self):
        mesh_path = self.original / "mesh.ply"
        mesh_path.write_bytes(mesh_path.read_bytes() + b"different source")
        with self.assertRaisesRegex(ValueError, "different source"):
            self.run_projection()

    def test_native_arkit_pose_and_cv_pose_project_identically(self):
        _, _, cv_arrays = self.run_projection(output="projection-cv")
        (self.original / "scene.json").unlink()
        self.write_json(self.original / "capture.json", {
            "schema_version": "1.0", "session_id": self.session.name, "mesh": "mesh.ply",
            "coordinate_system": "arkit", "length_unit": "meter", "matrix_storage": "rows",
        })
        self.metadata["pose"]["T_world_from_camera"] = np.diag([1., -1., -1., 1.]).tolist()
        self.save_metadata()
        _, _, arkit_arrays = self.run_projection(output="projection-arkit")
        np.testing.assert_array_equal(arkit_arrays["face_ids"], cv_arrays["face_ids"])
        np.testing.assert_allclose(arkit_arrays["face_weights"], cv_arrays["face_weights"])

    def test_low_resolution_registered_depth_is_resized_without_changing_geometry(self):
        _, _, reference = self.run_projection(output="projection-full-depth")
        self.metadata["depth"].update(width=8, height=6)
        self.metadata["confidence"].update(width=8, height=6)
        self.save_depth(np.full((6, 8), 2000, dtype=np.uint16))
        Image.fromarray(np.full((6, 8), 2, dtype=np.uint8)).save(self.frame_dir / "confidence.png")
        self.save_metadata()
        _, _, downsampled = self.run_projection(output="projection-small-depth")
        np.testing.assert_array_equal(downsampled["face_ids"], reference["face_ids"])

    def test_binary_phone_depth_in_meters_matches_png_depth(self):
        _, _, reference = self.run_projection(output="projection-png")
        for depth_name, confidence_name in (("depth.bin", "confidence.bin"), ("depth.f32", "confidence.u8")):
            with self.subTest(depth=depth_name):
                np.full((12, 16), 2., dtype="<f4").tofile(self.frame_dir / depth_name)
                np.full((12, 16), 2, dtype="u1").tofile(self.frame_dir / confidence_name)
                self.metadata["depth"] = {"path": depth_name, "width": 16, "height": 12,
                                          "dtype": "float32", "unit": "meter", "byte_order": "little"}
                self.metadata["confidence"] = {"path": confidence_name, "width": 16, "height": 12, "dtype": "uint8"}
                self.save_metadata()
                _, _, binary = self.run_projection(output="projection-"+depth_name)
                np.testing.assert_array_equal(binary["face_ids"], reference["face_ids"])
                np.testing.assert_allclose(binary["face_weights"], reference["face_weights"])

    def test_supersampling_preserves_planar_face_correspondence(self):
        _, _, low = self.run_projection(output="projection-raster1")
        _, _, high = self.run_projection(output="projection-raster2", settings=replace(self.settings, raster_scale=2))
        self.assertEqual(set(low["face_ids"]), set(high["face_ids"]))

    def test_original_files_and_previous_stage_files_are_unchanged(self):
        inputs = [self.original, self.segmentation, self.session / "mesh_preprocessing"]
        before = {str(path.relative_to(self.session)): path.read_bytes()
                  for folder in inputs for path in folder.rglob("*") if path.is_file()}
        self.run_projection()
        after = {str(path.relative_to(self.session)): path.read_bytes()
                 for folder in inputs for path in folder.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_changed_rgb_or_metadata_hash_is_rejected(self):
        metadata_path = self.frame_dir / "metadata.json"
        metadata_path.write_text(metadata_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_projection()
        self.assertFalse((self.session / "mask_projection").exists())

    def test_mask_paths_cannot_escape_segmentation_directory(self):
        self.manifest["frames"][0]["detections"][0]["mask"] = "../original/frames/000001/rgb.png"
        self.write_json(self.segmentation / "detections.json", self.manifest)
        with self.assertRaises(ValueError):
            self.run_projection()

    def test_existing_result_and_original_output_are_protected(self):
        self.run_projection()
        service = MaskProjectionService(self.settings)
        with self.assertRaises(FileExistsError):
            service.project(self.session)
        with self.assertRaises(ValueError):
            service.project(self.session, self.original / "projection")


if __name__ == "__main__":
    unittest.main()
