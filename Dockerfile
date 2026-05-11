# syntax=docker/dockerfile:1.7

# ---- Stage 1: build wheels in a fatter base ----
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml ./
COPY backend/src ./src
COPY backend/eval ./eval
RUN pip install uv && uv pip install --system --no-cache .

# Pre-warm the embedding model so the first request after deploy is fast.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# ---- Stage 2: slim runtime ----
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
# Carry the cached HF model so it doesn't re-download at runtime.
COPY --from=builder /root/.cache /root/.cache

COPY backend/src ./src
COPY backend/data ./data
COPY backend/eval ./eval
COPY backend/scripts/start.sh /start.sh
RUN chmod +x /start.sh

RUN useradd -u 1000 -m app && mkdir -p /data/logs && chown -R app:app /data /app /root/.cache
USER app

ENV AUDIT_LOG_PATH=/data/logs/audit.ndjson
ENV DATA_DIR=/app/data
ENV HF_HUB_DISABLE_SYMLINKS_WARNING=1

EXPOSE 8000 8001

CMD ["/start.sh"]
