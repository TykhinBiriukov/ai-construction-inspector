"""The initial inspection API accepts storage keys, not uploaded geometry."""
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints, computed_field, field_validator


class InspectionParameters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tolerance_m: Annotated[FiniteFloat, Field(gt=0, le=1)] = 0.1
    include_boundary_candidates: bool = False
    interior_point_ifc_m: tuple[FiniteFloat, FiniteFloat, FiniteFloat] | None = None


class InspectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')]
    capture_manifest_path: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    ifc_object_key: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    parameters: InspectionParameters = Field(default_factory=InspectionParameters)

    @field_validator('capture_manifest_path', 'ifc_object_key')
    @classmethod
    def validate_storage_key(cls, value, info):
        if (value != value.strip() or value.startswith('/') or any(c in value for c in '\\:?#')
                or any(ord(c) < 32 or ord(c) == 127 for c in value)
                or any(part in ('', '.', '..') for part in value.split('/'))):
            raise ValueError('Expected a relative storage object key without traversal, URL or query parameters')
        suffix = '.json' if info.field_name == 'capture_manifest_path' else '.ifc'
        if PurePosixPath(value).suffix.lower() != suffix:
            raise ValueError(f'Expected a {suffix} storage object')
        return value


class InspectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    job_id: UUID = Field(validation_alias='id')
    session_id: str
    capture_manifest_path: str
    ifc_object_key: str
    ifc_sha256: str | None
    status: Literal['queued', 'running', 'completed', 'failed', 'cancelled']
    current_stage: str | None
    pipeline_version: str
    parameters: InspectionParameters
    result_manifest_key: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @computed_field
    @property
    def status_url(self) -> str:
        return f'/inspections/{self.job_id}'
