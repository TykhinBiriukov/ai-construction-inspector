import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from geometry_engine.common.mesh import write_mesh
from geometry_engine.openings.mesh_boundaries import MeshBoundaryService, mesh_edge_incidence, semantic_contour_edges
from geometry_engine.segmentation.mask_projection import file_hash


class MeshContourTests(unittest.TestCase):
    def fixture(self, gap=False, split=False):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        (root/'input').mkdir();(root/'mesh_preprocessing').mkdir();(root/'mesh_segmentation').mkdir()
        xy=np.array([(x,y) for y in np.linspace(0,4,33) for x in np.linspace(-2,2,33)])
        v=np.column_stack((xy,np.zeros(len(xy))));faces=[]
        for y in range(32):
            for x in range(32):
                a=y*33+x;faces.extend([[a,a+1,a+34],[a,a+34,a+33]])
        f=np.array(faces);centers=v[f].mean(1)
        window=(abs(centers[:,0])<.5)&(centers[:,1]>1)&(centers[:,1]<3)
        classes=window.astype(int);instances=np.where(window,1,0)
        if split:instances[window&(centers[:,0]>0)]=2
        if gap:classes[(centers[:,1]>=3)&(centers[:,1]<3.3)&(abs(centers[:,0])<.75)]=-1
        mesh=root/'mesh_preprocessing/mesh.ply';write_mesh(mesh,v,np.full((len(v),4),255,dtype=np.uint8),f)
        seg=root/'mesh_segmentation'
        np.savez_compressed(seg/'labels.npz',face_class_id=classes,face_instance_id=instances)
        rows=[{'instance_id':int(i),'label':'window','observation_ids':[]} for i in sorted(set(instances[window]))]
        (seg/'objects.json').write_text(json.dumps({'labels':['wall','window'],'objects':rows}))
        (seg/'report.json').write_text(json.dumps({'source_mesh_sha256':file_hash(mesh)}))
        return root,mesh

    def test_complete_interface_recovers_analytic_rectangle_without_mutating_mesh(self):
        root,mesh=self.fixture();before=mesh.read_bytes()
        result=MeshBoundaryService().run(root);obj=result['objects'][0]
        self.assertEqual(obj['status'],'accepted',obj['quality'])
        self.assertAlmostEqual(obj['width_m'],1,places=5);self.assertAlmostEqual(obj['height_m'],2,places=5)
        self.assertEqual(before,mesh.read_bytes());self.assertIsNone(result['model'])
        self.assertTrue(all(s['observed'] for s in obj['quality']['side_support'].values()))

    def test_unknown_strip_cannot_be_reported_as_observed_wall_interface(self):
        root,_=self.fixture(gap=True);obj=MeshBoundaryService().run(root)['objects'][0]
        self.assertEqual(obj['status'],'needs_review');self.assertIsNone(obj['width_m'])
        self.assertAlmostEqual(obj['candidate_width_m'],1,places=5)
        self.assertFalse(obj['quality']['side_support']['top']['observed'])

    def test_coplanar_fragments_combine_without_ifc(self):
        root,_=self.fixture(split=True);rows=MeshBoundaryService().run(root)['objects']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['source_instance_ids'],[1,2])
        self.assertAlmostEqual(rows[0]['width_m'],1,places=5)

    def test_resume_rejects_changed_inputs(self):
        root,mesh=self.fixture();service=MeshBoundaryService();service.run(root)
        self.assertEqual(service.run(root,resume=True)['status'],'completed')
        with mesh.open('ab') as f:f.write(b'changed')
        with self.assertRaisesRegex(ValueError,'changed'):service.run(root,resume=True)

    def test_mismatched_segmentation_source_rejected(self):
        root,_=self.fixture();(root/'mesh_segmentation/report.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'does not match'):MeshBoundaryService().run(root)

    def test_only_different_classes_share_a_boundary_not_instance_split(self):
        f=np.array([[0,1,2],[1,3,2]])
        e,a,b,c=mesh_edge_incidence(f)
        rim,wall=semantic_contour_edges(e,a,b,c,np.array([1,1]),np.array([1,2]),[1,2],1,0)
        self.assertEqual(len(rim),4);self.assertEqual(len(wall),0)

    def test_default_dependency_constructs_no_meta_backend(self):
        from geometry_engine_api.dependencies import get_opening_boundary_factory
        with patch.dict('os.environ',{'MODEL_API_KEY':''}):
            self.assertIsInstance(get_opening_boundary_factory()(),MeshBoundaryService)

    def test_incomplete_contour_requires_explicit_candidate_comparison(self):
        from geometry_engine.comparison.opening_comparison import load_boundary_observations
        root,_=self.fixture(gap=True);MeshBoundaryService().run(root)
        measured,rejected=load_boundary_observations(root,root/'mesh_boundaries',np.eye(4),{})
        self.assertEqual(len(measured),0);self.assertEqual(len(rejected),1)
        measured,_=load_boundary_observations(root,root/'mesh_boundaries',np.eye(4),{},include_candidates=True)
        self.assertEqual(measured[0]['measurement_status'],'mesh_contour_candidate')
        np.testing.assert_allclose(measured[0]['_intrinsic_dimensions'],[1,2],atol=1e-5)

    def test_rotated_contour_dimensions_are_not_inflated_by_ifc_axis_projection(self):
        from geometry_engine.comparison.opening_comparison import match_instances,OpeningComparisonSettings
        angle=np.deg2rad(5);rotation=np.array([[np.cos(angle),-np.sin(angle),0],[np.sin(angle),np.cos(angle),0],[0,0,1]])
        points=np.array([[-.5,1,0],[.5,1,0],[.5,-1,0],[-.5,-1,0]])@rotation.T
        observed={'category':'window','surface_area_m2':2,'_points':points,'_intrinsic_dimensions':(1,2)}
        reference={'category':'window','geometry_status':'loaded','u_axis':[1,0,0],'v_axis':[0,1,0],
                   'normal_axis':[0,0,1],'origin_m':[0,0,0],'bounds_local_m':[[-.5,-1,0],[.5,1,0]]}
        pairs,_=match_instances([observed],[reference],OpeningComparisonSettings())
        measured=pairs[0][2]
        self.assertGreater(measured['observed_projected_width_m'],1.1)
        self.assertEqual(measured['observed_width_m'],1)
        self.assertEqual(measured['deltas_m']['width'],0)
        np.testing.assert_allclose(measured['observed_corners_ifc_m'],points)


if __name__=='__main__':unittest.main()
