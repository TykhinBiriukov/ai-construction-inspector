"""HTTP entry point for queued inspections, results and viewer exports."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, TimeoutError as DatabasePoolTimeout

from .database.connection import DatabaseConfigurationError
from .routers.inspections import router as inspections_router
from .routers.viewers import router as viewers_router
from .routers.results import router as results_router


def create_app():
    app = FastAPI(title="Geometry Engine API", version="0.1.0")
    app.include_router(inspections_router)
    app.include_router(viewers_router)
    app.include_router(results_router)

    @app.exception_handler(DatabaseConfigurationError)
    @app.exception_handler(DBAPIError)
    @app.exception_handler(DatabasePoolTimeout)
    async def database_error(request: Request, error: Exception):
        return JSONResponse(status_code=503, headers={'Retry-After': '5'}, content={
            'detail': 'Database unavailable. Check database settings and apply migrations.'
        })

    return app


app = create_app()
