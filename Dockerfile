# syntax=docker/dockerfile:1
# Tractive ingestor – multi-arch image (amd64 i arm64 / Raspberry Pi 5).
# Build:  docker build -t tractive-ingestor .
# Run:    docker run --rm --env-file .env tractive-ingestor check

ARG PYTHON_VERSION=3.13

# ---------- builder: build deps + venv ----------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build deps jen pro případ, že pro arm64 chybí wheel (asyncpg, aiohttp, ...).
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /src
# Nejdřív jen závislosti → lepší cache vrstev
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# ---------- runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_FORMAT=json \
    HEALTH_HOST=0.0.0.0 \
    HEALTH_PORT=8080

RUN groupadd --system --gid 10001 tractive \
 && useradd --system --uid 10001 --gid tractive --home-dir /app --shell /usr/sbin/nologin tractive \
 && mkdir -p /app/fixtures \
 && chown -R tractive:tractive /app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
USER 10001:10001

# Vestavěný health server (src/tractive_ingestor/health.py):
#   /healthz = liveness  (proces + event loop odpovídá)
#   /readyz  = readiness (přihlášeno k Tractive + kanál dostal event/keep-alive za posledních 90 s)
EXPOSE 8080

# Container HEALTHCHECK záměrně míří na /healthz, NE na /readyz:
# krátký výpadek Tractive (429, údržba, reconnect s backoffem) by jinak označil kontejner
# jako unhealthy a orchestrátor/autoheal by ho zbytečně restartoval – reconnect přitom
# řeší ingestor sám. /readyz je pro externí monitoring (Uptime Kuma, curl přes Tailscale…).
# Python stdlib místo curl → do slim image není potřeba nic doinstalovat.
# Pozn.: HEALTH_PORT=0 health server vypne → tenhle check pak selže.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('HEALTH_PORT', '8080'), timeout=4)"]

ENTRYPOINT ["python", "-m", "tractive_ingestor"]
CMD ["run"]
