"""Known geometry checks for existing-instance versus IFC comparison."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from geometry_engine.common.mesh import write_mesh
from geometry_engine.comparison.opening_comparison import (
    OpeningComparisonService, OpeningComparisonSettings,
    measure_observation, match_instances, extract_instances,
)

ENGINE = Path(__file__).resolve().parents[1]


def reference(guid='door-a', category='door', origin=(0,0,0)):
    return {'guid':guid, 'category':category, 'name':guid, 'geometry_status':'loaded',
            'origin_m':list(origin), 'u_axis':[0,1,0], 'v_axis':[0,0,1],
            'normal_axis':[-1,0,0], 'bounds_local_m':[[0,0,0],[1,2,.04]]}


def points(ref, offset=(0,0,0), width=1., height=2.):
    axes=np.asarray([ref['u_axis'],ref['v_axis'],ref['normal_axis']])
    local=np.array([[0,0,0],[width,0,.04],[width,height,.04],[0,height,0]])+offset
    return local@axes+ref['origin_m']


def observation(identity, vertices, category='door'):
    return {'instance_id':identity, 'category':category, 'surface_area_m2':1., '_points':vertices}


class OpeningComparisonTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir=ENGINE)
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.settings=OpeningComparisonSettings()

    def test_rotated_reference_horizontal_shift_and_width_are_not_world_aabb(self):
        ref=reference(origin=(10,20,3))
        measured=measure_observation(points(ref,offset=(.3,.1,.02),width=1.2),ref)
        self.assertAlmostEqual(measured['deltas_m']['left'],.3)
        self.assertAlmostEqual(measured['deltas_m']['right'],.5)
        self.assertAlmostEqual(measured['deltas_m']['horizontal_center'],.4)
        self.assertAlmostEqual(measured['deltas_m']['normal_center'],.02)
        self.assertAlmostEqual(measured['deltas_m']['width'],.2)
        self.assertAlmostEqual(measured['observed_height_m'],2.)

    def test_partial_observation_is_measured_without_completing_to_ifc(self):
        ref=reference()
        result=measure_observation(points(ref,width=.6,height=1.5),ref)
        self.assertAlmostEqual(result['observed_width_m'],.6)
        self.assertAlmostEqual(result['deltas_m']['width'],-.4)
        self.assertAlmostEqual(result['deltas_m']['height'],-.5)

    def test_matching_is_one_to_one_and_detects_competing_fragments(self):
        ref=reference()
        observations=[observation(0,points(ref)),observation(1,points(ref,offset=(.05,0,0)))]
        assignments,_=match_instances(observations,[ref],self.settings)
        self.assertEqual(len(assignments),1)
        self.assertTrue(next(iter(assignments.values()))[1])

    def test_spatial_and_type_gates_prevent_invalid_assignments(self):
        ref=reference()
        observations=[observation(0,points(ref),'window'),
                      observation(1,points(ref,offset=(0,0,1.))),
                      observation(2,points(ref,offset=(4,0,0)))]
        assignments,costs=match_instances(observations,[ref],self.settings)
        self.assertEqual(assignments,{})
        self.assertTrue(np.isinf(costs).all())

    def test_empty_inputs_are_supported(self):
        assignments,costs=match_instances([],[],self.settings)
        self.assertEqual(assignments,{})
        self.assertEqual(costs.shape,(0,0))

    def fixture(self):
        root=self.root;seg=root/'mesh_segmentation';seg.mkdir()
        (root/'mesh_preprocessing').mkdir();(root/'input').mkdir();(root/'scan_vs_bim').mkdir()
        vertices=points(reference());faces=np.array([[0,1,2],[0,2,3]],dtype=np.int32)
        mesh=root/'mesh_preprocessing/mesh.ply'
        write_mesh(mesh,vertices,np.full((4,4),255,np.uint8),faces)
        capture=root/'input/capture.json';capture.write_text(json.dumps({'coordinate_system':'arkit','length_unit':'meter'}))
        (seg/'objects.json').write_text(json.dumps({'labels':['door'], 'objects':[
            {'instance_id':7,'label':'door','observation_ids':[]}]}))
        np.savez(seg/'labels.npz',face_class_id=np.array([0,0]),face_instance_id=np.array([7,7]),face_view_count=np.array([3,1]))
        digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
        (seg/'report.json').write_text(json.dumps({'source_mesh_sha256':digest(mesh),'status':'completed'}))
        ifc=root/'test.ifc';ifc.write_text('fixture uses mocked IFC adapter')
        report={'status':'completed','source_mesh_sha256':digest(mesh),'ifc_sha256':digest(ifc),
                'capture_sha256':digest(capture),'registration':{'accepted':True,'transform_scan_to_ifc':np.eye(4).tolist()},
                'semantic_evidence':{'fingerprints':{n:digest(seg/n) for n in ('report.json','objects.json','labels.npz')}}}
        (root/'scan_vs_bim/report.json').write_text(json.dumps(report))
        return ifc,seg,vertices,faces,report

    def test_existing_instance_ids_and_view_gate_are_preserved(self):
        _,seg,vertices,faces,_=self.fixture()
        observations,unassigned=extract_instances(vertices,faces,seg,2)
        self.assertEqual(len(observations),1)
        self.assertEqual(observations[0]['instance_id'],7)
        self.assertEqual(observations[0]['face_count'],1)
        self.assertEqual(unassigned['door'],0)
        np.testing.assert_array_equal(observations[0]['_points'],vertices[:3])

    def test_rejected_registration_blocks_measurements(self):
        ifc,_,_,_,report=self.fixture()
        report['registration']['accepted']=False
        (self.root/'scan_vs_bim/report.json').write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError,'accepted completed'):
            OpeningComparisonService().compare(self.root,ifc)
        self.assertFalse((self.root/'opening_comparison').exists())

    def test_stale_segmentation_blocks_measurements(self):
        ifc,seg,_,_,_=self.fixture()
        np.savez(seg/'labels.npz',face_class_id=np.array([0,0]),face_instance_id=np.array([7,7]),face_view_count=np.array([3,2]))
        with self.assertRaisesRegex(ValueError,'Segmentation changed'):
            OpeningComparisonService().compare(self.root,ifc)

    def test_scaled_registration_is_rejected(self):
        ifc,_,_,_,report=self.fixture()
        report['registration']['transform_scan_to_ifc'][0][0]=1.1
        (self.root/'scan_vs_bim/report.json').write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError,'rigid global'):
            OpeningComparisonService().compare(self.root,ifc)

    def test_valid_alignment_reuses_instances_without_new_segmentation(self):
        ifc,_,_,_,_=self.fixture()
        with patch('geometry_engine.comparison.opening_reference.load_opening_references',return_value=[reference()]), \
             patch.object(OpeningComparisonService,'_evidence'), \
             patch('geometry_engine.visualization.opening_report.write_opening_review',return_value={}):
            report=OpeningComparisonService().compare(self.root,ifc)
        self.assertEqual(report['status'],'completed')
        self.assertEqual(report['summary']['observed_instances'],1)
        findings=json.loads((self.root/'opening_comparison/findings.json').read_text())['findings']
        self.assertEqual(findings[0]['instance_id'],7)
        self.assertTrue(findings[0]['review_required'])
        self.assertEqual(findings[0]['agent_verdict'],'pending')

    def test_changed_projection_fingerprints_cannot_rewrite_photo_provenance(self):
        _,seg,_,_,_=self.fixture()
        projection=self.root/'mask_projection';projection.mkdir()
        (projection/'observations.json').write_text('{}')
        (projection/'report.json').write_text('{"input_fingerprints":{}}')
        (projection/'observations.npz').write_bytes(b'fixture')
        hashes={name:hashlib.sha256((projection/name).read_bytes()).hexdigest()
                for name in ('report.json','observations.json','observations.npz')}
        (seg/'report.json').write_text(json.dumps({'projection':'mask_projection','projection_fingerprints':hashes}))
        (projection/'report.json').write_text('{"input_fingerprints":{"forged":"hash"}}')
        with self.assertRaisesRegex(ValueError,'Projection evidence changed'):
            OpeningComparisonService()._evidence(self.root,seg,[],[],np.eye(4),{})

    def test_fallback_photos_use_projection_camera_and_skip_rejected(self):
        _,seg,_,_,_=self.fixture()
        projection=self.root/'mask_projection';projection.mkdir()
        frames=[];source_hashes={}
        K=np.array([[20,0,20],[0,20,15],[0,0,1.]])
        for fid in ['good','rejected']:
            folder=self.root/'input/frames'/fid;folder.mkdir(parents=True)
            meta={'frame_id':fid,'rgb':{'width':40,'height':30},'intrinsics':{'K':K.tolist()},
                  'pose':{'T_world_from_camera':np.eye(4).tolist()}}
            (folder/'metadata.json').write_text(json.dumps(meta));(folder/'rgb.png').write_bytes(b'photo')
            frame={'frame_id':fid,'metadata':f'input/frames/{fid}/metadata.json','rgb':f'input/frames/{fid}/rgb.png'}
            frames.append(frame)
            for field in ['metadata','rgb']:source_hashes[frame[field]]=hashlib.sha256((self.root/frame[field]).read_bytes()).hexdigest()
        (projection/'observations.json').write_text(json.dumps({'observations':[],'frames':frames}))
        (projection/'observations.npz').write_bytes(b'fixture')
        (projection/'report.json').write_text(json.dumps({'input_fingerprints':source_hashes}))
        fingerprints={n:hashlib.sha256((projection/n).read_bytes()).hexdigest()
            for n in ['report.json','observations.json','observations.npz']}
        (seg/'report.json').write_text(json.dumps({'projection':'mask_projection','projection_fingerprints':fingerprints}))
        ref=reference();ref.update(origin_m=[0,0,2],u_axis=[1,0,0],v_axis=[0,1,0],normal_axis=[0,0,1],bounds_local_m=[[0,0,0],[1,1,.04]])
        finding={'observation_ids':[],'ifc_guid':ref['guid'],'measurements':None,'review_reasons':[]}
        def camera(meta,coordinates,fid):
            if fid=='rejected':raise ValueError('Rejected camera')
            return K,np.eye(4) # CV camera sees +Z; original ARKit pose would look away.
        hashes={}
        with patch('geometry_engine.comparison.opening_comparison.ProjectionCameras') as resolver:
            resolver.return_value.parameters.side_effect=camera
            resolver.return_value.fingerprints={str(seg/'report.json'):'tracked'}
            OpeningComparisonService()._evidence(self.root,seg,[finding],[ref],np.eye(4),hashes)
        self.assertEqual([e['frame_id'] for e in finding['evidence']],['good'])
        self.assertEqual(hashes[seg/'report.json'],'tracked')


if __name__=='__main__':unittest.main()
