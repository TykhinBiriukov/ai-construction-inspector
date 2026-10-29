"""Restricted IFC importer for the first rectangular-room test (not arbitrary BIM)."""
from pathlib import Path
import hashlib
import numpy as np
import ifcopenshell
import ifcopenshell.geom
from localization_service.openings import bounds_after, wall_for, select_entry


def load_room(path):
    path = Path(path)
    model = ifcopenshell.open(str(path))
    units=[u for u in model.by_type('IfcProject')[0].UnitsInContext.Units if getattr(u,'UnitType',None)=='LENGTHUNIT']
    if len(units)!=1 or not units[0].is_a('IfcSIUnit') or units[0].Name!='METRE' or units[0].Prefix is not None:
        raise ValueError('First test supports IFC metre units only')
    walls, doors, openings, spaces = [model.by_type(t) for t in ('IfcWall','IfcDoor','IfcOpeningElement','IfcSpace')]
    if len(walls) != 4 or not doors or not openings or len(spaces) != 1:
        raise ValueError('Requires one rectangular room, four walls and at least one door/opening')
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
    length, width = xmax-xmin, ymax-ymin
    if min(length,width,height) <= 0: raise ValueError('Invalid interior room bounds')
    # Full rectangular boundary required; rotated/partial walls remain unsupported.
    if any(b[0][1]>ymin+.06 or b[1][1]<ymax-.06 for b in xs) or any(b[0][0]>xmin+.06 or b[1][0]<xmax-.06 for b in ys):
        raise ValueError('Walls do not enclose a complete rectangle')
    transform=np.array([[1,0,0,-xmin],[0,0,1,-floor],[0,-1,0,ymin],[0,0,0,1]])
    apertures=[]
    for opening in openings:
        hosts=[rel.RelatingBuildingElement for rel in opening.VoidsElements]
        if hosts and (len(hosts)!=1 or hosts[0] not in walls):raise ValueError('Only wall openings in this room are supported')
        lo,hi=bounds_after(*bounds(opening),transform)
        axis,plane=wall_for(lo,hi,length,width)
        other=2 if axis==0 else 0
        if lo[1]<-.06 or hi[1]>height+.06 or hi[other]-lo[other]<.05:raise ValueError('Invalid opening bounds')
        fills=[rel.RelatedBuildingElement for rel in opening.HasFillings]
        kind=fills[0].is_a() if len(fills)==1 else 'IfcOpeningElement'
        apertures.append(dict(opening_id=opening.GlobalId,kind=kind,axis=axis,plane=plane,low=lo,high=hi))
    candidates=[]
    for door in sorted(doors,key=lambda d:d.GlobalId):
        linked=[rel.RelatingOpeningElement.GlobalId for rel in door.FillsVoids]
        if linked:
            matches=[o for o in apertures if o['opening_id'] in linked]
        else:
            # Legacy simple IFCs may omit IfcRelFillsElement; only a unique geometric match is accepted.
            lo,hi=bounds_after(*bounds(door),transform)
            matches=[o for o in apertures if all(hi[i]>=o['low'][i]-.06 and lo[i]<=o['high'][i]+.06 for i in range(3))]
        if len(matches)!=1:raise ValueError('Door must reference exactly one identifiable opening')
        opening=matches[0]
        if abs(opening['low'][1])>.05:raise ValueError('Entry doors must start at floor level')
        if any(d['opening_id']==opening['opening_id'] for d in candidates):raise ValueError('Multiple doors share one opening')
        opening['kind']='IfcDoor'
        candidates.append(dict(opening,element_id=door.GlobalId,label='Дверь '+str(len(candidates)+1),name=door.Name or ''))
    room={'import_version':2,'model_version':hashlib.sha256(path.read_bytes()).hexdigest(), 'room_id':spaces[0].GlobalId,
          'units':'m','T_bim_ifc':transform.tolist(),'length':float(length),'width':float(width),'height':float(height),
          'openings':apertures,'doors':candidates,'door_dimensions_approximate':True,
          'assumption':'First image is centered on the selected door from inside the room.'}
    return select_entry(room,candidates[0]['element_id'],confirmed=len(candidates)==1)


def load_display_meshes(path, room):
    """Actual triangulated IFC geometry, in exactly the solver's BIM frame."""
    model=ifcopenshell.open(str(path));settings=ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS,True)
    transform=np.array(room['T_bim_ifc']);meshes=[]
    for element in model.by_type('IfcWall')+model.by_type('IfcSlab')+model.by_type('IfcDoor')+model.by_type('IfcWindow'):
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
