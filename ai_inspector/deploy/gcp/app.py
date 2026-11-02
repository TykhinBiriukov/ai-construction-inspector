"""Native Inspector with existing restricted Supabase persistence and gateway auth."""
import hmac
import os
from ai_inspector.api import create_app
from ai_inspector.config import Settings
from ai_inspector.postgres_storage import PostgresDatabase
from ai_inspector.service import InspectorService
from starlette.responses import JSONResponse

class GatewayAuth:
    def __init__(self, app, token):
        self.app, self.token = app, token
    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            headers = dict(scope['headers'])
            supplied = headers.get(b'x-inspector-gateway-token', b'')
            if not hmac.compare_digest(supplied, self.token.encode()):
                if scope['type'] == 'websocket':
                    await send({'type': 'websocket.close', 'code': 1008})
                else:
                    await JSONResponse({'code': 'unauthorized'}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)

def create_backend():
    settings = Settings.from_env()
    token = os.environ['INSPECTOR_GATEWAY_TOKEN']
    if len(token) < 32 or not settings.database_url or settings.bim_storage != 'supabase':
        raise RuntimeError('Persistent backend configuration is required')
    service = InspectorService(settings, database=PostgresDatabase(settings.database_url, initialize=False))
    app = create_app(settings, service=service, background_worker=os.environ.get('INSPECTOR_BACKGROUND_WORKER', 'true') == 'true', static_ui=False)
    @app.get('/deployment-health')
    def deployment_health():
        with service.database.connection() as conn:
            conn.execute('SELECT 1 FROM chats LIMIT 1').fetchone()
        return {'status': 'ok', 'hosting': 'gcp', 'revision': os.environ.get('INSPECTOR_REVISION', 'unknown')}
    app.add_middleware(GatewayAuth, token=token)
    return app
