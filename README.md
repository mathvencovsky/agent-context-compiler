# NINA — context compiler for AI agents

NINA turns public technical documentation (HTML and PDF) into token-budgeted
context for AI agents. Every fragment it returns carries its provenance and a
literal quote from the source, so an agent can cite what it read.

| Operation | Input | Output |
|---|---|---|
| `resolve` | a URL | a structured document (block tree) with provenance, as Markdown and JSON |
| `assemble` | a question, 1–20 URLs, a token budget | the most useful fragments within the budget, each with citable evidence |

Interfaces: HTTP API (FastAPI), Python SDK (`nina-client`), MCP server.

**Status:** private MVP. Milestones M0–M9 are done; M10, a pre-registered
end-to-end agent evaluation, is in progress ([status](#status)).

## Design guarantees

- **Deterministic.** No LLM in extraction or selection: the same URLs and query
  give the same output. LLMs appear only in the benchmark, to answer and judge.
- **Provenance on every output.** Blocks and fragments carry `document_id`,
  `version_id`, `block_id`, `source_url` and, for PDFs, `page`.
- **SSRF-safe fetching.** Every outbound request, from httpx and from headless
  Chromium, goes through one guard (`app/fetch/guard.py`). It validates every
  resolved IP; blocks loopback, private, link-local, CGNAT and cloud-metadata
  addresses; connects to the validated IP; and respects `robots.txt`.
- **Untrusted content stays data.** Extracted text is returned with
  `trust: "untrusted_source"`.
- **Explicit failure.** Partial or empty extraction is reported in `warnings`;
  the API never returns an empty 200 silently.

## How it works

```
URL ──▶ fetch guard (SSRF, robots.txt) ──▶ HTML │ PDF │ SPA (Playwright)
    ──▶ IR block tree (app/extract) ──▶ Markdown / JSON                    resolve
    ──▶ chunks (≤ 800 tokens) ──▶ lexical (tsvector) + vector (pgvector) index

question + URLs + budget ──▶ lexical + vector search ──▶ RRF fusion
    ──▶ cross-encoder rerank (top 30) ──▶ redundancy-aware greedy selection
    ──▶ fragments + evidence                                               assemble
```

`assemble` works on demand: it resolves the URLs it receives (cache first),
indexes them and queries. There is no crawler. Contracts and algorithms are
specified in [`docs/SPEC.md`](docs/SPEC.md).

| Path | Role |
|---|---|
| `app/fetch` | guarded HTTP client, robots.txt, render decision, Playwright rendering |
| `app/extract` | HTML/PDF → IR block tree, Markdown, metadata |
| `app/index` | chunking, embeddings, lexical and vector indexing |
| `app/retrieve` | search, RRF, rerank, selection, `assemble_context` |
| `app/api`, `app/usage` | FastAPI routes, API keys, rate limits, usage events |
| `app/mcp_server` | MCP server, a thin client of the HTTP API |
| `app/providers` | embeddings, rerank and LLM interfaces, each with a deterministic fake |
| `app/store`, `migrations/` | SQLAlchemy models, S3 blob store, Alembic migrations |
| `sdk/python` | `nina-client`, a separate distribution |
| `bench/` | evaluation harness (see [Benchmark](#benchmark)) |

## Quickstart

Requirements: Docker and [uv](https://docs.astral.sh/uv/), which installs
Python 3.12.

```bash
make setup               # uv sync, .env (never over an existing one), services, migrations
make dev                 # API at http://localhost:8000
```

On Windows (no `make`): `powershell -ExecutionPolicy Bypass -File scripts/setup.ps1`,
then `uv run uvicorn app.main:app --reload`.

```bash
curl http://localhost:8000/health   # {"status":"ok","db":"ok","blobs":"ok"}, or 503 naming the broken dependency
```

- `assemble` needs an OpenAI key (`EMBEDDINGS_API_KEY`) and a Jina key
  (`RERANKER_API_KEY`); without them it answers `503 provider_not_configured`.
  To try it with no keys, set `PROVIDERS=fake` in `.env` (dev only): the
  fragment ranking is then not meaningful, and the response says so.
- First run takes about 1.5 minutes plus ~1.4 GB of Docker images
  (`docs/ONBOARDING.md`). On Windows, if `uv sync` fails building `jstyleson`,
  enable long paths or set a short `UV_CACHE_DIR`.
- Auth is off when `ENV=dev`.
- The OpenAPI schema is at `/openapi.json`; interactive docs are at `/docs`.
- SPA rendering and the `browser` tests need Chromium:
  `uv run playwright install chromium`.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/documents/resolve` | URL → structured document; cached, versioned by content hash |
| `GET` | `/v1/documents/{document_id}` | stored document without refetching (`version_id`, `formats` query params) |
| `POST` | `/v1/context/assemble` | question + URLs + budget → fragments with evidence |
| `GET` | `/v1/usage` | per-key requests, tokens delivered, latency p50/p95 |
| `GET` | `/health` | database and blob-store health; no auth |

```bash
curl -s -X POST http://localhost:8000/v1/context/assemble \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"query": "Which header carries the API key?",
       "urls": ["https://docs.example.com/auth"], "max_tokens": 4000}'
```

```jsonc
{
  "fragments": [{
    "chunk_id": "…", "document_id": "doc_…", "version_id": "ver_…",
    "source_url": "https://docs.example.com/auth", "heading_path": ["Authentication"],
    "md": "…", "tokens": 182,
    "scores": {"lexical_rank": 1, "vector_rank": 3, "rrf": 0.032, "rerank": 0.91},
    "evidence": [{"block_id": "…", "page": null, "quote": "Send the key in the X-Api-Key header"}]
  }],
  "sources": [ /* per-URL status */ ],
  "token_usage": {"output_tokens": 1495, "budget_tokens": 4000, "tokenizer": "cl100k_base (approximate)"},
  "coverage": "sufficient",   // heuristic over the top rerank score
  "warnings": [],
  "trust": "untrusted_source"
}
```

- **Request fields:** `max_tokens` is 256–32000 (default 4000);
  `include_evidence` defaults to true. Ablation flags are rejected when
  `ENV=production`.
- **Partial failure:** if one URL fails, the response is still 200, with the
  failure in `sources[].status` and `warnings`. If every URL fails, it is 422
  `all_sources_failed`.
- **Errors:** every error uses `{"error": {"code", "message", "details"}}`. The
  full code table is in SPEC §8.

### Auth, limits and usage

```bash
uv run python -m app.admin create-key --name pilot-x   # prints nina_… once; only its sha256 is stored
uv run python -m app.admin revoke-key --name pilot-x
```

- When `ENV` is not `dev`, every `/v1` route requires
  `Authorization: Bearer <key>`; a missing or bad key gets 401 `unauthorized`.
- Each key has a token bucket and a daily render quota. Over the limit, the API
  returns 429 with `Retry-After`. The bucket is in memory, one per process.
- Every `/v1` request writes a usage event, and `GET /v1/usage` aggregates them.

### Python SDK

```bash
pip install ./sdk/python
```

```python
from nina_client import NinaClient

client = NinaClient("http://localhost:8000", "nina_...")
ctx = client.assemble(
    "Which header carries the API key?", ["https://docs.example.com/auth"]
)
for frag in ctx.fragments:
    print(frag.source_url, frag.md)
```

The client is typed and also has `resolve()` and `get_document()`. It retries
429/5xx responses honoring `Retry-After` and raises typed errors mapped from the
API's error codes.

### MCP server

```bash
API_URL=http://localhost:8000 API_KEY=nina_... uv run python -m app.mcp_server          # stdio
API_URL=http://localhost:8000 API_KEY=nina_... uv run python -m app.mcp_server --http   # streamable HTTP
```

It exposes three tools: `resolve_url`, `assemble_context` and `get_document`.
Each tool description warns that returned content is untrusted. Configs for
Claude Desktop and Claude Code are in [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

## Configuration

`.env.example` lists every variable. The main ones:

| Variable | Purpose |
|---|---|
| `ENV` | `dev` disables auth and allows ablation flags |
| `POSTGRES_*` | database connection |
| `EMBEDDINGS_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` | embeddings provider |
| `RERANKER_API_KEY`, `RERANKER_MODEL` | rerank provider (Jina) |
| `RESOLVE_CACHE_TTL_SECONDS` | how long a resolved version is served from cache (default 24 h) |
| `SELECTION_LAMBDA`, `MIN_VALUE`, `COVERAGE_LOW`, `COVERAGE_HIGH`, `RRF_K`, `LEXICAL_WEIGHT` | retrieval and selection parameters, frozen after calibration on the `dev` split |
| `RATE_LIMIT_CAPACITY`, `RATE_LIMIT_REFILL_PER_SEC`, `RENDER_DAILY_QUOTA` | per-key limits |
| `FETCHER_USER_AGENT`, `MAX_BODY_BYTES`, `ALLOWED_PORTS`, timeouts | fetcher behavior |
| `BENCH_ANSWERER_API_KEY`, `BENCH_JUDGE_API_KEY` | benchmark only |

## Benchmark

The harness in `bench/` is part of the product: NINA is only worth building if
it beats existing approaches on measured cost at equal quality.

### Single-call results (M8)

- **Setup:** a frozen corpus of 50 pages from 10 documentation sites (including
  SPAs and PDFs). One LLM answers from each strategy's context, and a different
  LLM judges the answer.
- **Results:** held-out `test` split, 41 tasks, budget 4,000 tokens.

| Strategy | Success (95% CI) | Context tokens | Cost per correct answer | Citation precision | Gave up (`INSUFFICIENT`) |
|---|---|---|---|---|---|
| **`ours` (NINA)** | 0.756 [0.610, 0.878] | 1,495 | $0.00525 | 0.585 | 0.000 |
| `naive_rag` (BM25) | 0.732 [0.585, 0.878] | 2,677 | $0.00862 | 0.512 | 0.098 |
| `naive_rag_vector` (BM25 + embeddings) | 0.683 [0.537, 0.829] | 2,673 | $0.00923 | 0.512 | 0.098 |
| `strong_rag` (overlap chunks, hybrid RRF, cross-encoder rerank) | 0.683 [0.537, 0.829] | 2,823 | $0.00968 | 0.512 | 0.098 |

**What this shows:** NINA used about half the context (53% of `strong_rag`'s),
and the RAG baselines cost 1.6–1.8× more per correct answer.

**What it does not show:**
- **No quality difference is significant.** All CIs overlap, and paired
  McNemar p for `ours` against each baseline ranges from 0.38 to 1.0.
- **The sample is small and self-made.** There are only 41 tasks, drafted by an
  LLM from NINA's own extraction, with one URL each.
- **One run is noisy.** `naive_rag` got identical context in two runs, and 4
  of its 41 verdicts still changed.
- **It prices a single answer call,** not an agent session.

### End-to-end agent evaluation (M10, in progress)

Pre-registered in [`bench/PREREG.md`](bench/PREREG.md) and pushed to GitHub
before any run:

- **Tasks:** real Stack Overflow questions with accepted answers, from the
  2023–2024 data dump (CC BY-SA), with 5 URLs per task.
- **Arms:** the same agent (Claude Sonnet 5.5) answers each task once per arm:
  no docs, full-page Markdown (the primary comparator), strong RAG, NINA through
  its MCP server, built-in WebFetch, and Context7.
- **Metrics:** success, with a 7.5-point non-inferiority margin, and billed USD
  per correct task, with the judge checked against human labels.
- **Reporting rule:** the headline is the lower 95% bound of the cost ratio,
  whatever it turns out to be.

```bash
uv run python -m bench.run --strategies naive_rag,ours --split test --budget 4000   # add --real to use the paid providers
uv run python -m bench.sweep --strategies naive_rag,strong_rag,ours --split test --budgets 2000,4000 --real
uv run python -m bench.power --psi 0.25                                             # M10 sample size
```

## Development

| Command | What it runs |
|---|---|
| `make up` / `make down` | start / stop Postgres and SeaweedFS |
| `make migrate` | `alembic upgrade head` |
| `make check` | `ruff check`, `ruff format --check`, `mypy app` (strict), `pytest -m "not browser"` |
| `make test` | `pytest` only |
| `make dev` | API with auto-reload |

- **Test markers:**
  - `integration` needs `make up`.
  - `browser` needs Chromium.
  - `allow_network` marks loopback-server tests.
- **Fast offline gate:** `uv run pytest -m "not integration and not browser"`.
- **CI** (GitHub Actions) starts the infrastructure, migrates, runs
  `make check`, then the browser tests.
- **Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic,
  Postgres 16 + pgvector, httpx, lxml, readability-lxml, extruct, Playwright,
  pdfplumber, tiktoken, boto3 (SeaweedFS locally, AWS S3 in production),
  structlog, pytest + syrupy, ruff, mypy.
- **Licenses:** dependencies are MIT, BSD or Apache-2.0 only.
- **No network in tests:** external providers sit behind `app/providers/`
  interfaces with deterministic fakes, so tests never call the network.

## Status

| Milestone | Scope | State |
|---|---|---|
| M0–M7 | repo and CI, secure fetcher, frozen corpus and baselines, HTML/PDF/SPA extraction, persistence and `resolve`, chunking and indexing, search and `assemble` | done |
| M8 | full benchmark and calibration (on `dev` only) | done; decision: proceed |
| M9 | API keys, rate limits, usage, MCP server, SDK | done |
| M10 | pre-registered end-to-end agent evaluation | in progress |
| M11 | deploy and observability | not started |

Out of scope for the MVP: crawling and URL discovery, continuous indexing, OCR,
a web UI, billing, and multi-tenancy beyond API keys.

## Documentation

| File | Content |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | architecture, API contracts, schemas, algorithms (source of truth) |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | milestones M0–M11 with acceptance criteria |
| [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | pilot setup: API key, curl, SDK, MCP config |
| [`bench/PREREG.md`](bench/PREREG.md) | M10 pre-registration and amendments |
| [`PROGRESS.md`](PROGRESS.md) | current state, per milestone |
| [`DECISIONS.md`](DECISIONS.md) | log of non-obvious decisions |
| [`docs/HUMANO.md`](docs/HUMANO.md) | steps that need a human |
| [`CLAUDE.md`](CLAUDE.md) | engineering rules for contributors and agents |

Everything except this README and `bench/PREREG.md` is written in Portuguese.

## Security and license

- Report vulnerabilities privately to the maintainer, not in a public issue.
- Secrets live only in `.env`, which git ignores.
- The project is private during the MVP; a license will be chosen before any
  public release.
