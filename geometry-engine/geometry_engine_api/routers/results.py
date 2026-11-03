"""Read completed inspection evidence without generating a viewer."""
from typing import Annotated
from uuid import UUID
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from geometry_engine_api.dependencies import get_settings, get_supabase_client
from geometry_engine_api.routers.inspections import get_inspection_service
from geometry_engine_api.schemas.inspection_results import InspectionResultResponse
from geometry_engine_api.services.artifacts import ArtifactError, ArtifactStore, ObjectStoreError, SupabaseObjectStore
from geometry_engine_api.services.inspection_results import InspectionResults, InvalidResultLink, ResultLinkSigner
from geometry_engine_api.services.inspections import InspectionService


router = APIRouter(prefix='/inspections', tags=['Inspections'])


def get_result_service(settings=Depends(get_settings), client=Depends(get_supabase_client)):
    secret = os.getenv('RESULT_URL_SIGNING_SECRET') or settings.supabase_secret_key
    try:
        signer = ResultLinkSigner(secret)
    except ValueError:
        raise HTTPException(503, 'Result link signing is not configured') from None
    artifacts = ArtifactStore(SupabaseObjectStore(client, settings.storage_bucket),
                              cache_dir=settings.data_dir / 'artifact_cache')
    return InspectionResults(artifacts, settings.data_dir, signer)


def _completed_job(service, job_id):
    job = service.get(job_id)
    if job is None:
        raise HTTPException(404, 'Inspection not found')
    if job.status != 'completed':
        raise HTTPException(409, {'code': 'inspection_not_ready', 'status': job.status,
                                 'error_code': job.error_code, 'status_url': f'/inspections/{job_id}'},
                            headers={'Retry-After': '10', 'Cache-Control': 'no-store'})
    if not job.result_manifest_key or not job.result_manifest_sha256:
        raise HTTPException(409, 'Completed inspection has no committed result manifest')
    return job


def _artifact_error(error):
    if isinstance(error, ObjectStoreError):
        raise HTTPException(503, 'Result storage is unavailable', headers={'Retry-After': '5'}) from None
    raise HTTPException(409, 'Inspection result is incomplete or failed its integrity check') from None


@router.get('/{job_id}/result', response_model=InspectionResultResponse,
            responses={404: {'description': 'Inspection not found'},
                       409: {'description': 'Inspection is not complete or its artifacts are invalid'},
                       503: {'description': 'Result storage is unavailable'}})
def get_result(job_id: UUID, request: Request, response: Response,
               inspections: Annotated[InspectionService, Depends(get_inspection_service)],
               results: Annotated[InspectionResults, Depends(get_result_service)],
               expires_in: Annotated[int, Query(ge=60, le=86400)] = 3600):
    job = _completed_job(inspections, job_id)
    base = os.getenv('GEOMETRY_PUBLIC_BASE_URL', '').strip() or str(request.base_url)
    url = urlsplit(base)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise HTTPException(503, 'GEOMETRY_PUBLIC_BASE_URL must be a valid HTTP(S) base URL')
    try:
        result = results.get(job, base, expires_in=expires_in)
    except (ArtifactError, FileNotFoundError, KeyError, TypeError, ObjectStoreError) as error:
        _artifact_error(error)
    response.headers['Cache-Control'] = 'no-store'
    return result


@router.get('/{job_id}/result/files/{stage}/{path:path}', response_class=FileResponse,
            include_in_schema=False)
def result_file(job_id: UUID, stage: str, path: str,
                inspections: Annotated[InspectionService, Depends(get_inspection_service)],
                results: Annotated[InspectionResults, Depends(get_result_service)],
                expires: Annotated[int, Query(ge=1)],
                signature: Annotated[str, Query(pattern='^[0-9a-f]{64}$')]):
    job = _completed_job(inspections, job_id)
    try:
        results.signer.verify(job, stage, path, expires, signature)
    except InvalidResultLink:
        raise HTTPException(403, 'Result link is expired or invalid; request fresh links') from None
    try:
        file = results.file(job, stage, path)
    except FileNotFoundError:
        raise HTTPException(404, 'Result artifact not found') from None
    except (ArtifactError, KeyError, TypeError, ObjectStoreError) as error:
        _artifact_error(error)
    return FileResponse(file, headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
