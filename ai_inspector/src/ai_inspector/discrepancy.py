"""Versioned candidate/evidence/review contracts. No detection or measurement engine."""
from __future__ import annotations

import hashlib
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .errors import APIError
from .storage import canonical_json

ID = Annotated[str, StringConstraints(min_length=1, max_length=150, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:$-]*$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=1000, pattern=r"\S")]
Task = Annotated[str, StringConstraints(min_length=1, max_length=2000, pattern=r"\S")]
Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Number = Annotated[float, Field(allow_inf_nan=False, strict=True)]
AssetKind = Literal["photo", "segmentation", "point_cloud", "ifc", "measurement", "reference"]
CATEGORIES = ("ifc_only", "scan_only", "geometry_mismatch")
MAX_CASES = 12
MAX_ASSETS = 6
MAX_PAYLOAD = 1_600_000


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class EvidenceArguments(Contract):
    asset_id: ID


class SourceBinding(Contract):
    session_id: UUID | None = None
    manifest_sha256: Hash | None = None
    geometry_job_id: UUID | None = None
    geometry_revision: Annotated[int, Field(gt=0)] | None = None
    model_version: ID | None = None
    model_sha256: Hash | None = None
    segmentation_sha256: Hash | None = None
    origin: Literal["operator_supplied", "recorded", "synthetic", "geometry"] = "operator_supplied"


class AssetRef(Contract):
    asset_id: ID
    file_id: ID
    sha256: Hash
    kind: AssetKind


class Measurement(Contract):
    measurement_id: ID
    parameter: ID
    observed: Number
    expected: Number | None = None
    unit: Literal["m", "rad"]
    interval: tuple[Number, Number] | None = None
    acceptance_interval: tuple[Number, Number] | None = None
    extent: Literal["full_target", "visible_fragment", "unknown"] = "unknown"
    coordinate_frame_id: ID | None = None
    requires_alignment: bool = False
    alignment_verified: bool = False
    algorithm: Text
    algorithm_version: ID
    source_asset_ids: Annotated[list[ID], Field(min_length=1, max_length=MAX_ASSETS)]

    @model_validator(mode="after")
    def intervals(self):
        if self.interval is not None and not self.interval[0] <= self.observed <= self.interval[1]:
            raise ValueError("Measurement interval must contain its observed value")
        if self.acceptance_interval is not None and self.acceptance_interval[0] > self.acceptance_interval[1]:
            raise ValueError("Acceptance interval is reversed")
        return self


class MeasurementEvidence(Contract):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["measurement_evidence"] = "measurement_evidence"
    measurements: Annotated[list[Measurement], Field(min_length=1, max_length=96)]


class ReferenceFacts(Contract):
    """Reported upstream facts, not facts independently established by the LLM."""
    expected_region_observed: bool = False
    reference_scope_checked: bool = False
    source_asset_ids: Annotated[list[ID], Field(max_length=MAX_ASSETS)] = Field(default_factory=list)
    limitations: Annotated[list[Text], Field(max_length=8)] = Field(default_factory=list)


class Candidate(Contract):
    case_id: ID
    detection_reason: Text
    object_type: Text
    ifc_id: ID | None = None
    segment_id: ID | None = None
    location: Annotated[str, StringConstraints(max_length=500)] = ""
    evidence_ids: Annotated[list[ID], Field(min_length=1, max_length=MAX_ASSETS)]
    mismatch_types: Annotated[list[Literal["displacement", "shape_or_dimensions"]], Field(max_length=2)] = Field(default_factory=list)
    measurements: Annotated[list[Measurement], Field(max_length=8)] = Field(default_factory=list)
    comparison_basis: Literal["metric", "visual_shape"] = "metric"
    reference_facts: ReferenceFacts = Field(default_factory=ReferenceFacts)


class Candidates(Contract):
    ifc_only: Annotated[list[Candidate], Field(max_length=MAX_CASES)]
    scan_only: Annotated[list[Candidate], Field(max_length=MAX_CASES)]
    geometry_mismatch: Annotated[list[Candidate], Field(max_length=MAX_CASES)]

    def rows(self):
        return [(category, item) for category in CATEGORIES for item in getattr(self, category)]


