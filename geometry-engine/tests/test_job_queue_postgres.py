"""Real PostgreSQL concurrency, retry, fencing, and export API tests.

All records live in a newly generated disposable schema. Application jobs are
never claimed, modified, or executed.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from pathlib import Path
import re
import threading
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from geometry_engine_api.app import create_app
from geometry_engine_api.database.connection import get_database_session
from geometry_engine_api.database.migrate import upgrade_database
from geometry_engine_api.database.models import InspectionJob, ViewerExport
from geometry_engine_api.schemas.inspections import InspectionRequest
from geometry_engine_api.services.inspections import InspectionService
from geometry_engine_api.services.job_queue import AttemptsExhausted, JobQueue, LeaseLost


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Set TEST_DATABASE_URL for PostgreSQL integration tests')
class JobQueuePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = 'queue_test_' + uuid4().hex
        cls.admin = create_engine(os.environ['TEST_DATABASE_URL'], connect_args={'connect_timeout': 5})
        with cls.admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(os.environ['TEST_DATABASE_URL'], pool_size=10,
            isolation_level='READ COMMITTED', connect_args={
                'connect_timeout': 5, 'options': f'-c search_path={cls.schema} -c timezone=UTC'})
        try:
            with cls.engine.connect() as connection:
                upgrade_database(connection)
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        assert re.fullmatch(r'queue_test_[0-9a-f]{32}', cls.schema)
        with cls.admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.admin.dispose()

    def setUp(self):
        with self.engine.begin() as connection:
            connection.execute(delete(InspectionJob))
        self.queue = JobQueue(self.engine, retry_base_seconds=0)

    def submit(self):
        with Session(self.engine, expire_on_commit=False) as session:
            return InspectionService(session).submit(InspectionRequest(
                session_id='capture', capture_manifest_path='capture/manifest.json',
                ifc_object_key='capture/reference.ifc'), str(uuid4()))

    def expire(self, lease):
        model = InspectionJob if lease.kind == 'inspection' else ViewerExport
        with self.engine.begin() as connection:
            connection.execute(update(model).where(model.id == lease.id).values(
                lease_expires_at=func.now() - timedelta(seconds=1)))

    def test_concurrent_workers_claim_once(self):
        job = self.submit()
        barrier = threading.Barrier(6)
        def claim(index):
            barrier.wait(timeout=10)
            return self.queue.claim(worker_id=str(index))
        with ThreadPoolExecutor(max_workers=6) as pool:
            leases = [lease for lease in pool.map(claim, range(6)) if lease is not None]
        self.assertEqual(len(leases), 1)
        self.assertEqual(leases[0].id, job.id)

    def test_internal_tables_have_rls_without_client_policies(self):
        names = ('inspection_jobs', 'inspection_stages', 'viewer_exports', 'segmentation_cache')
        with self.engine.connect() as connection:
            rows = connection.execute(text('''
                SELECT c.relname, c.relrowsecurity
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema AND c.relname IN
                    ('inspection_jobs', 'inspection_stages', 'viewer_exports', 'segmentation_cache')
            '''), {'schema': self.schema}).all()
            policies = connection.scalar(text('''
                SELECT count(*) FROM pg_policies WHERE schemaname = :schema
                  AND tablename IN ('inspection_jobs', 'inspection_stages', 'viewer_exports', 'segmentation_cache')
            '''), {'schema': self.schema})
        self.assertEqual(dict(rows), {name: True for name in names})
        self.assertEqual(policies, 0)

    def test_expired_owner_cannot_publish_or_heartbeat(self):
        self.submit()
        old = self.queue.claim()
        self.queue.begin_stage(old, 'reconstruction', 'a'*64)
        self.expire(old)
        new = self.queue.claim()
        self.assertNotEqual(old.token, new.token)
        self.assertFalse(self.queue.heartbeat(old))
        with self.assertRaises(LeaseLost):
            self.queue.complete_stage(old, 'reconstruction', 'old/manifest.json', 'b'*64)
        with self.assertRaises(ValueError):
            self.queue.complete_stage(new, 'reconstruction', 'unexecuted/manifest.json', 'b'*64)
        state = self.queue.begin_stage(new, 'reconstruction', 'a'*64)
        self.assertEqual(state.attempt, 2)
        self.queue.complete_stage(new, 'reconstruction', 'new/manifest.json', 'b'*64)
        self.assertEqual(self.queue.stages(new)[0].artifact_manifest_key, 'new/manifest.json')

    def test_retry_preserves_completed_stages_and_is_bounded(self):
        self.submit()
        lease = self.queue.claim()
        self.queue.begin_stage(lease, 'download', 'a'*64)
        self.queue.complete_stage(lease, 'download', 'download/manifest.json', 'b'*64)
        for attempt in range(1, 4):
            state = self.queue.begin_stage(lease, 'segmentation', 'c'*64)
            self.assertEqual(state.attempt, attempt)
            result = self.queue.fail_stage(lease, 'segmentation', 'provider_timeout', retryable=True)
            if attempt < 3:
                self.assertEqual(result, 'retry_wait')
                lease = self.queue.claim()
                saved = self.queue.begin_stage(lease, 'download', 'a'*64)
                self.assertEqual((saved.status, saved.attempt), ('completed', 1))
            else:
                self.assertEqual(result, 'failed')
                self.assertIsNone(self.queue.claim())

    def test_backoff_and_non_retryable_errors(self):
        self.submit()
        queue = JobQueue(self.engine, retry_base_seconds=60)
        lease = queue.claim()
        queue.begin_stage(lease, 'download', 'a'*64)
        queue.fail_stage(lease, 'download', 'storage_timeout', retryable=True)
        self.assertIsNone(queue.claim())
        with self.engine.begin() as connection:
            connection.execute(update(InspectionJob).values(next_attempt_at=func.now()-timedelta(seconds=1)))
        lease = queue.claim()
        queue.begin_stage(lease, 'download', 'a'*64)
        self.assertEqual(queue.fail_stage(lease, 'download', 'invalid_capture', retryable=False), 'failed')
        self.assertIsNone(queue.claim())

    def test_repeated_crashes_exhaust_attempts(self):
        self.submit()
        for attempt in range(1, 4):
            lease = self.queue.claim()
            self.assertEqual(self.queue.begin_stage(lease, 'mesh', 'a'*64).attempt, attempt)
            self.expire(lease)
        lease = self.queue.claim()
        with self.assertRaises(AttemptsExhausted):
            self.queue.begin_stage(lease, 'mesh', 'a'*64)
        self.assertIsNone(self.queue.claim())

    def test_stage_settings_cannot_change_inside_job(self):
        self.submit()
        lease = self.queue.claim()
        self.queue.begin_stage(lease, 'segmentation', 'a'*64)
        self.queue.complete_stage(lease, 'segmentation', 'm.json', 'b'*64)
        with self.assertRaises(ValueError):
            self.queue.begin_stage(lease, 'segmentation', 'c'*64)

    def test_outside_stage_recovery_is_bounded(self):
        self.submit()
        for attempt in range(1, 4):
            lease = self.queue.claim()
            expected = 'retry_wait' if attempt < 3 else 'failed'
            self.assertEqual(self.queue.fail(lease, 'storage_timeout', retryable=True), expected)
        self.assertIsNone(self.queue.claim())

    def test_crash_before_any_stage_is_also_bounded(self):
        job = self.submit()
        for _ in range(3):
            lease = self.queue.claim()
            self.assertIsNotNone(lease)
            self.expire(lease)
        self.assertIsNone(self.queue.claim())
        with Session(self.engine) as session:
            saved = session.get(InspectionJob, job.id)
            self.assertEqual(saved.status, 'failed')
            self.assertEqual(saved.error_code, 'recovery_attempts_exhausted')

    def test_same_worker_cannot_start_a_stage_twice(self):
        self.submit()
        lease = self.queue.claim()
        self.queue.begin_stage(lease, 'download', 'a'*64)
        with self.assertRaises(ValueError):
            self.queue.begin_stage(lease, 'download', 'a'*64)

    def test_viewer_is_idempotent_and_independent_from_completed_inspection(self):
        job = self.submit()
        app = create_app()
        def session():
            with Session(self.engine, expire_on_commit=False) as value:
                yield value
        app.dependency_overrides[get_database_session] = session
        with TestClient(app) as client:
            self.assertEqual(client.post(f'/inspections/{job.id}/viewer').status_code, 409)
            lease = self.queue.claim()
            self.queue.begin_stage(lease, 'comparison', 'a'*64)
            self.queue.complete_stage(lease, 'comparison', 'comparison/manifest.json', 'b'*64)
            self.queue.complete(lease, 'result-manifest.json', 'c'*64, ifc_sha256='d'*64)
            first = client.post(f'/inspections/{job.id}/viewer')
            self.assertEqual(first.status_code, 202, first.text)
            second = client.post(f'/inspections/{job.id}/viewer')
            self.assertEqual(first.json(), second.json())
            export = self.queue.claim(kind='viewer')
            self.assertEqual(str(export.id), first.json()['export_id'])
            self.assertEqual(export.result_manifest_sha256, 'c'*64)
            self.queue.begin_stage(export, 'viewer', 'e'*64)
            self.queue.fail_stage(export, 'viewer', 'invalid_artifact', retryable=False)
            saved = client.get(f'/inspections/{job.id}').json()
            self.assertEqual(saved['status'], 'completed')
            self.assertEqual(saved['viewer_exports'][0]['status'], 'failed')
            self.assertEqual(saved['stages'][0]['status'], 'completed')
            # An ordinary replay returns failure; an explicit retry starts a new
            # bounded budget while preserving unique output attempt directories.
            unchanged = client.post(f'/inspections/{job.id}/viewer').json()
            self.assertEqual(unchanged['status'], 'failed')
            resumed = client.post(f'/inspections/{job.id}/viewer', json={'retry_failed': True})
            self.assertEqual(resumed.status_code, 202, resumed.text)
            self.assertEqual(resumed.json()['export_id'], first.json()['export_id'])
            self.assertEqual(resumed.json()['status'], 'queued')
            export = self.queue.claim(kind='viewer')
            self.assertEqual(len(export.implementation_sha256), 64)
            self.assertEqual(self.queue.begin_stage(export, 'viewer', 'e'*64).attempt, 2)
            self.queue.complete_stage(export, 'viewer', 'viewer/artifacts.json', 'f'*64)
            self.queue.complete(export, 'viewer/artifacts.json', 'f'*64)

    def test_completed_export_returns_browser_url(self):
        job = self.submit()
        lease = self.queue.claim()
        self.queue.complete(lease, 'result.json', 'c'*64)
        with Session(self.engine, expire_on_commit=False) as session:
            export = InspectionService(session).submit_viewer(job.id)
        lease = self.queue.claim(kind='viewer')
        self.queue.begin_stage(lease, 'viewer', 'a'*64)
        self.queue.complete_stage(lease, 'viewer', 'viewer/artifacts.json', 'b'*64)
        self.queue.complete(lease, 'viewer/result.json', 'd'*64)
        from geometry_engine_api.schemas.inspections import ViewerResponse
        with Session(self.engine) as session:
            result = ViewerResponse.model_validate(session.get(ViewerExport, export.id))
        self.assertEqual(result.viewer_url, f'/inspections/{job.id}/viewer?export_id={export.id}')

    def _uncertain_commit(self, operation):
        from geometry_engine_api.services.artifacts import ArtifactStore
        from geometry_engine_api.worker import InspectionWorker

        class Objects:
            def __init__(self): self.data = {}
            def upload(self, key, data, content_type=None):
                if key in self.data:
                    assert self.data[key] == data
                self.data[key] = data
            def download(self, key): return self.data[key]

        class Inputs:
            calls = 0
            def download(self, lease, workspace):
                self.calls += 1
                (workspace/'original').mkdir()
                (workspace/'original'/'capture.json').write_text('{}')
                (workspace/'reference.ifc').write_bytes(b'IFC test fixture')
                for name in ('download-report.json', 'inputs.json'):
                    (workspace/name).write_text('{}')
                return {}

        class Adapter:
            calls = 0
            @staticmethod
            def implementation_fingerprint(): return 'implementation'
            @staticmethod
            def stage_names(): return ('download', 'reconstruction')
            @staticmethod
            def stage_configuration(*args): return {}
            @staticmethod
            def stage_dependencies(name): return () if name == 'download' else ('download',)
            @staticmethod
            def stage_outputs(name): return (name,)
            def run_stage(self, name, workspace, *args, **kwargs):
                self.calls += 1
                (workspace/name).mkdir()
                (workspace/name/'mesh.ply').write_bytes(b'mesh test fixture')

        class UncertainQueue(JobQueue):
            broken = False
            last_lease = None
            def claim(self, *args, **kwargs):
                value = super().claim(*args, **kwargs)
                if value: self.last_lease = value
                return value
            def complete_stage(self, lease, name, *args):
                super().complete_stage(lease, name, *args)
                if operation == 'stage' and name == 'reconstruction' and not self.broken:
                    self.broken = True
                    raise OperationalError('commit acknowledgement lost', {}, Exception())
            def complete(self, *args, **kwargs):
                super().complete(*args, **kwargs)
                if operation == 'job' and not self.broken:
                    self.broken = True
                    raise OperationalError('commit acknowledgement lost', {}, Exception())

        job = self.submit()
        queue, inputs, adapter = UncertainQueue(self.engine), Inputs(), Adapter()
        with TemporaryDirectory(prefix='queue-uncertain-') as temporary:
            worker = InspectionWorker(queue, ArtifactStore(Objects(), chunk_bytes=64), inputs,
                Path(temporary), segmentation_factory=lambda _: None, adapter=adapter)
            self.assertTrue(worker.run_once())
            if operation == 'stage':
                with Session(self.engine) as session:
                    saved = session.get(InspectionJob, job.id)
                    self.assertEqual(saved.status, 'running')
                    self.assertEqual([row.status for row in saved.stages], ['completed', 'completed'])
                self.expire(queue.last_lease)
                self.assertTrue(worker.run_once())
            self.assertFalse(worker.run_once())
        self.assertEqual(inputs.calls, 1)
        self.assertEqual(adapter.calls, 1)
        with Session(self.engine) as session:
            saved = session.get(InspectionJob, job.id)
            self.assertEqual(saved.status, 'completed')
            self.assertEqual([row.attempt for row in saved.stages], [1, 1])

    def test_lost_stage_commit_response_restores_without_recomputing(self):
        self._uncertain_commit('stage')

    def test_lost_job_commit_response_does_not_fail_completed_job(self):
        self._uncertain_commit('job')
