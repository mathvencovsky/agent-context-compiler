# SPEC: Agent Context Compiler (MVP)

Versão 0.1. Mudanças nesta especificação exigem aprovação do humano e um registro em `DECISIONS.md`.

## 1. Visão geral do fluxo

```
URL ──> guard (SSRF, robots) ──> fetch HTTP ──┬──> HTML ──> extração ──┐
                                              │   (se insuficiente:    │
                                              │    render navegador)   ├──> IR (árvore de blocos)
                                              └──> PDF ──> pdfplumber ─┘          │
                                                                                  ├──> Markdown / JSON (resolve)
                                                                                  └──> chunks ──> índice lexical + vetorial
                                                                                                        │
pergunta + URLs + orçamento ──> busca híbrida (RRF) ──> rerank ──> seleção sob orçamento ──> fragmentos + evidência (assemble)
```

O MVP opera **sob demanda**: o `assemble` resolve as URLs recebidas (usando o cache quando possível), indexa e consulta. Não há crawler.

## 2. Estrutura do repositório

```
app/
  main.py                FastAPI app, montagem de rotas
  config.py              pydantic-settings
  api/                   rotas, schemas de request/response, erros, auth
  fetch/
    guard.py             validação de URL e de IP, política de portas
    client.py            cliente httpx com conexão no IP validado
    robots.py            cache e checagem de robots.txt
    detect.py            content-type, charset, decisão de renderização
    render.py            Playwright com interceptação de requisições
  extract/
    ir.py                modelos Pydantic da IR
    html.py              HTML -> IR
    pdf.py               PDF -> IR
    jsonld.py            metadados e JSON-LD
    to_markdown.py       IR -> Markdown
  store/
    models.py            SQLAlchemy
    repo.py              acesso a dados
    blobs.py             S3 (SeaweedFS local, AWS S3 em produção)
  index/
    tokens.py            contagem de tokens
    chunker.py           IR -> chunks
    indexer.py           grava chunks, tsvector e embeddings
  retrieve/
    lexical.py, vector.py, rrf.py, rerank.py, select.py, assemble.py
  providers/
    embeddings/ rerank/ llm/ firecrawl/   (base.py, fake.py, <real>.py em cada)
  usage/                 registro de uso e limites
  mcp_server/            servidor MCP
bench/
  corpus/                snapshots congelados (HTML/PDF) + manifest.json
  tasks/tasks.yaml       tarefas do benchmark
  runners/               uma estratégia por arquivo
  answer.py              chamada ao LLM respondedor
  grade.py               correção
  report.py              agregação e relatório
  run.py                 CLI
sdk/python/              pacote cliente
tests/
  unit/ integration/ fixtures/html/ fixtures/pdf/ golden/
migrations/
```

## 3. Aquisição segura (`app/fetch`)

### 3.1 Guard

- Esquemas permitidos: `http` e `https`.
- Portas permitidas: 80 e 443 (configurável).
- Normalização da URL: minúsculas no host, remoção do fragmento, IDNA.
- Rejeitar hosts que sejam IPs literais em notações alternativas (decimal, octal, hexadecimal).
- Resolver **todos** os registros A e AAAA. Rejeitar se **qualquer** IP não for global (`ipaddress.ip_address(x).is_global` falso), incluindo IPv6 mapeado para IPv4, loopback, link-local, faixas privadas, `169.254.169.254` e `100.64.0.0/10`.
- **Conectar no IP validado**, e não resolver de novo. Com httpx, faça a requisição para o IP, com header `Host` original e a extensão `sni_hostname` para HTTPS. Isso fecha a janela de DNS rebinding. Se não for viável, pare e registre a alternativa em DECISIONS.md.
- Redirecionamentos manuais, no máximo 5, com cada destino passando pelo guard de novo.
- Limites: corpo de até 15 MB (lido em streaming e abortado ao passar), timeout total de 20 s, timeout de conexão de 5 s.
- User-agent: `AgentContextBot/0.1 (+<url de contato configurável>)`.

