"""Thin HTTP layer: validate input and delegate session stages through DI."""

from typing import Annotated
import json

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy.exc import DBAPIError, TimeoutError as DatabasePoolTimeout

from .dependencies import (
    get_archive_service,
    get_pipeline_service,
    get_scan_vs_bim_service,
    get_opening_comparison_service,
    get_settings,
    get_opening_boundary_factory,
)
from .services.archive import ArchiveService, ArchiveServiceError, Settings
from geometry_engine.pipeline import PipelineService
from geometry_engine.segmentation.sam_backend import Sam3UnavailableError
from .database.connection import DatabaseConfigurationError
from .routers.inspections import router as inspections_router
from .routers.viewers import router as viewers_router
from .routers.results import router as results_router


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]


class ExtractRequest(SessionRequest):
    project_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")] | None = None
    manifest_path: Annotated[str, StringConstraints(min_length=1, max_length=1024)] | None = None


class ExtractResponse(BaseModel):
    session_id: str
    directory: str
    validation: dict


class ProcessRequest(SessionRequest):
    measure_openings: bool = False


class PipelineResponse(BaseModel):
    session_id: str
    directory: str
    pipeline: dict


class CompareBimRequest(SessionRequest):
    ifc_file: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.ifc$")]
    segmentation_stage: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")] = "mesh_segmentation"
    compare_openings: bool = False
    measure_openings: bool = False
    use_opening_boundaries: bool = False
    resume_boundaries: bool = False


class CompareBimResponse(BaseModel):
    session_id: str
    directory: str
    comparison: dict
    opening_comparison: dict | None = None


def create_app():
    app = FastAPI(title="Geometry Engine API", version="0.1.0")
    app.include_router(inspections_router)
    app.include_router(viewers_router)
    app.include_router(results_router)

    @app.exception_handler(DatabaseConfigurationError)
    @app.exception_handler(DBAPIError)
    @app.exception_handler(DatabasePoolTimeout)
    async def database_error(request: Request, error: Exception):
        return JSONResponse(status_code=503, headers={'Retry-After': '5'}, content={
            'detail': 'Database unavailable. Check database settings and apply migrations.'
        })

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
        if payload.manifest_path is not None:
            result = service.extract(payload.session_id, project_id=payload.project_id,
                                     manifest_path=payload.manifest_path)
        elif payload.project_id is not None:
            result = service.extract(payload.session_id, project_id=payload.project_id)
        else:
            result = service.extract(payload.session_id)
        validation = {"status": "not_run", "reason": "Validation runs in /sessions/process"}
        return ExtractResponse(session_id=result.session_id, directory=str(result.directory), validation=validation)

    @app.post("/sessions/process", response_model=PipelineResponse)
    def process(
        payload: ProcessRequest,
        service: Annotated[PipelineService, Depends(get_pipeline_service)],
        settings: Annotated[Settings, Depends(get_settings)],
    ):
        root = settings.data_dir.resolve() / "processed"
        directory = (root / payload.session_id).resolve()
        if directory.parent != root:
            raise HTTPException(status_code=422, detail="Invalid session directory")
        try:
            report = service.run(directory, measure_openings=True) if payload.measure_openings else service.run(directory)
        except FileExistsError:
            raise HTTPException(status_code=409, detail="Pipeline or stage results already exist; see pipeline.json and use stage CLI to continue") from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except (OSError, RuntimeError):
            raise HTTPException(status_code=500, detail="Pipeline failed; see processed session pipeline.json for the failed stage") from None
        return PipelineResponse(session_id=payload.session_id, directory=str(directory), pipeline=report)

    @app.post("/sessions/compare-bim", response_model=CompareBimResponse, response_model_exclude_none=True)
    def compare_bim(
        payload: CompareBimRequest,
        service: Annotated[object, Depends(get_scan_vs_bim_service)],
        opening_service: Annotated[object, Depends(get_opening_comparison_service)],
        boundary_factory: Annotated[object, Depends(get_opening_boundary_factory)],
        settings: Annotated[Settings, Depends(get_settings)],
    ):
        if (payload.measure_openings or payload.use_opening_boundaries or payload.resume_boundaries) and not payload.compare_openings:
            raise HTTPException(status_code=422, detail="Boundary options require compare_openings=true")
        if (payload.measure_openings or payload.use_opening_boundaries) and payload.segmentation_stage != "mesh_segmentation":
            raise HTTPException(status_code=422, detail="Automatic boundaries currently require mesh_segmentation stage")
        if payload.resume_boundaries and not payload.measure_openings:
            raise HTTPException(status_code=422, detail="resume_boundaries requires measure_openings=true")
        data = settings.data_dir.resolve()
        root, references = (data / "processed").resolve(), (data / "references").resolve()
        directory = (root / payload.session_id).resolve()
        ifc_path = (references / payload.ifc_file).resolve()
        segmentation = (directory / payload.segmentation_stage).resolve()
        output = (directory / "scan_vs_bim").resolve()
        opening_output = (directory / "opening_comparison").resolve()
        # Resolve every boundary to reject symlink/junction escapes as well as '..'.
        if (root.parent != data or references.parent != data or directory.parent != root
                or ifc_path.parent != references or segmentation.parent != directory
                or output.parent != directory or opening_output.parent != directory):
            raise HTTPException(status_code=422, detail="Invalid comparison input or output path")
        if not directory.is_dir() or not ifc_path.is_file() or not segmentation.is_dir():
            raise HTTPException(status_code=404, detail="Session, IFC reference or segmentation stage not found")
        try:
            opening_report = None
            boundary_options = {}
            if payload.measure_openings:
                boundary_factory().run(directory, resume=payload.resume_boundaries)
            if payload.measure_openings or payload.use_opening_boundaries:
                boundary_options['boundaries_dir'] = directory/'mesh_boundaries'

            if payload.compare_openings and output.exists():
                saved_report = (output / "report.json").resolve()
                if saved_report.parent != output:
                    raise ValueError("Invalid saved comparison report path")
                report = json.loads(saved_report.read_text(encoding="utf-8"))
                if not isinstance(report, dict):
                    raise ValueError("Invalid saved comparison report")
            else:
                report = service.compare(directory, ifc_path, output_dir=output, segmentation_dir=segmentation)
            if payload.compare_openings:
                # The opening service verifies accepted registration and input hashes.
                opening_report = opening_service.compare(directory, ifc_path,
                    output_dir=opening_output, segmentation_dir=segmentation, comparison_dir=output, **boundary_options)
        except FileExistsError:
            raise HTTPException(status_code=409, detail="Requested comparison results already exist; use a new output directory through the stage CLI") from None
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Required comparison input not found") from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except (OSError, RuntimeError):
            raise HTTPException(status_code=500, detail="Scan-versus-BIM comparison failed; inspect the session results and server logs") from None
        return CompareBimResponse(session_id=payload.session_id, directory=str(directory), comparison=report,
                                  opening_comparison=opening_report)

    return app


app = create_app()
