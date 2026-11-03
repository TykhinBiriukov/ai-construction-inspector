"""Persistence contract using SQLite as a test double, not a live Postgres test."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4
import numpy as np
from localization_service.cloud_server import configuration
from localization_service.cloud_store import PostgresStore
from localization_service.lab_server import Lab
from test_lab import fixture
from test_registration import ROOM

class TestQueries:
    def __init__(self,db):self.db=db
    def execute(self,query,args=()):return self.db.execute(query.replace('localization.',''),args)

class StoreDouble(PostgresStore):
    def __init__(self,path):
        self.path=path;self.fail_commit=False
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE models (sha256 TEXT PRIMARY KEY,content BLOB,room TEXT,meshes TEXT)')
            db.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY,operation TEXT UNIQUE,state TEXT NOT NULL)')
            db.execute('CREATE TABLE files (session_id TEXT,observation_id TEXT,name TEXT,sha256 TEXT,byte_length INTEGER,content BLOB,PRIMARY KEY(session_id,observation_id,name))')
    @contextmanager
    def connect(self):
        with sqlite3.connect(self.path) as db:
            db.create_function('octet_length',1,lambda value:len(value) if value is not None else None)
            yield TestQueries(db)
            if self.fail_commit:raise RuntimeError('simulated durable store failure')

class CloudStoreTests(unittest.TestCase):
    def test_configuration_fails_closed(self):
        for env in [{},{'LOCALIZATION_SERVICE_TOKEN':'1234'}, {'LOCALIZATION_SERVICE_TOKEN':'a'*32}]:
            with self.assertRaises(ValueError):configuration(env)
        cfg={'LOCALIZATION_SERVICE_TOKEN':'a'*32,'LOCALIZATION_DATABASE_URL':'postgresql://test','LOCALIZATION_MODEL_SHA256':'a'*64}
        self.assertEqual(str(configuration(cfg)[2]),'/data')
        cfg['LOCALIZATION_SERVICE_TOKEN']='a'*32+'\n'
        with self.assertRaises(ValueError):configuration(cfg)

    def test_recovery_in_empty_cache_and_transaction_failure(self):
        room=dict(ROOM,model_version='fixture',room_id='fixture',entry_element_id='door',bim_frame_id='fixture-y-up')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=StoreDouble(root/'durable.sqlite')
            lab=Lab(root/'pod1',room,'test',store=store)
            state=lab.request('POST',['test','sessions'],{'operation_id':str(uuid4())})
            sid=state['session_id'];segment=str(uuid4());route=['test','sessions',sid]
            lab.request('POST',route+['start'],{'segment_id':segment})
            views=[([1.8,1.4,-.8],[0,1,-.8]),([1.5,1.4,-.8],[0,.7,-.15]),([2,1.4,-1.2],[0,.65,-.2]),([2,1.35,-1.1],[.1,.1,-.3])]
            for camera,target in views:
                body,frame=fixture(sid,segment,camera,target,np.eye(4))
                state=lab.request('POST',route+['observations'],body)
            self.assertEqual(state['guidance']['phase'],'inspecting')
            restarted=Lab(root/'pod2',room,'test',store=store)
            restored=restarted.request('GET',route,{})
            self.assertEqual(restored['alignments'],state['alignments'])
            self.assertEqual(restored['coverage'],state['coverage'])
            self.assertEqual(restored['observation_count'],4)
            self.assertFalse(list((root/'pod2').rglob('rgb.jpg')))
            self.assertEqual(len(list((root/'pod2').rglob('depth.f32'))),4)
            self.assertEqual(restarted.request('POST',route+['observations'],body)['observation_count'],4)
            files={'frame.json':b'changed','rgb.jpg':b'changed','depth.f32':b'changed'}
            with store.connect() as db:
                with self.assertRaises(ValueError):store.save_files(db,sid,frame['observation_id'],files)
            next_body,_=fixture(sid,segment,*views[0],np.eye(4))
            store.fail_commit=True
            with self.assertRaises(RuntimeError):restarted.request('POST',route+['observations'],next_body)
            store.fail_commit=False
            self.assertEqual(restarted.request('GET',route,{})['observation_count'],4)
            self.assertEqual(restarted.request('POST',route+['observations'],next_body)['observation_count'],5)

    def test_sessions_pin_distinct_models_and_tokens(self):
        from localization_service.cloud_server import CloudRegistry
        import base64
        with tempfile.TemporaryDirectory() as tmp:
            store=StoreDouble(Path(tmp)/'db');registry=CloudRegistry(store,Path(tmp)/'cache','test-service-secret'*3)
            assignments=[]
            for length in (5.0,8.0):
                content=('synthetic-model-'+str(length)).encode();digest=hashlib.sha256(content).hexdigest()
                room=dict(ROOM,length=length,model_version=digest,room_id='fixture',entry_element_id='door',bim_frame_id='fixture')
                with store.connect() as db:db.execute('INSERT INTO localization.models VALUES (?,?,?,?)',(digest,content,json.dumps(room),'[]'))
                body=dict(session_id=str(uuid4()),project_id='test',room_id='fixture',model_version='upload-sha256:'+digest,ifc_base64=base64.b64encode(content).decode())
                access=registry.provision(body);assignments.append((body,access))
                config=registry.request('GET',['v1','localization','sessions',body['session_id'],'config'],{})
                self.assertEqual(config['room']['length'],length)
            self.assertNotEqual(assignments[0][1]['session_token'],assignments[1][1]['session_token'])
            wrong=dict(assignments[1][0],session_id=assignments[0][0]['session_id'])
            with self.assertRaises(ValueError):registry.provision(wrong)
            fresh=CloudRegistry(store,Path(tmp)/'new-cache',registry.token)
            result=fresh.request('GET',['v1','localization','sessions',assignments[0][0]['session_id'],'config'],{})
            self.assertEqual(result['room']['length'],5.0)
