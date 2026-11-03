"""Materialize the displayed interior IFC surfaces as a separate IFC4 file."""
import base64
import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

import numpy as np


def _triangles_as_set(vertices, faces):
    triangles = np.round(np.asarray(vertices)[faces], 6)
    return {tuple(sorted(map(tuple, triangle))) for triangle in triangles}


def _verify_surface(vertices, faces, actual_vertices, actual_faces):
    if _triangles_as_set(actual_vertices, actual_faces) == _triangles_as_set(vertices, faces):
        return 0.
    # IFC kernels may remove coplanar triangle subdivisions when applying voids.
    # Verify the surface itself, rather than requiring identical triangulation.
    import open3d as o3d
    def area(v, f):
        t=v[f]
        return np.linalg.norm(np.cross(t[:,1]-t[:,0],t[:,2]-t[:,0]),axis=1).sum()/2
    if not np.isclose(area(vertices,faces),area(actual_vertices,actual_faces),rtol=1e-7,atol=1e-6):
        raise ValueError('Reopened IFC surface area differs from the viewer')
    def distance(source, target_vertices, target_faces):
        triangles = source[0][source[1]]
        weights = np.array([[a/4,b/4,1-(a+b)/4] for a in range(5) for b in range(5-a)])
        points = np.einsum('kj,njc->nkc',weights,triangles).reshape(-1,3)
        scene = o3d.t.geometry.RaycastingScene(nthreads=2)
        scene.add_triangles(o3d.core.Tensor(target_vertices.astype(np.float32)),
                            o3d.core.Tensor(target_faces.astype(np.uint32)))
        return float(scene.compute_distance(o3d.core.Tensor(points.astype(np.float32)),nthreads=2).numpy().max())
    maximum = max(distance((vertices,faces),actual_vertices,actual_faces),
                  distance((actual_vertices,actual_faces),vertices,faces))
    if maximum > 5e-6:
        raise ValueError('Reopened IFC surface positions differ from the viewer')
    return maximum


