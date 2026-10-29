.PHONY: setup up down migrate check test bench dev m10a-pending extract-eval m10b-retrieval

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

# Zero-cost extraction eval (M10b, Phase P): downloads the pinned WCXB docs
# subset and the PDF facts set from GitHub (checksum-verified, never
# committed), then measures the extractors on dev + PDFs and checks the
# committed baseline's automatic gates. The test split is one look per change:
#   make extract-eval ARGS="--split test"
extract-eval:
	uv run python -m bench.extract_eval.fetch
	uv run python -m bench.extract_eval.run $(ARGS)

# Zero-cost M10b retrieval experiments (research round 4, fronts H and I) on
# the dev slice; rules fixed in DECISIONS 2026-10-29. OpenAI/Jina are read from
# the cache only (a miss stops the run, never pays); local models go through a
# llama.cpp server on 127.0.0.1. Examples:
#   make m10b-retrieval ARGS="lexical --half A --vector none"
#   make m10b-retrieval ARGS="rerank --half A --comparator none --reranker-url http://127.0.0.1:8081 --reranker-model qwen3-reranker-0.6b-q8_0"
m10b-retrieval:
	uv run python -m bench.m10b_retrieval $(ARGS)
