"""Thin HTTP layer: validate input and delegate extraction through FastAPI DI."""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints

from .dependencies import get_archive_service, get_validation_service
from .services.archive import ArchiveService, ArchiveServiceError
from geometry_engine.validation import SessionValidationService


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]


class ExtractResponse(BaseModel):
    session_id: str
    directory: str
    validation: dict


def create_app():
    app = FastAPI(title="Geometry Engine API", version="0.1.0")

    @app.exception_handler(ArchiveServiceError)
    async def service_error(request: Request, error: ArchiveServiceError):
        statuses = {"session_exists": 409, "download_failed": 502}
        return JSONResponse(status_code=statuses.get(error.code, 422), content={"detail": str(error)})

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

    return app


app = create_app()
