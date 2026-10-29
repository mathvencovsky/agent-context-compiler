# Frente A: concorrentes e produtos vizinhos (rodada 3, 2026-10-28)

Escopo: o que é novo ou mais profundo do que `docs/CONCORRENTES.md`,
`docs/PESQUISA_LABS.md` e `docs/CONTEXT_ENGINEERING.md` já registram.

**Limite de acesso desta sessão.** A rede só alcançava github.com (clone),
www.anthropic.com, claude.com e docs.claude.com. Os sites de docs dos
fornecedores (tavily, parallel, exa, jina, brave, perplexity, linkup, you.com,
deepwiki, mintlify, gitbook, cursor, openai, google, artificialanalysis,
marginlab) davam DNS bloqueado. Por isso:
- **lidos na íntegra:** os repositórios dos fornecedores (código das MCPs, SDKs,
  changelogs, docs versionadas no GitHub), o harness aberto da Exa (WebCode),
  context-bench, mcpbr, as evals da You.com e as páginas da Anthropic;
- **só por resumo de busca:** números de blogs e de páginas de preço. Cada item
  diz qual dos dois casos vale.

Cópias locais (dados não confiáveis, só para leitura):
`scratchpad da sessão: research3/A_src/`.

---

## 1. Lições acionáveis para o NINA (por impacto esperado)

### L1. Existe um benchmark público, aberto e com licença MIT, que mede exatamente a tarefa do `assemble` com 1 URL: o WebCode da Exa. O NINA deveria rodar nele.

**O que a fonte mostra.** O repositório `exa-labs/benchmarks` (MIT, lido na
íntegra: harness, graders e dados) tem a suíte WebCode, com dois conjuntos
executáveis:
- `webcode-highlights`: 250 perguntas, cada uma com `query`,
  `expected_answer`, `citation_url` e `citation_excerpt` literal. O sistema
  recebe URL + pergunta e devolve trechos (modo `extract-rag`, `num_results=1`).
  É o `assemble(question, [url], max_tokens)` do NINA.
- `webcode-rag`: 307 perguntas com `source_url` conhecido. São páginas longas e
  oficiais: gnu.org (40), w3.org (35), IETF (15), docs.python.org (11),
  docs.spring.io (10), doc.rust-lang.org (8), hexdocs, man7 e pkg.go.dev.
  Mediana do trecho-ouro: 110 caracteres. É o domínio central do NINA, em
  páginas monolíticas como o manual do gettext e a CSS Grid spec.
- O grader (`benchmarks/graders/rag.py`) mede duas coisas. Correção usa o
  prompt do SimpleQA. **Groundedness** pergunta se os trechos recuperados contêm
  todos os fatos não triviais do gabarito; o juiz cita a evidência e o resultado
  é GROUNDED, PARTIAL ou UNGROUNDED. Também mede precisão de citação e tokens por
  citação (tiktoken o200k). Os ICs são de bootstrap com 10.000 reamostragens.
- Resultados publicados (históricos, sem IC, números da própria Exa) para
  Highlights:

  | sistema | groundedness | correctness | tokens médios |
  |---|---|---|---|
  | Exa highlights (4.000 caracteres) | 94,8 | 93,2 | 696 |
  | Parallel Extract (4.000 caracteres) | 85,6 | 86,4 | 858 |
  | Claude `web_fetch_20260209` (filtragem dinâmica, Opus 5.5) | 81,5 | 85,9 | 319 |

**Por que importa para o NINA.** É o primeiro conjunto público em que NINA,
Exa, Parallel e o fetch nativo do Claude podem ser comparados na mesma tarefa
e com o mesmo grader. Os números dos concorrentes já estão publicados. O
gabarito traz o trecho literal, então dá para medir recall de evidência sem
LLM: o `citation_excerpt` normalizado está contido nos fragmentos? Isso conversa
diretamente com o `evidence` literal do formato detalhado
(`app/retrieve/assemble.py`, `Evidence`). Também responde à pergunta do
humano: o NINA está no nível de qualquer empresa?

**Proposta.**
1. Sem custo: um script em `bench/` que lê `highlights.jsonl` e `rag.jsonl`,
   chama `assemble` com orçamentos de 500, 1.000, 2.000 e 4.000 e mede o recall
   do trecho-ouro (contido ou não, após normalizar espaços e Markdown).
   Custo: fetch e indexação são locais. Embeddings da OpenAI e rerank da Jina
   custam centavos, mas tecnicamente são chamadas pagas; com os provedores fake
   dá para validar só o pipeline.
2. Rodada paga e pequena: o grader da Exa sem mudanças, com o NINA como
   `Searcher.extract()`, que é só um adaptador novo em `harness/searchers/`.
   O resultado entra em uma tabela comparável à publicada.

Nada disso toca `app/`. Cuidados: 36 das 250 URLs de Highlights são do GitHub
e várias são blogs (dev.to, medium), fora do foco do NINA. Relatar por
categoria. O corpus `contents` (fidelidade de extração) não traz o Markdown-ouro,
porque a licença não permite.

**Força da evidência:** fonte primária lida na íntegra (código, dados e
grader). Os números da tabela são da Exa e não têm IC.

