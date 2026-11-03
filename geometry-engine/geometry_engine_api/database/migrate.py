"""Apply schema migrations once before starting API/worker replicas."""
from pathlib import Path

from alembic import command
from alembic.config import Config


def migration_config(connection=None) -> Config:
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).with_name('migrations')))
    if connection is not None:
        config.attributes['connection'] = connection
    return config


def upgrade_database(connection=None):
    command.upgrade(migration_config(connection), 'head')


if __name__ == '__main__':
    from sqlalchemy.exc import SQLAlchemyError
    from geometry_engine_api.database.connection import DatabaseConfigurationError
    try:
        upgrade_database()
    except (SQLAlchemyError, DatabaseConfigurationError):
        raise SystemExit('Database migration failed. Check PostgreSQL availability and database settings.') from None
    print('Database migrations applied.')
