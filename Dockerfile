FROM python:3.11-slim

WORKDIR /app

# Install socat for virtual serial port pairs (RTU support)
RUN apt-get update && \
    apt-get install -y --no-install-recommends socat && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies first (cached layer)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e . 2>/dev/null || true

# Copy source
COPY dertwin/ ./dertwin/
COPY docker-entrypoint.sh ./

# Re-install in case pyproject.toml needs the source tree
RUN pip install --no-cache-dir -e .

# pyserial is needed by the TCP bridge relay in the entrypoint
RUN pip install --no-cache-dir pyserial

RUN chmod +x /app/docker-entrypoint.sh

# Configs and register maps are mounted at runtime — not baked in.
ENV CONFIG_PATH=/app/configs/simple_config.json

ENTRYPOINT ["/app/docker-entrypoint.sh"]