### L2. Os concorrentes operam com orçamentos de 140 a 900 tokens. O NINA nunca foi medido abaixo de 2.000.

**O que as fontes mostram.**
- Na tabela acima, Exa, Parallel e Claude devolvem em média 320 a 860 tokens.
- O `read_url` da Jina com `question` tem padrão `topk=1` e
  `chunk_size=100` palavras, ou seja ~130 a 150 tokens
  (`jina-ai/MCP/src/utils/read.ts`, lido).
- O `get_code_context_exa` pede highlights com `text.maxCharacters=300`
  (`exa-mcp-server/src/tools/exaCode.ts`). O `web_fetch_exa` corta em 3.000
  caracteres por padrão.
- A Exa diz que 500 caracteres de highlights igualam a acurácia no SimpleQA dos
  primeiros 8.000 caracteres da página (16x menos tokens; blog de abril de
  2026, só resumo).
- As "Dynamic Highlights" (agosto de 2026) escolhem tokens **entre todas as URLs
  sob um orçamento comum**. A Exa diz: −95% de tokens contra a página inteira;
  +40% de eficiência e +3,8% de qualidade sobre os highlights a 12.000
  caracteres; −30% de tokens em uso agêntico. A Artificial Analysis viu 47%
  menos tokens por tarefa, mas a tarefa ficou 48% mais lenta (só resumo).
- O Parallel Extract tem `max_chars_total` (orçamento entre URLs) e
  `excerpt_settings.max_chars_per_result` (SDK oficial, lido).

**Por que importa.** O padrão do NINA é `max_tokens=4000`
(`app/mcp_server/server.py`) e o M8 só varreu 2.000, 4.000 e 8.000 (PROGRESS,
`bench.sweep --budgets 2000,4000,8000`). A tese "metade do contexto dos
baselines de RAG" pode ser fraca contra produtos que entregam 5 a 10 vezes
menos. Em comparação direta, o NINA a 4.000 parecerá "caro", mesmo acertando
mais.

**Proposta.** Sem custo de produto: incluir 500 e 1.000 na varredura de recall
de evidência (L1) e no `bench.sweep`, como análise exploratória fora do PREREG.
Se o recall a 1.000 ficar perto do de 4.000, reavaliar o padrão depois do M10
(mudança em `app/`, congelado pela Emenda 15). O `budget_limited` já existe
para pedir mais quando falta.

**Força:** primária para os padrões de Jina e Exa (código). Os números de
qualidade são afirmações de fornecedor.

### L3. O comparador "nativo da Anthropic" mais forte não é o WebFetch do Claude Code, e sim o `web_fetch` da API com filtragem dinâmica. O M10 não o mede.

**O que a fonte mostra** (docs.claude.com e claude.com, lidos na íntegra):
- `web_fetch_20260209` e versões posteriores (atual: `web_fetch_20260318`) deixam
  o Claude escrever e rodar código que filtra a página antes de ela entrar no
  contexto. Fica ligado por padrão no Sonnet/Opus 4.6+ e não tem custo extra
  além dos tokens.
- Na busca: +11% de desempenho médio com −24% de tokens de entrada. BrowseComp
  do Sonnet 4.6 foi de 33,3% para 46,6%; do Opus 4.6, de 45,3% para 61,6%.
  DeepsearchQA F1 foi de 52,6 para 59,4 (Sonnet) e de 69,8 para 77,3 (Opus).
  **Mas o custo ponderado por preço subiu no Opus 4.6.**
- Outros controles do `web_fetch`:
  - `max_content_tokens`: corte simples por tamanho;
  - `use_cache=false`: pula o cache;
  - `response_inclusion="excluded"`;
  - `citations` com `char_location`;
  - `retrieved_at` em cada resultado.
- Limites:
  - não renderiza JavaScript;
  - respeita robots.txt e bloqueia endereços privados (`url_not_allowed`);
  - só busca URLs que já apareceram na conversa, como proteção contra
    exfiltração;
  - erros chegam como resultado com código (`url_not_accessible`,
    `unsupported_content_type` etc.).
- No WebCode Highlights, a Exa rodou exatamente esse modo
  (`claude-extract`, `web_fetch_20260209`): 319 tokens, groundedness 81,5.

**Por que importa.** No M10, `webfetch_live` é o WebFetch do Claude Code
(`bench/m10_agent.py`: Haiku resume a página). É outro produto. Um avaliador
externo dirá que o NINA ganhou do comparador mais fraco da Anthropic. Por
outro lado, o próprio dado da Exa mostra a filtragem dinâmica com groundedness
13 pontos abaixo do melhor extrator. É uma vantagem a provar.

**Proposta.** Não mexer no PREREG (congelado). Fazer a comparação na rodada de
uma chamada (L1): adicionar o braço `claude-extract` (já existe no harness da
Exa) contra o NINA no WebCode e, se possível, nas tarefas do M8. Custo: rodada
paga pequena. Registrar no `docs/CONCORRENTES.md` a diferença entre o WebFetch
do Claude Code e o `web_fetch` da API.

