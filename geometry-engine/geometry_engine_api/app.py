"""Thin HTTP layer: validate input and delegate session stages through DI."""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints

from .dependencies import (
    get_archive_service,
    get_pipeline_service,
    get_settings,
)
from .services.archive import ArchiveService, ArchiveServiceError, Settings
from geometry_engine.pipeline import PipelineService
from geometry_engine.sam_backend import Sam3UnavailableError


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]


class ExtractRequest(SessionRequest):
    project_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")] | None = None


class ExtractResponse(BaseModel):
    session_id: str
    directory: str
    validation: dict


class PipelineResponse(BaseModel):
    session_id: str
    directory: str
    pipeline: dict


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
    ):
        if payload.project_id is not None:
            result = service.extract(payload.session_id, project_id=payload.project_id)
        else:
            result = service.extract(payload.session_id)
        validation = {"status": "not_run", "reason": "Validation runs in /sessions/process"}
        return ExtractResponse(session_id=result.session_id, directory=str(result.directory), validation=validation)

    @app.post("/sessions/process", response_model=PipelineResponse)
    def process(
        payload: SessionRequest,
        service: Annotated[PipelineService, Depends(get_pipeline_service)],
        settings: Annotated[Settings, Depends(get_settings)],
    ):
        root = settings.data_dir.resolve() / "processed"
        directory = (root / payload.session_id).resolve()
        if directory.parent != root:
            raise HTTPException(status_code=422, detail="Invalid session directory")
        try:
            report = service.run(directory)
        except FileExistsError:
            raise HTTPException(status_code=409, detail="Pipeline or stage results already exist; see pipeline.json and use stage CLI to continue") from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except (OSError, RuntimeError):
            raise HTTPException(status_code=500, detail="Pipeline failed; see processed session pipeline.json for the failed stage") from None
        return PipelineResponse(session_id=payload.session_id, directory=str(directory), pipeline=report)

    return app


app = create_app()
