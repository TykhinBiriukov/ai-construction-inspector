"""Synthetic checks for mesh cleanup, topology, source mapping and ray queries."""

from pathlib import Path
import tempfile
import unittest

import numpy as np

ENGINE = Path(__file__).resolve().parents[1]
from geometry_engine import mesh as alignment


class MeshCleanupTests(unittest.TestCase):
    def test_defects_are_removed_and_source_ids_remain_traceable(self):
        vertices=np.array([[0.,0,0],[1.,0,0],[0.,1,0],[0.,0,0],[2.,2,2],[np.nan,0,0]])
        colors=np.tile([10,20,30,255],(len(vertices),1)).astype(np.uint8)
        colors[3]=[90,80,70,255]
        faces=np.array([[0,1,2],[3,1,2],[2,1,0],[0,0,1],[0,1,5],[0,1,99]])
        geometry,mapping,report=alignment.prepare_mesh(vertices,colors,faces)
        self.assertEqual(len(geometry["vertices"]),3)
        self.assertEqual(len(geometry["faces"]),1)
        for field,number in {"nonfinite_vertices_removed":1,"exact_duplicate_vertices_merged":1,
                             "faces_with_invalid_indices_removed":1,"faces_with_nonfinite_vertices_removed":1,
                             "degenerate_faces_removed":1,"duplicate_faces_removed":2,
                             "unused_unique_vertices_removed":1}.items():
            self.assertEqual(report[field],number,field)
        vertex_map=mapping["source_vertex_to_prepared"]
        self.assertEqual(vertex_map[0],vertex_map[3])
        self.assertEqual(vertex_map[4],-1);self.assertEqual(vertex_map[5],-1)
        np.testing.assert_array_equal(mapping["source_face_to_prepared"],[0,0,0,-1,-1,-1])
        np.testing.assert_array_equal(geometry["vertices"][geometry["faces"][0]],vertices[faces[0]])
        np.testing.assert_array_equal(geometry["rgba"][vertex_map[0]],colors[0])
        self.assertEqual(report["duplicate_position_observations_with_different_color"],1)

    def test_normal_and_area_are_correct_without_moving_surface(self):
        vertices=np.array([[0.,0,0],[2.,0,0],[0.,3,0]])
        colors=np.full((3,4),255,dtype=np.uint8)
        geometry,_,_=alignment.prepare_mesh(vertices,colors,np.array([[0,1,2]]))
        np.testing.assert_allclose(geometry["face_areas_m2"],[3.])
        np.testing.assert_allclose(geometry["face_normals"],[[0,0,1]])
        np.testing.assert_allclose(geometry["vertex_normals"],[[0,0,1]]*3)

    def test_empty_valid_surface_is_rejected(self):
        with self.assertRaises(ValueError):
            alignment.prepare_mesh(np.zeros((3,3)),np.zeros((3,4),dtype=np.uint8),np.array([[0,1,2]]))

    def test_ply_roundtrip_preserves_triangles_and_colors(self):
        with tempfile.TemporaryDirectory(dir=ENGINE) as directory:
            path=Path(directory)/"mesh.ply"
            vertices=np.array([[0.,0,0],[1.,0,0],[0.,1,0]])
            colors=np.array([[10,20,30,255],[40,50,60,128],[70,80,90,0]],dtype=np.uint8)
            faces=np.array([[0,1,2]],dtype=np.int32)
            alignment.write_mesh(path,vertices,colors,faces)
            actual_vertices,actual_colors,actual_faces=alignment.read_mesh(path)
            np.testing.assert_array_equal(actual_vertices,vertices)
            np.testing.assert_array_equal(actual_colors,colors)
            np.testing.assert_array_equal(actual_faces,faces)

    def test_double_precision_coordinates_are_not_rounded(self):
        with tempfile.TemporaryDirectory(dir=ENGINE) as directory:
            path=Path(directory)/"mesh.ply"
            vertices=np.array([[.123456789123,0,0],[1.,0,0],[0.,1,0]])
            alignment.write_mesh(path,vertices,np.full((3,4),255,dtype=np.uint8),np.array([[0,1,2]]))
            actual,_,_=alignment.read_mesh(path)
            np.testing.assert_array_equal(actual,vertices)


class MeshTopologyTests(unittest.TestCase):
    def setUp(self):
        self.vertices=np.array([[0.,0,0],[1.,0,0],[0.,1,0],[0.,0,1]])
        self.faces=np.array([[0,2,1],[0,1,3],[0,3,2],[1,2,3]])

    def test_closed_tetrahedron_has_consistent_edges(self):
        labels,report=alignment.mesh_topology(self.vertices,self.faces,np.ones(4))
        self.assertEqual(report["connected_components"],1)
        self.assertEqual(report["boundary_edges"],0)
        self.assertEqual(report["nonmanifold_edges"],0)
        self.assertEqual(report["inconsistent_winding_edges"],0)
        self.assertTrue((labels==0).all())

    def test_reversed_face_is_reported_without_repair(self):
        faces=self.faces.copy();faces[0]=faces[0,::-1]
        _,report=alignment.mesh_topology(self.vertices,faces,np.ones(4))
        self.assertEqual(report["inconsistent_winding_edges"],3)
        self.assertFalse(report["winding_reoriented"])

    def test_nonmanifold_edge_is_reported(self):
        vertices=np.vstack([self.vertices,[.5,-.3,0]])
        faces=np.vstack([self.faces,[0,1,4]])
        _,report=alignment.mesh_topology(vertices,faces,np.ones(5))
        self.assertEqual(report["nonmanifold_edges"],1)

    def test_disconnected_parts_are_preserved(self):
        vertices=np.vstack([self.vertices,self.vertices+[5,0,0]])
        faces=np.vstack([self.faces,self.faces+4])
        _,report=alignment.mesh_topology(vertices,faces,np.ones(8))
        self.assertEqual(report["connected_components"],2)
        self.assertFalse(report["small_components_removed"])