**Força:** fonte primária na íntegra (docs e blog da Anthropic; harness da Exa).

### L4. O concorrente mais parecido com o NINA hoje é o Parallel Extract, e ele tem controles que o NINA não tem.

**O que a fonte mostra.** SDK oficial `parallel-sdk-python`, tipos gerados do
OpenAPI, lidos na íntegra:

| campo do Parallel Extract | o que faz |
|---|---|
| `urls` | até 20 |
| `objective` | linguagem natural, com contexto da tarefa |
| `search_queries` | palavras-chave |
| `max_chars_total` | orçamento entre URLs |
| `excerpt_settings.max_chars_per_result` | teto por URL |
| `full_content` | página inteira, opcional |
| `session_id` | agrupa chamadas de uma tarefa para melhorar as seguintes |
| `client_model` | "o modelo que consome; ajusta os padrões" |
| `fetch_policy.max_age_seconds` | idade máxima do cache (mínimo 600 s) |
| `fetch_policy.disable_cache_fallback` | erro em vez de cache velho |
| `fetch_policy.timeout_seconds` | tempo máximo do fetch ao vivo |

A resposta traz `excerpts[]` em Markdown, `publish_date`, `errors[]` por URL,
`warnings[]` e `usage[]`. Preço: US$ 1 por 1.000 URLs (só resumo). O Amp
(Sourcegraph) usa o Parallel Extract como web fetch (estudo de caso, só resumo).
O próprio Parallel mediu 5 páginas: HTML ~47,6 mil tokens, Markdown inteiro
~8,2 mil, excerpts ~1,6 mil (vendor, o200k).

**Por que importa.** O contrato do NINA (SPEC §8) já cobre urls 1–20, orçamento,
warnings e proveniência, e vai além: `chunk_id`, `version_id`, evidência
literal, `exclude_chunk_ids` e tags que a página não forja. Faltam três coisas
que o mercado já oferece:
- **Frescor controlável por chamada.** O NINA usa cache fixo de 24 h (SPEC §5,
  passo 1). Parallel (`max_age_seconds`), Anthropic (`use_cache`) e Exa
  (`maxAgeHours`, que desde fevereiro de 2026 substitui o `livecrawl`) deixam o
  agente pedir conteúdo novo.
- **Data visível ao agente.** O `<document>` do formato conciso
  (`app/retrieve/render.py`) só traz `source`, `id` e `version`. Parallel
  devolve `publish_date`, Brave devolve `age`, Anthropic devolve
  `retrieved_at`. O NINA já extrai `published_at` e `last_modified`
  (`app/extract/jsonld.py`, `ir.py`), mas não os entrega.
- **Teto por URL** em pedidos com várias URLs (Parallel
  `max_chars_per_result`, Brave `maximum_number_of_tokens_per_url`).

**Proposta (depois do M10; mudam `app/`, congelado):**
- `max_age_seconds` opcional no `assemble`/`resolve`;
- atributos `fetched="…"` e `modified="…"` no `<document>`;
- avaliar teto por URL apenas se uma medição mostrar uma URL dominando o
  orçamento. A seleção ciente de redundância já reduz isso.

Sem custo agora: registrar na SPEC como proposta.

**Força:** primária (tipos do SDK). Preço e números de tokens são do fornecedor.

### L5. Muitas plataformas de docs servem Markdown nativo por página. O fetcher do NINA ignora isso.

**O que as fontes mostram.**
- Docs7 (Upstash, docs no repositório `upstash/context7/docs/docs7/agent-readiness.mdx`,
  lido): cada página responde em `/<page>.md` e também com
  `Accept: text/markdown`. O HTML anuncia o Markdown, o `llms.txt` e o índice de
  skills em cabeçalhos `Link`. Um 404 devolve sugestões das páginas prováveis.
- Mintlify e GitBook (só resumo): `.md` em qualquer URL de página, `llms.txt` e
  `llms-full.txt` gerados, MCP automático em `{site}/~gitbook/mcp`.
- GitMCP (código lido): prioriza `llms.txt`, depois a versão de docs "para IA",
  depois o README. Respeita robots.txt antes de buscar o `llms.txt`.

**Por que importa.** O pedido do NINA não manda `Accept`
(`app/fetch/client.py` envia só `Host` e `User-Agent`). Em sites Mintlify,
GitBook ou Docs7, o NINA extrai do HTML com readability + walker, quando existe
uma versão Markdown limpa, mantida pelo autor e com tabelas e código intactos.
Isso reduziria os casos de `readability_fallback` e a dependência do
Playwright em SPAs.

**Riscos.**
- O Markdown pode diferir do HTML de propósito: o componente
  `<Visibility for="agents">` do Docs7 mostra texto só a agentes. É conteúdo
  não confiável como qualquer outro, mas abre espaço para cloaking.
- A proveniência precisa continuar apontando para a URL HTML, com
  `markdown_url` registrado.

**Proposta (depois do M10; SPEC + `app/fetch`):** negociar `Accept: text/html,
text/markdown;q=0.9` ou seguir um `Link rel=alternate type=text/markdown`,
com warning `markdown_alternate_used` e o mesmo guard e robots.

