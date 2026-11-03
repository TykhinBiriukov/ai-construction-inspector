import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from geometry_engine_api.services.artifacts import ArtifactError, ArtifactStore
from geometry_engine_api.services.viewer_files import ViewerFiles


class MemoryObjects:
    def __init__(self):
        self.data, self.downloads, self.uploads = {}, [], []

    def upload(self, key, data, content_type='application/octet-stream'):
        if key in self.data and self.data[key] != data:
            raise AssertionError('Immutable object overwritten')
        self.data[key] = bytes(data)
        self.uploads.append(key)

    def download(self, key):
        self.downloads.append(key)
        return self.data[key]


class ArtifactTests(unittest.TestCase):
    def test_restore_counts_growth_when_replacing_existing_file(self):
        objects = MemoryObjects()
        artifacts = ArtifactStore(objects)
        objects.data['capture/photo'] = b'123456'
        ref = artifacts.put_json('manifests', {'schema_version': 1, 'files': [
            {'path': 'photo', 'size': 6, 'sha256': hashlib.sha256(b'123456').hexdigest(),
             'source': {'object_key': 'capture/photo'}}]})
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'other').write_bytes(b'12345')
            (root/'photo').write_bytes(b'x')
            with patch('geometry_engine_api.services.artifacts.MAX_RESTORE_BYTES', 10):
                with self.assertRaisesRegex(ArtifactError, 'workspace exceeds'):
                    artifacts.restore(ref, root)
            self.assertEqual((root/'photo').read_bytes(), b'x')
            self.assertNotIn('capture/photo', objects.downloads)

    def test_malformed_file_descriptors_fail_as_integrity_errors(self):
        objects = MemoryObjects()
        artifacts = ArtifactStore(objects)
        base = {'path': 'photo', 'size': 1, 'sha256': hashlib.sha256(b'x').hexdigest(),
                'source': {'object_key': 'capture/photo'}}
        for entry in (None, {}, {**base, 'sha256': 'bad'}, {**base, 'source': None},
                      {**base, 'source': {}}, {**base, 'size': True},
                      {**base, 'source': {'object_key': '../escape'}}):
            with self.subTest(entry=entry), TemporaryDirectory() as temporary:
                ref = artifacts.put_json('manifests', {'schema_version': 1, 'files': [entry]})
                with self.assertRaises(ArtifactError):
                    artifacts.restore(ref, temporary)

    def test_bundle_sizes_are_validated_before_downloading_chunks(self):
        objects = MemoryObjects()
        artifacts = ArtifactStore(objects)
        ref = artifacts.put_json('bundles', {'size': 5, 'sha256': 'a' * 64,
            'chunks': [{'key': 'chunk', 'size': 6, 'sha256': 'b' * 64}]})
        with TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ArtifactError, 'chunk sizes'):
                artifacts._download_bundle(ref, Path(temporary)/'bundle.zip')
        self.assertNotIn('chunk', objects.downloads)

    def test_missing_bundle_member_is_an_integrity_error(self):
        objects = MemoryObjects()
        artifacts = ArtifactStore(objects)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'photo').write_bytes(b'photo')
            _, manifest = artifacts.publish(root, ('photo',), 'capture/results/stage')
            manifest['files'][0]['source']['member'] = 'missing'
            ref = artifacts.put_json('manifests', manifest)
            with self.assertRaisesRegex(ArtifactError, 'member is missing'):
                artifacts.restore(ref, root/'restored')
            self.assertFalse((root/'restored/photo').exists())

    def test_chunked_bundle_roundtrip_references_and_local_alias_reuse(self):
        objects, source_bytes = MemoryObjects(), b'photo-content'
        objects.data['capture/photo.jpg'] = source_bytes
        artifacts = ArtifactStore(objects, chunk_bytes=64)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'source'
            (source/'original').mkdir(parents=True)
            (source/'original/photo.jpg').write_bytes(source_bytes)
            (source/'original/metadata.json').write_text('{"frame":1}')
            external = {'original/photo.jpg': {'sha256': hashlib.sha256(source_bytes).hexdigest(),
                        'size': len(source_bytes), 'source': {'object_key': 'capture/photo.jpg'}}}
            raw_ref, raw = artifacts.publish(source, ('original',), 'capture/results/job/download/attempt_001',
                                              external_files=external)
            (source/'input').mkdir()
            (source/'input/photo.jpg').write_bytes(source_bytes)
            second_ref, second = artifacts.publish(source, ('input',), 'capture/results/job/input/attempt_001',
                                                   known_files=artifacts.file_index(raw))
            self.assertEqual(second['files'][0]['source'], external['original/photo.jpg']['source'])
            target, local = root/'restored', {}
            artifacts.restore(raw_ref, target, local_files=local)
            before = len(objects.downloads)
            artifacts.restore(second_ref, target, local_files=local)
            self.assertEqual(objects.downloads[before:], [second_ref.key])
            self.assertEqual((target/'input/photo.jpg').read_bytes(), source_bytes)
            self.assertTrue(all(len(value) <= 64 for key, value in objects.data.items() if key.endswith('.bin')))

    def test_corrupt_bundle_is_rejected(self):
        objects = MemoryObjects()
        artifacts = ArtifactStore(objects)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'file.txt').write_text('geometry')
            ref, _ = artifacts.publish(root, ('file.txt',), 'capture/results/job/stage')
            chunk = next(key for key in objects.data if key.endswith('.bin'))
            objects.data[chunk] = b'bad'
            with self.assertRaises(ArtifactError):
                artifacts.restore(ref, root/'other')

    def test_manifest_corruption_is_rejected(self):
        objects = MemoryObjects()
        artifacts = ArtifactStore(objects)
        ref = artifacts.put_json('capture/manifests', {'schema_version': 1, 'files': []})
        objects.data[ref.key] += b' '
        with self.assertRaises(ArtifactError):
            artifacts.read_json(ref)

    def test_no_path_traversal_or_absolute_paths(self):
        objects, artifacts = MemoryObjects(), None
        artifacts = ArtifactStore(objects)
        for name in ('../outside', '/absolute', 'C:/outside', 'nested/../escape', 'file:stream'):
            with self.subTest(name=name), TemporaryDirectory() as temporary:
                ref = artifacts.put_json('manifests', {'schema_version': 1, 'files': [
                    {'path': name, 'size': 0, 'sha256': hashlib.sha256(b'').hexdigest(),
                     'source': {'object_key': 'empty'}}]})
                with self.assertRaises(ArtifactError):
                    artifacts.restore(ref, Path(temporary)/'work')

    def test_source_mutation_does_not_silently_change_checkpoint(self):
        objects = MemoryObjects()
        artifacts = ArtifactStore(objects)
        objects.data['photo'] = b'changed'
        ref = artifacts.put_json('manifests', {'schema_version': 1, 'files': [
            {'path': 'original/photo', 'size': 3, 'sha256': hashlib.sha256(b'old').hexdigest(),
             'source': {'object_key': 'photo'}}]})
        with TemporaryDirectory() as temporary, self.assertRaises(ArtifactError):
            artifacts.restore(ref, temporary)

    def test_viewer_cache_serves_only_manifest_assets_without_repeat_download(self):
        objects = MemoryObjects()
        artifacts = ArtifactStore(objects)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'source/viewer/review').mkdir(parents=True)
            (root/'source/viewer/viewer.html').write_text('<html>viewer</html>')
            (root/'source/viewer/review/photo.jpg').write_bytes(b'jpg')
            ref, _ = artifacts.publish(root/'source', ('viewer',), 'capture/results/job/viewer')
            export = SimpleNamespace(id='export-1', result_manifest_key=ref.key, result_manifest_sha256=ref.sha256)
            files = ViewerFiles(artifacts, root/'cache')
            path = files.file(export, 'viewer.html')
            reads = len(objects.downloads)
            self.assertEqual(files.file(export, 'review/photo.jpg').read_bytes(), b'jpg')
            self.assertEqual(len(objects.downloads), reads)
            with self.assertRaises(FileNotFoundError):
                files.file(export, 'secret.txt')
            with self.assertRaises(ArtifactError):
                files.file(export, '../secret.txt')
            path.write_text('changed')
            self.assertEqual(files.file(export, 'viewer.html').read_text(), '<html>viewer</html>')
