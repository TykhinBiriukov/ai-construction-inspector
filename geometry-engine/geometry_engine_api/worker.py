"""Durable local worker: PostgreSQL leases, Storage checkpoints and cached SAM."""
import argparse
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import re
import socket
from tempfile import TemporaryDirectory
import threading
import time
from uuid import uuid4

import httpx
from sqlalchemy.exc import OperationalError, InterfaceError, TimeoutError as DatabaseTimeout
from supabase import ClientOptions, create_client
from storage3.exceptions import StorageApiError

from geometry_engine import inspection
from geometry_engine.segmentation.cache import CachedSamBackend
from geometry_engine.segmentation.sam_backend import Sam3Backend
from geometry_engine.segmentation.service import SegmentationService
from geometry_engine_api.database.connection import get_engine
from geometry_engine_api.services.archive import ArchiveServiceError, Settings
from geometry_engine_api.services.artifacts import ArtifactError, ArtifactRef, ArtifactStore, SupabaseObjectStore, fingerprint, sha_file
from geometry_engine_api.services.job_inputs import InspectionInputs, INPUT_OUTPUTS
from geometry_engine_api.services.job_queue import AttemptsExhausted, JobQueue, LeaseLost
from geometry_engine_api.services.segmentation_cache import SegmentationCacheRepository


logger = logging.getLogger('geometry.worker')


def classify_error(error):
    """Persist only stable codes. Provider messages may contain signed URLs."""
    if isinstance(error, ArtifactError):
        return 'artifact_integrity_failed', False
    if isinstance(error, ArchiveServiceError):
        return ('input_download_failed', True) if error.code == 'download_failed' else ('invalid_capture', False)
    if isinstance(error, (OperationalError, InterfaceError, DatabaseTimeout)):
        return 'database_unavailable', True
    if isinstance(error, StorageApiError):
        try:
            status = int(error.status)
        except (ValueError, TypeError, AttributeError):
            status = 0
        return f'storage_http_{status}', not status or status in (408, 425, 429) or status >= 500
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return f'upstream_http_{status}', status in (408, 425, 429) or status >= 500
    if isinstance(error, (httpx.HTTPError, TimeoutError, ConnectionError)):
        return 'upstream_unavailable', True
    if hasattr(error, 'retryable'):
        code = getattr(error, 'code', None)
        if not isinstance(code, str) or not re.fullmatch(r'[a-z0-9_]{1,64}', code.lower()):
            code = 'provider_unavailable' if error.retryable else 'provider_configuration_or_response'
        return code.lower(), bool(error.retryable)
    if isinstance(error, (ValueError, FileNotFoundError)):
        return 'invalid_input_or_configuration', False
    if isinstance(error, OSError):
        return 'local_io_failed', False
    return 'stage_execution_failed', False


class Heartbeat:
    def __init__(self, queue, lease):
        self.queue, self.lease = queue, lease
        self.stop, self.lost = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name='inspection-heartbeat')

    def _run(self):
        while not self.stop.wait(min(20, self.queue.lease_seconds / 3)):
            try:
                if not self.queue.heartbeat(self.lease):
                    self.lost.set()
                    return
            except Exception:
                self.lost.set()
                return

    def check(self):
        if self.lost.is_set():
            raise LeaseLost('Worker heartbeat failed; results will not be committed')

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=10)


