import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from geometry_engine.pipeline import PipelineService, prepare_input
from geometry_engine_api.app import create_app
from geometry_engine_api.dependencies import get_pipeline_service, get_settings
from geometry_engine_api.services.archive import Settings


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.session = self.data / 'processed' / 'scan'
        (self.data / 'raw/scan/original').mkdir(parents=True)

    def test_pipeline_order_failure_report_and_repeat_protection(self):
        from contextlib import ExitStack
        calls = []
        with ExitStack() as stack:
            for name in ('ReconstructionService', 'SessionValidationService', 'FrameFilterService',
                         'MeshSegmentationService', 'WallPlaneService'):
                service = stack.enter_context(patch('geometry_engine.pipeline.' + name)).return_value
                method = {'ReconstructionService': 'reconstruct', 'SessionValidationService': 'validate',
                          'FrameFilterService': 'filter', 'MeshSegmentationService': 'segment',
                          'WallPlaneService': 'detect'}[name]
                getattr(service, method).side_effect = lambda *args, n=name: (calls.append(n) or {'status': 'valid'})
            stack.enter_context(patch('geometry_engine.pipeline.prepare_input', side_effect=lambda *a: calls.append('input')))
            stack.enter_context(patch('geometry_engine.pipeline.preprocess_mesh', side_effect=lambda *a: calls.append('mesh')))
            sam = Mock()
            sam.segment.side_effect = lambda *a: calls.append('sam')
            report = PipelineService(sam).run(self.session)
            self.assertEqual(report['status'], 'completed')
            self.assertEqual(calls, ['ReconstructionService', 'input', 'SessionValidationService', 'mesh',
                                     'FrameFilterService', 'sam', 'MeshSegmentationService', 'WallPlaneService'])
            with self.assertRaises(FileExistsError):
                PipelineService(sam).run(self.session)
            self.assertEqual(sam.segment.call_count, 1)

    def test_failure_keeps_stage_and_does_not_call_sam(self):
        sam = Mock()
        with patch('geometry_engine.pipeline.ReconstructionService') as reconstruction:
            reconstruction.return_value.reconstruct.side_effect = ValueError('bad depth')
            with self.assertRaises(ValueError):
                PipelineService(sam).run(self.session)
        report = json.loads((self.session / 'pipeline.json').read_text())
        self.assertEqual((report['status'], report['current_stage']), ('failed', 'reconstruction'))
        sam.segment.assert_not_called()

    def test_two_endpoints_and_error_mapping(self):
        app = create_app()
        service = Mock()
        service.run.return_value = {'status': 'completed'}
        app.dependency_overrides[get_pipeline_service] = lambda: service
        app.dependency_overrides[get_settings] = lambda: Settings(data_dir=self.data)
        self.assertEqual(set(app.openapi()['paths']), {'/sessions/extract', '/sessions/process'})
        with TestClient(app) as client:
            response = client.post('/sessions/process', json={'session_id': 'scan'})
            self.assertEqual(response.status_code, 200, response.text)
            service.run.assert_called_once_with(self.session.resolve())
            for error, status in ((FileExistsError(), 409), (ValueError('invalid'), 422), (OSError('secret'), 500)):
                service.run.side_effect = error
                response = client.post('/sessions/process', json={'session_id': 'scan'})
                self.assertEqual(response.status_code, status)
                self.assertNotIn('secret', response.text)
            self.assertEqual(client.post('/sessions/process', json={'session_id': '../escape'}).status_code, 422)

    def test_adapter_preserves_raw_and_binds_reconstructed_mesh(self):
        from tests.test_raw_capture import RawCaptureTests
        from geometry_engine.validation import SessionValidationService
        fixture = RawCaptureTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        session = fixture.session
        (session / 'reconstruction').mkdir()
        (session / 'reconstruction/report.json').write_text(json.dumps({
            'frames': [{'frame_id': 'frame-a', 'status': 'integrated'}]}))
        import numpy as np
        from geometry_engine.mesh import write_mesh
        mesh_path = session / 'reconstruction/mesh.ply'
        write_mesh(mesh_path, np.array([[0,0,0],[1,0,0],[0,1,0]], dtype=float),
                   np.full((3,4), 255, dtype=np.uint8), np.array([[0,1,2]]))
        mesh = mesh_path.read_bytes()
        before = (fixture.base / 'metadata.json').read_bytes()
        prepare_input(session)
        self.assertEqual(before, (fixture.base / 'metadata.json').read_bytes())
        report = SessionValidationService().validate(session)
        self.assertEqual(report['status'], 'valid', report)
        self.assertEqual((session / 'input/mesh.ply').read_bytes(), mesh)
