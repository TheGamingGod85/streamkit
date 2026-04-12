.PHONY: dev test deploy

dev:
	uv run uvicorn api.main:create_app --factory --host 0.0.0.0 --port 8000 --reload

test:
	uv run pytest

deploy:
	docker compose -f docker-compose.prod.yml up -d --build
