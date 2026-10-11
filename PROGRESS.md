# PROGRESS

Marco atual: M3 (IR e extração de HTML) — concluído. A camada HTML→IR
(`app/extract/`) está implementada e coberta por testes unitários e por uma
suíte de aceite sobre 18 fixtures HTML reais (snapshots syrupy de Markdown e da
IR serializada, cobertura 100% de `<table>`/`<pre>` do conteúdo principal,
limpeza de chrome, estabilidade de `block_id` e `heading_path`). Gate completo
verde (só o teste de integração do /health do M0 fica deselecionado sem Docker).
Próximo: M2 — Corpus congelado, harness e baselines (requer lista humana de
sites e tarefas validadas).

M1 (Fetcher seguro) permanece concluído e aprovado na revisão de segurança
independente (veredito APPROVED, sem findings HIGH/MEDIUM; o LOW-1 foi corrigido).

## Concluídos

### M3 — IR e extração de HTML

Conversão determinística de HTML público em IR (SPEC §4), sem LLM e sem rede no
caminho de extração. Pacote `app/extract/`:

- `app/extract/ir.py`: modelos Pydantic v2 da IR (`Document`, `Block`,
  `SourceInfo`, `Metadata`, `Link`, `Warning`), ids estáveis
  (`make_document_id`, `make_version_id`, `base_block_id`) e `assign_block_ids`
  (disambiguação `_2`/`_3` por chave de conteúdo — ver DECISIONS 2026-10-11
  "block_id estável sob edição"), além de `normalize_text`.
- `app/extract/jsonld.py`: `extract_metadata` (title/lang/published_at/
  modified_at com fallbacks documentados; JSON-LD preservado inalterado via
  extruct).
- `app/extract/html.py`: `extract_html` com decodificação por charset, seleção
  robusta de conteúdo principal (`<main>`/`<article>` → `role="main"` →
  contêineres Sphinx/MkDocs conhecidos), isolamento por readability-lxml com
  fallback obrigatório para o `<main>` original quando o readability descarta
  `table/pre/ul/ol/dl`, limpeza de chrome por tag/role/classe, e um walker que
  emite os oito tipos de bloco. O walker desce em contêineres aninhados
  (`<dd>`, `<li>`, admonitions, blockquotes, web components) para nunca perder
  blocos estruturais, e trata `table.highlighttable` como código (ver as três
  decisões de 2026-10-11 no DECISIONS.md).
- `app/extract/to_markdown.py`: `to_markdown` gera Markdown SEMPRE a partir da
  IR (nunca do HTML), com tabelas GFM, blocos de código cercados com linguagem,
  headings por nível e listas aninhadas com indentação de 2 espaços.

Fixtures e aceite (FEAT-003/FEAT-004):

- `tests/fixtures/html/` traz 18 páginas de documentação técnica real baixadas
  uma única vez pelo fetcher seguro do M1 (primeiro acesso de rede autorizado;
  licenças só MIT/BSD/Apache-2.0/PSF), com `SOURCES.md` registrando URL, fonte,
  licença, categoria e data. O downloader `_download_html_fixtures.py` usa só
  `app.fetch.client.fetch` e não é coletado pelo pytest.
- `tests/unit/extract/test_fixtures_snapshots.py`: snapshots syrupy do Markdown
  e da IR serializada (`model_dump(mode="json")`) por fixture, com `fetched_at`
  fixo e `content_hash` = sha256 dos bytes para determinismo. 36 snapshots em
  `tests/unit/extract/__snapshots__/` (versionados).
- `test_fixtures_coverage.py`: afirma que 100% das `<table>` de dados e dos
  `<pre>` de código do conteúdo principal viram blocos table/code na IR (conta
  espelhando a classificação do walker: `highlighttable` é código, calha de
  numeração não é conteúdo). Passa nas 18 fixtures.
- `test_fixtures_cleanup.py`: lista de strings proibidas por fixture (chrome de
  navegação/rodapé/feedback/ToC/permalink) que não podem aparecer em
  `block.text`.
- `test_block_id_stability.py`: mesma página duas vezes → ids idênticos; editar
  um parágrafo → só o id daquele bloco muda.
- `test_fixtures_heading_path.py`: `heading_path` em fixtures representativas.

#### Amostragem manual de heading_path (M3)

Verificado manualmente nas fixtures abaixo (observado = esperado, travado por
`test_fixtures_heading_path.py`):

- `python_tutorial_classes` (guia longo CPython):
  - heading h1 "9. Classes" → `heading_path == []` (é o topo).
  - parágrafo "Classes provide a means of bundling data and functionality…" →
    `["9. Classes"]`.
  - parágrafo "This is an example demonstrating how to reference…" →
    `["9. Classes", "9.2. Python Scopes and Namespaces",
    "9.2.1. Scopes and Namespaces Example"]` (h1 > h2 > h3).
