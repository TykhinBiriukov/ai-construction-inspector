import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
import httpx
from fastapi.testclient import TestClient
from ai_inspector.api import create_app
from ai_inspector.config import Settings
from ai_inspector.storage import canonical_json

class LocalizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        settings=Settings(Path(self.tmp.name),'operator','device',localization_base_url='https://localization.example',localization_service_token='localization-secret')
        app=create_app(settings,background_worker=False);self.client=TestClient(app);self.addCleanup(self.client.close)
        self.service=app.state.service;self.device={'Authorization':'Bearer device'};self.operator={'Authorization':'Bearer operator'}
        self.session=self.client.post('/v1/capture-sessions',headers=self.operator,json={'schema_version':'0.1','request_id':str(uuid4()),'device_id':'iphone-1'}).json()
        self.sid=self.session['session_id'];self.path='/v1/sessions/'+self.sid+'/localization'
        self.content=b'synthetic IFC transport fixture';self.digest=hashlib.sha256(self.content).hexdigest()
        with self.service.bim_store.stage(__import__('io').BytesIO(self.content)) as staged:self.service.bim_store.publish(staged)
        with self.service.database.transaction() as db:
            inspection=self.service._load(db,'inspections',self.session['inspection_id'])
            inspection['bim_model']={'sha256':self.digest}
            db.execute('UPDATE inspections SET data=? WHERE inspection_id=?',(canonical_json(inspection),inspection['inspection_id']))
            self.session['model_version']='upload-sha256:'+self.digest
            db.execute('UPDATE sessions SET data=? WHERE session_id=?',(canonical_json(self.session),self.sid))
        self.calls=[]
        def remote(request):
            self.calls.append(request);body=json.loads(request.content)
            self.assertEqual(request.headers['authorization'],'Bearer localization-secret')
            self.assertEqual(base64.b64decode(body['ifc_base64']),self.content)
            return httpx.Response(200,json={'session_id':self.sid,'model_version':self.session['model_version'],'session_token':'a'*64})
        self.service.localization_transport=httpx.MockTransport(remote)

    def test_transfer_and_device_scope(self):
        self.assertEqual(self.client.post(self.path).status_code,401)
        response=self.client.post(self.path,headers=self.device)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['endpoint'],'https://localization.example')
        with self.service.database.transaction() as db:
            self.session['device_id']='iphone-other'
            db.execute('UPDATE sessions SET data=? WHERE session_id=?',(canonical_json(self.session),self.sid))
        self.assertEqual(self.client.post(self.path,headers=self.device).status_code,403)
        self.assertEqual(len(self.calls),1)

    def test_upstream_mismatch_and_closed_session(self):
        self.service.localization_transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'session_id':str(uuid4()),'model_version':'wrong','session_token':'b'*64}))
        self.assertEqual(self.client.post(self.path,headers=self.device).status_code,503)
        self.client.post('/v1/sessions/'+self.sid+'/abort',headers=self.operator)
        self.assertEqual(self.client.post(self.path,headers=self.device).status_code,409)
