"""Create an inspection request and read its persisted state."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from geometry_engine_api.database.connection import get_database_session
from geometry_engine_api.schemas.inspections import InspectionRequest, InspectionResponse
from geometry_engine_api.services.inspections import IdempotencyConflict, InspectionService


router = APIRouter(prefix='/inspections', tags=['Inspections'])


def get_inspection_service(session: Annotated[Session, Depends(get_database_session)]):
    return InspectionService(session)


@router.post('', response_model=InspectionResponse, status_code=202,
             responses={409: {'description': 'Idempotency key conflicts with another request'},
                        503: {'description': 'Database unavailable'}})
def submit_inspection(
    payload: InspectionRequest,
    response: Response,
    idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128,
                                         pattern=r'^[A-Za-z0-9._:-]+$')],
    service: Annotated[InspectionService, Depends(get_inspection_service)],
):
    try:
        job = service.submit(payload, idempotency_key)
    except IdempotencyConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    result = InspectionResponse.model_validate(job)
    response.headers['Location'] = result.status_url
    response.headers['Cache-Control'] = 'no-store'
    return result


@router.get('/{job_id}', response_model=InspectionResponse,
            responses={404: {'description': 'Inspection not found'}, 503: {'description': 'Database unavailable'}})
def get_inspection(job_id: UUID, response: Response,
                   service: Annotated[InspectionService, Depends(get_inspection_service)]):
    job = service.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail='Inspection not found')
    response.headers['Cache-Control'] = 'no-store'
    return InspectionResponse.model_validate(job)
