"""Agent report, signed artifact access, cache reuse and failure contracts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from geometry_engine_api.app import create_app
from geometry_engine_api.routers.inspections import get_inspection_service
from geometry_engine_api.routers.results import get_result_service
from geometry_engine_api.services.artifacts import ArtifactError, ArtifactRef, ArtifactStore, ObjectStoreError
from geometry_engine_api.services.inspection_results import InspectionResults, ResultLinkSigner, compact_object
from test_artifacts import MemoryObjects


class InspectionResultTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.objects = MemoryObjects()
        self.artifacts = ArtifactStore(self.objects, cache_dir=self.root/'artifact_cache')
        self.job_id = uuid4()
        self.now = [1000]
        self.signer = ResultLinkSigner('test-secret-for-links-12345', clock=lambda: self.now[0])
        self.results = InspectionResults(self.artifacts, self.root/'data', self.signer)
        self.finding = {'finding_id': 'door-1', 'object_id': 'mesh-door-1', 'ifc_guid': 'ifc-door',
            'category': 'door', 'status': 'candidate_deviation',
            'measurements': {'observed_width_m': .8959636654750832, 'ifc_width_m': .919},
            'deltas_m': {'width': -.023036334524916824}, 'review_required': True,
            'quality': {'reasons': ['incomplete_wall_interface_left']}, 'agent_verdict': 'pending',
            'evidence': [{'frame_id': 'frame-1', 'rgb': 'input/frames/frame-1/rgb.jpg',
                          'metadata': 'input/frames/frame-1/metadata.json',
                          'mask': 'segmentation/masks/frame-1/mask.png'}]}
        opening = {'report.json': {'summary': {'matched_pairs': 1},
                    'agent_review': {'executed': False}, 'limitations': ['Review required']},
                   'findings.json': {'findings': [self.finding]},
                   'reference_objects.json': {'objects': [{'guid': 'ifc-door'}]},
                   'evidence_index.json': [{'finding_id': 'door-1', 'frame_id': 'frame-1',
                     'rgb_thumbnail': 'evidence/rgb.jpg', 'mask_thumbnail': 'evidence/mask.jpg',
                     'rotation_ccw_degrees': 90}]}
        source = self.root/'source'
        for path, data in {**{'opening_comparison/'+k: json.dumps(v).encode() for k,v in opening.items()},
            'opening_comparison/evidence/rgb.jpg': b'annotated photo',
            'opening_comparison/evidence/mask.jpg': b'annotated mask',
            'input/frames/frame-1/rgb.jpg': b'original photo',
            'input/frames/frame-1/metadata.json': b'{}',
            'segmentation/masks/frame-1/mask.png': b'original mask',
            'reconstruction/point_cloud.ply': b'cloud', 'reconstruction/mesh.ply': b'mesh',
            'mesh_segmentation/mesh.ply': b'segmented mesh', 'reference.ifc': b'ifc',
            'scan_vs_bim/report.json': b'{"compared_faces":100,"total_faces":300}'}.items():
            target = source/path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self.stage_refs = {}
        for stage in ('opening_comparison', 'input', 'segmentation', 'reconstruction', 'mesh_segmentation', 'scan_vs_bim', 'download'):
            outputs = ('reference.ifc',) if stage == 'download' else (stage,)
            ref, _ = self.artifacts.publish(source, outputs, f'capture/results/{self.job_id}/{stage}')
            self.stage_refs[stage] = {'key': ref.key, 'sha256': ref.sha256}
        ref = self.artifacts.put_json('capture/results/manifests', {'job_id': str(self.job_id),
            'session_id': 'capture', 'stages': self.stage_refs})
        self.job = SimpleNamespace(id=self.job_id, session_id='capture', status='completed',
            parameters={'include_boundary_candidates': True}, error_code=None,
            result_manifest_key=ref.key, result_manifest_sha256=ref.sha256)
        self.inspections = Mock()
        self.inspections.get.side_effect = lambda identifier: self.job if identifier == self.job.id else None
        self.app = create_app()
        self.app.dependency_overrides[get_inspection_service] = lambda: self.inspections
        self.app.dependency_overrides[get_result_service] = lambda: self.results
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.url = f'/inspections/{self.job.id}/result'

    def test_ready_report_preserves_numbers_and_links_evidence_without_downloading_mesh(self):
        before = deepcopy(self.finding)
        self.objects.downloads.clear()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        obj = data['objects'][0]
        self.assertEqual(obj['dimensions']['width'], {
            'ifc': before['measurements']['ifc_width_m'],
            'scan': before['measurements']['observed_width_m'],
            'difference': before['deltas_m']['width']})
        self.assertEqual(obj['quality']['issues'], before['quality']['reasons'])
        self.assertEqual(obj['match_status'], 'matched')
        self.assertEqual(data['summary']['matched'], 1)
        self.assertEqual(data['schema_version'], '2.0')
        self.assertEqual(set(obj['photos'][0]), {'frame_id', 'rgb_url', 'mask_url', 'overlay_url'})
        self.assertEqual(set(data['artifacts']), {'point_cloud', 'mesh', 'segmented_mesh', 'ifc'})
        for name in ('agent_review', 'agent_verdict', 'review_required', 'metadata', 'input_fingerprints',
                     'predicted_corners_world_m', 'source_instance_ids', 'result_manifest_sha256'):
            self.assertNotIn('"'+name+'"', response.text)
        self.assertIn('point_cloud', data['artifacts'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertFalse(any('/reconstruction/chunks/' in key for key in self.objects.downloads))
        self.assertEqual(self.finding, before)
        # A second report is served from verified local manifests and reports.
        self.objects.downloads.clear()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.objects.downloads, [])

    def test_photo_and_mesh_links_are_real_files_and_share_cached_bundles(self):
        data = self.client.get(self.url).json()
        files = data['objects'][0]['photos'][0]
        photo = self.client.get(files['rgb_url'])
        self.assertEqual(photo.status_code, 200, photo.text)
        self.assertEqual(photo.content, b'original photo')
        self.assertEqual(self.client.get(files['mask_url']).content, b'original mask')
        self.assertEqual(self.client.get(files['overlay_url']).content, b'annotated mask')
        self.assertEqual(self.client.get(data['artifacts']['mesh']).content, b'mesh')
        self.objects.downloads.clear()
        self.assertEqual(self.client.get(data['artifacts']['point_cloud']).content, b'cloud')
        self.assertEqual(self.objects.downloads, [], 'The same mesh bundle must not download twice')

    def test_expiry_tampering_and_cross_job_reuse_are_rejected(self):
        data = self.client.get(self.url, params={'expires_in': 60}).json()
        url = data['objects'][0]['photos'][0]['rgb_url']
        self.assertEqual(self.client.get(url.replace('rgb.jpg', 'metadata.json')).status_code, 403)
        other = deepcopy(self.job)
        other.id = uuid4()
        self.inspections.get.side_effect = lambda identifier: other if identifier == other.id else self.job
        self.assertEqual(self.client.get(url.replace(str(self.job.id), str(other.id))).status_code, 403)
        self.now[0] += 61
        self.assertEqual(self.client.get(url).status_code, 403)
        fresh = self.client.get(self.url, params={'expires_in': 60}).json()
        self.assertEqual(self.client.get(fresh['artifacts']['mesh']).status_code, 200)

    def test_corrupt_local_bundle_and_manifest_are_repaired_before_reuse(self):
        data = self.client.get(self.url).json()
        self.assertEqual(self.client.get(data['artifacts']['mesh']).content, b'mesh')
        stage = self.artifacts.read_json(ArtifactRef(
                self.stage_refs['reconstruction']['key'], self.stage_refs['reconstruction']['sha256']))
        bundle_hash = stage['files'][0]['source']['bundle_sha256']
        (self.root/'artifact_cache/bundles'/bundle_hash).write_bytes(b'broken bundle')
        self.assertEqual(self.client.get(data['artifacts']['point_cloud']).content, b'cloud')
        (self.root/'artifact_cache/manifests'/self.job.result_manifest_sha256).write_bytes(b'broken manifest')
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_readiness_unknown_jobs_and_query_validation(self):
        for state in ('queued', 'running', 'failed', 'cancelled'):
            self.job.status = state
            result = self.client.get(self.url)
            self.assertEqual(result.status_code, 409)
            self.assertEqual(result.json()['detail']['status'], state)
        self.assertEqual(self.client.get('/inspections/'+str(uuid4())+'/result').status_code, 404)
        self.assertEqual(self.client.get('/inspections/bad-uuid/result').status_code, 422)
        for ttl in (0, 59, 86401):
            self.assertEqual(self.client.get(self.url, params={'expires_in': ttl}).status_code, 422)

    def test_integrity_and_storage_errors_are_sanitized(self):
        original = self.results._manifest
        self.results._manifest = Mock(side_effect=ArtifactError('private internal path'))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('private', response.text)
        self.results._manifest = Mock(side_effect=ObjectStoreError('secret URL', retryable=True))
        self.assertEqual(self.client.get(self.url).status_code, 503)
        self.results._manifest = original

    def test_unobserved_ifc_is_not_reported_as_absent_or_zero_deviation(self):
        obj = compact_object({'finding_id': 'ifc-only', 'category': 'window',
                              'ifc_guid': 'window-guid', 'status': 'unobserved_reference'},
                             {'bounds_local_m': [[0, 0, 0], [3.331, 1.513, 0]]}, [])
        self.assertEqual(obj['match_status'], 'ifc_object_not_observed')
        self.assertEqual(obj['dimensions']['width']['ifc'], 3.331)
        self.assertIsNone(obj['dimensions']['width']['scan'])
        self.assertIsNone(obj['dimensions']['width']['difference'])
        self.assertTrue(all(value is None for value in obj['displacement'].values()))
        self.assertIsNone(obj['quality']['boundary_supported_on_all_sides'])

    def test_signed_displacements_are_preserved_without_workflow_fields(self):
        finding = deepcopy(self.finding)
        finding['deltas_m'].update(horizontal_center=-.055, vertical_center=.058, normal_center=.026)
        finding['review_reasons'] = ['ifc_boundary_definition_requires_review',
                                     'other_instances_near_same_reference_may_be_fragments']
        obj = compact_object(finding, None, [])
        self.assertEqual(obj['displacement'], {'along_wall': -.055, 'vertical': .058, 'normal_to_wall': .026})
        self.assertIn('boundary_correspondence_unverified', obj['quality']['issues'])
        self.assertIn('possible_duplicate_fragments', obj['quality']['issues'])
        self.assertNotIn('review_reasons', obj)
        self.assertNotIn('agent_verdict', obj)
