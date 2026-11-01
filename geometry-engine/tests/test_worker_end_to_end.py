"""Full durable control flow on isolated PostgreSQL, deterministic tiny stages.

Geometry algorithms have separate real geometry tests; Meta and Storage are
substituted here so failure recovery never calls paid inference or public jobs.
"""
from collections import Counter
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from geometry_engine import inspection
from geometry_engine.segmentation.cache import CachedSamBackend
from geometry_engine.segmentation.sam_backend import Detection
from geometry_engine.segmentation.service import SegmentationService
from geometry_engine_api.app import create_app
from geometry_engine_api.database.connection import get_database_session
from geometry_engine_api.database.migrate import upgrade_database
from geometry_engine_api.database.models import InspectionJob
from geometry_engine_api.routers.viewers import get_viewer_files
from geometry_engine_api.schemas.inspections import InspectionRequest
from geometry_engine_api.services.artifacts import ArtifactStore
from geometry_engine_api.services.inspections import InspectionService
from geometry_engine_api.services.job_queue import JobQueue
from geometry_engine_api.services.segmentation_cache import SegmentationCacheRepository
from geometry_engine_api.services.viewer_files import ViewerFiles
from geometry_engine_api.worker import InspectionWorker
from test_artifacts import MemoryObjects


class TinyInputs:
    def __init__(self):
        self.calls = 0

    def download(self, lease, workspace):
        self.calls += 1
        (workspace/'original').mkdir()
        Image.new('RGB', (8, 6), '#cc4411').save(workspace/'original/photo.png')
        (workspace/'reference.ifc').write_text('test IFC')
        (workspace/'download-report.json').write_text('{}')
        (workspace/'inputs.json').write_text('{}')
        return {}


class TinySam:
    model = 'test-sam'
    model_info = {'name': model}
    def __init__(self):
        self.calls = 0

    def predict(self, image, prompts):
        self.calls += 1
        return [Detection(p, .9, np.ones((image.height, image.width), dtype=bool)) for p in prompts]


