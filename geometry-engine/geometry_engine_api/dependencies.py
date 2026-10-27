"""FastAPI dependency providers."""

from .services.archive import ArchiveService, Settings


def get_archive_service() -> ArchiveService:
    return ArchiveService(Settings.from_env())
