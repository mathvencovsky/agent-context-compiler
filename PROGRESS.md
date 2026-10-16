# PROGRESS

Marco atual: **M10a (aprofundar o NINA antes do piloto) — Fase 3 em andamento**
(item 1 da trilha 1 aceito: `selection_lambda` 0,3 → 0,0). Próximo passo: item 2
da trilha 1 (seção fora dos candidatos). Da Fase 3 do M10 continuam prontos o
custo por sessão, o tamanho de página e os arquivos de saída; o harness do
agente e a ligação do braço `nina` seguem pausados. Nenhuma sessão de agente
foi feita no M10.

### M10a Fase 3 — item 2b: limpar a pergunta antes de buscar (2026-10-16, rejeitado na metade B)

`uv run python -m bench.m10a_query --half A --data-root <checkout com os dados>`
(só ranking): tirar do corpo as linhas de código e as frases de praxe do Stack
Overflow leva o pedaço da resposta aos 30 candidatos em 89 de 139 tarefas,
contra 87 hoje; só o título e as 60 primeiras palavras limpas, 84. Ganho de 2
tarefas, do tamanho do ruído das variantes do item 2. A pedido do humano, o
pipeline completo na metade A (`--full`, 137 chamadas pagas de rerank): acerto
em 8k de 0,719 para 0,748 (+2,9 pts, IC 95% [−0,7; +7,2], p = 0,29), 2k +2,9,
4k −0,7, 1k igual, mesmos tokens. Na metade B (175 tarefas, também a pedido
do humano, cerca de 175 chamadas pagas de rerank): **rejeitado**, pior em todos
os orçamentos (8k de 0,691 para 0,669, −2,3 pts, IC [−5,7; +0,6]). O ganho da
metade A não se repetiu. Nada mudou em `app/`. Detalhes em
`bench/results/m10a/changes.md`.

### M10a — busca léxica do produto alinhada ao benchmark (2026-10-16)

O produto usava `websearch_to_tsquery`, que exige todas as palavras: uma
pergunta de 41 palavras voltava vazia. Agora `lexical_search` e o runner do
benchmark usam a mesma função, `app/retrieve/lexical.overlap_ranks` (contagem de
palavras da pergunta no pedaço), e os números medidos continuam valendo. Teste:
`uv run pytest tests/integration/test_retrieve.py` (com `make up`). Dívida: a
coluna `tsv` e o índice GIN ficaram sem uso; a busca pontua em Python todos os
pedaços das versões pedidas. BM25 fica como experimento futuro (metades A/B).

### M10a Fase 3 — item 2: seção fora dos candidatos (2026-10-16, rejeitado)

`uv run python -m bench.m10a_pool --half A` (só ranking, 0 chamadas pagas):
o pedaço da resposta entra nos 30 candidatos em 87 de 139 tarefas hoje; BM25
não muda (87), peso léxico 0,25 chega a 90, só o título 84, só vetor 81. A busca
léxica não é o gargalo: nas 23 perdas, a pergunta longa (título e corpo com
código) não acha o pedaço em nenhuma das duas buscas. Nada mudou em `app/`.
Achado à parte: a busca léxica do produto (`websearch_to_tsquery`) exige todas
as palavras da consulta, diferente da do benchmark; falta um teste no Postgres.

### M10a Fase 3 — item 1: parada precoce da seleção (2026-10-16)

As 315 tarefas com gabarito foram divididas por sorteio com semente em metade A
(139, ajuste) e metade B (176, confirmação); `bench.anchor_qrels.in_tune_half`.
Regra de aceite em DECISIONS.md (2026-10-16, com a troca da regra de tokens
feita antes de ver a metade B). Na metade A, `uv run python -m
bench.m10a_selection --half A` comparou λ, `min_value` e um portão por valor:
λ = 0 foi o melhor. Na metade B, `uv run python -m bench.m10a_selection --half B
--confirm lambda_0` aceitou: acerto em 8k de 0,608 para 0,688 (+8,0 pts, IC
[+4,0; +12,5]), sem queda em 1k/2k/4k, cobertura por mil tokens acima da do
strong_rag em todos os orçamentos. 0 chamadas pagas. `app/config.py` passou a
λ = 0,0, com teste de regressão em `tests/unit/retrieve/test_select.py`.
Detalhes em `bench/results/m10a/changes.md`. Como testar: os dois comandos
acima (precisam do cache em `bench/.cache/`). Dívida: o `.env` local com
`SELECTION_LAMBDA=0.3` sobrepõe o novo padrão; recongelar `app/` por emenda
antes do piloto.

### M10a Fase 2 — diagnóstico das falhas do NINA (2026-10-16)

`uv run python -m bench.m10a_diagnose` (só cache: 0 chamadas pagas) mede a
seção em cada etapa do pipeline. Falhas em 4k (137 de 315), pela primeira causa:
fora dos candidatos com a seção inteira num pedaço 53, descartada na seleção 53,
picotada entre pedaços 16, perdida na extração 15 (11 no PostgreSQL). A seção
está no índice em 300 tarefas, nos 30 candidatos em 231. Os candidatos somam
mediana de 12.105 tokens (só 94 tarefas abaixo de 8k): a parada do NINA em 5,4
mil tokens vem da seleção, não do tamanho do conjunto (hipótese a confirmar: a
penalidade de diversidade leva o valor ajustado abaixo de `min_value = 0`).
Pareado contra strong_rag: +4,4 pts em 1k (IC [−0,6; +9,2], p = 0,09),
empate em 2k e 4k, −9,5 pts em 8k (IC [−14,9; −3,8], p = 0,001). Saída local
em `bench/results/m10a/{diagnose.csv,failures.md}`.

### M10a Fase 1 — linha de base com provedores reais (2026-10-16)

`uv run python -m bench.m10a_baseline --real` sobre as 315 tarefas `dev` com
gabarito (embeddings OpenAI e rerank Jina, com cache em disco). Acerto =
cobertura da seção ≥ 0,5. Números de desenvolvimento, não evidência da tese.

| orçamento | NINA | strong_rag | naive_rag |
|---|---|---|---|
| 1k | 0,289 (938 tokens) | 0,244 (868) | 0,181 (981) |
| 2k | 0,429 (1.881) | 0,406 (1.992) | 0,337 (1.999) |
| 4k | 0,565 (3.491) | 0,565 (3.994) | 0,492 (3.999) |
| 8k | 0,625 (5.366) | 0,721 (7.928) | 0,629 (7.850) |

Página inteira: 0,978 com 44.823 tokens. Extração da página citada: NINA
0,952, trafilatura 0,978. O NINA tem a maior cobertura por mil tokens em todos
os orçamentos, mas no orçamento de 8k para em 5,4 mil tokens e perde para o
strong_rag; entre ele e a página inteira há ~35 pontos, quase todos na busca e
na seleção. Latência mediana com cache: NINA 1,1–1,6 s, strong_rag ~0,7 s.
Ocorrências: o cache de provedores regravava 297 MB a cada vetor (corrigido em
`bench/provider_cache.py`, agora um log `.jsonl` de uma linha por entrada); duas
chaves da Jina esgotaram a cota no meio da rodada (403), sem perda graças ao
cache. Saída local em `bench/results/m10a/baseline_real.{csv,md}`.

### M10a Fase 1 (parcial) — o que foi feito

- **`bench/anchor_qrels.py`:** a seção que cada âncora nomeia, tirada do HTML
  congelado (sem depender da IR do NINA nem do trafilatura). Gabarito: 315 das
  756 tarefas `dev`; sem chave: 376 sem link com âncora, 30 âncoras inexistentes,
  26 seções sem frase mensurável, 9 seções acima de 4.000 tokens. Seções de 73
  (p10) a 3.746 (p99) tokens, mediana 326. Por site: Python 175, MDN 50,
  PostgreSQL 39, React 19, Docker 15, Kubernetes 9, Flask 4, SQLite 2, RFC 2.
  `uv run python -m bench.anchor_qrels` grava `bench/results/m10a/qrels.jsonl`.
- **`bench/section_recall.py`:** frases normalizadas da seção, cobertura (fração
  presente num contexto), acerto com cobertura ≥ 0,5 e posição do primeiro
  fragmento `[URL]` que traz a seção.

### M10a Fase 0 — o que foi feito

- **Emenda 6 do PREREG:** 25% das tarefas `holdout` (hash com semente
  `20261015:dev:<id>`) viram `dev`, só para ajustar o produto; triagem, rodada
  principal, auditoria e rotulagem usam só as 2.278 `holdout`. O `app/` pode
  mudar no M10a e será congelado de novo por emenda antes de qualquer sessão de
  agente.
- **Contrato:** SPEC §10.6 e `ExternalSplit` aceitam `dev`; o montador aplica
  `in_dev_slice` (com teste); o `external.yaml` foi reetiquetado sem mudar a
  ordem nem outro campo (piloto 20, `holdout` 2.278, `dev` 756).

### M10 Fase 3 (parcial) — o que foi feito

- **`bench/session_cost.py`:** custo de uma sessão a partir do uso por modelo
  (entrada, escrita e leitura de cache, saída) com os preços do
  `bench/prices.yaml`; falha explícita para modelo sem preço ou preço de cache
  ausente; `reconcile` aplica a regra dos ±3% contra a fatura (PREREG §6).
- **`bench/page_length.py`:** a página citada como Markdown do trafilatura
  (`page_markdown`, o texto que o braço `full_page` vai servir), a soma das até
  4 páginas citadas por tarefa e as faixas da Emenda 4. Rodado no corpus:
  curtas 1.453, médias 927, longas 674 (piloto: 10, 6, 4). As longas se
  concentram em Python (473 de 866), PostgreSQL (86 de 308) e RFC (64 de 101), e
  quase não existem no MDN (6 de 1.228): na análise por faixa, faixa e site
  andam juntos. Saída local em `bench/results/m10/page_lengths.csv`.
- **`bench/m10_records.py`:** `results.csv` (uma linha por tarefa × braço,
  gravada ao fim de cada sessão, com `done_pairs` para retomar) e
  `answers.jsonl` (entrada da rotulagem cega). Sessão excluída fica com
  `correct` vazio, e o `bench/power.py` passou a ignorá-la ao parear braços.

### M10 Fase 2 — o que foi feito

- **`bench/judge_external.py`:** a rubrica do PREREG Apêndice B literal (um
  teste a compara com o `bench/PREREG.md`), preenchida numa passada só;
  extração da resposta final pela regra do §4 (último JSON com `answer`, senão a
  mensagem inteira); julgamento com até 2 novas tentativas e semente nova quando
  a resposta do juiz não é JSON válido, e veredicto `None` (falha de
  infraestrutura) se persistir; cegamento dos nomes de ferramentas e produtos
  dos braços (`[tool]`); seleção por hash de ~10% das sessões para o segundo
  julgamento e a taxa de troca de veredicto. Juízes: `gpt-6.1-sol` (principal)
  e `claude-opus-5-5` (segundo), pelos provedores existentes em
  `app/providers/llm`.
- **`bench/agreement.py`:** κ de Cohen (NaN quando indefinido) e a trava de 0,6
  do PREREG §9.
- **`bench/label_answers.py`:** `sample` (100 respostas, cotas iguais por braço ×
  veredicto, semente 20261015, sorteada uma vez), `label --grader A|B` (cego ao
  braço e ao veredicto, pode parar e retomar), `settle` (só as divergências) e
  `report` (κ entre avaliadores e entre o juiz e os rótulos finais, mais a trava).
- **Emenda 5 do PREREG** e DECISIONS 2026-10-15: falha do juiz vira falha de
  infraestrutura, cegamento, e amostra humana só da população primária.
- A precisão de citação reaproveita `bench.grade._grade_citations`, que já
  funciona com `ExternalTask` (há teste). Nada em `app/` mudou.

### Como verificar a Fase 2

```
uv run pytest tests/unit/bench/test_judge_external.py tests/unit/bench/test_agreement.py tests/unit/bench/test_label_answers.py -q
```

### M10 Fase 1 — o que foi feito

- **Fonte (Emenda 1):** dump arquivado do Stack Overflow de 2024-04 (archive.org,
  item `stackexchange`): `stackoverflow.com-Posts.7z` (sha1 `ea0b89fe…`) e
  `stackoverflow.com-Users.7z` (sha1 `0350c000…`), conferidos pelo sha1 que o
  archive.org publica. A API foi descartada porque a Acceptable Use Policy do
  Stack Overflow proíbe coleta automatizada para benchmark de IA. O dump fica
  fora do repositório (`C:\Users\Matheus\Downloads\so-dump-2024-04`).
