"""Dedicated PostgreSQL schema; local files are a disposable cache.

RGB is validated and hashed but is not duplicated here. Capture uploads remain
owned by the phone/Inspector. Frame/depth/confidence allow localization recovery.
"""
from contextlib import contextmanager
import hashlib
import json
import re
import threading
from pathlib import Path
from uuid import UUID


class Queries:
    def __init__(self, connection):self.connection=connection
    def execute(self, sql, args=()):
        # Only internal Lab queries pass through this adapter; no user SQL.
        return self.connection.execute(re.sub(r'(?<![.\w])sessions\b','localization.sessions',sql).replace('?','%s'),args)


class PostgresStore:
    def __init__(self, dsn):
        import psycopg
        self.psycopg=psycopg;self.dsn=dsn;self.connection=None;self.lock=threading.RLock()

    @contextmanager
    def connect(self):
        with self.lock:
            with self._transaction() as db:yield db

    @contextmanager
    def _transaction(self):
        if self.connection is None or self.connection.closed:
            self.connection=self.psycopg.connect(self.dsn,connect_timeout=10,autocommit=True)
        with self.connection.transaction():
            self.connection.execute("SET LOCAL statement_timeout='20s'")
            self.connection.execute("SET LOCAL lock_timeout='10s'")
            # Serializes DB state across overlapping deploys as well as threads.
            self.connection.execute('SELECT pg_advisory_xact_lock(731288491)')
            yield Queries(self.connection)

    def model_bytes(self, model_hash):
        with self.connect() as db:
            row=db.execute('SELECT content FROM localization.models WHERE sha256=?',(model_hash,)).fetchone()
            if not row:raise ValueError('Configured BIM model is not provisioned')
            content=bytes(row[0])
            if hashlib.sha256(content).hexdigest()!=model_hash:raise ValueError('BIM model hash mismatch')
            return content

    def save_files(self, db, sid, oid, files):
        sid=str(UUID(sid));oid=str(UUID(oid))
        previous={r[0]:(r[1],r[2]) for r in db.execute(
            'SELECT name,sha256,byte_length FROM localization.files WHERE session_id=? AND observation_id=?',(sid,oid)).fetchall()}
        refs={name:(hashlib.sha256(content).hexdigest(),len(content)) for name,content in files.items()}
        if previous:
            if previous!=refs:raise ValueError('Observation bytes changed')
            return
        additional=sum(len(v) for k,v in files.items() if k!='rgb.jpg')
        # Bound this prototype's footprint in the shared free database.
        used=db.execute('SELECT COALESCE(sum(octet_length(content)),0) FROM localization.files').fetchone()[0]
        if used+additional>100*1024*1024:
            raise ValueError('Localization storage is full; archive completed sessions before more captures')
        for name,content in files.items():
            db.execute('INSERT INTO localization.files (session_id,observation_id,name,sha256,byte_length,content) VALUES (?,?,?,?,?,?)',
                       (sid,oid,name,*refs[name],None if name=='rgb.jpg' else content))

    def restore_files(self, db, session, data):
        sid=str(UUID(session.session_id))
        # Local cache paths are not identity; rebase both evidence and retry records.
        for batch in session.batches.values():
            for item in batch['observations']:
                oid=str(UUID(item['observation_id']))
                item['_folder']=str((Path(data)/sid/'observations'/oid).resolve())
        for item in session.evidence.values():
            oid=str(UUID(item['observation_id']))
            folder=Path(data)/sid/'observations'/oid;folder.mkdir(parents=True,exist_ok=True)
            item['_folder']=str(folder.resolve())
            required=[ref for ref in item['files'] if ref['name']!='rgb.jpg']
            if all((folder/r['name']).is_file() and (folder/r['name']).stat().st_size==r['byte_length'] for r in required):continue
            rows=db.execute('SELECT name,sha256,content FROM localization.files WHERE session_id=? AND observation_id=? AND content IS NOT NULL',(sid,oid)).fetchall()
            content_by_name={r[0]:(r[1],bytes(r[2])) for r in rows}
            for ref in required:
                name=ref['name']
                if name not in {'frame.json','depth.f32','confidence.u8'}:raise ValueError('Invalid stored file')
                digest,content=content_by_name[name]
                if digest!=ref['sha256'] or hashlib.sha256(content).hexdigest()!=digest:raise ValueError('Stored evidence hash mismatch')
                temp=folder/(name+'.tmp');temp.write_bytes(content);temp.replace(folder/name)