### 3.2 robots.txt

Busca `/robots.txt` pelo mesmo guard, faz cache por domínio por 24 h e respeita `Disallow` para o user-agent próprio e para `*`. Se o robots.txt der 4xx, considere permitido. Se der 5xx ou timeout, considere bloqueado temporariamente (erro `robots_unavailable`).

### 3.3 Decisão de renderização

Parâmetro `render`: `auto` (padrão), `never` ou `always`. No modo `auto`, renderize com navegador somente se as duas condições valerem:

1. O texto principal extraído do HTML tem menos de 500 caracteres.
2. O HTML tem sinais de SPA: contêiner vazio (`<div id="root"></div>`, `<div id="app"></div>`), `__NEXT_DATA__` ou `window.__NUXT__` sem conteúdo renderizado, ou `<noscript>` pedindo JavaScript.

Os limiares ficam em `config.py`.

### 3.4 Renderização (Playwright)

- Contexto novo e efêmero por requisição, sem persistência, sem downloads e sem permissões.
- `page.route("**/*")` intercepta **toda** requisição da página e valida o destino pelo guard. Destinos bloqueados são abortados.
- Bloquear tipos `image`, `media` e `font` para economizar.
- Esperar `domcontentloaded` + `networkidle` com teto de 15 s, e então capturar `page.content()`.

## 4. Representação intermediária (IR)

Modelos Pydantic em `app/extract/ir.py`.

```python
class SourceInfo(BaseModel):
    url: str                  # URL pedida, normalizada
    final_url: str            # após redirecionamentos
    content_type: str
    fetched_at: datetime
    etag: str | None
    last_modified: str | None
    content_hash: str         # sha256 do corpo bruto
    render_mode: Literal["http", "browser", "pdf"]

class Metadata(BaseModel):
    title: str | None
    lang: str | None
    published_at: str | None
    modified_at: str | None
    jsonld: list[dict]        # blocos JSON-LD encontrados, sem alteração

BlockType = Literal["heading", "paragraph", "list", "code", "table",
                    "blockquote", "admonition", "definition_list"]

class Block(BaseModel):
    block_id: str             # estável, ver regra abaixo
    type: BlockType
    order: int                # posição no documento
    level: int | None         # só para heading (1 a 6)
    heading_path: list[str]   # títulos ancestrais, do mais alto ao mais próximo
    text: str                 # texto normalizado, sem markup
    data: dict                # específico do tipo, ver abaixo
    links: list[Link]         # {text, href absoluto}
    page: int | None          # só para PDF, começando em 1

class Warning(BaseModel):
    code: str                 # ex.: "table_unparsed", "low_text_content", "rendered_with_browser"
    message: str
    block_id: str | None

class Document(BaseModel):
    document_id: str          # "doc_" + sha1(url normalizada)[:16]
    version_id: str           # "ver_" + content_hash[:16]
    source: SourceInfo
    metadata: Metadata
    blocks: list[Block]
    warnings: list[Warning]
```

`data` por tipo:

- `table`: `{"caption": str | None, "headers": list[str], "rows": list[list[str]], "notes": list[str]}`. Células mescladas são repetidas. Tabela sem cabeçalho detectável gera headers vazios e o warning `table_no_header`.
- `code`: `{"language": str | None, "code": str}`. O código é preservado byte a byte, sem normalizar espaços.
- `list`: `{"ordered": bool, "items": list[str]}`, com aninhamento representado por indentação de dois espaços em cada item.
- `admonition`: `{"kind": "note" | "warning" | "tip" | "danger" | "info"}`. Detectar por classes comuns (`admonition`, `callout`, `alert`, `note`, `warning`) e por `role="note"`.

