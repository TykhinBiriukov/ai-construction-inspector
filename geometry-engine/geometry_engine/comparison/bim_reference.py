"""Read real IFC geometry in metres while retaining each element's identity.

Uses IfcOpenShell's geometry kernel, including placements, unit conversion and
opening subtraction. No room dimensions or alignment are inferred by this module.
References:
https://docs.ifcopenshell.org/ifcopenshell-python/geometry_processing.html
https://docs.ifcopenshell.org/ifcopenshell/geometry_settings.html
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class BimReference:
    vertices: np.ndarray
    faces: np.ndarray
    face_element_ids: np.ndarray
    elements: list[dict]
    source_sha256: str
    schema: str
    input_length_unit_scale: float
    length_unit: str = "meter"
    up_axis: str = "z"


def _category(element):
    if element.is_a("IfcWall"):
        return "wall", True
    if element.is_a("IfcSlab"):
        predefined = getattr(element, "PredefinedType", None)
        return ("floor", True) if predefined in {"FLOOR", "BASESLAB"} else ("slab", False)
    return ("door", False) if element.is_a("IfcDoor") else ("window", False)


def _triangles(shape):
    vertices = np.asarray(shape.geometry.verts, dtype=np.float64).reshape(-1, 3)
    faces = np.asarray(shape.geometry.faces, dtype=np.int64).reshape(-1, 3)
    if (not len(vertices) or not len(faces) or not np.isfinite(vertices).all()
            or np.any(faces < 0) or np.any(faces >= len(vertices))):
        raise ValueError("Invalid or empty triangulation")
    triangles = vertices[faces]
    area2 = np.linalg.norm(np.cross(triangles[:, 1]-triangles[:, 0],
                                    triangles[:, 2]-triangles[:, 0]), axis=1)
    faces = faces[area2 > 1e-12]
    if not len(faces):
        raise ValueError("Triangulation contains no nondegenerate faces")
    return vertices, faces


def load_bim_reference(path) -> BimReference:
    """Load wall/floor triangles plus metadata for other slabs, doors and windows.

    Every face_element_ids entry indexes ``elements``. Only elements marked
    ``compare=True`` contribute faces. FLOOR and BASESLAB qualify; ambiguous or
    roof slabs stay metadata-only. Doors/windows keep bounds and nominal sizes,
    but are not silently included in the wall/floor distance target.

    IFC geometry is returned in its world placement, Z-up, SI metres. Georeferenced
    map conversions are not applied on top of that placement. Placement and units
    must subsequently be reconciled with the scan by the registration stage.
    Structural conversion failures abort rather than produce a partial reference.
    """
    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"IFC reference does not exist: {source}")
    if source.suffix.lower() != ".ifc":
        raise ValueError("Expected an .ifc STEP file")
    try:
        import ifcopenshell
        import ifcopenshell.geom
        import ifcopenshell.util.unit
    except ImportError as error:
        raise RuntimeError("IfcOpenShell is required to load BIM references") from error
    with source.open("rb") as stream:
        source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    try:
        model = ifcopenshell.open(str(source))
        projects = model.by_type("IfcProject")
        if len(projects) != 1 or projects[0].UnitsInContext is None:
            raise ValueError("Expected one IFC project with declared length units")
        declared = [unit for unit in projects[0].UnitsInContext.Units
                    if getattr(unit, "UnitType", None) == "LENGTHUNIT"]
        if len(declared) != 1:
            raise ValueError("Expected exactly one IFC length unit")
        unit_scale = float(ifcopenshell.util.unit.calculate_unit_scale(model))
        if not np.isfinite(unit_scale) or unit_scale <= 0:
            raise ValueError("Invalid IFC length unit scale")
    except Exception as error:
        raise ValueError("Could not read an IFC project with valid declared units") from error
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    settings.set("convert-back-units", False)
    # Opening subtraction is intentionally kept ON (the default).
    settings.set("disable-opening-subtractions", False)
    elements = sorted({element.id(): element for kind in ("IfcWall", "IfcSlab", "IfcDoor", "IfcWindow")
                       for element in model.by_type(kind)}.values(), key=lambda element: element.id())
    records, all_vertices, all_faces, owners = [], [], [], []
    vertex_offset = 0
    for element in elements:
        category, compare = _category(element)
        record = {"element_id": len(records), "ifc_step_id": element.id(),
                  "guid": element.GlobalId, "ifc_class": element.is_a(),
                  "name": element.Name or "", "category": category,
                  "predefined_type": getattr(element, "PredefinedType", None),
                  "compare": compare, "face_count": 0, "bounds_m": None}
        for field in ("OverallHeight", "OverallWidth"):
            value = getattr(element, field, None)
            if value is not None:
                numeric = float(value)*unit_scale
                if np.isfinite(numeric) and numeric > 0:
                    record[field+"_m"] = numeric
        try:
            shape = ifcopenshell.geom.create_shape(settings, element)
            vertices, faces = _triangles(shape)
            record["bounds_m"] = [vertices.min(axis=0).tolist(), vertices.max(axis=0).tolist()]
            record["geometry_status"] = "loaded"
        except Exception as error:
            if compare:
                raise ValueError(f"Could not triangulate structural IFC element {element.GlobalId}") from error
            record["geometry_status"] = "unavailable"
            records.append(record)
            continue
        if compare:
            if vertex_offset+len(vertices) > np.iinfo(np.int32).max:
                raise ValueError("IFC geometry exceeds supported index size")
            all_vertices.append(vertices)
            all_faces.append((faces+vertex_offset).astype(np.int32))
            owners.append(np.full(len(faces), len(records), dtype=np.int32))
            record["face_count"] = len(faces)
            vertex_offset += len(vertices)
        records.append(record)
    if not all_faces:
        raise ValueError("IFC has no usable walls or FLOOR/BASESLAB geometry")
    with source.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != source_hash:
            raise ValueError("IFC source changed while loading geometry")
    return BimReference(np.concatenate(all_vertices), np.concatenate(all_faces),
                        np.concatenate(owners), records, source_hash, model.schema, unit_scale)


def interior_faces(vertices, faces, category, point):
    """Explicit room-side surface selection; point must lie inside the scanned room."""
    t=np.asarray(vertices)[faces];c=t.mean(1)
    n=np.cross(t[:,1]-t[:,0],t[:,2]-t[:,0]);n/=np.maximum(np.linalg.norm(n,axis=1)[:,None],1e-15)
    toward=np.asarray(point)-c
    facing=np.einsum('ij,ij->i',n,toward)>1e-6
    if category=='floor':return facing&(n[:,2]>.9)
    if category=='wall':
        # Keep main wall sides; exclude thickness returns and opening reveals.
        area=np.linalg.norm(np.cross(t[:,1]-t[:,0],t[:,2]-t[:,0]),axis=1)
        horizontal=abs(n[:,2])<.2
        if not horizontal.any():return np.zeros(len(faces),bool)
        cov=(n[horizontal].T*area[horizontal])@n[horizontal]
        axis=np.linalg.eigh(cov)[1][:,-1]
        return facing&horizontal&(abs(n@axis)>.95)
    return np.ones(len(faces),bool)
