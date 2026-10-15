# ROADMAP do MVP

Doze marcos. Cada marco termina com `make check` passando, os critérios de aceite atendidos, `PROGRESS.md` atualizado e aprovação do humano.

Visão geral:

| Marco | Entrega | Bloqueado por ação humana? |
|---|---|---|
| M0 | Repositório, ferramentas, CI | Não |
| M1 | Fetcher seguro | Não |
| M2 | Corpus congelado, harness e baselines | **Sim:** lista de sites e tarefas validadas |
| M3 | IR e extração de HTML | Não |
| M4 | Renderização por navegador e PDF | Não |
| M5 | Persistência e `resolve` | Não |
| M6 | Chunking e indexação | **Sim:** chave do provedor de embeddings |
| M7 | Busca, seleção e `assemble` | **Sim:** chave do reranker |
| M8 | Benchmark completo e calibração | **Sim:** decisão de seguir, ajustar ou parar |
| M9 | Chaves de API, limites, MCP e SDK | Não |
| M10 | Avaliação ponta a ponta com agente (pré-registrada) | **Sim:** chaves, teto de gasto, auditoria de tarefas e avaliadores humanos |
| M11 | Deploy e observabilidade | **Sim:** escolha de hospedagem e contas |

---

## M0: Fundação do repositório

**Objetivo:** projeto que roda, testa e faz lint desde o primeiro commit.

Tarefas:
- `pyproject.toml` com uv, grupos de dependências `dev` e `bench`.
- Estrutura de pastas da SPEC §2, com `__init__.py` e módulos vazios onde fizer sentido.
- `docker-compose.yml` com Postgres 16 + pgvector e SeaweedFS como S3 local (bucket criado no startup; ver DECISIONS.md 2026-10-11).
- `app/config.py` com pydantic-settings; `.env.example` completo.
- `app/main.py` com `GET /health` checando Postgres e o S3 local (SeaweedFS).
- Alembic configurado, com a primeira migração habilitando a extensão `vector`.
- structlog com logs JSON e `request_id` por requisição.
- `Makefile` com os comandos do CLAUDE.md.
- GitHub Actions rodando `make check` com serviço Postgres.
- `PROGRESS.md` e `DECISIONS.md` inicializados.

Aceite:
- `make up && make migrate && make check` passa em máquina limpa.
- `GET /health` responde `{"status": "ok", "db": "ok", "blobs": "ok"}`.
- CI verde.

Modelo sugerido: Opus.

---

## M1: Fetcher seguro

**Objetivo:** buscar URLs públicas com proteção contra SSRF, limites e respeito a robots.txt (SPEC §3.1 e §3.2).

Tarefas:
- `guard.py`, `client.py`, `robots.py` e `detect.py` (só content-type e charset neste marco).
- Resultado tipado: `FetchResult(final_url, status, headers, body, content_type, redirects, elapsed_ms)`.
- Erros tipados mapeados para os códigos da SPEC §8.

Aceite (todos com teste automatizado):
- Bloqueia `http://127.0.0.1`, `http://localhost`, `http://[::1]`, `http://169.254.169.254/latest/meta-data`, `http://10.0.0.1`, `http://2130706433`, `http://0x7f000001`, `http://[::ffff:127.0.0.1]`, `file:///etc/passwd`, `gopher://...` e porta 8080.
- Bloqueia o redirecionamento de uma URL pública para um IP privado (teste com servidor local + resolvedor de DNS mockado).
- Simulação de DNS rebinding: o resolvedor devolve um IP público na validação e um privado depois. A conexão precisa ir para o IP validado.
- Aborta corpo acima de 15 MB sem carregá-lo inteiro na memória.
- Respeita `Disallow` do robots.txt e faz cache dele.
- Nenhum teste acessa a internet.

Modelo sugerido: Opus. Peça uma revisão de segurança independente ao fim (ver PROMPTS.md).

---

## M2: Corpus congelado, harness e baselines

**Objetivo:** ter a régua de medição antes do produto. Ao fim deste marco, já é possível medir os baselines.

Pré-requisito humano: `bench/sites.yaml` com 10 a 15 sites de documentação pública e as URLs de cada um (ver HUMANO.md).