class InspectionRequest(Contract):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["inspection_request"] = "inspection_request"
    request_id: UUID
    project_id: ID
    task: Task
    source: SourceBinding = Field(default_factory=SourceBinding)
    assets: Annotated[list[AssetRef], Field(min_length=1, max_length=MAX_ASSETS)]
    cases: Candidates

    @model_validator(mode="after")
    def scope(self):
        rows = self.cases.rows()
        if not 1 <= len(rows) <= MAX_CASES or len({c.case_id for _, c in rows}) != len(rows):
            raise ValueError("A batch needs 1–12 unique cases")
        assets = {a.asset_id for a in self.assets}
        if len(assets) != len(self.assets):
            raise ValueError("Asset IDs must be unique")
        for category, case in rows:
            refs = set(case.evidence_ids) | set(case.reference_facts.source_asset_ids)
            ids = [m.measurement_id for m in case.measurements]
            if len(ids) != len(set(ids)):
                raise ValueError("Measurement IDs must be unique in each case")
            for m in case.measurements:
                refs.update(m.source_asset_ids)
            if not refs <= assets:
                raise ValueError("A case refers to an unknown asset")
            if category != "geometry_mismatch" and case.mismatch_types:
                raise ValueError("Geometry subtypes belong only to geometry_mismatch")
            if case.comparison_basis == "visual_shape" and (category != "geometry_mismatch" or case.mismatch_types != ["shape_or_dimensions"]):
                raise ValueError("Visual shape assessment requires an explicit shape-only geometric case")
        return self


class Evidence(Contract):
    asset_id: ID
    file_id: ID
    sha256: Hash
    kind: AssetKind
    media_type: Literal["image/jpeg", "image/png", "image/webp", "application/json", "text/plain", "application/octet-stream"]
    byte_length: Annotated[int, Field(ge=0)]
    available: bool
    text: Annotated[str, StringConstraints(max_length=12000)] | None = None
    truncated: bool = False
    image_data: Annotated[str, StringConstraints(max_length=270000)] | None = None
    preview_sha256: Hash | None = None
    preview_transform: Annotated[str, StringConstraints(max_length=300)] | None = None
    limitation: Annotated[str, StringConstraints(max_length=500)] | None = None
    reported_measurements: Annotated[list[Measurement], Field(max_length=96)] = Field(default_factory=list)

    @model_validator(mode="after")
    def image(self):
        if self.image_data is not None:
            import base64
            prefix = "data:image/jpeg;base64,"
            if not self.image_data.startswith(prefix):
                raise ValueError("Evidence previews must be inline JPEG")
            try:
                raw = base64.b64decode(self.image_data[len(prefix):], validate=True)
            except ValueError:
                raise ValueError("Invalid image preview") from None
            if not raw.startswith(b"\xff\xd8\xff") or hashlib.sha256(raw).hexdigest() != self.preview_sha256:
                raise ValueError("Preview identity mismatch")
        if self.available and self.text is None and self.image_data is None and not self.reported_measurements:
            raise ValueError("Available evidence needs actual readable content")
        if not self.available and not self.limitation:
            raise ValueError("Unavailable evidence needs a limitation")
        return self


class EvidencePackage(Contract):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["evidence_package"] = "evidence_package"
    inspection: InspectionRequest
    request_sha256: Hash
    evidence: Annotated[list[Evidence], Field(min_length=1, max_length=MAX_ASSETS)]

    @model_validator(mode="after")
    def bindings(self):
        if self.request_sha256 != fingerprint(self.inspection):
            raise ValueError("Inspection request fingerprint mismatch")
        actual = {e.asset_id: e for e in self.evidence}
        if len(actual) != len(self.evidence) or set(actual) != {a.asset_id for a in self.inspection.assets}:
            raise ValueError("Evidence package must account for every requested asset")
        for asset in self.inspection.assets:
            e = actual[asset.asset_id]
            if (e.file_id, e.sha256, e.kind) != (asset.file_id, asset.sha256, asset.kind):
                raise ValueError("Evidence belongs to a different source")
        if len(canonical_json(self.model_dump(mode="json")).encode()) > MAX_PAYLOAD:
            raise ValueError("Evidence batch exceeds its transport budget")
        return self


