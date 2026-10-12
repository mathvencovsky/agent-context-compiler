# NINA

**Compilador de contexto para agentes.** NINA transforma documentação técnica
pública (HTML e PDF) em pacotes de contexto otimizados para agentes de IA, com
proveniência e evidência rastreável em cada fragmento retornado.

Agentes que leem documentação ao vivo gastam tokens com navegação, rodapés e
HTML irrelevante, e frequentemente citam trechos que não existem. NINA resolve
isso em duas operações:

| Operação | Entrada | Saída |
|---|---|---|
| **`resolve`** | uma URL | documento estruturado (árvore de blocos) com proveniência, em Markdown e JSON |
| **`assemble`** | pergunta + URLs + orçamento de tokens | os fragmentos mais úteis, cada um com evidência citável |

A API é consumível por **HTTP**, por um **SDK Python** e por um **servidor MCP**.

> **Codinome e nomes.** "NINA" é o codinome do projeto; o pacote de distribuição
> é `nina` e o código da aplicação vive em `app/`, conforme a estrutura fixada em
> [`docs/SPEC.md`](docs/SPEC.md) §2.

---

## Por que NINA é diferente

- **Determinístico, sem LLM no caminho crítico.** Extração e seleção não usam
  modelos de linguagem — o resultado é reproduzível e auditável. LLMs aparecem
  apenas no benchmark (para responder e julgar).
- **Proveniência obrigatória.** Todo bloco e todo fragmento carrega
  `document_id`, `version_id`, `block_id`, `source_url` e, em PDFs, `page`.
  Nenhuma saída sem origem.
- **Seguro por construção.** Toda requisição de saída — do cliente HTTP e do
  navegador headless — passa por um guard único com proteção contra SSRF
  (validação de todos os IPs resolvidos, bloqueio de endereços internos e de
  metadados de nuvem, respeito a `robots.txt`).
- **Conteúdo tratado como não confiável.** O texto extraído de páginas externas
  é dado, nunca instrução; as respostas o marcam com `trust: "untrusted_source"`.
- **A tese é mensurável.** Um harness de avaliação (`bench/`) compara NINA com
  extratores existentes (incluindo um RAG ingênuo competente) em tarefas reais.
  Se não houver vantagem demonstrável, o projeto não avança — a medição faz
  parte do produto, não é acessório.

---

## Posicionamento: NINA vs. extratores de Markdown (Firecrawl, Jina, etc.)

Ferramentas como Firecrawl e Jina Reader resolvem **extração**: dada uma URL,
devolvem Markdown limpo (e, no caso do Firecrawl, fazem crawling de sites
inteiros). NINA faz extração também (`resolve`), mas o produto é a **segunda
operação** — `assemble` — que esses extratores não têm: transformar uma pergunta
e um orçamento de tokens nos fragmentos certos, com evidência rastreável.

A diferença, em uma frase: um extrator entrega **a página limpa**; NINA entrega
**a resposta montada, sob orçamento, com a prova**.

### Onde NINA ganha vantagem

| | Extrator de Markdown (Firecrawl / Jina) | **NINA** |
|---|---|---|
| **Seleção sob orçamento** | ❌ devolve a página inteira; você monta o RAG | ✅ `assemble` seleciona os fragmentos mais úteis para `max_tokens` |
| **Evidência rastreável** | ❌ Markdown solto; rastreabilidade por sua conta | ✅ cada fragmento cita `block_id` + `quote` literal da IR |
| **Determinismo** | ⚠️ varia; alguns usam LLM na limpeza | ✅ extração e seleção sem LLM — mesma URL, mesmo resultado |
| **Proveniência completa** | ⚠️ URL de origem | ✅ `document_id` + `version_id` + `block_id` + `page` (PDF) |
| **Versionamento + cache** | ⚠️ por chamada | ✅ versões por `content_hash`; reresolver inalterado não recria nada |
| **Segurança SSRF auditada** | — (serviço hospedado) | ✅ guard único em HTTP e navegador, revisado por red-team |
| **Integração nativa com agentes** | SDK / API | ✅ HTTP + SDK Python + **servidor MCP** |

