"""The local launcher applies migrations before serving requests."""
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

from sqlalchemy.exc import OperationalError


class DatabaseStartupTests(unittest.TestCase):
    def test_migrations_finish_before_api_start(self):
        calls = []
        with patch('geometry_engine_api.database.migrate.upgrade_database', side_effect=lambda: calls.append('migrate')), \
             patch('uvicorn.run', side_effect=lambda *a, **k: calls.append('api')):
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'main.py'), run_name='__main__')
        self.assertEqual(calls, ['migrate', 'api'])

    def test_failed_migration_prevents_api_start_and_hides_credentials(self):
        failure = OperationalError('SQL', {}, Exception('private-password'))
        with patch('geometry_engine_api.database.migrate.upgrade_database', side_effect=failure), \
             patch('uvicorn.run') as server, self.assertRaises(SystemExit) as stopped:
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'main.py'), run_name='__main__')
        server.assert_not_called()
        self.assertNotIn('private-password', str(stopped.exception))
