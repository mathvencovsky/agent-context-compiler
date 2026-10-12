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
| **M6** | Chunking e indexação | ◻️ Próximo |
| **M7** | Busca, seleção e endpoint `assemble` | ◻️ Pendente (chave de reranker) |
| **M8** | Benchmark completo e calibração | ◻️ Ponto de decisão |
| **M9** | Chaves de API, limites, MCP e SDK | ◻️ Pendente |
| **M10** | Deploy e observabilidade | ◻️ Pendente (hospedagem) |

**Pipeline de aquisição, extração e persistência (M1, M3, M4, M5) está completo
e verificado:** HTTP seguro → HTML/PDF → IR → Markdown/JSON → persistência +
cache → `resolve`/`get-document`. Os marcos de segurança (M1, M4) passaram por
revisão adversarial independente, sem findings HIGH/MEDIUM em aberto.

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