**Regra do `block_id`:** `"b_" + sha1(" > ".join(heading_path) + "|" + type + "|" + text_normalizado)[:12]`. Se repetir dentro do documento, acrescente `_2`, `_3` e assim por diante, em ordem. Um bloco que não mudou entre versões mantém o mesmo id.

**Extração HTML:** `readability-lxml` isola o conteúdo principal. Em seguida, um percorredor próprio sobre lxml converte o HTML principal em blocos. Se o readability descartar tabelas ou blocos de código que existiam no `<main>` ou `<article>` original, use o `<main>`/`<article>` diretamente e registre o warning `readability_fallback`. Remova `nav`, `footer`, `aside`, botões de "copiar" e âncoras de permalink (`¶`, `#`).

**Extração PDF:** pdfplumber, página a página. Títulos são inferidos por tamanho de fonte relativo (os 2 maiores tamanhos acima do corpo). Tabelas via `extract_tables`. Cabeçalhos e rodapés repetidos em mais de 50% das páginas são removidos. PDF sem texto extraível gera o erro `extraction_empty` com a observação de que OCR está fora do escopo.

**Markdown:** gerado sempre a partir da IR (`to_markdown.py`), nunca diretamente do HTML. Tabelas viram tabelas GFM, código vira bloco cercado com a linguagem.

## 5. Chunking e indexação

- Tokens contados com tiktoken `cl100k_base`. É uma aproximação: tokenizadores variam por modelo, e a resposta da API deixa isso claro em `token_usage.tokenizer`.
- Um chunk agrupa blocos consecutivos da mesma seção (mesmo `heading_path`), com alvo de 200 a 500 tokens e máximo de 800.
- Tabelas e blocos de código nunca são cortados, a menos que passem de 800 tokens. Nesse caso, tabelas são divididas por linhas, repetindo os headers em cada parte, e código é dividido por linhas em branco.
- O texto indexado do chunk recebe um prefixo com o caminho de seções: `"Autenticação > Tokens > Expiração\n\n" + conteúdo`.
- Lexical: coluna `tsvector` gerada com a configuração `simple` e índice GIN. Ranking com `ts_rank_cd`.
- Vetorial: coluna `vector(DIM)` (DIM vindo do provedor configurado) com índice HNSW de distância cosseno.
- Chunks pertencem a uma `version_id`. Uma versão nova de um documento gera chunks novos; os antigos ficam até a limpeza.

## 6. Recuperação e seleção (`assemble`)

1. Resolver as URLs (cache válido por padrão de 24 h) e garantir que as versões atuais estejam indexadas.
2. Recuperar os 50 melhores por busca lexical e os 50 melhores por busca vetorial, restritos às versões atuais das URLs pedidas.
3. Fundir com RRF: `score(d) = Σ 1 / (k + rank_j(d))`, com `k = 60`.
4. Rerank dos 30 melhores pelo provedor de reranking. Normalize os scores para [0, 1].
5. Seleção gulosa sob orçamento `B = max_tokens`:
   - valor do candidato `i`: `V_i = rerank_i` (ou o RRF normalizado se o rerank estiver desligado);
   - a cada passo, escolha o candidato que maximiza `(V_i - λ · max_sim(i, selecionados)) / sqrt(t_i)`, onde `max_sim` é o cosseno máximo entre o embedding de `i` e o dos já selecionados, e `t_i` são os tokens do chunk;
   - pule candidatos que não cabem no restante do orçamento e pare quando nenhum couber ou quando o melhor valor ajustado ficar abaixo de `min_value`;
   - parâmetros iniciais: `λ = 0.5`, `min_value = 0.05`. Ficam em `config.py` e são calibrados no M8.
6. Ordene os selecionados por documento e por `order`, para que a leitura siga o documento.
7. `coverage`: `insufficient` se o maior rerank for menor que `coverage_low` (inicial 0.2), `partial` se for menor que `coverage_high` (inicial 0.5) e `sufficient` caso contrário. É uma heurística, calibrada no M8, e precisa ser documentada assim na API.

