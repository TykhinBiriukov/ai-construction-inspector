"""Anonymous demo sessions isolated from the operator project and each other."""
import hashlib
import hmac
import re
import secrets
import time
from collections import OrderedDict, deque
from starlette.responses import JSONResponse
from .config import Settings

PREFIX = '/public-demo'


def mint(settings):
    payload = f'{secrets.token_hex(16)}.{int(time.time()) + 86400}'
    signature = hmac.new(settings.operator_token.encode(), ('public-demo:' + payload).encode(), hashlib.sha256).hexdigest()
    return 'demo.' + payload + '.' + signature


def verify(settings, token):
    match = re.fullmatch(r'demo\.([a-f0-9]{32})\.(\d{10})\.([a-f0-9]{64})', token)
    if not match or int(match[2]) <= time.time():
        return None
    expected = hmac.new(settings.operator_token.encode(), ('public-demo:' + match[1] + '.' + match[2]).encode(), hashlib.sha256).hexdigest()
    return match[1] if hmac.compare_digest(expected, match[3]) else None


class PublicDemo:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.apps = OrderedDict()
        self.expensive = deque()

    async def __call__(self, scope, receive, send):
        if not scope.get('path', '').startswith(PREFIX + '/'):
            return await self.app(scope, receive, send)
        if scope['type'] != 'http':
            if scope['type'] == 'websocket':
                await send({'type':'websocket.close','code':1008})
            return
        token = dict(scope.get('headers', [])).get(b'authorization', b'').decode('latin1').removeprefix('Bearer ')
        session = verify(self.settings, token)
        if not session:
            return await JSONResponse({'message':'Demo session expired. Reload the demo.'},401)(scope,receive,send)
        path = scope['path'][len(PREFIX):]
        allowed = (path in ('/v1/operator/overview','/v1/capabilities','/v1/demo/config','/v1/voice/config','/v1/voice/transcribe','/v1/chats')
                   or path == '/v1/devices/iphone-1/status' or path.startswith('/v1/chats/'))
        if not allowed or scope['method'] not in ('GET','POST','HEAD'):
            return await JSONResponse({'message':'This action is unavailable in the demo'},403)(scope,receive,send)
        if scope['method']=='POST' and (path.endswith('/messages') or path=='/v1/voice/transcribe'):
            now=time.monotonic()
            while self.expensive and self.expensive[0] < now-3600:self.expensive.popleft()
            if len(self.expensive)>=180:
                return await JSONResponse({'message':'Demo usage limit reached. Please try again later.'},429)(scope,receive,send)
            self.expensive.append(now)
        directory=self.settings.data_dir/'public-demo'/session
        parent=directory.parent
        if not directory.exists() and parent.exists() and sum(1 for p in parent.iterdir() if p.is_dir())>=256:
            return await JSONResponse({'message':'The demo is at capacity. Please try again later.'},503)(scope,receive,send)
        if session not in self.apps:
            from .api import create_app
            # Construct from defaults: never inherit production DB, storage, devices,
            # remote agents, Geometry or Localization credentials into a visitor app.
            config=Settings(data_dir=self.settings.data_dir/'public-demo'/session,
                operator_token=token,device_token=secrets.token_urlsafe(32),project_id='demo-'+session,
                saved_demo_dir=self.settings.saved_demo_dir,meta_base_url=self.settings.meta_base_url,
                meta_model=self.settings.meta_model,meta_api_key=self.settings.meta_api_key,
                meta_timeout_s=self.settings.meta_timeout_s,voice_enabled=self.settings.voice_enabled,
                max_bim_bytes=8*1024*1024)
            self.apps[session]=create_app(config,background_worker=False,static_ui=False)
            while len(self.apps)>32:self.apps.popitem(last=False)
        self.apps.move_to_end(session)
        if scope['method']=='POST' and (path=='/v1/chats' or path.endswith('/files')):
            service=self.apps[session].state.service
            with service.database.connection() as connection:
                chats=connection.execute('SELECT COUNT(*) FROM chats').fetchone()[0]
                files=connection.execute('SELECT COUNT(*) FROM workspace_files').fetchone()[0]
            if chats>=20 or files>=120:
                return await JSONResponse({'message':'This demo session has reached its storage limit.'},429)(scope,receive,send)
        child_scope=dict(scope,path=path,raw_path=path.encode(),root_path='')
        await self.apps[session](child_scope,receive,send)
