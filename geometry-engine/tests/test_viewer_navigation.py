"""Swagger must receive a link rather than a large generated HTML response."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from geometry_engine_api.app import create_app
from geometry_engine_api.database.connection import get_database_session


class ViewerNavigationTests(unittest.TestCase):
    def setUp(self):
        self.job = UUID('e25b6cb6-5b41-41ae-ace0-0bb225acdf78')
        self.export = UUID('46ca3981-d2b2-4ee0-9bb3-1eee3feb0854')
        self.app = create_app()
        self.app.dependency_overrides[get_database_session] = lambda: object()
        self.url = f'/inspections/{self.job}/viewer?export_id={self.export}'
        self.asset = f'/inspections/{self.job}/viewer/assets/{self.export}/viewer.html'

    def test_swagger_gets_small_json_link_without_loading_html(self):
        with patch('geometry_engine_api.routers.viewers._export', return_value=SimpleNamespace(id=self.export)), \
             TestClient(self.app) as client:
            result = client.get(self.url, headers={'Accept': 'application/json'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {'viewer_url': self.asset})
        self.assertLess(len(result.content), 300)

    def test_browser_navigation_redirects_to_page(self):
        with patch('geometry_engine_api.routers.viewers._export', return_value=SimpleNamespace(id=self.export)), \
             TestClient(self.app) as client:
            result = client.get(self.url, headers={'Accept': 'text/html,application/xhtml+xml'}, follow_redirects=False)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers['Location'], self.asset)

    def test_default_clients_retain_redirect_behavior(self):
        with patch('geometry_engine_api.routers.viewers._export', return_value=SimpleNamespace(id=self.export)), \
             TestClient(self.app) as client:
            result = client.get(self.url, follow_redirects=False)
        self.assertEqual(result.status_code, 303)

    def test_swagger_documents_link_object_instead_of_string(self):
        schema = self.app.openapi()
        response = schema['paths']['/inspections/{job_id}/viewer']['get']['responses']['200']
        model = response['content']['application/json']['schema']['$ref'].split('/')[-1]
        self.assertIn('viewer_url', schema['components']['schemas'][model]['properties'])
