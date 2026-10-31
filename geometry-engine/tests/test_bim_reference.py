"""Exercise IFC unit/placement conversion and actual opening subtraction."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np

from geometry_engine.comparison.bim_reference import load_bim_reference


ENGINE = Path(__file__).resolve().parents[1]
HAS_IFC = importlib.util.find_spec("ifcopenshell") is not None


def make_reference(path, *, millimeters=False, opening=False, broken_wall=False):
    import ifcopenshell
    import ifcopenshell.guid
    model = ifcopenshell.file(schema="IFC4")
    factor = 1000. if millimeters else 1.
    def point(values):
        return model.create_entity("IfcCartesianPoint", tuple(float(v)*factor for v in values))
    def placement(position, parent=None, rotated=False):
        axis = model.create_entity("IfcAxis2Placement3D", point(position), None,
            model.create_entity("IfcDirection", (0., 1., 0.)) if rotated else None)
        return model.create_entity("IfcLocalPlacement", parent, axis)
    units = model.create_entity("IfcUnitAssignment", [model.create_entity("IfcSIUnit", None,
        "LENGTHUNIT", "MILLI" if millimeters else None, "METRE")])
    context = model.create_entity("IfcGeometricRepresentationContext", "Model", "Model", 3, 1e-6,
                                 model.create_entity("IfcAxis2Placement3D", point((0,0,0))), None)
    model.create_entity("IfcProject", GlobalId=ifcopenshell.guid.new(), Name="Unit test", UnitsInContext=units,
                        RepresentationContexts=[context])
    def representation(width, depth, height):
        profile = model.create_entity("IfcRectangleProfileDef", "AREA", None,
            model.create_entity("IfcAxis2Placement2D", point((width/2, depth/2))), width*factor, depth*factor)
        solid = model.create_entity("IfcExtrudedAreaSolid", profile,
            model.create_entity("IfcAxis2Placement3D", point((0,0,0))),
            model.create_entity("IfcDirection", (0.,0.,1.)), height*factor)
        body = model.create_entity("IfcShapeRepresentation", context, "Body", "SweptSolid", [solid])
        return model.create_entity("IfcProductDefinitionShape", None, None, [body])
    wall = model.create_entity("IfcWall", GlobalId=ifcopenshell.guid.new(), Name="Rotated wall",
        ObjectPlacement=placement((1,2,0),placement((10,20,0)),True),
        Representation=None if broken_wall else representation(2,.2,3), PredefinedType="STANDARD")
    if opening:
        cut = model.create_entity("IfcOpeningElement", GlobalId=ifcopenshell.guid.new(), Name="Opening",
            ObjectPlacement=placement((.75,-.1,.5),wall.ObjectPlacement),
            Representation=representation(.5,.4,1), PredefinedType="OPENING")
        model.create_entity("IfcRelVoidsElement", GlobalId=ifcopenshell.guid.new(), RelatingBuildingElement=wall,
                            RelatedOpeningElement=cut)
        # No explicit door geometry: metadata stays available without becoming a
        # comparison target or aborting a valid wall reference.
        model.create_entity("IfcDoor", GlobalId=ifcopenshell.guid.new(), Name="Door metadata",
                            OverallWidth=.5*factor, OverallHeight=1*factor)
    model.write(str(path))
    return wall.GlobalId


@unittest.skipUnless(HAS_IFC, "IfcOpenShell not installed")
class BimReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ENGINE)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_world_placement_and_millimeters_are_converted_exactly_once(self):
        for millimeters in (False, True):
            with self.subTest(millimeters=millimeters):
                path = self.root / f"units_{millimeters}.ifc"
                guid = make_reference(path,millimeters=millimeters)
                reference = load_bim_reference(path)
                np.testing.assert_allclose(reference.vertices.min(axis=0), [10.8,22.,0.],atol=1e-7)
                np.testing.assert_allclose(reference.vertices.max(axis=0), [11.,24.,3.],atol=1e-7)
                self.assertEqual(reference.elements[0]["guid"], guid)
                self.assertEqual(reference.elements[0]["category"], "wall")
                self.assertTrue(np.all(reference.face_element_ids == 0))
                self.assertEqual(reference.length_unit,"meter")
                self.assertAlmostEqual(reference.input_length_unit_scale,.001 if millimeters else 1.)

    def test_opening_is_subtracted_and_door_dimensions_are_metadata(self):
        path = self.root / "opening.ifc"
        make_reference(path,millimeters=True,opening=True)
        reference = load_bim_reference(path)
        triangles = reference.vertices[reference.faces]
        # Signed tetrahedra work for translated closed solids and all face subdivisions.
        volume = abs(np.einsum('ij,ij->i',triangles[:,0],np.cross(triangles[:,1],triangles[:,2])).sum())/6
        self.assertAlmostEqual(volume,1.1,places=6)
        door = next(r for r in reference.elements if r["category"] == "door")
        self.assertFalse(door["compare"])
        self.assertEqual(door["face_count"],0)
        self.assertAlmostEqual(door["OverallWidth_m"],.5)
        self.assertAlmostEqual(door["OverallHeight_m"],1.)

    def test_structural_conversion_failure_is_not_silently_skipped(self):
        path = self.root / "broken.ifc"
        make_reference(path,broken_wall=True)
        with self.assertRaisesRegex(ValueError,"structural IFC element"):
            load_bim_reference(path)

    def test_invalid_ifc_rejected(self):
        path = self.root / "invalid.ifc"
        path.write_text("not an IFC",encoding="utf-8")
        with self.assertRaises(ValueError):
            load_bim_reference(path)

    def test_missing_units_rejected(self):
        import ifcopenshell
        path = self.root / "missing_units.ifc"
        make_reference(path)
        model = ifcopenshell.open(str(path))
        model.by_type("IfcProject")[0].UnitsInContext = None
        model.write(str(path))
        with self.assertRaisesRegex(ValueError,"declared units"):
            load_bim_reference(path)


if __name__ == "__main__":
    unittest.main()
