import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from geometry_engine.segmentation.mask_projection import file_hash
from geometry_engine.segmentation.projection_camera import ProjectionCameras


class ProjectionCameraTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.tmp.cleanup)
        self.session=Path(self.tmp.name)
        self.metadata={'frame_id':'f','intrinsics':{'K':np.eye(3).tolist()},
            'pose':{'T_world_from_camera':np.eye(4).tolist()}}

    def make_stage(self,status='accepted_refined'):
        s=self.session
        for name in ['chosen_projection','alignment','mesh_segmentation']:(s/name).mkdir()
        (s/'mesh.ply').write_bytes(b'test')
        (s/'alignment/report.json').write_text('{}')
        (s/'alignment/cameras.json').write_text('{}')
        self.T=np.eye(4);self.T[0,3]=.12
        row={'frame_id':'f','status':status}
        if status!='rejected':row['T_world_from_camera_cv']=self.T.tolist()
        self.row=row
        observation={'frame_id':'f','camera_status':status,'T_world_from_camera_cv':self.T.tolist()}
        (s/'chosen_projection/observations.json').write_text(json.dumps({'frames':[observation]}))
        report={'projection_alignment':'alignment','mesh':'mesh.ply','input_fingerprints':{},
            'artifact_fingerprints':{'observations.json':file_hash(s/'chosen_projection/observations.json')}}
        (s/'chosen_projection/report.json').write_text(json.dumps(report))
        (s/'mesh_segmentation/report.json').write_text(json.dumps({'projection':'chosen_projection',
            'projection_fingerprints':{'report.json':file_hash(s/'chosen_projection/report.json')}}))

    def test_legacy_uses_metadata_axes(self):
        _,T=ProjectionCameras(self.session).parameters(self.metadata,'arkit')
        np.testing.assert_array_equal(T,np.diag([1,-1,-1,1]))

    def test_linked_pose_is_absolute_cv_without_double_flip(self):
        self.make_stage()
        with patch('geometry_engine.segmentation.projection_alignment.load_projection_cameras',return_value={'f':self.row}) as loader:
            c=ProjectionCameras(self.session)
            _,T=c.parameters(self.metadata,'arkit')
        np.testing.assert_array_equal(T,self.T)
        self.assertEqual(loader.call_args.args[0],self.session/'alignment')
        self.assertIn(str(self.session/'chosen_projection/report.json'),c.fingerprints)

    def test_rejected_camera_never_falls_back_to_metadata(self):
        self.make_stage('rejected')
        with patch('geometry_engine.segmentation.projection_alignment.load_projection_cameras',return_value={'f':self.row}):
            c=ProjectionCameras(self.session)
        with self.assertRaisesRegex(ValueError,'no accepted projection camera'):
            c.parameters(self.metadata,'arkit')

    def test_changed_projection_cannot_remove_camera_provenance(self):
        self.make_stage()
        (self.session/'chosen_projection/report.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'changed since segmentation'):
            ProjectionCameras(self.session)