Flags de ablação (aceitas somente quando `ENV != "production"`): `use_lexical`, `use_vector`, `use_rerank`, `structural_chunking`, `selection: "greedy" | "truncate"`.

## 7. Modelo de dados

```
sources(id pk, url_normalized unique, domain, current_version_id null, created_at)
document_versions(id pk, source_id fk, content_hash, fetched_at, etag, last_modified,
                  render_mode, raw_blob_key, ir_blob_key, status, warnings jsonb,
                  unique(source_id, content_hash))
chunks(id pk, version_id fk, block_ids text[], heading_path text[], text, md,
       tokens int, tsv tsvector generated, embedding vector(DIM), ord int)
api_keys(id pk, key_hash unique, name, created_at, revoked_at null, limits jsonb)
usage_events(id pk, api_key_id fk, endpoint, ts, status_code, latency_ms, units jsonb)
```

O HTML ou PDF bruto e a IR completa ficam no armazenamento S3 (SeaweedFS no dev local, AWS S3 em produção — ver DECISIONS.md 2026-10-11). O Postgres guarda metadados, chunks e índices. Chaves de API são armazenadas apenas como hash (sha256) e mostradas uma única vez na criação.

## 8. Contratos de API

Todas as rotas exigem `Authorization: Bearer <chave>` a partir do M9. Antes disso, a autenticação fica desligada em `ENV=dev`.

### `POST /v1/documents/resolve`

Request:
```json
{ "url": "https://docs.example.com/auth", "formats": ["markdown", "blocks"], "force_refresh": false, "render": "auto" }
```

Response 200:
```json
{
  "document_id": "doc_...", "version_id": "ver_...",
  "source": { "...": "SourceInfo" },
  "metadata": { "...": "Metadata" },
  "markdown": "# ...",
  "blocks": [ { "...": "Block" } ],
  "stats": { "blocks": 42, "tokens_markdown": 3100, "tokens_raw_html": 41200, "cache_hit": false },
  "warnings": [],
  "trust": "untrusted_source"
}
```

### `GET /v1/documents/{document_id}`

Query opcional `version_id` e `formats`. Devolve o mesmo formato do `resolve`, lido do armazenamento, sem refetch.

### `POST /v1/context/assemble`

Request:
```json
{
  "query": "Which header is required to authenticate API requests?",
  "urls": ["https://docs.example.com/auth", "https://docs.example.com/errors"],
  "max_tokens": 4000,
  "include_evidence": true
}
```
Limites: `urls` entre 1 e 20, `max_tokens` entre 256 e 32000.

Response 200:
```json
{
  "query": "...",
  "fragments": [
    {
      "chunk_id": "chk_...", "document_id": "doc_...", "version_id": "ver_...",
      "source_url": "https://docs.example.com/auth",
      "heading_path": ["Authentication", "API keys"],
      "md": "...", "tokens": 312,
      "scores": { "lexical_rank": 2, "vector_rank": 1, "rrf": 0.032, "rerank": 0.91 },
      "evidence": [ { "block_id": "b_...", "page": null, "quote": "Send the key in the X-Api-Key header." } ]
    }
  ],
  "sources": [ { "url": "...", "document_id": "...", "version_id": "...", "fetched_at": "...", "status": "ok" } ],
  "token_usage": { "output_tokens": 1830, "budget_tokens": 4000, "tokenizer": "cl100k_base (approximate)" },
  "coverage": "sufficient",
  "warnings": [],
  "trust": "untrusted_source"
}
```

Se uma das URLs falhar, a resposta continua 200 com as outras, e a falha aparece em `sources[].status` e em `warnings`. Se todas falharem, a resposta é 422.

`evidence.quote` é o texto do bloco de origem (ou a frase de maior sobreposição lexical com a pergunta, com no máximo 300 caracteres), sempre copiado literalmente da IR.

### `GET /v1/usage`