Tarefas:
- `bench/snapshot.py`: baixa as URLs de `sites.yaml` usando o fetcher do M1, salva em `bench/corpus/` e gera o `manifest.json`. Para páginas SPA, salva também a versão renderizada (pode usar Playwright diretamente aqui, com a mesma interceptação do guard).
- Esquema e validador de `tasks.yaml` (SPEC §10.2). Linhas inválidas falham com mensagem clara.
- `bench/draft_tasks.py`: usa o LLM para **rascunhar** tarefas a partir dos snapshots, sempre com `author: ai_draft` e `validated: false`, e com a `quote` copiada literalmente do documento. É um rascunho para o humano revisar, não verdade de referência.
- `bench/review_tasks.py`: CLI simples que mostra cada tarefa rascunhada com o trecho de origem e pergunta aceitar, editar ou descartar, gravando `validated: true` quando aceita.
- Runners `raw_html`, `naive_text`, `naive_rag` e `firecrawl` (este último pulado se não houver chave).
- `answer.py`, `grade.py` e `report.py` conforme SPEC §10.4 e §10.5. Provedor LLM atrás de interface, com fake para testes.
- `bench/prices.yaml` com preços por milhão de tokens (preenchido pelo humano).

Aceite:
- `python -m bench.run --strategies raw_html,naive_text,naive_rag --split dev` gera `results.csv`, `report.md` e `config.json`.
- Duas execuções seguidas com o provedor fake produzem resultados idênticos.
- Testes do grader cobrem `exact`, `contains_all`, citação válida, citação inventada e resposta `INSUFFICIENT`.
- Relatório ignora tarefas `validated: false` e informa quantas foram ignoradas.

Modelo sugerido: Opus.

---

## M3: IR e extração de HTML

**Objetivo:** HTML vira uma IR fiel ao conteúdo, com tabelas, código e hierarquia preservados (SPEC §4).

Tarefas:
- Modelos da IR em `ir.py`.
- `html.py`: readability + percorredor lxml, com o fallback para `<main>`/`<article>` descrito na SPEC.
- `jsonld.py`: título, idioma, datas e JSON-LD.
- `to_markdown.py`.
- Fixtures: pelo menos 15 páginas reais do corpus em `tests/fixtures/html/`, cobrindo referência de API com tabela de parâmetros, guia longo, página com abas de código em várias linguagens, changelog e página com admonitions.
- Testes de snapshot (syrupy) do Markdown e da IR de cada fixture.

Aceite:
- Em todas as fixtures, 100% das tabelas e dos blocos de código presentes no conteúdo principal aparecem na IR. Escreva um teste que conte `<table>` e `<pre>` no conteúdo principal e compare com a IR.
- `block_id` é estável: extrair a mesma página duas vezes gera os mesmos ids, e alterar um parágrafo muda só o id daquele bloco.
- Nenhum bloco de navegação, rodapé ou "copiar código" aparece nas fixtures (verificado por lista de strings proibidas por fixture).
- `heading_path` correto em todos os blocos (verificado por amostragem manual e registrado em PROGRESS.md).

Modelo sugerido: este é um dos marcos mais trabalhosos, porque HTML real é bagunçado. Se o Opus travar em casos específicos, vale usar o Fable aqui.

---

## M4: Renderização por navegador e PDF

**Objetivo:** cobrir páginas dependentes de JavaScript e PDFs digitais (SPEC §3.3, §3.4 e §4).

Tarefas:
- `detect.py`: decisão de renderização.
- `render.py`: Playwright com interceptação pelo guard.
- `pdf.py`: pdfplumber para IR, com `page` em cada bloco.
- Warnings `rendered_with_browser`, `table_no_header`, `low_text_content`.

Aceite:
- Página SPA de teste (servida localmente) que só mostra conteúdo após o JS: `auto` renderiza e extrai; `never` devolve o warning `low_text_content`.
- Uma página renderizada que tenta carregar `http://169.254.169.254` tem essa requisição bloqueada (teste automatizado).
- Pelo menos 3 PDFs técnicos de fixture: títulos detectados, tabelas extraídas e cabeçalhos/rodapés repetidos removidos.
- PDF sem texto devolve `extraction_empty`.

Modelo sugerido: Opus.

---

## M5: Persistência e endpoint `resolve`

**Objetivo:** primeiro endpoint público funcionando com cache (SPEC §7 e §8).

