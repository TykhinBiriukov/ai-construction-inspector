"""Automatic boundary integration never substitutes fragment bounds on failure."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
from test_opening_comparison import OpeningComparisonTests, reference
from geometry_engine.comparison.opening_comparison import OpeningComparisonService


class AutomaticOpeningIntegrationTests(unittest.TestCase):
    setUp = OpeningComparisonTests.setUp
    fixture = OpeningComparisonTests.fixture
    def boundary_fixture(self,status='accepted'):
        ifc,seg,vertices,faces,registration=self.fixture()
        folder=self.root/'mesh_boundaries';folder.mkdir()
        from geometry_engine.comparison.scan_vs_bim import _hash
        data={'status':'completed','coordinate_system':'arkit','unit':'meter','input_fingerprints':{
            str(self.root/'mesh_preprocessing/mesh.ply'):_hash(self.root/'mesh_preprocessing/mesh.ply'),
            str(seg/'labels.npz'):_hash(seg/'labels.npz')},'objects':[{
                'object_id':'door-merged','category':'door','measurement_target':'door_opening',
                'source_instance_ids':[7],'observation_ids':[],'status':status,
                'corners_world_m':vertices.tolist(),'quality':{'reason':'test'}}]}
        (folder/'objects.json').write_text(json.dumps(data))
        (folder/'report.json').write_text(json.dumps({k:v for k,v in data.items() if k!='objects'}))
        return ifc,folder

    def compare_boundaries(self,ifc,folder):
        with patch('geometry_engine.comparison.opening_reference.load_boundary_references',return_value=[reference()]), \
             patch.object(OpeningComparisonService,'_evidence'), \
             patch('geometry_engine.visualization.opening_report.write_opening_review',return_value={}):
            return OpeningComparisonService().compare(self.root,ifc,boundaries_dir=folder)

    def test_rejected_contour_is_not_replaced_with_segmented_fragment(self):
        ifc,folder=self.boundary_fixture('needs_review')
        result=self.compare_boundaries(ifc,folder)
        self.assertEqual(result['summary']['observed_instances'],0)
        self.assertEqual(len(result['unverified_boundaries']),1)
        findings=json.loads((self.root/'opening_comparison/findings.json').read_text())['findings']
        self.assertTrue(all(x['measurements'] is None for x in findings))

    def test_accepted_contour_skips_manual_window_annotations(self):
        ifc,folder=self.boundary_fixture()
        manual=self.root/'window_measurement';manual.mkdir()
        (manual/'objects.json').write_text('invalid manual annotations are deliberately ignored')
        result=self.compare_boundaries(ifc,folder)
        self.assertEqual(result['summary']['matched_pairs'],1)
        findings=json.loads((self.root/'opening_comparison/findings.json').read_text())['findings']
        self.assertEqual(findings[0]['finding_id'],'door-merged')
        self.assertEqual(findings[0]['measurement_target'],'door_opening')

    def test_failed_resume_cannot_reuse_previous_completed_objects(self):
        ifc,folder=self.boundary_fixture()
        (folder/'report.json').write_text(json.dumps({'status':'failed'}))
        with self.assertRaisesRegex(ValueError,'did not complete'):
            self.compare_boundaries(ifc,folder)

    def test_running_boundary_stage_cannot_be_compared(self):
        ifc,folder=self.boundary_fixture()
        (folder/'.running').write_text('')
        with self.assertRaisesRegex(ValueError,'still running'):
            self.compare_boundaries(ifc,folder)

    def test_stale_boundary_input_rejected(self):
        ifc,folder=self.boundary_fixture()
        path=folder/'objects.json';data=json.loads(path.read_text())
        data['input_fingerprints'][str(self.root/'mesh_preprocessing/mesh.ply')]='bad'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'Boundary objects differ|Boundary input changed'):
            self.compare_boundaries(ifc,folder)





class BoundaryReferenceTests(unittest.TestCase):
    def test_door_uses_structural_opening_in_own_axes_not_leaf(self):
        import tempfile
        from test_opening_reference import make_door_reference
        from geometry_engine.comparison.opening_reference import load_boundary_references
        import ifcopenshell
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as tmp:
            path=Path(tmp)/'door.ifc'
            _,guid,_=make_door_reference(path,millimeters=True)
            model=ifcopenshell.open(str(path))
            door=model.by_guid(guid)
            # Give leaf a different local placement while keeping opening unchanged.
            parent=door.ObjectPlacement
            pos=model.create_entity('IfcAxis2Placement3D',model.create_entity('IfcCartesianPoint',(0.,0.,0.)),None,
                                    model.create_entity('IfcDirection',(0.,1.,0.)))
            door.ObjectPlacement=model.create_entity('IfcLocalPlacement',parent,pos)
            model.write(str(path))
            row=next(r for r in load_boundary_references(path) if r['guid']==guid)
            self.assertEqual(row['geometry_status'],'loaded')
            b=np.asarray(row['bounds_local_m'])
            self.assertAlmostEqual(b[1,0]-b[0,0],1.2)
            self.assertAlmostEqual(b[1,1]-b[0,1],2.2)
            self.assertAlmostEqual(b[0,2],.2)


class AutomaticPipelineTests(unittest.TestCase):
    def test_opt_in_preserves_raw_geometry_and_adds_boundary_stage(self):
        import tempfile
        from contextlib import ExitStack
        from unittest.mock import Mock
        from geometry_engine.pipeline import PipelineService
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as tmp:
            directory=Path(tmp);(directory/'original').mkdir()
            sam=Mock(); boundary=Mock(); boundary.run.return_value={'status':'completed'}
            factory=Mock(return_value=boundary)
            with ExitStack() as stack:
                services={}
                for name,method in [('ReconstructionService','reconstruct'),
                                    ('SessionValidationService','validate'),('FrameFilterService','filter'),
                                    ('MeshSegmentationService','segment'),
                                    ('ProjectionAlignmentService','align')]:
                    services[name]=stack.enter_context(patch('geometry_engine.pipeline.'+name)).return_value
                    getattr(services[name],method).return_value={'status':'valid'}
                stack.enter_context(patch('geometry_engine.pipeline.prepare_session',return_value=directory))
                stack.enter_context(patch('geometry_engine.pipeline.prepare_input'))
                stack.enter_context(patch('geometry_engine.pipeline.preprocess_mesh'))
                report=PipelineService(sam,boundary_factory=factory).run(directory,measure_openings=True)
                services['ReconstructionService'].reconstruct.assert_called_once_with(directory)
                services['MeshSegmentationService'].segment.assert_called_once_with(directory,alignment_dir=directory/'projection_alignment')
                boundary.run.assert_called_once_with(directory)
                self.assertEqual(report['completed_stages'][-1],'mesh_boundaries')
                self.assertNotIn('wall_planes',report['completed_stages'])


class AutomaticOpeningApiTests(unittest.TestCase):
    def test_existing_endpoint_builds_or_reuses_contours_only_when_requested(self):
        from test_opening_api import OpeningApiTests
        from unittest.mock import Mock
        from geometry_engine_api.dependencies import get_opening_boundary_factory
        fixture=OpeningApiTests('test_no_new_endpoint_is_added')
        fixture.setUp()
        try:
            stage=Mock(); factory=Mock(return_value=stage)
            fixture.app.dependency_overrides[get_opening_boundary_factory]=lambda:factory
            response=fixture.post(compare_openings=True,measure_openings=True,resume_boundaries=True)
            self.assertEqual(response.status_code,200,response.text)
            stage.run.assert_called_once_with(fixture.session.resolve(),resume=True)
            self.assertEqual(fixture.opening_service.compare.call_args.kwargs['boundaries_dir'],fixture.session.resolve()/'mesh_boundaries')
            stage.reset_mock();factory.reset_mock()
            response=fixture.post(compare_openings=True,use_opening_boundaries=True)
            self.assertEqual(response.status_code,200,response.text)
            factory.assert_not_called()
            response=fixture.post(measure_openings=True)
            self.assertEqual(response.status_code,422)
        finally:
            fixture.doCleanups()


if __name__=='__main__':unittest.main()