- `python_stdlib_argparse` (guia de módulo CPython):
  - h1 do módulo ("argparse — …") → `heading_path == []`.
  - h2 "ArgumentParser objects" → `[<título do módulo>]`.
  - parágrafo "By default, ArgumentParser calculates the name of the program…"
    (subseção "prog") → `[<título do módulo>, "ArgumentParser objects", "prog"]`.
- `fastapi_first_steps` (tutorial FastAPI, MkDocs-Material):
  - h1 "First Steps" → `heading_path == []`.
  - parágrafo "In this case, OpenAPI is a specification…" →
    `["First Steps", "OpenAPI", 'API "schema"']` (h1 > h3 > h4).

O título do módulo em `python_stdlib_argparse` contém um travessão cujo byte
depende do charset; o teste captura o texto real do h1 em vez de fixá-lo, para
não acoplar a asserção à decodificação.

### M1 — Fetcher seguro

### M1 — Fetcher seguro

Aquisição HTTP segura com proteção contra SSRF, limites de corpo/timeout,
detecção de content-type/charset e robots.txt (SPEC §3.1, §3.2, §8). Escopo
estrito do marco: NÃO inclui renderização por navegador (Playwright) nem
extração de IR; `detect.py` cobre apenas content-type e charset.

- `app/fetch/errors.py`: hierarquia tipada `FetchError` + 8 subclasses 1:1 com
  a tabela de erros da SPEC §8 (`invalid_url` 400, `blocked_destination` 400,
  `robots_disallowed` 403, `robots_unavailable` 503, `too_large` 413,
  `unsupported_content_type` 415, `fetch_failed` 502, `timeout` 504). Cada uma
  carrega `code`/`http_status` como atributos de classe e `message`/`details`,
  prontas para o handler global do M5.
- `app/fetch/guard.py` (núcleo de segurança): `Resolver` (Protocol injetável),
  `SocketResolver` real (A+AAAA via `getaddrinfo`), `GuardResult` e
  `validate_url(...)`. Esquemas só http/https (senão `invalid_url`); portas via
  `allowed_ports` (senão `blocked_destination`); normalização (host minúsculo,
  fragmento removido, IDNA); rejeição de literais IPv4 em notação alternativa
  (decimal, octal, hex, formas curtas/empacotadas) ANTES de resolver; resolução
  de TODOS os A/AAAA e validação de CADA IP com falha fechada (um único IP não
  global rejeita o host). `_validate_ip` não confia só em `is_global`: rejeita
  loopback, link-local, privado, reservado, multicast, unspecified,
  `169.254.169.254` e `100.64.0.0/10`, e desembrulha formas IPv6 que embutem
  IPv4 (mapeado `::ffff:x`, NAT64 `64:ff9b::/96`, 6to4 `2002::/16`) revalidando
  o IPv4 embutido.
- `app/fetch/client.py` (único módulo fora de testes que usa httpx para saída):
  `FetchResult` tipado e `fetch(...)`. Conecta no IP validado (fecha DNS
  rebinding — ver DECISIONS 2026-10-11 D1), com `Host` restaurado ao hostname
  original, extensão `sni_hostname` = hostname e `verify=True`. Redirecionamentos
  manuais (`follow_redirects=False`, máx. 5), cada `Location` repassa pelo guard
  (redirect para IP privado => `blocked_destination`). Corpo lido em streaming e
  abortado ao cruzar 15 MB sem bufferizar o corpo inteiro (`too_large`), com
  curto-circuito por `Content-Length` sem confiar só nele. Timeouts de conexão
  (5s) e total (20s); UA computado `AgentContextBot/0.1 (+<contato>)`;
  `trust_env=False` (ignora proxies de ambiente); transport injetável.
- `app/fetch/robots.py`: `RobotsChecker` com fetch guardado injetável e relógio
  injetável. Busca `/robots.txt` pelo mesmo guard, cache por domínio com TTL
  configurável (24h), respeita `Disallow` para o UA próprio e para `*` (UA
  próprio tem precedência via `urllib.robotparser`). 4xx => permitido; 5xx /
  timeout / destino bloqueado => `robots_unavailable` (falha fechada, não
  cacheia como permitido); path proibido => `robots_disallowed`.
- `app/fetch/detect.py`: `parse_content_type` (media type + charset) e
  `detect_charset` (header > BOM UTF-8/16/32 > fallback utf-8). Puro, só stdlib.
  Sem decisão de renderização nem parsing de HTML (ficam para M3/M4).
