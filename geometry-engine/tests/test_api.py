"""Public inspection routes and Storage configuration; no remote calls."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from geometry_engine_api.app import create_app
from geometry_engine_api.database.connection import get_database_session
from geometry_engine_api.dependencies import get_settings
from geometry_engine_api.services.archive import Settings


class ApiTests(unittest.TestCase):
    def test_only_inspection_endpoints_are_exposed(self):
        app = create_app()
        self.assertEqual(set(app.openapi()['paths']), {
            '/inspections', '/inspections/{job_id}',
            '/inspections/{job_id}/viewer', '/inspections/{job_id}/result',
        })
        schemas = app.openapi()['components']['schemas']
        self.assertFalse({'ExtractRequest', 'ProcessRequest', 'CompareBimRequest'} & schemas.keys())
        with TestClient(app) as client:
            for path in ('extract', 'process', 'compare-bim'):
                with self.subTest(path=path):
                    self.assertEqual(client.post('/sessions/' + path, json={}).status_code, 404)

    def test_storage_configuration_errors_are_service_unavailable(self):
        settings = Settings(data_dir=Path('data'), supabase_url='https://storage.example',
                            supabase_secret_key='test-secret-key-only')
        for change in ({'supabase_secret_key': ''}, {'supabase_url': ''},
                       {'supabase_url': 'file:///invalid'}, {'storage_bucket': ''}):
            with self.subTest(change=change):
                app = create_app()
                app.dependency_overrides[get_settings] = lambda: replace(settings, **change)
                app.dependency_overrides[get_database_session] = lambda: Mock()
                with TestClient(app) as client:
                    response = client.get(f'/inspections/{uuid4()}/viewer/assets/{uuid4()}/viewer.html')
                self.assertEqual(response.status_code, 503)
                self.assertIn('SUPABASE', response.json()['detail'])
                self.assertNotIn(settings.supabase_secret_key, response.text)

    def test_supabase_settings_are_read_from_environment(self):
        with patch.dict('os.environ', {'SUPABASE_URL': 'https://storage.example',
                'SUPABASE_SECRET_KEY': 'test-secret', 'SUPABASE_STORAGE_BUCKET': 'captures'}):
            settings = Settings.from_env()
        self.assertEqual(settings.supabase_url, 'https://storage.example')
        self.assertEqual(settings.storage_bucket, 'captures')
        self.assertEqual(settings.supabase_secret_key, 'test-secret')
        self.assertNotIn('test-secret', repr(settings))
