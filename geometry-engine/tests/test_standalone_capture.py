import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from geometry_engine.capture.raw_capture import load_raw_capture, load_rgbd
from geometry_engine.pipeline import prepare_input


class StandaloneCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.session = Path(temporary.name)
        self.folder = self.session / 'original/observations/frame-a'
        self.folder.mkdir(parents=True)
        Image.new('RGB', (8, 6), 'red').save(self.folder / 'rgb.jpg')
        np.full((3, 4), 2, dtype='<f4').tofile(self.folder / 'depth.f32')
        np.full((3, 4), 2, dtype='u1').tofile(self.folder / 'confidence.u8')
        self.frame = dict(schema_version='0.2', session_id='scan', frame_id='frame-a',
            observation_id='frame-a', segment_id='seg', timestamp_s=1., tracking_state='normal',
            rgb={'name': 'rgb.jpg'}, depth={'name': 'depth.f32'}, confidence={'name': 'confidence.u8'},
            rgb_size=[8, 6], depth_size=[4, 3], depth_unit='m',
            image_orientation='native_buffer_unrotated_no_exif_rotation',
            depth_to_rgb_mapping='Unrotated aligned buffers; pixel origin top-left',
            source_to_stored_image=np.eye(3).tolist(), K_rgb=[[8, 0, 4], [0, 8, 2], [0, 0, 1]],
            T_world_camera=np.eye(4).tolist())
        self.manifest = dict(format='standalone_capture_v1', session_id='scan', observation_count=1,
            observations=[dict(observation_id='frame-a', segment_id='seg')],
            segments=[dict(segment_id='seg', coordinate_convention='arkit_world_camera_v1',
                           matrix_layout='rows_column_vectors', units='m')])
        self.manifest_path = self.session / 'original/manifests/hash/capture-manifest.json'
        self.manifest_path.parent.mkdir(parents=True)
        self.save()

    def save(self):
        (self.folder / 'frame.json').write_text(json.dumps(self.frame))
        self.manifest_path.write_text(json.dumps(self.manifest))

    def test_load_and_prepare_input_preserve_raw_and_convert_contract(self):
        original = (self.folder / 'frame.json').read_bytes()
        frame, = load_raw_capture(self.session)
        np.testing.assert_allclose(frame.K_depth, [[4, 0, 2], [0, 4, 1], [0, 0, 1]])
        np.testing.assert_allclose(frame.T_world_from_camera_cv, np.diag([1, -1, -1, 1]))
        self.assertEqual(load_rgbd(frame)[2]['valid_pixels'], 12)
        reconstruction = self.session / 'reconstruction'
        reconstruction.mkdir()
        (reconstruction / 'report.json').write_text(json.dumps({'frames': [{'frame_id': 'frame-a', 'status': 'integrated'}]}))
        (reconstruction / 'mesh.ply').write_bytes(b'mesh')
        self.assertEqual(prepare_input(self.session)['frame_count'], 1)
        metadata = json.loads((self.session / 'input/frames/frame-a/metadata.json').read_text())
        self.assertEqual(metadata['schema_version'], '1.0')
        self.assertEqual(metadata['depth']['unit'], 'meter')
        self.assertEqual((self.folder / 'frame.json').read_bytes(), original)
        self.assertEqual((self.session / 'input/frames/frame-a/depth.f32').read_bytes(), (self.folder / 'depth.f32').read_bytes())

    def test_rejects_mixed_segments_and_frame_identity(self):
        self.manifest['segments'].append(dict(self.manifest['segments'][0], segment_id='other'))
        self.save()
        with self.assertRaisesRegex(ValueError, 'one coordinate segment'):
            load_raw_capture(self.session)
        self.manifest['segments'].pop()
        self.frame['session_id'] = 'other'
        self.save()
        with self.assertRaisesRegex(ValueError, 'differs from manifest'):
            load_raw_capture(self.session)

    def test_rejects_rotated_pixels_and_invalid_depth_size(self):
        self.frame['source_to_stored_image'][0][0] = -1
        self.save()
        with self.assertRaisesRegex(ValueError, 'alignment'):
            load_raw_capture(self.session)
        self.frame['source_to_stored_image'] = np.eye(3).tolist()
        self.save()
        (self.folder / 'depth.f32').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'byte count'):
            load_raw_capture(self.session)

    def test_explicit_segment_selection_preserves_raw_manifest(self):
        self.manifest['segments'].append(dict(self.manifest['segments'][0], segment_id='other'))
        self.manifest['observations'].append(dict(observation_id='other-frame', segment_id='other'))
        self.manifest['observation_count'] = 2
        self.save()
        before = self.manifest_path.read_bytes()
        selection = self.session / 'capture_selection.json'
        selection.write_text(json.dumps({'segment_id': 'seg'}))
        frames = load_raw_capture(self.session)
        self.assertEqual([frame.frame_id for frame in frames], ['frame-a'])
        self.assertEqual(self.manifest_path.read_bytes(), before)
        selection.write_text(json.dumps({'segment_id': 'missing'}))
        with self.assertRaisesRegex(ValueError, 'no observations'):
            load_raw_capture(self.session)