Sem custo agora: um script de sondagem em `bench/` (HEAD com
`Accept: text/markdown` e `<url>.md`) sobre os hosts de `bench/sites.yaml`,
para saber quantos sites do corpus oferecem isso antes de decidir.

**Força:** primária para Docs7 e GitMCP. Mintlify e GitBook só por resumo.

### L6. A âncora `#seção` da URL é sinal que o agente dá de graça. O NINA a descarta.

**O que as fontes mostram.**
- Os exemplos do Ref mostram agentes lendo URLs com âncora
  (`.../merge/#merge`, `#post-comments-endpoint`). O Ref devolve "os 5k tokens
  mais relevantes" usando o histórico de buscas da sessão (README lido).
- No `bench/tasks/external.yaml` (corpus de Stack Overflow do M10a) há 254
  URLs com âncora, como `kubernetes.io/...#services-without-selectors` e
  `rfc8252#section-7.3`.

**Por que importa.** O `app/fetch/guard.py:228` remove o fragmento ao
normalizar. Isso é correto para cache e identidade, mas o sinal se perde antes
da recuperação. Quando o agente pede `…/service/#services-without-selectors`,
a seção com aquele `id` deveria ganhar prioridade.

**Proposta.**
- Sem custo: medir no corpus do M10a com que frequência a seção da âncora
  contém a resposta-ouro (o `heading_path` dos chunks permite casar o id do
  cabeçalho).
- Depois do M10 (`app/retrieve`, congelado): bônus de rank, ou fragmento
  garantido, para o chunk cuja âncora casa, sempre dentro do orçamento.

**Força:** a evidência no corpus vem de dados do próprio repositório. A
prática do Ref vem do README (vendor).

### L7. O ranqueamento por pergunta quebra tabelas e código em vários concorrentes. O NINA preserva os dois. É vantagem a provar com números.

**O que as fontes mostram (código lido):**
- A Jina admite no código (`read.ts`): com `question`, "a number or a command
  that lives in a table or a code block is not reachable by question; those need
  a plain read". As tabelas e o código cercado são removidos antes do rerank;
  no teste deles, a lista de PIB voltou com os nomes dos países e sem os
  números.
- A Tavily, na descrição do `query` do `tavily_extract`, avisa: "Leave it empty
  for tables, lists, data files and API responses, which it can cut short".
- O Brave avisa que os chunks podem misturar prosa, Markdown e JSON serializado.

**Por que importa.** O chunker do NINA divide tabelas por linha **repetindo o
cabeçalho em cada parte** e divide código por linhas em branco
(`app/index/chunker.py`, `_split_table` e `_split_code`), com o caminho de
seções como prefixo. É um diferencial concreto contra Jina e Tavily nas
perguntas cuja resposta está em tabela ou código, e que hoje não aparece em
nenhum material.

**Proposta (sem custo).**
- Na varredura de L1 e nas tarefas do M8, separar o recall por tipo do bloco
  que contém o ouro (tabela, código, prosa).
- Se a vantagem se confirmar, ela entra nos argumentos de posicionamento (P6 de
  `CONTEXT_ENGINEERING.md`) só depois dos números.

**Força:** primária (código dos concorrentes). A vantagem do NINA ainda não
foi medida.

### L8. Robustez da interface MCP: o que a Context7 aprendeu em produção, release a release

**O que o changelog mostra** (`packages/mcp/CHANGELOG.md`, lido na íntegra):

| versão | mudança |
|---|---|
| 2.2.5 | Aceita nomes de argumento alucinados (`userQuery`/`question` → `query`; `libraryName` → `libraryId`) reescrevendo antes de validar, sem mudar o schema publicado. |
| 3.2.3 | A descrição do `query` pede um conceito por chamada, com exemplos bons e ruins ("too vague: 'auth'"; "too broad: 'routing and auth and caching'"). |
| 4.2.1 | `isError: true` em toda falha; "no documentation found" é resposta válida, não erro. |
| 3.2.0 | Trocou o convite de login injetado no resultado por *elicitation*, porque "some agents treated the in-result text as untrusted instruction content". |
| 4.3.0 | Argumento `language` opcional para ranquear exemplos na linguagem do usuário. |
| 2.0.1 | Anotações `readOnlyHint`/`idempotentHint`/`openWorldHint`. |
| 4.4.3 | `ttlMs` nos `tools/list` (protocolo 2026-07-28). |
| 4.4.4 | As instruções do servidor ganharam a linha "Do not use for: …". |

Exa e Brave também anotam `readOnlyHint` (código lido).

**Comparação com o NINA (`app/mcp_server/server.py`):**
- `ToolError` vira `isError`: já está ok.
- A descrição já pede "one short, focused question".
- As hints em `<nina_notes>` já descrevem sem dar ordens (DECISIONS 2026-10-17),
  em linha com a lição da elicitation.
- Faltam: anotações de ferramenta, a linha "não use para" nas `INSTRUCTIONS` e
  tolerância a nomes alternativos de argumento (`question`, `url` no lugar de
  `urls`, `max_tokens` como string).