O ganho central é **eficiência de tokens com rastreabilidade**: entregar menos
contexto, mais relevante e citável, em vez de despejar a página inteira no
prompt do agente — o que reduz custo e alucinação de citação.

### Onde um extrator maduro ainda leva vantagem

Honestidade de escopo, para alinhar expectativas de piloto:

- **Crawling e descoberta de URLs.** NINA é **sob demanda, sem crawler** (fora de
  escopo do MVP). Firecrawl varre sites inteiros.
- **Cobertura e maturidade.** Extratores comerciais lidam há mais tempo com
  anti-bot, JS pesado e um universo maior de sites. NINA é um MVP.
- **Disponibilidade.** São serviços hospedados que funcionam hoje; NINA está em
  construção.

### Vantagem comprovada vs. hipótese

Tudo acima é o diferencial **pretendido** — e, por decisão de projeto, **não
conta como vantagem até o benchmark provar**. O teste decisivo (marco M8) não é
nem o Firecrawl: é o **`naive_rag`**, o RAG competente que um bom dev montaria em
uma tarde. No benchmark, o Firecrawl entra em pé de igualdade de pipeline
(Markdown do Firecrawl + o mesmo chunking/BM25 do `naive_rag`), de modo que a
comparação isola a qualidade de **extração + seleção**, não o crawling.

A pergunta que o M8 responde, com intervalos de confiança: *em que categoria, se
em alguma, o NINA (`ours`) vence o `naive_rag` sem sobreposição de IC?* Enquanto
essa resposta não existir, trate os itens acima como hipóteses a validar, não
como fatos de marketing.

---

## Arquitetura

```
                       ┌─────────────────── app/fetch (guard SSRF) ───────────────────┐
   URL ───────────────▶│  guard → cliente HTTP (conecta no IP validado) → robots.txt  │
                       └───────────────────────────────┬──────────────────────────────┘
                                                        │ HTML / PDF
                              ┌─────────────────────────┼─────────────────────────┐
                              ▼                          ▼                         ▼
                       HTML (readability           PDF (pdfplumber,          SPA → navegador
                       + walker lxml)              página a página)          (Playwright, guard)
                              └─────────────────────────┼─────────────────────────┘
                                                        ▼
                                          IR — árvore de blocos (app/extract)
                                                        │
                              ┌─────────────────────────┼─────────────────────────┐
                              ▼                                                     ▼
                       Markdown / JSON                                  chunks → índice léxico
                       (resolve)                                        + vetorial (pgvector)
                                                                                    │
   pergunta + URLs + orçamento ──▶ busca híbrida (RRF) ──▶ rerank ──▶ seleção sob orçamento ──▶
                                                              fragmentos + evidência (assemble)
```

O MVP opera **sob demanda**: `assemble` resolve as URLs recebidas (usando cache
quando possível), indexa e consulta. Não há crawler. Veja a especificação
completa — contratos de API, esquemas da IR, modelo de dados e algoritmos de
chunking/seleção — em [`docs/SPEC.md`](docs/SPEC.md).

### Stack

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 + Alembic ·
Postgres 16 + pgvector · httpx · lxml / readability-lxml / extruct ·
Playwright (Chromium) · pdfplumber · tiktoken · S3 (SeaweedFS local, AWS em
produção) · structlog · pytest · ruff · mypy (strict em `app/`) · Docker Compose.

Todas as dependências são MIT/BSD/Apache-2.0. Provedores externos (embeddings,
reranker, LLM do benchmark, Firecrawl) ficam atrás de interfaces em
`app/providers/`, cada uma com uma implementação fake determinística; os testes
nunca acessam a rede externa.

---

## Status

NINA é construído em marcos (M0–M10), um de cada vez. Cada marco fecha com o
gate verde (`ruff` + `mypy` + `pytest`), critérios de aceite atendidos e CI
verde no GitHub Actions.

