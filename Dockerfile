FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e . 2>/dev/null || true

# Copy source
COPY dertwin/ ./dertwin/
COPY main.py* ./

# Re-install in case pyproject.toml needs the source tree
RUN pip install --no-cache-dir -e .

# Configs and register maps are mounted at runtime — not baked in.
# Default config path can be overridden via CMD or environment.
ENV CONFIG_PATH=/app/configs/simple_config.json

CMD python -m dertwin.main -c ${CONFIG_PATH}