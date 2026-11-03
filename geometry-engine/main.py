"""Run the Geometry Engine HTTP service with python main.py."""

import uvicorn

from geometry_engine_api.app import app


if __name__ == "__main__":
    from geometry_engine_api.database.migrate import upgrade_database
    from geometry_engine_api.database.connection import DatabaseConfigurationError
    from sqlalchemy.exc import SQLAlchemyError

    try:
        upgrade_database()
    except (SQLAlchemyError, DatabaseConfigurationError):
        raise SystemExit('Database startup failed. Check PostgreSQL availability and database settings.') from None
    uvicorn.run(app, host="127.0.0.1", port=8000)
