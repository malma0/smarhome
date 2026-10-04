# The home model's service in a container. The model itself stays in Ollama on
# the host (it needs the GPU); the service reaches it at host.docker.internal.
#   docker compose --profile model-service up -d --build      (from the repo root)
FROM python:3.11-slim

WORKDIR /srv
COPY model_service/requirements.txt model_service/requirements.txt
RUN pip install --no-cache-dir -r model_service/requirements.txt

COPY app app
COPY model_service model_service

ENV MODEL_SERVICE_HOST=0.0.0.0 \
    MODEL_SERVICE_PORT=8090 \
    MODEL_SERVICE_OLLAMA_URL=http://host.docker.internal:11434 \
    PYTHONUNBUFFERED=1
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"
CMD ["python", "-m", "model_service"]
