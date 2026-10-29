"""Workers asyncpg adapter for the shared synchronous domain service."""
from contextlib import contextmanager
import asyncpg
import re
from pyodide.ffi import run_sync
from ai_inspector.storage import Database
from ai_inspector.errors import APIError

WRITE_LOCK = 7341202601
TABLE_REF = re.compile(r'\b(FROM|INTO|UPDATE|JOIN)\s+(workspace_files|workspace_items|workspace_observations|backlog_items|chats|agent_runs|inspections|sessions|receipts|capture_completions|geometry_jobs|work_items|device_events|device_connections|upload_requests|upload_objects|geometry_events)\b', re.IGNORECASE)

class Row(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)

class Cursor:
    def __init__(self, rows=(), rowcount=0):
        self.rows = [Row(row) for row in rows]
        self.rowcount = rowcount
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def fetchall(self):
        return self.rows
    def __iter__(self):
        return iter(self.rows)

class Connection:
    def __init__(self, raw):
        self.raw = raw
    def execute(self, query, parameters=()):
        if query.strip() == 'BEGIN':
            query = 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY'
        if query.startswith('INSERT OR IGNORE INTO '):
            query = query.replace('INSERT OR IGNORE INTO ', 'INSERT INTO ', 1) + ' ON CONFLICT DO NOTHING'
        query = TABLE_REF.sub(lambda m: m[1] + ' inspector.' + m[2], query)
        pieces = query.split('?')
        query = ''.join(piece + (f'${i+1}' if i < len(pieces)-1 else '') for i,piece in enumerate(pieces))
        if query.lstrip().upper().startswith(('SELECT','WITH')) or ' RETURNING ' in query.upper():
            return Cursor(run_sync(self.raw.fetch(query, *parameters)))
        status = run_sync(self.raw.execute(query, *parameters))
        return Cursor(rowcount=int(status.split()[-1]) if status.split()[-1].isdigit() else 0)
    def commit(self):
        run_sync(self.raw.execute('COMMIT'))
    def rollback(self):
        run_sync(self.raw.execute('ROLLBACK'))

class WorkerDatabase(Database):
    def __init__(self, url, *, hyperdrive=False):
        self.url = url
        self.hyperdrive = hyperdrive
    @contextmanager
    def connection(self):
        raw = None
        try:
            raw = run_sync(asyncpg.connect(self.url, ssl=False if self.hyperdrive else None, statement_cache_size=0, timeout=15))
            yield Connection(raw)
        except (OSError, asyncpg.PostgresConnectionError, asyncpg.CannotConnectNowError):
            raise APIError(503, 'storage_unavailable', 'Database is temporarily unavailable', retryable=True) from None
        finally:
            if raw:
                if raw.is_in_transaction():
                    run_sync(raw.execute('ROLLBACK'))
                run_sync(raw.close())
    @contextmanager
    def transaction(self):
        with self.connection() as conn:
            run_sync(conn.raw.execute('BEGIN'))
            try:
                conn.execute('SELECT pg_advisory_xact_lock(?)',(WRITE_LOCK,))
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
