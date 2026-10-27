"""Run the Geometry Engine HTTP service with python main.py."""

import uvicorn

from geometry_engine_api.app import app


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
