"""Oriented, metre-valued IFC door/window geometry for scan comparison.

The target is the IFC element's actual geometry, not a world-axis bounding box
or the nominal opening size. Filled openings are recorded separately so callers
cannot silently compare a segmented leaf to a structural hole.
"""
from itertools import product
from pathlib import Path
import hashlib

import numpy as np

from geometry_engine.comparison.bim_reference import _triangles


def _bounds(vertices, origin, axes):
    local = (vertices - origin) @ axes
    lo, hi = local.min(axis=0), local.max(axis=0)
    corners = np.array(list(product(*zip(lo, hi)))) @ axes.T + origin
    return [lo.tolist(), hi.tolist()], corners.tolist()


def load_opening_references(ifc_path) -> list[dict]:
    """Return one record per IfcDoor/IfcWindow; unavailable geometry stays explicit.

    ``bounds_local_m`` uses columns (u, v, n): element-local X, Z, Y.
    ``corners_world_m`` contains the eight corners of those oriented bounds.
    Placements and geometry both use IFC world coordinates in SI metres; map
    conversions are not separately applied. Parent placements are included.
    """
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.placement
    import ifcopenshell.util.unit

    path = Path(ifc_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() != '.ifc':
        raise ValueError('Expected an .ifc STEP file')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        model = ifcopenshell.open(str(path))
        projects = model.by_type('IfcProject')
        if len(projects) != 1 or projects[0].UnitsInContext is None:
            raise ValueError('Missing project units')
        lengths = [u for u in projects[0].UnitsInContext.Units if getattr(u, 'UnitType', None) == 'LENGTHUNIT']
        if len(lengths) != 1:
            raise ValueError('Missing or ambiguous length unit')
        scale = float(ifcopenshell.util.unit.calculate_unit_scale(model))
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError('Invalid length scale')
    except Exception as error:
        raise ValueError('Could not read an IFC project with valid declared units') from error

    settings = ifcopenshell.geom.settings()
    settings.set('use-world-coords', True)
    settings.set('convert-back-units', False)
    records = []
    elements = sorted(model.by_type('IfcDoor') + model.by_type('IfcWindow'), key=lambda e: e.id())
    for element in elements:
        openings = [rel.RelatingOpeningElement for rel in getattr(element, 'FillsVoids', ())]
        hosts = {rel.RelatingBuildingElement.GlobalId for opening in openings
                 for rel in getattr(opening, 'VoidsElements', ())
                 if rel.RelatingBuildingElement.is_a('IfcWall')}
        record = {
            'guid': element.GlobalId, 'ifc_step_id': element.id(), 'name': element.Name or '',
            'category': 'door' if element.is_a('IfcDoor') else 'window',
            'host_wall_guid': next(iter(hosts)) if len(hosts) == 1 else None,
            'host_wall_guids': sorted(hosts), 'measurement_basis': 'ifc_element_geometry',
            'geometry_status': 'unavailable', 'length_unit': 'meter',
            'origin_m': None, 'u_axis': None, 'v_axis': None, 'normal_axis': None,
            'bounds_local_m': None, 'corners_world_m': [], 'filled_openings': [],
        }
        for name in ('OverallWidth', 'OverallHeight'):
            value = getattr(element, name, None)
            if value is not None and np.isfinite(float(value)) and float(value) > 0:
                record[name + '_m'] = float(value) * scale
        try:
            placement = element.ObjectPlacement
            if placement is None or not placement.is_a('IfcLocalPlacement'):
                raise ValueError('A local element placement is required')
            matrix = np.asarray(ifcopenshell.util.placement.get_local_placement(placement), dtype=float)
            if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
                    or not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-7)):
                raise ValueError('Invalid rigid element placement')
            origin = matrix[:3, 3] * scale
            axes = matrix[:3, [0, 2, 1]]
            record.update(origin_m=origin.tolist(), u_axis=axes[:, 0].tolist(),
                          v_axis=axes[:, 1].tolist(), normal_axis=axes[:, 2].tolist())
            vertices, faces = _triangles(ifcopenshell.geom.create_shape(settings, element))
            bounds, corners = _bounds(vertices[np.unique(faces)], origin, axes)
            record.update(bounds_local_m=bounds, corners_world_m=corners, geometry_status='loaded')
        except Exception:
            record['geometry_reason'] = 'element_geometry_or_placement_unavailable'

        for opening in openings:
            info = {'guid': opening.GlobalId, 'geometry_status': 'unavailable',
                    'measurement_basis': 'ifc_opening_geometry', 'bounds_in_element_axes_m': None}
            if record['origin_m'] is not None:
                try:
                    vertices, faces = _triangles(ifcopenshell.geom.create_shape(settings, opening))
                    bounds, corners = _bounds(vertices[np.unique(faces)], origin, axes)
                    info.update(geometry_status='loaded', bounds_in_element_axes_m=bounds,
                                corners_world_m=corners)
                except Exception:
                    pass
            record['filled_openings'].append(info)
        records.append(record)
    if hashlib.sha256(path.read_bytes()).hexdigest() != before:
        raise ValueError('IFC source changed while loading opening geometry')
    return records