- **Mineração (`bench/mine_dump.py`):** lê o 7z em streaming pelo `tar` do
  Windows e aplica as regras 1 a 4 do PREREG §2.1 na ordem, sem depender da
  ordem das linhas (o dump não vem ordenado por Id). Resultado
  (`bench/results/m10/mining_stats.json`, gitignored): 24.101.803 perguntas;
  335.514 de 2023-01-01 a 2024-03-31 com resposta aceita (regra 1); removidas
  324.942 pela regra 2 (sem link para doc oficial), 6.727 pela regra 3 (placar
  ou pergunta fechada) e 381 pela regra 4 (imagem ou tamanho); 391 sem a resposta
  aceita no dump; **3.073 candidatas**, 0 posts com erro. As contagens fecham
  exatamente.
- **robots.txt:** conferido à mão pelo humano nos 15 hosts e cruzado com as
  1.914 páginas citadas (DECISIONS 2026-10-15): 28 bloqueadas (27 de versões EOL
  do Python e 1 do SQLite), listadas em `bench/tasks/robots_excluded.txt` e
  nunca buscadas.
- **Build (`bench/external_tasks.py`):** ordem aleatória estratificada por site
  (semente 20261015), congelamento das páginas citadas em modo append e em lotes
  (1.871 páginas novas, 0 falhas; as 50 originais intactas), regra 5 (19
  candidatas ficaram sem página congelada), 5 URLs por tarefa (citadas mais
  distratores do mesmo site) e evidência-ouro a partir dos blockquotes.
  Resultado: **3.054 tarefas**, piloto com 2 por site. Por site: MDN 1.228,
  Python 866, PostgreSQL 308, React 158, Docker 133, Angular 131, RFC 101,
  Kubernetes 63, SQLite 50, Flask 16. Por tipo: code 1.827, multi_doc 724,
  fact 332, version 171. Com evidência-ouro: 176 tarefas (193 citações).
- **Emendas 2 e 3:** a auditoria cobre cerca de 20% da população primária,
  depois da triagem, com seleção por hash por tarefa; o `count_tokens` lê
  `<|endoftext|>` como texto comum, e o `app/` congelado do M10 passa a ser
  `f1e9f2a`. A Emenda 4 (análises secundárias por tamanho de página e
  extrapolação para sessões longas) veio da sessão principal.
- **Ferramentas novas:** `bench/audit_external.py` (auditoria que pode parar e
  retomar; exige o arquivo com os ids da população primária),
  `snapshot(..., append=True)`, `CorpusStore` resolvendo também a URL final de
  redirecionamentos, e o modelo `ExternalTask` em `bench/tasks_schema.py`.

### M10 — pendente e dívidas

- Fase 3: o harness do agente e a ligação do braço `nina` ao corpus congelado
  estão pausados (duas respostas do agente foram interrompidas pelo sistema de
  segurança nesse trabalho); o humano decide como seguir. Depois, as Fases 4 e
  5, uma por vez, cada uma com plano aprovado.
- Antes do piloto da Fase 3 (PREREG §6; a Emenda 4 usa `p_read`):
  `bench/prices.yaml` não tem `cache_write_usd_per_1m` nem
  `cache_read_usd_per_1m` para nenhum modelo; o humano confere os valores na
  página de preços. O custo por sessão usa `bench/session_cost.py`, que falha se
  esses preços faltarem (o `model_cost_usd` do M8 não serve: só lê entrada e
  saída e custa 0 para modelo desconhecido).
- O humano ainda vai indicar os 2 avaliadores da rotulagem cega (cerca de 2 a 3
  horas cada, depois da rodada principal da Fase 3).
- O formato de entrada da rotulagem (`bench/results/m10/answers.jsonl`, uma
  linha por tarefa × braço com `task_id`, `arm`, `answer`, `judge_correct`) está
  descrito em `bench/label_answers.py`; a Fase 3 precisa gravá-lo assim.
- Só 176 das 3.054 tarefas (5,8%) têm evidência-ouro: os blockquotes das
  respostas costumam citar outra versão da doc, mensagens de erro ou outras
  fontes. Nas demais, a taxonomia de falhas do PREREG §8 dá `unattributed`.
- `bench/snapshot.py` não consulta robots.txt sozinho (no M10 a checagem foi
  manual). Corrigir antes de qualquer snapshot novo.
- O pool é desbalanceado (MDN e Python somam 68%). A ordem estratificada iguala
  as cotas até os sites pequenos acabarem (Flask tem 16 tarefas).
- Fora desta fase: o commit `b815b75` (README em inglês) faz o
  `ruff format --check .` falhar por causa de um trecho de Python no README.

### Como verificar a Fase 1

```
uv run pytest tests/unit/bench/test_mine_dump.py tests/unit/bench/test_external_tasks.py tests/unit/bench/test_audit_external.py tests/unit/bench/test_snapshot.py -q
uv run python -c "from bench.tasks_schema import load_external_tasks as f; print(len(f('bench/tasks/external.yaml')))"
```

Reproduzir do zero leva horas e usa a rede: baixar o dump e conferir os sha1
(PREREG Emenda 1), depois rodar
`uv run python -m bench.mine_dump --posts <dir>/stackoverflow.com-Posts.7z --users <dir>/stackoverflow.com-Users.7z`
e `uv run python -m bench.external_tasks --exclude bench/tasks/robots_excluded.txt`.

### M10 Fase 0 — o que foi feito

- **`bench/PREREG.md`** (em inglês, para leitores de fora): afirmação testada,
  população (Stack Overflow 2023+, resposta aceita citando página oficial dos 10
  sites), triagem sem documentação, fatia sintética-ponte (41 tarefas `test`),
  braços, harness, tamanho de amostra, H1 (não-inferioridade, margem 7.5 pts) e
  H2 (custo por tarefa correta), regra de relato (a manchete usa o limite
  inferior do IC), causas de falha, juízes + κ, protocolo e regra de parada,
  emendas. Apêndices: evidências, textos congelados (prompts, rubrica,
  checklist da auditoria) e domínios oficiais.
- **`bench/power.py`** + `tests/unit/bench/test_power.py`: discordância pareada
  a partir de qualquer `results.csv` e n para não-inferioridade. Reproduz a
  tabela do PREREG (349 tarefas com ψ = 0.25 e margem 7.5 pts).
- **Evidência medida nos artefatos existentes** (PREREG Apêndice A): piso de
  ruído (o `naive_rag` trocou 4/41 veredictos com contexto byte-idêntico entre
  duas rodadas), discordância 0.12–0.24 contra os RAGs, McNemar exato p de 0.11
  a 1.0, e 1 URL por tarefa nas 137 tarefas.
- **Verificado:** `claude-agent-sdk` 0.2.167 é MIT e embute o CLI do Claude
  Code; a API do Stack Exchange responde sem chave (300 req/dia) e não tem
  robots.txt (404, ou seja, sem Disallow).
