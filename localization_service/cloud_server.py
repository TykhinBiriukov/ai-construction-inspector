"""Single-instance cloud entrypoint, HTTPS terminated by the hosting provider."""
import hashlib
import ipaddress
import os
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer
from localization_service.lab_server import Handler, Lab
from localization_service.cloud_store import PostgresStore
from localization_service.room_model import load_room, load_display_meshes


class CloudHandler(Handler):
    def do_GET(self):
        if self.path=='/healthz':self.reply(200,{'status':'ready'})
        else:super().do_GET()


def configuration(env):
    token=env.get('LOCALIZATION_SERVICE_TOKEN','')
    if len(token)<32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError('Set a dedicated random ASCII LOCALIZATION_SERVICE_TOKEN (>=32 characters)')
    dsn=env.get('LOCALIZATION_DATABASE_URL','')
    if not dsn:raise ValueError('Configure the dedicated localization database role')
    model_hash=env.get('LOCALIZATION_MODEL_SHA256','')
    if len(model_hash)!=64 or any(c not in '0123456789abcdef' for c in model_hash):
        raise ValueError('Configure LOCALIZATION_MODEL_SHA256')
    return token,dsn,model_hash,Path(env.get('LOCALIZATION_DATA_DIR','/data'))


def main():
    token,dsn,model_hash,data=configuration(os.environ)
    store=PostgresStore(dsn)
    data.mkdir(parents=True,exist_ok=True)
    model=data/'room.ifc';model.write_bytes(store.model_bytes(model_hash))
    room=load_room(model)
    server=ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8788'))),CloudHandler)
    server.lab=Lab(data,room,token,load_display_meshes(model,room),store=store)
    server.allowed_network=ipaddress.ip_network('0.0.0.0/0')
    server.attempts={};server.attempts_lock=threading.Lock()
    print('Localization ready: authenticated API, external durable state',flush=True)
    server.serve_forever()


if __name__=='__main__':main()
