# NINA — Agent Context Compiler (MVP)

NINA é uma API que transforma URLs de documentação técnica pública (HTML e PDF)
em contexto otimizado para agentes de IA, com proveniência e evidência rastreável:

- **`resolve`**: uma URL vira um documento estruturado (árvore de blocos) com
  proveniência, renderizável em Markdown e JSON.
- **`assemble`**: pergunta + lista de URLs + orçamento de tokens viram os
  fragmentos mais úteis, com evidência citável.

A API é exposta por HTTP, por um SDK Python e por um servidor MCP. A tese do
produto só se sustenta se o benchmark (`bench/`) provar vantagem sobre
extratores existentes — por isso o harness de avaliação faz parte do produto.

> "NINA" é o codinome do projeto. O pacote de distribuição é `nina`; o código da
> aplicação vive em `app/` conforme `docs/SPEC.md` §2.

## Estado atual

- **M0 — Fundação do repositório: concluído.** Projeto roda, testa e faz lint
  desde o primeiro commit (uv, FastAPI, Postgres 16 + pgvector, S3 local via
  SeaweedFS, Alembic, structlog, CI).
- **M1 — Fetcher seguro: concluído.** Aquisição HTTP com proteção contra SSRF
  (guard que resolve e valida todos os IPs, falha fechada, conexão no IP
  validado para fechar DNS rebinding), limites de corpo/timeout, redirecionamentos
  re-validados e respeito a robots.txt (SPEC §3.1, §3.2, §8). Aprovado em revisão
  de segurança independente (veredito APPROVED, sem findings HIGH/MEDIUM; o único
  LOW corrigido).
- **M3 — IR e extração de HTML: concluído.** Modelos Pydantic v2 da IR (SPEC §4),
  ids estáveis e normalização de texto em `app/extract/ir.py`; `extract_html`
  (seleção robusta de conteúdo principal, isolamento por readability com fallback
  obrigatório, limpeza de chrome e walker dos oito tipos de bloco), metadados +
  JSON-LD (`jsonld.py`) e `to_markdown` sempre a partir da IR. Coberto por testes
  unitários e por uma suíte de aceite sobre 18 fixtures HTML reais: snapshots
  syrupy de Markdown e da IR, cobertura 100% de `<table>`/`<pre>` do conteúdo
  principal, limpeza de chrome, estabilidade de `block_id` e `heading_path`.
  Dependências de runtime: lxml (BSD), readability-lxml (Apache-2.0), extruct (BSD).
- **M4 — Renderização por navegador e extração de PDF: concluído.** Fundação
  (FEAT-001): dependências Playwright (Apache-2.0) e pdfplumber (MIT, com
  pdfminer.six MIT) de runtime e reportlab (BSD) de dev, marcador de teste
  `browser`, campo de config `render_networkidle_timeout_ms` (SPEC §3.4, 15 s) e a
  decisão de renderização PURA `should_render` em `app/fetch/detect.py` (SPEC §3.3:
  `auto`/`never`/`always` + sinais de SPA), coberta por testes sem rede nem
  navegador. Renderização por navegador (FEAT-002): `app/fetch/render.py` usa
  Playwright com `page.route('**/*')` (e `page.route_web_socket('**/*')`)
  roteando TODA requisição pelo guard (abortando destinos bloqueados, falha
  fechada), contexto efêmero por chamada e o teste CENTRAL de SSRF que prova zero
  conexões a `169.254.169.254`/`10.0.0.1`. Extração de PDF (FEAT-003):
  `app/extract/pdf.py` lê o PDF página a página com pdfplumber (determinístico,
  sem OCR), infere títulos por tamanho de fonte relativo, extrai tabelas via
  `extract_tables` (com `table_no_header`), remove cabeçalhos/rodapés repetidos em
  >50% das páginas e levanta `extraction_empty` (HTTP 422) para PDF sem texto;
  fixtures geradas por reportlab em `tests/fixtures/pdf/`. O CI mantém o gate
  principal rápido (`-m "not browser"`) e roda os testes de navegador num passo
  dedicado que instala o Chromium. Aprovado nas duas revisões independentes
  (técnica e red-team de segurança SSRF: ambos APPROVED, sem findings
  HIGH/MEDIUM). **Setup:** rode `uv run playwright install chromium` uma vez antes
  de executar os testes marcados `browser` (`uv run playwright install --with-deps
  chromium` no Linux/CI). O **M2 — Corpus congelado, harness e baselines** depende
  de insumo humano (lista de sites e 100 tarefas validadas) e corre em paralelo.

