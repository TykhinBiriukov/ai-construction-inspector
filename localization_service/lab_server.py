"""Opt-in LAN-only phone test. Separate from Inspector and its credentials/storage.

Raw RGB-D stays in the chosen local data directory. Limited to the configured IFC.
"""
import argparse
import base64
import hashlib
import hmac
import io
import ipaddress
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4
from PIL import Image
from localization_service.core import Localization, rigid
from localization_service.room_model import load_room, load_display_meshes
from localization_service.registration import RoomSolver
from localization_service.coverage import measured_coverage


def json_bytes(value):return json.dumps(value,allow_nan=False).encode()

def atomic(path,content):
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_bytes(content);temp.replace(path)


class Lab:
    def __init__(self, data, room, code, meshes=None, store=None):
        self.data=Path(data);self.data.mkdir(parents=True,exist_ok=True)
        self.store=store
        self.meshes=meshes or []
        self.coverage_cache={}
        self.room=room;self.code=code;self.solver=RoomSolver(room);self.lock=threading.Lock()
        self.database=self.data/'sessions.sqlite3'
        with self.connect() as db:
            if self.store is None:
                db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, operation TEXT UNIQUE, state TEXT NOT NULL)')

    def connect(self):
        return self.store.connect() if self.store else sqlite3.connect(self.database)

    def restore(self,db,session,state):
        session.__dict__.update(json.loads(state))
        if self.store:
            self.store.restore_files(db,session,self.data)

    def snapshot(self,session):
        state=session.snapshot(time.time())
        state['room']=self.room
        state['assignment_binding']=getattr(session,'assignment_binding',None)
        state['observation_count']=len(session.evidence)
        state['test_mode']='cloud_real_depth' if self.store else 'local_wifi_real_depth'
        state['observation_poses']=[]
        state['coverage']=None
        if session.alignment:
            import numpy as np
            t=np.array(session.alignment['T_bim_arkit'])
            cache_key=json.dumps([session.session_id,session.alignment,list(session.evidence.keys())],sort_keys=True)
            if cache_key not in self.coverage_cache:
                self.coverage_cache={cache_key:measured_coverage(self.room,session.alignment,session.evidence.values())}
            state['coverage']=self.coverage_cache[cache_key]
            for item in session.evidence.values():
                frame=json.loads((Path(item['_folder'])/'frame.json').read_text())
                pose=t@np.array(frame['T_world_camera'])
                state['observation_poses'].append({'observation_id':frame['observation_id'], 'T_bim_camera':pose.tolist()})
        return state

    def request(self, method, parts, body):
        with self.lock, self.connect() as db:
            if parts[:2]==['v1','localization']:
                parts=['test']+parts[2:]
                if parts==['test','sessions'] and method=='POST':
                    sid=str(UUID(body['session_id']))
                    binding={k:body[k] for k in ('session_id','project_id','room_id','model_version')}
                    binding['session_id']=sid
                    if binding['model_version'] not in (self.room['model_version'],'upload-sha256:'+self.room['model_version']):
                        raise ValueError('BIM SHA256 differs from the IFC loaded in Localization Service')
                    if any(not isinstance(v,str) or not v for v in binding.values()):raise ValueError('Invalid assignment binding')
                    row=db.execute('SELECT state FROM sessions WHERE id=?',(sid,)).fetchone()
                    session=Localization(sid,self.room,self.solver)
                    if row:
                        self.restore(db,session,row[0])
                        if getattr(session,'assignment_binding',None)!=binding:raise ValueError('Assignment binding changed')
                    else:
                        session.assignment_binding=binding
                        self.save(db,session,sid)
                    return self.snapshot(session)
            if parts==['test','config'] and method=='GET':return {'protocol':'local_wifi_v1','room':self.room,'meshes':self.meshes}
            if parts==['test','sessions'] and method=='POST':
                operation=str(UUID(body['operation_id']))
                row=db.execute('SELECT id,state FROM sessions WHERE operation=?',(operation,)).fetchone()
                if row:
                    session=Localization(row[0],self.room,self.solver);self.restore(db,session,row[1])
                else:
                    session=Localization(str(uuid4()),self.room,self.solver)
                    self.save(db,session,operation)
                return self.snapshot(session)
            if len(parts) not in (3,4) or parts[:2]!=['test','sessions']:raise LookupError('route')
            sid=str(UUID(parts[2]));row=db.execute('SELECT state FROM sessions WHERE id=?',(sid,)).fetchone()
            if not row:raise LookupError('session')
            session=Localization(sid,self.room,self.solver);self.restore(db,session,row[0])
            if session.context!=self.room:raise ValueError('Pinned BIM changed; start a new test')
            if len(parts)==3 and method=='GET':return self.snapshot(session)
            if method!='POST' or len(parts)!=4:raise LookupError('route')
            action=parts[3]
            if action=='start':session.start(body['segment_id'])
            elif action=='observations':
                segment=str(UUID(body['segment_id']))
                if segment!=session.segment or session.completed:raise ValueError('Old segment or closed session')
                frame,files=self.validate_observation(body,sid,segment)
                oid=frame['observation_id'];folder=self.data/sid/'observations'/oid
                if self.store:self.store.save_files(db,sid,oid,files)
                folder.mkdir(parents=True,exist_ok=True)
                # Original bytes are immutable and retained even if solving fails.
                for name,content in files.items():
                    path=folder/name
                    if path.exists() and path.read_bytes()!=content:raise ValueError('Observation bytes changed')
                for name,content in files.items():
                    if self.store and name=='rgb.jpg':continue # RGB stays on the phone and its normal capture upload.
                    if not (folder/name).exists():atomic(folder/name,content)
                record={'observation_id':oid,'source_verified':True,'_folder':str(folder.resolve()),
                        'files':[{'name':name,'sha256':hashlib.sha256(data).hexdigest(),'byte_length':len(data)} for name,data in sorted(files.items())]}
                session.observe(oid,segment,[record])
            elif action=='pose':session.telemetry(body,time.time())
            elif action=='finish':session.finish()
            else:raise LookupError('route')
            self.save(db,session)
            return self.snapshot(session)

    def save(self,db,session,operation=None):
        state=json_bytes({k:v for k,v in session.__dict__.items() if k!='solver'}).decode()
        if operation is not None:db.execute('INSERT INTO sessions VALUES (?,?,?)',(session.session_id,operation,state))
        else:db.execute('UPDATE sessions SET state=? WHERE id=?',(state,session.session_id))

    @staticmethod
    def validate_observation(body,sid,segment):
        encoded=body['files']
        allowed={'frame.json','rgb.jpg','depth.f32','confidence.u8'}
        if not isinstance(encoded,dict) or not {'frame.json','rgb.jpg','depth.f32'}<=encoded.keys() or not encoded.keys()<=allowed:
            raise ValueError('LiDAR frame files required')
        files={k:base64.b64decode(v,validate=True) for k,v in encoded.items()}
        frame=json.loads(files['frame.json'])
        if frame['schema_version']!='0.2' or str(UUID(frame['session_id']))!=sid or str(UUID(frame['segment_id']))!=segment:
            raise ValueError('Frame scope mismatch')
        if str(UUID(frame['observation_id']))!=frame['frame_id'] or frame['capture_origin']!='device':raise ValueError('Real frame required')
        if frame['tracking_state']!='normal' or frame['depth_source']!='sceneDepth' or frame['depth_unit']!='m':raise ValueError('Normal sceneDepth capture required')
        rigid(frame['T_world_camera'])
        w,h=frame['depth_size']
        if type(w)!=int or type(h)!=int or not 0<w*h<=1000000 or len(files['depth.f32'])!=w*h*4:raise ValueError('Depth dimensions')
        if 'confidence.u8' in files and len(files['confidence.u8'])!=w*h:raise ValueError('Confidence dimensions')
        if frame.get('source_to_stored_image')!=[[1,0,0],[0,1,0],[0,0,1]]:raise ValueError('Rotated/resized RGB unsupported')
        if frame['image_orientation']!='native_buffer_unrotated_no_exif_rotation':raise ValueError('Unsupported image orientation')
        for key,name in [('rgb','rgb.jpg'),('depth','depth.f32'),('confidence','confidence.u8')]:
            ref=frame.get(key)
            if (ref is None)!=(name not in files):raise ValueError('Missing or extra file')
            if ref is not None and (ref['name']!=name or ref['byte_length']!=len(files[name]) or ref['sha256']!=hashlib.sha256(files[name]).hexdigest()):raise ValueError('File hash mismatch')
        with Image.open(io.BytesIO(files['rgb.jpg'])) as im:
            if im.format!='JPEG' or list(im.size)!=frame['rgb_size']:raise ValueError('RGB dimensions')
            im.verify()
        return frame,files


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass # No photos, pairing codes, headers or body in logs.
    def do_GET(self):self.handle_request()
    def do_POST(self):self.handle_request()
    def handle_request(self):
        self.connection.settimeout(30)
        if ipaddress.ip_address(self.client_address[0]) not in self.server.allowed_network:
            self.reply(403,{'message':'Local Wi-Fi clients only'});return
        now=time.monotonic();client=self.client_address[0]
        with self.server.attempts_lock:
            failures=[t for t in self.server.attempts.get(client,[]) if now-t<60]
            limited=len(failures)>=5
            if not limited and not hmac.compare_digest(self.headers.get('X-Localization-Code',''),self.server.lab.code):
                failures.append(now)
            self.server.attempts[client]=failures
        if limited:
            self.reply(429,{'message':'Слишком много неверных кодов. Подождите минуту.'});return
        if not hmac.compare_digest(self.headers.get('X-Localization-Code',''),self.server.lab.code):
            self.reply(401,{'message':'Неверный код подключения'});return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<=length<=16*1024*1024:raise ValueError('Request exceeds 16 MiB')
            body=json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(body,dict):raise ValueError('JSON object required')
            result=self.server.lab.request(self.command,self.path.strip('/').split('/'),body)
            self.reply(200,result)
        except (KeyError,ValueError,TypeError,OverflowError) as error:self.reply(422,{'message':str(error)[:250]})
        except LookupError:self.reply(404,{'message':'Not found'})
        except Exception:self.reply(500,{'message':'Не удалось обработать данные; оригиналы сохранены, повторите запрос.'})
    def reply(self,code,value):
        data=json_bytes(value)
        self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--ifc',required=True);parser.add_argument('--data',default='.local/live-localization')
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8788)
    parser.add_argument('--allow-network',default='127.0.0.0/8')
    args=parser.parse_args();data=Path(args.data).resolve();data.mkdir(parents=True,exist_ok=True)
    codefile=data/'pairing-code.txt'
    if not codefile.exists():
        fd=os.open(codefile,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(secrets.token_hex(6)+'\n')
    code=codefile.read_text().strip();room=load_room(args.ifc)
    server=ThreadingHTTPServer((args.host,args.port),Handler);server.lab=Lab(data,room,code,load_display_meshes(args.ifc,room));server.allowed_network=ipaddress.ip_network(args.allow_network);server.attempts={};server.attempts_lock=threading.Lock()
    print(f'Local Wi-Fi test on port {args.port}; pairing code in {codefile}',flush=True)
    print('Real-room accuracy UNVERIFIED. First photo must center selected door from inside.',flush=True)
    server.serve_forever()

if __name__=='__main__':main()
