# Concorrentes: o que erraram, o que aprenderam e o que vale para o NINA

Pesquisa de 2026-10-16 (M10a). Fontes no fim. Números de fornecedores são
afirmações deles, não medições independentes; estão marcados assim.

## Quem é quem

| produto | o que entrega ao agente | como escolhe o conteúdo |
|---|---|---|
| Context7 (Upstash) | trechos de código e referência de API por biblioteca | índice pré-extraído; desde 2026 reranqueia no servidor |
| Ref (ref.tools) | busca em docs + leitura de página com só as seções relevantes | o agente escolhe o que ler; páginas grandes são filtradas na hora |
| Exa | "highlights": trechos curtos por consulta | método próprio, < 100 ms por pedido |
| Firecrawl | Markdown da página (`onlyMainContent`) | conteúdo principal por heurística, com fallback |
| Jina Reader | Markdown da página | começou com heurística; hoje usa um modelo (ReaderLM-v2) |
| Crawl4AI | "fit markdown": página podada | poda por densidade de texto/links ou por BM25 da consulta |

O NINA fica entre o Ref e o Exa: recebe pergunta e URLs e devolve só os
fragmentos úteis dentro de um orçamento, com proveniência.

## Lições

### 1. A resposta da ferramenta precisa ser enxuta por padrão

- O guia da Anthropic para ferramentas de agentes pede respostas de alto sinal,
  um parâmetro `response_format` (conciso ou detalhado) e evitar
  identificadores técnicos (UUID, hashes) quando o agente não precisa deles; no
  exemplo deles, o conciso usa cerca de um terço dos tokens.
- O Context7 mudou para filtrar e reranquear no servidor e relata 65% menos
  tokens de contexto e 38% menos latência (números internos).

**No NINA:** medimos na metade A (só cache, sem custo) que o JSON do `assemble`
que o agente recebe pelo MCP custa **+104% de tokens em 4k e +77% em 8k** sobre o
texto dos fragmentos: ids `doc_`/`ver_`/`chunk_id`, quatro scores, um trecho de
evidência que repete o próprio texto, a pergunta ecoada e o Markdown escapado
dentro do JSON. Aplicada em `23b887f`: o formato conciso, padrão no MCP, corta
42% a 61% desses tokens.

### 2. Mais texto não é melhor

- Exa: 500 caracteres de highlights igualam a precisão dos primeiros 8.000
  caracteres da página; 4 mil de highlights batem 32 mil de texto completo
  (números do fornecedor).
- Chroma ("context rot"): em 18 modelos, o desempenho fica menos confiável
  conforme a entrada cresce, e trechos parecidos com a resposta (distratores)
  pioram mais.
- Ref: 500 a 5 mil tokens por consulta conforme a dificuldade, contra um lote
  fixo do Context7.

**No NINA:** com λ = 0 a seleção enche o orçamento pedido. A cobertura da seção
sobe com o orçamento, mas a qualidade da resposta pode não subir junto; isso só
se mede com respostas (trilha 2 ou M10). Não mudar sem essa medida.

### 3. Não repetir o que o agente já viu

O Ref diz nunca devolver o mesmo link duas vezes numa sessão; Context7 e Ref
guardam estado de sessão para evitar duplicatas.

**No NINA:** um agente chama `assemble` várias vezes nas mesmas URLs e recebe de
novo os mesmos fragmentos. Um parâmetro opcional para excluir fragmentos já
entregues economizaria tokens nas sessões do M10.

### 4. Trechos pré-extraídos perdem avisos e prosa

A crítica do Ref ao Context7: só trechos de código perdem avisos e explicações.

**No NINA:** já trabalhamos sobre a página inteira, com admonições como blocos.
Manter.

### 5. "Só o conteúdo principal" precisa de fallback

O Firecrawl passou `onlyMainContent` a padrão na v1 e depois adicionou um
fallback porque o filtro às vezes cortava demais.

**No NINA:** foi exatamente o item 4 da Fase 3 (aceito): o readability guardava
só um bloco nas páginas sem `<main>`. A medida de retenção por site virou uma
checagem de regressão (`uv run python -m bench.extract_retention --check`).

### 6. Contexto no pedaço antes de indexar; rerank profundo

