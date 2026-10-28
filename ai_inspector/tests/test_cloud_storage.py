import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi.testclient import TestClient

from ai_inspector.config import Settings
from ai_inspector.supabase_bim import SupabaseBIMStore
from ai_inspector.errors import APIError


class RemoteBIMTests(unittest.TestCase):
    def test_publish_reopen_and_integrity(self):
        objects = {}
        def handler(request):
            key = request.url.path
            if request.method == 'POST':
                if key in objects:
                    return httpx.Response(409)
                objects[key] = request.read()
                return httpx.Response(200)
            return httpx.Response(200, content=objects[key])
        with tempfile.TemporaryDirectory() as root, httpx.Client(transport=httpx.MockTransport(handler)) as client:
            settings = Settings(Path(root), 'operator', 'device', supabase_url='https://example.supabase.co',
                                supabase_storage_bucket='captures', supabase_secret_key='test-secret', bim_storage='supabase')
            store = SupabaseBIMStore(settings, client)
            content = b'opaque BIM fixture'
            digest = hashlib.sha256(content).hexdigest()
            for _ in range(2):
                with store.stage(io.BytesIO(content)) as model:
                    store.publish(model)
            store.path_for(digest).unlink()
            reopened = SupabaseBIMStore(settings, client)
            self.assertEqual(reopened.path_for(digest).read_bytes(), content)
            reopened.path_for(digest).unlink()
            objects[next(iter(objects))] = b'corrupt'
            with self.assertRaises(APIError) as error:
                reopened.path_for(digest)
            self.assertEqual(error.exception.body['code'], 'model_integrity_error')


@unittest.skipUnless(os.environ.get('INSPECTOR_TEST_POSTGRES_URL'), 'Set a disposable PostgreSQL test URL')
class PostgreSQLTests(unittest.TestCase):
    def test_atomic_idempotency_snapshot_and_reopen(self):
        from ai_inspector.api import create_app
        from ai_inspector.postgres_storage import PostgresDatabase
        with tempfile.TemporaryDirectory() as root:
            settings = Settings(Path(root), 'operator', 'device', project_id='test-'+uuid4().hex,
                                database_url=os.environ['INSPECTOR_TEST_POSTGRES_URL'])
            request = {'schema_version':'0.1', 'request_id':str(uuid4()), 'device_id':'iphone-1',
                       'room_id':'test-room', 'prompt':'Capture'}
            def create(_):
                with TestClient(create_app(settings)) as client:
                    response = client.post('/v1/capture-sessions', json=request, headers={'Authorization':'Bearer operator'})
                    self.assertIn(response.status_code, (200,201), response.text)
                    return response.json()['session_id']
            with ThreadPoolExecutor(max_workers=3) as pool:
                ids = list(pool.map(create, range(3)))
            self.assertEqual(len(set(ids)),1)
            with TestClient(create_app(settings)) as client:
                overview = client.get('/v1/operator/overview', headers={'Authorization':'Bearer operator'})
                self.assertEqual(overview.status_code,200,overview.text)
                self.assertEqual(len(overview.json()['sessions']),1)
                service = client.app.state.service
                from ai_inspector.service import Principal
                snapshot = service.device_events.snapshot(Principal('device',settings.project_id,'iphone-1'),'iphone-1')
                self.assertEqual(snapshot['sessions'][0]['session_id'],ids[0])
                with self.assertRaises(RuntimeError):
                    with service.database.transaction() as db:
                        db.execute('DELETE FROM sessions WHERE session_id=?',(ids[0],))
                        raise RuntimeError('rollback probe')
                with service.database.connection() as db:
                    self.assertIsNotNone(db.execute('SELECT 1 FROM sessions WHERE session_id=?',(ids[0],)).fetchone())
