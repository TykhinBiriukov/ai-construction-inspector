"""Geometry/evidence contracts for observed-only scan-vs-BIM comparison."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from geometry_engine.common.mesh import write_mesh
from geometry_engine.comparison.scan_vs_bim import (
    ScanVsBimService, ScanVsBimSettings, compare_surfaces, load_semantics, source_to_z_up,
)


class ScanVsBimTests(unittest.TestCase):
    def reference(self):
        vertices=np.array([[0.,0,0],[0,1,0],[0,1,1],[0,0,1]])
        faces=np.array([[0,1,2],[0,2,3]],dtype=np.int32)
        element={'element_id':0,'category':'wall','compare':True,'guid':'wall-guid',
                 'ifc_class':'IfcWall','name':'Wall'}
        return SimpleNamespace(vertices=vertices,faces=faces,
                               face_element_ids=np.array([0,0]),elements=[element])

    def settings(self):
        return ScanVsBimSettings(min_observed_area_m2=.001)

    def test_known_wall_offset_is_not_refitted_away(self):
        ref=self.reference();scan=ref.vertices+[.04,0,0]
        result=compare_surfaces(scan,ref.faces,ref,np.ones(2,dtype=bool),np.zeros(2,dtype=bool),self.settings())
        np.testing.assert_allclose(result['deviations_m'],.04,atol=1e-7)
        self.assertEqual(result['elements'][0]['status'],'observed_deviation')
        self.assertAlmostEqual(result['elements'][0]['statistics']['median_signed_m'],.04)
        self.assertFalse(result['elements'][0]['whole_element_verified'])

    def test_signed_distance_follows_bim_normal(self):
        ref=self.reference();scan=ref.vertices+[-.025,0,0]
        result=compare_surfaces(scan,ref.faces,ref,np.ones(2,dtype=bool),np.zeros(2,dtype=bool),self.settings())
        np.testing.assert_allclose(result['deviations_m'],-.025,atol=1e-7)
        self.assertEqual(result['elements'][0]['status'],'observed_within_tolerance')

    def test_camera_side_prevents_matching_opposite_wall_surface(self):
        ref=self.reference()
        ref.vertices=np.vstack((ref.vertices,ref.vertices+[-.2,0,0]))
        # Inner surface points into the room (+X); outer surface faces away.
        ref.faces=np.array([[0,1,2],[0,2,3],[4,6,5],[4,7,6]],dtype=np.int32)
        ref.face_element_ids=np.zeros(4,dtype=np.int32)
        scan=ref.vertices[:4]+[-.15,0,0]
        result=compare_surfaces(scan,ref.faces[:2],ref,np.ones(2,dtype=bool),
            np.zeros(2,dtype=bool),self.settings(),camera_positions=np.array([[1.,.5,.5]]))
        # The nearest outside face would incorrectly yield -0.05 m. The
        # intended interior surface is 0.15 m away and must retain that offset.
        np.testing.assert_allclose(result['deviations_m'],-.15,atol=1e-7)

    def test_furniture_and_unknown_are_not_wall_deviations(self):
        ref=self.reference()
        result=compare_surfaces(ref.vertices+[.12,0,0],ref.faces,ref,np.zeros(2,dtype=bool),np.zeros(2,dtype=bool),self.settings())
        self.assertTrue(np.isnan(result['deviations_m']).all())
        self.assertTrue((result['element_ids']==-1).all())
        self.assertEqual(result['elements'][0]['status'],'insufficient_evidence')

    def test_perpendicular_triangle_is_not_matching_wall(self):
        ref=self.reference()
        scan=np.array([[.01,.2,.5],[.1,.2,.5],[.1,.8,.5]])
        result=compare_surfaces(scan,np.array([[0,1,2]]),ref,np.ones(1,dtype=bool),np.zeros(1,dtype=bool),self.settings())
        self.assertTrue(np.isnan(result['deviations_m'][0]))
        self.assertEqual(result['face_status'][0],2)

    def test_edge_only_nearest_match_is_rejected(self):
        ref=self.reference()
        scan=np.array([[.01,1.03,.2],[.01,1.04,.2],[.01,1.035,.8]])
        result=compare_surfaces(scan,np.array([[0,1,2]]),ref,np.ones(1,dtype=bool),np.zeros(1,dtype=bool),self.settings())
        self.assertTrue(np.isnan(result['deviations_m'][0]))

    def test_floor_cannot_match_wall_despite_same_position(self):
        ref=self.reference()
        result=compare_surfaces(ref.vertices,ref.faces,ref,np.zeros(2,dtype=bool),np.ones(2,dtype=bool),self.settings())
        self.assertTrue(np.isnan(result['deviations_m']).all())

    def test_arkit_basis_is_rigid_and_right_handed(self):
        r=source_to_z_up('arkit');np.testing.assert_allclose(r@np.array([1,2,3]),[1,-3,2])
        self.assertAlmostEqual(np.linalg.det(r),1)
        with self.assertRaises(ValueError):source_to_z_up('unknown')

    def fixture(self,path):
        (path/'mesh_preprocessing').mkdir();(path/'input').mkdir();seg=path/'mesh_segmentation';seg.mkdir()
        ref=self.reference();mesh=path/'mesh_preprocessing/mesh.ply'
        write_mesh(mesh,ref.vertices,np.full((4,4),255,dtype=np.uint8),ref.faces)
        (path/'input/capture.json').write_text(json.dumps({'coordinate_system':'arkit','length_unit':'meter'}))
        (seg/'report.json').write_text(json.dumps({'status':'completed','source_mesh_sha256':hashlib.sha256(mesh.read_bytes()).hexdigest()}))
        (seg/'objects.json').write_text(json.dumps({'labels':['wall','floor']}))
        np.savez(seg/'labels.npz',face_class_id=np.array([0,1]),face_view_count=np.array([3,1]))
        ifc=path/'reference.ifc';ifc.write_text('fake IFC for dependency-injection test')
        return mesh,seg,ifc,ref

    def test_semantic_fingerprint_mismatch_is_rejected(self):
        with TemporaryDirectory() as temp:
            mesh,seg,_,_=self.fixture(Path(temp))
            (seg/'report.json').write_text(json.dumps({'source_mesh_sha256':'wrong'}))
            with self.assertRaisesRegex(ValueError,'SHA-256'):load_semantics(seg,mesh,2,2)

    def test_minimum_view_count_excludes_single_view_evidence(self):
        with TemporaryDirectory() as temp:
            mesh,seg,_,_=self.fixture(Path(temp))
            wall,floor,_=load_semantics(seg,mesh,2,2)
            np.testing.assert_array_equal(wall,[True,False]);self.assertFalse(floor.any())

    def test_rejected_registration_never_publishes_deviation_measurements(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);_,_,ifc,ref=self.fixture(root)
            rejected={'accepted':False,'reason':'ambiguous_competing_alignment',
                      'transform':np.eye(4).tolist(),'candidates':[]}
            def viewer(path,*args):Path(path).write_text('rejected preview')
            with patch('geometry_engine.comparison.scan_vs_bim.load_bim_reference',return_value=ref), \
                 patch('geometry_engine.comparison.scan_vs_bim.register_scan',return_value=rejected), \
                 patch('geometry_engine.comparison.scan_vs_bim.compare_surfaces') as compare, \
                 patch('geometry_engine.comparison.scan_vs_bim.export_scan_bim_viewer',side_effect=viewer):
                report=ScanVsBimService().compare(root,ifc)
            self.assertEqual(report['status'],'registration_rejected')
            self.assertEqual(report['compared_faces'],0);compare.assert_not_called()
            self.assertFalse((root/'scan_vs_bim/deviations.npz').exists())
            self.assertFalse((root/'scan_vs_bim/objects.json').exists())

    def test_segmented_only_preserves_coordinates_and_filters_semantic_indices(self):
        from geometry_engine.common.mesh import read_mesh
        with TemporaryDirectory() as temp:
            root=Path(temp);mesh,seg,ifc,ref=self.fixture(root)
            np.savez(seg/'labels.npz',face_class_id=np.array([0,-1]),face_view_count=np.array([3,0]))
            rejected={'accepted':False,'reason':'test','transform':np.eye(4).tolist(),'candidates':[]}
            with patch('geometry_engine.comparison.scan_vs_bim.load_bim_reference',return_value=ref), \
                 patch('geometry_engine.comparison.scan_vs_bim.register_scan',return_value=rejected) as register, \
                 patch('geometry_engine.comparison.scan_vs_bim.export_scan_bim_viewer'):
                report=ScanVsBimService().compare(root,ifc,segmented_only=True)
            vertices,_,faces=read_mesh(root/'scan_vs_bim/segmented_scan.ply')
            original,_,triangles=read_mesh(mesh)
            np.testing.assert_array_equal(vertices[faces],original[triangles[:1]])
            self.assertEqual(report['mesh_filter']['removed_faces'],1)
            self.assertEqual(len(register.call_args.args[1]),1)
            np.testing.assert_array_equal(register.call_args.kwargs['source_wall_mask'],[True])
            with np.load(root/'scan_vs_bim/source_mapping.npz') as mapping:
                np.testing.assert_array_equal(mapping['source_face_ids'],[0])


if __name__=='__main__':unittest.main()