class TinyStages:
    VIEWER_VERSION = inspection.VIEWER_VERSION
    stage_names = staticmethod(inspection.stage_names)
    stage_dependencies = staticmethod(inspection.stage_dependencies)
    stage_outputs = staticmethod(inspection.stage_outputs)
    implementation_fingerprint = staticmethod(inspection.implementation_fingerprint)
    code_fingerprint = staticmethod(inspection.code_fingerprint)
    stage_configuration = staticmethod(inspection.stage_configuration)

    def __init__(self, fail_once=None):
        self.calls = Counter()
        self.fail_once = fail_once

    def run_stage(self, name, workspace, parameters, segmentation, progress=None):
        assert workspace.name == 'capture', 'Session identity must survive private worker workspace paths'
        self.calls[name] += 1
        if name == self.fail_once and self.calls[name] == 1:
            raise TimeoutError('simulated temporary upstream failure')
        for output in self.stage_outputs(name):
            (workspace/output).mkdir()
            (workspace/output/'report.json').write_text(json.dumps({'status': 'completed', 'session_id': workspace.name}))
        if name == 'input':
            shutil.copyfile(workspace/'original/photo.png', workspace/'input/photo.png')
        if name == 'segmentation':
            with Image.open(workspace/'original/photo.png') as image:
                segmentation.backend.prepare(iter([image]), ('wall', 'window'))
                result = segmentation.backend.predict(image, ('wall', 'window'))
            (workspace/'segmentation/masks.json').write_text(json.dumps({'count': len(result)}))
        if name == 'reconstruction':
            for filename in ('point_cloud.ply', 'mesh.ply'):
                (workspace/name/filename).write_bytes(b'tiny geometry')
        if name == 'mesh_segmentation':
            (workspace/name/'mesh.ply').write_bytes(b'tiny segmentation')

    def build_viewer(self, workspace):
        self.calls['viewer'] += 1
        (workspace/'viewer/review').mkdir(parents=True)
        (workspace/'viewer/viewer.html').write_text('<!doctype html><a href="review/photo.jpg">Photo</a>')
        (workspace/'viewer/review/photo.jpg').write_bytes(b'test-jpg')
        (workspace/'viewer/report.json').write_text('{"unchanged_measurements":true}')


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Set TEST_DATABASE_URL for PostgreSQL integration tests')
class WorkerEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = 'worker_e2e_' + uuid4().hex
        cls.admin = create_engine(os.environ['TEST_DATABASE_URL'])
        with cls.admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(os.environ['TEST_DATABASE_URL'], connect_args={
            'options': f'-c search_path={cls.schema} -c timezone=UTC'})
        with cls.engine.connect() as connection:
            upgrade_database(connection)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        assert re.fullmatch(r'worker_e2e_[0-9a-f]{32}', cls.schema)
        with cls.admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.admin.dispose()

    def submit(self):
        with Session(self.engine, expire_on_commit=False) as session:
            return InspectionService(session).submit(InspectionRequest(
                session_id='capture', capture_manifest_path='capture/manifest.json',
                ifc_object_key='capture/reference.ifc'), str(uuid4()))

    def test_resume_batch_sam_cache_viewer_and_http_assets(self):
        objects, inputs, sam = MemoryObjects(), TinyInputs(), TinySam()
        artifacts = ArtifactStore(objects, chunk_bytes=128)
        adapter = TinyStages(fail_once='projection_alignment')
        repository = SegmentationCacheRepository(self.engine)
        queue = JobQueue(self.engine, retry_base_seconds=0)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = InspectionWorker(queue, artifacts, inputs, root/'work', adapter=adapter,
                segmentation_factory=lambda workspace: SegmentationService(CachedSamBackend(
                    sam, repository, objects, workspace/'.cache')))
            job = self.submit()
            self.assertTrue(worker.run_once())
            with Session(self.engine) as session:
                failed = session.get(InspectionJob, job.id)
                self.assertEqual(failed.status, 'queued')
                self.assertEqual(failed.current_stage, 'projection_alignment')
                rejected = next(s for s in failed.stages if s.name == 'projection_alignment')
                self.assertIsNotNone(rejected.artifact_manifest_key)
            self.assertEqual(sam.calls, 2)
            self.assertTrue(worker.run_once())
            with Session(self.engine) as session:
                done = session.get(InspectionJob, job.id)
                self.assertEqual(done.status, 'completed', done.error_code)
                self.assertEqual(len(done.stages), len(adapter.stage_names()))
                self.assertTrue(all(s.status == 'completed' for s in done.stages))
            self.assertEqual(inputs.calls, 1)
            self.assertEqual(adapter.calls['segmentation'], 1)
            self.assertEqual(adapter.calls['reconstruction'], 1)
            # A new independent job reuses SAM inference, but executes its pipeline.
            second = self.submit()
            self.assertTrue(worker.run_once())
            self.assertEqual(sam.calls, 2)
            self.assertEqual(adapter.calls['segmentation'], 2)
            self.assertEqual(inputs.calls, 2)

            app = create_app()
            def database_session():
                with Session(self.engine, expire_on_commit=False) as session:
                    yield session
            app.dependency_overrides[get_database_session] = database_session
            app.dependency_overrides[get_viewer_files] = lambda: ViewerFiles(artifacts, root/'viewer_cache')
            with TestClient(app) as client:
                response = client.post(f'/inspections/{job.id}/viewer')
                self.assertEqual(response.status_code, 202, response.text)
                export_id = response.json()['export_id']
                self.assertTrue(worker.run_once())
                self.assertEqual(sam.calls, 2)
                self.assertEqual(adapter.calls['reconstruction'], 2)
                status = client.get(f'/inspections/{job.id}').json()
                export = next(e for e in status['viewer_exports'] if e['export_id'] == export_id)
                self.assertEqual(export['status'], 'completed', export)
                page = client.get(export['viewer_url'])
                self.assertEqual(page.status_code, 200, page.text)
                self.assertIn('text/html', page.headers['content-type'])
                self.assertIn('review/photo.jpg', page.text)
                photo = client.get(f'/inspections/{job.id}/viewer/assets/{export_id}/review/photo.jpg')
                self.assertEqual(photo.content, b'test-jpg')
                cross_job = client.get(f'/inspections/{second.id}/viewer?export_id={export_id}')
                self.assertEqual(cross_job.status_code, 404)
                replay = client.post(f'/inspections/{job.id}/viewer').json()
                self.assertEqual(replay['export_id'], export_id)
                self.assertEqual(replay['status'], 'completed')
            self.assertFalse(worker.run_once())