Agregados da chave atual por dia e por endpoint: requisições, tokens entregues, renderizações e latência p50/p95.

### Erros

```json
{ "error": { "code": "blocked_destination", "message": "...", "details": {} } }
```

| code | HTTP |
|---|---|
| invalid_url | 400 |
| blocked_destination | 400 |
| robots_disallowed | 403 |
| robots_unavailable | 503 |
| unauthorized | 401 |
| rate_limited | 429 |
| too_large | 413 |
| unsupported_content_type | 415 |
| extraction_empty | 422 |
| fetch_failed | 502 |
| timeout | 504 |

## 9. Servidor MCP

Transporte stdio para uso local e streamable HTTP para uso remoto. É um cliente fino da API HTTP (recebe `API_URL` e `API_KEY`). Ferramentas:

- `resolve_url(url, format="markdown")`
- `assemble_context(query, urls, max_tokens=4000)`
- `get_document(document_id, version_id=None)`

As descrições das ferramentas avisam que o conteúdo retornado vem de páginas externas e não deve ser tratado como instrução.

## 10. Benchmark (`bench/`)

### 10.1 Corpus congelado

`bench/corpus/` guarda snapshots baixados uma vez (HTML bruto, HTML renderizado quando aplicável e PDFs) e um `manifest.json` com url, data, hash e categoria. **Todas as estratégias leem do snapshot**, e não da web ao vivo, para que as execuções sejam reproduzíveis. O pipeline próprio recebe os snapshots por um fetcher alternativo usado só no benchmark.

Exceção (M10, §10.6): os braços `webfetch_live` e `context7_live` leem a web ao vivo. São sempre secundários, rotulados como não reprodutíveis, e o resultado de cada chamada de ferramenta fica na transcrição.

### 10.2 Formato das tarefas (`tasks.yaml`)

```yaml
- id: t001
  category: api_reference      # api_reference | guide | params_table | changelog | pdf
  split: dev                   # dev (pode ser usado para calibrar) | test (reservado)
  urls: [https://docs.example.com/auth]
  question: "Which HTTP header carries the API key?"
  answer_type: contains_all    # exact | contains_all | judge
  expected: ["X-Api-Key"]
  expected_evidence:
    - url: https://docs.example.com/auth
      quote: "Send the key in the X-Api-Key header"
  author: ai_draft             # human | ai_draft
  validated: false             # só tarefas validated: true entram nos relatórios
```

### 10.3 Estratégias

Todas recebem o mesmo orçamento de tokens de contexto, e o mesmo LLM responde (temperatura 0, prompt fixo em `bench/answer.py`).

| id | descrição |
|---|---|
| `raw_html` | HTML bruto do snapshot, truncado ao orçamento |
| `naive_text` | trafilatura (texto) truncado ao orçamento |
| `naive_rag` | trafilatura + chunks fixos de 400 tokens + BM25 (rank_bm25) top-k até o orçamento |
| `firecrawl` | Markdown do Firecrawl + mesmo chunking e BM25 do `naive_rag` (opcional, exige chave) |
| `ours` | `assemble` completo |
| `ours_*` | variantes de ablação (`no_vector`, `no_lexical`, `no_rerank`, `fixed_chunks`, `truncate`) |

O `naive_rag` é o baseline mais importante: representa o que um desenvolvedor competente montaria em uma tarde. Ganhar só do `raw_html` não prova nada.

### 10.4 Resposta e correção

- O respondedor deve devolver JSON `{"answer": str, "citations": [{"url": str, "quote": str}]}`. Se a evidência no contexto for insuficiente, ele deve responder `"answer": "INSUFFICIENT"`.
- `exact` e `contains_all`: comparação determinística, sem diferenciar maiúsculas e ignorando espaços extras.
- `judge`: LLM juiz com rubrica fixa, que devolve `{"correct": bool, "reason": str}`. O juiz deve ser um modelo diferente do respondedor, ou pelo menos usar outro prompt e ter o resultado auditado por amostragem.
- Precisão de citação: a citação conta como correta se a `quote` existir (correspondência aproximada, razão ≥ 0.9) no documento citado **e** sobrepuser alguma `expected_evidence`.

