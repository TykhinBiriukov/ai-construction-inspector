"""Opt-in real PostgreSQL race and lease-fencing tests in a disposable schema."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
import re
import threading
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, func, text, update

from geometry_engine_api.database.migrate import upgrade_database
from geometry_engine_api.database.models import SegmentationCacheEntry
from geometry_engine_api.services.segmentation_cache import SegmentationCacheRepository


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL for PostgreSQL integration tests")
class SegmentationCachePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = "sam_cache_test_" + uuid4().hex
        cls.admin = create_engine(os.environ["TEST_DATABASE_URL"], connect_args={"connect_timeout": 5})
        with cls.admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(os.environ["TEST_DATABASE_URL"], pool_size=10,
            isolation_level="READ COMMITTED", connect_args={
                "connect_timeout": 5, "options": f"-c search_path={cls.schema} -c timezone=UTC"})
        try:
            with cls.engine.connect() as connection:
                upgrade_database(connection)
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        assert re.fullmatch(r"sam_cache_test_[0-9a-f]{32}", cls.schema)
        with cls.admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.admin.dispose()

    def setUp(self):
        self.repository = SegmentationCacheRepository(self.engine)
        self.key = uuid4().hex + uuid4().hex

    def test_concurrent_workers_issue_only_one_inference_claim(self):
        barrier = threading.Barrier(8)
        def claim(_):
            barrier.wait(timeout=10)
            return self.repository.claim(self.key, uuid4(), 900)
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(claim, range(8)))
        self.assertEqual(sum(claims), 1)

    def test_expired_owner_cannot_publish_refresh_or_release_successor(self):
        old, new = uuid4(), uuid4()
        self.assertTrue(self.repository.claim(self.key, old, 900))
        with self.engine.begin() as connection:
            connection.execute(update(SegmentationCacheEntry).where(
                SegmentationCacheEntry.request_hash == self.key).values(
                lease_expires_at=func.clock_timestamp() - timedelta(seconds=1)))
        self.assertFalse(self.repository.publish(self.key, old, "old", "a" * 64, 1))
        self.assertFalse(self.repository.heartbeat(self.key, old, 900))
        self.assertTrue(self.repository.claim(self.key, new, 900))
        self.repository.release(self.key, old)
        self.assertTrue(self.repository.publish(self.key, new, "cache/sam/new.npz", "b" * 64, 50))
        self.assertEqual(self.repository.lookup([self.key])[self.key]["artifact_key"], "cache/sam/new.npz")

    def test_batch_lookup_hides_running_and_invalidation_is_fenced_by_digest(self):
        token = uuid4()
        self.assertTrue(self.repository.claim(self.key, token, 900))
        self.assertEqual(self.repository.lookup([self.key]), {})
        self.assertTrue(self.repository.publish(self.key, token, "cache/sam/result.npz", "a" * 64, 10))
        self.assertFalse(self.repository.claim(self.key, uuid4(), 900))
        self.repository.invalidate(self.key, "b" * 64)
        self.assertIn(self.key, self.repository.lookup([self.key, "f" * 64]))
        self.repository.invalidate(self.key, "a" * 64)
        self.assertEqual(self.repository.lookup([self.key]), {})
        self.assertTrue(self.repository.claim(self.key, uuid4(), 900))


if __name__ == "__main__":
    unittest.main()
