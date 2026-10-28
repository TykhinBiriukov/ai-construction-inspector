import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from ai_inspector.api import create_app
from ai_inspector.config import Settings


class OperatorUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(Path(self.temp.name), 'operator-test', 'device-test')
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer operator-test'}

    def test_static_ui_and_role_boundary(self):
        for path in ('/', '/ui/style.css', '/ui/app.js'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('operator-test', response.text)
            self.assertIn('no-store', response.headers['cache-control'])
            self.assertIn("frame-ancestors 'none'", response.headers['content-security-policy'])
        self.assertEqual(self.client.get('/v1/operator/overview').status_code, 401)
        self.assertEqual(self.client.get('/v1/operator/overview', headers={'Authorization': 'Bearer device-test'}).status_code, 403)

    def test_pagination_is_scoped_includes_pending_and_survives_reopen(self):
        service = self.client.app.state.service
        for number in range(3):
            response = self.client.post('/v1/capture-sessions', headers=self.headers, json={
                'schema_version': '0.1', 'request_id': str(uuid4()), 'device_id': 'iphone-1',
                'room_id': f'room-{number}', 'prompt': 'Record room',
            })
            self.assertEqual(response.status_code, 201, response.text)
        with service.database.transaction() as db:
            db.execute('UPDATE sessions SET storage_ready=0 WHERE assignment_order=3')
            record = json.loads(db.execute('SELECT data FROM sessions LIMIT 1').fetchone()['data'])
            record['project_id'] = 'another-project'
            db.execute('INSERT INTO sessions(session_id,inspection_id,project_id,device_id,data) VALUES (?,?,?,?,?)',
                       (str(uuid4()), record['inspection_id'], 'another-project', 'iphone-1', json.dumps(record)))
        page = self.client.get('/v1/operator/overview?limit=2', headers=self.headers).json()
        self.assertEqual([s['room_id'] for s in page['sessions']], ['room-2', 'room-1'])
        self.assertEqual(page['project_id'], 'demo-building')
        self.assertEqual(page['device_id'], 'iphone-1')
        self.assertEqual(len(page['inspections']), 3)
        self.assertNotIn('operator_token', page)
        reopened = TestClient(create_app(self.settings))
        self.addCleanup(reopened.close)
        older = reopened.get(f"/v1/operator/overview?limit=2&before={page['next_before']}", headers=self.headers).json()
        self.assertEqual([s['room_id'] for s in older['sessions']], ['room-0'])
        self.assertIsNone(older['next_before'])
        self.assertEqual(self.client.get('/v1/operator/overview?limit=101', headers=self.headers).status_code, 422)