### 10.5 Métricas e relatório

Por estratégia e por categoria: taxa de sucesso (com IC de 95% por bootstrap sobre as tarefas), tokens médios de contexto, custo estimado por tarefa e por tarefa **concluída** (tabela de preços em `bench/prices.yaml`), latência p50/p95 do pipeline, precisão de citação e taxa de `INSUFFICIENT`.

Saída em `bench/results/<timestamp>/`: `results.csv` (uma linha por tarefa x estratégia), `report.md` e `config.json` (versões, modelos, parâmetros, commit).

CLI: `python -m bench.run --strategies naive_rag,ours --split test --budget 4000`.

### 10.6 Trilha de agente (M10)

Mede o produto como um cliente o usa: um agente (Claude Agent SDK, modelo `claude-sonnet-5-5`) responde perguntas reais de desenvolvedores chamando uma ferramenta de documentação, que muda por braço. O protocolo completo (população, braços, tamanho de amostra, hipóteses, regra de relato e de parada) está em `bench/PREREG.md`, congelado antes de qualquer execução paga. Esta seção fixa só os contratos.

Tarefas externas ficam em `bench/tasks/external.yaml` (CC BY-SA 4.0, com atribuição), separadas de `tasks.yaml`:

```yaml
- id: so-77001234              # "so-" + id da pergunta no Stack Overflow
  split: pilot                 # pilot | holdout
  site: python                 # chave de bench/sites.yaml
  task_type: version           # multi_doc | version | code | fact (regra em PREREG §2.1)
  urls: [...]                  # páginas citadas (máx. 4) + distratores do mesmo site; total 5, embaralhadas
  cited_urls: [...]            # páginas oficiais citadas na resposta aceita, após redirecionamento
  question_title: "..."
  question: "..."              # corpo da pergunta, texto puro, literal
  reference_answer: "..."      # resposta aceita, texto puro, literal: é o gabarito
  expected_evidence:           # blockquotes da resposta aceita que casam (razão >= 0.9) com o snapshot; pode ser vazio
    - url: https://docs.python.org/3/library/functions.html
      quote: "..."
  source:
    question_url: https://stackoverflow.com/q/77001234
    answer_url: https://stackoverflow.com/a/77005678
    question_author: "..."     # nome exibido + link do perfil
    answer_author: "..."
    license: CC BY-SA 4.0
    created: 2024-03-02
  author: external
  audit: null                  # null (fora da amostra) | valid | invalid
```

Para tarefas externas, a regra "só `validated: true` entra nos relatórios" (§10.2) é substituída pela auditoria amostral de PREREG §2.1: entram todas, exceto as marcadas `invalid`.

- **Braços:** `no_docs`, `full_page`, `strong_rag`, `nina`, `webfetch_live` e `context7_live` (definidos em PREREG §3). Não há orçamento igualado entre braços: cada ferramenta usa o próprio padrão, e o que se mede é sucesso e custo faturado.
- **Juízes:** o principal é de um provedor diferente do agente (`gpt-6.1-sol`). O segundo juiz (`claude-opus-5-5`) e os rótulos humanos (κ de Cohen) auditam o principal. A referência do juiz é a resposta aceita, literal.
- **Métricas:** além das de §10.5, tokens de cache (criação e leitura), turnos, chamadas de ferramenta, tokens devolvidos pelas ferramentas e custo por tarefa correta, conciliado com o export de custo do provedor (PREREG §6).
- **Saída** em `bench/results/m10/`: `results.csv` (uma linha por tarefa x braço, com o id do braço na coluna `strategy`), `report.md`, `decision.md`, `config.json` e as transcrições completas (gitignored).