**Proposta (depois do M10; `app/mcp_server`, congelado):**
- anotações `readOnlyHint=True` e `openWorldHint=True`;
- uma frase "Do not use for…" (ex.: código que não depende de docs externas);
- aliases de argumento no servidor, com teste.

Custo baixo. No M10 nada muda, porque os braços usam a ferramenta congelada.

**Força:** primária (changelog e código).

### L9. Evidência independente de que uma ferramenta de docs pode não ajudar, ou até atrapalhar. É relevante para o desenho e a leitura do M10.

**O que as fontes mostram.**
- MarginLab, "Do MCPs Actually Improve Coding Agents? Part 1" (abril de 2026,
  só resumo): Context7 no Terminal-Bench 2.0 (89 tarefas) com Codex/GPT-5.4,
  com e sem a ferramenta. Não houve benefício mensurável; o autor chama isso de
  um único ponto de dado.
- mcpbr (Georgia Tech, Zenodo, fevereiro de 2026; README lido, paper só por
  resumo): MCP de grafo de código nas 500 tarefas do SWE-bench Verified com
  Claude Sonnet. Resolução caiu de 49,8% para 42,4% (−14,9% relativo), com 42%
  menos chamadas de ferramenta, 14% menos tokens e 15% menos custo. Ajudou em 1
  de 12 repositórios e piorou em 10. Experimento pareado: mesma tarefa e mesmo
  commit, com e sem MCP, em Docker.
- Evals da You.com (`youdotcom-oss/web-search-agent-evals`, relatório lido): 151
  prompts, k=10. Mostram "flakiness" alta mesmo no melhor par (32%), e P90/P99
  de latência cravados em 60 s, sinal de corte por timeout que censura o
  resultado.

**Por que importa.**
- A H1 do M10 é não inferioridade contra `full_page`, e a H2 é custo por
  acerto. O mcpbr é o padrão de "eficiência sobe, acerto cai" que o PREREG
  precisa conseguir detectar, e a leitura de custo não pode compensar perda de
  acerto. O PREREG já trata custo por acerto e não só custo, o que está certo.
- `context7_live` sem benefício é um resultado plausível. Não deve ser lido
  como "o harness está errado".
- Timeouts precisam ser relatados como falha, não descartados. Conferir com o
  PREREG; o harness já reprocessa falhas 2 vezes (`bench/m10_agent.py`).

**Proposta (sem custo):** no relatório do M10, separar acerto por repositório
ou tema, como o mcpbr fez, e relatar a taxa de cortes por limite de tempo e de
turnos por braço. Se isso não estiver no PREREG, vai como análise
exploratória, sem emenda.

**Força:** mcpbr com código e README primários; números do paper só por
resumo. MarginLab só por resumo.

### L10. Trocar o reranker é a melhoria barata mais óbvia no caminho de seleção