- **Docs:** SPEC §10.1 (exceção dos braços ao vivo) e §10.6 (trilha de agente),
  DECISIONS 2026-10-15, ROADMAP (M10 novo, Deploy → M11), HUMANO.md ("No
  M10"), CLAUDE.md (PREREG nos documentos de referência) e os comentários M10 →
  M11 em `app/config.py` e `app/fetch/render.py`.
- Antes da Fase 0, o trabalho pendente do `strong_rag` foi commitado sozinho
  (`3c8bd49`), com o gate completo passando.

### Como verificar a Fase 0

```
uv run pytest tests/unit/bench/test_power.py -q
uv run python -m bench.power --psi 0.25
uv run python -m bench.power --results bench/results/strong-rag-20261014T211154_582208/results.csv --a ours --b strong_rag --budget 4000
```

O último comando lê um artefato local (gitignored).

---

Marco anterior: **M9 (Pronto para pilotos) — CONCLUÍDO** (SPEC §7/§8/§9, ROADMAP
M9). O NINA agora é consumível com segurança por um piloto externo: chaves de
API + autenticação Bearer em todas as rotas /v1, rate limit (token bucket em
memória por processo) + cota diária, gravação de `usage_events` em toda
requisição /v1 + `GET /v1/usage`, servidor MCP (3 ferramentas, stdio +
streamable HTTP), SDK Python tipado instalável (`sdk/python/`, dist
`nina-client`) e `docs/QUICKSTART.md` com 3 exemplos copiáveis. Sem crawler e sem
LLM no caminho crítico (inalterado). NADA foi commitado (decisão humana).

### M9 — o que foi entregue

- **FEAT-001 — Chaves + auth Bearer.** Migração `0004_api_keys_usage_events`
  (novo HEAD), modelos `ApiKey`/`UsageEvent`, geração/hash de chave
  (`app/usage/keys.py`: `nina_` + `token_urlsafe(32)`, sha256, mostrada uma
  vez), `ApiKeyRepo`, CLI `python -m app.admin create-key/revoke-key`
  (`app/admin.py`), e o dependency `require_api_key` (`app/api/auth.py`) anexado
  a TODAS as rotas /v1 — no-op em `ENV=dev`, exigido fora de dev, `/health` sem
  auth. `Unauthorized`(401) em `app/api/errors.py`.
- **FEAT-002 — Rate limit + cota + uso.** `TokenBucketLimiter` em memória por
  processo (`app/usage/limits.py`, thread-safe, relógio injetável), cota diária
  de renders reusando `rate_limited`/429 + `Retry-After`, `UsageEventRepo`
  (`app/usage/events.py`, agregação p50/p95 nearest-rank), `UsageMiddleware` +
  `GET /v1/usage` (`app/api/usage.py`), schemas de usage e `RateLimited`(429)
  com header `Retry-After`.
- **FEAT-003 — Servidor MCP.** `app/mcp_server/` com o SDK oficial `mcp` (MIT):
  `client.py` (cliente HTTP fino tipado, `httpx` próprio, Bearer da env),
  `server.py` (`build_server` com as 3 ferramentas `resolve_url`/
  `assemble_context`/`get_document`, cada descrição avisando `untrusted_source`),
  `__main__.py` (stdio default, streamable HTTP via `--http`).
- **FEAT-004 — SDK Python + e2e + QUICKSTART + docs.** `sdk/python/` como
  distribuição SEPARADA (`nina-client`, build `hatchling`, só `httpx` em
  runtime): `nina_client/client.py` (`NinaClient` tipado com
  `resolve`/`assemble`/`get_document`, Bearer, retries/backoff honrando
  `Retry-After`, transport injetável), `errors.py` (erros tipados mapeando a
  tabela §8 por `code`) e `models.py` (dataclasses tolerantes espelhando
  `app/api/schemas.py`). `tests/unit/sdk/test_sdk_client.py` (OFFLINE,
  `httpx.MockTransport`): Bearer enviado, resposta tipada, retry em
  429+`Retry-After`, `UnauthorizedError`/`RateLimitedError` do envelope.
  `tests/integration/test_sdk_e2e.py`: `NinaClient` contra a API local
  (TestClient, ENV=production, providers FAKE, chave via `ApiKeyRepo`, transport
  sync delegando ao TestClient) recebe fragmentos com proveniência — zero rede,
  zero chaves reais. `docs/QUICKSTART.md` com 3 blocos copiáveis (curl, SDK,
  config MCP no Claude Desktop/Code) começando pelo passo de criar a chave.

### M9 — posicionamento (eficiência + evidência) nos docs

O README ganhou uma seção de uso para pilotos (criar chave, auth Bearer, SDK,
MCP, link para o QUICKSTART) que reafirma o posicionamento validado no M8:
**paridade de qualidade com o `naive_rag` a ~metade do custo, com evidência
rastreável** (test @4000: sucesso `ours` 0.780 vs 0.707 — empate de IC; custo
US$ 0.0051 vs 0.0089, ~43% mais barato; ~44% menos tokens de contexto; citação
0.585; INSUFFICIENT 0.000). O produto é vendido como "o mesmo resultado de um
RAG competente, por menos custo e com a prova anexada", NÃO como "mais preciso
que RAG".

### M9 — dívidas / notas

- O token bucket é EM MEMÓRIA por processo (decisão humana): não escala
  horizontalmente — 1 bucket por worker, adequado ao MVP de 1 worker. Escalar
  horizontalmente exigirá um limiter compartilhado (Redis/Postgres) num marco
  futuro.
- A cota diária de renders reusa o code §8 `rate_limited` (a SPEC não tem code
  dedicado para cota) — escolha documentada em DECISIONS.md.
- Os provedores reais (OpenAI/Jina) e o MCP/SDK contra a API real nunca são
  exercidos em teste (zero rede); só a execução humana com chave os usa.
- O SDK é uma distribuição separada e NÃO é instalado pelo build principal; os
  testes o importam adicionando `sdk/python` ao `sys.path`. Para publicar o SDK,
  empacotar `sdk/python/` isoladamente (`pip install ./sdk/python`).

### Como testar o M9 manualmente

Gate rápido (sem serviços, sem rede), Windows/PowerShell (separador `;`):

```
uv run ruff check . ; uv run ruff format --check . ; uv run mypy app ; uv run pytest -m "not integration and not browser"
```

Integração (Postgres + pgvector + SeaweedFS via Docker):

```
docker compose up -d --wait postgres seaweedfs ; docker compose up s3-init ; uv run alembic upgrade head ; uv run pytest -m integration -q
```

(head `0004_api_keys_usage_events`). Round-trip de migração:
`uv run alembic downgrade -1 ; uv run alembic upgrade head`. NÃO rode
`cp .env.example .env` por cima do `.env` real.

Uso real do SDK/MCP (humano, exige a API no ar e uma chave): criar a chave com
`python -m app.admin create-key --name piloto-x` (mostrada uma vez), exportar
`API_URL`/`API_KEY`, e seguir os 3 blocos do `docs/QUICKSTART.md`.

### M9 — resultado do gate (árvore atual, nada commitado)

```
uv run ruff check .            -> All checks passed!
uv run ruff format --check .   -> 201 files already formatted
uv run mypy app                -> Success: no issues found in 65 source files
uv run pytest -m "not integration and not browser"
                               -> 490 passed, 48 deselected, 37 snapshots passed
uv run pytest -m integration -q (infra no ar, head 0004)
                               -> 39 passed, 499 deselected
```

Os 490 incluem os 6 novos unit do SDK (`tests/unit/sdk`); os 39 de integração
incluem os 2 novos e2e do SDK (`tests/integration/test_sdk_e2e.py`). O SDK
isolado passa `mypy --strict` (4 arquivos). Nenhum teste M0–M8 regrediu.

---

### strong_rag — confronto com um RAG não-naive (split test, 2026-10-14)

Reforço posterior ao M8, por pedido (objeção do primo): o `naive_rag_vector`,
mesmo vetorial, ainda é "naive"; faltava medir o NINA contra um RAG NÃO-naive.
Foi adicionado o baseline `strong_rag` — um pipeline INDEPENDENTE, sem nenhuma
peça do NINA, montado para VENCER (não espantalho): chunking com overlap (janela
deslizante ~400 tokens, passo 300 = ~25% overlap), híbrido BM25+vetorial fundido
por RRF k=60, rerank cross-encoder Jina do top-30, seleção top-k por score de
rerank sob orçamento, e proveniência `[URL]` byte-idêntica aos demais. FRONTEIRA
SEM-LLM: sem reescrita de query nem compressão por LLM (quebraria a regra de
"nenhum LLM na recuperação") e SEM a seleção MMR do NINA (a variável isolada). O
split `test` foi medido UMA vez reusando o cache persistente; `app/config.py` só
foi lido (`git diff` vazio), `lexical_weight`=1.0. Artefato:
`bench/results/strong-rag-20261014T211154_582208/` (git_commit `a6b29bb`).

Resultado honesto (orçamento de referência 4000, IC95% bootstrap). Nenhum IC não
sobreposto em categoria alguma → **EMPATE em todas, inclusive geral**; o
`strong_rag` NÃO venceu o `ours`, e o `ours` não venceu o `strong_rag`:

| categoria | n | ours | ours IC95 | strong_rag | strong_rag IC95 | veredito |
|---|---|---|---|---|---|---|
| geral | 41 | 0.756 | [0.610, 0.878] | 0.683 | [0.537, 0.829] | empate |
| api_reference | 17 | 0.706 | [0.471, 0.941] | 0.706 | [0.471, 0.882] | empate |
| changelog | 4 | 1.000 | [1.000, 1.000] | 0.750 | [0.250, 1.000] | empate |
| guide | 12 | 0.833 | [0.583, 1.000] | 0.750 | [0.500, 1.000] | empate |
| params_table | 5 | 0.600 | [0.200, 1.000] | 0.600 | [0.200, 1.000] | empate |
| pdf | 3 | 0.667 | [0.000, 1.000] | 0.333 | [0.000, 1.000] | empate |

À paridade de qualidade, o `ours` entrega com MUITO menos recurso (orçamento
4000): **contexto 1495.0 vs 2823.4 tokens** (~53% do contexto do `strong_rag`),
**custo/tarefa concluída US$ 0.005250 vs US$ 0.009675** (o `strong_rag` custa
~1.84×), **INSUFFICIENT 0.000 vs 0.098** e **citação 0.585 vs 0.512**. No
orçamento 2000 o quadro se mantém: `ours` 0.805 [0.683, 0.927] vs `strong_rag`
0.659 [0.512, 0.805] → empate, com o `ours` de novo no menor contexto; elevar
2000→4000 não melhorou o `ours` (0.805→0.756) e nudou o `strong_rag`
(0.659→0.683). (Latência, fora do veredito de qualidade: no orçamento 2000 o
`strong_rag` teve p50 ≈ 58s / p95 ≈ 980s por chamar a Jina real para os chunks
de overlap novos na primeira passagem; a passagem 4000 reusou o cache e
normalizou para p50 ≈ 96ms.)

Custo real: respondedor OpenAI `gpt-6.1-sol` **US$ 1.566** exatos do
`results.csv` (ours $0.291, naive_rag $0.419, naive_rag_vector $0.421,
strong_rag $0.435); recuperação ~96% do cache (Jina 41 reais / 82 hits;
embeddings 35 batches / 9371 hits — os chunks de overlap geram alguns embeddings
novos, como esperado); somando o juiz Anthropic + embeddings/rerank, total real
~US$ 1.6–2.0, dentro da folga de ~US$ 8. **TESE SOBREVIVE:** mesmo contra um RAG
NÃO-naive, o NINA entrega a MESMA qualidade por ~metade do custo e do contexto,
com citação verificável e sem desistir de tarefas.

### Como testar o strong_rag manualmente

Gate rápido offline (sem serviços, sem rede), Windows/PowerShell (separador `;`):

```
uv run ruff check . ; uv run ruff format --check . ; uv run mypy app ; uv run pytest -m "not integration and not browser"
```

(inclui os testes offline do runner em `tests/unit/bench/test_strong_rag.py`:
overlap da janela deslizante, seleção top-k por rerank — NÃO MMR —, proveniência
`[URL]`, determinismo e registro no registry).

Execução real da medição (humano, gasta chaves; split `test` medido UMA vez,
reusa `bench/.cache/`). Subir a infra e confirmar as 4 chaves no `.env` real
(`BENCH_ANSWERER_API_KEY` OpenAI, `BENCH_JUDGE_API_KEY` Anthropic,
`RERANKER_API_KEY` Jina, `EMBEDDING_DIM=1536`) — NÃO rode `cp .env.example .env`
por cima do `.env` real:

```
docker compose up -d --wait postgres seaweedfs ; docker compose up s3-init ; uv run alembic upgrade head
uv run python -m bench.sweep --strategies naive_rag,naive_rag_vector,strong_rag,ours --split test --budgets 2000,4000 --real --out-name strong-rag-2026-10-14
```

Saída: `bench/results/strong-rag-2026-10-14/{results.csv,report.md,config.json,sumario.md}`
(gitignored). HONESTIDADE: `app/config.py` só é LIDO; `git diff app/config.py`
deve permanecer vazio; `lexical_weight`=1.0; o split `test` é medido UMA vez.

---

Marco anterior: M8 ETAPA 2 (benchmark completo no split `test`) — CONCLUÍDA. A
Etapa 1 (calibração dos parâmetros de seleção no `dev` e congelamento) está
concluída e commitada em `e75273e`; a Etapa 2 rodou o benchmark COMPLETO no
split `test` (41 tarefas × 9 estratégias × 3 orçamentos) com os provedores REAIS
e os parâmetros CONGELADOS, e produziu os quatro artefatos (`report.md`,
`results.csv`, `config.json`, `sumario.md`) em
`bench/results/m8-stage2-2026-10-13/`. A REGRA DE HONESTIDADE foi mantida: o
split `test` foi apenas MEDIDO, nunca usado para ajustar; `git diff app/config.py`
permanece vazio. Veja a seção "M8 ETAPA 2" abaixo para o resultado e o gate.

O que foi entregue na Etapa 1:

- **Parâmetros injetáveis.** `OursRunner` (`bench/runners/ours.py`) ganhou
  campos opcionais `lambda_`/`min_value` (default = constantes do módulo,
  comportamento idêntico ao de hoje) e `embeddings`/`reranker` (default `None` →
  os fakes determinísticos são construídos por chamada, então todo teste
  continua offline). A seleção em `app/retrieve/select.py` e `assemble.py` já
  recebia/encaminhava os parâmetros; a mudança foi mínima e aditiva. Novo método
  `OursRunner.max_rerank_score(task, store=...)` roda os passos 1–5 do pipeline e
  devolve o MAIOR score de rerank do top-30 (o observável que a cobertura usa).
- **Mecanismo de calibração (`bench/calibrate.py`, utilitário de execução
  humana, NÃO coletado pelo pytest).** `GRID` = λ ∈ {0.3,0.5,0.7} × min_value ∈
  {0.0,0.05,0.1} (9 combinações), `CALIBRATION_BUDGET = 4000`. A LÓGICA PURA é
  unit-testada com dados sintéticos (sem rede): `pick_best` (taxa de sucesso
  desc; desempate por menor custo/tarefa concluída, depois menor média de tokens
  de contexto, depois `(lambda_, min_value)` crescente — vencedor único e
  determinístico), `percentiles`/`coverage_from_scores` (p33/p66). `run_calibration`
  carrega SÓ `split == "dev"` (regra de honestidade em código), constrói os
  provedores REAIS, mede cada combinação (reusando o caminho answer+grade+price
  do `bench.run`), escolhe o vencedor, deriva a cobertura dos percentis do
  máximo-rerank observado no `dev` (D7) e escreve o artefato
  `bench/results/calibration_<ts>/` (`combinations.csv`, `winner.json`,
  `summary.md`). O reranker Jina ganhou retry/backoff em 429/5xx.
- **Cache de cota (em memória + PERSISTENTE em disco).** `lambda_`/`min_value`
  afetam SÓ a seleção gulosa final; busca+RRF+rerank são invariantes a eles.
  Então o calibrador roda a parte cara UMA vez por tarefa (`prepare_candidates`)
  e aplica só a seleção pura (`assemble_from_candidates`) por combinação — rerank
  e embeddings caem de 9×96 para 1×96. Um teste prova que a seleção com
  candidatos cacheados é byte-idêntica ao pipeline completo nas 9 combinações.
  Além do cache em memória, `bench/provider_cache.py` (`bench/.cache/`,
  gitignored) persiste rerank/embeddings content-addressed por `(model+query+docs)`
  / `(model+text)`: sobrevive a re-execução e é REUSÁVEL na Etapa 2 (o rerank de
  uma tarefa é invariante a orçamento/ablação), então o split `test` NÃO
  re-chamará a Jina para pares já vistos. O rerank só é chamado para o top-30 do
  RRF.
- **Correção de bug de conformidade com a SPEC §5 (ver DECISIONS.md 2026-10-13).**
  A calibração real expôs um `definition_list` de 11.779 tokens (Flask) que
  violava o limite de 800 tokens da SPEC §5 e estourava o limite de 8192 tokens
  da API de embeddings. O chunker passou a DIVIDIR prosa/list/definition_list
  grandes (antes só tabela/código eram divididos). Varredura do corpus após a
  correção: 3132 chunks, nenhum acima de 800 nem de 8192. O property test ganhou
  um gerador de prosa grande.

### M8 ETAPA 1 — calibração (execução humana)

A calibração real é um passo HUMANO (gasta chaves) — o agente/CI não a executa em
teste. Pré-requisitos (Windows/PowerShell, separador `;`):

```
docker compose up -d --wait postgres seaweedfs ; docker compose up s3-init ; uv run alembic upgrade head
```

(head `0003_chunks`). O `.env` real precisa das chaves `BENCH_ANSWERER_API_KEY`
(OpenAI), `BENCH_JUDGE_API_KEY` (Anthropic), `RERANKER_API_KEY` (Jina) e
`EMBEDDING_DIM=1536`. **NÃO** rode `cp .env.example .env` por cima do `.env`
real — isso apagaria as chaves. Invocação:

```
uv run python -m bench.calibrate --real
```

Toca APENAS `split == "dev"` (96 tarefas), NUNCA o `test` (41 tarefas), orçamento
4000, 9 combinações. Gasta chaves (OpenAI respondedor + embeddings, Anthropic
juiz, Jina reranker). A saída (o artefato sob `bench/results/calibration_<ts>/`)
alimenta o congelamento dos parâmetros.

### M8 ETAPA 1 — resultado da calibração real (dev, orçamento 4000)

Calibração REAL concluída no split `dev` (96 tarefas), orçamento 4000, provedores
reais (respondedor OpenAI `gpt-6.1-sol`, juiz Anthropic `claude-sonnet-5-5`,
reranker Jina, embeddings OpenAI `text-embedding-3-small`). Artefato:
`bench/results/calibration_20261012T171216_425732`. Grade: λ ∈ {0.3,0.5,0.7} ×
min_value ∈ {0.0,0.05,0.1} = 9 combinações.

| lambda | min_value | success | cost/completed (USD) | ctx_tokens |
|---|---|---|---|---|
| 0.30 | 0.00 | **0.8125** | 0.004593 | 1352.2 | ← VENCEDOR |
| 0.30 | 0.05 | 0.6875 | 0.001914 | 156.0 | |
| 0.30 | 0.10 | 0.2500 | 0.002867 | 15.5 | |
| 0.50 | 0.00 | 0.8021 | 0.003506 | 887.5 | |
| 0.50 | 0.05 | 0.6354 | 0.001867 | 125.8 | |
| 0.50 | 0.10 | 0.2396 | 0.002917 | 14.5 | |
| 0.70 | 0.00 | 0.7812 | 0.002864 | 606.1 | |
| 0.70 | 0.05 | 0.5833 | 0.001891 | 108.1 | |
| 0.70 | 0.10 | 0.2292 | 0.003048 | 14.5 | |

**Vencedor (taxa de sucesso): λ = 0.3, min_value = 0.0 (0.8125).** Resultado
honesto do dev: a seleção se sai MELHOR SEM corte por valor mínimo — qualquer
`min_value` > 0 reduz o sucesso (0.05 → ~0.63-0.69; 0.10 → ~0.23-0.25).

**Cobertura (coverage_low/high) — corrigida (DECISIONS.md 2026-10-13 "fix").**
A causa raiz era que o `JinaRerankProvider` normalizava min-max POR CHAMADA
(desde o M7), então o máximo de rerank por tarefa era sempre 1.0 e `_coverage`
(que olha esse máximo) retornava "sufficient" quase sempre — campo enganoso.
Corrigido: o reranker passa a devolver o `relevance_score` ABSOLUTO (comparável
entre tarefas); a normalização min-max usada pela SELEÇÃO foi movida para
`rerank_candidates` (só para o `value`, seleção inalterada), e `scores.rerank`
reporta o absoluto. Os limiares vêm agora do p33/p66 da distribuição do MÁXIMO
ABSOLUTO por tarefa — a MESMA grandeza que `_coverage` observa — recalculada SÓ
no dev via `bench.calibrate --coverage-only --real` (96 tarefas; cache de rerank
reconstruído com scores ABSOLUTOS, embeddings do cache intacto → ZERO OpenAI):
min 0.1413, max 0.9073 → **coverage_low = p33 = 0.6137**,
**coverage_high = p66 = 0.7746**. Com esses limiares CONGELADOS (arredondados em
config), as 96 tarefas se dividem via `_coverage` em **32 insufficient / 31
partial / 33 sufficient** (a tarefa com máximo 0.61369967 fica < 0.6137 →
insufficient) — a cobertura NÃO colapsa.
Artefato: `bench/results/coverage_20261012T193038_322186/` (`coverage.json` +
`coverage_scores.csv` com os 96 máximos por tarefa; re-rodar `percentiles` sobre o
CSV reproduz 0.6137/0.7746, sem Jina). A primeira passagem sofreu 2 respostas 429
(rate limit) em draft-0063/draft-0087; a re-execução serviu as 94 já cacheadas e
completou as 2 restantes (~96 chamadas reais de Jina no total).

**Parâmetros CONGELADOS** em `app/config.py`, `.env.example` e `.env` real:
`selection_lambda=0.3`, `min_value=0.0`, `coverage_low=0.6137`,
`coverage_high=0.7746` (`rrf_k=60` mantido, não calibrado). As CHAVES do `.env`
real foram preservadas.

**Custo da calibração:** respondedor OpenAI ~437.135 tokens in + ~51.781 out ≈
**1,39 USD** (`bench/prices.yaml`). Rerank/embeddings ficaram ~1×96 chamadas
graças ao cache persistente (muito abaixo dos 10M tokens do plano free da Jina).

**Gate (após o fix da cobertura):** `ruff check` limpo, `ruff format --check`
limpo, `mypy app` limpo, `pytest -m "not integration and not browser"` → **440
passed, 36 deselected, 37 snapshots passed** (os 36 deselecionados são
`integration`/`browser`). O gate do commit da calibração (`a34d52c`) também
passou verde (orquestrador + CI).

**HONESTIDADE:** a calibração foi feita EXCLUSIVAMENTE no split `dev`. O split
`test` (41 tarefas) NÃO foi tocado — o calibrador filtra `split == "dev"` em
código. A ETAPA 2 (split `test` completo) roda depois, pelo humano, reusa o cache
persistente (zero novas chamadas de Jina para pares já vistos) e exigirá créditos
OpenAI (o respondedor roda muito e não é cacheável).

### Como testar o M8 Etapa 1 manualmente

Gate rápido (sem serviços, sem rede): `uv run ruff check . ; uv run ruff format
--check . ; uv run mypy app ; uv run pytest -m "not integration and not browser"`
— inclui os testes de LÓGICA de calibração (`tests/unit/bench/test_calibrate.py`),
o teste de injeção de reranker em `test_ours_runner.py` e o property test de
chunker estendido para prosa grande. A calibração real é o comando humano acima.

### M8 ETAPA 1 — resultado do gate (iteração de revisão 2, 2026-10-13)

Gate executado no estado atual da árvore (mudanças da Etapa 1 aplicadas; nada
commitado por esta iteração), Windows/PowerShell:

```
uv run ruff check .            -> All checks passed!
uv run ruff format --check .   -> 167 files already formatted
uv run mypy app                -> Success: no issues found in 56 source files
uv run pytest -m "not integration and not browser"
                               -> 440 passed, 36 deselected, 37 snapshots passed
```

Os 440 são os 437 testes pré-existentes de retrieve/bench mais os acréscimos da
Etapa 1 (lógica de calibração, injeção/equivalência do `ours`, rerank absoluto e
cobertura). Nenhum teste foi desativado, pulado ou afrouxado. O driver de
calibração (`run_calibration`/`recompute_coverage`) não é coletado pelo pytest —
só a lógica pura é testada.

### M8 — MEDIÇÃO FINAL honesta no split `test` após o fix de proveniência (2026-10-13, commit `6cfad7c`)

Esta é a MEDIÇÃO FINAL do split reservado, DEPOIS da correção do Defeito 1
(proveniência por fragmento no runner `ours`, commit `6cfad7c`) e do diagnóstico
do Defeito 2 (rerank). SUPERSEDE os números da seção "M8 ETAPA 2" logo abaixo
(que vinham do commit `48e9e57`, ANTES do fix, com citação 0.000). Artefato:
`bench/results/m8-test-2026-10-13/` (1107 linhas = 41 × 9 × 3; git_commit
`6cfad7c`). `git diff app/config.py` = **vazio**; `.env` preservado. Detalhe
completo em `.agents/tasks/m8-test-remeasure.md`.

- **Citação do `ours` no TEST: 0.000 → 0.585.** O fix de proveniência fez o
  `ours` passar a emitir citações rastreáveis e VERIFICÁVEIS: coluna
  `citation_correct` do `results.csv` = 24/41 tarefas (precisão 0.585) nos três
  orçamentos. Confirma, no split reservado, o mesmo efeito do dev (0.000 → 0.406).
- **Qualidade `ours` × `naive_rag` por categoria (orçamento de referência 4000,
  IC95% bootstrap, n_boot=1000/seed=1234567).** Nenhum IC não sobreposto em
  categoria alguma → EMPATE em todas, inclusive geral; `ours` numericamente à
  frente, nenhuma derrota de IC não sobreposto:

  | categoria | n | ours | ours IC95 | naive_rag | naive_rag IC95 | veredito |
  |---|---|---|---|---|---|---|
  | geral | 41 | **0.780** | [0.634, 0.902] | 0.707 | [0.561, 0.854] | empate |
  | api_reference | 17 | 0.765 | [0.529, 0.941] | 0.706 | [0.471, 0.882] | empate |
  | changelog | 4 | 1.000 | [1.000, 1.000] | 1.000 | [1.000, 1.000] | empate |
  | guide | 12 | 0.833 | [0.583, 1.000] | 0.750 | [0.500, 1.000] | empate |
  | params_table | 5 | 0.600 | [0.200, 1.000] | 0.600 | [0.200, 1.000] | empate |
  | pdf | 3 | 0.667 | [0.000, 1.000] | 0.333 | [0.000, 1.000] | empate |

  Nos três orçamentos: `ours` 0.732/0.780/0.805 (SOBE com orçamento) vs
  `naive_rag` 0.780/0.707/0.683 (CAI com orçamento).
- **Custo ~metade.** Orçamento 4000, custo/tarefa concluída: `ours` **US$
  0.005100** vs `naive_rag` **US$ 0.008923** (~43% mais barato), com ~44% menos
  tokens de contexto (1495 vs 2678) e INSUFFICIENT=0.000 (vs 0.098 do RAG). A
  tese do produto ("menos custo pelo mesmo resultado, com evidência rastreável")
  se SUSTENTA no split reservado — qualidade em empate, mais barato, e agora
  COM citações verificáveis.
- **Rerank AJUDA no test** (orçamento 4000): `ours`=0.780 > `ours_no_rerank`=
  0.732 (delta +0.049 a favor do reranker; ICs sobrepostos). INVERTE o sinal da
  rodada pré-fix (que dizia no-rerank melhor) e fica consistente com o dev
  (+0.062). Não desligar o reranker por padrão.
- **9 falhas** (`correct=0`) do `ours` no 4000, diagnosticadas (DS6):
  retrieval/answer (ver `report.md`).
- **Reuso do cache: ZERO gasto novo de Jina/embeddings.** Rerank (Jina): 0
  chamadas reais / 205 cache hits. Embeddings (OpenAI): 0 batches reais / 14501
  cache hits. O fix muda só o passo de emissão (header `[URL]`), não a
  recuperação, então todos os pares já estavam no cache persistente. Nenhum dos
  10M tokens de Jina/embeddings consumido. O respondedor OpenAI + juiz Anthropic
  (não cacheáveis) gastaram na ordem autorizada (US$3–6) nesta rodada de 1107
  respostas.
- **Template obsoleto FLAGADO e corrigido.** O gerador `bench/report_m8.py`
  emitia um parágrafo FIXO ("citation precision is 0.000" / "`ours` não cita" /
  "ours_no_rerank=0.805 vs ours=0.732") herdado da rodada antiga, que
  CONTRADIZIA a própria coluna `cite_prec`=0.585 do mesmo relatório. O gerador
  foi corrigido para COMPUTAR a precisão de citação e o efeito do rerank a partir
  dos dados medidos; `report.md`/`sumario.md` desta rodada carregam os números
  reais (fonte autoritativa: `results.csv`, conferido coluna a coluna).

### M8 ETAPA 2 — benchmark no split `test` (concluída, 2026-10-13)

> NOTA: os números desta seção são da rodada pré-fix (commit `48e9e57`, citação
> 0.000). A MEDIÇÃO FINAL pós-fix (commit `6cfad7c`, citação 0.585) está na seção
> acima e a SUPERSEDE. Esta seção fica como registro histórico da mecânica da
> Etapa 2.

O que foi entregue:

- **Varredura de orçamentos com reuso de recuperação.** `bench/sweep.py`
  (`run_sweep` + a função pura `build_sweep_rows`) mede todas as estratégias em
  3 orçamentos (2000/4000/8000) numa só execução, preparando a recuperação cara
  (`prepare_candidates`) UMA vez por tarefa e aplicando só a seleção pura por
  orçamento (DS3). `make_ours_runners`/`bench.run.run`/`register_ours` ganharam
  o seam para injetar os provedores REAIS de recuperação (OpenAI embeddings +
  Jina reranker, disk-cached) nos runners `ours*` no caminho `--real`; o default
  continua sendo os fakes (todo teste segue offline).
- **Relatório estendido M8.** `bench/report_m8.py` (`write_report`,
  `compare_ours_vs_rag`, `diagnose_failure`) emite os quatro artefatos: tabelas
  por estratégia e por categoria com IC95% bootstrap, custo/concluída e latência
  p50/p95 por orçamento; a análise de ablação; a lista de falhas do `ours` com
  diagnóstico determinístico (DS6); `config.json` com os parâmetros congelados +
  commit + versões; e o `sumario.md` em português.
- **Contagens.** 41 tarefas × 9 estratégias × 3 orçamentos = **1107 linhas**
  em `results.csv` (firecrawl ausente, 0 tarefas ignoradas por `validated:false`).
  Orçamento de referência (ablação + falhas) = 4000.
- **Resultado — `ours` × `naive_rag` por categoria (orçamento de referência
  4000, IC95% bootstrap, n_boot=1000/seed=1234567).** Nenhum IC não sobreposto em
  categoria alguma → **EMPATE em todas**, inclusive no geral:

  | categoria | n | ours | ours IC95 | naive_rag | naive_rag IC95 | veredito |
  |---|---|---|---|---|---|---|
  | geral | 41 | 0.732 | [0.585, 0.854] | 0.707 | [0.561, 0.854] | empate |
  | api_reference | 17 | 0.706 | [0.471, 0.941] | 0.706 | [0.471, 0.882] | empate |
  | changelog | 4 | 1.000 | [1.000, 1.000] | 1.000 | [1.000, 1.000] | empate |
  | guide | 12 | 0.833 | [0.583, 1.000] | 0.750 | [0.500, 1.000] | empate |
  | params_table | 5 | 0.400 | [0.000, 0.800] | 0.600 | [0.200, 1.000] | empate |
  | pdf | 3 | 0.667 | [0.000, 1.000] | 0.333 | [0.000, 1.000] | empate |

  Leitura honesta: `ours` NÃO vence `naive_rag` com IC não sobreposto em
  nenhuma categoria; é empate em todas, com `ours` nominalmente à frente em
  guide/pdf e atrás em params_table (todos com ICs largos — n pequeno). Sinais
  honestos para o humano: `ours` emite precisão de citação 0.000 em todos os
  orçamentos (não conta como falha por §10.4, mas é relevante para um produto
  cuja tese é evidência rastreável); e `ours_no_rerank`=0.805 supera `ours`=0.732
  no 4000 (remover o reranker eleva o sucesso, ICs ainda sobrepostos). As 11
  falhas (`correct=0`) do `ours` no 4000 são diagnosticadas como
  retrieval/answer. A decisão (seguir/ajustar/parar) é do humano.
- **Custo real por provedor/estratégia.** Jina (reranker): 133 chamadas reais /
  **72 chamadas evitadas pelo cache**; cota free (bem abaixo dos 10M tokens).
  Embeddings OpenAI (`text-embedding-3-small`): 60 chamadas batched / 13381
  servidas do cache (poucos centavos). Respondedor OpenAI (`gpt-6.1-sol`) + juiz
  Anthropic (`claude-sonnet-5-5`): NÃO cacheáveis (o contexto muda por
  estratégia/orçamento), gasto total do run de 1107 linhas na ordem autorizada
  de US$3–6. Custo por tarefa concluída no orçamento 4000: **`ours` US$ 0.005313
  vs `naive_rag` US$ 0.008935** (o `ours` entrega o mesmo sucesso com ~40% menos
  custo/concluída, usando ~metade dos tokens de contexto). Esta iteração de
  revisão NÃO re-rodou o benchmark pago: regenerou os artefatos a partir das
  linhas brutas já produzidas e recomputou os probes de diagnóstico servidos
  ESTRITAMENTE do cache (sem rede, sem novo gasto).
- **Como reproduzir.** Os parâmetros exatos do run estão em
  `bench/results/m8-stage2-2026-10-13/config.json` (orçamentos 2000/4000/8000,
  parâmetros congelados `selection_lambda=0.3`/`min_value=0.0`/
  `coverage_low=0.6137`/`coverage_high=0.7746`/`rrf_k=60`, `seed=1234567`,
  `n_boot=1000`, modelos answerer/judge, `git_commit=0e41598`, versões de
  pacotes). Com a infra no ar e as chaves no `.env`, o comando da seção "Como
  testar o M8 Etapa 2 manualmente" abaixo reproduz os quatro artefatos a partir
  desse config (reusa o cache persistente `bench/.cache/`, sem re-pagar pares já
  vistos).

### M8 ETAPA 2 — resultado do gate (2026-10-13, árvore em `0e41598`)

Gate executado na árvore com o código da Etapa 2 commitado (`0e41598`),
Windows/PowerShell:

```
uv run ruff check .            -> All checks passed!
uv run ruff format --check .   -> 170 files already formatted
uv run mypy app                -> Success: no issues found in 56 source files
uv run pytest -m "not integration and not browser"
                               -> 450 passed, 36 deselected, 37 snapshots passed
```

Os 450 são os 440 da Etapa 1 mais os 10 novos testes OFFLINE em
`tests/unit/bench/test_run_sweep.py` (injeção de recuperação, varredura +
coluna `budget`, determinismo módulo tempo, o classificador de falhas incluindo
a correção da revisão — correto-mas-sem-citação NÃO é falha —, a seção de sinal
de citação, e `config.json` com os parâmetros congelados). Nenhum teste foi
desativado, pulado ou afrouxado. `git diff app/config.py` vazio.

### Como testar o M8 Etapa 2 manualmente

Gate rápido (sem serviços): `uv run ruff check . ; uv run ruff format --check .
; uv run mypy app ; uv run pytest -m "not integration and not browser"` — tudo
verde (450 passam).

Execução real (humano, gasta chaves): subir a infra
`docker compose up -d --wait postgres seaweedfs ; docker compose up s3-init ;
uv run alembic upgrade head` (head `0003_chunks`), confirmar as 4 chaves do
`.env` sem ecoar valores, e rodar
`uv run python -m bench.sweep --strategies raw_html,naive_text,naive_rag,ours,ours_no_vector,ours_no_lexical,ours_no_rerank,ours_fixed_chunks,ours_truncate --split test --budgets 2000,4000,8000 --real --out-name m8-stage2-2026-10-13`.
Reusa o cache persistente `bench/.cache/`; firecrawl é pulado (sem chave).

### M8 ETAPA 1 — correção da cobertura (revisão) e estado

1. **Commit da calibração.** A Etapa 1 foi commitada e publicada como `a34d52c`
   (cumprindo a regra de honestidade do M8: parâmetros congelados e commitados
   ANTES do split test). A correção da cobertura desta revisão (score absoluto +
   limiares pelo máximo-por-tarefa) está sobre `a34d52c` como um novo conjunto de
   mudanças — pode ser commitada com `M8 (stage 1 fix): ...`.
2. **Proveniência dos limiares — RESOLVIDA.** O bug: `assemble._coverage` olha o
   MÁXIMO de rerank por tarefa, mas o `JinaRerankProvider` normalizava min-max por
   chamada, então esse máximo era sempre 1.0 e os limiares (derivados antes da
   distribuição pooled do top-30) comparavam uma grandeza diferente. Corrigido: o
   reranker devolve o score ABSOLUTO; a normalização para a SELEÇÃO foi movida
   para `rerank_candidates` (seleção inalterada). O cache de rerank antigo
   guardava scores normalizados e foi perdido no diagnóstico, então foi
   reconstruído chamando a Jina real no dev com o provider de score ABSOLUTO
   (embeddings do cache intacto → ZERO OpenAI), e `bench.calibrate
   --coverage-only --real` recalculou os limiares do p33/p66 do máximo ABSOLUTO
   por tarefa — a MESMA grandeza de `_coverage`: **0.6137 / 0.7746** (96 tarefas),
   reproduzíveis a partir de
   `bench/results/coverage_20261012T193038_322186/coverage_scores.csv` (sem Jina).
   As 96 tarefas dev se dividem em 32/31/33 (insufficient/partial/sufficient, via
   `_coverage` com os limiares arredondados de config). O `CachingRerankProvider`
   agora persiste o score ABSOLUTO (documentado), evitando a recorrência do bug.

---

Marco anterior: M7 (Busca, seleção e endpoint `assemble`) — CONCLUÍDO. Fecha o
caminho público do produto: `POST /v1/context/assemble` (SPEC §6/§8) resolve 1–20
URLs (reusa o resolve do M5 + cache 24h), indexa a versão atual (M6), roda o
núcleo de retrieve/select e devolve um pacote de contexto pronto para agente —
fragmentos com proveniência completa, evidência literal, status por fonte,
contagem de tokens e uma heurística de cobertura. Sem LLM em nenhum ponto do
caminho de extração/seleção (CLAUDE.md); nenhum provider real é tocado em teste
(embeddings via `FakeEmbeddingProvider`, rerank via `FakeRerankProvider`).
Entregue em três features:

- **FEAT-001 — Reranker (Jina).** `reranker_api_key`/`reranker_model` em
  `app/config.py` (espelhados no `.env.example`; `.env` real intocado), a
  Protocol `RerankProvider` (`app/providers/rerank/base.py`), o
  `FakeRerankProvider` determinístico (Jaccard de tokens, usado em TODOS os
  testes) e o `JinaRerankProvider` real (httpx próprio, Bearer, normalização
  min-max para [0,1], `MissingAPIKey` sem chave — nunca em testes).
- **FEAT-002 — Núcleo de retrieve/select (SPEC §6).** `app/retrieve/`: `rrf.py`
  (fusão RRF pura e genérica, k=60, desempate por chave), `select.py` (seleção
  gulosa sob orçamento `(V_i - λ·max_sim)/sqrt(t_i)`, `cosine` seguro),
  `rerank.py` (top-30, valor = rerank clampado ou RRF normalizado quando o
  rerank está off), `lexical.py` (`ts_rank_cd` sobre `tsv` 'simple', top-50,
  restrito às versões), `vector.py` (cosseno/HNSW do pgvector, top-50) e
  `assemble.py` (orquestrador: resolve+indexa por URL independentemente, funde,
  reranqueia, seleciona, monta fragmentos com evidência literal). Math pura
  unit-testada; busca DB testada em integração.
- **FEAT-003 — Superfície pública + benchmark + docs.** O handler
  `app/api/assemble.py` (sync `def`, seams `Depends` incl. embeddings+reranker,
  flags de ablação só fora de produção, falha parcial = 200, todas falham =
  422), o mount em `app/main.py`, os erros `AllSourcesFailed`(422)/
  `AblationNotAllowed`(400) em `app/api/errors.py`, o runner `ours` + as
  ablações (`bench/runners/ours.py`, registradas em `bench/runners/base.py`) e
  os testes (`tests/integration/test_assemble_api.py`,
  `tests/unit/bench/test_ours_runner.py`).

M1 (Fetcher seguro), M3 (IR e extração de HTML), M4 (navegador + PDF), M5
(Persistência e endpoint `resolve`) e M6 (Chunking e indexação) permanecem
concluídos; M1 e M4 foram aprovados nas revisões de segurança independentes
(APPROVED, sem findings HIGH/MEDIUM). M2 (Corpus congelado, harness e baselines)
corre em paralelo e depende de execução humana para o snapshot/draft reais.

### Como testar o M7 manualmente

Gate rápido (sem serviços): `uv run ruff check . ; uv run ruff format --check . ;
uv run mypy app ; uv run pytest -m "not browser and not integration"` — inclui os
testes do runner `ours` e o `tests/integration/test_openapi.py` (não marcado
`integration`), que agora mostra a rota `/v1/context/assemble`.

Integração (Postgres + pgvector + SeaweedFS via Docker): `docker compose up -d
--wait postgres seaweedfs ; docker compose up s3-init ; uv run alembic upgrade
head ; uv run pytest -m integration -q` — `tests/integration/test_assemble_api.py`
prova o 200 feliz (fragmentos com proveniência+scores), a falha parcial em 200
(fonte morta em `sources[]`+`warnings`), o 422 all-fail e a rejeição de ablação
com `ENV=production`.

Benchmark e2e offline (FAKE providers): aponte
`BENCH_CORPUS_DIR`/`BENCH_TASKS_PATH`/`BENCH_RESULTS_ROOT` para
`tests/fixtures/bench` e rode `uv run python -m bench.run --strategies
naive_rag,ours --split dev` — gera `results.csv`/`report.md`/`config.json`, sem
rede.

### Dívidas / notas do M7

- A `coverage` (`insufficient`/`partial`/`sufficient`) é uma HEURÍSTICA sobre o
  maior score de rerank — não é garantia de correção. Os limiares eram
  provisórios no M7 (`0.2`/`0.5`); foram CALIBRADOS e congelados no M8 Etapa 1
  (`coverage_low=0.6137`/`coverage_high=0.7746`, do p33/p66 do máximo absoluto
  por tarefa no dev — ver a seção do M8 acima).
- Com os providers FAKE sobre a fixture pequena, os scores do reranker Jaccard
  ficam quase sempre abaixo de `min_value=0.05`, então o caminho guloso (`ours`)
  tende a selecionar pouco ou nada; a ablação `ours_truncate` (sem o corte por
  `min_value`) é a que prova a montagem de contexto fim a fim offline. Isso é
  esperado do fake, não do pipeline — a execução real com Jina produz scores
  informativos.
- Os providers reais (`JinaRerankProvider`, `OpenAIEmbeddingProvider`) nunca são
  exercidos em teste (zero rede); só a execução humana com chave os usa.

### Como testar o M6 manualmente

Gate rápido (sem serviços): `uv run ruff check . && uv run ruff format --check .
&& uv run mypy app && uv run pytest -m "not browser and not integration"` — o
teste de integração é corretamente excluído aqui.

Integração (Postgres + pgvector via Docker): `make up && make migrate` aplica
até a revisão `0003_chunks`; o round-trip `uv run alembic downgrade -1 && uv run
alembic upgrade head` recria a tabela limpa; `uv run pytest
tests/integration/test_indexer.py -m integration` prova a idempotência (mesmo nº
de chunks em duas reindexações da mesma `version_id`), `block_ids` não-vazio e
`tsv` preenchido pelo banco.

### Dívidas / notas do M6

- O M6 só indexa: a busca lexical (GIN/`tsv`) e vetorial (HNSW/`embedding`) é
  consumida pelo M7 (`assemble`/retrieve). Os índices já existem para o M7 usar.
- O adaptador real `OpenAIEmbeddingProvider` nunca é exercido em testes (zero
  rede); só a execução humana com chave o usa, em lote com retry/backoff.

## Concluídos

### M7 — Busca, seleção e endpoint `assemble`

Caminho público do produto (SPEC §6/§8): fusão de busca lexical + vetorial,
seleção gulosa sob orçamento com penalização de diversidade, reranker Jina, e o
endpoint `POST /v1/context/assemble` que entrega contexto pronto para agente com
proveniência e evidência literal. Determinístico e sem LLM. M7 NÃO adiciona
migração (consome a tabela `chunks` + índices GIN/HNSW do 0003 do M6). Entregue
em três features:

- **FEAT-001 — Reranker (Jina).** `app/config.py` ganhou `reranker_api_key` e
  `reranker_model="jina-reranker-v2-base-multilingual"` (espelhados em
  `.env.example` como `RERANKER_API_KEY=` vazio e `RERANKER_MODEL=`; o `.env`
  real NUNCA foi tocado). `app/providers/rerank/base.py`: `@runtime_checkable
  Protocol RerankProvider` (`model: str`; `rerank(query, documents) ->
  list[float]`, um score em [0,1] por documento NA ORDEM DE ENTRADA).
  `fake.py`: `FakeRerankProvider` determinístico por sobreposição Jaccard de
  tokens (disjunto → 0.0, idêntico → 1.0), zero rede, usado em TODOS os testes.
  `jina.py`: `JinaRerankProvider` real (httpx importado tardiamente, `POST
  https://api.jina.ai/v1/rerank`, Bearer, corpo `{model,query,documents,
  top_n}`, reordena para a entrada por `index`, min-max para [0,1], `MissingAPIKey`
  reusada de `app/providers/llm/openai.py` sem chave) — nunca exercido em teste.
- **FEAT-002 — Núcleo de retrieve/select.** `app/retrieve/rrf.py::rrf_fuse`
  (soma `1/(k+rank)` 1-based, ordena por score desc e chave asc; k=`rrf_k`=60).
  `app/retrieve/select.py`: `@dataclass(frozen=True) Candidate` + `select`
  (passo guloso maximizando `(V_i - λ·max_sim)/sqrt(t_i)`, pula o que não cabe,
  para quando nada cabe ou o melhor valor ajustado < `min_value`; saída ordenada
  por `(document_id, ord)`) + `cosine` seguro para norma zero.
  `app/retrieve/rerank.py::rerank_candidates` (top-30; valor = rerank clampado
  [0,1] com score cru preservado, ou RRF min-max normalizado quando
  `use_rerank=False`). `app/retrieve/lexical.py::lexical_search`
  (`ts_rank_cd(tsv, websearch_to_tsquery('simple', query))` desc, filtrado por
  `tsv @@ query` E `version_id IN (...)`, top-50, desempate `(version_id, ord)`).
  `app/retrieve/vector.py::vector_search` (`embedding.cosine_distance(qvec)` asc,
  HNSW, mesma restrição + desempate, top-50). `app/retrieve/assemble.py::
  assemble_context` (resolve+indexa cada URL independentemente, coleta as
  versões atuais, busca lexical+vetorial ablation-aware, funde por chave
  `(document_id, ord)`, reranqueia o top-30, seleciona sob `max_tokens` — gulosa
  ou `truncate` por valor — e monta `Fragment`s com proveniência completa, scores
  e `Evidence` literal <=300 chars; `coverage` pela heurística do maior rerank).
  Testes: `tests/unit/retrieve/` (RRF à mão, orçamento/duplicatas da seleção,
  propriedade `hypothesis` de orçamento, quote literal) e
  `tests/integration/test_retrieve.py` (busca DB real + `FakeEmbeddingProvider`).
- **FEAT-003 — Superfície pública + benchmark + docs.** `app/api/schemas.py`
  ganhou o contrato §8 do assemble (`AssembleRequest` com `urls` 1–20,
  `max_tokens` 256–32000, flags de ablação; `AssembleResponse` com
  `FragmentModel`/`FragmentScoresModel`/`EvidenceItem`/`SourceStatusModel`/
  `TokenUsage`, `coverage`, `warnings`, `trust`). `app/api/assemble.py`: handler
  `def` SÍNCRONO (threadpool), seams `Depends` iguais aos do documents MAIS
  `provide_embeddings` (OpenAI real por padrão, fake em teste) e
  `provide_reranker` (Jina real por padrão, fake em teste); rejeita ablação com
  `ENV=production` (`AblationNotAllowed` 400), falha de UMA URL permanece 200
  (status em `sources[]` + `warnings`), TODAS falham → `AllSourcesFailed` (422);
  mapeia `AssembleResult` → `AssembleResponse`. `app/api/errors.py` ganhou
  `AllSourcesFailed`(code `all_sources_failed`, 422) e `AblationNotAllowed`(code
  `ablation_not_allowed`, 400). `app/main.py` monta o router de assemble igual ao
  de documents (estende `app.router.routes` e seta
  `dependency_overrides_provider`+reconstrói o handler em cada `APIRoute`).
  `bench/runners/ours.py`: `OursRunner` roda o pipeline em memória sobre o
  snapshot congelado (sem Postgres) e as ablações `ours_no_vector`/
  `ours_no_lexical`/`ours_no_rerank`/`ours_fixed_chunks`/`ours_truncate`, todas
  registradas em `bench/runners/base.py`. Testes:
  `tests/integration/test_assemble_api.py` (200 feliz com proveniência+scores,
  falha parcial 200, all-fail 422, ablação rejeitada em produção) e
  `tests/unit/bench/test_ours_runner.py` (cada runner dentro do orçamento,
  determinístico, offline). Decisões do M7 (httpx de provider confiável, forma
  do Jina, desempate RRF, heurísticas de valor/cobertura, reuso de embeddings no
  `max_sim`, código `all_sources_failed`) em DECISIONS.md (2026-10-13).

### M6 — Chunking e indexação

Transformação determinística da IR (Document/Block) em chunks indexados para
busca lexical e vetorial (SPEC §5, §7), sem LLM e sem rede no caminho de
indexação. Entregue em duas features:

- **FEAT-001 — Fundação + chunking (sem banco).** Dependência `pgvector`
  (PostgreSQL License, permissiva) em `[project].dependencies` e `hypothesis`
  (BSD) no grupo `dev`; `uv.lock` regenerado. `app/config.py`: `embedding_dim`
  1024→1536, novos `embedding_model="text-embedding-3-small"` e
  `embeddings_api_key` (fallback para `bench_answerer_api_key`), espelhados no
  `.env.example`. `app/providers/embeddings/`: `base.py` (`@runtime_checkable
  Protocol EmbeddingProvider` com `model`/`dim`/`embed`), `fake.py`
  (`FakeEmbeddingProvider` determinístico de `sha256` normalizado L2, zero rede,
  usado em TODOS os testes) e `openai.py` (`OpenAIEmbeddingProvider` real, SDK
  lazy, batch + retry/backoff, `MissingAPIKey` sem chave — nunca usado em
  testes). `app/index/chunker.py`: `@dataclass(frozen=True) Chunk` + o
  `chunk_document` determinístico (seções por `heading_path`, janela 200–500 /
  máx. 800 tokens medidos sobre o `text` com prefixo, corte de `table`/`code`
  isolado >800 por linhas/linhas-em-branco repetindo headers, prosa >800 como
  chunk único, `block_ids` não-vazio). Testes unitários incl. a propriedade por
  `hypothesis` (nenhum chunk >800; table/code <800 nunca dividido).
- **FEAT-002 — Persistência + indexação (banco).** `app/store/models.py`:
  modelo `Chunk` (`chunks`) com `id` autoincremento, `version_id`
  FK→`document_versions.id` (indexado), `block_ids`/`heading_path` text[],
  `text`/`md`/`tokens`/`ord`, `embedding` `Vector(1536)` e a coluna `tsv`
  GERADA pelo banco mapeada read-only (`Computed(..., persisted=True)`). Migração
  `0003_chunks` (down_revision `0002_sources_document_versions`): cria a tabela,
  a coluna `tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', text))
  STORED`, a coluna `embedding vector(1536)` (extensão `vector` já de `0001`), o
  índice GIN sobre `tsv` e o índice HNSW `vector_cosine_ops` sobre `embedding`;
  `downgrade()` dropa índices e tabela. `app/index/indexer.py::index_version`
  (idempotente delete-then-insert por `version_id`, embeddings em lote, `tsv`
  nunca escrito pelo app, caso de zero chunks tratado). Teste de integração
  `tests/integration/test_indexer.py` (Postgres real + `FakeEmbeddingProvider`):
  nº de chunks igual em duas reindexações, `block_ids` não-vazio, `tsv`
  preenchido pelo banco. O `_truncate` do harness limpa `chunks` antes de
  `document_versions`. Ambiguidades da SPEC §5 e a estratégia delete-then-insert
  registradas em DECISIONS.md (2026-10-12).


### M2 FASE A — Corpus, harness e baselines (provado com LLM FAKE, sem rede)

Marco M2 é a RÉGUA DE MEDIÇÃO do produto (SPEC §10). A FASE A entrega TODO o
código do harness e o prova com um provedor LLM fake determinístico, SEM rede e
SEM gastar chaves. O snapshot real e o draft real (que acessam a web e gastam
tokens) são EXECUÇÃO HUMANA pós-workflow — não rodam no CI nem nos testes.
Entregue em três features:

- **FEAT-001 — Fundação (deps + interface do provider LLM).** Grupo `bench` no
  `pyproject.toml` (`trafilatura>=2`, `rank-bm25`, `openai`, `anthropic`,
  `pyyaml`) + `uv.lock`; `[tool.uv].default-groups=["dev","bench"]`.
  `app/providers/llm/`: `base.py` (Protocol `LLMProvider.complete(prompt,*,
  temperature=0.0,response_format)` + `LLMResult`), `fake.py` determinístico
  (saída derivada de `sha256(model|format|prompt)`, JSON válido no shape de
  answerer/judge, zero rede), `openai.py`/`anthropic.py` reais (SDK lazy, leem
  `BENCH_ANSWERER_API_KEY`/`BENCH_JUDGE_API_KEY`, erro claro sem chave).
  Settings + `.env.example` ganharam as duas chaves `BENCH_*` vazias.
- **FEAT-002 — Corpus e tarefas.** `bench/corpus_store.py` (modelo Pydantic do
  `manifest.json` + `CorpusStore` read-only por URL), `bench/snapshot.py`
  (reusa `app.fetch.client.fetch`/`render_url`, injetáveis p/ fake; pula URLs
  robots-disallowed sem abortar), `bench/tasks_schema.py` (`Task` da SPEC §10.2
  + `load/dump_tasks` round-trip estável), `bench/draft_tasks.py` (rascunha com
  o answerer, `author:ai_draft`/`validated:false`, quote literal do snapshot,
  mapeia rótulos ricos → 5 categorias canônicas) e `bench/review_tasks.py` (CLI
  de revisão com I/O injetável). Nada acessa a rede nos testes.
- **FEAT-003 — Harness de medição + prova dos 4 critérios de aceite.** Runners
  baseline com interface comum (`bench/runners/base.py`:
  `ContextResult`/`Runner`/`get_runner`, truncagem determinística por tokens
  via `app.index.tokens`): `raw_html` (HTML bruto concatenado), `naive_text`
  (`trafilatura.extract` texto), `naive_rag` (texto → chunks fixos de 400
  tokens → BM25 `rank_bm25` top-k até o orçamento) e `firecrawl`
  (auto-PULADO sem `FIRECRAWL_API_KEY`; não registrado). `ours`/`ours_*` NÃO
  implementados (M7). `bench/answer.py` (respondedor temperatura 0, PROMPT
  FIXO, contrato JSON `{answer,citations}`, `INSUFFICIENT` quando sem
  evidência). `bench/grade.py` (`exact`/`contains_all` determinísticos
  case-insensitive, `judge` via provider juiz com rubrica fixa; precisão de
  citação por `difflib.SequenceMatcher.ratio()>=0.9` no doc citado E
  sobreposição com a `expected_evidence`; INSUFFICIENT como acerto quando
  `expected==['INSUFFICIENT']`). `bench/report.py` (agrega por estratégia e por
  categoria: taxa de sucesso + IC95% por bootstrap `n_boot=1000` com semente
  estável derivada de `strategy+category`, tokens médios de contexto, custo/
  tarefa e custo/tarefa concluída de `bench/prices.yaml`, precisão de citação,
  taxa de INSUFFICIENT; IGNORA `validated:false` e INFORMA quantas; latência
  p50/p95 só no `results.csv`, fora do `report.md` p/ determinismo).
  `bench/run.py` (CLI argparse, fia store+runners+answer+grade+report, provider
  FAKE por padrão, `--real` só p/ humano). Fixtures de aceite em
  `tests/fixtures/bench/` (3 HTML pequenos + `manifest.json` + `tasks.yaml`
  validadas cobrindo exact/contains_all/judge/1 INSUFFICIENT/1 validated:false)
  e testes e2e provando: (a) os 3 arquivos são gerados em
  `bench/results/<ts>/`; (b) duas execuções com o fake dão `results.csv`/
  `report.md` idênticos ignorando só o timestamp do diretório e a coluna
  `latency_ms`; (c) o grader cobre os 5 casos; (d) o relatório ignora
  `validated:false` e informa a contagem.

Pendências e postura de teste (M2 FASE A):

- O **snapshot real** (`bench/snapshot.py`) e o **draft real**
  (`bench/draft_tasks.py` com provider real) são EXECUÇÃO HUMANA: acessam a web
  e gastam chaves `BENCH_*`. NÃO são rodados pelo workflow nem pelo CI. Fluxo
  humano: `uv sync` → preencher `.env` → rodar o snapshot → draftar tarefas →
  revisar (`bench/review_tasks.py`) → rodar `python -m bench.run --real`.
- `bench/corpus/` e `bench/results/` são gitignored (gerados). O corpus de
  aceite vive em `tests/fixtures/bench/` (versionado, feito à mão, sem rede).
- Como testar (sem rede): `uv run pytest tests/unit/bench -q` e o gate completo
  (`ruff check .`, `ruff format --check .`, `mypy app`, `pytest -m "not
  integration and not browser"`). Para gerar um relatório sobre a fixture sem a
  web, aponte o CLI à fixture via `BENCH_CORPUS_DIR`/`BENCH_TASKS_PATH`/
  `BENCH_RESULTS_ROOT` e rode `python -m bench.run --strategies raw_html,
  naive_text,naive_rag --split dev`.

### M5 — Persistência e endpoint `resolve`

Primeiro endpoint público COM cache (SPEC §5, §7, §8), sem LLM no caminho de
extração/seleção e com toda saída de rede passando pelo guard do M1. Entregue em
três features:

- **FEAT-001 — Fundação de persistência.** Dependência `tiktoken` (MIT) fixada
  em `pyproject.toml` (`tiktoken>=0.7,<1`) + `uv.lock`; campo de config
  `resolve_cache_ttl_seconds=86400` (SPEC §5) em `app/config.py` espelhado no
  `.env.example` (`RESOLVE_CACHE_TTL_SECONDS`). `app/index/tokens.py::count_tokens`
  carrega `cl100k_base` uma vez (`@lru_cache`), conta `len(enc.encode(text))`,
  texto vazio → 0, sem rede. `app/store/models.py`: modelos SQLAlchemy 2 tipados
  (`Base`, `Source`, `DocumentVersion`) com a FK circular
  `sources.current_version_id ↔ document_versions.id` quebrada por `use_alter`,
  unique `(source_id, content_hash)` para dedup no nível do esquema, `warnings`
  em JSONB e `status` textual default `'ready'`. Migração Alembic
  `0002_sources_document_versions` (down_revision `0001_enable_vector`) cria as
  duas tabelas e a FK circular via `ALTER TABLE`; `migrations/env.py` aponta
  `target_metadata = Base.metadata`. `app/store/blobs.py::BlobStore` grava/lê
  `raw/{version_id}` e `ir/{version_id}.json` (boto3, overwrite idempotente).
  `app/store/repo.py`: `make_engine`/`make_session_factory` e `DocumentRepo`
  (get_or_create_source, get_current_version, get_version, get_version_by_hash,
  create_version que seta `current_version_id`, touch_fetched_at), com
  `domain_from_url`.
- **FEAT-002 — API + máquina de cache + handler de erros.** `app/api/schemas.py`
  com `ResolveRequest`/`Stats`/`ResolveResponse` EXATOS da SPEC §8 (+`examples`
  no OpenAPI) e `ErrorBody`. `app/api/errors.py`: `fetch_error_handler`
  serializa qualquer `FetchError` como `{"error":{code,message,details}}` com
  `status=exc.http_status` (zero casing por erro), `generic_exception_handler`
  devolve 500 sem vazar stack, e `NotFound`(404) para GET de documento
  inexistente. `app/api/documents.py`: handlers `def` (threadpool reusa o stack
  síncrono auditado), com os selos injetáveis via `Depends` (session factory,
  BlobStore, RobotsChecker, resolver, transport, relógio, settings). Fluxo do
  `POST /v1/documents/resolve`: validar URL pelo guard → source/versão atual →
  CACHE HIT (versão fresca, sem force_refresh) serve do S3 com `cache_hit=true`
  e ZERO rede → senão robots → GET pelo cliente auditado → 304 só atualiza
  `fetched_at` → senão `content_hash`: igual ao atual deduplica (sem nova
  versão), diferente extrai (PDF→`extract_pdf`; HTML→decodifica, `extract_html`,
  `should_render`→render+re-extrai) → `_ensure_not_empty` garante a regra de
  falha explícita → persiste blobs + versão → monta o envelope com
  `markdown`/`blocks` conforme `formats`. `GET /v1/documents/{id}` lê a versão
  (query `version_id` ou a atual) do storage com `cache_hit=true`, sem refetch.
- **FEAT-003 — Prova de aceite: testes de integração + docs.** `tests/integration/`
  (marcado `integration`) com Postgres + SeaweedFS reais e HTTP SEMPRE mockado
  por um `httpx.MockTransport` instrumentado (contador de chamadas + script por
  path, servindo `/robots.txt`), um `FakeResolver` (host→IP público), um
  `BlobStore` real e uma session factory ligada ao Postgres de teste; migrações
  aplicadas no setup, tabelas truncadas entre casos. Cobre: primeira busca (miss,
  rows + blobs criados), cache hit com **contador do transport inalterado**
  (prova de zero rede), 304 (sem nova versão), conteúdo alterado (nova versão,
  `current_version_id` atualizado), mesmo hash (sem nova versão), stats
  (`tokens_raw_html`/`tokens_markdown` > 0), GET do storage sem refetch e todos
  os erros §8 que o resolve emite (invalid_url 400, blocked_destination 400,
  robots_disallowed 403, too_large 413, unsupported_content_type 415,
  fetch_failed 502, timeout 504, extraction_empty 422), cada um com o envelope
  `{"error":{code,message,details}}` e o HTTP exato. `tests/integration/
  test_openapi.py` NÃO é marcado `integration` (não toca DB) — roda no gate
  rápido e afirma os dois paths e os `examples` nos schemas. Correção de wiring
  (ver DECISIONS 2026-10-11 addendum): a extensão direta das rotas em
  `create_app` não definia o `dependency_overrides_provider`, então
  `app.dependency_overrides` era ignorado silenciosamente nos endpoints do
  resolve; `create_app` passou a setar o provider e reconstruir o handler em
  cache de cada `APIRoute`, mantendo as rotas enumeráveis em `app.routes` e
  fazendo os selos injetáveis valerem (sem mudança de comportamento em runtime).

### M4 — Renderização por navegador e extração de PDF

Renderização sob demanda por navegador e extração determinística de PDF digital
(SPEC §3.3, §3.4, §4, §8), sem LLM e sem rede fora do guard. Entregue em três
features:

- **FEAT-001 — Fundação + decisão de renderização.** Dependências de runtime
  Playwright (Apache-2.0) e pdfplumber (MIT, puxa pdfminer.six MIT) e de dev
  reportlab (BSD) — todas MIT/BSD/Apache-2.0, nenhuma GPL/AGPL, sem PyMuPDF.
  Marcador de teste `browser` registrado no pyproject.toml; campo de config
  `render_networkidle_timeout_ms=15000` (cap do SPEC §3.4) espelhado no
  `.env.example`, com `render_min_text_chars=500` mantido. `app/fetch/detect.py`
  ganhou a decisão de renderização PURA `should_render(*, main_text_len,
  html_text, render='auto', settings=None)` (SPEC §3.3: `never`→False,
  `always`→True, `auto`→True só se texto principal < limiar E há sinais de SPA)
  e o helper `_has_spa_signals` (contêiner-raiz vazio, `__NEXT_DATA__`/
  `window.__NUXT__`, `<noscript>` pedindo JS) — por string, sem rede nem
  navegador. `parse_content_type`/`detect_charset` ficaram inalterados.
- **FEAT-002 — Renderização por navegador (núcleo de segurança SSRF).**
  `app/fetch/render.py::render_url(url, *, resolver=None, settings=None,
  browser_launch_args=None) -> RenderResult(final_url, html, status)` usa a API
  SYNC do Playwright, com contexto efêmero por chamada (`accept_downloads=False`)
  e Chromium sempre fechado em try/finally. ANTES de navegar registra DOIS
  handlers de interceptação cobrindo os dois seams de saída: `page.route("**/*")`
  para HTTP(S) e `page.route_web_socket("**/*")` para WebSocket (que esta build
  do Playwright não roteia por `page.route`). Cada requisição interceptada passa
  pelo guard `app.fetch.guard.validate_url`: `image`/`media`/`font` são abortados
  por economia; qualquer `FetchError` ou exceção inesperada aborta (falha
  fechada); só um sucesso do guard chama `route.continue_()`/`connect_to_server`.
  A URL de entrada também é validada pelo guard ANTES de lançar o Chromium (ponto
  de entrada ilegal falha com o erro tipado do guard, sem browser). O guard é a
  ÚNICA autoridade de destino — nenhuma política de IP/porta é duplicada aqui.
  Espera: `goto(wait_until="domcontentloaded")` + `wait_for_load_state(
  "networkidle", timeout=render_networkidle_timeout_ms)`, com timeout de
  networkidle NÃO fatal (captura `page.content()` mesmo assim). O chamador do M5
  marca `SourceInfo.render_mode='browser'` e anexa o warning
  `rendered_with_browser`. httpx NÃO é importado em `render.py`.
- **FEAT-003 — Extração PDF→IR + passo de CI dedicado.**
  `app/extract/pdf.py::extract_pdf(raw, source, *, settings=None) -> Document`
  abre o PDF com pdfplumber, itera páginas (1-based, `page` em TODO bloco e não
  decrescente), infere títulos pelos 2 maiores tamanhos de fonte acima do corpo
  (nível 1/2), extrai tabelas via find_tables/extract (header só quando a 1ª
  linha está cheia e há ≥1 linha de corpo, senão `headers=[]` + warning
  `table_no_header` pareado ao bloco), remove cabeçalhos/rodapés repetidos em
  >50% das páginas (bandas de 15% topo/base, token de número de página
  descartado), junta linhas monoespaçadas consecutivas em bloco `code` e emite
  `low_text_content` quando o texto fica abaixo do limiar. PDF sem texto levanta
  `ExtractionEmpty` (HTTP 422, OCR fora de escopo), novo erro tipado em
  `app/fetch/errors.py` (SPEC §8). Fixtures geradas por reportlab em
  `tests/fixtures/pdf/` (3 PDFs técnicos + 1 sem texto, documentadas em
  `SOURCES.md`). O CI mantém o gate principal rápido (`-m "not integration and
  not browser"`) e tem um passo dedicado que roda `uv run playwright install
  --with-deps chromium` e depois `uv run pytest -m "browser"`.

Teste central de SSRF (o que torna a prova não vacuosa): a suíte de navegador
usa `--host-resolver-rules` do Chromium para mapear o host BLOQUEADO
(`169.254.169.254` / `10.0.0.1`) ao servidor contador em loopback — ou seja, o
contador É alcançável pelo Chromium, e a única coisa que mantém seu contador em
ZERO é o guard abortando a requisição em `page.route` ANTES de o Chromium
conectar. O guard é usado verbatim (não é monkeypatched); páginas de fixture são
servidas sob hostnames públicos fictícios que o `FakeResolver` mapeia para um IP
público para o guard APROVAR, e o `--host-resolver-rules` redireciona a conexão
real para a porta de loopback. Resultado: 9 testes de navegador verdes, com o
caso central (parametrizado em `169.254.169.254` e `10.0.0.1`) provando contador
== 0.

Revisões independentes (rodada em 2026): revisão técnica APPROVED (gate verde,
critérios de aceite 1–6 atendidos, httpx confinado a `app/fetch/client.py`, sem
PyMuPDF) e red-team de segurança SSRF APPROVED (interceptação registrada antes de
navegar, falha fechada, URL de entrada guardada, teste central significativo). O
único resíduo é a janela de rebinding DNS DENTRO do navegador (o Chromium faz a
própria resolução e não expõe seam de conectar-no-IP-validado como o cliente
httpx do M1), documentado honestamente em DECISIONS.md e registrado como dívida
de M10 (restringir o egress do processo de navegador). Artefatos das revisões em
`.agents/tasks/m4-browser-pdf/`: `m4-review.json`, `m4-security-review.md`,
`m4-security-verdict.json`.

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
- `app/fetch/render.py` foi implementado no M4 (Playwright + interceptação pelo
  guard). Dívida: a janela de rebinding DNS DENTRO do navegador (entre a checagem
  do guard e a resolução/conexão PRÓPRIA do Chromium na MESMA requisição) NÃO é
  fechada como no cliente httpx do M1, porque o Chromium resolve o DNS sozinho e
  não expõe um seam para conectar no IP já validado. A mitigação é operacional e
  está registrada como dívida de M10: o processo do navegador deve rodar com
  egress restrito à internet pública e sem acesso a credenciais de nuvem (ver
  DECISIONS 2026-10-11 "M4 — janela de DNS rebinding no navegador").
- `app/extract/pdf.py` foi implementado no M4 (pdfplumber página a página,
  determinístico, sem LLM). Dívida/escopo: OCR está FORA de escopo — PDF sem
  texto extraível levanta `extraction_empty` (HTTP 422). As heurísticas (corpo
  pelos tamanhos de fonte, bandas de 15% para cabeçalho/rodapé repetido em >50%
  das páginas, monoespaçado→código) são determinísticas e validadas contra as
  fixtures reportlab; comportamento em PDFs reais de layout irregular não é
  testado. O restante de `app/extract` (ir, html, jsonld, to_markdown) foi
  implementado no M3.
- `app/store` (models, repo, blobs) e `app/index/tokens.py` foram implementados
  no M5. `app/index/chunker`/`indexer`, `app/retrieve`, `app/providers/*`,
  `app/usage` e `app/mcp_server` seguem como placeholders (docstring-only),
  implementados a partir dos marcos seguintes (M6+).
- M5 (dívida/escopo): o resolve NÃO envia headers condicionais
  `If-None-Match`/`If-Modified-Since` (o cliente auditado do M1 não os carrega);
  a economia de "conteúdo inalterado" vem do dedup por `content_hash`, e o ramo
  304 cobre transports/caches que já respondam 304 (ver DECISIONS 2026-10-11
  "M5 — requisição condicional ..."). `document_versions.status` só tem o valor
  `'ready'` no MVP (estados futuros reservados). Autenticação (Bearer) é do M9 e
  está DESLIGADA em `ENV=dev`.
- M3 (dívida/escopo): a limpeza de chrome e a seleção de conteúdo principal
  cobrem os padrões Sphinx/MkDocs/Starlight presentes nas 18 fixtures; outros
  temas podem exigir novos seletores (a postura é conservadora — na dúvida, não
  remover conteúdo, e o teste de strings proibidas guia). A estabilidade de
  `block_id` entre cópias idênticas é local às ocorrências anteriores (ver
  DECISIONS 2026-10-11 "block_id estável sob edição"). O gatilho de
  `readability_fallback` é por documento, não por bloco.
- `EMBEDDING_DIM=1024` é provisório; revisar ao escolher o provedor de
  embeddings no M6 (pode exigir migração do esquema).
- Grupo de dependências `bench` preenchido no M2 FASE A (`trafilatura>=2`,
  `rank-bm25`, `openai`, `anthropic`, `pyyaml`) e instalado por padrão
  (`[tool.uv].default-groups`).
- O alvo `make bench` chama `python -m bench.run`, implementado no M2 FASE A
  (provider FAKE por padrão; `--real` só na execução humana).
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

### Renderização por navegador e extração de PDF (M4)

Os testes de navegador exigem um Chromium real e, por isso, só rodam num passo
dedicado (não no gate rápido). Instale o Chromium uma vez antes de rodá-los:

```powershell
uv run playwright install chromium
```

No Linux/CI use `uv run playwright install --with-deps chromium`.

Decisão de renderização (pura, sem rede nem navegador):

```python
from app.fetch.detect import should_render

# auto: só renderiza com texto principal curto E sinais de SPA
print(should_render(main_text_len=120, html_text='<div id="root"></div>'))  # True
print(should_render(main_text_len=5000, html_text='<div id="root"></div>'))  # False
print(should_render(main_text_len=0, html_text="", render="always"))  # True
print(should_render(main_text_len=0, html_text="", render="never"))  # False
```

Extração de PDF (determinística, lê fixtures salvas, sem rede e sem OCR):

```python
from datetime import UTC, datetime
from app.extract.pdf import extract_pdf
from app.extract.to_markdown import to_markdown
from app.extract.ir import SourceInfo

raw = open("tests/fixtures/pdf/api_reference.pdf", "rb").read()
src = SourceInfo(
    url="https://example.test/api.pdf",
    final_url="https://example.test/api.pdf",
    content_type="application/pdf",
    fetched_at=datetime(2026, 10, 11, tzinfo=UTC),
    etag=None,
    last_modified=None,
    content_hash="0" * 64,
    render_mode="pdf",
)
doc = extract_pdf(raw, src)
print(len(doc.blocks), "blocks;", [w.code for w in doc.warnings])
print(to_markdown(doc)[:500])
# PDF sem texto (tests/fixtures/pdf/textless.pdf) levanta ExtractionEmpty (422).
```

Para regenerar as fixtures de PDF (geradas por reportlab, versionadas):
`uv run python tests/fixtures/_generate_pdf_fixtures.py`.

Gate completo do M4 (host Windows, sem Docker). Gate rápido:
`uv run ruff check . ; uv run ruff format --check . ; uv run mypy app ;
uv run pytest -m "not integration and not browser" -q` → ruff All checks passed;
format 98 files already formatted; mypy Success (48 arquivos); pytest 261 passed,
10 deselected, 37 snapshots. Passo dedicado de navegador:
`uv run playwright install chromium ; uv run pytest -m "browser" -q` → 9 passed,
262 deselected (inclui o teste central de SSRF com contador == 0 para
`169.254.169.254` e `10.0.0.1`). Rodar só a suíte de PDF:
`uv run pytest tests/unit/extract/test_pdf.py -q`; só a de navegador:
`uv run pytest -m "browser" -q`.

### Persistência e endpoint `resolve` (M5)

Pré-requisito: infra no ar (`docker compose up -d --wait postgres seaweedfs ;
docker compose up s3-init`) e migrações aplicadas (`uv run alembic upgrade head`).
Suba a API com `uv run uvicorn app.main:app --reload`.

Resolver uma URL pública (primeira busca → `cache_hit=false`; repita dentro de
24h → `cache_hit=true`, sem rede):

```powershell
curl.exe -s -X POST http://localhost:8000/v1/documents/resolve `
  -H "Content-Type: application/json" `
  -d '{\"url\":\"https://peps.python.org/pep-0008/\"}'
```

O corpo de resposta é o envelope da SPEC §8: `document_id`, `version_id`,
`source`, `metadata`, `markdown`/`blocks` (conforme `formats`), `stats`
(`blocks`, `tokens_markdown`, `tokens_raw_html`, `cache_hit`), `warnings` e
`trust="untrusted_source"`. Campos opcionais do request: `formats` (default
`["markdown","blocks"]`), `force_refresh` (default `false`, ignora o cache) e
`render` (`auto`/`never`/`always`).

Recuperar um documento já resolvido direto do storage (sem refetch,
`cache_hit=true`), usando o `document_id` devolvido acima; `version_id` é um
query param opcional (default = versão atual):

```powershell
curl.exe -s "http://localhost:8000/v1/documents/doc_xxxxxxxxxxxxxxxx"
curl.exe -s "http://localhost:8000/v1/documents/doc_xxxxxxxxxxxxxxxx?version_id=ver_yyyyyyyyyyyyyyyy&formats=markdown"
```

Erros seguem o envelope `{"error":{"code","message","details"}}` com o HTTP da
tabela §8 (ex.: `invalid_url` 400, `blocked_destination` 400, `robots_disallowed`
403, `too_large` 413, `unsupported_content_type` 415, `extraction_empty` 422,
`fetch_failed` 502, `timeout` 504; `not_found` 404 para um documento/versão
inexistente no GET). O schema (com exemplos) está em
`http://localhost:8000/openapi.json` e na UI `http://localhost:8000/docs`.

Gate completo do M5. Gate rápido (sem serviços):
`uv run ruff check . ; uv run ruff format --check . ; uv run mypy app ;
uv run pytest -m "not integration and not browser" -q` → ruff All checks passed;
format limpo; mypy Success (51 arquivos); pytest 268 passed, 29 deselected,
37 snapshots. Suíte de integração (com infra):
`docker compose up -d --wait postgres seaweedfs ; docker compose up s3-init ;
uv run alembic upgrade head ; uv run pytest -m "integration" -q` → 20 passed,
277 deselected. Rodar só a suíte do resolve:
`uv run pytest tests/integration -q` (os casos que não precisam de DB, como o de
OpenAPI, rodam mesmo sem infra).
