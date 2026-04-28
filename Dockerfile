FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install --prefix=/install .

# =========================

FROM python:3.11-slim

WORKDIR /app

# runtime-only libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# copy python deps
COPY --from=builder /install /usr/local

# copy source code AFTER deps
COPY src/ ./src/
COPY config/ ./config/
COPY docker/ ./docker/

RUN chmod +x /app/docker/entrypoint.sh

# non-root user (audit friendly)
RUN useradd -m appuser
USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python - <<EOF
import urllib.request
urllib.request.urlopen("http://localhost:8000/health", timeout=5)
EOF

ENTRYPOINT ["/app/docker/entrypoint.sh"]