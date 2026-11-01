"""Archive download/extraction safety independent of HTTP application routes."""
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock
import zipfile

import httpx

from geometry_engine_api.services.archive import ArchiveService, ArchiveServiceError, Settings, download_archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.settings = Settings(data_dir=Path(temporary.name) / 'data')
        self.bucket = Mock()
        self.bucket.create_signed_url.return_value = {'signedURL': 'https://storage.example/archive.zip?token=hidden'}
        self.sdk = Mock()
        self.sdk.storage.from_.return_value = self.bucket

    def service(self, entries=None, *, settings=None, status=200, content=None):
        if content is None:
            buffer = BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                for name, data in (entries or {'notes.txt': b'original'}).items():
                    archive.writestr(name, data)
            content = buffer.getvalue()
        transport = httpx.MockTransport(lambda request: httpx.Response(status, content=content))
        def download(url, destination, config):
            return download_archive(url, destination, config, transport=transport)
        return ArchiveService(settings or self.settings, self.sdk, download_fn=download)

    def test_extracts_plain_files_and_preserves_archive_without_geometry_processing(self):
        result = self.service().extract('scan-001')
        self.assertEqual(result.session_id, 'scan-001')
        self.assertEqual((result.directory / 'original/notes.txt').read_bytes(), b'original')
        self.assertTrue((result.directory / 'archives/scan-001.zip').is_file())
        self.assertFalse((result.directory / 'validation').exists())
        self.bucket.create_signed_url.assert_called_once_with('scan-001.zip', 660)

    def test_duplicate_does_not_overwrite_existing_data(self):
        service = self.service()
        result = service.extract('scan-001')
        with self.assertRaises(ArchiveServiceError) as failure:
            service.extract('scan-001')
        self.assertEqual(failure.exception.code, 'session_exists')
        self.assertEqual((result.directory / 'original/notes.txt').read_bytes(), b'original')

    def test_invalid_identity_is_rejected_before_storage_access(self):
        with self.assertRaises(ArchiveServiceError) as failure:
            self.service().extract('../outside')
        self.assertEqual(failure.exception.code, 'invalid_request')
        self.bucket.create_signed_url.assert_not_called()

    def test_unsafe_archive_paths_leave_no_partial_session(self):
        for name in ('../escape.txt', '/absolute.txt', 'C:/outside.txt', 'CON.txt', 'file:stream'):
            with self.subTest(name=name), self.assertRaises(ArchiveServiceError) as failure:
                self.service({name: b'bad'}).extract('scan-001')
            self.assertEqual(failure.exception.code, 'invalid_archive')
            self.assertEqual(list((self.settings.data_dir / 'raw').iterdir()), [])

    def test_download_and_extraction_limits_leave_no_partial_session(self):
        for settings, code in ((replace(self.settings, max_download_bytes=1), 'download_failed'),
                               (replace(self.settings, max_extract_bytes=1), 'invalid_archive')):
            with self.subTest(code=code), self.assertRaises(ArchiveServiceError) as failure:
                self.service(settings=settings).extract('scan-001')
            self.assertEqual(failure.exception.code, code)
            self.assertEqual(list((self.settings.data_dir / 'raw').iterdir()), [])

    def test_failed_download_hides_signed_url_and_cleans_working_files(self):
        with self.assertRaises(ArchiveServiceError) as failure:
            self.service(status=403).extract('scan-001')
        self.assertEqual(failure.exception.code, 'download_failed')
        self.assertNotIn('hidden', str(failure.exception))
        self.assertEqual(list((self.settings.data_dir / 'raw').iterdir()), [])

    def test_signing_failure_is_cleaned_up(self):
        self.bucket.create_signed_url.side_effect = httpx.ConnectError('private details')
        with self.assertRaises(ArchiveServiceError) as failure:
            self.service().extract('scan-001')
        self.assertEqual(failure.exception.code, 'download_failed')
        self.assertNotIn('private details', str(failure.exception))
        self.assertEqual(list((self.settings.data_dir / 'raw').iterdir()), [])

    def test_malformed_zip_is_rejected_without_partial_session(self):
        with self.assertRaises(ArchiveServiceError) as failure:
            self.service(content=b'not a ZIP').extract('scan-001')
        self.assertEqual(failure.exception.code, 'invalid_archive')
        self.assertEqual(list((self.settings.data_dir / 'raw').iterdir()), [])
