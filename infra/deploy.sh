#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -f ".env" ]; then
	echo "Warning: .env is missing. docker compose will rely on environment variables." >&2
fi

if docker compose version >/dev/null 2>&1; then
	COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
	COMPOSE_CMD=(docker-compose)
else
	echo "Docker Compose is not installed." >&2
	exit 1
fi

"${COMPOSE_CMD[@]}" -f docker-compose.prod.yml up -d --build "$@"
