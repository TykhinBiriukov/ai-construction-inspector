"""Restricted IFC importer for the first rectangular-room test (not arbitrary BIM)."""
from pathlib import Path
import hashlib
import numpy as np
import ifcopenshell
import ifcopenshell.geom


def load_room(path):
    path = Path(path)
    model = ifcopenshell.open(str(path))
    units=[u for u in model.by_type('IfcProject')[0].UnitsInContext.Units if getattr(u,'UnitType',None)=='LENGTHUNIT']
    if len(units)!=1 or not units[0].is_a('IfcSIUnit') or units[0].Name!='METRE' or units[0].Prefix is not None:
        raise ValueError('First test supports IFC metre units only')
    walls, doors, openings, spaces = [model.by_type(t) for t in ('IfcWall','IfcDoor','IfcOpeningElement','IfcSpace')]
    if len(walls) != 4 or len(doors) != 1 or len(openings) != 1 or len(spaces) != 1:
        raise ValueError('First test requires one rectangular room, four walls and one door/opening')
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    def bounds(element):
        shape = ifcopenshell.geom.create_shape(settings, element)
        vertices = np.array(shape.geometry.verts).reshape(-1,3)
        return vertices.min(axis=0), vertices.max(axis=0)
    boxes = [bounds(w) for w in walls]
    xs = sorted([b for b in boxes if b[1][0]-b[0][0] < b[1][1]-b[0][1]], key=lambda b:b[0][0])
    ys = sorted([b for b in boxes if b[1][1]-b[0][1] < b[1][0]-b[0][0]], key=lambda b:b[0][1])
    if len(xs)!=2 or len(ys)!=2:
        raise ValueError('Room walls must be axis aligned for this test')
    xmin, xmax = xs[0][1][0], xs[1][0][0]
    ymin, ymax = ys[0][1][1], ys[1][0][1]
    floors = [s for s in model.by_type('IfcSlab') if s.PredefinedType == 'FLOOR']
    if len(floors) != 1: raise ValueError('Expected one floor')
    floor = bounds(floors[0])[1][2]
    height = min(b[1][2] for b in boxes)-floor
    low, high = bounds(openings[0])
    if not low[0] <= xmin <= high[0] or abs(low[2]-floor) > .05:
        raise ValueError('First test requires the selected door in the min-X wall at floor level')
    length, width = xmax-xmin, ymax-ymin
    if min(length,width,height) <= 0: raise ValueError('Invalid interior room bounds')
    transform = [[1,0,0,-xmin],[0,0,1,-floor],[0,-1,0,ymin],[0,0,0,1]]
    return {'model_version':hashlib.sha256(path.read_bytes()).hexdigest(), 'room_id':spaces[0].GlobalId,
            'entry_element_id':doors[0].GlobalId, 'bim_frame_id':'local-meters-y-up',
            'units':'m', 'T_bim_ifc':np.array(transform).tolist(),
            'length':float(length), 'width':float(width), 'height':float(height),
            'door_offset':float(low[1]-ymin), 'door_width':float(high[1]-low[1]),
            'door_height':float(high[2]-floor), 'door_dimensions_approximate':True,
            'assumption':'First image is centered on the selected door from inside the room.'}


def load_display_meshes(path, room):
    """Actual triangulated IFC geometry, in exactly the solver's BIM frame."""
    model=ifcopenshell.open(str(path));settings=ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS,True)
    transform=np.array(room['T_bim_ifc']);meshes=[]
    for element in model.by_type('IfcWall')+model.by_type('IfcSlab')+model.by_type('IfcDoor'):
        shape=ifcopenshell.geom.create_shape(settings,element)
        vertices=np.array(shape.geometry.verts).reshape(-1,3)
        vertices=vertices@transform[:3,:3].T+transform[:3,3]
        triangles=np.array(shape.geometry.faces,dtype=int).reshape(-1,3)
        normals=np.zeros_like(vertices)
        face_normals=np.cross(vertices[triangles[:,1]]-vertices[triangles[:,0]],vertices[triangles[:,2]]-vertices[triangles[:,0]])
        for corner in range(3):np.add.at(normals,triangles[:,corner],face_normals)
        normals/=np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-12)
        meshes.append({'element_id':element.GlobalId,'kind':element.is_a(),
                       'vertices':vertices.tolist(),'normals':normals.tolist(),'triangles':triangles.reshape(-1).tolist()})
    return meshes