Tarefas:
- Modelos SQLAlchemy e migração para `sources` e `document_versions`.
- `blobs.py` gravando bruto e IR no S3 local (SeaweedFS).
- Cache: se a última versão tem menos de 24 h e `force_refresh` é falso, devolve do armazenamento. Caso contrário, faz requisição condicional (`If-None-Match` / `If-Modified-Since`); em 304, só atualiza `fetched_at`.
- Mesmo `content_hash` não cria versão nova.
- `POST /v1/documents/resolve` e `GET /v1/documents/{id}`.
- Handler global de erros no formato da SPEC.

Aceite:
- Testes de integração (Postgres real via compose, HTTP mockado) cobrindo: primeira busca, cache hit, 304, conteúdo alterado gerando versão nova e todos os códigos de erro.
- `stats.tokens_raw_html` e `stats.tokens_markdown` preenchidos.
- OpenAPI gerado em `/docs` com exemplos.

Modelo sugerido: Opus.

---

## M6: Chunking e indexação

**Objetivo:** IR vira chunks indexados para busca lexical e vetorial (SPEC §5).

Pré-requisito humano: chave do provedor de embeddings em `.env`.

Tarefas:
- `tokens.py`, `chunker.py` e `indexer.py`.
- Tabela `chunks` com `tsvector` gerado, índice GIN e índice HNSW.
- Provedor de embeddings real + fake (vetores determinísticos derivados do hash do texto).
- Indexação em lote, com retry e backoff no provedor.

Aceite:
- Nenhum chunk passa de 800 tokens; tabelas e códigos abaixo de 800 tokens nunca são divididos (teste de propriedade com hypothesis sobre IRs geradas).
- Tabela dividida repete os headers em cada parte.
- Todo chunk tem `block_ids` não vazio e `heading_path` coerente com seus blocos.
- Reindexar a mesma versão é idempotente.

Modelo sugerido: Opus.

---

## M7: Busca, seleção e endpoint `assemble`

**Objetivo:** o diferencial do produto (SPEC §6).

Pré-requisito humano: chave do reranker em `.env`.

Tarefas:
- `lexical.py`, `vector.py`, `rrf.py`, `rerank.py`, `select.py` e `assemble.py`.
- `POST /v1/context/assemble`, com as flags de ablação somente fora de produção.
- Runner `ours` e as variantes `ours_*` no benchmark.

Aceite:
- Testes unitários do RRF (exemplo calculado à mão) e da seleção: respeita o orçamento, nunca seleciona dois chunks quase idênticos quando há alternativa, e com orçamento grande seleciona tudo que tem valor acima de `min_value`.
- `output_tokens ≤ budget_tokens` em 100% dos casos (teste de propriedade).
- Todo `evidence.quote` existe literalmente na IR do bloco referido.
- `python -m bench.run --strategies naive_rag,ours --split dev` roda de ponta a ponta.

Modelo sugerido: a seleção e a calibração são as partes matematicamente mais delicadas. Bom candidato para o Fable se os resultados no `dev` ficarem estranhos.

---

## M8: Benchmark completo, ablação e calibração

**Objetivo:** responder com dados se o produto tem vantagem. **Este é o ponto de decisão do projeto.**

Tarefas:
- Calibrar `λ`, `min_value`, `coverage_low` e `coverage_high` **somente no split `dev`** (busca em grade simples). Registrar os valores escolhidos em DECISIONS.md.
- Rodar no split `test`: todas as estratégias e todas as ablações, com orçamentos de 2000, 4000 e 8000 tokens.
- Relatório `bench/results/<ts>/report.md` com: tabela por estratégia e categoria, ICs, custo por tarefa concluída, contribuição de cada componente (ablação) e uma lista de 10 falhas do `ours` com diagnóstico.
- Um resumo de uma página em português, escrito para o humano, com a pergunta: "em que categoria, se em alguma, o `ours` vence o `naive_rag` com IC que não se sobrepõe?".

Aceite:
- Nenhum parâmetro foi ajustado olhando o split `test` (o agente deve declarar isso explicitamente e o histórico de commits deve mostrar).
- Relatório reproduzível a partir do `config.json`.

Decisão humana (o agente não decide):
- **Seguir:** vantagem clara sobre `naive_rag` em pelo menos uma categoria relevante.
- **Ajustar:** vantagem só em um nicho. Redefinir o escopo antes do M9.
- **Parar ou pivotar:** sem vantagem sobre `naive_rag`.

Modelo sugerido: Opus para executar; o Fable pode ajudar na análise das falhas.

---

