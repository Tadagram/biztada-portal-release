# ── Base Image ───────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    fonts-liberation \
    xvfb \
    procps \
    && rm -rf /var/lib/apt/lists/*

# ── Target: Updater ──────────────────────────────────────────────────────────
FROM base AS updater

WORKDIR /app
COPY updater/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install Docker CLI for restarting worker containers
RUN install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    chmod a+r /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin && \
    rm -rf /var/lib/apt/lists/*

COPY updater /app/updater
CMD ["python", "-u", "/app/updater/worker_updater.py"]

# ── Target: Worker Runner ───────────────────────────────────────────────────
FROM base AS runner

WORKDIR /app

# Download cloudflared for Linux worker tunnel support
RUN curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared && \
    chmod +x /usr/local/bin/cloudflared

RUN pip install --no-cache-dir playwright python-dotenv requests paho-mqtt pycryptodome && \
    playwright install chromium --with-deps || true

COPY scripts/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh 2>/dev/null || true

CMD ["/bin/bash", "-c", "if [ -f /app/worker/run.sh ]; then bash /app/worker/run.sh; elif [ -f /app/worker/*.wrkx ]; then echo 'Worker binary detected. Starting supervisor...'; tail -f /dev/null; else echo 'Waiting for worker payload deployment from Updater...'; tail -f /dev/null; fi"]