class CaseDecision(Contract):
    case_id: ID
    detection_reason: Text
    status: Literal["confirmed", "rejected", "needs_more_data"]
    decision_reason: Text
    evidence_used: Annotated[list[ID], Field(max_length=MAX_ASSETS)]
    measurement_ids: Annotated[list[ID], Field(max_length=8)] = Field(default_factory=list)
    limitations: Annotated[list[Text], Field(max_length=8)]
    data_requests: Annotated[list[Text], Field(max_length=8)]


class Decisions(Contract):
    ifc_only: list[CaseDecision]
    scan_only: list[CaseDecision]
    geometry_mismatch: list[CaseDecision]

    def rows(self):
        return [(category, item) for category in CATEGORIES for item in getattr(self, category)]


class ReviewResult(Contract):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["review_result"] = "review_result"
    request_id: UUID
    project_id: ID
    package_sha256: Hash
    assessment_scope: Literal["supplied_evidence_review"] = "supplied_evidence_review"
    cases: Decisions


class CaseContext(Contract):
    object_type: Text
    ifc_id: ID | None = None
    segment_id: ID | None = None
    location: Annotated[str, StringConstraints(max_length=500)] = ""
    mismatch_types: Annotated[list[Literal["displacement", "shape_or_dimensions"]], Field(max_length=2)]
    evidence_ids: Annotated[list[ID], Field(min_length=1, max_length=MAX_ASSETS)]


class EvidenceStatus(Contract):
    available: bool
    truncated: bool
    limitation: Annotated[str, StringConstraints(max_length=500)] | None = None


class InspectionReport(Contract):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["inspection_report"] = "inspection_report"
    request_id: UUID
    project_id: ID
    chat_id: UUID
    task: Task
    request_sha256: Hash
    package_sha256: Hash
    reviewer_task_id: UUID
    review_sha256: Hash
    source: SourceBinding
    assessment_scope: Literal["supplied_evidence_review"] = "supplied_evidence_review"
    cases: Decisions
    case_context: Annotated[dict[ID, CaseContext], Field(max_length=MAX_CASES)]
    assets: Annotated[list[AssetRef], Field(min_length=1, max_length=MAX_ASSETS)]
    asset_status: Annotated[dict[ID, EvidenceStatus], Field(max_length=MAX_ASSETS)]
    measurements: dict[str, list[Measurement]]