A Anthropic ("contextual retrieval") prefixa 50 a 100 tokens de contexto em cada
pedaço antes do embedding e do BM25, e reranqueia 150 candidatos para entregar
20: a falha de recuperação cai de 5,7% para 1,9%.

**No NINA:** já prefixamos o caminho de seções de cada pedaço, de forma
determinística e sem LLM, que é a versão permitida pela nossa regra. O rerank é
de 30 candidatos; aprofundar custa Jina e fica fora do "sem gasto".

### 7. Depender de cotas de terceiros vira problema do usuário

Usuários do Context7 relatam que o corte do plano gratuito (1.000 pedidos por mês)
fez o assistente voltar a padrões desatualizados; alguns construíram
alternativas locais.

**No NINA:** sentimos o mesmo com o Jina (403 por cota, 429 por limite de taxa).
Um modo totalmente local (embeddings e reranker locais atrás das interfaces de
`app/providers/`) é candidato a marco futuro, não a esta fase.

### 8. Cuidado com moda: llms.txt

Cerca de 10% dos sites publicam `llms.txt`, mas estudos com 300 mil domínios e
logs de 900 domínios não acharam efeito mensurável, e muitos arquivos são
inválidos. Não investir agora.

### 9. Erros e cortes precisam dizer ao agente o que fazer

O guia da Anthropic pede mensagens de erro com a correção concreta e, quando a
resposta é cortada, instruções de como pedir o resto.

**No NINA:** quando a cobertura é `insufficient` ou o orçamento corta conteúdo,
a resposta pode dizer o que fazer (aumentar `max_tokens`, estreitar a pergunta,
passar outra URL). Mudança pequena, testável offline.

### 10. Páginas montadas por JavaScript

Jina e Firecrawl renderizam com navegador. No NINA, `should_render` já decide
renderizar 103 das 104 páginas do angular.io que vêm vazias no HTML bruto
(verificado no corpus). Só o benchmark, que por regra extrai o HTML bruto, as vê
vazias. Nada a fazer no produto.

## Erros reais dos usuários: 4.721 issues públicas

Coletadas em 2026-10-17 pela API do GitHub: issues abertas e fechadas, sem pull
requests e sem nomes de autores.

| repositório | issues | período |
|---|---|---|
| `upstash/context7` | 2.413 | abr/2025 – out/2026 |
| `firecrawl/firecrawl` | 1.007 | abr/2024 – out/2026 |
| `unclecode/crawl4ai` | 1.057 | mai/2024 – out/2026 |
| `jina-ai/reader` | 244 | abr/2024 – out/2026 |

**Método:**
- Cada issue é classificada por palavras-chave **no título**, que é o resumo
  escrito pelo próprio usuário. Os padrões estão no fim desta seção.
- Uma issue pode ter mais de um tema.
- No Context7, as issues abertas por formulário contam à parte.
- A precisão de cada tema foi estimada lendo 8 títulos sorteados.
- Uma primeira rodada também lia o corpo das issues e foi descartada: o texto
  fixo dos formulários poluía os temas.
- Os números contam **reclamações**; não medem a qualidade dos produtos. Entre
  51% e 66% das issues livres não caem em tema nenhum: são bugs e pedidos
  específicos de cada produto.

### Context7: mais da metade das issues é manutenção do índice

| formulário | issues | % do total |
|---|---|---|
| pedido de atualização ("Refresh request") | 723 | 30% |
| documentação errada ou faltando ("Library Report") | 315 | 13% |
| pedido de verificação de biblioteca | 290 | 12% |

Os três somam **55% das issues** do Context7. Entre as issues livres, outras 70
reclamam de documentação desatualizada ou de versão errada, e várias tratam de
bibliotecas "grandes demais para processar" ou presas em "processando". É o
custo de um índice montado antes: alguém precisa mantê-lo atualizado, e o
usuário vira a fila.

### Temas nas issues livres

Percentuais sobre as issues que não são formulário: Context7 1.085, Firecrawl
1.007, Crawl4AI 1.057, Jina Reader 244.