**O que as fontes mostram.** O jina-reranker-v3 (0,6B, listwise, "last but not
late interaction", até 64 documentos numa janela de 131k) marca 61,94 nDCG@10
no BEIR, contra 56,28 do Qwen3-Reranker-0.6B e 61,16 do 4B (paper arXiv
2509.25085, só resumo). Há ainda uma v3.5. O NINA usa
`jina-reranker-v2-base-multilingual` (`app/providers/rerank/jina.py`), de 2024.

**Por que importa.** O M8 apontou o rerank como componente decisivo (ablação
`ours_no_rerank`), e o top-30 cabe na janela listwise da v3.

**Proposta.** O modelo vem de `settings.reranker_model`, então a troca é por
configuração, sem código. Ainda assim muda o produto: medir primeiro com
`bench/diagnose_rerank.py` e a varredura de L1 (gasto de centavos na API da
Jina). Atenção à licença: os pesos da v3 são CC BY-NC. Pela API isso não pesa,
mas impede uso self-hosted comercial, o que conversa com a regra de
dependências MIT/BSD/Apache. **Não trocar antes do M10**: o PREREG congela o
sistema.

**Força:** só resumos (paper e blog). A relação com o NINA vem do código lido.

### L11. Outros benchmarks de "contexto para agentes de código" reutilizáveis com pouco custo

- **opactorai/context-bench** (MIT, lido): 20 cenários de integração com
  frameworks de IA (Agno, Autogen, LangGraph, OpenAI Agents, OpenRouter). Cada
  cenário tem `query`, `oracle` (código de referência) e **`sources`** (URLs
  oficiais). Uma chamada por ferramenta; 3 juízes (GPT-5, Grok-4,
  Deepseek-v3.2) votam completude e relevância. Resultados do autor:

  | ferramenta | cenários aprovados (de 20) | tokens médios |
  |---|---|---|
  | Deepcon | 18 | 2.365 |
  | Context7 | 13 | 5.626 |
  | NIA | 11 | 1.873 |
  | Exa | 5 | 4.753 |
  | Sonnet 4.5 sem ferramentas | 0 | — |

  A Deepcon parece ser do próprio autor, então há conflito de interesse.
  Como as `sources` já são as URLs, o NINA pode ser rodado nas mesmas
  condições, com orçamento parecido (~2k). n=20 é pequeno: serve como sinal,
  não como prova.
- **Nia vs Context7** (vendor, só resumo): taxa de alucinação 52,1% contra 63,4%
  em recursos novos e beta. Hallucination = uso de método ou parâmetro
  inexistente, julgado só contra a documentação fornecida. A métrica é útil
  para a frente "versão e atualidade".
- **WebCode E2E** (33 tarefas versionadas por `release_tag`, como AeroSpace
  v0.20.0, com `test_patch`): conjunto sem executor, mas é o formato
  GitChameleon com docs reais. Pode inspirar tarefas do M10 sensíveis a versão.
- **MCPMark, MCP-Universe, LiveMCPBench, Letta Context-Bench**: medem uso geral
  de MCP e operações de arquivo, não contexto de documentação. Para o NINA
  servem como método:
  - MCPMark relata `pass^4` (confiabilidade), além de pass@1;
  - Letta pontua correção **e eficiência do caminho**;
  - o MCP+ do MCP-Universe pós-processa saídas de ferramentas (−50 a 75% de
    tokens, afirmação deles).

  Nenhum compara Context7, Ref e DeepWiki. **Não existe benchmark público
  independente que compare ferramentas de docs para agentes de código.** O
  WebCode e o M10 seriam os primeiros com harness aberto.

**Proposta:** rodar o NINA no context-bench junto com L1, como evidência
secundária (rodada paga pequena: 20 cenários × 3 juízes). Relatar `pass^k` no
M10 só se o PREREG já previr repetições; senão, fica fora.

**Força:** primária (repositórios). Números de fornecedor.

### L12. Segurança: duas lições novas sobre conteúdo não confiável

1. **ContextCrush** (Noma Security, fevereiro de 2026, só resumo): instruções
   maliciosas nas "Custom Rules" de uma biblioteca falsa no Context7 chegavam
   ao agente como texto de um servidor confiável. A demo lia `.env`, mandava
   para um repositório do atacante e apagava diretórios. A correção, em 5 dias,
   foi sanitizar as regras. Hoje a Context7 roda um classificador de prompt
   injection no conteúdo indexado (`docs/security/data-safety.mdx`, lido).
   - **Para o NINA:** a defesa estrutural já está certa (tags inforjáveis, aviso
     `untrusted_source`, hints sem ordens, nada de canal "de regras" vindo do
     fornecedor). Vale citar o caso no README e na SPEC como motivo do desenho.
     Um classificador de injection violaria a regra de não usar LLM na
     extração; não adotar.
2. **Notas escondidas para agentes viraram produto.** O Context7 Ask e o Docs7
   põem "uma nota no topo do `<body>` que pessoas não veem, mas agentes leem"
   apontando para endpoints `/ask` e `/feedback` (GET com parâmetros). O
   `<Visibility for="agents">` mostra texto só no Markdown.
   - **Para o NINA:** páginas legítimas vão carregar texto oculto que pede ao
     agente para chamar URLs. O NINA já trata isso como dado. Falta uma
     **dica** determinística opcional, como `hidden_agent_note`, quando o walker
     encontra texto visualmente oculto (`display:none`, `hidden`,
     `aria-hidden`) que contenha URL ou verbo imperativo. Isso exige decidir se
     texto oculto deve sequer entrar no IR. É mudança de SPEC (congelada).
     Custo zero agora: escrever a proposta.

**Força:** Docs7 e Ask primários (docs no GitHub). ContextCrush só por resumo
de imprensa.

### L13. Distribuição: o que vira padrão em MCP e skill

- **Skill em contexto isolado.** O skill de extração da Parallel usa
  `context: fork` e um subagente próprio (`parallel-agent-skills`, lido): o
  conteúdo longo fica fora do contexto principal e vai para um arquivo JSON
  "autoritativo". O skill `skills/nina-docs` roda inline. Para leitura de
  página inteira (`resolve_url`), um modo fork faria sentido. Mudança só no
  skill (produto, fora de `app/`); o M10 roda sem skills.
- **Filtrar ferramentas no servidor.** A Jina deixa `include_tools`,
  `exclude_tags` e `max_tokens` na URL do MCP, porque "registering a tool costs
  context tokens whether or not it is called". A Exa faz o mesmo
  (`?tools=`). Com 3 ferramentas o NINA já é enxuto; registrar só como padrão
  de mercado.
- **Instruções de servidor como estratégia de pesquisa.** As `SERVER_INSTRUCTIONS`
  da Tavily ensinam um laço: confirmar o candidato lendo a página, manter o
  melhor candidato até haver contradição, parar quando novas buscas não trazem
  evidência. As do NINA dizem quando usar, não como raciocinar. Não mudar antes
  do M10.
- **Programmatic tool calling.** A Anthropic tornou GA o chamado de
  ferramentas a partir de código (`allowed_callers`). Um cliente pode chamar
  `assemble_context` de dentro do sandbox e filtrar. O formato `detailed` (JSON
  com ids e scores) é o que serve nesse caso; vale um exemplo no README depois
  do M10.

**Força:** primária (código e docs).

---

## 2. Onde o NINA já está igual ou melhor (com evidência)

| ponto | NINA | concorrentes |
|---|---|---|
| Tabelas e código sob seleção por pergunta | tabela partida por linha com cabeçalho repetido; código por blocos; prefixo de seções (`chunker.py`) | Jina remove tabelas e código antes do rerank (admitido em `read.ts`); Tavily manda não usar `query` em tabelas e listas |
| Orçamento entre várias URLs | `max_tokens` global, seleção gulosa ciente de redundância, 1–20 URLs | Parallel tem `max_chars_total` (até 20 URLs); Exa só ganhou orçamento entre URLs em agosto de 2026 (Dynamic Highlights, preview) |
| Proveniência por fragmento | `document_id`, `version_id`, `chunk_id`, `source_url`, página; evidência literal no `detailed` | Exa, Parallel, Tavily e Brave devolvem URL + texto; só a Anthropic dá offsets de caractere (`char_location`) |
| Fronteira entre texto da página e metadados | tags que a página não forja; `<nina_notes>` | todos concatenam texto da página com rótulos em texto puro ("Title:", "URL:"); a Context7 só tirou o convite de login de dentro do resultado na 3.2.0 |
| Falha explícita | `warnings`, `source_failed`, `low_coverage`, `budget_limited`, `mixed_versions` | Parallel tem `warnings`/`errors` por URL; Tavily `failed_results`; Anthropic códigos de erro; ninguém sinaliza cobertura baixa nem versões misturadas |
| Determinismo e custo previsível | sem LLM no caminho; rerank e embeddings baratos | filtragem dinâmica da Anthropic custa tokens de código e encareceu o Opus 4.6; o WebFetch do Claude Code passa a página por Haiku |
| SSRF e robots | guard próprio, conexão no IP validado, robots respeitado | Anthropic também bloqueia endereços privados e respeita robots; GitMCP checa robots antes do `llms.txt`; os demais não documentam |
| Anti-repetição entre chamadas | `exclude_chunk_ids` explícito, sem estado | Ref filtra por sessão; Parallel tem `session_id` (opaco) |
| JS/SPA | Playwright pelo guard | `web_fetch` da Anthropic não renderiza JS; Tavily exige `advanced` (2x preço); Parallel e Exa dizem que renderizam |

## 3. Onde o NINA está atrás de alguém (lacunas honestas)

1. **Nenhum número público comparável.** Exa, Parallel, Tavily, Brave e
   Perplexity já aparecem num harness aberto (WebCode), no índice da Artificial
   Analysis (provider-swap com GPT-5.6 Luna, 25 turnos, DeepSearchQA +
   BrowseComp + AA-Omniscience; Parallel advanced em 1º com 75 em agosto de
   2026) e em BrowseComp de fornecedor. O NINA só tem o M8 interno. → L1.
2. **Orçamento padrão 5 a 10 vezes maior** que o dos extratores de highlights,
   sem medição abaixo de 2.000 tokens. → L2.
3. **Frescor e data invisíveis ao agente:** cache fixo de 24 h, sem
   `max_age`, sem data no formato conciso. Parallel, Exa e Anthropic expõem
   ambos. → L4.
4. **Não usa os canais "para agentes"** que as plataformas de docs abriram
   (`.md`, `Accept: text/markdown`, `llms.txt`). → L5.
5. **Descarta a âncora da URL.** → L6.
6. **Reranker de geração anterior** (v2-base contra v3 listwise). → L10.
7. **Cobertura:** os concorrentes de busca encontram as URLs; o NINA exige que
   o agente já as tenha. É escolha de escopo (sem crawler), mas no mercado
   "busca + extração" vem junto (Exa, Parallel, Tavily, Brave LLM Context, Ref,
   Context7 Search API v3). O par natural no M10 é a busca nativa do agente +
   NINA. O PREREG já fixa URLs, então isso não afeta a validade, mas limita o
   argumento comercial.
8. **Distribuição:** Context7 está em todos os IDEs com setup de um comando
   (`npx ctx7 setup`), OAuth, plugin do Claude Code e cota gratuita de 1.000
   chamadas/mês (`docs/plans-pricing.mdx`, lido). DeepWiki, GitMCP, GitBook e
   Mintlify dão MCP sem cadastro. O NINA exige rodar a API.

## 4. Fontes lidas na íntegra

Repositórios (clone em 2026-10-28; arquivos citados lidos por completo):
- https://github.com/exa-labs/benchmarks (README, `benchmarks/exa.py`,
  `benchmarks/graders/rag.py`, `harness/searchers/claude.py`, `systems.toml`,
  `data/webcode/*.jsonl`)
