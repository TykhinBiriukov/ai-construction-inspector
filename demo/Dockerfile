FROM python:3.12-slim AS prepared
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
WORKDIR /repo
COPY demo /repo/demo
COPY docs/evidence /repo/docs/evidence
COPY geometry-engine/geometry_engine/visualization/model_comparison_viewer.html /repo/geometry-engine/geometry_engine/visualization/model_comparison_viewer.html
ENTRYPOINT ["python", "/repo/demo/run.py"]

FROM prepared AS full
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libgl1 libegl1 libglib2.0-0 libusb-1.0-0 libidn2-0 libssl3 libcurl4 libjson-c5 && rm -rf /var/lib/apt/lists/*
COPY geometry-engine /repo/geometry-engine
RUN python -m pip install --no-cache-dir '/repo/geometry-engine[scan-vs-bim,segmentation]'
