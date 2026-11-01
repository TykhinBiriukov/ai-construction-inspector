"""Opt-in PostgreSQL tests use an isolated disposable schema, not application rows.

Set TEST_DATABASE_URL to a local PostgreSQL connection string to enable them.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import re
import threading
import unittest
from uuid import UUID, uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from geometry_engine_api.app import create_app
from geometry_engine_api.database.connection import get_database_session
from geometry_engine_api.database.migrate import upgrade_database
from geometry_engine_api.database.models import Base, InspectionJob


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Set TEST_DATABASE_URL for PostgreSQL integration tests')
class InspectionPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = 'inspection_test_' + uuid4().hex
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
        # Only drop the newly generated test schema, never the public schema.
        assert re.fullmatch(r'inspection_test_[0-9a-f]{32}', cls.schema)
        with cls.admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.admin.dispose()

    def setUp(self):
        self.key = str(uuid4())
        self.body = {'session_id': 'scan-001', 'capture_manifest_path': 'captures/scan-001/manifest.json',
                     'ifc_object_key': 'references/room.ifc'}
        self.app = self.make_app()

    def make_app(self):
        app = create_app()
        def session():
            with Session(self.engine, expire_on_commit=False) as value: yield value
        app.dependency_overrides[get_database_session] = session
        return app

    def post(self, body=None, key=None):
        with TestClient(self.app) as client:
            return client.post('/inspections', json=body or self.body,
                               headers={'Idempotency-Key': key or self.key})

    def count(self):
        with Session(self.engine) as session:
            return session.scalar(select(func.count()).select_from(InspectionJob).where(
                InspectionJob.idempotency_key == self.key))

    def test_submission_is_committed_and_survives_app_recreation(self):
        response = self.post()
        self.assertEqual(response.status_code, 202, response.text)
        result = response.json()
        self.assertEqual(result['status'], 'queued')
        self.assertIsNone(result['current_stage'])
        self.assertIsNone(result['started_at'])
        self.assertIsNone(result['ifc_sha256'])
        self.assertEqual(response.headers['Location'], result['status_url'])
        self.assertNotIn('idempotency_key', result)
        self.assertNotIn('request_hash', result)
        self.assertEqual(self.count(), 1)
        with TestClient(self.make_app()) as client:
            saved = client.get(result['status_url'])
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), result)

    def test_replay_returns_same_job_and_normalizes_defaults(self):
        first = self.post()
        body = dict(self.body, parameters={'interior_point_ifc_m': None,
                                         'include_boundary_candidates': True, 'tolerance_m': .10})
        second = self.post(body)
        self.assertEqual(second.status_code, 202, second.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(self.count(), 1)

    def test_conflicting_key_does_not_modify_original_job(self):
        first = self.post().json()
        changed = dict(self.body, ifc_object_key='references/different.ifc')
        self.assertEqual(self.post(changed).status_code, 409)
        self.assertEqual(self.count(), 1)
        with TestClient(self.app) as client: saved = client.get(first['status_url']).json()
        self.assertEqual(first, saved)

    def test_same_capture_with_new_key_can_create_a_new_run(self):
        first = self.post().json()
        second = self.post(key=str(uuid4())).json()
        self.assertNotEqual(first['job_id'], second['job_id'])

    def test_concurrent_replays_create_exactly_one_job(self):
        barrier = threading.Barrier(8)
        def send(_):
            barrier.wait(timeout=10)
            return self.post()
        with ThreadPoolExecutor(max_workers=8) as pool: results = list(pool.map(send, range(8)))
        self.assertEqual([r.status_code for r in results], [202]*8)
        self.assertEqual(len({r.json()['job_id'] for r in results}), 1)
        self.assertEqual(self.count(), 1)

    def test_concurrent_different_requests_share_no_key(self):
        barrier = threading.Barrier(2)
        def send(body):
            barrier.wait(timeout=10)
            return self.post(body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, [self.body, dict(self.body, session_id='other-scan')]))
        self.assertEqual(sorted(r.status_code for r in results), [202, 409])
        self.assertEqual(self.count(), 1)

    def test_get_returns_persisted_state_not_a_fixed_queued_response(self):
        result = self.post().json()
        with Session(self.engine) as session, session.begin():
            job = session.get(InspectionJob, UUID(result['job_id']))
            job.status = 'failed'; job.error_code = 'invalid_capture'; job.current_stage = 'validation'
        with TestClient(self.app) as client: saved = client.get(result['status_url']).json()
        self.assertEqual(saved['status'], 'failed')
        self.assertEqual(saved['error_code'], 'invalid_capture')
        replay = self.post().json()
        self.assertEqual(replay['status'], 'failed')
        self.assertEqual(replay['job_id'], result['job_id'])

    def test_migrations_are_repeatable_and_match_models(self):
        with self.engine.connect() as connection:
            upgrade_database(connection)
            context = MigrationContext.configure(connection)
            self.assertEqual(context.get_current_revision(), '0002_durable_pipeline')
            differences = compare_metadata(context, Base.metadata)
            self.assertEqual(differences, [])
