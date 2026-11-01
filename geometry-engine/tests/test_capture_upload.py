"""Repackage captures without changing frame bytes or the original manifest."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from geometry_engine.capture.prepare_upload import prepare_capture_upload
from geometry_engine.capture.storage_layout import capture_manifest_location


class CaptureUploadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = self.root/'raw/scan-001'
        self.original = self.session/'original'
        self.file = self.original/'observations/frame-001/frame.json'
        self.file.parent.mkdir(parents=True)
        self.content = b'{"session_id":"scan-001","frame_id":"frame-001"}'
        self.file.write_bytes(self.content)
        digest = hashlib.sha256(self.content).hexdigest()
        self.manifest = {'format':'standalone_capture_v1', 'project_id':'standalone', 'session_id':'scan-001',
            'observation_count':1,'observations':[{'observation_id':'frame-001','files':[{
                'name':'frame.json','byte_length':len(self.content),'sha256':digest,
                'object_key':f'standalone/demo/phone/scan-001/observations/frame-001/{digest}/frame.json'}]}]}
        self.path = self.original/'manifests'/('a'*64)/'capture-manifest.json'
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(self.manifest))
        self.output = self.root/'upload'

    def test_export_rewrites_keys_and_manifest_hash_preserving_raw(self):
        original = self.path.read_bytes()
        result = prepare_capture_upload(self.session, self.output)
        manifest_path = self.output/result['capture_manifest_path']
        changed = json.loads(manifest_path.read_text())
        asset = changed['observations'][0]['files'][0]
        self.assertEqual(asset['object_key'], 'scan-001/observations/frame-001/frame.json')
        self.assertEqual((self.output/asset['object_key']).read_bytes(), self.content)
        self.assertEqual(asset['sha256'], hashlib.sha256(self.content).hexdigest())
        self.assertEqual(manifest_path.parent.name, hashlib.sha256(manifest_path.read_bytes()).hexdigest())
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.file.read_bytes(), self.content)
        self.assertEqual(result['file_count'], 2)
        self.assertEqual(capture_manifest_location(result['capture_manifest_path'],'scan-001')[0], 'scan-001')
        with self.assertRaises(FileExistsError): prepare_capture_upload(self.session, self.output)

    def test_corrupt_source_does_not_publish_partial_upload(self):
        self.file.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'SHA-256'): prepare_capture_upload(self.session, self.output)
        self.assertFalse((self.output/'scan-001').exists())
        self.assertEqual(list(self.output.iterdir()), [])

    def test_other_session_and_unsafe_manifest_paths_are_rejected(self):
        self.manifest['session_id'] = 'other'
        self.path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, 'matching'): prepare_capture_upload(self.session, self.output)
        for key in ('other/manifests/'+'a'*64+'/capture-manifest.json',
                    '../scan-001/manifests/'+'a'*64+'/capture-manifest.json'):
            with self.assertRaises(ValueError):capture_manifest_location(key,'scan-001')
