FROM python:3.12-slim-bookworm AS demo-inputs
COPY ai_inspector/scripts/prepare_demo_inputs.py /tmp/prepare_demo_inputs.py
COPY resources/progress4d/inputs/ /tmp/progress-inputs/
COPY docs/evidence/2026-11-03/geometry-case-manifest.json /tmp/geometry-case-manifest.json
RUN python /tmp/prepare_demo_inputs.py --progress-inputs /tmp/progress-inputs --geometry-manifest /tmp/geometry-case-manifest.json --destination /opt/inspector-demo-inputs \
    && rm -rf /tmp/progress-inputs /tmp/geometry-case-manifest.json /tmp/prepare_demo_inputs.py
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=demo-inputs /opt/inspector-demo-inputs/ /opt/inspector-demo-inputs/
ENV INSPECTOR_SAVED_DEMO_INPUTS_DIR=/opt/inspector-demo-inputs
COPY ai_inspector/ /app/
RUN pip install --no-cache-dir '.[evidence]'
CMD ["python", "-m", "uvicorn", "deploy.gcp.app:create_backend", "--factory", "--host", "0.0.0.0", "--port", "8200", "--no-access-log", "--timeout-graceful-shutdown", "30"]