- https://github.com/upstash/context7 (`packages/mcp/CHANGELOG.md`,
  `packages/mcp/src/index.ts`, `docs/plans-pricing.mdx`, `docs/ask/overview.mdx`,
  `docs/docs7/agent-readiness.mdx`, `docs/security/data-safety.mdx`,
  `docs/api-guide.mdx`)
- https://github.com/ref-tools/ref-tools-mcp (README)
- https://github.com/idosal/git-mcp (README, `src/api/tools/commonTools.ts`)
- https://github.com/exa-labs/exa-mcp-server (README, `src/tools/exaCode.ts`,
  `src/tools/webFetch.ts`)
- https://github.com/tavily-ai/tavily-mcp (`src/index.ts`, `src/format-results.ts`)
- https://github.com/brave/brave-search-mcp-server (README,
  `src/tools/llm_context/*`)
- https://github.com/jina-ai/MCP (README, `src/utils/read.ts`, `src/tools/jina-tools.ts`)
- https://github.com/parallel-web/parallel-sdk-python (`src/parallel/types/*extract*`,
  `fetch_policy_param.py`, `excerpt_settings_param.py`)
- https://github.com/parallel-web/parallel-agent-skills (`skills/parallel-web-extract/SKILL.md`)
- https://github.com/opactorai/context-bench (README, `scenarios/agno.yaml`)
- https://github.com/greynewell/mcpbr (README)
- https://github.com/youdotcom-oss/web-search-agent-evals (README,
  `data/comparisons/2026-02-18/REPORT.md`)
