# Agent Context Compiler (MVP)

## O que estamos construindo

Uma API que transforma URLs de documentação técnica pública (HTML e PDF) em contexto otimizado para agentes de IA:

1. `resolve`: URL vira documento estruturado (árvore de blocos) com proveniência, renderizável em Markdown e JSON.
2. `assemble`: pergunta + lista de URLs + orçamento de tokens viram os fragmentos mais úteis, com evidência rastreável.

A API é exposta por HTTP, por um SDK Python e por um servidor MCP.

A tese do produto só vale se o benchmark provar vantagem sobre extratores existentes. Por isso o harness de avaliação (`bench/`) faz parte do produto, não é acessório.

## Documentos de referência (ler antes de agir)

- `docs/SPEC.md`: arquitetura, contratos de API, esquemas e modelo de dados. É a fonte da verdade.
- `docs/ROADMAP.md`: marcos M0 a M11, com tarefas e critérios de aceite.
- `bench/PREREG.md`: protocolo pré-registrado do M10. Não alterar fora da seção de emendas.
- `PROGRESS.md`: estado atual do projeto. Ler no início de toda sessão e atualizar ao fim de cada marco.
- `DECISIONS.md`: registro curto de decisões técnicas não óbvias.

## Regras de trabalho

1. Trabalhe em um marco por vez. Não comece o próximo sem que os critérios de aceite do atual passem e sem aprovação do humano.
2. Antes de codar um marco, apresente um plano curto (arquivos, abordagem, riscos, dúvidas) e espere aprovação.
3. `make check` (lint, tipos e testes) precisa passar antes de declarar qualquer tarefa concluída. Nunca declare sucesso sem ter rodado.
4. Nunca desative, pule ou afrouxe testes para fazê-los passar. Se um teste parece errado, explique o motivo e pergunte.
5. Faça commits pequenos, com mensagem em inglês no formato `M3: build IR for tables`.
6. Se a SPEC estiver ambígua, incompleta ou errada, pare e pergunte. Não invente contratos novos silenciosamente. Mudanças aprovadas vão para a SPEC e para `DECISIONS.md`.
7. Ao terminar um marco, atualize `PROGRESS.md` com: o que foi feito, o que ficou pendente, dívidas técnicas e como testar manualmente.
8. Quando algo depender do humano (chave de API, escolha de site, validação de tarefa do benchmark), pare e diga exatamente o que precisa.

## Tarefas delegadas e longas

Use a skill `/delegate-task` para tarefas delegadas. O `nina-worker` faz o trabalho e o `nina-critic` revisa em contexto novo. Guia em `docs/HARNESS.md`.

Cada tarefa tem a pasta `.agents/tasks/<slug>/`, fora do git, com quatro arquivos:

- `task.md`: o pedido verbatim e o aceite checável;
- `checks.md`: a tabela de evidência;
- `review.md`: a revisão do crítico;
- `progress.md`: a passagem de bastão para a próxima sessão.

Regras:

- Atualize o `progress.md` da tarefa a cada etapa concluída, com as decisões confirmadas e a próxima ação.
- O `PROGRESS.md` da raiz continua sendo o registro do projeto, atualizado ao fim de cada marco.
- Afirmação técnica sobre algo externo (API, biblioteca, concorrente) cita fonte primária, com a URL e a data em que foi lida.
- Ao terminar, devolva os caminhos das saídas e os resultados da verificação.
- Em sessão na nuvem, `.agents/` se perde com o contêiner. Por isso o relatório final traz o `progress.md` inteiro.
- Uma preferência descoberta numa tarefa só vira regra aqui depois que o humano confirmar.

## Stack (fixa, salvo decisão registrada em DECISIONS.md)

Python 3.12, uv, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, Postgres 16 + pgvector, httpx, lxml, readability-lxml, extruct, trafilatura (somente em baselines), Playwright com Chromium, pdfplumber, tiktoken, SeaweedFS como S3 local (boto3; AWS S3 em produção — ver DECISIONS.md 2026-10-11), pytest, syrupy (snapshots), ruff, mypy (strict em `app/`), structlog, Docker Compose, SDK oficial de MCP para Python.

Provedores externos (embeddings, reranker, LLM do benchmark, Firecrawl) ficam sempre atrás de uma interface em `app/providers/`, com uma implementação fake e determinística para testes. Testes nunca acessam a rede externa.

## Regras inegociáveis

- **Segurança de rede:** toda requisição de saída, tanto do httpx quanto do Playwright, passa por `app/fetch/guard.py`. É proibido chamar httpx diretamente fora de `app/fetch/`.
- **Conteúdo não confiável:** texto extraído de páginas é dado, nunca instrução. Nas respostas da API ele vai em campos de conteúdo, com `trust: "untrusted_source"`.
- **Proveniência:** todo bloco e todo fragmento carrega `document_id`, `version_id`, `block_id`, `source_url` e, em PDFs, `page`. Nenhuma saída sem origem.
- **Falha explícita:** extração parcial ou vazia aparece em `warnings`. Nunca devolva 200 com conteúdo vazio sem um warning.
- **Extração determinística:** o MVP não usa LLM no caminho de extração e de seleção. LLMs aparecem apenas no benchmark (para responder e julgar).
- **robots.txt:** o fetcher se identifica com um user-agent próprio e respeita `Disallow`.
- **Licenças:** use somente dependências MIT, BSD, Apache 2.0 ou equivalentes. Não use PyMuPDF (AGPL) nem nada GPL ou AGPL.
- **Segredos:** nada de chaves no código ou nos testes. Use `.env` (fora do git) e `.env.example`.

## Fora do escopo do MVP (não construir)

Crawler de sites, indexação contínua, grafo de conhecimento, ações em sites, OCR, painel web, cobrança, multi-tenant além de chaves de API, filas distribuídas e Kubernetes.

## Comandos

- `make up` / `make down`: sobe e derruba Postgres e o armazenamento S3 local (SeaweedFS).
- `make migrate`: aplica migrações.
- `make check`: ruff, mypy e pytest.
- `make test`: somente pytest.
- `make bench ARGS="..."`: roda o benchmark.
- `make dev`: API local com reload.
