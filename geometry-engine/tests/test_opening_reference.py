"""Door bounds use the actual oriented geometry, including IFC unit conversion."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np

from geometry_engine.comparison.opening_reference import load_opening_references
from test_bim_reference import make_reference


ENGINE = Path(__file__).resolve().parents[1]


def make_door_reference(path, millimeters=False):
    import ifcopenshell
    import ifcopenshell.guid
    wall_guid = make_reference(path, millimeters=millimeters)
    model = ifcopenshell.open(str(path))
    factor = 1000. if millimeters else 1.
    wall = model.by_guid(wall_guid)
    context = model.by_type('IfcGeometricRepresentationContext')[0]
    def point(values):
        return model.create_entity('IfcCartesianPoint', tuple(float(v)*factor for v in values))
    direction = model.create_entity('IfcDirection', (float(np.sqrt(.5)), float(np.sqrt(.5)), 0.))
    position = model.create_entity('IfcAxis2Placement3D', point((.4, .02, .5)), None, direction)
    placement = model.create_entity('IfcLocalPlacement', wall.ObjectPlacement, position)
    def shape(width, depth, height, left):
        profile = model.create_entity('IfcRectangleProfileDef', 'AREA', None,
            model.create_entity('IfcAxis2Placement2D', point((left+width/2, depth/2))), width*factor, depth*factor)
        solid = model.create_entity('IfcExtrudedAreaSolid', profile,
            model.create_entity('IfcAxis2Placement3D', point((0,0,0))),
            model.create_entity('IfcDirection', (0.,0.,1.)), height*factor)
        body = model.create_entity('IfcShapeRepresentation', context, 'Body', 'SweptSolid', [solid])
        return model.create_entity('IfcProductDefinitionShape', None, None, [body])
    opening = model.create_entity('IfcOpeningElement', GlobalId=ifcopenshell.guid.new(),
        ObjectPlacement=placement, Representation=shape(1.2,.4,2.2,-.2))
    door = model.create_entity('IfcDoor', GlobalId=ifcopenshell.guid.new(), Name='Measured door',
        ObjectPlacement=placement, Representation=shape(1.,.07,2.,-.1), OverallWidth=1.2*factor, OverallHeight=2.2*factor)
    model.create_entity('IfcRelVoidsElement', GlobalId=ifcopenshell.guid.new(),
        RelatingBuildingElement=wall, RelatedOpeningElement=opening)
    model.create_entity('IfcRelFillsElement', GlobalId=ifcopenshell.guid.new(),
        RelatingOpeningElement=opening, RelatedBuildingElement=door)
    window = model.create_entity('IfcWindow', GlobalId=ifcopenshell.guid.new(), Name='Unavailable window',
        ObjectPlacement=placement, OverallWidth=1.5*factor, OverallHeight=1.*factor)
    model.write(str(path))
    return wall_guid, door.GlobalId, window.GlobalId


@unittest.skipUnless(importlib.util.find_spec('ifcopenshell'), 'IfcOpenShell not installed')
class OpeningReferenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=ENGINE)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_rotated_nested_placement_and_units_keep_actual_local_dimensions(self):
        for mm in (False, True):
            with self.subTest(mm=mm):
                path = self.root/f'rotated_{mm}.ifc'
                host, door_guid, _ = make_door_reference(path, mm)
                door = next(r for r in load_opening_references(path) if r['guid'] == door_guid)
                self.assertEqual(door['geometry_status'], 'loaded')
                self.assertEqual(door['host_wall_guid'], host)
                np.testing.assert_allclose(door['origin_m'], [10.98,22.4,.5], atol=1e-7)
                np.testing.assert_allclose(door['u_axis'], [-np.sqrt(.5),np.sqrt(.5),0], atol=1e-7)
                np.testing.assert_allclose(door['bounds_local_m'], [[-.1,0,0],[.9,2,.07]], atol=1e-7)
                self.assertAlmostEqual(door['OverallWidth_m'],1.2)
                # Nominal dimensions and opening dimensions are not the target bounds.
                opening = door['filled_openings'][0]
                np.testing.assert_allclose(opening['bounds_in_element_axes_m'], [[-.2,0,0],[1.,2.2,.4]], atol=1e-7)
                axes=np.column_stack([door[k] for k in ('u_axis','v_axis','normal_axis')])
                corners=(np.asarray(door['corners_world_m'])-door['origin_m'])@axes
                np.testing.assert_allclose(corners.min(axis=0),door['bounds_local_m'][0],atol=1e-7)
                np.testing.assert_allclose(corners.max(axis=0),door['bounds_local_m'][1],atol=1e-7)

    def test_missing_geometry_remains_unavailable_without_nominal_box_fallback(self):
        path=self.root/'missing.ifc'
        _,_,window_guid=make_door_reference(path)
        window=next(r for r in load_opening_references(path) if r['guid']==window_guid)
        self.assertEqual(window['geometry_status'],'unavailable')
        self.assertIsNone(window['bounds_local_m'])
        self.assertEqual(window['OverallWidth_m'],1.5)
        self.assertEqual(window['category'],'window')

    def test_declared_units_required(self):
        import ifcopenshell
        path=self.root/'units.ifc';make_door_reference(path)
        model=ifcopenshell.open(str(path));model.by_type('IfcProject')[0].UnitsInContext=None
        model.write(str(path))
        with self.assertRaisesRegex(ValueError,'declared units'):
            load_opening_references(path)


if __name__=='__main__':unittest.main()
