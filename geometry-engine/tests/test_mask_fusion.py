"""Multi-view labeling and instance conflicts on known mesh face sets."""

import unittest

import numpy as np

from geometry_engine.mask_fusion import FusionSettings, fuse_observations, region_relation


class MaskFusionTests(unittest.TestCase):
    def setUp(self):
        self.faces = np.arange(36, dtype=np.int32).reshape(12, 3)
        self.areas = np.ones(12)

    def observation(self, identity, frame, ids, label="door", score=.9, weights=None):
        ids = np.asarray(ids, dtype=np.int32)
        return {"observation_id": identity, "frame_id": frame, "label": label, "score": score,
                "face_ids": ids, "face_weights": np.ones(len(ids)) if weights is None else np.asarray(weights)}

    def fuse(self, rows, settings=None):
        before = self.faces.copy()
        result = fuse_observations(self.faces, self.areas, rows, settings)
        np.testing.assert_array_equal(before, self.faces)
        return result

    def test_two_views_merge_one_object_and_unobserved_faces_stay_unknown(self):
        result = self.fuse([self.observation("a", "frame1", range(6)),
                            self.observation("b", "frame2", range(3, 9))])
        np.testing.assert_array_equal(result["face_class_id"], [-1]*3+[0]*3+[-1]*6)
        np.testing.assert_array_equal(result["face_instance_id"], [-1]*3+[0]*3+[-1]*6)
        self.assertEqual(result["objects"][0]["observation_ids"], ["a", "b"])
        self.assertEqual(result["objects"][0]["surface_area_m2"], 3.)
        self.assertEqual(result["objects"][0]["view_count"], 2)
        np.testing.assert_allclose(result["face_confidence"][3:6], .9)

    def test_duplicate_masks_from_one_frame_do_not_create_multiple_views(self):
        result = self.fuse([self.observation(str(i), "same-frame", range(12)) for i in range(20)])
        self.assertTrue(np.all(result["face_class_id"] == -1))
        self.assertTrue(np.all(result["face_view_count"] == 1))
        self.assertEqual(result["objects"], [])

    def test_class_conflict_is_unknown_instead_of_arbitrary_tiebreak(self):
        rows = [self.observation(f"{label}{frame}", frame, range(12), label, 1.)
                for label in ("door", "wall") for frame in ("a", "b")]
        result = self.fuse(rows)
        self.assertTrue(np.all(result["face_class_id"] == -1))
        self.assertEqual(result["class_conflict_count"], 12)

    def test_class_votes_cap_overlapping_masks_per_frame(self):
        rows = [self.observation(f"door-{frame}-{i}", frame, range(12), "door", .7)
                for i in range(10) for frame in ("a", "b")]
        rows += [self.observation(f"wall-{frame}", frame, range(12), "wall", .9) for frame in ("a", "b")]
        result = self.fuse(rows)
        self.assertTrue(np.all(result["face_class_id"] == -1))
        self.assertTrue(np.all(result["face_view_count"] == 2))

    def test_zero_weight_or_score_cannot_vote(self):
        result = self.fuse([self.observation("a", "a", range(12), weights=np.zeros(12)),
                            self.observation("b", "b", range(12), score=0)])
        self.assertTrue(np.all(result["face_class_id"] == -1))
        self.assertTrue(np.all(result["face_view_count"] == 0))

    def test_same_frame_objects_cannot_merge_through_a_third_view(self):
        result = self.fuse([self.observation("a", "frame1", range(6)),
                            self.observation("b", "frame1", range(6, 12)),
                            self.observation("c", "frame2", range(12)),
                            self.observation("d", "frame3", range(6, 12))])
        self.assertEqual(len(result["objects"]), 2)
        for obj in result["objects"]:
            self.assertFalse({"a", "b"}.issubset(obj["observation_ids"]))
        self.assertNotEqual(result["observation_instance_ids"]["a"], result["observation_instance_ids"]["b"])

    def test_different_labels_never_share_instance_id(self):
        rows = [self.observation(f"door{frame}", frame, range(6), "door") for frame in ("a", "b")]
        rows += [self.observation(f"wall{frame}", frame, range(6, 12), "wall") for frame in ("a", "b")]
        result = self.fuse(rows)
        self.assertEqual(len(result["objects"]), 2)
        self.assertNotEqual(result["face_instance_id"][0], result["face_instance_id"][6])

    def test_sparse_overlap_can_confirm_class_without_inventing_instance(self):
        result = self.fuse([self.observation("a", "a", [0]), self.observation("b", "b", [0])])
        self.assertEqual(result["face_class_id"][0], 0)
        self.assertEqual(result["face_instance_id"][0], -1)
        self.assertEqual(result["objects"], [])

    def test_region_overlap_uses_area_and_weight_not_number_of_faces(self):
        areas = np.array([10., 1., 1.])
        iou, recall_a, recall_b, count = region_relation(np.array([0, 1]), np.array([.5, 1.]),
                                                        np.array([0, 2]), np.array([1., 1.]), areas)
        self.assertAlmostEqual(iou, 5 / 12)
        self.assertAlmostEqual(recall_a, 5 / 6)
        self.assertAlmostEqual(recall_b, 5 / 11)
        self.assertEqual(count, 1)

    def test_empty_input_is_a_valid_unlabeled_mesh(self):
        result = self.fuse([])
        self.assertEqual(result["labels"], [])
        self.assertEqual(result["objects"], [])
        self.assertTrue(np.all(result["face_class_id"] == -1))

    def test_observation_order_does_not_change_output(self):
        rows = [self.observation("b", "b", range(6)), self.observation("a", "a", range(6))]
        first, second = self.fuse(rows), self.fuse(rows[::-1])
        np.testing.assert_array_equal(first["face_instance_id"], second["face_instance_id"])
        self.assertEqual(first["objects"], second["objects"])

    def test_invalid_inputs_are_rejected(self):
        for changes in ({"face_ids": np.array([12])}, {"face_weights": np.array([float("nan")])},
                        {"score": 2}, {"face_ids": np.array([.5])}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.fuse([{**self.observation("a", "a", [0]), **changes}])
        row = self.observation("a", "a", [0])
        with self.assertRaises(ValueError):
            self.fuse([row, row])
        for settings in ({"min_views": 0}, {"min_views": True}, {"min_class_purity": .5},
                         {"instance_overlap": 0}, {"min_overlap_faces": 0}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                FusionSettings(**settings)


if __name__ == "__main__":
    unittest.main()
