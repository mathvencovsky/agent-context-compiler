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
| **M2** | Corpus congelado, harness e baselines | ⏳ Bloqueado (insumo humano) |
| **M3** | IR e extração de HTML | ✅ Concluído |
| **M4** | Renderização por navegador e extração de PDF | ✅ Concluído |
| **M5** | Persistência e endpoint `resolve` | ◻️ Próximo |
| **M6** | Chunking e indexação | ◻️ Pendente (chave de embeddings) |
| **M7** | Busca, seleção e endpoint `assemble` | ◻️ Pendente (chave de reranker) |
| **M8** | Benchmark completo e calibração | ◻️ Ponto de decisão |
| **M9** | Chaves de API, limites, MCP e SDK | ◻️ Pendente |
| **M10** | Deploy e observabilidade | ◻️ Pendente (hospedagem) |

**Pipeline de aquisição e extração (M1, M3, M4) está completo e verificado:**
HTTP seguro → HTML/PDF → IR → Markdown/JSON. Os marcos de segurança (M1, M4)
passaram por revisão adversarial independente, sem findings HIGH/MEDIUM em
aberto. O detalhe por marco está em [`PROGRESS.md`](PROGRESS.md); as decisões
técnicas não óbvias, em [`DECISIONS.md`](DECISIONS.md).

> **M2 depende de você.** O benchmark precisa de insumos humanos (lista de sites,
> chaves de LLM, 100 tarefas validadas). Veja [o que fazer](#benchmark-o-que-depende-de-você).

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

## Benchmark: o que depende de você

O harness de avaliação (M2) é o que prova a tese do produto. O código é
construído automaticamente, mas estes insumos exigem julgamento humano, contas
pagas e decisões de produto:

1. **Sites (`bench/sites.yaml`)** — 10 a 15 sites de documentação pública com
   estruturas variadas (referência de API com tabelas, guias longos, changelogs,
   ≥2 SPAs, ≥3 PDFs), 5 a 15 URLs por site, com `robots.txt` e termos de uso
   conferidos.
2. **LLMs do benchmark** — um modelo respondedor e um modelo juiz (de
   preferência diferentes), com as chaves no `.env`.
3. **Preços (`bench/prices.yaml`)** — preço por milhão de tokens de cada modelo,
   para o relatório calcular custo por tarefa concluída.
4. **100 tarefas validadas** — rascunhadas pela IA e **revisadas por você** (70
   `dev` para calibração, 30 `test` reservadas). É o passo mais importante do
   projeto: tarefas erradas tornam o benchmark sem sentido.
5. **(Opcional) chave do Firecrawl** — para o baseline comercial; sem ela, esse
   runner é pulado.

Detalhes e checklist completo em [`docs/HUMANO.md`](docs/HUMANO.md).

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
