"""Local service harness. Run python3 -m localization_service.server.

Authenticated internal API only. No default solver, uploads, external URLs or LLM.
SQLite persistence uses JSON, never pickle. Not a production deployment server.
"""
import hmac
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import UUID
from wsgiref.simple_server import make_server
from localization_service.core import Localization


class App:
    def __init__(self, database, token, solver=None):
        if len(token) < 24:
            raise ValueError('LOCALIZATION_SERVICE_TOKEN must have at least 24 characters')
        self.database, self.token, self.solver = str(database), token, solver
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, state TEXT NOT NULL)')

    def __call__(self, env, start_response):
        code, body = 200, {}
        try:
            if not hmac.compare_digest(env.get('HTTP_AUTHORIZATION', ''), 'Bearer '+self.token):
                code, body = 401, {'code':'unauthorized'}
            else:
                path = env.get('PATH_INFO', '').strip('/').split('/')
                method = env.get('REQUEST_METHOD')
                if path == ['health'] and method == 'GET':
                    body = {'status':'ok', 'solver_ready':self.solver is not None,
                            'live_localization_enabled':False, 'mode':'development_harness'}
                elif len(path) in (3, 4) and path[:2] == ['internal', 'sessions']:
                    sid = str(UUID(path[2]))
                    size = int(env.get('CONTENT_LENGTH') or 0)
                    if not 0 <= size <= 1048576:
                        raise ValueError('body too large')
                    raw = env['wsgi.input'].read(size)
                    data = json.loads(raw) if raw else {}
                    if not isinstance(data, dict): raise ValueError('object required')
                    with sqlite3.connect(self.database) as db:
                        db.execute('BEGIN IMMEDIATE')
                        row = db.execute('SELECT state FROM sessions WHERE id=?',(sid,)).fetchone()
                        if row is None:
                            if method != 'POST' or len(path) != 3:
                                raise LookupError('session not found')
                            session = Localization(sid, data['context'], self.solver)
                        else:
                            session = Localization(sid, None, self.solver)
                            session.__dict__.update(json.loads(row[0]))
                        if method == 'POST' and len(path) == 3:
                            if session.context != data['context']: raise ValueError('pinned context conflict')
                        elif method == 'POST' and len(path) == 4:
                            action = path[3]
                            if action == 'start': session.start(data['segment_id'])
                            elif action == 'evidence': session.observe(data['batch_id'], data['segment_id'], data['observations'])
                            elif action == 'pose': session.telemetry(data, time.time())
                            elif action == 'finish': session.finish()
                            else: raise LookupError('route not found')
                        elif method != 'GET' or len(path) != 3:
                            raise LookupError('route not found')
                        state = {k:v for k,v in session.__dict__.items() if k != 'solver'}
                        db.execute('INSERT INTO sessions VALUES (?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state',
                                   (sid,json.dumps(state, allow_nan=False)))
                        body = session.snapshot(time.time())
                else: raise LookupError('route not found')
        except LookupError:
            code, body = 404, {'code':'not_found'}
        except (ValueError, KeyError, TypeError):
            code, body = 422, {'code':'invalid_request'}
        except Exception:
            code, body = 500, {'code':'internal_error'}
        output = json.dumps(body, allow_nan=False).encode()
        labels={200:'OK',401:'Unauthorized',404:'Not Found',422:'Unprocessable Entity',500:'Internal Server Error'}
        start_response(f'{code} {labels[code]}',[('Content-Type','application/json'),('Content-Length',str(len(output)))])
        return [output]


if __name__ == '__main__':
    token = os.environ.get('LOCALIZATION_SERVICE_TOKEN','')
    folder = Path(os.environ.get('LOCALIZATION_DATA_DIR','.local/localization'))
    folder.mkdir(parents=True, exist_ok=True)
    app = App(folder/'state.sqlite3',token)
    print('Local development service: http://127.0.0.1:8787 — geometric solver NOT configured')
    make_server('127.0.0.1',8787,app).serve_forever()
