"""Worker stage contracts, portable artifacts and saved-measurement viewer export."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from geometry_engine.inspection import (
    STAGES, StageRejected, build_viewer, code_fingerprint, implementation_fingerprint,
    run_stage, stage_configuration,
    stage_dependencies, stage_outputs,
)
from geometry_engine.common.artifact_paths import resolve_artifact_reference


class InspectionStageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def test_dependencies_form_ordered_graph_and_preserve_projection_artifacts(self):
        done = set()
        for name in STAGES:
            self.assertLessEqual(set(stage_dependencies(name)), done)
            done.add(name)
        self.assertEqual(stage_outputs('mesh_segmentation'), ('mask_projection', 'mesh_segmentation'))
        self.assertLessEqual(set(stage_dependencies('viewer')), done)

    def test_tolerance_and_interior_seed_are_explicit_and_valid_at_api_limit(self):
        seed = [3., 2., 1.]
        settings = stage_configuration('scan_vs_bim', {'tolerance_m': 1., 'interior_point_ifc_m': seed})['settings']
        self.assertGreater(settings['max_match_distance_m'], settings['tolerance_m'])
        self.assertEqual(settings['interior_point'], seed)
        self.assertTrue(settings['segmented_only'])
        self.assertTrue(stage_configuration('opening_comparison')['settings']['include_boundary_candidates'])
        self.assertFalse(stage_configuration('opening_comparison', {
            'include_boundary_candidates': False})['settings']['include_boundary_candidates'])

    def test_api_and_worker_share_viewer_code_hash_with_different_dependencies(self):
        from importlib.metadata import PackageNotFoundError
        with patch('geometry_engine.inspection.version', side_effect=PackageNotFoundError):
            api_code, api_runtime = code_fingerprint(), implementation_fingerprint()
        with patch('geometry_engine.inspection.version', return_value='installed-worker-version'):
            worker_code, worker_runtime = code_fingerprint(), implementation_fingerprint()
        self.assertEqual(api_code, worker_code)
        self.assertNotEqual(api_runtime, worker_runtime)

    def test_raw_geometry_and_projection_only_camera_policy(self):
        def completed(*args, **kwargs):
            (self.root/'mask_projection').mkdir()
            (self.root/'mesh_segmentation').mkdir()
            return {'status': 'completed'}
        with patch('geometry_engine.inspection.MeshSegmentationService') as cls:
            cls.return_value.segment.side_effect = completed
            result = run_stage('mesh_segmentation', self.root)
        self.assertEqual(cls.call_args.kwargs['projection_settings'].min_comparable_depth_agreement, .6)
        self.assertEqual(cls.return_value.segment.call_args.kwargs['alignment_dir'], self.root/'projection_alignment')
        self.assertEqual(result['outputs'], ['mask_projection', 'mesh_segmentation'])

    def test_existing_outputs_are_not_silently_overwritten(self):
        (self.root/'reconstruction').mkdir()
        with patch('geometry_engine.inspection.ReconstructionService') as cls:
            with self.assertRaises(FileExistsError):
                run_stage('reconstruction', self.root)
            cls.assert_not_called()

    def test_invalid_validation_stops_downstream_work(self):
        with patch('geometry_engine.inspection.SessionValidationService') as cls:
            cls.return_value.validate.return_value = {'status': 'invalid', 'errors': ['bad pose']}
            with self.assertRaises(StageRejected) as error:
                run_stage('validation', self.root)
        self.assertEqual(error.exception.code, 'invalid_capture')
        self.assertFalse(error.exception.retryable)

    def test_rejected_registration_cannot_complete_inspection(self):
        with patch('geometry_engine.inspection.ScanVsBimService') as cls:
            cls.return_value.compare.return_value = {'status': 'registration_rejected', 'registration': {'accepted': False}}
            with self.assertRaises(StageRejected) as error:
                run_stage('scan_vs_bim', self.root)
        self.assertEqual(error.exception.code, 'registration_rejected')

    def test_reference_cannot_escape_workspace(self):
        with self.assertRaises(ValueError):
            resolve_artifact_reference('../secret', self.root)
        with self.assertRaises(ValueError):
            resolve_artifact_reference(str(self.root.parent/'secret'), self.root)

    def test_contours_and_viewer_survive_workspace_move_without_measurement_recompute(self):
        self._check_saved_comparison(gap=False)

    def test_incomplete_wall_interface_still_has_dimensions_and_viewer_by_default(self):
        self._check_saved_comparison(gap=True)

    def _check_saved_comparison(self, gap):
        from test_mesh_boundaries import MeshContourTests
        from test_bim_reference import make_reference
        from geometry_engine.openings.mesh_boundaries import MeshBoundaryService
        from geometry_engine.comparison.opening_comparison import OpeningComparisonService, load_boundary_observations
        from geometry_engine.segmentation.mask_projection import file_hash

        fixture = MeshContourTests('test_complete_interface_recovers_analytic_rectangle_without_mutating_mesh')
        self.addCleanup(fixture.doCleanups)
        source, mesh = fixture.fixture(gap=gap)
        source = source.resolve()
        segmentation = source/'mesh_segmentation'
        with np.load(segmentation/'labels.npz') as arrays:
            labels = {key: arrays[key].copy() for key in arrays.files}
        labels['face_view_count'] = np.full(len(labels['face_class_id']), 2)
        np.savez_compressed(segmentation/'labels.npz', **labels)
        capture = source/'input/capture.json'
        capture.write_text(json.dumps({'coordinate_system': 'arkit', 'length_unit': 'meter'}))
        shutil.copyfile(mesh, source/'input/mesh.ply')
        ifc = source/'reference.ifc'
        guid = make_reference(ifc)
        (source/'scan_vs_bim').mkdir()
        registration = {'status': 'completed', 'source_mesh_sha256': file_hash(mesh),
                        'ifc_sha256': file_hash(ifc), 'capture_sha256': file_hash(capture),
                        'registration': {'accepted': True, 'transform_scan_to_ifc': np.eye(4).tolist()},
                        'semantic_evidence': {'fingerprints': {name: file_hash(segmentation/name)
                                              for name in ('report.json', 'objects.json', 'labels.npz')}}}
        (source/'scan_vs_bim/report.json').write_text(json.dumps(registration))
        contour = MeshBoundaryService().run(source)
        self.assertTrue(all(not Path(key).is_absolute() for key in contour['input_fingerprints']))
        reference = {'guid': guid, 'category': 'window', 'name': 'Analytic test reference',
                     'geometry_status': 'loaded', 'origin_m': [0, 0, 0], 'u_axis': [1, 0, 0],
                     'v_axis': [0, 1, 0], 'normal_axis': [0, 0, 1],
                     'bounds_local_m': [[-.5, 1, 0], [.5, 3, 0]]}
        with patch('geometry_engine.comparison.opening_reference.load_boundary_references', return_value=[reference]), \
             patch.object(OpeningComparisonService, '_evidence'):
            result = run_stage('opening_comparison', source)['report']
        self.assertEqual(result['summary']['matched_pairs'], 1)
        self.assertTrue(all(not Path(key).is_absolute() for key in result['input_fingerprints']))
        self.assertEqual(result['boundary_stage'], 'mesh_boundaries')
        destination = self.root/'restored'
        shutil.copytree(source, destination)
        before = {path.relative_to(destination): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in destination.rglob('*') if path.is_file()}
        # Old source is now corrupt: restored reports must never read from it.
        mesh.write_bytes(b'old worker disk is unavailable')
        observations, _ = load_boundary_observations(destination, destination/'mesh_boundaries', np.eye(4), {},
                                                     include_candidates=gap)
        np.testing.assert_allclose(observations[0]['_intrinsic_dimensions'], [1, 2], atol=1e-5)
        with patch.object(OpeningComparisonService, 'compare', side_effect=AssertionError('must not remeasure')):
            html = build_viewer(destination)
        self.assertTrue(html.is_file())
        self.assertIn('review/review.html', html.read_text(encoding='utf-8'))
        self.assertTrue((destination/'viewer/review/review.html').is_file())
        for path, digest in before.items():
            self.assertEqual(hashlib.sha256((destination/path).read_bytes()).hexdigest(), digest)
        measurements = json.loads((destination/'viewer/report.json').read_text())['findings'][0]['measurements']
        self.assertAlmostEqual(measurements['observed_width_m'], 1, places=5)
        self.assertAlmostEqual(measurements['observed_height_m'], 2, places=5)
        if gap:
            finding = json.loads((destination/'viewer/report.json').read_text())['findings'][0]
            self.assertEqual(finding['measurement_status'], 'mesh_contour_candidate')
            self.assertIsNotNone(finding['deltas_m'])
            self.assertIn('incomplete_wall_interface_top', finding['quality']['reasons'])


if __name__ == '__main__':
    unittest.main()