def window_reference_at_wall(reference, plane):
    """Project the frame bounds onto the host interior plane, preserving size.

    Original IFC geometry remains recorded. The scan does not enter this
    operation: this explicitly changes the reference convention for an inside scan.
    """
    from geometry_engine.comparison.opening_comparison import reference_axes, rectangle_world
    axes,origin,bounds=reference_axes(reference)
    plane_origin,normal=map(lambda x:np.asarray(x,float),plane)
    normal=normal/np.linalg.norm(normal)
    if abs(axes[2]@normal)<1-1e-6:
        raise ValueError('Window frame plane is not parallel to the host wall')
    points=rectangle_world(bounds,axes,origin)
    distances=(points-plane_origin)@normal
    projected=points-distances[:,None]*normal
    local=(projected-origin)@axes.T
    new_bounds=bounds.copy();new_bounds[:,2]=local[:,2].mean()
    updated=dict(reference)
    updated['original_ifc_geometry']={k:reference[k] for k in
        ('origin_m','u_axis','v_axis','normal_axis','bounds_local_m','corners_world_m','measurement_basis')}
    updated.update(bounds_local_m=new_bounds.tolist(),corners_world_m=rectangle_world(new_bounds,axes,origin).tolist(),
        measurement_basis='ifc_window_frame_at_interior_wall',
        reference_projection={'policy':'interior_wall_surface','plane_origin_ifc_m':plane_origin.tolist(),
            'plane_normal_ifc':normal.tolist(),'reference_shift_m':float(-distances.mean()),
            'scan_geometry_changed':False})
    return updated


def load_boundary_references(ifc_path, interior_point=None):
    """Use door openings and window frames at the interior room surface.

    The door rectangle is at the opening face nearest the room interior when
    an interior seed is available. No door-leaf rotation influences its axes.
    Window frame definition still requires review (IFC may include a sill).
    """
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.placement
    import ifcopenshell.util.unit
    refs = load_opening_references(ifc_path)
    model = ifcopenshell.open(str(ifc_path))
    scale = float(ifcopenshell.util.unit.calculate_unit_scale(model))
    settings = ifcopenshell.geom.settings()
    settings.set('use-world-coords', True)
    settings.set('convert-back-units', False)
    for ref in refs:
        if ref['category'] == 'window':
            ref['measurement_target'] = 'outer_frame'
            ref['measurement_basis'] = 'ifc_window_geometry_frame_definition_requires_review'
            if interior_point is not None and ref['geometry_status']=='loaded':
                try:
                    from geometry_engine.comparison.bim_reference import interior_faces
                    from geometry_engine.common.surface_geometry import surface_plane
                    wall=model.by_guid(ref['host_wall_guid'])
                    if not wall.is_a('IfcWall'):raise ValueError('Missing host wall')
                    v,f=_triangles(ifcopenshell.geom.create_shape(settings,wall))
                    f=f[interior_faces(v,f,'wall',interior_point)]
                    if not len(f):raise ValueError('No interior wall face')
                    ref.update(window_reference_at_wall(ref,surface_plane(v,f)))
                except Exception:
                    ref.update(geometry_status='unavailable',geometry_reason='window_interior_wall_projection_unavailable')
            continue
        ref.update(measurement_target='door_opening', geometry_status='unavailable',
                   measurement_basis='ifc_opening_geometry', geometry_reason='missing_or_ambiguous_filled_opening')
        element = model.by_guid(ref['guid'])
        openings = [r.RelatingOpeningElement for r in getattr(element, 'FillsVoids', ())]
        if len(openings) != 1:
            continue
        try:
            opening = openings[0]
            matrix = np.asarray(ifcopenshell.util.placement.get_local_placement(opening.ObjectPlacement), float)
            if not np.allclose(matrix[:3,:3].T @ matrix[:3,:3], np.eye(3), atol=1e-7):
                raise ValueError('Non-rigid opening placement')
            origin = matrix[:3,3] * scale
            axes = matrix[:3,[0,2,1]]
            v,f = _triangles(ifcopenshell.geom.create_shape(settings, opening))
            bounds,corners = _bounds(v[np.unique(f)], origin, axes)
            bounds = np.asarray(bounds)
            # Reference depth is explicit; a room seed selects the interior face.
            depth = bounds[:,2].mean()
            if interior_point is not None:
                z = ((np.asarray(interior_point)-origin) @ axes)[2]
                depth = bounds[np.argmin(np.abs(bounds[:,2]-z)),2]
            bounds[:,2] = depth
            from geometry_engine.comparison.opening_comparison import rectangle_world
            ref.update(origin_m=origin.tolist(), u_axis=axes[:,0].tolist(),
                       v_axis=axes[:,1].tolist(), normal_axis=axes[:,2].tolist(),
                       bounds_local_m=bounds.tolist(),
                       corners_world_m=rectangle_world(bounds,axes.T,origin).tolist(),
                       geometry_status='loaded', opening_guid=opening.GlobalId,
                       measurement_basis='ifc_opening_interior_face' if interior_point is not None else 'ifc_opening_midplane')
            ref.pop('geometry_reason',None)
        except Exception:
            ref['geometry_reason']='opening_geometry_or_placement_unavailable'
    return refs
