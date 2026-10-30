from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Asset(StrictModel):
    asset_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    kind: Literal["photo", "drawing"]
    mime_type: Literal["image/png", "image/jpeg", "application/pdf"]
    data_base64: Annotated[str, Field(min_length=1, max_length=8_000_000)]
    page: Annotated[int, Field(ge=1, le=10000)] | None = None

    @model_validator(mode="after")
    def pdf_page_required(self):
        if self.mime_type == "application/pdf" and (self.kind != "drawing" or self.page is None):
            raise ValueError("PDF requires drawing kind and explicit one-based page")
        return self


class TaskRequest(StrictModel):
    request_id: UUID
    question: Annotated[str, Field(min_length=1, max_length=4000)]
    context: Annotated[str, Field(max_length=8000)] = ""
    assets: Annotated[list[Asset], Field(min_length=1, max_length=8)]
    parent_task_id: UUID | None = None

    @model_validator(mode="after")
    def check_assets(self):
        if len({a.asset_id for a in self.assets}) != len(self.assets):
            raise ValueError("Asset IDs must be unique")
        if sum(len(a.data_base64) for a in self.assets) > 20_000_000:
            raise ValueError("Combined encoded assets exceed 20 MB")
        return self


class Region(StrictModel):
    asset_id: str
    bbox: tuple[float, float, float, float]

    @model_validator(mode="after")
    def check_box(self):
        x, y, w, h = self.bbox
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1
                and x + w <= 1.00000001 and y + h <= 1.00000001):
            raise ValueError("bbox must be normalized [x,y,width,height] within original image")
        return self


class Claim(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence: Annotated[list[Region], Field(min_length=1, max_length=8)]


class Finding(StrictModel):
    criterion: Annotated[str, Field(min_length=1, max_length=1000)]
    design_claim: Claim
    photo_claim: Claim
    correspondence: Literal["supported", "uncertain"]
    correspondence_basis: Annotated[str, Field(min_length=1, max_length=2000)]
    visibility: Literal["sufficient", "insufficient"]
    verdict: Literal["match", "mismatch", "inconclusive"]
    justification: Annotated[str, Field(min_length=1, max_length=3000)]
    limitations: Annotated[list[Annotated[str, Field(min_length=1, max_length=1000)]], Field(max_length=20)]

    @model_validator(mode="after")
    def prevent_unsupported_verdict(self):
        if (self.correspondence == "uncertain" or self.visibility == "insufficient") and self.verdict != "inconclusive":
            raise ValueError("Uncertain correspondence or visibility requires inconclusive verdict")
        return self


class Report(StrictModel):
    status: Literal["completed", "needs_input"]
    findings: Annotated[list[Finding], Field(max_length=12)]
    questions: Annotated[list[Annotated[str, Field(min_length=1, max_length=1000)]], Field(max_length=10)]

    @model_validator(mode="after")
    def questions_match_state(self):
        if self.status == "needs_input" and not self.questions:
            raise ValueError("needs_input requires a specific question")
        if self.status == "completed" and self.questions:
            raise ValueError("Questions require needs_input status")
        if self.status == "completed" and not self.findings:
            raise ValueError("Completed report requires findings")
        return self