- `app/config.py`: campos novos `allowed_ports` (tupla, parse de string CSV via
  `NoDecode` + validator) e `robots_cache_ttl_seconds`, e o helper `user_agent()`
  que compõe o UA a partir de `fetcher_contact_url`. `.env.example` espelha os
  dois novos campos (`ALLOWED_PORTS`, `ROBOTS_CACHE_TTL_SECONDS`).
- Testes (nenhum acessa a internet): `tests/unit/fetch/conftest.py` com fixture
  autouse que bloqueia conexões reais (patch de `socket.create_connection`,
  `getaddrinfo`, `socket.socket.connect`), com opt-out `@pytest.mark.allow_network`;
  escopada ao pacote de fetch para não afetar os testes M0 do /health (que usam
  TestClient em loopback). `_resolvers.py` com `FakeResolver` e
  `SequenceResolver` (público→privado para o teste de rebinding). Cobertura:
  blocklist SSRF parametrizada (todas as URLs de aceite do ROADMAP + porta 8080),
  literais em notação alternativa rejeitados antes de resolver (resolver com zero
  chamadas), conjunto misto de IPs com falha fechada, redirect público→privado
  recusado no 2º hop, DNS rebinding com asserção de que a conexão foi ao IP
  validado e sem 2ª resolução (inspeção do request no `MockTransport`:
  URL host = IP público, `Host`/`sni_hostname` = hostname, resolver chamado uma
  vez), abort de corpo >limite em streaming com contagem de chunks produzidos, e
  robots (Disallow own-UA e `*`, 4xx permitido, 5xx/erro => unavailable, cache
  com contador de chamadas e expiração por TTL). `test_client_tls_sni.py`
  (2 testes, `@pytest.mark.allow_network`): servidor TLS de loopback com cert
  emitido via `trustme` só para o hostname — prova em TLS real que conectar no
  IP com `sni_hostname`+`Host`=hostname e `verify` faz o servidor ver o SNI =
  hostname (não o IP) e o handshake validar (200), e que sem o SNI o handshake
  falha (controle). Fecha a ressalva "provável, não confirmada" do red-team.

Revisão de segurança independente (adversarial SSRF red-team), rodada 1 em
2026-10-12: veredito APPROVED. Nenhum finding HIGH ou MEDIUM. Um finding LOW
(LOW-1) e dois informativos (INFO-1, INFO-2):

- LOW-1 (defense-in-depth, não bloqueante) — um literal IPv4 em notação packed
  COM ponto final (`http://2130706433.`, `http://0x7f000001.`) pulava a checagem
  pré-resolução de literal: `host.isdigit()` era `False` por causa do ponto e o
  ramo dotted era pulado por `host.endswith(".")`. O backstop de validação por
  IP ainda rejeitava qualquer loopback/privado resolvido (sem alcance interno
  real), mas a camada 1 era contornada para essa grafia. **Corrigido**:
  `validate_url` agora remove um único ponto final do hostname na normalização
  (também arruma o caso de FQDN com ponto final), antes da checagem de literal
  e da resolução. Travado por testes: `http://2130706433.` e `http://0x7f000001.`
  agora dão `invalid_url` e os casos entram em `test_blocklist`,
  `test_alternate_notation_rejected_before_resolving` (resolver com zero
  chamadas) e no positivo `test_trailing_dot_fqdn_normalized`.
- INFO-1 — `unsupported_content_type` (415) definido mas não aplicado no M1
  (decisão de escopo documentada; reservado para M3/M4). Sem ação.
- INFO-2 — resposta 3xx sem header `Location` é devolvida como `FetchResult` de
  sucesso carregando o status 3xx. Corpo foi limitado e destino guardado; sem
  impacto de segurança. Marcos seguintes que assumirem 2xx em sucesso devem
  tratar isso explicitamente. Sem ação no M1.

Artefatos da revisão em `.agents/tasks/m1-fetcher/`: `m1-security-review.md`,
`m1-security-verdict.json`, `m1-security-fixes.md`.

Resultado do gate (host Windows, sem Docker), após a correção do LOW-1 e a
adição dos testes de TLS/SNI: `uv run ruff check .` → All checks passed;
`uv run ruff format --check .` → 79 files already formatted; `uv run mypy app`
→ Success, no issues (48 arquivos); `uv run pytest -m "not integration"` → 92
passed, 1 deselected. O único teste deselecionado/que falha sem serviços é
`tests/integration/test_health.py::test_health_ok_with_services_up` (M0), que
exige Postgres/S3 reais via Docker — coberto no CI. Todos os testes de fetch do
M1 são livres de rede (os dois de TLS/SNI usam só loopback via
`@pytest.mark.allow_network`) e passam sem serviços externos. A suíte de guard
passou de 25 para 30 casos com os três testes do LOW-1; o M1 ganhou ainda 2
testes de TLS real (SNI confirmado + controle sem SNI).

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

