import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from ai_inspector.api import create_app
from ai_inspector.config import Settings
from ai_inspector.public_demo import mint, verify

class PublicDemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.settings=Settings(Path(self.tmp.name),'private-operator','private-device',public_demo_enabled=True,saved_demo_dir='/unused')
        self.patch=patch('ai_inspector.saved_demo.configure');self.patch.start()
        self.c=TestClient(create_app(self.settings,background_worker=False))
    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def token(self):
        r=self.c.post('/v1/demo/session');self.assertEqual(r.status_code,200)
        return r.json()['token']
    def test_tampering_and_private_access(self):
        token=self.token();self.assertIsNotNone(verify(self.settings,token))
        self.assertIsNone(verify(self.settings,token+'x'))
        self.assertEqual(self.c.get('/v1/operator/overview',headers={'Authorization':'Bearer '+token}).status_code,401)
        self.assertEqual(self.c.get('/public-demo/v1/operator/overview',headers={'Authorization':'Bearer private-operator'}).status_code,401)
    def test_visitor_isolation(self):
        from uuid import uuid4
        a,b=self.token(),self.token();headers=lambda t:{'Authorization':'Bearer '+t}
        body={'schema_version':'0.1','request_id':str(uuid4()),'room_id':'demo'}
        r=self.c.post('/public-demo/v1/chats',json=body,headers=headers(a));self.assertEqual(r.status_code,201,r.text)
        chat=r.json()['chat_id']
        self.assertEqual(self.c.get('/public-demo/v1/chats/'+chat,headers=headers(b)).status_code,404)
        self.assertEqual(self.c.get('/public-demo/v1/chats',headers=headers(b)).json()['chats'],[])
        self.assertEqual(self.c.get('/public-demo/v1/operator/backlog',headers=headers(a)).status_code,403)
    def test_bootstrap_is_opt_in(self):
        s=Settings(Path(self.tmp.name)/'off','op-off','dev-off')
        self.assertEqual(TestClient(create_app(s,background_worker=False)).post('/v1/demo/session').status_code,404)