| Marco | Entrega | Status |
|---|---|---|
| **M0** | Fundação: repositório, infraestrutura, CI | ✅ Concluído |
| **M1** | Fetcher seguro (SSRF, limites, robots.txt) | ✅ Concluído |
| **M2** | Corpus congelado, harness e baselines | ✅ Concluído |
| **M3** | IR e extração de HTML | ✅ Concluído |
| **M4** | Renderização por navegador e extração de PDF | ✅ Concluído |
| **M5** | Persistência e endpoint `resolve` | ✅ Concluído |
| **M6** | Chunking e indexação | ✅ Concluído |
| **M7** | Busca, seleção e endpoint `assemble` | ✅ Concluído |
| **M8** | Benchmark completo e calibração | ◧ Etapa 1 (calibração dev) concluída; Etapa 2 (test) pendente |
| **M9** | Chaves de API, limites, MCP e SDK | ◻️ Pendente |
| **M10** | Deploy e observabilidade | ◻️ Pendente (hospedagem) |

**Pipeline completo de ponta a ponta (M1, M3, M4, M5, M6, M7) está implementado
e verificado:** HTTP seguro → HTML/PDF → IR → Markdown/JSON → persistência +
cache → `resolve`/`get-document` → chunks indexados (tsvector lexical +
embeddings vetoriais `text-embedding-3-small` no pgvector) → busca híbrida (RRF)
+ rerank (Jina) + seleção sob orçamento → `POST /v1/context/assemble`. As duas
operações do produto (`resolve` e `assemble`) já respondem por HTTP. Os marcos de
segurança (M1, M4) passaram por revisão adversarial independente, sem findings
HIGH/MEDIUM em aberto. Falta a calibração do benchmark completo (M8).

**O benchmark (M2) já mede baselines com dados reais.** Corpus congelado de 50
páginas (10 fontes, incluindo SPAs e PDFs), 137 tarefas validadas por humano nas
cinco categorias com split dev/test estratificado, e uma primeira execução com os
modelos reais (respondedor OpenAI, juiz Anthropic) estabeleceu a linha de base: o
`naive_rag` — o RAG competente a ser batido — acerta ~73% no split `dev`, contra
~26% do `raw_html` bruto. É o número que o pipeline `ours` precisará superar no M8.
O detalhe por marco está em [`PROGRESS.md`](PROGRESS.md); as decisões técnicas não
óbvias, em [`DECISIONS.md`](DECISIONS.md).

---

## Começando

