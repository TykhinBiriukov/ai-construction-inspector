FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /app
COPY localization_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY localization_service /app/localization_service
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
USER app
ENV PORT=8788 LOCALIZATION_DATA_DIR=/data
EXPOSE 8788
CMD ["python", "-m", "localization_service.cloud_server"]
