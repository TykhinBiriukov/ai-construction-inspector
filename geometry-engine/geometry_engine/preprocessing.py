"""Reusable mesh preprocessing; accepts explicit paths and needs no scene dataset."""

import hashlib
import json
from pathlib import Path

import numpy as np

from .mesh import build_bvh, mesh_topology, prepare_mesh, read_mesh, write_mesh


def preprocess_mesh(input_mesh, output_dir, *, unit="meter"):
    """Prepare a triangle PLY, preserve source indices, and return its report.

    Input coordinates must already be in metres. Camera frames, dataset
    manifests, image libraries, and diagnostic plots are not required.
    Existing output directories are never overwritten.
    """
    if unit != "meter":
        raise ValueError("Input mesh coordinates must already be in metres")
    source = Path(input_mesh).resolve()
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Output exists; choose another directory: {output}")
    staging = output.with_name(output.name + ".building")
    if staging.exists():
        raise FileExistsError(f"Incomplete output exists: {staging}")

    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    vertices, rgba, faces = read_mesh(source)
    geometry, mapping, cleanup = prepare_mesh(vertices, rgba, faces)
    components, topology = mesh_topology(
        geometry["vertices"], geometry["faces"], geometry["face_areas_m2"]
    )
    geometry["vertex_component"] = components
    bvh = build_bvh(geometry["vertices"], geometry["faces"])

    representatives = mapping["prepared_vertex_source_representative"]
    face_representatives = mapping["prepared_face_source_representative"]
    if not np.array_equal(geometry["vertices"], vertices[representatives]):
        raise RuntimeError("Vertex coordinates changed")
    if not np.array_equal(vertices[faces[face_representatives]], geometry["vertices"][geometry["faces"]]):
        raise RuntimeError("Triangle correspondence changed")
    if not np.array_equal(np.sort(bvh["face_order"]), np.arange(len(geometry["faces"]))):
        raise RuntimeError("BVH lost faces")

    staging.mkdir(parents=True, exist_ok=False)
    write_mesh(staging / "mesh.ply", geometry["vertices"], geometry["rgba"], geometry["faces"])
    np.savez_compressed(staging / "geometry.npz", **geometry)
    np.savez_compressed(staging / "source-mapping.npz", **mapping)
    np.savez_compressed(staging / "spatial-index.npz", **bvh)
    saved_vertices, saved_rgba, saved_faces = read_mesh(staging / "mesh.ply")
    if not all((
        np.array_equal(saved_vertices, geometry["vertices"]),
        np.array_equal(saved_faces, geometry["faces"]),
        np.array_equal(saved_rgba, geometry["rgba"]),
    )):
        raise RuntimeError("Prepared PLY roundtrip changed geometry")
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
        raise RuntimeError("Source mesh changed")

    report = {
        "schema_version": "1.0", "input_mesh": str(source),
        "source_sha256": source_hash, "status": "prepared",
        "cleanup": cleanup, "topology": topology,
        "spatial_index": {"type": "triangle_AABB_BVH", "node_count": len(bvh["left"]), "max_leaf_faces": 64},
        "validation": {
            "source_mesh_unchanged": True, "vertex_positions_exactly_preserved": True,
            "retained_triangles_exactly_preserved": True, "saved_PLY_roundtrip_passed": True,
            "BVH_contains_every_prepared_face_once": True,
        },
        "outputs": {"mesh": "mesh.ply", "geometry": "geometry.npz", "mapping": "source-mapping.npz", "spatial_index": "spatial-index.npz"},
        "policies": {
            "coordinate_transform": "identity", "unit": unit,
            "exact_duplicate_color": "first source representative",
            "smoothing": False, "hole_filling": False, "decimation": False, "small_component_removal": False,
        },
        "notes": [
            "Open boundaries and disconnected components are reported and preserved.",
            "Nonmanifold and winding defects are reported; no speculative repair is applied.",
            "Vertex normals follow existing face winding, not a guaranteed outward direction.",
        ],
    }
    if topology["nonmanifold_edges"] or topology["inconsistent_winding_edges"] or cleanup["zero_vertex_normals"]:
        report["status"] = "prepared_with_topology_warnings"
    (staging / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    staging.rename(output)
    return report
