"""Compact measured facts for downstream inspection consumers."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, FiniteFloat


class DimensionComparison(BaseModel):
    ifc: FiniteFloat | None
    scan: FiniteFloat | None
    difference: FiniteFloat | None = Field(description='Scan dimension minus IFC dimension, in metres.')


class ObjectDimensions(BaseModel):
    width: DimensionComparison
    height: DimensionComparison


class ObjectDisplacement(BaseModel):
    along_wall: FiniteFloat | None = Field(description='Signed shift along the IFC opening horizontal axis, in metres.')
    vertical: FiniteFloat | None = Field(description='Signed vertical shift in metres; positive is upward.')
    normal_to_wall: FiniteFloat | None = Field(description='Signed shift along the IFC opening normal axis, in metres.')


class ObjectQuality(BaseModel):
    boundary_supported_on_all_sides: bool | None
    absolute_accuracy_verified: bool | None
    issues: list[str]


class ObjectPhoto(BaseModel):
    frame_id: str
    rgb_url: str | None
    mask_url: str | None
    overlay_url: str | None


class ComparedObject(BaseModel):
    id: str
    type: Literal['door', 'window']
    scan_object_id: str | None
    ifc_guid: str | None
    match_status: Literal['matched', 'ambiguous', 'scan_object_not_matched', 'ifc_object_not_observed',
                          'ifc_reference_unavailable']
    dimensions: ObjectDimensions
    displacement: ObjectDisplacement
    quality: ObjectQuality
    photos: list[ObjectPhoto]


class SurfaceComparison(BaseModel):
    ifc_guid: str
    type: str
    status: str
    compared_area_m2: FiniteFloat | None
    p95_absolute_deviation: FiniteFloat | None
    area_fraction_outside_tolerance: FiniteFloat | None
    whole_element_verified: bool


class ResultArtifacts(BaseModel):
    point_cloud: str
    mesh: str
    segmented_mesh: str
    ifc: str


class InspectionResultResponse(BaseModel):
    schema_version: Literal['2.0']
    job_id: UUID
    session_id: str
    units: Literal['meter']
    difference_convention: Literal['scan_minus_ifc']
    tolerance: FiniteFloat
    links_expires_at: datetime
    summary: dict[str, int]
    objects: list[ComparedObject]
    surfaces: list[SurfaceComparison]
    artifacts: ResultArtifacts