| tema | Context7 | Firecrawl | Crawl4AI | Jina Reader | precisão na amostra |
|---|---|---|---|---|---|
| auto-hospedagem, Docker, instalação | 36 (3%) | **254 (25%)** | **134 (13%)** | 9 (4%) | alta (6–8 de 8) |
| integração com agentes e MCP | **185 (17%)** | 35 (3%) | 23 (2%) | 6 (2%) | alta no Context7 (8 de 8) |
| documentação desatualizada ou versão errada | 70 (6%) | 7 (1%) | 8 (1%) | 0 | alta (8 de 8) |
| extração e formato do Markdown | 5 (0%) | 37 (4%) | 68 (6%) | 15 (6%) | alta (6–7 de 8) |
| conteúdo faltando ou vazio | 26 (2%) | 58 (6%) | 43 (4%) | 11 (5%) | baixa no Firecrawl (~3 de 8; pega métodos faltando no SDK) |
| timeout, lentidão, travamento | 21 (2%) | 48 (5%) | 51 (5%) | 9 (4%) | alta (7–8 de 8) |
| páginas com JavaScript | — | 47 (5%) | 41 (4%) | 11 (5%) | baixa no Firecrawl (~3 de 8; pega `js-sdk`); no Context7 só pega nomes como Node.js |
| bloqueio, anti-bot, robots.txt | 11 (1%) | 17 (2%) | 25 (2%) | 12 (5%) | alta (6–8 de 8) |
| cota, limite de taxa, cobrança | 14 (1%) | 33 (3%) | 8 (1%) | 11 (5%) | alta (7–8 de 8) |
| crawl e descoberta de URLs | 1 (0%) | 29 (3%) | 50 (5%) | 0 | alta (8 de 8) |
| PDF e arquivos | 6 (1%) | 24 (2%) | 14 (1%) | 7 (3%) | alta |
| segurança | 8 (1%) | 12 (1%) | 7 (1%) | 1 (0%) | alta (~6 de 8) |
| codificação e idioma | 0 | 8 (1%) | 3 (0%) | 5 (2%) | alta |

### O que isso diz para o NINA

1. **Não ter índice pré-montado é uma vantagem medida.**
   - 55% das issues do Context7 são manutenção do índice.
   - O NINA lê a página que o agente passa, com cache de 24 h, sem fila de
     atualização nem limite de tamanho por biblioteca. Isso vale como argumento
     de venda.
   - Também vale como teste: páginas enormes precisam passar pelo `assemble`
     sem erro. Uma das tarefas do piloto cita 493.925 tokens de páginas.
2. **Instalar e hospedar é o maior atrito dos projetos abertos.**
   - É 25% das issues livres no Firecrawl e 13% no Crawl4AI.
   - A pilha do NINA (Postgres com pgvector, S3, Playwright) é tão pesada
     quanto, e quem for hospedar vai sofrer igual.
   - Medir o tempo do `git clone` ao primeiro resultado (Fase 5 do M10) não
     custa nada e deveria vir antes de qualquer piloto externo.
   - Um modo leve, sem Postgres e S3, é decisão de produto para depois.
3. **Integração com agentes é o maior tema entre as issues livres do Context7
   (17%).**
   - Os problemas são de conexão, transporte e clientes como Cursor e Claude.
   - No Crawl4AI, log escrito no stdout quebrou o transporte stdio do MCP
     (`unclecode/crawl4ai#1968`).
   - No NINA os logs vão para o stderr (`logging.StreamHandler()` padrão), e o
     servidor MCP nem configura logging. Um teste garante isso desde a trilha B
     (`tests/unit/mcp/test_stdio_clean.py`).
4. **Ferramenta de documentação vira alvo de prompt injection.**
   - No Context7, respostas do `query-docs` vieram com um "aviso do sistema" e
     conteúdo de injeção anexado (`upstash/context7#2663`, `#2673`). Também
     houve injeção pela descrição da ferramenta (`#2336`).
   - O NINA marca o conteúdo como `untrusted_source` e não acrescenta
     instruções à resposta.
   - A lição vale para as dicas acionáveis (lição 9): elas têm de vir separadas
     do texto das páginas e marcadas como metadados do NINA, nunca como ordem ao
     agente.
5. **SSRF e TLS são falhas reais da categoria.**
   - O Firecrawl teve SSRF para a rede interna (`firecrawl/firecrawl#2070`,
     `#4902`), e o Jina Reader, SSRF por redirecionamento
     (`jina-ai/reader#1252`).
   - O Crawl4AI baixava o robots.txt com a verificação de TLS desligada
     (`unclecode/crawl4ai#2252`).
   - O guard do NINA revalida cada redirecionamento, e o robots.txt passa pelo
     mesmo cliente com `verify=True`. Isso já está coberto pelos testes do M1 e
     vale citar como diferencial.
