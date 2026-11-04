"""Public browser cookies, conversation ownership and own-image submission."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

from ai_inspector.api import create_app
from ai_inspector.config import Settings
from ai_inspector import browser_session, orchestrator_policy, public_access


class PublicDemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(Path(self.tmp.name), 'private-operator', 'private-device')
        self.app = create_app(self.settings, background_worker=False)
        self.origin = self.settings.browser_origin
        self.headers = {'Origin': self.origin}
        self.c = self.client()
        self.cookie = browser_session.cookie_settings(self.settings, public=True)[0]

    def client(self, app=None):
        client = TestClient(app or self.app, base_url=self.origin)
        self.addCleanup(client.close)
        return client

    def bootstrap(self, client=None):
        client = client or self.c
        response = client.post('/v1/demo/session', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'authenticated': True, 'mode': 'public'})
        return client.cookies.get(self.cookie)

    def chat(self, client=None):
        response = (client or self.c).post('/v1/chats', headers=self.headers, json={
            'schema_version': '0.1', 'request_id': str(uuid4()), 'room_id': 'demo'})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['chat_id']

    def test_cookie_integrity_and_private_access(self):
        token = self.bootstrap()
        self.assertIsNotNone(public_access.visitor(self.settings, token))
        self.assertIsNone(public_access.visitor(self.settings, token + 'x'))
        self.assertEqual(self.bootstrap(), token)
        response = self.c.post('/v1/demo/session', headers=self.headers)
        for attribute in ('HttpOnly', 'Secure', 'SameSite=strict', 'Path=/'):
            self.assertIn(attribute, response.headers['set-cookie'])
        self.assertNotIn('Domain=', response.headers['set-cookie'])
        self.assertEqual(self.c.get('/v1/operator/backlog').status_code, 403)
        self.assertEqual(self.c.get('/v1/operator/overview', headers={
            'Authorization': 'Bearer ' + token}).status_code, 401)
        self.c.cookies.clear()
        self.c.cookies.set(self.cookie, token + 'x')
        self.assertEqual(self.c.get('/v1/chats').status_code, 401)

    def test_visitor_isolation(self):
        self.bootstrap(); chat = self.chat()
        other = self.client(); self.bootstrap(other)
        self.assertEqual(other.get('/v1/chats/' + chat).status_code, 403)
        self.assertEqual(other.get('/v1/chats').json()['chats'], [])
        self.assertEqual(self.c.get('/v1/chats/' + chat).status_code, 200)

    def test_bootstrap_and_mutations_require_exact_origin(self):
        self.assertEqual(self.c.post('/v1/demo/session').status_code, 403)
        self.assertEqual(self.c.post('/v1/demo/session', headers={'Origin': 'https://other.example'}).status_code, 403)
        self.bootstrap()
        body = {'schema_version': '0.1', 'request_id': str(uuid4()), 'room_id': 'demo'}
        for headers in ({}, {'Origin': 'https://other.example'}):
            self.assertEqual(self.c.post('/v1/chats', headers=headers, json=body).status_code, 403)
        self.assertEqual(self.c.post('/v1/chats', headers=self.headers, json=body).status_code, 201)

    def test_own_image_reaches_model_and_completed_request_reopens(self):
        token = self.bootstrap(); chat = self.chat(); file_id = str(uuid4())
        raw = io.BytesIO(); Image.new('RGB', (32, 24), 'red').save(raw, format='PNG')
        response = self.c.post(f'/v1/chats/{chat}/files', headers=self.headers,
            data={'file_id': file_id}, files={'file': ('my-site.png', raw.getvalue(), 'image/png')})
        self.assertEqual(response.status_code, 200, response.text)
        calls = []
        class Model:
            def complete(self, messages, tools):
                calls.append(messages)
                prompt = messages[0]['content']
                if prompt.startswith(orchestrator_policy.SCOPE_MARKER):
                    request = json.loads(messages[-1]['content'])['new_request']
                    return {'role': 'assistant', 'content': json.dumps({
                        'status': 'in_scope', 'intent': 'inspection',
                        'product_request': request, 'language': 'en'})}
                if prompt.startswith(orchestrator_policy.REVIEW_MARKER):
                    return {'role': 'assistant', 'content': '{"verdict":"approved"}'}
                return {'role': 'assistant', 'content': 'Offline fixture: image received.'}
        self.app.state.service.agent.model = Model()
        body = {'schema_version': '0.1', 'request_id': str(uuid4()),
                'text': 'Describe my site photo.', 'file_ids': [file_id]}
        response = self.c.post(f'/v1/chats/{chat}/messages', headers=self.headers, json=body)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['status'], 'completed', response.text)
        self.assertEqual(len(calls), 3)  # Scope, image assessment, reply review.
        image_messages = [m['content'] for request in calls for m in request
                          if m['role'] == 'user' and isinstance(m['content'], list)]
        self.assertEqual(len(image_messages), 1)
        parts = image_messages[0]
        self.assertEqual(sum(p['type'] == 'image_url' for p in parts), 1)
        self.assertEqual(self.c.post(f'/v1/chats/{chat}/messages', headers=self.headers, json=body).status_code, 200)
        self.assertEqual(len(calls), 3)
        reopened = self.client(create_app(self.settings, background_worker=False))
        reopened.cookies.set(self.cookie, token)
        response = reopened.get('/v1/chats/' + chat)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['runs'][-1]['reply'], 'Offline fixture: image received.')
