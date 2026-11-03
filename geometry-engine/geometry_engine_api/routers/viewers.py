"""Open trusted generated HTML through the API, including relative photo assets."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from geometry_engine_api.database.connection import get_database_session
from geometry_engine_api.database.models import ViewerExport
from geometry_engine_api.schemas.inspections import ViewerLinkResponse
from geometry_engine_api.dependencies import get_settings, get_supabase_client
from geometry_engine_api.services.artifacts import ArtifactError, ArtifactStore, ObjectStoreError, SupabaseObjectStore
from geometry_engine_api.services.viewer_files import ViewerFiles


router = APIRouter(prefix='/inspections', tags=['Inspections'])


def get_viewer_files(settings=Depends(get_settings), client=Depends(get_supabase_client)):
    return ViewerFiles(ArtifactStore(SupabaseObjectStore(client, settings.storage_bucket)),
                       settings.data_dir / 'viewer_cache')


def _export(session, job_id, export_id=None):
    statement = select(ViewerExport).where(ViewerExport.job_id == job_id)
    if export_id is not None:
        statement = statement.where(ViewerExport.id == export_id)
    else:
        statement = statement.where(ViewerExport.status == 'completed')
    export = session.scalar(statement.order_by(ViewerExport.created_at.desc()).limit(1))
    if export is None:
        raise HTTPException(404, 'Viewer export not found')
    if export.status != 'completed' or not export.result_manifest_key or not export.result_manifest_sha256:
        raise HTTPException(409, 'Viewer is not ready; check the inspection status')
    return export


@router.get('/{job_id}/viewer', response_class=JSONResponse, response_model=ViewerLinkResponse,
            responses={303: {'description': 'Browser redirect to the generated HTML page'}},
            description='JSON clients receive a viewer_url. Open that URL in a browser to view the 3D report.')
def open_viewer(job_id: UUID, request: Request, session: Annotated[Session, Depends(get_database_session)],
                export_id: UUID | None = None):
    export = _export(session, job_id, export_id)
    url = f'/inspections/{job_id}/viewer/assets/{export.id}/viewer.html'
    accept = request.headers.get('accept', '').lower()
    # Swagger fetches application/json and would otherwise render tens of MB of
    # HTML as response text. Browser navigation still opens the actual page.
    if 'application/json' in accept and 'text/html' not in accept:
        return JSONResponse({'viewer_url': url}, headers={'Cache-Control': 'no-store'})
    return RedirectResponse(url, status_code=303,
                            headers={'Cache-Control': 'no-store'})


@router.get('/{job_id}/viewer/assets/{export_id}/{name:path}', response_class=FileResponse,
            include_in_schema=False)
def viewer_asset(job_id: UUID, export_id: UUID, name: str,
                 session: Annotated[Session, Depends(get_database_session)],
                 files: Annotated[ViewerFiles, Depends(get_viewer_files)]):
    export = _export(session, job_id, export_id)
    try:
        path = files.file(export, name)
    except FileNotFoundError:
        raise HTTPException(404, 'Viewer asset not found') from None
    except ArtifactError:
        raise HTTPException(409, 'Viewer artifact integrity check failed') from None
    except ObjectStoreError:
        raise HTTPException(503, 'Viewer storage is temporarily unavailable') from None
    return FileResponse(path, headers={'Cache-Control': 'private, max-age=3600',
                                       'X-Content-Type-Options': 'nosniff'})
