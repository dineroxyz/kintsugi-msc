# Kintsugi — one image, three Railway services, selected by KINTSUGI_ROLE:
#   console (default) — operations console + control plane API at /api
#   alpha             — demo order service
#   beta              — demo pricing service

FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git rsync \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV PYTHONPATH=/app:/app/services

CMD case "$KINTSUGI_ROLE" in \
      alpha) exec uvicorn alpha.main:app --host 0.0.0.0 --port ${PORT:-8080} ;; \
      beta)  exec uvicorn beta.main:app  --host 0.0.0.0 --port ${PORT:-8080} ;; \
      *)     exec uvicorn console.app:app --host 0.0.0.0 --port ${PORT:-8100} ;; \
    esac