class InspectionWorker:
    def __init__(self, queue, artifacts, inputs, work_root, *, segmentation_factory,
                 adapter=inspection, worker_id=None):
        self.queue, self.artifacts, self.inputs = queue, artifacts, inputs
        self.work_root = Path(work_root).resolve()
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.segmentation_factory, self.adapter = segmentation_factory, adapter
        self.worker_id = worker_id or f'{socket.gethostname()[:60]}-{os.getpid()}-{uuid4().hex[:8]}'
        self._prefer_viewer = False

    def run_once(self):
        order = ('viewer', 'inspection') if self._prefer_viewer else ('inspection', 'viewer')
        lease = None
        for kind in order:
            lease = self.queue.claim(kind, self.worker_id)
            if lease is not None:
                break
        if lease is None:
            return False
        self._prefer_viewer = lease.kind == 'inspection'
        logger.info('Claimed %s %s', lease.kind, lease.id)
        try:
            with TemporaryDirectory(prefix=f'{lease.id}-{lease.token}-', dir=self.work_root) as temporary:
                workspace = Path(temporary) / lease.session_id
                workspace.mkdir()
                with Heartbeat(self.queue, lease) as heartbeat:
                    self.execute(lease, workspace, heartbeat)
        except OSError as error:
            self._fail(lease, None, error)
        return True

    def execute(self, lease, workspace, heartbeat):
        active_stage = None
        try:
            implementation = self.adapter.implementation_fingerprint()
            if lease.kind == 'viewer':
                return self._viewer(lease, workspace, heartbeat, implementation)
            manifests, known, local_files = {}, {}, {}
            segmentation = None
            for name in self.adapter.stage_names():
                heartbeat.check()
                if name == 'segmentation':
                    segmentation = self.segmentation_factory(workspace)
                stage_fingerprint = fingerprint({
                    'name': name, 'pipeline_version': lease.pipeline_version, 'implementation': implementation,
                    'configuration': self.adapter.stage_configuration(name, lease.parameters, segmentation),
                    'inputs': {'session_id': lease.session_id, 'capture_manifest_path': lease.capture_manifest_path,
                               'ifc_object_key': lease.ifc_object_key} if name == 'download' else {
                        dependency: manifests[dependency]['sha256'] for dependency in self.adapter.stage_dependencies(name)},
                })
                state = self.queue.begin_stage(lease, name, stage_fingerprint)
                if state.status == 'completed':
                    ref = ArtifactRef(state.artifact_manifest_key, state.artifact_sha256)
                    logger.info('%s restoring %s', lease.id, name)
                    manifest = self.artifacts.restore(ref, workspace, local_files=local_files)
                else:
                    active_stage = name
                    logger.info('%s starting %s attempt=%d', lease.id, name, state.attempt)
                    references = None
                    if name == 'download':
                        references = self.inputs.download(lease, workspace)
                    else:
                        self.adapter.run_stage(name, workspace, lease.parameters, segmentation,
                                               progress=lambda text: self._progress(lease, heartbeat, text))
                    heartbeat.check()
                    prefix = f'{lease.session_id}/results/{lease.id}/{name}/attempt_{state.attempt:03d}'
                    outputs = INPUT_OUTPUTS if name == 'download' else self.adapter.stage_outputs(name)
                    ref, manifest = self.artifacts.publish(workspace, outputs, prefix,
                                                         known_files=known, external_files=references)
                    heartbeat.check()
                    self.queue.complete_stage(lease, name, ref.key, ref.sha256)
                    active_stage = None
                    logger.info('%s completed %s', lease.id, name)
                manifests[name] = {'key': ref.key, 'sha256': ref.sha256, 'fingerprint': stage_fingerprint}
                known.update(self.artifacts.file_index(manifest))
            heartbeat.check()
            result = {'schema_version': 1, 'job_id': str(lease.id), 'session_id': lease.session_id,
                      'pipeline_version': lease.pipeline_version, 'implementation': implementation,
                      'parameters': lease.parameters, 'stages': manifests,
                      'ifc_sha256': sha_file(workspace / 'reference.ifc'),
                      'artifacts': {
                          'point_cloud': 'reconstruction/point_cloud.ply',
                          'mesh': 'reconstruction/mesh.ply',
                          'segmented_mesh': 'mesh_segmentation/mesh.ply',
                          'comparison_report': 'scan_vs_bim/report.json',
                          'opening_report': 'opening_comparison/report.json',
                          'photo_evidence': 'opening_comparison/evidence',
                      }}
            ref = self.artifacts.put_json(f'{lease.session_id}/results/{lease.id}/result-manifests', result)
            heartbeat.check()
            self.queue.complete(lease, ref.key, ref.sha256, ifc_sha256=result['ifc_sha256'])
            logger.info('%s completed inspection', lease.id)
        except (LeaseLost, AttemptsExhausted):
            logger.warning('%s execution stopped: lease lost or attempts exhausted', lease.id)
        except Exception as error:
            self._fail(lease, active_stage, error, workspace)

    @staticmethod
    def _progress(lease, heartbeat, text):
        heartbeat.check()
        logger.info('%s %s', lease.id, text)

    def _fail(self, lease, stage, error, workspace=None):
        code, retryable = classify_error(error)
        diagnostic = {}
        if stage and workspace is not None:
            try:
                state = next(s for s in self.queue.stages(lease) if s.name == stage)
                if state.status == 'running':
                    report = workspace / '_failures' / f'{stage}.json'
                    report.parent.mkdir(exist_ok=True)
                    report.write_text(json.dumps({'job_id': str(lease.id), 'stage': stage,
                        'attempt': state.attempt, 'error_code': code, 'retryable': retryable,
                        'error_type': type(error).__name__}, indent=2) + '\n', encoding='utf-8')
                    outputs = [report.relative_to(workspace).as_posix()]
                    for output in self.adapter.stage_outputs(stage):
                        path = workspace / output / 'report.json'
                        if path.is_file():
                            outputs.append(path.relative_to(workspace).as_posix())
                    job_id = lease.source_job_id or lease.id
                    prefix = f'{lease.session_id}/results/{job_id}/{stage}/attempt_{state.attempt:03d}/failure/{lease.token}'
                    ref, _ = self.artifacts.publish(workspace, outputs, prefix)
                    diagnostic = {'artifact_manifest_key': ref.key, 'artifact_sha256': ref.sha256}
            except Exception:
                logger.warning('%s could not upload failed-stage diagnostics', lease.id)
        try:
            if stage:
                status = self.queue.fail_stage(lease, stage, code, retryable=retryable, **diagnostic)
            else:
                status = self.queue.fail(lease, code, retryable=retryable)
            logger.warning('%s stage=%s status=%s error=%s type=%s', lease.id, stage, status, code, type(error).__name__)
        except Exception:
            # If the database is unavailable, leave the lease to expire. Another
            # worker can recover; never claim a checkpoint was committed locally.
            logger.warning('%s could not persist failure; waiting for lease expiry', lease.id)

    def _viewer(self, lease, workspace, heartbeat, implementation):
        active_stage = None
        try:
            source = self.artifacts.read_json(ArtifactRef(lease.result_manifest_key, lease.result_manifest_sha256))
            if source.get('job_id') != str(lease.source_job_id) or source.get('session_id') != lease.session_id:
                raise ArtifactError('Viewer source belongs to a different inspection')
            requested_code = self.adapter.code_fingerprint()
            if (lease.viewer_version != self.adapter.VIEWER_VERSION
                    or getattr(lease, 'requested_implementation', requested_code) != requested_code):
                raise ValueError('Viewer implementation changed after submission; request a new export')
            fp = fingerprint({'source': lease.result_manifest_sha256, 'export': lease.fingerprint,
                              'implementation': implementation})
            state = self.queue.begin_stage(lease, 'viewer', fp)
            if state.status == 'completed':
                ref = ArtifactRef(state.artifact_manifest_key, state.artifact_sha256)
                self.artifacts.read_json(ref)
            else:
                active_stage = 'viewer'
                local_files = {}
                for name, saved in source['stages'].items():
                    heartbeat.check()
                    self.artifacts.restore(ArtifactRef(saved['key'], saved['sha256']), workspace, local_files=local_files)
                self.adapter.build_viewer(workspace)
                heartbeat.check()
                prefix = f'{lease.session_id}/results/{lease.source_job_id}/viewer/{lease.id}/attempt_{state.attempt:03d}'
                ref, _ = self.artifacts.publish(workspace, ('viewer',), prefix)
                heartbeat.check()
                self.queue.complete_stage(lease, 'viewer', ref.key, ref.sha256)
                active_stage = None
            heartbeat.check()
            self.queue.complete(lease, ref.key, ref.sha256)
            logger.info('%s completed viewer export', lease.id)
        except (LeaseLost, AttemptsExhausted):
            raise
        except Exception as error:
            self._fail(lease, active_stage, error, workspace)


