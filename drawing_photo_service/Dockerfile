FROM python:3.12.16-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MINION_DATA_DIR=/data
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r requirements.lock && useradd --uid 10001 --create-home minion && mkdir /data && chown minion:minion /data
COPY . /app/drawing_photo_service
USER minion
EXPOSE 8080
CMD ["uvicorn", "drawing_photo_service.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