- https://github.com/SalesforceAIResearch/MCP-Universe (README)
- https://github.com/icip-cas/LiveMCPBench (README)
- https://github.com/letta-ai/letta-evals (README e estrutura do leaderboard)

Páginas:
- https://docs.claude.com/en/docs/agents-and-tools/tool-use/web-fetch-tool.md
- https://docs.claude.com/en/docs/agents-and-tools/tool-use/web-search-tool.md
- https://claude.com/blog/improved-web-search-with-dynamic-filtering

## 5. Fontes que não consegui acessar e que valem a leitura completa

(Host bloqueado nesta sessão. Salvar como PDF.)

1. **MarginLab, "Do MCPs Actually Improve Coding Agents? Part 1"**:
   https://marginlab.ai/blog/do-mcps-improve-coding-agents-part-1/ — único
   teste independente do Context7 num benchmark de agente (TB2.0); faltam os
   números e o método de variância.
2. **Artificial Analysis, metodologia do Search Index**:
   https://artificialanalysis.ai/methodology/search-api — benchmark
   provider-swap independente; ver como dão `web_fetch` a cada provedor e como
   contam custo. Modelo para comparar o NINA de forma externa.
3. **Exa, "WebCode"**: https://exa.ai/blog/webcode — método de construção dos
   250/307 itens e das 33 tarefas E2E (como evitaram contaminação).
4. **Exa, "Highlights" (abril de 2026) e "Dynamic Highlights" (agosto de 2026)**:
   https://exa.ai/blog/highlights-for-agents e
   https://exa.ai/docs/changelog/february-2026-api-updates — método da
   afirmação "500 caracteres = 8.000" e o desenho do orçamento entre URLs
   (o mesmo problema da seleção do NINA).
5. **Parallel, "How we evaluate web search APIs"**:
   https://parallel.ai/articles/how-we-evaluate-web-search-apis e
   https://parallel.ai/benchmarks — custo por mil consultas incluindo tokens
   do LLM, que é a métrica da H2 do NINA.
6. **Parallel, docs do Extract (best practices e advanced settings)**:
   https://docs.parallel.ai/extract/best-practices.md e
   https://docs.parallel.ai/extract/advanced-extract-settings.md — como o
   `objective` muda os excerpts e o efeito de `client_model` e `session_id`.
7. **Nia, "Nia vs Context7" e "context augmentation benchmark"**:
   https://trynia.ai/blog/nia-vs-context7 e
   https://trynia.ai/blog/nia-context-augmentation-benchmark — métrica de
   alucinação de API (método e parâmetro inexistentes) para tarefas sensíveis a
   versão.
8. **mcpbr, preprint completo**: https://zenodo.org/records/18627369 — desenho
   pareado, estatística e análise por repositório; comparar com o PREREG.
9. **jina-reranker-v3 (paper)**: https://arxiv.org/abs/2509.25085 — tabelas
   completas e latência para decidir L10.
10. **Noma Security, ContextCrush**:
    https://noma.security/blog/contextcrush-context7-the-mcp-server-vulnerability/
    — vetor de ataque detalhado, útil para a seção de segurança da SPEC.
11. **Tavily, "Exa vs Parallel: benchmarking retrieval APIs for AI agents in
    2026"**:
    https://tavily.com/blog/exa-vs-parallel-benchmarking-retrieval-apis-for-ai-agents-in-2026
    — a metodologia não apareceu nos resumos.
12. **Gemini API, URL context (limites e esquema)**:
    https://ai.google.dev/gemini-api/docs/url-context — confirmar índice → fetch
    ao vivo, 20 URLs e 34 MB, e `url_context_metadata`. Pelos resumos, o
    conteúdo recuperado conta como tokens de entrada e o índice pode estar
    defasado.

Fora daqui, mas de menor valor (só resumos; não priorizar): preços e SimpleQA
da Linkup, Perplexity Search API (`max_tokens_per_page`, US$ 5 por 1.000),
Cursor @Docs, Copilot Spaces, DeepWiki MCP (3 ferramentas: `read_wiki_structure`,
`read_wiki_contents`, `ask_question`; respostas geradas por LLM, sem número
público de qualidade) e Amp. Nenhum publicou método ou número que mude uma
decisão do NINA.