6. **Os mesmos defeitos de extração aparecem nos três extratores.**
   - Quebras de linha perdidas em bloco de código (`jina-ai/reader#77`),
     código faltando (`firecrawl/firecrawl#215`) e texto repetido
     (`unclecode/crawl4ai#1213`).
   - Links relativos errados, tabelas quebradas e o "só o conteúdo principal"
     cortando demais (`firecrawl/firecrawl#540`).
   - Cada um vira um teste de extração do NINA com uma fixture pequena, somado
     ao teste de retenção por site (lição 5).
7. **Bloqueio é inevitável para quem respeita o robots.txt e não contorna
   anti-bot.**
   - É 5% das issues do Jina, e o NINA não contorna anti-bot, por regra.
   - O que dá para fazer é falhar de forma explícita e dizer o que fazer, para
     o agente não concluir que a página está vazia.
   - No Firecrawl, uma decisão do robots.txt era descartada em silêncio
     (`firecrawl/firecrawl#4241`).
8. **Cota e cobrança irritam mais do que o número sugere.**
   - Há relatos de 402 com saldo disponível, de limite atingido já no primeiro
     pedido e de picos de consumo de tokens.
   - Isso reforça o modo local de provedores (lição 7) e o `Retry-After` que o
     NINA já devolve.
9. **Crawl gera muitos bugs:** 5% no Crawl4AI e 3% no Firecrawl. O NINA não tem
   crawler (fora do escopo do MVP) e fica livre dessa fonte de erros.

**Padrões usados** (no título, sem diferenciar maiúsculas; trechos principais):

| tema | palavras-chave |
|---|---|
| auto-hospedagem | `self-host`, `docker`, `kubernetes`, `helm`, `deploy`, `build error/fail`, `compose`, `redis`, `install` |
| integração | `mcp`, `cursor`, `claude`, `copilot`, `vscode`, `windsurf`, `cline`, `tool call/schema/description`, nomes das ferramentas do Context7, `sse`, `stdio`, `n8n`, `langchain`, `agent` |
| desatualizada | `outdated`, `out of date`, `stale`, `old version`, `wrong version`, `latest version`, `needs update`, `reindex`, `refresh` |
| extração e formato | `markdown`, `formatting`, `table`, `code block`, `spacing`, `newline`, `html instead`, `garbled`, `duplicate text`, `broken link`, `relative link/path/url`, `readability`, `main content`, `fit_markdown` |
| conteúdo faltando | `missing`, `incomplete`, `empty`, `truncat`, `cut off`, `partial`, `no content`, `no data`, `blank`, `not returning` |
| timeout | `timeout`, `timed out`, `slow`, `latency`, `hang`, `stuck`, `freez`, `performance`, `cpu`, `memory`, `oom`, `crash` |
| JavaScript | `javascript`, `js`, `dynamic`, `client-side`, `lazy load`, `spa`, `single-page`, `scroll`, `wait_for`, `render` |
| bloqueio | `403`, `forbidden`, `blocked`, `captcha`, `cloudflare`, `anti-bot`, `bot protection/detection/challenge`, `access denied`, `robots.txt`, `firewall` |
| cota | `rate limit`, `429`, `quota`, `credits`, `billing`, `pricing`, `free tier/plan`, `too many requests`, `payment`, `402`, `subscription`, `token usage`, `pro plan`, `paid` |
| crawl | `deep crawl`, `sitemap`, `max_depth`, `recursive`, `whole/entire site`, `link discovery`, `bfs`, `dfs`, `map` |
| PDF | `pdf`, `docx`, `pptx`, `epub` |
| segurança | `injection`, `malicious`, `vulnerab`, `ssrf`, `xss`, `exfiltrat`, `poison`, `cve`, `tls verification`, `supply chain`, `security issue/audit/finding`, `leaking key`, `exposed` |
| codificação | `encoding`, `utf-8`, `unicode`, `charset`, `mojibake`, nomes de idiomas, `non-english`, `locale` |

## O que dá para fazer sem gastar

