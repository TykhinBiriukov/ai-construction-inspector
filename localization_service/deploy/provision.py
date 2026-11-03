"""Explicit one-time administrator setup; never run as part of server startup.

Creates a new restricted role/schema, and writes runtime
credentials to a local mode-0600 file. Refuses to replace existing credentials.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
from urllib.parse import quote, urlsplit, urlunsplit
import psycopg
from psycopg import sql


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin-env',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    target=args.output;pending=target.with_suffix('.pending.json')
    if target.exists() or pending.exists():raise SystemExit('Existing credentials: refusing overwrite')
    values={}
    for line in args.admin_env.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1);values[k.strip()]=v.strip().strip('\"\'')
    admin=values['INSPECTOR_DATABASE_URL']
    password=secrets.token_urlsafe(36);u=urlsplit(admin)
    suffix=u.username.split('.',1)[1] if '.' in u.username else None
    username='localization_app'+('.'+suffix if suffix else '')
    netloc=quote(username,safe='')+':'+quote(password,safe='')+'@'+u.hostname+(':'+str(u.port) if u.port else '')
    # Supabase pooler requires TLS; do not downgrade the supplied connection.
    if 'sslmode=' not in u.query:raise SystemExit('Admin URI must explicitly configure TLS')
    dsn=urlunsplit((u.scheme,netloc,u.path,u.query,u.fragment))
    config={'LOCALIZATION_SERVICE_TOKEN':secrets.token_urlsafe(36),
            'LOCALIZATION_DATABASE_URL':dsn,'PORT':'8788'}
    target.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(config,f)
    with psycopg.connect(admin,connect_timeout=15) as db:
        if db.execute("SELECT 1 FROM pg_roles WHERE rolname='localization_app'").fetchone():
            raise SystemExit('Role exists; refusing replacement. Pending config is NOT active.')
        db.execute(sql.SQL('CREATE ROLE localization_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT').format(sql.Literal(password)))
        db.execute(Path(__file__).with_name('schema.sql').read_text())
    # Preserve generated credentials even if verification needs a network retry.
    pending.replace(target)
    with psycopg.connect(dsn,connect_timeout=15) as db:
        assert db.execute('SELECT count(*) FROM localization.models').fetchone()[0]==0
        assert not db.execute("SELECT has_schema_privilege(current_user,'inspector','USAGE')").fetchone()[0]
    print('Dedicated role/schema provisioned; Inspector schema denied. Private config saved.')


if __name__=='__main__':main()
