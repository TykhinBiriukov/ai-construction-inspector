from pathlib import Path
import ifcopenshell
import ifcopenshell.guid
from localization_service.room_model import load_room,load_display_meshes

def make(path, length=5., width=4.):
 f=ifcopenshell.file(schema='IFC4')
 def point(xyz):return f.create_entity('IfcCartesianPoint',Coordinates=tuple(float(v) for v in xyz))
 def axis(xyz):return f.create_entity('IfcAxis2Placement3D',Location=point(xyz))
 context=f.create_entity('IfcGeometricRepresentationContext',ContextType='Model',CoordinateSpaceDimension=3,Precision=1e-5,WorldCoordinateSystem=axis((0,0,0)))
 units=f.create_entity('IfcUnitAssignment',Units=[f.create_entity('IfcSIUnit',UnitType='LENGTHUNIT',Name='METRE')])
 f.create_entity('IfcProject',GlobalId=ifcopenshell.guid.new(),Name='Synthetic cloud regression',RepresentationContexts=[context],UnitsInContext=units)
 def box(kind,name,low,high,**extra):
  profile=f.create_entity('IfcRectangleProfileDef',ProfileType='AREA',XDim=float(high[0]-low[0]),YDim=float(high[1]-low[1]))
  solid=f.create_entity('IfcExtrudedAreaSolid',SweptArea=profile,Position=axis(((low[0]+high[0])/2,(low[1]+high[1])/2,low[2])),ExtrudedDirection=f.create_entity('IfcDirection',DirectionRatios=(0.,0.,1.)),Depth=float(high[2]-low[2]))
  shape=f.create_entity('IfcShapeRepresentation',ContextOfItems=context,RepresentationIdentifier='Body',RepresentationType='SweptSolid',Items=[solid])
  rep=f.create_entity('IfcProductDefinitionShape',Representations=[shape])
  return f.create_entity(kind,GlobalId=ifcopenshell.guid.new(),Name=name,Representation=rep,**extra)
 walls=[]
 for name,low,high in [('west',(-.2,-.2,0),(0,width+.2,3)),('east',(length,-.2,0),(length+.2,width+.2,3)),('south',(0,-.2,0),(length,0,3)),('north',(0,width,0),(length,width+.2,3))]:walls.append(box('IfcWall',name,low,high))
 box('IfcSlab','floor',(-.2,-.2,-.2),(length+.2,width+.2,0),PredefinedType='FLOOR')
 box('IfcSpace','room',(0,0,0),(length,width,3))
 ids=[]
 for i,(low,high) in enumerate([((-.2,.3,0),(.01,1.3,2.1)),((length-.01,.5,0),(length+.2,1.5,2.1)),((.7,-.2,0),(1.7,.01,2.1)),((.9,width-.01,0),(1.9,width+.2,2.1))]):
  opening=box('IfcOpeningElement','opening',low,high)
  door=box('IfcDoor','entry '+str(i),low,high,OverallHeight=2.1,OverallWidth=1.)
  f.create_entity('IfcRelVoidsElement',GlobalId=ifcopenshell.guid.new(),RelatingBuildingElement=walls[i],RelatedOpeningElement=opening)
  f.create_entity('IfcRelFillsElement',GlobalId=ifcopenshell.guid.new(),RelatingOpeningElement=opening,RelatedBuildingElement=door)
  ids.append(door.GlobalId)
 opening=box('IfcOpeningElement','window opening',(-.2,2.,1.),(.01,3.,2.2))
 window=box('IfcWindow','window',(-.15,2.,1.),(-.05,3.,2.2),OverallHeight=1.2,OverallWidth=1.)
 f.create_entity('IfcRelVoidsElement',GlobalId=ifcopenshell.guid.new(),RelatingBuildingElement=walls[0],RelatedOpeningElement=opening)
 f.create_entity('IfcRelFillsElement',GlobalId=ifcopenshell.guid.new(),RelatingOpeningElement=opening,RelatedBuildingElement=window)
 f.write(str(path))
 return ids
