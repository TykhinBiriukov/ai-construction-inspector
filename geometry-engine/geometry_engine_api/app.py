"""Thin HTTP layer: validate input and delegate session stages through DI."""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .dependencies import get_archive_service, get_validation_service, get_segmentation_service, get_settings
from .services.archive import ArchiveService, ArchiveServiceError, Settings
from geometry_engine.sam_backend import Sam3UnavailableError
from geometry_engine.segmentation import SegmentationService
from geometry_engine.validation import SessionValidationService


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]


class ExtractResponse(BaseModel):
    session_id: str
    directory: str
    validation: dict


class SegmentRequest(ExtractRequest):
    max_frames: Annotated[int, Field(strict=True, ge=1)] | None = None
    resume: Annotated[bool, Field(strict=True)] = False


class SegmentResponse(BaseModel):
    session_id: str
    directory: str
    segmentation: dict


def create_app():
    app = FastAPI(title="Geometry Engine API", version="0.1.0")

    @app.exception_handler(ArchiveServiceError)
    async def service_error(request: Request, error: ArchiveServiceError):
        statuses = {"session_exists": 409, "download_failed": 502}
        return JSONResponse(status_code=statuses.get(error.code, 422), content={"detail": str(error)})

    @app.exception_handler(Sam3UnavailableError)
    async def sam_error(request: Request, error: Sam3UnavailableError):
        return JSONResponse(status_code=502, content={
            "detail": "Meta SAM API request failed. Check API access, limits and connectivity."
        })

    @app.post("/sessions/extract", response_model=ExtractResponse, status_code=201)
    def extract(
        payload: ExtractRequest,
        service: Annotated[ArchiveService, Depends(get_archive_service)],
        validator: Annotated[SessionValidationService, Depends(get_validation_service)],
    ):
        result = service.extract(payload.session_id)
        validation = validator.validate(result.directory)
        if validation["status"] == "invalid":
            raise HTTPException(status_code=422, detail={"session_id": result.session_id, "validation": validation})
        return ExtractResponse(session_id=result.session_id, directory=str(result.directory), validation=validation)

    @app.post("/sessions/segment", response_model=SegmentResponse, status_code=201)
    def segment(
        payload: SegmentRequest,
        service: Annotated[SegmentationService, Depends(get_segmentation_service)],
        settings: Annotated[Settings, Depends(get_settings)],
    ):
        root = settings.data_dir.resolve()
        directory = (root / payload.session_id).resolve()
        if directory.parent != root:
            raise HTTPException(status_code=422, detail="Session directory must stay inside GEOMETRY_DATA_DIR")
        try:
            options = {"max_frames": payload.max_frames}
            if payload.resume:
                options["resume"] = True
            report = service.segment(directory, **options)
        except FileExistsError:
            raise HTTPException(status_code=409, detail="Segmentation already exists; use the CLI with a new --output directory") from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except OSError:
            raise HTTPException(status_code=500, detail="Could not read or write segmentation files") from None
        return SegmentResponse(session_id=payload.session_id, directory=str(directory / "segmentation"), segmentation=report)

    return app


app = create_app()