@contextmanager
def configured_worker():
    settings = Settings.from_env()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ValueError('Set SUPABASE_URL and SUPABASE_SECRET_KEY in .env')
    engine = get_engine()
    with httpx.Client(timeout=120, trust_env=False) as http_client:
        client = create_client(settings.supabase_url, settings.supabase_secret_key,
            options=ClientOptions(httpx_client=http_client, auto_refresh_token=False, persist_session=False))
        objects = SupabaseObjectStore(client, settings.storage_bucket, http_client=http_client)
        repository = SegmentationCacheRepository(engine)

        def segmentation_factory(workspace):
            backend = Sam3Backend(os.getenv('MODEL_API_KEY', ''))
            cached = CachedSamBackend(backend, repository, objects, workspace / '.sam-cache',
                                      cache_epoch=os.getenv('SAM_CACHE_EPOCH', '1'))
            return SegmentationService(cached)

        yield InspectionWorker(JobQueue(engine,
            lease_seconds=float(os.getenv('WORKER_LEASE_SECONDS', '120')),
            max_attempts=int(os.getenv('WORKER_MAX_ATTEMPTS', '3')),
            retry_base_seconds=float(os.getenv('WORKER_RETRY_BASE_SECONDS', '10'))),
            ArtifactStore(objects), InspectionInputs(settings, client),
            settings.data_dir / 'work', segmentation_factory=segmentation_factory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Process at most one ready inspection or viewer export')
    parser.add_argument('--poll-seconds', type=float, default=2)
    args = parser.parse_args()
    if not 0 < args.poll_seconds <= 60:
        parser.error('--poll-seconds must be between 0 and 60')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    # Suppress SDK URL/header logs; signed Storage URLs are credentials.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    try:
        with configured_worker() as worker:
            logger.info('Worker ready. Apply migrations before starting workers.')
            while True:
                try:
                    processed = worker.run_once()
                except (OperationalError, InterfaceError, DatabaseTimeout):
                    logger.warning('Database unavailable; retrying polling')
                    processed = False
                if args.once:
                    return
                if not processed:
                    time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        logger.info('Worker stopped; unfinished leases will be recovered after expiry')


if __name__ == '__main__':
    main()
