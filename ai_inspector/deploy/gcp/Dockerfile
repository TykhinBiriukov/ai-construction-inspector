FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY ai_inspector/ /app/
RUN pip install --no-cache-dir '.[evidence]'
CMD ["python", "-m", "uvicorn", "deploy.gcp.app:create_backend", "--factory", "--host", "0.0.0.0", "--port", "8200", "--no-access-log", "--timeout-graceful-shutdown", "30"]