**Pré-requisitos:** Docker, [uv](https://docs.astral.sh/uv/) e Python 3.12
(o uv instala e fixa a versão por projeto).

```bash
# 1. Dependências e configuração
uv sync
cp .env.example .env                   # .env fica fora do git

# 2. Infraestrutura local (Postgres + pgvector, S3 via SeaweedFS, bucket)
make up
make migrate                           # habilita a extensão pgvector

# 3. (Opcional) navegador para renderização de SPA e testes 'browser'
uv run playwright install chromium     # --with-deps chromium no Linux/CI

# 4. Rodar
make check                             # ruff + mypy (strict) + pytest
make dev                               # API local em http://localhost:8000
make down                              # derruba a infraestrutura
```

Verifique a saúde do serviço:

```bash
curl http://localhost:8000/health
# {"status":"ok","db":"ok","blobs":"ok"}  (HTTP 200)
```

O `/health` retorna **503** com o campo quebrado em `"error"` se Postgres ou o
S3 estiverem indisponíveis — nunca 200 com uma dependência quebrada.

### Comandos (`Makefile`)

| Comando | Ação |
|---|---|
| `make up` / `make down` | sobe / derruba Postgres e o S3 local |
| `make migrate` | aplica as migrações do Alembic |
| `make check` | `ruff` + `mypy` + `pytest` (gate completo) |
| `make test` | somente `pytest` |
| `make dev` | API local com reload |
| `make bench ARGS="..."` | executa o benchmark |

> **Windows / PowerShell.** `make` pode não estar disponível; rode os comandos
> que cada alvo encapsula (ex.: `docker compose up -d`,
> `uv run alembic upgrade head`,
> `uv run ruff check . ; uv run mypy app ; uv run pytest`). O separador de
> comandos é `;`, não `&&`.

### Marcadores de teste

- Gate padrão: `uv run pytest -m "not integration and not browser"` — rápido, sem
  serviços nem navegador.
- `integration`: exige Postgres e S3 reais (via `make up`).
- `browser`: exige Chromium (`uv run playwright install chromium`); roda num
  passo dedicado do CI.

---

## API: `resolve` e `get-document` (M5)

O primeiro endpoint público transforma uma URL em um documento estruturado com
proveniência e o persiste com cache. É consumível por HTTP hoje; o SDK Python e
o servidor MCP entram no M9. A especificação completa dos contratos está em
[`docs/SPEC.md`](docs/SPEC.md) §8; o schema OpenAPI vivo (com exemplos) fica em
`http://localhost:8000/openapi.json` e a UI interativa em
`http://localhost:8000/docs`.

Autenticação está **desligada em `ENV=dev`** (o Bearer token é do M9).

### `POST /v1/documents/resolve`

Resolve uma URL: valida pelo guard SSRF, serve do cache quando há uma versão
recente, senão busca (pelo cliente auditado), extrai para a IR, persiste o corpo
bruto e a IR no S3 e devolve o envelope da SPEC §8.

**Corpo da requisição**

| Campo | Tipo | Default | Descrição |
|---|---|---|---|
| `url` | `string` | — (obrigatório) | URL pública http/https a resolver |
| `formats` | `("markdown" \| "blocks")[]` | `["markdown","blocks"]` | quais representações incluir na resposta |
| `force_refresh` | `boolean` | `false` | ignora o cache e força nova busca |
| `render` | `"auto" \| "never" \| "always"` | `"auto"` | política de renderização por navegador (SPA) |

```bash
curl -s -X POST http://localhost:8000/v1/documents/resolve \
  -H "Content-Type: application/json" \
  -d '{"url":"https://peps.python.org/pep-0008/"}'
```

**Corpo da resposta (200)**

```jsonc
{
  "document_id": "doc_0123456789abcdef",   // doc_ + sha1(url_normalizada)[:16]
  "version_id":  "ver_fedcba9876543210",   // ver_ + sha256(corpo_bruto)[:16]
  "source":   { "url": "...", "final_url": "...", "content_type": "text/html",
                "fetched_at": "...", "etag": "...", "last_modified": "...",
                "content_hash": "...", "render_mode": "http" },
  "metadata": { "title": "...", "lang": "en", "published_at": null,
                "modified_at": null, "jsonld": [] },
  "markdown": "# ...",         // presente se "markdown" ∈ formats, senão null
  "blocks":   [ /* ... */ ],   // presente se "blocks"   ∈ formats, senão null
  "stats":    { "blocks": 42, "tokens_markdown": 1234,
                "tokens_raw_html": 5678, "cache_hit": false },
  "warnings": [],
  "trust":    "untrusted_source"
}
```

Cada bloco carrega `block_id`, `type`, `heading_path`, `text`, `links` e, em
PDFs, `page` — a proveniência exigida pelo CLAUDE.md. `trust` é sempre
`"untrusted_source"`: o conteúdo externo é dado, nunca instrução.

### Cache e versionamento

- **Hit:** se existe uma versão com `fetched_at` dentro do TTL
  (`RESOLVE_CACHE_TTL_SECONDS`, default 24h) e `force_refresh=false`, a resposta
  vem do storage com `stats.cache_hit=true` e **zero** rede.
- **Miss:** respeita `robots.txt`, busca pelo cliente seguro e extrai.
- **Conteúdo inalterado:** se o `content_hash` do corpo bate com o da versão atual,
  só o `fetched_at` é atualizado — **nenhuma versão nova** é criada (dedup).
- **Conteúdo alterado:** um `content_hash` diferente cria uma **nova versão** e
  passa a ser a versão corrente do documento.
- **304 Not Modified:** quando um cache/transport à frente responde 304, só o
  `fetched_at` é atualizado, sem nova versão. (O cliente auditado não envia
  headers condicionais — ver [`DECISIONS.md`](DECISIONS.md) 2026-10-11.)

### `GET /v1/documents/{document_id}`

Recupera um documento já resolvido **direto do storage, sem refetch**
(`stats.cache_hit=true`). Query params opcionais: `version_id` (default = versão
corrente) e `formats`.

```bash
curl -s "http://localhost:8000/v1/documents/doc_0123456789abcdef"
curl -s "http://localhost:8000/v1/documents/doc_0123456789abcdef?formats=markdown"
```

### Erros (SPEC §8)

Todos os erros usam o mesmo envelope, com o HTTP da tabela §8:

```json
{ "error": { "code": "blocked_destination", "message": "...", "details": {} } }
```

| `code` | HTTP | Quando |
|---|---|---|
| `invalid_url` | 400 | URL malformada ou esquema não http/https |
| `blocked_destination` | 400 | destino resolve para IP interno/metadados ou porta fora da allow-list |
| `robots_disallowed` | 403 | `robots.txt` proíbe o caminho |
| `robots_unavailable` | 503 | `robots.txt` indisponível (falha fechada) |
| `too_large` | 413 | corpo excede o limite de tamanho |
| `unsupported_content_type` | 415 | conteúdo não é HTML nem PDF |
| `extraction_empty` | 422 | PDF sem texto extraível (OCR fora de escopo) |
| `fetch_failed` | 502 | falha de transporte (DNS, conexão, redirects demais) |
| `timeout` | 504 | timeout de conexão ou total |
| `not_found` | 404 | GET de documento/versão inexistente |
| `all_sources_failed` | 422 | `assemble`: todas as URLs falharam ao resolver |
| `ablation_not_allowed` | 400 | `assemble`: flag de ablação enviada com `ENV=production` |

---

## API: `assemble` + núcleo de busca e seleção (M7)

O segundo endpoint público — e o produto de fato — transforma uma pergunta, um
conjunto de URLs e um orçamento de tokens nos fragmentos mais úteis, cada um com
evidência citável. O **núcleo matemático** da recuperação híbrida vive em
`app/retrieve/`: toda a matemática (RRF e seleção) é determinística, sem LLM e
sem rede; a busca em banco é coberta por teste de integração com o
`FakeEmbeddingProvider` e o rerank pelo `FakeRerankProvider` (nenhum provider
real é tocado em teste).

### `POST /v1/context/assemble`

Resolve 1–20 URLs (reusando o cache do `resolve`), indexa a versão corrente de
cada uma, roda busca léxica + vetorial, funde por RRF, reranqueia e seleciona sob
`max_tokens`, devolvendo os fragmentos com proveniência e evidência literal.

**Corpo da requisição**

| Campo | Tipo | Default | Descrição |
|---|---|---|---|
| `query` | `string` | — (obrigatório) | a pergunta a responder |
| `urls` | `string[]` | — (1 a 20) | URLs públicas a montar o contexto |
| `max_tokens` | `int` | `4000` | orçamento de tokens de saída (256–32000) |
| `include_evidence` | `boolean` | `true` | inclui a `evidence` literal nos fragmentos |
| `use_lexical` / `use_vector` / `use_rerank` | `boolean` | `true` | flags de ablação (SPEC §6) — **aceitas só fora de produção** |
| `selection` | `"greedy" \| "truncate"` | `"greedy"` | estratégia de seleção (ablação) |

```bash
curl -s -X POST http://localhost:8000/v1/context/assemble \
  -H "Content-Type: application/json" \
  -d '{"query":"Qual header autentica as requisições?",
       "urls":["https://docs.example.com/auth"],
       "max_tokens":4000}'
```

**Corpo da resposta (200)** — `fragments[]` (cada um com `chunk_id`,
`document_id`, `version_id`, `source_url`, `heading_path`, `md`, `tokens`,
`scores{lexical_rank,vector_rank,rrf,rerank}` e `evidence[]{block_id,page,quote}`),
`sources[]` (status por URL), `token_usage{output_tokens,budget_tokens,tokenizer}`,
`coverage` (`insufficient`/`partial`/`sufficient` — heurística do maior rerank
ABSOLUTO; limiares calibrados no M8: `coverage_low=0.6137`,
`coverage_high=0.7746`), `warnings[]` e `trust: "untrusted_source"`.

**Semântica de falha (SPEC §8):** se **uma** URL falha, a resposta continua
**200** com o erro em `sources[].status` + `warnings` (nunca um 200 vazio e
silencioso); se **todas** falham, a resposta é **422** `all_sources_failed`. Uma
flag de ablação com valor não-default sob `ENV=production` → **400**
`ablation_not_allowed`.

### Núcleo (funções)

| Função | Módulo | O que faz |
|---|---|---|
| `rrf_fuse(ranked_lists, *, k)` | `app/retrieve/rrf.py` | Fusão por reciprocal rank: `score(d) = Σ 1/(k + rank_j(d))`, `k=60` (SPEC §6), genérica na chave, com desempate determinístico pela chave. |
| `lexical_search(session, *, query, version_ids, limit=50)` | `app/retrieve/lexical.py` | Top-50 por `ts_rank_cd` sobre o `tsvector` gerado (`'simple'`), restrito às versões, posições 1-based, desempate por `(version_id, ord)`. |
| `vector_search(session, *, query_embedding, version_ids, limit=50)` | `app/retrieve/vector.py` | Top-50 por distância de cosseno pgvector (índice HNSW), restrito às versões, mesmo desempate. |
| `rerank_candidates(query, fused, *, provider, use_rerank, documents, top_n=30)` | `app/retrieve/rerank.py` | Rerank dos 30 melhores: score do provedor clampado a `[0,1]`, ou — com rerank desligado — o RRF normalizado (min-max; único/empate → `1.0`). |
| `select(candidates, *, budget_tokens, lambda_, min_value)` + `Candidate` | `app/retrieve/select.py` | Seleção gulosa sob orçamento: a cada passo maximiza `(V_i − λ·max_sim)/√t_i` (calibrado no M8: `λ=0.3`, `min_value=0.0`), penaliza quase-duplicatas, ordena a saída por `(document_id, ord)`. Inclui `cosine` numericamente seguro. |
| `assemble_context(req, *, session_factory, blobs, robots, resolver, transport, embeddings, reranker, settings, now)` | `app/retrieve/assemble.py` | Orquestra os passos 1–7 da SPEC §6: resolve+indexa cada URL (falha independente), busca léxica+vetorial, RRF, rerank, seleção (ou ablação `truncate`), e monta `Fragment`s com proveniência completa e `evidence.quote` **literal** da IR (≤300 chars). Sem LLM. |
| `assemble(req, ...)` (handler HTTP) | `app/api/assemble.py` | Handler `def` síncrono de `POST /v1/context/assemble`: seams `Depends` injetáveis (resolve + `provide_embeddings`/`provide_reranker`), rejeita ablação em produção (`AblationNotAllowed`/400), mapeia `AssembleResult` → `AssembleResponse`, 200 na falha parcial e `AllSourcesFailed`/422 quando todas as URLs falham. |

### Provedor de rerank (`app/providers/rerank/`)

O rerank fica atrás de uma interface, com um fake determinístico usado em TODOS
os testes e o adaptador real nunca exercido em teste (igual ao padrão de
embeddings/LLM).

| Símbolo | Módulo | O que faz |
|---|---|---|
| `RerankProvider` (Protocol) | `base.py` | `model: str`; `rerank(query, documents) -> list[float]` com um score em `[0,1]` por documento **na ordem de entrada**. |
| `FakeRerankProvider` | `fake.py` | Score determinístico por sobreposição Jaccard de tokens (disjunto → `0.0`, idêntico → `1.0`), zero rede. Usado em todos os testes. |
| `JinaRerankProvider` | `jina.py` | Adaptador real da API Jina (`POST /v1/rerank`, Bearer, httpx próprio, min-max para `[0,1]`, `MissingAPIKey` sem chave). Nunca usado em testes. |

Os schemas `AssembleRequest` / `AssembleResponse` (SPEC §8) estão em
`app/api/schemas.py`. O endpoint é consumível por HTTP hoje; o SDK Python e o
servidor MCP entram no M9. A rota aparece no OpenAPI vivo em
`http://localhost:8000/openapi.json`.

---

## Calibração dos parâmetros de seleção (M8 Etapa 1)

A Etapa 1 do M8 é o **ponto de decisão** do projeto: calibrar os parâmetros de
seleção (`selection_lambda`, `min_value`, `coverage_low`, `coverage_high`) numa
grade, medindo cada combinação **apenas no split `dev`** com os provedores
reais, e **congelar** os vencedores em `app/config.py`. A **regra de honestidade**
é inegociável e reforçada em código: o calibrador filtra `split == "dev"`; o
split `test` (41 tarefas) nunca é tocado na calibração.

Os parâmetros de seleção são **injetáveis**: `OursRunner` aceita `lambda_` /
`min_value` (default = os valores de hoje) e `embeddings` / `reranker` (default =
os fakes determinísticos, então todo teste continua offline). A calibração injeta
os provedores REAIS (OpenAI embeddings + Jina reranker); nenhum teste os usa.

### Utilitário de calibração (`bench/calibrate.py` — execução humana)

Utilitário humano que gasta chaves; **não** é coletado pelo pytest. Só a LÓGICA
PURA é unit-testada (`tests/unit/bench/test_calibrate.py`), sem rede.

| Função | O que faz |
|---|---|
| `pick_best(results)` | Escolhe a combinação vencedora: taxa de sucesso (desc), desempate por menor custo/tarefa concluída, depois menor média de tokens de contexto, depois `(lambda_, min_value)` crescente — vencedor único e determinístico. |
| `percentiles(scores, pcts)` | Percentis por posição (nearest-rank, mesma convenção de `report._percentile`), ex.: `p33`/`p66`. |
| `coverage_from_scores(scores)` | Deriva `(coverage_low, coverage_high) = (p33, p66)` da distribuição do **máximo de rerank por tarefa** (score absoluto) observado no `dev` — a mesma grandeza que `assemble._coverage` classifica em runtime, então limiar e observável são a mesma coisa. Heurística honesta sobre o observável real, sem rótulos. |
| `run_calibration(...)` | Carrega SÓ `split == "dev"`, mede as 9 combinações (λ ∈ {0.3,0.5,0.7} × min_value ∈ {0.0,0.05,0.1}) no orçamento 4000 com os provedores reais, escolhe o vencedor, deriva a cobertura do máximo-rerank por tarefa e escreve o artefato `bench/results/calibration_<ts>/` (inclui `coverage_scores.csv` para auditoria). |
| `recompute_coverage(...)` | Recomputa SÓ os limiares de cobertura a partir da distribuição do máximo-rerank do `dev` (apenas Jina, via cache; sem OpenAI, sem a grade). CLI: `--coverage-only`. |

Invocação (após subir a infra e com as chaves no `.env` real):

```bash
uv run python -m bench.calibrate --real
```

### Economia de cota (cache persistente)

`lambda_`/`min_value` afetam só a seleção gulosa final; a busca, o RRF e o rerank
são invariantes a eles. Por isso o calibrador roda a parte cara (busca + rerank
do top-30) **uma vez por tarefa** e aplica só a seleção pura por combinação,
cortando as chamadas de rerank/embeddings de 9×96 para ~1×96. Um cache
persistente content-addressed (`bench/provider_cache.py`, `bench/.cache/`,
gitignored) guarda rerank e embeddings em disco: uma re-execução não re-chama a
Jina para pares `(query, documents)` já vistos, e a **Etapa 2** (split `test`,
vários orçamentos) reusa o mesmo cache (o rerank de uma tarefa é invariante ao
orçamento), gastando zero cota de Jina para pares já computados.

### Resultado congelado (M8 Etapa 1)

A calibração real no `dev` (96 tarefas, orçamento 4000) elegeu **λ = 0.3,
min_value = 0.0** (taxa de sucesso 0.8125 — no dev, a seleção vai melhor **sem**
corte por valor mínimo). Os limiares de cobertura são o **p33/p66 do máximo
ABSOLUTO de rerank por tarefa** no dev (96 tarefas) — a mesma grandeza que
`assemble._coverage` observa: **coverage_low = 0.6137**, **coverage_high =
0.7746**. Com eles (arredondados em config), as 96 tarefas dev se dividem via
`assemble._coverage` em 32/31/33 (insufficient/partial/sufficient), ou seja, a
cobertura não colapsa num único rótulo. Valores congelados em `app/config.py` / `.env.example`. A calibração foi
feita **só no split `dev`**; o split `test` não foi tocado (regra de honestidade
do M8). Detalhes e a tabela das 9 combinações em [`PROGRESS.md`](PROGRESS.md) e
[`DECISIONS.md`](DECISIONS.md).

> **Cobertura — score absoluto.** O `JinaRerankProvider` devolve o
> `relevance_score` **absoluto** da Jina (comparável entre tarefas); a
> normalização min-max usada pela **seleção** vive em `rerank_candidates` (não
> afeta a cobertura). Assim `assemble._coverage` e os limiares congelados
> comparam a **mesma grandeza** (o máximo absoluto por tarefa). Os números são
> reproduzíveis a partir de
> `bench/results/coverage_20261012T193038_322186/coverage_scores.csv`
> (re-rodar `percentiles` sobre o CSV reproduz 0.6137/0.7746, sem gastar Jina).

---

## Benchmark: o que depende de você

O harness de avaliação (M2) é o que prova a tese do produto. Ele exigiu insumos
humanos — julgamento, contas pagas e decisões de produto — que já foram
fornecidos:

1. ✅ **Sites (`bench/sites.yaml`)** — 10 fontes de documentação pública com
   estruturas variadas (referência de API com tabelas, guias, changelogs, 2 SPAs
   e 3 PDFs), 5 URLs cada, congeladas sem bloqueios de `robots.txt`.
2. ✅ **LLMs do benchmark** — respondedor OpenAI (`gpt-6.1-sol`) e juiz Anthropic
   (`claude-sonnet-5-5`), modelos de fornecedores diferentes, com as chaves no
   `.env`.
3. ✅ **Preços (`bench/prices.yaml`)** — preço por milhão de tokens de cada
   modelo, usado para o custo por tarefa concluída.
4. ✅ **137 tarefas validadas** — rascunhadas a partir da IR limpa e revisadas
   por humano; recategorizadas nas cinco categorias canônicas com split
   estratificado (96 `dev` / 41 `test`). Este é o passo mais importante do
   projeto: tarefas erradas tornam o benchmark sem sentido.
5. ◻️ **(Opcional) chave do Firecrawl** — para o baseline comercial; sem ela,
   esse runner é pulado.

> **Reprodutibilidade (ver [`DECISIONS.md`](DECISIONS.md)):** a geração atual de
> modelos OpenAI e Anthropic descontinuou o parâmetro de temperatura, então a
> SPEC §10.3 ("temperatura 0") foi substituída por prompt fixo + `seed` fixo +
> intervalos de confiança por bootstrap.

Expandir o conjunto de tarefas no futuro: `bench/draft_tasks.py` rascunha novos
candidatos a partir do corpus e `bench/review_tasks.py` os valida. Detalhes e
checklist completo em [`docs/HUMANO.md`](docs/HUMANO.md).

---

## Documentação do projeto

| Arquivo | Conteúdo |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | Arquitetura, contratos de API, esquemas e algoritmos (fonte da verdade) |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Os 11 marcos (M0–M10) com tarefas e critérios de aceite |
| [`docs/HUMANO.md`](docs/HUMANO.md) | O que depende de você e quando (bloqueios de marco) |
| [`docs/PROMPTS.md`](docs/PROMPTS.md) | Prompts de condução do projeto |
| [`PROGRESS.md`](PROGRESS.md) | Estado atual detalhado, atualizado a cada marco |
| [`DECISIONS.md`](DECISIONS.md) | Registro de decisões técnicas não óbvias |
| [`CLAUDE.md`](CLAUDE.md) | Regras permanentes de engenharia do projeto |

---

## Segurança

- **SSRF:** toda saída de rede (httpx e Playwright) passa por `app/fetch/guard.py`,
  que valida todos os IPs resolvidos e bloqueia loopback, redes privadas,
  link-local, CGNAT e o endereço de metadados de nuvem (`169.254.169.254`). O
  cliente HTTP conecta no IP já validado para fechar a janela de DNS rebinding.
- **Segredos:** nunca no código nem nos testes — apenas em `.env` (fora do git);
  `.env.example` documenta todas as variáveis.
- **Licenças:** apenas MIT/BSD/Apache-2.0; GPL/AGPL são proibidas.

Encontrou um problema de segurança? Abra uma issue privada ou contate o
mantenedor; não divulgue publicamente antes da correção.

---

## Licença

Projeto privado durante o MVP. A licença será definida antes de qualquer
distribuição pública.
