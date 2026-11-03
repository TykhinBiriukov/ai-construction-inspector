"""Named image views and grid evidence. All coordinate arithmetic is server-owned."""
import math
import re
from dataclasses import dataclass
from typing import Annotated

from PIL import Image, ImageDraw, ImageFont
from pydantic import Field, model_validator

from .models import StrictModel, Claim, Finding, Report, Region


class ReportReferenceError(ValueError):
    """Safe, actionable report diagnostics without raw model/provider output."""
    def __init__(self, code, loc=(), **details):
        self.issue = {"type": code, "loc": list(loc), **details}
        super().__init__(code)


class GridRegion(StrictModel):
    view_id: Annotated[str, Field(min_length=1, max_length=80)]
    top_left: Annotated[str, Field(pattern=r"^[A-J](?:10|[1-9])$")]
    bottom_right: Annotated[str, Field(pattern=r"^[A-J](?:10|[1-9])$")]

    @model_validator(mode="after")
    def ordered(self):
        a, b = cell(self.top_left), cell(self.bottom_right)
        if a[0] > b[0] or a[1] > b[1]:
            raise ValueError("Grid corners must be ordered top-left to bottom-right")
        return self


def cell(value):
    return ord(value[0]) - ord("A"), int(value[1:]) - 1


class GridClaim(Claim):
    evidence: Annotated[list[GridRegion], Field(min_length=1, max_length=4)]


class GridFinding(Finding):
    design_claim: GridClaim
    photo_claim: GridClaim


class GridReport(Report):
    findings: Annotated[list[GridFinding], Field(max_length=12)]


@dataclass
class View:
    asset_id: str
    # Exact integer bounds in the oriented original image / rendered PDF page.
    bounds: tuple[int, int, int, int]


class Views:
    def __init__(self, images):
        self.images = images
        self.views = {key: View(key, (0, 0, image.width, image.height)) for key, image in images.items()}
        self.counter = 0

    def pixels(self, ref):
        view = self.views[ref.view_id]
        x0, y0, x1, y1 = view.bounds
        left, top = cell(ref.top_left)
        right, bottom = cell(ref.bottom_right)
        return (x0 + math.floor((x1-x0)*left/10), y0 + math.floor((y1-y0)*top/10),
                x0 + math.ceil((x1-x0)*(right+1)/10), y0 + math.ceil((y1-y0)*(bottom+1)/10))

    def resolve(self, ref):
        view = self.views[ref.view_id]
        x0, y0, x1, y1 = self.pixels(ref)
        image = self.images[view.asset_id]
        return Region(asset_id=view.asset_id, bbox=(x0/image.width, y0/image.height,
                                                   (x1-x0)/image.width, (y1-y0)/image.height))

    def new_crop(self, ref):
        bounds = self.pixels(ref)
        if bounds[2]-bounds[0] < 20 or bounds[3]-bounds[1] < 20:
            raise ValueError("Requested view too small")
        self.counter += 1
        name = f"crop-{self.counter}"
        while name in self.views:
            self.counter += 1
            name = f"crop-{self.counter}"
        self.views[name] = View(self.views[ref.view_id].asset_id, bounds)
        return name

    def image(self, view_id):
        view = self.views[view_id]
        return self.images[view.asset_id].crop(view.bounds)

    def grid(self, view_id):
        image = self.image(view_id)
        image.thumbnail((1200, 1200))
        margin = 32
        result = Image.new("RGB", (image.width+2*margin, image.height+2*margin), "#ffffff")
        result.paste(image, (margin, margin))
        draw = ImageDraw.Draw(result)
        font = ImageFont.load_default(size=19)
        for i in range(11):
            x, y = margin+image.width*i/10, margin+image.height*i/10
            draw.line((x, margin, x, margin+image.height), fill="#43a3aa", width=1)
            draw.line((margin, y, margin+image.width, y), fill="#43a3aa", width=1)
            if i < 10:
                cx, cy = margin+image.width*(i+.5)/10, margin+image.height*(i+.5)/10
                for yy in (15, margin+image.height+16):
                    draw.text((cx, yy), chr(65+i), font=font, fill="black", anchor="mm")
                for xx in (15, margin+image.width+16):
                    draw.text((xx, cy), str(i+1), font=font, fill="black", anchor="mm")
        return result

    def report(self, draft, kinds):
        value = draft.model_dump()
        refs = []
        for index, finding in enumerate(value["findings"]):
            for key, kind in (("design_claim", "drawing"), ("photo_claim", "photo")):
                resolved = []
                for evidence_index, raw in enumerate(finding[key]["evidence"]):
                    ref = GridRegion.model_validate(raw)
                    loc = ("findings", index, key, "evidence", evidence_index, "view_id")
                    allowed = [name for name, view in self.views.items() if kinds.get(view.asset_id) == kind]
                    if ref.view_id not in self.views:
                        raise ReportReferenceError("unknown_evidence_view", loc, allowed_view_ids=allowed)
                    region = self.resolve(ref)
                    if kinds.get(region.asset_id) != kind:
                        raise ReportReferenceError("wrong_evidence_role", loc, expected_kind=kind,
                                                   allowed_view_ids=allowed)
                    resolved.append(region.model_dump())
                    refs.append({"criterion": finding["criterion"], "claim": key,
                                 "grid": raw, "original": region.model_dump()})
                finding[key]["evidence"] = resolved
        if len(refs) > 32:
            raise ReportReferenceError("too_many_evidence_regions", ("findings",), max_regions=32)
        return Report.model_validate(value), refs