- `unsupported_content_type` (415) está definido em `app/fetch/errors.py` mas
  NÃO é aplicado no M1: o fetcher devolve corpo + content-type crus. A decisão
  de quais tipos são aceitáveis pertence aos marcos de extração (M3/M4).
  Reservado para não inventar contrato novo aqui. (Revisão de segurança INFO-1.)
- Uma resposta 3xx sem header `Location` é devolvida como `FetchResult` de
  sucesso com o status 3xx (não vira `fetch_failed`). Sem impacto de segurança
  no M1 (corpo limitado, destino guardado), mas marcos que assumirem 2xx em
  sucesso precisam tratar esse caso. (Revisão de segurança INFO-2.)
- `app/fetch/render.py` continua placeholder (Playwright é M4), assim como os
  demais módulos abaixo.
- `app/extract/pdf.py` continua placeholder (PDF é M4). O restante de
  `app/extract` (ir, html, jsonld, to_markdown) foi implementado no M3.
- Módulos placeholder (docstring-only) em `app/store`, `app/index`,
  `app/retrieve`, `app/providers/*`, `app/usage`, `app/mcp_server` —
  implementados a partir dos marcos seguintes.
- M3 (dívida/escopo): a limpeza de chrome e a seleção de conteúdo principal
  cobrem os padrões Sphinx/MkDocs/Starlight presentes nas 18 fixtures; outros
  temas podem exigir novos seletores (a postura é conservadora — na dúvida, não
  remover conteúdo, e o teste de strings proibidas guia). A estabilidade de
  `block_id` entre cópias idênticas é local às ocorrências anteriores (ver
  DECISIONS 2026-10-11 "block_id estável sob edição"). O gatilho de
  `readability_fallback` é por documento, não por bloco.
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
5. Rodar o gate completo: `make check` ou, no Windows/PowerShell,
   `uv run ruff check . ; uv run ruff format --check . ; uv run mypy app ;
   uv run pytest`. O teste de integração do /health (`integration`) exige a
   infra no ar; sem Docker, use `uv run pytest -m "not integration"` para a
   suíte livre de serviços (90 passed).
6. Derrubar a infraestrutura: `make down` (ou `docker compose down -v`).

### Fetcher seguro (M1)

Os testes de fetch são livres de rede e cobrem os critérios de aceite. Para
exercitar manualmente contra a internet (fora da suíte, que proíbe rede):

```python
from app.fetch.client import fetch

r = fetch("https://example.com/")
print(r.status, r.content_type, len(r.body), r.final_url)
```

A validação pura de URL (sem buscar nada) pode ser inspecionada com
`app.fetch.guard.validate_url(url, resolver=SocketResolver())` — devolve
`(normalized_url, hostname, validated_ip, port)` ou levanta um erro tipado.
Rodar só a suíte de fetch: `uv run pytest tests/unit/fetch -q`.

### IR e extração de HTML (M3)

A extração é determinística, lê fixtures salvas (sem rede) e não usa LLM. Para
exercitar manualmente sobre uma das fixtures reais:

```python
from datetime import UTC, datetime
from app.extract.html import extract_html
from app.extract.to_markdown import to_markdown
from app.extract.ir import SourceInfo

body = open("tests/fixtures/html/flask_api_reference.html", "rb").read()
src = SourceInfo(
    url="https://example.test/flask",
    final_url="https://example.test/flask",
    content_type="text/html; charset=utf-8",
    fetched_at=datetime(2026, 10, 11, tzinfo=UTC),
    etag=None,
    last_modified=None,
    content_hash="0" * 64,
    render_mode="http",
)
doc = extract_html(body, src)
print(len(doc.blocks), "blocks;", [w.code for w in doc.warnings])
print(to_markdown(doc)[:500])
```

Rodar só a suíte de extração: `uv run pytest tests/unit/extract -q`. Para
regenerar os snapshots syrupy após uma mudança intencional na extração:
`uv run pytest tests/unit/extract --snapshot-update` e depois `uv run pytest
tests/unit/extract` (sem a flag) para confirmar que o run limpo fica verde, sem
snapshots obsoletos. O gate completo do M3 (host Windows, sem Docker):
`uv run ruff check . ; uv run ruff format --check . ; uv run mypy app ;
uv run pytest -m "not integration"` → ruff All checks passed; format 92 files
already formatted; mypy Success (48 arquivos); pytest 218 passed, 1 deselected
(só o teste de integração do /health do M0, que exige Docker), 36 snapshots.