| # | mudança | lição | como medir sem custo | mexe no contrato? |
|---|---|---|---|---|
| 1 | `response_format` conciso no `assemble` e no MCP: Markdown com um cabeçalho por documento, caminho de seções, referência curta por fragmento; sem scores, sem evidência duplicada, sem ecoar a pergunta. **Feito (`23b887f`).** | 1 | tokens na metade A, com o mesmo conteúdo | sim (SPEC §8) |
| 2 | teste de regressão da retenção de texto por site no corpus congelado. **Feito (`fcea16d`).** | 5 | roda local | não |
| 3 | dicas acionáveis quando a cobertura é baixa ou o orçamento corta, separadas do texto das páginas e marcadas como metadados do NINA. **Feito (`158e0ca`).** | 9; achado 4 | testes de unidade | sim, aditivo |
| 4 | excluir fragmentos já entregues (parâmetro opcional). **Feito (`23bd3ac`).** | 3 | testes; ganho real só no M10 | sim, aditivo |
| 5 | teste de que o servidor MCP via stdio não escreve nada no stdout além do protocolo. **Feito (`ed94811`).** | achado 3 | teste de unidade | não |
| 6 | testes de extração para bloco de código (quebras de linha), tabela, link relativo e texto repetido. **Feito (`c11182c`, `6819766`).** | achado 6 | fixtures pequenas, local | não |
| 7 | `assemble` sobre uma página de centenas de milhares de tokens termina sem erro e dentro do orçamento. **Feito (`0f4c969`).** | achado 1 | corpus congelado, local | não |
| 8 | falha explícita e acionável para página bloqueada ou proibida pelo robots.txt. **Feito (`edf579f`, `158e0ca`).** | achado 7; lição 9 | testes de unidade | sim, aditivo |

O item 1 era o de maior ganho econômico: o conteúdo é o mesmo, só muda a
embalagem. Foi medido em −48% de tokens em 4k e −42% em 8k. O risco é a
qualidade da resposta mudar com o formato; sem gastar, essa verificação fica
para as sessões de agente do M10, que já vão medir o custo por tarefa resolvida.

Fica para depois (custa ou exige medir respostas): rerank mais profundo e modo
local de provedores. O orçamento adaptativo foi medido sem custo e rejeitado na
metade A (A1, `cd80170`): todo corte por relevância perdia mais cobertura do que
economizava.

## Firecrawl em 2026-10-28: Research Index, Developer Search e Deep Research

Lido na íntegra: o README do repositório `firecrawl/firecrawl`, o README do
`firecrawl-mcp-server`, as páginas "Research Index" e "Deep Research" (o
texto-fonte está no repositório `firecrawl/firecrawl-docs`; o site
docs.firecrawl.dev é bloqueado neste ambiente) e a skill
`firecrawl-research-index` (repositório `firecrawl/cli`).

**O que eles lançaram:**
- **Research Index:** ~43 milhões de resumos de artigos (PubMed, bioRxiv,
  medRxiv, arXiv). O recurso mais próximo do NINA é
  `read_paper(id, question, k)`: dado um documento conhecido e uma pergunta,
  devolve só os trechos que respondem. É o mesmo desenho do
  `assemble_context`, num domínio diferente.
- **Developer Search** (`firecrawl_developer_search`): um índice para agentes
  de código com issues e PRs do GitHub, READMEs e documentação de código.
  Devolve trechos por resultado, até `k = 100`. **É concorrente direto do NINA**
  nas perguntas de programação, com o modelo de índice pré-montado (como o
  Context7).
- **Deep Research:** o caminho recomendado é um laço busca → raspar a página
  inteira → LLM analisa → repetir. É exatamente o padrão `full_page` que o
  NINA quer baratear.

**O que isso diz para o NINA:**
1. **Validação de mercado.** Um dos maiores fornecedores de contexto para
   agentes construiu "trechos de um documento conhecido para uma pergunta".
   Isso confirma o formato do produto.
2. **O próprio Firecrawl manda evitar a página inteira.** O guia do MCP deles
   diz para usar Markdown completo "com parcimônia", só quando a tarefa exige
   a página toda, e preferir extração dirigida "para não estourar a janela de
   contexto". Argumento de posicionamento: o NINA faz essa redução sem LLM,
   com proveniência e dentro de um orçamento de tokens.
