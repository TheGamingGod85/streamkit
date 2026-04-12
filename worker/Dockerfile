FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	UV_LINK_MODE=copy \
	UV_COMPILE_BYTECODE=1 \
	UV_PYTHON_DOWNLOADS=0

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY api ./api
COPY worker ./worker
COPY streamkit_mcp ./streamkit_mcp
COPY mcp ./mcp
COPY migrations ./migrations

RUN uv sync --frozen --no-dev --python 3.12

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PATH="/app/.venv/bin:$PATH" \
	PYTHONPATH="/app"

RUN apt-get update \
	&& apt-get install -y --no-install-recommends ffmpeg ca-certificates \
	&& rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY api ./api
COPY worker ./worker
COPY streamkit_mcp ./streamkit_mcp
COPY mcp ./mcp
COPY migrations ./migrations

CMD ["celery", "-A", "worker.celery_app:celery_app", "worker", "-l", "info", "-Q", "streamkit,video,metadata,image"]
