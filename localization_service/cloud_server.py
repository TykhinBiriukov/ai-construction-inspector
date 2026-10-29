"""Session-scoped cloud API. Inspector provides the IFC for each assignment."""
import base64
from collections import OrderedDict
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer
from uuid import UUID
from localization_service.lab_server import Handler, Lab
from localization_service.cloud_store import PostgresStore
from localization_service.room_model import load_room, load_display_meshes


class CloudRegistry:
    def __init__(self,store,data,token):
        self.store=store;self.data=Path(data);self.token=token;self.lock=threading.RLock();self.labs=OrderedDict()
        self.data.mkdir(parents=True,exist_ok=True)

    def session_token(self,sid):
        return hmac.new(self.token.encode(),('localization-session:'+str(UUID(sid))).encode(),hashlib.sha256).hexdigest()

    def model(self,digest,content=None):
        with self.store.connect() as db:
            row=db.execute('SELECT room,meshes FROM localization.models WHERE sha256=?',(digest,)).fetchone()
        if row:return json.loads(row[0]),json.loads(row[1])
        if content is None:raise LookupError('model')
        if not content or len(content)>8*1024*1024 or hashlib.sha256(content).hexdigest()!=digest:
            raise ValueError('IFC size or SHA256 mismatch')
        path=self.data/(digest+'.ifc');path.write_bytes(content)
        try:
            room=load_room(path);meshes=load_display_meshes(path,room)
        except Exception as error:
            raise ValueError('Unsupported IFC: requires one axis-aligned rectangular room, four walls, one floor and one door/opening on the min-X wall, in metres') from error
        finally:path.unlink(missing_ok=True)
        with self.store.connect() as db:
            used=db.execute('SELECT COALESCE(sum(octet_length(content)),0) FROM localization.models').fetchone()[0]
            if used+len(content)>50*1024*1024:raise ValueError('BIM cache capacity reached')
            db.execute('INSERT INTO localization.models (sha256,content,room,meshes) VALUES (?,?,?,?) ON CONFLICT (sha256) DO NOTHING',
                       (digest,content,json.dumps(room),json.dumps(meshes)))
        return room,meshes

    def lab(self,digest,content=None):
        if digest in self.labs:
            self.labs.move_to_end(digest);return self.labs[digest]
        room,meshes=self.model(digest,content)
        lab=Lab(self.data/'evidence',room,'unused',meshes,store=self.store)
        self.labs[digest]=lab
        while len(self.labs)>2:self.labs.popitem(last=False)
        return lab

    def provision(self,body):
        with self.lock:
            binding={k:body[k] for k in ('session_id','project_id','room_id','model_version')}
            binding['session_id']=str(UUID(binding['session_id']))
            digest=binding['model_version'].removeprefix('upload-sha256:')
            if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('BIM SHA256 required')
            content=base64.b64decode(body['ifc_base64'],validate=True)
            if hashlib.sha256(content).hexdigest()!=digest:raise ValueError('IFC hash mismatch')
            lab=self.lab(digest,content)
            state=lab.request('POST',['v1','localization','sessions'],binding)
            return {'session_id':state['session_id'],'model_version':binding['model_version'],
                    'session_token':self.session_token(state['session_id'])}

    def request(self,method,parts,body):
        with self.lock:
            if len(parts)<4 or parts[:3]!=['v1','localization','sessions']:raise LookupError('route')
            sid=str(UUID(parts[3]))
            with self.store.connect() as db:
                row=db.execute('SELECT state FROM sessions WHERE id=?',(sid,)).fetchone()
            if not row:raise LookupError('session')
            state=json.loads(row[0]);lab=self.lab(state['context']['model_version'])
            if len(parts)==5 and parts[4]=='config' and method=='GET':
                return {'room':lab.room,'meshes':lab.meshes}
            return lab.request(method,parts,body)


class CloudHandler(Handler):
    def handle_request(self):
        self.connection.settimeout(30)
        if self.path=='/healthz' and self.command=='GET':
            self.reply(200,{'status':'ready','model_mode':'per_session_ifc'});return
        parts=self.path.strip('/').split('/')
        internal=parts==['internal','localization','sessions'] and self.command=='POST'
        registry=self.server.registry
        try:
            if internal:
                expected=registry.token;provided=self.headers.get('Authorization','').removeprefix('Bearer ')
            elif len(parts)>=4 and parts[:3]==['v1','localization','sessions']:
                expected=registry.session_token(parts[3]);provided=self.headers.get('X-Localization-Code','')
            else:raise LookupError('route')
            if not hmac.compare_digest(provided,expected):
                self.reply(401,{'message':'Unauthorized'});return
            length=int(self.headers.get('Content-Length','0'))
            if not 0<=length<=16*1024*1024:raise ValueError('Request exceeds16MiB')
            body=json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(body,dict):raise ValueError('JSON object required')
            result=registry.provision(body) if internal else registry.request(self.command,parts,body)
            self.reply(200,result)
        except (ValueError,KeyError,TypeError) as error:self.reply(422,{'message':str(error)[:250]})
        except LookupError:self.reply(404,{'message':'Not found'})
        except Exception:self.reply(503,{'message':'Localization temporarily unavailable; retry'})


def configuration(env):
    token=env.get('LOCALIZATION_SERVICE_TOKEN','')
    if len(token)<32 or not token.isascii() or any(c.isspace() for c in token):raise ValueError('Dedicated random service token required')
    dsn=env.get('LOCALIZATION_DATABASE_URL','')
    if not dsn:raise ValueError('Dedicated database role required')
    return token,dsn,Path(env.get('LOCALIZATION_DATA_DIR','/data'))


def main():
    token,dsn,data=configuration(os.environ)
    store=PostgresStore(dsn)
    with store.connect() as db:db.execute('SELECT 1 FROM sessions LIMIT 1')
    server=ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8788'))),CloudHandler)
    server.registry=CloudRegistry(store,data,token)
    print('Localization ready: session-specific IFC from Inspector',flush=True)
    server.serve_forever()

if __name__=='__main__':main()