def export_interior_ifc(source_ifc, viewer_path, output_path):
    """Export default IFC layers, retaining GUIDs, placements and opening links.

    Vertices come from the saved viewer, not from the scan or newly inferred
    dimensions. Structural elements and semantic openings are represented at
    the interior surface. Wall holes are already included in their mesh geometry.
    """
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.placement
    import ifcopenshell.util.unit
    from ifcopenshell.api.geometry.assign_representation import assign_representation
    from ifcopenshell.api.geometry.remove_representation import remove_representation
    from ifcopenshell.api.geometry.unassign_representation import unassign_representation
    from geometry_engine.comparison.bim_reference import _triangles
    from geometry_engine.common.surface_geometry import opening_on_wall, surface_plane

    source_ifc, viewer_path, output_path = map(lambda p: Path(p).resolve(),
                                              (source_ifc, viewer_path, output_path))
    if output_path == source_ifc or output_path.exists():
        raise FileExistsError('Use a new IFC output; source and existing files are not overwritten')
    if output_path.suffix.lower() != '.ifc':
        raise ValueError('Output must be an IFC file')
    source_hash = hashlib.sha256(source_ifc.read_bytes()).hexdigest()
    report = json.loads(viewer_path.with_name('report.json').read_text(encoding='utf-8'))
    if report.get('source_ifc_sha256') != source_hash:
        raise ValueError('Source IFC does not match the saved comparison')
    found = re.search(r'<script id="data" type="application/json">([\s\S]*?)</script>',
                      viewer_path.read_text(encoding='utf-8'))
    if found is None:
        raise ValueError('Viewer geometry payload not found')
    payload = json.loads(found[1])
    if payload.get('unit') != 'meter' or payload.get('up_axis') != 'z':
        raise ValueError('Expected IFC-world coordinates in meters, Z up')
    model = ifcopenshell.open(str(source_ifc))
    if model.schema != 'IFC4':
        raise ValueError('This surface export currently supports IFC4')
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    expected = {}
    colors = {'wall': (.15, .72, 1.), 'floor': (.6, .35, 1.),
              'ceiling': (.9, .35, .75), 'door': (.1, 1., .55), 'window': (1., .75, .1)}
    classes = {'wall': 'IfcWall', 'floor': 'IfcSlab', 'ceiling': 'IfcSlab',
               'door': 'IfcDoor', 'window': 'IfcWindow'}
    for layer in payload['layers']:
        if (layer.get('side') != 'ifc' or layer.get('category') in ('unknown', 'contour')
                or layer.get('default_hidden')):
            continue
        category = layer.get('opening_kind') if layer['category'] == 'opening' else layer['category']
        if category not in classes:
            continue
        identity = layer['id'].removesuffix('-opening')
        if identity in expected:
            raise ValueError('Duplicate displayed IFC element')
        element = model.by_guid(identity)
        if not element.is_a(classes[category]):
            raise ValueError('Viewer category does not match the source IFC element')
        vertices = np.frombuffer(base64.b64decode(layer['positions']), dtype='<f4').reshape(-1, 3).astype(float)
        faces = np.frombuffer(base64.b64decode(layer['indices']), dtype='<u4').reshape(-1, 3).astype(int)
        if not np.isfinite(vertices).all() or not len(faces) or faces.max() >= len(vertices):
            raise ValueError('Invalid displayed geometry')
        # Projected box faces can coincide. Keep each geometric triangle once.
        keys = set(); unique = []
        for face in faces:
            key = tuple(sorted(map(tuple, np.round(vertices[face], 9))))
            if key not in keys:
                keys.add(key); unique.append(face)
        faces = np.asarray(unique, dtype=int)
        matrix = np.asarray(ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement))
        local = (vertices/scale-matrix[:3, 3]) @ matrix[:3, :3]
        representations = list(element.Representation.Representations)
        context = next((r.ContextOfItems for r in representations if r.RepresentationIdentifier == 'Body'),
                       representations[0].ContextOfItems)
        for representation in representations:
            unassign_representation(model, product=element, representation=representation)
            remove_representation(model, representation=representation)
        points = model.create_entity('IfcCartesianPointList3D', CoordList=local.tolist())
        item = model.create_entity('IfcTriangulatedFaceSet', Coordinates=points, Closed=False,
                                   CoordIndex=(faces+1).tolist())
        representation = model.create_entity('IfcShapeRepresentation', ContextOfItems=context,
            RepresentationIdentifier='Body', RepresentationType='Tessellation', Items=[item])
        assign_representation(model, product=element, representation=representation)
        color = model.create_entity('IfcColourRgb', Red=colors[category][0],
                                    Green=colors[category][1], Blue=colors[category][2])
        shading = model.create_entity('IfcSurfaceStyleShading', SurfaceColour=color, Transparency=0.)
        style = model.create_entity('IfcSurfaceStyle', Name='Interior reference '+category,
                                   Side='BOTH', Styles=[shading])
        model.create_entity('IfcStyledItem', Item=item, Styles=[style])
        element.Description = 'Interior surface reference exported from the comparison viewer. Source GUID retained.'
        expected[identity] = (category, vertices, faces)
    if not expected:
        raise ValueError('No displayed IFC elements to export')
    physical = {e.GlobalId for t in ('IfcWall', 'IfcSlab', 'IfcDoor', 'IfcWindow') for e in model.by_type(t)}
    if physical != set(expected):
        raise ValueError('Viewer does not cover every physical source element; export would retain old geometry')
    # Keep void/fill relations usable by opening comparison without retaining an
    # old opening midplane behind the new surface reference. The wall mesh already
    # contains the holes, so the derived reference requires no volumetric cutters.
    opening_surfaces = {}
    opening_settings = ifcopenshell.geom.settings()
    opening_settings.set('use-world-coords', True)
    opening_settings.set('convert-back-units', False)
    for opening in model.by_type('IfcOpeningElement'):
        hosts = [r.RelatingBuildingElement.GlobalId for r in opening.VoidsElements]
        if len(hosts) != 1 or hosts[0] not in expected:
            raise ValueError('Opening has no unique exported host wall')
        _, wall_vertices, wall_faces = expected[hosts[0]]
        vertices, faces = _triangles(ifcopenshell.geom.create_shape(opening_settings, opening))
        vertices, faces = opening_on_wall(vertices, faces, surface_plane(wall_vertices, wall_faces))
        matrix = np.asarray(ifcopenshell.util.placement.get_local_placement(opening.ObjectPlacement))
        local = (vertices/scale-matrix[:3,3]) @ matrix[:3,:3]
        representations = list(opening.Representation.Representations)
        context = representations[0].ContextOfItems
        for representation in representations:
            unassign_representation(model,product=opening,representation=representation)
            remove_representation(model,representation=representation)
        points = model.create_entity('IfcCartesianPointList3D',CoordList=local.tolist())
        item = model.create_entity('IfcTriangulatedFaceSet',Coordinates=points,Closed=False,CoordIndex=(faces+1).tolist())
        representation = model.create_entity('IfcShapeRepresentation',ContextOfItems=context,
            RepresentationIdentifier='Body',RepresentationType='Tessellation',Items=[item])
        assign_representation(model,product=opening,representation=representation)
        opening_surfaces[opening.GlobalId] = (vertices,faces)
    model.header.file_description.description = (
        'Interior surface reference; walls and floor are open surfaces.',
        'Opening relations retained with coplanar interior-surface geometry.',
        'Source IFC SHA-256: '+source_hash,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.interior-ifc-', dir=output_path.parent) as temporary:
        staged = Path(temporary)/'reference.ifc'
        model.write(str(staged))
        loaded = ifcopenshell.open(str(staged))
        settings = ifcopenshell.geom.settings()
        settings.set('use-world-coords', True)
        settings.set('convert-back-units', False)
        verified = []
        for identity, (category, vertices, faces) in expected.items():
            element = loaded.by_guid(identity)
            actual_vertices, actual_faces = _triangles(ifcopenshell.geom.create_shape(settings, element))
            deviation = _verify_surface(vertices, faces, actual_vertices, actual_faces)
            if category in ('wall', 'floor'):
                centered = actual_vertices-actual_vertices.mean(0)
                if np.linalg.svd(centered, compute_uv=False)[-1] > 1e-6:
                    raise ValueError('Exported structural surface has thickness')
            verified.append({'guid': identity, 'category': category, 'faces': len(actual_faces),
                             'max_surface_roundtrip_difference_m': deviation})
        for identity, (vertices, faces) in opening_surfaces.items():
            actual_vertices, actual_faces = _triangles(ifcopenshell.geom.create_shape(settings,loaded.by_guid(identity)))
            _verify_surface(vertices,faces,actual_vertices,actual_faces)
        if hashlib.sha256(source_ifc.read_bytes()).hexdigest() != source_hash:
            raise ValueError('Source IFC changed during export')
        staged.rename(output_path)
    return {'output': str(output_path), 'source_ifc_sha256': source_hash,
            'source_viewer': str(viewer_path), 'elements': verified,
            'geometry_matches_viewer': True, 'original_ifc_unchanged': True}