3. **Onde o NINA difere do Developer Search:**
   - sem índice: lê qualquer URL que o agente passa, e não sofre do problema de
     manutenção que é 55% das issues do Context7;
   - orçamento em tokens em vez de `k` resultados;
   - texto literal com `document_id`, `version_id` e `chunk_id`;
   - determinístico e auto-hospedável;
   - o Firecrawl é AGPL-3.0, o que o NINA não pode usar como dependência.
4. **Eles distribuem instruções ao agente por Skill, além do MCP.** A
   documentação "recomenda fortemente" CLI ou MCP junto com uma skill
   (`npx skills add …`). A skill de pesquisa ensina quando usar cada
   ferramenta, como reformular a pergunta quando o resultado vem fraco ("passe
   várias formulações distintas") e os erros comuns. É a divulgação
   progressiva do texto da Anthropic sobre skills.
   **Proposta:** uma `SKILL.md` do NINA (quando usar, pergunta curta,
   `exclude_chunk_ids`, como ler `<nina_notes>`). Só produto; o braço `nina` do
   M10 roda sem skills (`skills=[]`).
5. **Resultados grandes.** Acima de ~20 mil tokens estimados (bytes ÷ 4), o
   Firecrawl devolve um aviso curto e guarda o resultado num espaço de
   trabalho que o agente consulta por Bash. É a resposta deles ao limite de 50
   mil caracteres do Claude Code (`docs/PESQUISA_LABS.md`, C1). Confirma que o
   limite é real e afeta todo fornecedor de página inteira.
6. **Atrito de adoção.** O MCP hospedado deles tem um nível gratuito sem chave
   (scrape, search e parse limitados por taxa), e o Research Index funciona sem
   chave para começar. O NINA exige chave e uma pilha pesada para hospedar,
   que é o maior tema de issues dos projetos abertos (seção acima). Uma
   demonstração hospedada sem chave seria a maior alavanca de adoção; é M11.
7. **Quantidade de ferramentas.** O perfil completo do MCP deles tem 27
   ferramentas, e por isso oferecem perfis de 3 e de 9. O NINA tem 3, dentro
   da faixa em que a Anthropic diz que a escolha de ferramenta funciona bem.

## Fontes

- Anthropic, "Writing effective tools for agents": https://www.anthropic.com/engineering/writing-tools-for-agents
- Anthropic, "Contextual Retrieval": https://anthropic.com/news/contextual-retrieval
- Context7 (Upstash): https://upstash.com/blog/context7-mcp e resumo de "Context7 without context bloat": https://www.plushcap.com/content/upstash/blog/upstash-context7-without-context-bloat
- Ref, comparação com o Context7 (material do fornecedor): https://docs.ref.tools/comparison/context7
- Ref MCP: https://glama.ai/mcp/servers/ref-tools/ref-tools-mcp
- Relato de cotas do Context7: https://medium.com/@moshesimantov/i-built-a-context7-local-first-alternative-with-claude-code-eb14c9fd654f
- Exa, "Highlights for agents": https://exa.ai/blog/highlights-for-agents
- Chroma, "Context Rot": https://research.trychroma.com/context-rot
- Firecrawl v1: https://www.firecrawl.dev/blog/launch-week-i-day-4-introducing-firecrawl-v1 e notas de versão: https://releases.sh/release/rel_rVsk52mw4NSzu46r_kLxF
- Jina ReaderLM-v2: https://arxiv.org/html/2503.01151v1
- Crawl4AI fit markdown: https://docs.crawl4ai.com/core/fit-markdown/
- llms.txt na prática: https://www.digitalapplied.com/blog/llms-txt-in-practice-adoption-evidence-2026
- Firecrawl (lido em 2026-10-28): https://github.com/firecrawl/firecrawl ·
  https://github.com/firecrawl/firecrawl-mcp-server ·
  https://github.com/firecrawl/firecrawl-docs (features/research.mdx, use-cases/deep-research.mdx) ·
  https://github.com/firecrawl/cli (skills/firecrawl-research-index/SKILL.md)
- Issues públicas (coletadas em 2026-10-17): https://github.com/upstash/context7/issues, https://github.com/firecrawl/firecrawl/issues, https://github.com/unclecode/crawl4ai/issues e https://github.com/jina-ai/reader/issues
