from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import IDENTIFIER_PATTERN


Identifier = Annotated[str, Field(pattern=IDENTIFIER_PATTERN)]


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InspectionInput(APIModel):
    schema_version: Literal["0.1"]
    request_id: UUID
    project_id: Identifier
    room_id: Identifier
    prompt: Annotated[str, Field(min_length=1, max_length=8000)]
    model_is_synthetic: bool = False

    @field_validator("prompt")
    @classmethod
    def nonempty_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Prompt cannot be blank")
        return value


class CreateSessionRequest(APIModel):
    schema_version: Literal["0.1"]
    request_id: UUID
    device_id: Identifier
    room_id: Identifier
    capture_origin: Literal["device", "synthetic"]
    input_mode: Literal["live_capture", "recorded_capture", "synthetic_fixture"]
    parent_session_id: UUID | None = None

    @model_validator(mode="after")
    def matching_origin(self) -> "CreateSessionRequest":
        if (self.capture_origin == "synthetic") != (self.input_mode == "synthetic_fixture"):
            raise ValueError("Synthetic origin and synthetic_fixture must be used together")
        return self


class QuickSessionRequest(CreateSessionRequest):
    room_id: Identifier = "room-204"
    capture_origin: Literal["device", "synthetic"] = "device"
    input_mode: Literal["live_capture", "recorded_capture", "synthetic_fixture"] = "live_capture"
    prompt: Annotated[str, Field(min_length=1, max_length=8000)] = "Record the selected room."


class DeviceInfo(APIModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    os_version: Annotated[str, Field(min_length=1, max_length=100)]
    app_version: Annotated[str, Field(min_length=1, max_length=100)]


class AcknowledgeRequest(APIModel):
    schema_version: Literal["0.1"]
    operation_id: UUID


class CaptureStartRequest(AcknowledgeRequest):
    device: DeviceInfo


class BIMModel(APIModel):
    filename: str
    media_type: str
    byte_length: int
    sha256: str
    status: Literal["stored_unvalidated"]
    download_url: str


class Inspection(APIModel):
    schema_version: Literal["0.1"]
    inspection_id: UUID
    project_id: str
    room_id: str
    model_version: str
    model_is_synthetic: bool
    policy_version: str
    prompt: str
    created_at: str
    bim_model: BIMModel | None = None
    inspection_kind: Literal["bim_inspection", "capture_only"] = "bim_inspection"


class CaptureStorage(APIModel):
    storage_id: str
    key_prefix: str
    provider: Literal["supabase"] | None = None
    project_url: str | None = None
    bucket: str | None = None
    folder_status: Literal["pending", "ready"] | None = None


class CaptureTask(APIModel):
    text: Annotated[str, Field(min_length=1, max_length=8000)]
    source: Literal["operator_request"]


class Session(APIModel):
    schema_version: Literal["0.1"]
    session_id: UUID
    inspection_id: UUID
    project_id: str
    room_id: str
    model_version: str
    model_is_synthetic: bool
    policy_version: str
    device_id: str
    capture_origin: Literal["device", "synthetic"]
    input_mode: Literal["live_capture", "recorded_capture", "synthetic_fixture"]
    created_at: str
    parent_session_id: UUID | None
    revision: int
    capture_state: Literal["assigned", "capturing", "submitted", "sealed", "cancelled"]
    processing_state: Literal[
        "idle", "validating", "queued_geometry", "building_geometry",
        "evaluating", "completed", "failed", "cancelled",
    ]
    storage: CaptureStorage
    device_acknowledged_at: str | None
    capture_task: CaptureTask | None = None
    completion: dict | None = None
    source: dict | None = None
    geometry_job: dict | None = None
    display_view_id: str | None = None
    result: dict | None = None
    error: dict | None = None
    geometry_result: dict | None = None
    processing_scope: Literal["geometry_only_v1"] = "geometry_only_v1"


class AssignmentList(APIModel):
    assignments: list[Session]
    next_cursor: str | None


class Acknowledgement(APIModel):
    session_id: UUID
    operation_id: UUID
    acknowledged_at: str


class CaptureStartReceipt(APIModel):
    session_id: UUID
    operation_id: UUID
    capture_state: Literal["capturing"]


class CaptureReference(APIModel):
    storage_id: Identifier
    manifest_key: Annotated[str, Field(min_length=1, max_length=1024)]
    manifest_version_id: Annotated[str, Field(min_length=1, max_length=1024)]
    manifest_byte_length: Annotated[int, Field(gt=0, strict=True)]
    manifest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @field_validator("manifest_version_id")
    @classmethod
    def immutable_version(cls, value: str) -> str:
        if not value.strip() or value == "null":
            raise ValueError("An immutable S3 object version is required")
        return value


class CompleteCaptureRequest(APIModel):
    schema_version: Literal["0.1"]
    completion_id: UUID
    capture_ref: "CaptureReference | SupabaseCaptureReference | SupabaseArchiveReference"


class SupabaseCaptureReference(APIModel):
    format: Literal["supabase_capture_v1"]
    storage_id: Identifier
    manifest_key: Annotated[str, Field(min_length=1, max_length=1024)]
    manifest_byte_length: Annotated[int, Field(gt=0, strict=True)]
    manifest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SupabaseArchiveReference(APIModel):
    format: Literal["supabase_capture_zip_v1"]
    storage_id: Identifier
    archive_key: Annotated[str, Field(min_length=1, max_length=1024)]
    archive_byte_length: Annotated[int, Field(gt=0, strict=True)]
    archive_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class UploadObject(APIModel):
    observation_id: UUID | None = None
    name: Literal["frame.json", "rgb.jpg", "depth.f32", "confidence.u8", "capture-manifest.json", "capture.zip"]
    media_type: Literal["application/json", "image/jpeg", "application/octet-stream", "application/zip"]
    byte_length: Annotated[int, Field(gt=0, strict=True)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class UploadPlanRequest(APIModel):
    schema_version: Literal["0.1"]
    request_id: UUID
    kind: Literal["observation_files", "capture_manifest", "capture_archive"]
    completion_id: UUID | None = None
    objects: Annotated[list[UploadObject], Field(min_length=1, max_length=50)]

    @model_validator(mode="after")
    def validate_objects(self) -> "UploadPlanRequest":
        if self.kind in ("capture_manifest", "capture_archive"):
            expected_name = "capture.zip" if self.kind == "capture_archive" else "capture-manifest.json"
            if self.completion_id is None or len(self.objects) != 1 or self.objects[0].name != expected_name or self.objects[0].observation_id is not None:
                raise ValueError("Manifest/archive upload requires one matching object and completion_id")
        elif self.completion_id is not None or any(item.observation_id is None or item.name in ("capture-manifest.json", "capture.zip") for item in self.objects):
            raise ValueError("Observation upload objects require observation_id and no completion_id")
        seen = set()
        for item in self.objects:
            identity = (item.observation_id, item.name)
            if identity in seen:
                raise ValueError("Duplicate upload object")
            seen.add(identity)
            expected = ("application/zip" if item.name == "capture.zip" else "application/json" if item.name.endswith("json")
                        else "image/jpeg" if item.name == "rgb.jpg" else "application/octet-stream")
            if item.media_type != expected:
                raise ValueError("Media type does not match filename")
        return self


class CompletionStatus(APIModel):
    completion_id: UUID
    status: Literal["pending", "rejected", "accepted"]
    error: dict | None


class CompletionReceipt(APIModel):
    session_id: UUID
    completion: CompletionStatus


class ErrorEnvelope(APIModel):
    code: str
    message: str
    retryable: bool
    details: dict