## M9: Pronto para pilotos

**Objetivo:** terceiros conseguem usar com segurança mínima.

Tarefas:
- Chaves de API (CLI `python -m app.admin create-key --name piloto-x` que mostra a chave uma vez), autenticação Bearer em todas as rotas `/v1`.
- Rate limit por chave (token bucket no Postgres ou em memória por processo, documentando a limitação) e cota diária de renderizações.
- `usage_events` gravado em toda requisição; `GET /v1/usage`.
- Servidor MCP (SPEC §9), testado com o MCP Inspector.
- SDK Python em `sdk/python` com `resolve()`, `assemble()` e `get_document()`, tipado, com retries.
- `docs/QUICKSTART.md` com 3 exemplos copiáveis: curl, SDK e configuração do MCP no Claude Desktop/Claude Code.

Aceite:
- Requisição sem chave, com chave inválida ou revogada recebe 401; acima do limite recebe 429 com `Retry-After`.
- Teste de ponta a ponta: SDK chamando a API local e recebendo fragmentos.
- Servidor MCP lista as 3 ferramentas e executa `assemble_context` contra a API local.

Modelo sugerido: Opus.

---

## M10: Avaliação ponta a ponta com agente (pré-registrada)

**Objetivo:** provar ou refutar, com evidência que não foi escrita por nós, que um agente usando o NINA conclui o mesmo trabalho dependente de documentação por cerca de metade dos tokens faturados. Medição primeiro: nenhuma feature de produto neste marco, salvo se uma falha medida exigir e o humano aprovar.

Pré-requisito humano: chaves (Anthropic num workspace dedicado, OpenAI), teto de gasto do piloto e da rodada principal, auditoria de tarefas e dois avaliadores humanos (ver HUMANO.md).

Tarefas (uma fase por vez, cada uma com plano aprovado):
- Fase 0: `bench/PREREG.md` (hipóteses, métricas, margem, tamanho de amostra, análise, regra de parada) e `bench/power.py`, commitados antes de qualquer execução paga.
- Fase 1: tarefas externas do Stack Overflow (SPEC §10.6), snapshot das páginas citadas, distratores e auditoria; as 137 tarefas `ai_draft` viram uma fatia sintética separada.
- Fase 2: rotulagem humana cega com κ de Cohen, segundo juiz e autoconsistência do juiz.
- Fase 3: harness de agente (Claude Agent SDK) com os braços da SPEC §10.6; piloto, depois rodada principal com teto; conciliação com o export de custo do provedor.
- Fase 4: análise pareada (McNemar + bootstrap), curva qualidade × custo, resultados por tipo de tarefa e causas de falha do NINA.
- Fase 5: tempo do `git clone` ao primeiro `assemble` no Claude Code numa máquina limpa; calculadora de ROI só com números medidos.

Aceite:
- `bench/PREREG.md` commitado antes da primeira execução paga, sem mudanças fora da seção de emendas.
- `bench/results/m10/{results.csv, report.md, decision.md}` gerados, com o custo total conciliado com o export do provedor em ±3%.
- κ entre o juiz principal e os rótulos humanos relatado; manchete conforme a regra de relato de PREREG §7, qualquer que seja o resultado.

Modelo sugerido: Opus.

---

## M11: Deploy e observabilidade

**Objetivo:** API acessível pela internet, com visibilidade de erro, custo e latência.

Pré-requisito humano: escolha da hospedagem e criação das contas (ver HUMANO.md).

Tarefas:
- Dockerfile de produção (multi-stage, usuário não root, Chromium do Playwright incluído).
- Configuração para a hospedagem escolhida, com Postgres gerenciado com pgvector e bucket S3.
- O processo que roda o navegador deve ter egress apenas para a internet pública e nenhum acesso a credenciais da nuvem.
- OpenTelemetry ou métricas Prometheus: latência por endpoint, taxa de renderização, erros por código, custo estimado de provedores por dia.
- Sentry (ou equivalente) para exceções.
- `docs/RUNBOOK.md`: deploy, rollback, rotação de chaves, como revogar uma chave de piloto.

Aceite:
- Deploy reproduzível com um comando documentado.
- Smoke test pós-deploy: `/health`, `resolve` e `assemble` contra uma URL pública conhecida.
- Teste de SSRF repetido contra o ambiente implantado (incluindo o endereço de metadados da nuvem em uso).

Modelo sugerido: Opus.