def fingerprint(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_json(model, value):
    from .a2a_protocol import decode_json
    try:
        if not isinstance(value, str) or len(value.encode()) > MAX_PAYLOAD:
            raise ValueError
        return model.model_validate(decode_json(value))
    except (ValueError, TypeError, RecursionError):
        raise APIError(502, "invalid_inspection_output", "The inspection JSON or its field types are invalid") from None


def validate_review(result, package):
    request = package.inspection
    if (result.request_id, result.project_id, result.package_sha256) != (request.request_id, request.project_id, fingerprint(package)):
        raise APIError(502, "invalid_inspection_output", "The review refers to different inputs")
    expected = {(cat, c.case_id): c for cat, c in request.cases.rows()}
    rows = result.cases.rows()
    if len(rows) != len(expected) or {(cat, c.case_id) for cat, c in rows} != set(expected):
        raise APIError(502, "invalid_inspection_output", "Every input case must have exactly one result in its original category")
    available = {e.asset_id for e in package.evidence if e.available}
    complete = {e.asset_id for e in package.evidence if e.available and not e.truncated}
    assets = {e.asset_id: e for e in package.evidence}
    for category, decision in rows:
        candidate = expected[category, decision.case_id]
        allowed = set(candidate.evidence_ids) | set(candidate.reference_facts.source_asset_ids)
        for m in candidate.measurements:
            allowed.update(m.source_asset_ids)
        measurements = {m.measurement_id: m for m in candidate.measurements}
        if (decision.detection_reason != candidate.detection_reason or not set(decision.evidence_used) <= allowed & available
                or len(set(decision.evidence_used)) != len(decision.evidence_used)
                or not set(decision.measurement_ids) <= set(measurements)
                or len(set(decision.measurement_ids)) != len(decision.measurement_ids)):
            raise APIError(502, "invalid_inspection_output", "A review changed the case reason or invented evidence/measurements")
        if decision.status != "needs_more_data" and not decision.evidence_used:
            raise APIError(502, "invalid_inspection_output", "A conclusive decision requires accessible evidence")
        if decision.status == "needs_more_data" and (not decision.limitations or not decision.data_requests):
            raise APIError(502, "invalid_inspection_output", "An inconclusive case needs limitations and a concrete data request")
        if decision.status == "confirmed":
            facts = candidate.reference_facts
            if category in ("ifc_only", "scan_only"):
                sufficient = facts.expected_region_observed if category == "ifc_only" else facts.reference_scope_checked
                if not sufficient or not facts.source_asset_ids or not set(facts.source_asset_ids) <= set(decision.evidence_used) & complete:
                    raise APIError(502, "invalid_inspection_output", "Absence/nonmatching claims require scoped supporting evidence")
            if category == "geometry_mismatch":
                if candidate.comparison_basis == "visual_shape":
                    kinds = {assets[aid].kind for aid in decision.evidence_used}
                    if decision.measurement_ids or not {"photo", "reference"} <= kinds:
                        raise APIError(502, "invalid_inspection_output", "Visual shape assessment needs photo and reference evidence and cannot invent dimensions")
                    continue
                checked = [measurements[mid] for mid in decision.measurement_ids]
                if not checked:
                    raise APIError(502, "invalid_inspection_output", "A confirmed geometric mismatch needs supplied measurements")
                for m in checked:
                    if (m.extent != "full_target" or m.interval is None or m.acceptance_interval is None
                            or not set(m.source_asset_ids) <= set(decision.evidence_used) & complete
                            or not all(any(reported == m for reported in assets[aid].reported_measurements)
                                       for aid in m.source_asset_ids)
                            or ((m.requires_alignment or "displacement" in candidate.mismatch_types)
                                and (not m.alignment_verified or not m.coordinate_frame_id))
                            or not (m.interval[1] < m.acceptance_interval[0] or m.interval[0] > m.acceptance_interval[1])):
                        raise APIError(502, "invalid_inspection_output", "Partial, uncertain or unaligned measurements cannot confirm a geometric deviation")
    return result


def assemble_report(package, review, reviewer_task_id, chat_id):
    validate_review(review, package)
    selected = {d.case_id: set(d.measurement_ids) for _, d in review.cases.rows()}
    measurements = {c.case_id: [m for m in c.measurements if m.measurement_id in selected[c.case_id]]
                    for _, c in package.inspection.cases.rows()}
    context = {c.case_id: CaseContext.model_validate(c.model_dump(include={
        "object_type", "ifc_id", "segment_id", "location", "mismatch_types", "evidence_ids"}))
        for _, c in package.inspection.cases.rows()}
    statuses = {e.asset_id: EvidenceStatus(available=e.available, truncated=e.truncated, limitation=e.limitation)
                for e in package.evidence}
    return InspectionReport(request_id=package.inspection.request_id, project_id=package.inspection.project_id,
        chat_id=chat_id, task=package.inspection.task, case_context=context, assets=package.inspection.assets, asset_status=statuses,
        request_sha256=package.request_sha256, package_sha256=fingerprint(package), reviewer_task_id=reviewer_task_id,
        review_sha256=fingerprint(review), source=package.inspection.source, cases=review.cases, measurements=measurements)
