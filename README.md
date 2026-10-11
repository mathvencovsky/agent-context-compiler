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
- Próximo: **M2 — Corpus congelado, harness e baselines** (requer lista humana
  de sites e tarefas validadas).

Acompanhe `PROGRESS.md` para o detalhe de cada marco.

## Rodando localmente

Pré-requisitos: Docker, [uv](https://docs.astral.sh/uv/) e Python 3.12
(o uv instala/fixa a versão por projeto).

```bash
cp .env.example .env        # ajuste se necessário; .env fica fora do git
make up                     # Postgres + S3 local (SeaweedFS) + criação do bucket
make migrate                # habilita a extensão pgvector
make check                  # ruff + mypy (strict em app/) + pytest
make dev                    # API local com reload (http://localhost:8000)
make down                   # derruba a infra
```

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
