FROM python:3.12.16-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MINION_DATA_DIR=/data
WORKDIR /app
COPY drawing_photo_service/requirements.lock /app/requirements.lock
COPY drawing_photo_service/requirements-observability.txt /app/requirements-observability.txt
RUN pip install --no-cache-dir -r requirements.lock -r requirements-observability.txt && useradd --uid 10001 --create-home minion && mkdir /data && chown minion:minion /data
COPY drawing_photo_service /app/drawing_photo_service
COPY ai_inspector/src/ai_inspector/observability.py /app/ai_inspector/observability.py
COPY ai_inspector/src/ai_inspector/__init__.py /app/ai_inspector/__init__.py
USER minion
EXPOSE 8080
CMD ["uvicorn", "drawing_photo_service.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
