"""PostgreSQL index and fenced leases for immutable SAM cache artifacts."""
from datetime import timedelta

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from geometry_engine_api.database.models import SegmentationCacheEntry


class SegmentationCacheRepository:
    def __init__(self, engine):
        self.engine = engine

    def lookup(self, keys):
        if not keys:
            return {}
        # One batch query for the selected frames (at most 32 prompts per image).
        with Session(self.engine) as session:
            rows = session.scalars(select(SegmentationCacheEntry).where(
                SegmentationCacheEntry.request_hash.in_(keys),
                SegmentationCacheEntry.status == "completed")).all()
            return {row.request_hash: {"artifact_key": row.artifact_key,
                                      "artifact_sha256": row.artifact_sha256,
                                      "artifact_size": row.artifact_size} for row in rows}

    def claim(self, key, token, seconds):
        entry = SegmentationCacheEntry
        now = func.clock_timestamp()
        values = dict(status="running", lease_token=token,
                      lease_expires_at=now + timedelta(seconds=seconds), updated_at=now,
                      artifact_key=None, artifact_sha256=None, artifact_size=None)
        statement = insert(entry).values(request_hash=key, **values).on_conflict_do_update(
            index_elements=[entry.request_hash], set_=values,
            where=(entry.status == "running") & or_(entry.lease_expires_at <= now,
                                                     entry.lease_expires_at.is_(None)),
        ).returning(entry.request_hash)
        with Session(self.engine) as session, session.begin():
            return session.scalar(statement) is not None

    @staticmethod
    def _owned(key, token):
        entry = SegmentationCacheEntry
        return (entry.request_hash == key, entry.status == "running", entry.lease_token == token,
                entry.lease_expires_at > func.clock_timestamp())

    def heartbeat(self, key, token, seconds):
        with Session(self.engine) as session, session.begin():
            result = session.execute(update(SegmentationCacheEntry).where(*self._owned(key, token)).values(
                lease_expires_at=func.clock_timestamp() + timedelta(seconds=seconds),
                updated_at=func.clock_timestamp()))
            return result.rowcount == 1

    def publish(self, key, token, object_key, sha256, size):
        with Session(self.engine) as session, session.begin():
            result = session.execute(update(SegmentationCacheEntry).where(*self._owned(key, token)).values(
                status="completed", artifact_key=object_key, artifact_sha256=sha256, artifact_size=size,
                lease_token=None, lease_expires_at=None, updated_at=func.clock_timestamp()))
            return result.rowcount == 1

    def invalidate(self, key, sha256):
        with Session(self.engine) as session, session.begin():
            session.execute(delete(SegmentationCacheEntry).where(
                SegmentationCacheEntry.request_hash == key,
                SegmentationCacheEntry.status == "completed",
                SegmentationCacheEntry.artifact_sha256 == sha256))

    def release(self, key, token):
        with Session(self.engine) as session, session.begin():
            session.execute(delete(SegmentationCacheEntry).where(
                SegmentationCacheEntry.request_hash == key,
                SegmentationCacheEntry.status == "running",
                SegmentationCacheEntry.lease_token == token))
