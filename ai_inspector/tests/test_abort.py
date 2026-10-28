import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from fastapi.testclient import TestClient
from ai_inspector.api import create_app
from ai_inspector.config import Settings
from ai_inspector.errors import APIError
from ai_inspector.storage import canonical_json

class AbortTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = create_app(Settings(Path(self.tmp.name), 'operator', 'device'), background_worker=False)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {'Authorization':'Bearer operator'}
        self.session = self.client.post('/v1/capture-sessions',headers=self.headers,json={
            'schema_version':'0.1','request_id':str(uuid4()),'device_id':'iphone-1'}).json()
        self.id = self.session['session_id']
        self.path = '/v1/sessions/'+self.id
        self.service = self.app.state.service

    def test_abort_auth_idempotency_and_device_event(self):
        self.assertEqual(self.client.post(self.path+'/abort').status_code,401)
        self.assertEqual(self.client.post(self.path+'/abort',headers={'Authorization':'Bearer device'}).status_code,403)
        first = self.client.post(self.path+'/abort',headers=self.headers)
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(first.json()['capture_state'],'cancelled')
        self.assertEqual(self.client.post(self.path+'/abort',headers=self.headers).json(),first.json())
        with self.service.database.connection() as db:
            events = db.execute('SELECT data FROM device_events WHERE session_id=? ORDER BY sequence',(self.id,)).fetchall()
        self.assertEqual(json.loads(events[-1]['data'])['session']['processing_state'],'cancelled')
        self.assertEqual(first.json()['revision'],self.session['revision']+1)
        self.assertEqual(self.client.post(self.path+'/capture-start',headers={'Authorization':'Bearer device'},json={
            'schema_version':'0.1','operation_id':str(uuid4()),'device':{'name':'test','os_version':'test','app_version':'test'}}).status_code,409)

    def test_abort_revokes_worker_and_preserves_evidence(self):
        session = self.session | {'capture_state':'sealed','processing_state':'queued_geometry',
                                  'source':{'status':'accepted','evidence':'keep'},'geometry_result':{'artifact':'keep'}}
        with self.service.database.transaction() as db:
            db.execute('UPDATE sessions SET data=? WHERE session_id=?',(canonical_json(session),self.id))
            db.execute("INSERT INTO work_items(work_id,session_id,kind,operation_id,status,lease_token) VALUES (?,?, 'validate',?,'processing',?)",('test-work',self.id,'test-operation','old-lease'))
        result=self.client.post(self.path+'/abort',headers=self.headers).json()
        self.assertEqual(result['source'],session['source'])
        self.assertEqual(result['geometry_result'],session['geometry_result'])
        stale={'work_id':'test-work','lease_token':'old-lease','kind':'validate','session_id':self.id,'operation_id':'test-operation'}
        self.service.capture._record_error(stale,APIError(503,'retry','late error',retryable=True))
        self.assertEqual(self.client.get(self.path,headers=self.headers).json()['processing_state'],'cancelled')
        with self.service.database.connection() as db:
            self.assertFalse(self.service.capture._owns_lease(db,stale))
        self.assertIsNone(self.service.capture._claim())
        self.assertEqual(self.client.post(self.path+'/geometry-jobs/dispatch',headers=self.headers).status_code,409)

    def test_pending_completion_is_rejected_without_erasing_inventory(self):
        completion={'completion_id':str(uuid4()),'status':'pending','error':None}
        session=self.session | {'capture_state':'submitted','processing_state':'validating','completion':completion}
        with self.service.database.transaction() as db:
            db.execute('UPDATE sessions SET data=? WHERE session_id=?',(canonical_json(session),self.id))
            db.execute('INSERT INTO capture_completions VALUES (?,?,?,?,?)',
                       (self.id,completion['completion_id'],'{}',canonical_json(completion),'preserved-evidence'))
        result=self.client.post(self.path+'/abort',headers=self.headers).json()
        self.assertEqual(result['completion']['status'],'rejected')
        with self.service.database.connection() as db:
            row=db.execute('SELECT data,inventory FROM capture_completions WHERE session_id=?',(self.id,)).fetchone()
        self.assertEqual(json.loads(row['data']),result['completion'])
        self.assertEqual(row['inventory'],'preserved-evidence')

    def test_completed_session_is_unchanged(self):
        session=self.session | {'capture_state':'sealed','processing_state':'completed'}
        with self.service.database.transaction() as db:
            db.execute('UPDATE sessions SET data=? WHERE session_id=?',(canonical_json(session),self.id))
        self.assertEqual(self.client.post(self.path+'/abort',headers=self.headers).status_code,409)
        self.assertEqual(self.client.get(self.path,headers=self.headers).json()['processing_state'],'completed')
