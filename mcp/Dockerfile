# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

FROM base AS builder

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./

RUN mkdir -p api worker streamkit_mcp mcp

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --python ${PYTHON_VERSION}

COPY api ./api
COPY worker ./worker
COPY streamkit_mcp ./streamkit_mcp
COPY mcp ./mcp

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --python ${PYTHON_VERSION}

FROM base AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app" \
    HOME="/home/app"

RUN groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/streamkit \
    && chown -R app:app /app /tmp/streamkit /home/app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/api /app/api
COPY --from=builder --chown=app:app /app/worker /app/worker
COPY --from=builder --chown=app:app /app/streamkit_mcp /app/streamkit_mcp
COPY --from=builder --chown=app:app /app/mcp /app/mcp

USER app

EXPOSE 8001

CMD ["python", "-m", "streamkit_mcp.server"]
