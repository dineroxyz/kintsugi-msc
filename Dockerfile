# Kintsugi — one image, three Railway services.
# Default command runs the console (control plane API mounted at /api);
# the alpha and beta demo services override the start command:
#   alpha: uvicorn alpha.main:app --host 0.0.0.0 --port $PORT --app-dir services
#   beta:  uvicorn beta.main:app  --host 0.0.0.0 --port $PORT --app-dir services

FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git rsync \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV PYTHONPATH=/app:/app/services

CMD uvicorn console.app:app --host 0.0.0.0 --port ${PORT:-8100}
