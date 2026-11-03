"""Lazy database connections; importing the API never connects to PostgreSQL."""
from functools import lru_cache
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session


class DatabaseConfigurationError(ValueError):
    """Invalid database configuration, without exposing connection secrets."""


def database_url() -> URL:
    load_dotenv(Path(__file__).resolve().parents[2] / '.env')
    try:
        value = os.getenv('DATABASE_URL', '').strip()
        if value:
            url = make_url(value)
            if url.drivername not in ('postgres', 'postgresql', 'postgresql+psycopg'):
                raise ValueError('Unsupported driver')
            url = url.set(drivername='postgresql+psycopg')
        else:
            password = os.getenv('POSTGRES_PASSWORD', '')
            if not password:
                raise ValueError('Missing database password')
            url = URL.create(
                'postgresql+psycopg', username=os.getenv('POSTGRES_USER', 'geometry_engine'),
                password=password, host=os.getenv('POSTGRES_HOST', '127.0.0.1'),
                port=int(os.getenv('POSTGRES_PORT', '5432')),
                database=os.getenv('POSTGRES_DB', 'geometry_engine'))
        if not url.database or not url.host:
            raise ValueError('Missing database or host')
        return url
    except (ValueError, TypeError, ArgumentError):
        raise DatabaseConfigurationError('Configure DATABASE_URL or POSTGRES_* in .env') from None


@lru_cache(maxsize=1)
def get_engine():
    return create_engine(database_url(), pool_pre_ping=True, pool_size=5, max_overflow=5,
                         isolation_level='READ COMMITTED',
                         connect_args={'connect_timeout': 5, 'options': '-c timezone=UTC'})


def get_database_session():
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session
