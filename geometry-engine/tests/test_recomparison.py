"""Comparison-only revisions stay successful when a disposable cache fails."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from geometry_engine_api.database.models import InspectionJob, ViewerExport
from geometry_engine_api.services.artifacts import ArtifactRef
from geometry_engine_api.services.recomparison import RecomparisonService


class RecomparisonTests(unittest.TestCase):
    def test_committed_revision_is_returned_if_local_viewer_cache_cannot_be_written(self):
        source_id = uuid4()
        source = SimpleNamespace(session_id='capture', capture_manifest_path='capture/manifest.json',
            ifc_object_key='capture/reference.ifc', ifc_sha256='a' * 64, pipeline_version='mesh-contours-v1',
            parameters={}, status='completed', result_manifest_key='source.json', result_manifest_sha256='b' * 64)
        db = MagicMock()
        db.__enter__.return_value = db
        db.get.return_value = source
        artifacts = MagicMock()
        artifacts.read_json.return_value = {'job_id': str(source_id), 'session_id': 'capture', 'stages': {
            'download': {'key': 'download.json', 'sha256': 'c' * 64, 'fingerprint': 'd' * 64}}}
        artifacts.publish.side_effect = [(ArtifactRef('opening.json', 'e' * 64), {}),
                                         (ArtifactRef('viewer.json', 'f' * 64), {'files': []})]
        artifacts.put_json.return_value = ArtifactRef('result.json', '1' * 64)
        messages = []
        with TemporaryDirectory() as directory, \
                patch('geometry_engine_api.services.recomparison.Session', return_value=db), \
                patch('geometry_engine_api.services.recomparison.restore_saved_stages'), \
                patch('geometry_engine_api.services.recomparison.inspection.run_stage',
                      return_value={'report': {'summary': {'matched_pairs': 2}}}), \
                patch('geometry_engine_api.services.recomparison.inspection.stage_configuration', return_value={}), \
                patch('geometry_engine_api.services.recomparison.inspection.stage_dependencies', return_value=()), \
                patch('geometry_engine_api.services.recomparison.inspection.implementation_fingerprint', return_value={}), \
                patch('geometry_engine_api.services.recomparison.inspection.build_viewer'), \
                patch('geometry_engine_api.services.recomparison.inspection.code_fingerprint', return_value='2' * 64), \
                patch('geometry_engine_api.services.recomparison.seed_viewer_cache', side_effect=OSError('Disk full')):
            result = RecomparisonService(object(), artifacts, Path(directory)).run(source_id, progress=messages.append)
        rows = [call.args[0] for call in db.add.call_args_list]
        job = next(row for row in rows if isinstance(row, InspectionJob))
        viewer = next(row for row in rows if isinstance(row, ViewerExport))
        self.assertEqual(result['job_id'], str(job.id))
        self.assertEqual(result['export_id'], str(viewer.id))
        self.assertEqual(job.status, 'completed')
        self.assertEqual(viewer.status, 'completed')
        self.assertEqual(result['summary']['matched_pairs'], 2)
        self.assertTrue(any('local cache unavailable' in message for message in messages))
