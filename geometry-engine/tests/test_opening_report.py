import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from geometry_engine.visualization.opening_report import write_opening_review, projected_box_segments


class OpeningReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.session = Path(temp.name)
        self.original = self.session / 'original'
        self.original.mkdir()
        self.segmentation = self.session / 'segmentation'
        self.segmentation.mkdir()
        Image.new('RGB', (40, 30), (20, 30, 40)).save(self.original / 'rgb.png')
        mask = np.zeros((30, 40), dtype=np.uint8)
        mask[:, :20] = 255
        Image.fromarray(mask).save(self.segmentation / 'mask.png')
        self.evidence = {'frame_id': 'frame-1', 'rgb': 'original/rgb.png', 'mask': 'mask.png'}
        self.report = {'summary': {'candidates': 1}, 'findings': [{
            'finding_id': '<script>alert(1)</script>', 'status': 'candidate_deviation', 'category': 'door',
            'instance_id': 1, 'ifc_guid': 'test', 'measurements': {'width_m': .8},
            'deltas_m': {'width_m': -.3}, 'review_reasons': ['Check <edge>'], 'evidence': [self.evidence]}]}

    def export(self):
        return write_opening_review(self.session/'review', self.report, self.session, self.segmentation)

    def test_portable_evidence_and_untrusted_text_escaped(self):
        result = self.export()
        html = (self.session/'review/review.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>alert(1)', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('photo review required', html)
        self.assertIn('No agent has reviewed the photos yet', html)
        self.assertEqual(result['review'], 'review.html')
        index = json.loads((self.session/'review/evidence_index.json').read_text())
        self.assertTrue((self.session/'review'/index[0]['rgb_thumbnail']).is_file())
        self.assertTrue((self.session/'review'/index[0]['mask_thumbnail']).is_file())
        with Image.open(self.session/'review'/index[0]['rgb_thumbnail']) as rgb, \
             Image.open(self.session/'review'/index[0]['mask_thumbnail']) as mask:
            self.assertFalse(np.array_equal(np.asarray(rgb), np.asarray(mask)))

    def test_six_frame_limit(self):
        self.report['findings'][0]['evidence'] = [self.evidence] * 10
        self.export()
        index = json.loads((self.session/'review/evidence_index.json').read_text())
        self.assertEqual(len(index), 6)

    def test_no_evidence_does_not_assert_absence(self):
        self.report['findings'][0].update(status='unobserved_reference', evidence=[])
        self.export()
        html = (self.session/'review/review.html').read_text(encoding='utf-8')
        self.assertIn('absence is unproven', html)
        self.assertIn('Object absence cannot be confirmed', html)

    def test_capture_escape_is_rejected(self):
        self.evidence['rgb'] = '../outside.png'
        with self.assertRaises(ValueError):
            self.export()

    def test_mask_must_match_rgb(self):
        Image.new('L', (2, 2), 255).save(self.segmentation/'mask.png')
        with self.assertRaisesRegex(ValueError, 'dimensions differ'):
            self.export()

    def camera(self):
        return {'rgb': {'width': 40, 'height': 30},
                'intrinsics': {'K': [[20., 0., 20.], [0., 20., 15.], [0., 0., 1.]]},
                'pose': {'T_world_from_camera': np.eye(4).tolist()}}

    def test_known_arkit_camera_projection_and_behind_camera(self):
        corners = [[-1, 1, -2], [1, 1, -2], [1, -1, -2], [-1, -1, -2]]
        result = projected_box_segments(corners, self.camera(), 'arkit', (40, 30))
        np.testing.assert_allclose(result[0], [[10, 5], [30, 5]])
        np.testing.assert_allclose(result[2], [[30, 25], [10, 25]])
        self.assertEqual(projected_box_segments(-np.asarray(corners), self.camera(), 'arkit', (40, 30)), [])

    def test_viewport_clips_lines_without_drawing_offscreen_edges(self):
        corners = [[-10, 1, -2], [10, 1, -2], [10, -1, -2], [-10, -1, -2]]
        result = projected_box_segments(corners, self.camera(), 'arkit', (40, 30))
        self.assertEqual(len(result), 2)
        np.testing.assert_allclose(result[0], [[0, 5], [39, 5]])
        with self.assertRaisesRegex(ValueError, 'Unknown camera axes'):
            projected_box_segments(corners, self.camera(), {}, (40, 30))

    def test_box_projection_is_recorded_and_drawn_on_mask_copy(self):
        (self.original/'capture.json').write_text(json.dumps({'coordinate_system': 'arkit'}))
        metadata = self.original/'metadata.json'
        metadata.write_text(json.dumps(self.camera()))
        self.evidence.update(metadata=str(metadata),
                             predicted_corners_world_m=[[-1, 1, -2], [1, 1, -2], [1, -1, -2], [-1, -1, -2]])
        self.export()
        index = json.loads((self.session/'review/evidence_index.json').read_text())
        self.assertEqual(len(index[0]['projected_box_segments_native_pixels']['ifc']), 4)
        html = (self.session/'review/review.html').read_text(encoding='utf-8')
        self.assertIn('candidate bounding rectangles', html)

    def test_measurements_and_review_reasons_are_readable_in_centimetres(self):
        self.report['parameters'] = {'tolerance_m': .1}
        self.report['summary'] = {'observed_instances': 4, 'matched_pairs': 1}
        self.report['findings'][0].update(
            measurements={'observed_width_m': .8, 'ifc_width_m': 1.1,
                          'observed_height_m': 2., 'ifc_height_m': 2.1},
            deltas_m={'left': .29, 'width': -.3, 'height': -.1},
            review_reasons=['not_detected_does_not_mean_absent', 'no_linked_photo_evidence'])
        self.export()
        html = (self.session/'review/review.html').read_text(encoding='utf-8')
        self.assertIn('Left edge', html)
        self.assertIn('+29.0', html)
        self.assertIn('-30.0', html)
        self.assertIn('0.800', html)
        self.assertIn('1.100', html)
        self.assertIn('Matched pairs', html)
        self.assertNotIn('not_detected_does_not_mean_absent', html)
        self.assertIn('labeled region', html)
        self.assertIn('<details>', html)


if __name__ == '__main__':
    unittest.main()
