# PROGRESS

Marco atual: M0 (Fundação do repositório) — concluído.

## Concluídos

### M0 — Fundação do repositório

- `pyproject.toml` gerenciado por uv (dist `nina`, Python 3.12), com grupos de
  dependências (dev, bench placeholder) e config de ruff, mypy (strict em
  `app/`) e pytest (`asyncio_mode=auto`, marcador `integration`). `.python-version`
  fixado em 3.12; `uv.lock` resolvido.
- Esqueleto de pacotes da SPEC seção 2 em `app/` com módulos placeholder
  (docstring-only) e diretórios de dados (`bench/`, `tests/fixtures/`, etc.).
- `app/config.py`: `Settings` (pydantic-settings) lida de `.env`, `database_url`
  computado (psycopg3), `get_settings()` com `lru_cache`. `.env.example` 1:1 com
  os campos de Settings, sem segredos.
- `docker-compose.yml`: Postgres `pgvector/pgvector:pg16`, object storage
  SeaweedFS `chrislusf/seaweedfs:3.80` (S3 na porta 8333) e `s3-init` (aws-cli)
  que cria o bucket `nina-blobs` uma vez. Tudo via `${VAR}` do `.env`, com
  healthchecks.
- `app/logging.py`: `configure_logging()` (structlog em JSON, ligado ao logging
  stdlib) e `RequestIdMiddleware` (ASGI) que gera `request_id` por requisição,
  faz bind nos contextvars do structlog, loga início/fim (método, path, status,
  duration_ms) e limpa os contextvars ao final.
- `app/main.py`: fábrica `create_app()` (+ `app = create_app()` para o uvicorn)
  com `GET /health` checando Postgres (`SELECT 1` via SQLAlchemy, com
  `connect_timeout` para falhar rápido) e blobs (boto3 `head_bucket`). Resposta
  exata `{"status","db","blobs"}`: 200 só quando ambos ok, 503 com o campo que
  falhou em `"error"` (regra de falha explícita — nunca 200 com dependência
  quebrada). Cada check é isolado por try/except, então uma falha não mascara a
  outra.
- Alembic: `alembic.ini` sem URL hardcoded, `migrations/env.py` lendo a URL de
  `app.config.get_settings()` (offline + online), `migrations/script.py.mako` e
  a primeira migração `0001_enable_vector_extension` (só `CREATE EXTENSION IF
  NOT EXISTS vector`, sem tabelas — tabelas ficam para M5/M6).
- Testes: `tests/test_smoke.py` (sem serviços, não marcado `integration`),
  `tests/test_health_explicit_failure.py` (sem serviços; faz patch de
  `_check_db`/`_check_blobs` e prova o 503 nas duas direções e com ambos
  quebrados, mais o 200 quando tudo ok — cobertura automatizada da regra de
  falha explícita, roda no CI sem Docker) e `tests/integration/test_health.py`
  (marcado `integration`, afirma 200 e corpo ok exato contra serviços reais).
- `Makefile` com os alvos exatos do CLAUDE.md (up, down, migrate, check, test,
  bench, dev) com indentação por TAB real e prefixo `uv run`.
- `.github/workflows/ci.yml`: sobe a stack via docker compose no job, roda
  `make migrate` e depois `make check` contra Postgres + SeaweedFS, derruba a
  infra no fim.

## Pendências e dívidas técnicas

- Módulos placeholder (docstring-only) em `app/fetch`, `app/extract`,
  `app/store`, `app/index`, `app/retrieve`, `app/providers/*`, `app/usage`,
  `app/mcp_server` — implementados a partir do M1.
- `EMBEDDING_DIM=1024` é provisório; revisar ao escolher o provedor de
  embeddings no M6 (pode exigir migração do esquema).
- Grupo de dependências `bench` está vazio (placeholder); o harness entra no M2.
- O alvo `make bench` chama `python -m bench.run`, que ainda é placeholder.
- `make` não está instalado no host Windows de desenvolvimento; os alvos rodam
  no CI (Linux). Localmente, use os comandos `uv run ...` equivalentes.
- O objeto `.env` é gitignored; copie de `.env.example` antes de subir a infra.

## Como testar manualmente

Pré-requisito: `uv sync` e, para os passos com serviços, `cp .env.example .env`
(no Windows/PowerShell: `Copy-Item .env.example .env`).

1. Subir a infraestrutura: `make up` (ou `docker compose up -d --wait postgres
   seaweedfs` e `docker compose up s3-init` para criar o bucket).
2. Aplicar as migrações: `make migrate` (habilita a extensão `vector`).
3. Rodar a API local: `make dev` (uvicorn com reload) — ou
   `uv run uvicorn app.main:app --reload`.
4. Checar a saúde: `curl http://localhost:8000/health` → deve devolver 200 com
   `{"status":"ok","db":"ok","blobs":"ok"}`. Pare o Postgres ou o SeaweedFS e
   repita: a resposta vira 503 com o campo correspondente em `"error"`.
5. Rodar o gate completo: `make check` (ruff check + ruff format --check + mypy
   + pytest). O teste de integração do /health exige a infra no ar.
6. Derrubar a infraestrutura: `make down` (ou `docker compose down -v`).