Acompanhe `PROGRESS.md` para o detalhe de cada marco.

## Rodando localmente

Pré-requisitos: Docker, [uv](https://docs.astral.sh/uv/) e Python 3.12
(o uv instala/fixa a versão por projeto).

```bash
cp .env.example .env        # ajuste se necessário; .env fica fora do git
make up                     # Postgres + S3 local (SeaweedFS) + criação do bucket
make migrate                # habilita a extensão pgvector
uv run playwright install chromium  # navegador p/ renderização (testes 'browser')
make check                  # ruff + mypy (strict em app/) + pytest
make dev                    # API local com reload (http://localhost:8000)
make down                   # derruba a infra
```

> A renderização por navegador (M4) e os testes marcados `browser` precisam de
> um Chromium real. Instale-o uma vez com `uv run playwright install chromium`
> (no Linux/CI, `uv run playwright install --with-deps chromium`). O gate rápido
> (`-m "not browser"`) não exige o navegador.

`GET /health` responde `{"status":"ok","db":"ok","blobs":"ok"}` quando Postgres
e o armazenamento S3 estão de pé, e `503` com o campo quebrado em `"error"`
quando algo falha (falha explícita, nunca 200 com dependência quebrada).

> No Windows/PowerShell, `make` pode não estar disponível; rode os comandos que
> cada alvo encapsula (ex.: `docker compose up -d`, `uv run alembic upgrade head`,
> `uv run ruff check . ; uv run mypy app ; uv run pytest`).

## Documentos do projeto

| Arquivo | Para quem | Função |
|---|---|---|
| `CLAUDE.md` | Agente | Regras permanentes, lidas em toda sessão |
| `docs/SPEC.md` | Agente | Arquitetura, contratos, esquemas e algoritmos (fonte da verdade) |
| `docs/ROADMAP.md` | Agente e você | 11 marcos (M0–M10) com tarefas e critérios de aceite |
| `docs/PROMPTS.md` | Você | O que colar no agente de código em cada etapa |
| `docs/HUMANO.md` | Você | O que só você pode fazer e quando (bloqueios de marco) |
| `PROGRESS.md` | Agente | Estado atual, atualizado a cada marco |
| `DECISIONS.md` | Agente e você | Decisões técnicas registradas |

## Processo de construção

1. Um marco por vez; cada um fecha com `make check` verde, critérios de aceite
   atendidos, `PROGRESS.md` atualizado e aprovação humana.
2. Antes de codar um marco: plano curto (arquivos, abordagem, riscos, dúvidas) e
   aprovação. Durante: o contexto vive nos arquivos, não na conversa.
3. Nos marcos M1, M4, M7 e M9, rode também a revisão independente (ver
   `docs/PROMPTS.md`, prompt 5).
4. Não pule o M8: é o ponto de decisão (seguir, ajustar o nicho ou parar).

## O que esperar

- O código anda rápido; o gargalo provável é humano: validar 100 tarefas do
  benchmark, revisar cada marco e conseguir pilotos.
- Os marcos mais difíceis para a IA são o M3 (HTML real é bagunçado) e o M7
  (seleção e calibração).
- Revise de verdade os testes do M1. Segurança de rede é onde um erro silencioso
  custa mais caro.
