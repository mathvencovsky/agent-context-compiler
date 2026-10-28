.PHONY: setup up down migrate check test bench dev m10a-pending

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

# The zero-cost M10a measurements still to run (corpus and provider cache
# needed). Each cache-only script prints "paid calls"; any number above 0 means
# the cache missed and the run cost money. tokenizer_ratio uses Anthropic's free
# token-counting endpoint (an Anthropic key in .env).
m10a-pending:
	uv run python -m bench.m10_result_limits
	uv run python -m bench.page_health
	uv run python -m bench.m10a_baseline --real
	uv run python -m bench.m10a_order_dedup --half A
	uv run python -m bench.m10a_section_expand --half A
	uv run python -m bench.m10a_version --half A
	uv run python -m bench.tokenizer_ratio
