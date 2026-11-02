"""Cloudflare entrypoint: persisted Inspector activity attempts and timing v3."""
from contextvars import ContextVar
from pathlib import Path
import httpx
from workers import WorkerEntrypoint, DurableObject, Response, asgi
from ai_inspector.api import create_app
from ai_inspector.config import Settings
from ai_inspector.service import InspectorService
from database import WorkerDatabase
from runtime_settings import meta_settings
from model_transport import WorkerModelTransport
from ai_inspector.model_client import MetaModelClient
from ai_inspector.worker_observability import WorkerTelemetry
from ai_inspector.observability import ObservabilityConfig

CLOUD_LIMITS = dict(max_bim_bytes=8 * 1024**2, max_archive_bytes=16 * 1024**2,
                    max_unpacked_archive_bytes=64 * 1024**2, max_capture_file_bytes=16 * 1024**2,
                    max_manifest_bytes=4 * 1024**2, max_capture_files=4000, max_archive_entries=4000)

# Compile routes and validation schemas in the deployment snapshot, not per request.
_settings = ContextVar('settings', default=Settings(data_dir=Path('/tmp/inspector'),
                                                   operator_token='snapshot-operator', device_token='snapshot-device', **CLOUD_LIMITS))
_service = ContextVar('service')

class SettingsProxy:
    def __getattr__(self, name):
        return getattr(_settings.get(), name)

class ServiceProxy:
    def __getattr__(self, name):
        return getattr(_service.get(), name)

APP = create_app(SettingsProxy(), service=ServiceProxy(), background_worker=False, static_ui=False, websocket_poll_interval=5.0)

class InspectorRuntime(DurableObject):
    def service(self):
        env = self.env
        settings = Settings(
            observability=ObservabilityConfig.from_mapping({name: getattr(env, name, None) or default for name, default in {
                'LANGFUSE_PUBLIC_KEY':'', 'LANGFUSE_SECRET_KEY':'', 'LANGFUSE_BASE_URL':'https://cloud.langfuse.com',
                'LANGFUSE_ENABLED':'true', 'LANGFUSE_TRACING_ENVIRONMENT':'production', 'LANGFUSE_RELEASE':'',
                'INSPECTOR_LANGFUSE_PROMPTS_ENABLED':'false', 'INSPECTOR_LANGFUSE_PROMPT_LABEL':'production'}.items()}),
            data_dir=Path('/tmp/inspector'), **CLOUD_LIMITS, **meta_settings(env),
            operator_token=env.INSPECTOR_OPERATOR_TOKEN,
            device_token=env.INSPECTOR_DEVICE_TOKEN,
            database_url=env.INSPECTOR_DATABASE_URL,
            bim_storage='supabase',
            supabase_url=env.SUPABASE_URL,
            supabase_storage_bucket=env.SUPABASE_STORAGE_BUCKET,
            supabase_secret_key=env.SUPABASE_SECRET_KEY,
            drawing_photo_workflow=getattr(env,'INSPECTOR_DRAWING_PHOTO_WORKFLOW','legacy'),
            drawing_photo_base_url=getattr(env,'INSPECTOR_DRAWING_PHOTO_BASE_URL',''),
            drawing_photo_service_token=getattr(env,'INSPECTOR_DRAWING_PHOTO_SERVICE_TOKEN',''),
            localization_base_url=getattr(env,'INSPECTOR_LOCALIZATION_BASE_URL',''),
            localization_service_token=getattr(env,'INSPECTOR_LOCALIZATION_SERVICE_TOKEN',''),
            geometry_base_url=getattr(env,'INSPECTOR_GEOMETRY_BASE_URL',''),
            geometry_review_inspector_id=getattr(env,'INSPECTOR_GEOMETRY_REVIEW_INSPECTOR_ID',''),
            geometry_api_token=getattr(env,'INSPECTOR_GEOMETRY_API_TOKEN',''),
            geometry_api_username=getattr(env,'INSPECTOR_GEOMETRY_API_USERNAME','') or '',
            remote_agents_json=getattr(env,'INSPECTOR_REMOTE_AGENTS_JSON','[]') or '[]',
        )
        service = InspectorService(settings,
            database=WorkerDatabase(env.HYPERDRIVE.connectionString, hyperdrive=True),
            model_client=MetaModelClient(settings, transport=WorkerModelTransport()),
            remote_transport=WorkerModelTransport(), secret_resolver=lambda name: getattr(env, name, None))
        service.localization_transport = WorkerModelTransport()
        service.geometry_transport = WorkerModelTransport()
        # Supabase and Geometry share the bounded Fetch transport on Workers;
        # native socket transports cannot perform this IO in Python/WASM.
        storage_client = httpx.Client(transport=WorkerModelTransport(), timeout=30)
        from ai_inspector.supabase_captures import SupabaseCaptureSourceStore
        service.capture._supabase_source_store = SupabaseCaptureSourceStore(settings, storage_client)
        service.session_folders.client = storage_client
        service.bim_store.remote.http = storage_client
        service.agent.telemetry = WorkerTelemetry(settings.observability, WorkerModelTransport())
        return service

    async def fetch(self, request):
        service = self.service()
        settings_token = _settings.set(service.settings)
        service_token = _service.set(service)
        try:
            if (request.headers.get('upgrade') or '').lower() == 'websocket':
                return await asgi.websocket(APP,request,self.env)
            return await asgi.fetch(APP,request,self.env)
        finally:
            service.agent.telemetry.flush()
            _service.reset(service_token)
            _settings.reset(settings_token)

    async def process(self):
        service = self.service()
        try:
            captured = service.capture.process_next(heartbeat_enabled=False)
            return service.agent.process_next() or captured
        finally:
            service.agent.telemetry.flush()

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response('Private runtime', status=404)

    async def scheduled(self, controller, env, ctx):
        await self.env.INSPECTOR.getByName('main').process()
