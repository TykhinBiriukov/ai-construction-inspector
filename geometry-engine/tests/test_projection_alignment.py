import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np

from geometry_engine.segmentation.projection_alignment import (
    ProjectionAlignmentSettings, _partition, _observable, _rays, _render,
    align_frame, choose_camera, depth_metrics,
    load_projection_cameras,
)
from geometry_engine.segmentation.mask_projection import file_hash


def quality(error=.01,coverage=.95):
    m=dict(samples=1000,coverage=coverage,median_m=error,p90_m=error*2,
        agreement=.9,tiles=[dict(tile=i,samples=100,coverage=coverage,median_m=error,agreement=.9) for i in range(9)])
    return {'train':dict(m),'held_out':dict(m)}


class AcceptanceTests(unittest.TestCase):
    def test_planar_geometry_cannot_justify_correction(self):
        x,y=np.meshgrid(np.linspace(-1,1,30),np.linspace(-1,1,30))
        p=np.column_stack((x.ravel(),y.ravel(),np.ones(x.size)))
        self.assertFalse(_observable(p,np.tile([0,0,1],(len(p),1)))[0])
        status,reasons=choose_camera(quality(.10),quality(.01),.1,1,False)
        self.assertEqual(status,'rejected')
        self.assertIn('underconstrained_geometry',reasons)

    def test_good_raw_pose_is_preserved_when_proposal_unnecessary(self):
        status,_=choose_camera(quality(),quality(),0,0,False)
        self.assertEqual(status,'accepted_raw')

    def test_missing_surface_cannot_improve_by_disappearing(self):
        status,reasons=choose_camera(quality(.10),quality(.01,.4),.1,1,True)
        self.assertEqual(status,'rejected');self.assertIn('coverage_decreased',reasons)

    def test_local_regression_rejects_even_when_global_median_improves(self):
        before=quality(.06);after=quality(.01)
        before['held_out']['tiles'][0]['median_m']=.01
        after['held_out']['tiles'][0]['median_m']=.05
        status,reasons=choose_camera(before,after,.1,1,True)
        self.assertEqual(status,'rejected');self.assertIn('local_depth_regression',reasons)

    def test_large_rotation_not_hidden_by_small_camera_translation(self):
        status,reasons=choose_camera(quality(.10),quality(.01),.01,7,True)
        self.assertEqual(status,'rejected');self.assertIn('correction_exceeds_bound',reasons)

    def test_held_out_spatial_blocks_are_separate(self):
        held,_=_partition((64,64))
        self.assertTrue((held[:16,:16]==False).all())
        self.assertTrue(held[:16,16:32].all())

    def test_occluded_or_wrong_depth_counts_as_disagreement(self):
        depth=np.full((64,64),2.);rendered=np.full((64,64),1.)
        held,sectors=_partition(depth.shape)
        m=depth_metrics(depth,rendered,np.ones(depth.shape,bool),held,sectors)
        self.assertEqual(m['coverage'],1)
        self.assertEqual(m['agreement'],0)


class SyntheticRoomTests(unittest.TestCase):
    def test_bounded_fit_recovers_camera_without_changing_mesh(self):
        import open3d as o3d
        # Camera inside three visible perpendicular room surfaces.
        vertices=np.array([[-2,-2,3],[2,-2,3],[2,1.2,3],[-2,1.2,3],
            [-2,1.2,.4],[2,1.2,.4],[-2,-2,.4],[2,-2,.4]],np.float32)
        faces=np.array([[0,1,2],[0,2,3],[3,2,5],[3,5,4],
            [1,7,5],[1,5,2],[0,3,4],[0,4,6]],np.uint32)
        original=vertices.copy();scene=o3d.t.geometry.RaycastingScene(nthreads=2)
        scene.add_triangles(o3d.core.Tensor(vertices),o3d.core.Tensor(faces))
        shape=(96,128);K=np.array([[70.,0,63.5],[0,70.,47.5],[0,0,1.]])
        depth,_=_render(scene,_rays(K,shape),np.eye(4),o3d)
        valid=np.isfinite(depth)&(depth>0);depth=np.where(valid,depth,0)
        raw=np.eye(4);raw[:3,3]=[.07,-.03,.06]
        result=align_frame(scene,K,raw,depth,valid,o3d)
        self.assertEqual(result['status'],'accepted_refined',result)
        np.testing.assert_allclose(np.array(result['T_world_from_camera_cv'])[:3,3],0,atol=.01)
        np.testing.assert_array_equal(vertices,original)


class ProvenanceTests(unittest.TestCase):
    def test_loader_rejects_changed_depth_and_keeps_rejected_frames_explicit(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);stage=root/'projection_alignment';stage.mkdir()
            mesh=root/'mesh.ply';mesh.write_bytes(b'mesh fingerprint only')
            depth=root/'depth.bin';depth.write_bytes(b'original depth')
            data=dict(schema_version='1.0',unit='meter',camera_axes='right_down_forward',frames=[
                dict(frame_id='a',status='accepted_raw',T_world_from_camera_cv=np.eye(4).tolist()),
                dict(frame_id='b',status='rejected')])
            (stage/'cameras.json').write_text(json.dumps(data))
            report=dict(status='completed',mesh_sha256=file_hash(mesh),cameras_sha256=file_hash(stage/'cameras.json'),
                input_fingerprints={'depth.bin':file_hash(depth)})
            (stage/'report.json').write_text(json.dumps(report))
            loaded=load_projection_cameras(stage,root,mesh)
            self.assertEqual(loaded['b']['status'],'rejected')
            self.assertNotIn('T_world_from_camera_cv',loaded['b'])
            depth.write_bytes(b'changed depth')
            with self.assertRaisesRegex(ValueError,'input changed'):
                load_projection_cameras(stage,root,mesh)


if __name__=='__main__':unittest.main()
