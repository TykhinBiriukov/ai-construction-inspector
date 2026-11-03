"""Alembic environment; credentials are loaded at runtime, never from the INI."""
from alembic import context
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from geometry_engine_api.database.connection import database_url
from geometry_engine_api.database.models import Base


def run_online(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        # Serializes migration runners sharing this database. The lock is
        # released by PostgreSQL on commit, rollback or connection failure.
        connection.execute(text('SELECT pg_advisory_xact_lock(739174026)'))
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=Base.metadata,
                      literal_binds=True, dialect_opts={'paramstyle': 'named'})
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied = context.config.attributes.get('connection')
    if supplied is not None:
        run_online(supplied)
    else:
        engine = create_engine(database_url(), poolclass=NullPool, connect_args={'connect_timeout': 5})
        try:
            with engine.connect() as connection:
                run_online(connection)
        finally:
            engine.dispose()
