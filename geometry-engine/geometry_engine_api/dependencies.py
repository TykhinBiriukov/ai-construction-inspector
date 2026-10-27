"""FastAPI dependency providers."""

from typing import Annotated
import os

from fastapi import Depends, HTTPException
import httpx
from supabase import Client, ClientOptions, create_client

from .services.archive import ArchiveService, ArchiveServiceError, Settings, validate_url
from geometry_engine.validation import SessionValidationService
from geometry_engine.sam_backend import Sam3Backend
from geometry_engine.segmentation import SegmentationService
from geometry_engine.mesh_segmentation import MeshSegmentationService


def get_validation_service() -> SessionValidationService:
    return SessionValidationService()


def get_settings() -> Settings:
    return Settings.from_env()


def get_sam_backend(settings: Annotated[Settings, Depends(get_settings)]) -> Sam3Backend:
    # get_settings loads .env; this dependency needs no Supabase connection.
    api_key = os.getenv("MODEL_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="Set MODEL_API_KEY in geometry-engine/.env to use Meta SAM")
    return Sam3Backend(api_key=api_key)


def get_segmentation_service(backend: Annotated[Sam3Backend, Depends(get_sam_backend)]) -> SegmentationService:
    return SegmentationService(backend)


def get_mesh_segmentation_service() -> MeshSegmentationService:
    return MeshSegmentationService()


def get_supabase_client(settings: Annotated[Settings, Depends(get_settings)]):
    if not settings.supabase_url or not settings.supabase_secret_key or not settings.storage_bucket:
        raise ArchiveServiceError("invalid_request", "Set SUPABASE_URL, SUPABASE_SECRET_KEY and SUPABASE_STORAGE_BUCKET")
    try:
        validate_url(settings.supabase_url)
    except ValueError:
        raise ArchiveServiceError("invalid_request", "Invalid SUPABASE_URL") from None
    with httpx.Client(timeout=30, trust_env=False) as http_client:
        client = create_client(
            settings.supabase_url,
            settings.supabase_secret_key,
            options=ClientOptions(httpx_client=http_client, auto_refresh_token=False, persist_session=False),
        )
        yield client


def get_archive_service(
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[Client, Depends(get_supabase_client)],
) -> ArchiveService:
    return ArchiveService(settings, client)
