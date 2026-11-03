"""Manifest download service tests without external requests."""
from dataclasses import replace
from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import Mock
from urllib.parse import urlsplit
from geometry_engine_api.services.archive import ArchiveService, ArchiveServiceError, Settings

class SessionDownloadTests(unittest.TestCase):

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = Settings(data_dir=self.root / 'data')
        self.prefix = 'projects/demo-building/sessions/scan-001'
        content = b'{"frame_id":"frame-001"}'
        self.asset = {'name': 'frame.json', 'object_key': self.prefix + '/observations/frame-001/frame.json', 'byte_length': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
        self.manifest = {'format': 'capture_manifest_supabase_v1', 'project_id': 'demo-building', 'session_id': 'scan-001', 'observation_count': 1, 'observations': [{'observation_id': 'frame-001', 'files': [self.asset]}]}
        self.objects = {self.prefix + '/.session.json': json.dumps({'project_id': 'demo-building', 'session_id': 'scan-001'}).encode(), self.asset['object_key']: content}
        self.bucket = Mock()
        self.bucket.list.return_value = [{'id': 'manifest-object', 'name': 'manifest-001.json'}]
        self.bucket.create_signed_url.side_effect = lambda key, expiry: {'signedURL': 'https://storage.example/' + key}
        sdk = Mock()
        sdk.storage.from_.return_value = self.bucket

        def download(url, destination, settings):
            content = self.objects[urlsplit(url).path.lstrip('/')]
            if len(content) > settings.max_download_bytes:
                raise ValueError('too large')
            destination.write_bytes(content)
        self.service = ArchiveService(self.settings, sdk, download_fn=download)

    def extract(self, payload):
        return self.service.extract(payload['session_id'], project_id=payload.get('project_id'), manifest_path=payload.get('manifest_path'))

    def request(self):
        self.objects[self.prefix + '/manifests/manifest-001.json'] = json.dumps(self.manifest).encode()
        return self.extract({'project_id': 'demo-building', 'session_id': 'scan-001'})

    def test_downloads_manifest_files_and_keeps_storage_layout(self):
        response = self.request()
        self.assertEqual(response.session_id, 'scan-001')
        original = self.settings.data_dir / 'raw' / 'scan-001/original'
        self.assertEqual((original / 'observations/frame-001/frame.json').read_bytes(), self.objects[self.asset['object_key']])
        self.assertTrue((original / 'manifests/manifest-001.json').is_file())
        self.assertTrue((original / '.session.json').is_file())
        with self.assertRaises(ArchiveServiceError) as failure:
            self.request()
        self.assertEqual(failure.exception.code, 'session_exists')

    def test_cross_session_paths_and_hash_mismatches_leave_no_partial_session(self):
        original_key = self.asset['object_key']
        for change in ({'object_key': 'projects/other/sessions/scan-001/private.txt'}, {'sha256': '0' * 64}, {'name': '../escape.json'}):
            with self.subTest(change=change):
                original = dict(self.asset)
                try:
                    self.asset.update(change)
                    with self.assertRaises(ArchiveServiceError) as failure:
                        self.request()
                    self.assertEqual(failure.exception.code, 'invalid_request')
                    self.assertFalse((self.settings.data_dir / 'raw' / 'scan-001').exists())
                    self.assertEqual(list((self.settings.data_dir / 'raw').iterdir()), [])
                finally:
                    self.asset.clear()
                    self.asset.update(original)
        self.assertIn(original_key, self.objects)

    def test_ambiguous_manifest_and_total_size_are_rejected(self):
        self.bucket.list.return_value *= 2
        with self.assertRaises(ArchiveServiceError) as failure:
            self.request()
        self.assertEqual(failure.exception.code, 'invalid_request')
        self.bucket.list.return_value = self.bucket.list.return_value[:1]
        self.service.settings = replace(self.settings, max_download_bytes=700)
        self.asset['byte_length'] = 1000
        with self.assertRaises(ArchiveServiceError) as failure:
            self.request()
        self.assertEqual(failure.exception.code, 'invalid_request')
        self.assertFalse((self.settings.data_dir / 'raw' / 'scan-001').exists())

    def test_invalid_project_id_is_rejected_before_storage_access(self):
        with self.assertRaises(ArchiveServiceError) as failure:
            self.extract({'project_id': '../escape', 'session_id': 'scan-001'})
        self.assertEqual(failure.exception.code, 'invalid_request')
        self.bucket.create_signed_url.assert_not_called()

    def make_parts(self, entries=None):
        entries = entries or [{'capture.json': b'first', 'mesh.ply': b'mesh-one'}, {'capture.json': b'second', 'mesh.ply': b'mesh-two'}]
        parts = []
        for index, files in enumerate(entries, 1):
            data = BytesIO()
            with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name, content in files.items():
                    archive.writestr(name, content)
            content = data.getvalue()
            name = f'capture-{index:04d}.zip'
            key = f'{self.prefix}/geometry/completion-001/{name}'
            self.objects[key] = content
            parts.append({'name': name, 'object_key': key, 'byte_length': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'frame_ids': [f'frame-{index:03d}'], 'segment_id': 'segment-001'})
        self.manifest = {'format': 'geometry_capture_parts_v1', 'schema_version': '1.0', 'project_id': 'demo-building', 'session_id': 'scan-001', 'completion_id': 'completion-001', 'frame_count': len(parts), 'parts': parts}
        return parts

    def test_downloads_and_extracts_parts_without_overwriting_shared_names(self):
        parts = self.make_parts()
        response = self.request()
        self.assertEqual(response.session_id, 'scan-001')
        session = self.settings.data_dir / 'raw' / 'scan-001'
        self.assertEqual((session / 'original/parts/capture-0001/mesh.ply').read_bytes(), b'mesh-one')
        self.assertEqual((session / 'original/parts/capture-0002/mesh.ply').read_bytes(), b'mesh-two')
        for part in parts:
            self.assertEqual((session / 'archives' / part['name']).read_bytes(), self.objects[part['object_key']])
        report = json.loads((session / 'download-report.json').read_text())
        self.assertEqual(report['format'], 'geometry_capture_parts_v1')
        self.assertEqual(report['part_count'], 2)
        self.assertEqual(report['frame_count'], 2)
        self.assertTrue(report['sha256_verified'])

    def test_parts_enforce_hashes_paths_and_unique_frames(self):
        for fault in ('checksum', 'path', 'frames', 'invalid_zip'):
            with self.subTest(fault=fault):
                parts = self.make_parts()
                if fault == 'checksum':
                    parts[0]['sha256'] = '0' * 64
                elif fault == 'path':
                    parts[0]['object_key'] = 'projects/other/geometry/capture-0001.zip'
                elif fault == 'frames':
                    parts[1]['frame_ids'] = parts[0]['frame_ids']
                else:
                    content = b'not-a-zip'
                    self.objects[parts[0]['object_key']] = content
                    parts[0].update(byte_length=len(content), sha256=hashlib.sha256(content).hexdigest())
                with self.assertRaises(ArchiveServiceError) as failure:
                    self.request()
                self.assertEqual(failure.exception.code, 'invalid_request')
                self.assertEqual(list((self.settings.data_dir / 'raw').iterdir()), [])

    def test_parts_enforce_total_extraction_limit_and_reject_zip_traversal(self):
        self.make_parts([{'safe.txt': b'a' * 8}, {'safe.txt': b'b' * 8}])
        self.service.settings = replace(self.settings, max_extract_bytes=12)
        with self.assertRaises(ArchiveServiceError) as failure:
            self.request()
        self.assertEqual(failure.exception.code, 'invalid_request')
        self.assertEqual(list((self.settings.data_dir / 'raw').iterdir()), [])
        self.service.settings = self.settings
        self.make_parts([{'../escape.txt': b'bad'}])
        with self.assertRaises(ArchiveServiceError) as failure:
            self.request()
        self.assertEqual(failure.exception.code, 'invalid_request')
        self.assertEqual(list((self.settings.data_dir / 'raw').iterdir()), [])

    def test_unknown_manifest_format_returns_specific_error(self):
        self.manifest['format'] = 'unknown_v1'
        with self.assertRaises(ArchiveServiceError) as failure:
            self.request()
        self.assertEqual(failure.exception.code, 'invalid_request')
        self.assertIn('Unsupported manifest format', str(failure.exception))

    def standalone_payload(self):
        prefix = 'standalone/demo-building/iphone-1/scan-001'
        content = self.objects[self.asset['object_key']]
        self.asset['object_key'] = f"{prefix}/observations/frame-001/{self.asset['sha256']}/frame.json"
        self.objects[self.asset['object_key']] = content
        self.manifest.update(format='standalone_capture_v1', project_id='standalone')
        key = prefix + '/manifests/' + 'a' * 64 + '/capture-manifest.json'
        self.objects[key] = json.dumps(self.manifest).encode()
        return {'session_id': 'scan-001', 'manifest_path': key}

    def test_standalone_downloads_hashed_objects_without_descriptor_or_listing(self):
        payload = self.standalone_payload()
        response = self.extract(payload)
        self.assertEqual(response.session_id, 'scan-001')
        session = self.settings.data_dir / 'raw/scan-001'
        self.assertEqual((session / 'original/observations/frame-001/frame.json').read_bytes(), self.objects[self.asset['object_key']])
        self.assertTrue((session / 'original/manifests' / ('a' * 64) / 'capture-manifest.json').is_file())
        self.assertFalse((session / 'original/.session.json').exists())
        self.bucket.list.assert_not_called()
        report = json.loads((session / 'download-report.json').read_text())
        self.assertEqual(report['file_count'], 2)
        self.assertEqual(report['format'], 'standalone_capture_v1')
        with self.assertRaises(ArchiveServiceError) as failure:
            self.extract(payload)
        self.assertEqual(failure.exception.code, 'session_exists')

    def test_short_layout_downloads_flat_objects_without_descriptor(self):
        payload = self.standalone_payload()
        content = self.objects[self.asset['object_key']]
        self.asset['object_key'] = 'scan-001/observations/frame-001/frame.json'
        self.objects[self.asset['object_key']] = content
        payload['manifest_path'] = 'scan-001/manifests/' + 'b' * 64 + '/capture-manifest.json'
        self.objects[payload['manifest_path']] = json.dumps(self.manifest).encode()
        response = self.extract(payload)
        self.assertEqual(response.session_id, 'scan-001')
        self.assertEqual((self.settings.data_dir / 'raw/scan-001/original/observations/frame-001/frame.json').read_bytes(), content)
        self.bucket.list.assert_not_called()
        report = json.loads((self.settings.data_dir / 'raw/scan-001/download-report.json').read_text())
        self.assertEqual(report['storage_prefix'], 'scan-001')
        self.assertTrue(report['sha256_verified'])

    def test_short_layout_also_accepts_hashed_file_directories(self):
        payload = self.standalone_payload()
        content = self.objects[self.asset['object_key']]
        self.asset['object_key'] = f"scan-001/observations/frame-001/{self.asset['sha256']}/frame.json"
        self.objects[self.asset['object_key']] = content
        payload['manifest_path'] = 'scan-001/manifests/' + 'b' * 64 + '/capture-manifest.json'
        self.objects[payload['manifest_path']] = json.dumps(self.manifest).encode()
        response = self.extract(payload)
        self.assertEqual(response.session_id, 'scan-001')

    def test_short_layout_rejects_cross_session_paths_and_corruption(self):
        payload = self.standalone_payload()
        content = self.objects[self.asset['object_key']]
        key = 'scan-001/observations/frame-001/frame.json'
        payload['manifest_path'] = 'scan-001/manifests/' + 'b' * 64 + '/capture-manifest.json'
        for fault in ('cross_session', 'content', 'traversal'):
            with self.subTest(fault=fault):
                self.asset['object_key'] = {'cross_session': 'other-session/observations/frame-001/frame.json', 'traversal': 'scan-001/../other-session/frame.json'}.get(fault, key)
                self.objects[key] = b'corrupt' if fault == 'content' else content
                self.objects[payload['manifest_path']] = json.dumps(self.manifest).encode()
                with self.assertRaises(ArchiveServiceError) as failure:
                    self.extract(payload)
                self.assertEqual(failure.exception.code, 'invalid_request')
                self.assertFalse((self.settings.data_dir / 'raw/scan-001').exists())

    def test_short_layout_rejects_wrong_session_before_storage_access(self):
        with self.assertRaises(ArchiveServiceError) as failure:
            self.extract({'session_id': 'scan-001', 'manifest_path': 'other-session/manifests/' + 'b' * 64 + '/capture-manifest.json'})
        self.assertEqual(failure.exception.code, 'invalid_request')
        self.bucket.create_signed_url.assert_not_called()

    def test_standalone_invalid_paths_rejected_before_storage_access(self):
        payload = self.standalone_payload()
        for path in (payload['manifest_path'].replace('scan-001', 'other-session'), '../' + payload['manifest_path'], 'https://example.com/' + payload['manifest_path']):
            with self.subTest(path=path):
                with self.assertRaises(ArchiveServiceError) as failure:
                    self.extract({**payload, 'manifest_path': path})
                self.assertEqual(failure.exception.code, 'invalid_request')
        self.bucket.create_signed_url.assert_not_called()

    def test_standalone_rejects_identity_cross_session_and_corrupt_content(self):
        payload = self.standalone_payload()
        original_manifest = json.loads(json.dumps(self.manifest))
        for fault in ('identity', 'path', 'content'):
            with self.subTest(fault=fault):
                manifest = json.loads(json.dumps(original_manifest))
                if fault == 'identity':
                    manifest['session_id'] = 'other-session'
                elif fault == 'path':
                    manifest['observations'][0]['files'][0]['object_key'] = 'other/private.json'
                else:
                    self.objects[self.asset['object_key']] = b'corrupt'
                self.objects[payload['manifest_path']] = json.dumps(manifest).encode()
                with self.assertRaises(ArchiveServiceError) as failure:
                    self.extract(payload)
                self.assertEqual(failure.exception.code, 'invalid_request')
                self.assertFalse((self.settings.data_dir / 'raw/scan-001').exists())
