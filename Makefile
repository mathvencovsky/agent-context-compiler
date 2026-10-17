.PHONY: setup up down migrate check test bench dev

# First run: dependencies, .env (never over an existing one), services, schema.
setup:
	uv sync
	test -f .env || cp .env.example .env
	docker compose up -d --wait --remove-orphans
	uv run alembic upgrade head

up:
	docker compose up -d --wait --remove-orphans

down:
	docker compose down

migrate:
	uv run alembic upgrade head

check:
	uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest -m "not browser"

test:
	uv run pytest

bench:
	uv run python -m bench.run $(ARGS)

dev:
	uv run uvicorn app.main:app --reload
