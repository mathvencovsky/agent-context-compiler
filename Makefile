.PHONY: up down migrate check test bench dev

up:
	docker compose up -d

down:
	docker compose down

migrate:
	uv run alembic upgrade head

check:
	uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest

test:
	uv run pytest

bench:
	uv run python -m bench.run $(ARGS)

dev:
	uv run uvicorn app.main:app --reload
