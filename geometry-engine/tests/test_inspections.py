"""Request validation and failure contracts without a database or model calls."""
import os
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from geometry_engine_api.app import create_app
from geometry_engine_api.database.connection import DatabaseConfigurationError, database_url
from geometry_engine_api.routers.inspections import get_inspection_service
from geometry_engine_api.schemas.inspections import InspectionRequest


def request_body():
    return {'session_id': 'scan-001',
            'capture_manifest_path': 'standalone/demo/phone/scan-001/manifests/hash/capture-manifest.json',
            'ifc_object_key': 'references/room.ifc'}


class InspectionContractTests(unittest.TestCase):
    def test_fitted_contours_are_compared_by_default(self):
        request = InspectionRequest.model_validate(request_body())
        self.assertTrue(request.parameters.include_boundary_candidates)

    def setUp(self):
        self.app = create_app()
        self.service = Mock()
        self.app.dependency_overrides[get_inspection_service] = lambda: self.service
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_key_is_required_and_bounded_before_submission(self):
        for key in (None, '', 'contains space', 'x' * 129):
            headers = {} if key is None else {'Idempotency-Key': key}
            with self.subTest(key=key):
                response = self.client.post('/inspections', json=request_body(), headers=headers)
                self.assertEqual(response.status_code, 422)
        self.service.submit.assert_not_called()

    def test_invalid_paths_and_unknown_options_are_rejected(self):
        for field, value in [('session_id', '../escape'), ('ifc_object_key', '../room.ifc'),
                             ('ifc_object_key', 'https://storage/room.ifc'),
                             ('capture_manifest_path', '/manifest.json'),
                             ('capture_manifest_path', 'folder//manifest.json'),
                             ('capture_manifest_path', 'folder/./manifest.json'),
                             ('capture_manifest_path', 'manifest.json?token=secret'),
                             ('ifc_object_key', 'room.zip'), ('ifc_object_key', 'room.ifc\x00')]:
            body = request_body(); body[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                InspectionRequest.model_validate(body)
        for parameters in ({'unknown': True}, {'tolerance_m': 0}, {'tolerance_m': float('inf')},
                           {'interior_point_ifc_m': [0, 1]}, {'interior_point_ifc_m': [0, float('nan'), 1]}):
            with self.subTest(parameters=parameters), self.assertRaises(ValidationError):
                InspectionRequest.model_validate(dict(request_body(), parameters=parameters))

    def test_unknown_and_invalid_job_ids(self):
        self.service.get.return_value = None
        self.assertEqual(self.client.get('/inspections/'+str(uuid4())).status_code, 404)
        self.service.get.reset_mock()
        self.assertEqual(self.client.get('/inspections/not-a-uuid').status_code, 422)
        self.service.get.assert_not_called()

    def test_database_failures_are_retryable_without_leaking_secrets(self):
        self.service.submit.side_effect = OperationalError('private SQL', {'password': 'private-password'},
                                                          Exception('private-password'))
        response = self.client.post('/inspections', json=request_body(), headers={'Idempotency-Key': 'retry-me'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers['Retry-After'], '5')
        self.assertNotIn('private', response.text)

    def test_connection_settings_support_special_characters_and_override(self):
        with patch('geometry_engine_api.database.connection.load_dotenv'), patch.dict(os.environ, {
            'POSTGRES_USER': 'local', 'POSTGRES_PASSWORD': 'a@b:/%word', 'POSTGRES_PORT': '5433',
        }, clear=True):
            url = database_url()
            self.assertEqual(url.password, 'a@b:/%word')
            self.assertEqual(url.port, 5433)
            os.environ['DATABASE_URL'] = 'postgresql://remote:pass@db:5432/inspections'
            self.assertEqual(database_url().host, 'db')
            self.assertEqual(database_url().drivername, 'postgresql+psycopg')
            os.environ['DATABASE_URL'] = 'sqlite:///unsupported.db'
            with self.assertRaises(DatabaseConfigurationError): database_url()
            os.environ['DATABASE_URL'] = 'invalid-url-containing-private-password'
            with self.assertRaises(DatabaseConfigurationError) as failure: database_url()
            self.assertNotIn('private-password', str(failure.exception))
