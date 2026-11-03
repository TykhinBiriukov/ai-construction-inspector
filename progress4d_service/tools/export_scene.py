"""Export IFC geometry for synthetic rendering; never used by photogrammetry."""
import argparse
import hashlib
import json
from pathlib import Path
import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.unit


def main():
    p=argparse.ArgumentParser()
    p.add_argument('ifc',type=Path)
    p.add_argument('output',type=Path)
    a=p.parse_args()
    model=ifcopenshell.open(a.ifc)
    settings=ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS,True)
    elements=[]
    for obj in model.by_type('IfcElement'):
        if obj.is_a('IfcOpeningElement') or not obj.Representation:
            continue
        shape=ifcopenshell.geom.create_shape(settings,obj)
        vertices=shape.geometry.verts
        faces=shape.geometry.faces
        elements.append(dict(id=obj.GlobalId,name=obj.Name,ifc_class=obj.is_a(),
            vertices=[list(vertices[i:i+3]) for i in range(0,len(vertices),3)],
            faces=[list(faces[i:i+3]) for i in range(0,len(faces),3)]))
    # This generator deliberately targets the supplied room, not arbitrary IFCs.
    payload=dict(source_sha256=hashlib.sha256(a.ifc.read_bytes()).hexdigest(),
                 units='m',dimensions=[7.219,3.331,2.775],elements=elements,
                 source_task_count=len(model.by_type('IfcTask')),
                 source_schedule_count=len(model.by_type('IfcWorkSchedule')))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(payload))
    print(json.dumps({k:v for k,v in payload.items() if k!='elements'}))


if __name__=='__main__': main()
