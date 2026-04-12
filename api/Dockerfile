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

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY api ./api
COPY worker ./worker
COPY streamkit_mcp ./streamkit_mcp
COPY mcp ./mcp
COPY migrations ./migrations

EXPOSE 8000

CMD ["uvicorn", "api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