class TriangleBVHTests(unittest.TestCase):
    def setUp(self):
        self.vertices=np.array([[-2.,-2,2],[2.,-2,2],[0.,2,2],[-1.,-1,1],[1.,-1,1],[0.,1,1]])
        self.faces=np.array([[0,1,2],[3,4,5]])
        self.bvh=alignment.build_bvh(self.vertices,self.faces,leaf_size=1)

    def test_closest_visible_triangle_and_metric_distance(self):
        hit=alignment.raycast([0,0,0],[0,0,4],self.vertices,self.faces,self.bvh)
        self.assertEqual(hit["face_index"],1);self.assertAlmostEqual(hit["distance_m"],1.)

    def test_ray_works_from_back_side(self):
        hit=alignment.raycast([0,0,3],[0,0,-1],self.vertices,self.faces,self.bvh)
        self.assertEqual(hit["face_index"],0);self.assertAlmostEqual(hit["distance_m"],1.)

    def test_parallel_miss_and_zero_direction(self):
        hit=alignment.raycast([10,0,0],[0,0,1],self.vertices,self.faces,self.bvh)
        self.assertEqual(hit["face_index"],-1);self.assertIsNone(hit["distance_m"])
        with self.assertRaises(ValueError):
            alignment.raycast([0,0,0],[0,0,0],self.vertices,self.faces,self.bvh)

    def test_all_faces_are_present_once(self):
        np.testing.assert_array_equal(np.sort(self.bvh["face_order"]),[0,1])



def calibration(transform=None):
    return {
        "pose": {"T_world_from_camera": (np.eye(4) if transform is None else transform).tolist()},
        "intrinsics": {"width": 5, "height": 5, "fx": 2., "fy": 2., "cx": 2., "cy": 2.},
    }


class MeshRenderingTests(unittest.TestCase):
    def setUp(self):
        self.wall = np.array([[-2., -2., 2.], [2., -2., 2.],
                              [2., 2., 2.], [-2., 2., 2.]])
        self.faces = np.array([[0, 1, 2], [0, 2, 3]])

    def test_flat_wall_renders_known_depth_at_every_pixel(self):
        rendered = alignment.render_mesh_depth(self.wall, self.faces, calibration())
        np.testing.assert_allclose(rendered, np.full((5, 5), 2.), atol=1e-12)

    def test_nearer_triangle_occludes_wall_independent_of_face_order(self):
        vertices = np.vstack([self.wall, [[-.5, -.5, 1.], [.5, -.5, 1.], [0., .5, 1.]]])
        for faces in (np.vstack([self.faces, [4, 5, 6]]),
                      np.vstack([[4, 5, 6], self.faces])):
            with self.subTest(faces=faces.tolist()):
                rendered = alignment.render_mesh_depth(vertices, faces, calibration())
                self.assertAlmostEqual(rendered[2, 2], 1., places=12)
                self.assertAlmostEqual(rendered[0, 0], 2., places=12)

    def test_transformed_camera_reconstructs_same_wall_depth(self):
        transform = np.array([[0., -1., 0., 4.], [1., 0., 0., -3.],
                              [0., 0., 1., 5.], [0., 0., 0., 1.]])
        # Known forward camera->world equation, independent from rendering inverse.
        world = np.column_stack([-self.wall[:, 1] + 4., self.wall[:, 0] - 3., self.wall[:, 2] + 5.])
        rendered = alignment.render_mesh_depth(world, self.faces, calibration(transform))
        np.testing.assert_allclose(rendered, np.full((5, 5), 2.), atol=1e-12)

    def test_slanted_triangle_uses_perspective_correct_depth(self):
        vertices = np.array([[-1., -1., 1.], [2., -2., 2.], [0., 2., 2.]])
        rendered = alignment.render_mesh_depth(vertices, np.array([[0, 1, 2]]), calibration())
        # Plane -4x-2y+10z=16, ray at pixel(2,1) is (0,-.5,1)*depth.
        self.assertAlmostEqual(rendered[1, 2], 16./11., places=12)

    def test_mesh_behind_camera_produces_no_depth(self):
        behind = self.wall.copy()
        behind[:, 2] = -2.
        rendered = alignment.render_mesh_depth(behind, self.faces, calibration())
        self.assertTrue(np.all(np.isinf(rendered)))




if __name__ == "__main__":
    unittest.main()
