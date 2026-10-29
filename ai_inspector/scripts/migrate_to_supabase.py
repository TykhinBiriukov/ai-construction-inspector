"""Copy a SQLite backup into an EMPTY private PostgreSQL schema without deleting source data."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from ai_inspector.config import Settings
from ai_inspector.postgres_storage import PostgresDatabase, TABLES
from ai_inspector.supabase_bim import SupabaseBIMStore


def migrate(source, destination, store=None):
    with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Source SQLite integrity check failed')
        existing = {row['name'] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        records = {}
        for table in TABLES:
            # Backups from before agent chat have no chat tables yet.
            if table not in existing and table in ('chats', 'agent_runs', 'backlog_items'):
                records[table] = []
            else:
                records[table] = [dict(row) for row in db.execute('SELECT * FROM '+table)]
        # Preserve the previously implicit ordering too.
        for table in ('inspections','work_items'):
            records[table] = [dict(row) for row in db.execute('SELECT rowid, * FROM '+table)]
    if store:
        for row in records['inspections']:
            model = json.loads(row['data']).get('bim_model')
            if model:
                path = Path(source).parent / 'models' / model['sha256']
                with path.open('rb') as handle, store.stage(handle) as staged:
                    if staged.sha256 != model['sha256'] or staged.byte_length != model['byte_length']:
                        raise RuntimeError('Local BIM failed verification')
                    store.publish(staged)
    with destination.transaction() as target:
        if any(target.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone() for table in TABLES):
            raise RuntimeError('Destination is not empty; refusing to merge or overwrite data')
        for table in TABLES:
            for row in records[table]:
                columns = list(row)
                target.execute('INSERT INTO '+table+' ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')', tuple(row.values()))
            restored = [dict(row) for row in target.execute('SELECT * FROM '+table).fetchall()]
            canonical = lambda rows: sorted(json.dumps(r, sort_keys=True) for r in rows)
            if canonical(restored) != canonical(records[table]):
                raise RuntimeError('Readback mismatch for '+table)
        for table,column in (('sessions','assignment_order'),('device_events','sequence'),('inspections','rowid'),('work_items','rowid'),('agent_runs','sequence')):
            target.execute("SELECT setval(pg_get_serial_sequence(?, ?), COALESCE((SELECT MAX("+column+") FROM "+table+"), 1), EXISTS(SELECT 1 FROM "+table+"))", ('inspector.'+table,column))
    return {table:len(rows) for table,rows in records.items()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Offline SQLite database or consistent SQLite backup')
    parser.add_argument('--env', type=Path, default=Path('.env'))
    args=parser.parse_args()
    settings=Settings.from_env(args.env)
    if not settings.database_url or not settings.supabase_enabled:
        parser.error('PostgreSQL and Supabase Storage must both be configured')
    print(json.dumps(migrate(args.source,PostgresDatabase(settings.database_url),SupabaseBIMStore(settings)),indent=2))

if __name__ == '__main__':
    main()
