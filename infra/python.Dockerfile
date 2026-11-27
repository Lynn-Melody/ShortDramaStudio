FROM ghcr.io/astral-sh/uv:0.13.10 AS uv
FROM python:3.14-slim-trixie AS development
COPY --from=uv /uv /bin/uv
WORKDIR /workspace
ENV PATH="/workspace/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/apps/backend \
    UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-cache
COPY apps/backend apps/backend
COPY apps/agents apps/agents
COPY apps/render apps/render
COPY contracts contracts
COPY infra/init-bucket.py infra/init-bucket.py

FROM development AS render
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
