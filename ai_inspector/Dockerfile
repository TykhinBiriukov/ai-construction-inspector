FROM python:3.14.8-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 INSPECTOR_DATA_DIR=/data PORT=8000
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src ./src
RUN pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home inspector && mkdir /data && chown inspector /data
USER inspector
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn ai_inspector.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
