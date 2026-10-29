# Frente J: como a documentação chega melhor aos agentes de código (evidência 2025–2026) e o que isso implica para o produto e o benchmark do NINA

Pesquisa de 2026-10-29, rodada 4.

**Arquivos do NINA inspecionados:** `app/mcp_server/server.py`, `app/retrieve/render.py`, `skills/nina-docs/SKILL.md`, `bench/PREREG.md` (§3, §4, Apêndice B, Emendas 15 a 19), `docs/SPEC.md` (tipos aceitos, `llms.txt`), `docs/CONCORRENTES.md` §8, `docs/pesquisa/rodada3/D_agentes_mcp.md` e `A_concorrentes.md`. Estes dois últimos não são repetidos aqui. D1 a D11 e L1 a L13 continuam valendo.

**Rede deste ambiente.**
- Bloqueados: vercel.com, nextjs.org, unpkg, jsdelivr, mintlify.com, context7.com, trynia.ai, arxiv.org e developers.openai.com.
- Funcionaram:
  - `git clone` de repositórios públicos do GitHub e `raw.githubusercontent.com`;
  - `registry.npmjs.org`, de onde baixei o tarball do `next@16.4.3` e li a documentação embutida no pacote;
  - `code.claude.com`.
- O que veio só de resumo de busca está marcado "só resumo".

**Achado principal.** A Vercel publica o harness (`vercel/next-evals-oss`, MIT) **com as transcrições de todas as execuções** (1,7 GB, 3.022 sessões de agente). Escrevi um script de análise (`research4/J_scripts/an3.py`), que não executa nada baixado. Ele mediu o que nenhum post publicou: quantas vezes o agente abre a documentação, como lê e quanto lê. As seções J1, J2 e J5 se apoiam nesses números próprios.

Legenda de custo:
- **zero:** muda `docs/`, `skills/`, `bench/` sem rodada paga, ou é só análise.
- **app/:** mexe no código congelado pela Emenda 15. Só entra num recongelamento com emenda ou depois do M10.
- **paga:** precisa de chamadas de modelo. A decisão é do humano.

---

## 1. Lições acionáveis, por impacto esperado

### J1. Fora do benchmark, o que mais pesa é o agente chegar a consultar a documentação, não a qualidade do trecho. Um bloco curto e sempre carregado no AGENTS.md vence a skill sob demanda (produto; impacto muito alto)

**O que a fonte mostra.**

*Vercel, "AGENTS.md outperforms skills in our agent evals" (jan/2026; post só resumo, código lido):*
- Na versão de janeiro da suíte (APIs do Next.js 16 ausentes do treino), os resultados foram:
  - sem docs: 53%;
  - skill com o comportamento padrão: 53%, porque **em 56% dos casos a skill nem foi acionada**;
  - skill com instrução explícita para usá-la: até 79%, e "frágil", porque mudanças pequenas de redação mudavam muito o resultado;
  - índice comprimido de ~8 KB dentro do AGENTS.md: 100%.
- O codemod `agents-md` (`packages/next-codemod/lib/agents-md.ts`, lido na íntegra) gera esse índice:
  - formato: uma linha separada por `|`, com `[Next.js Docs Index]|root: ./.next-docs|STOP. What you remember about Next.js is WRONG for this project. Always search docs and read before any task.|<dir>:{a.mdx,b.mdx,...}|...`;
  - só traz **caminhos de arquivo, sem resumo**, apontando para a documentação da versão instalada, baixada para `.next-docs/`.

*O desenho mudou depois disso (documentação embutida no `next@16.4.3`, lida no tarball):*
- Desde o 16.2, o pacote traz a documentação da versão em `node_modules/next/dist/docs/`: 474 arquivos `.md`, 3,4 MB, mediana de 3,4 KB por página e p90 de 20 KB.
- Desde o 16.3, o `next dev` detecta o agente e escreve no AGENTS.md um bloco gerenciado (`generate-agent-files.js`, lido) com **dois parágrafos e nenhum índice**. O bloco diz "This is NOT the Next.js you know… Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices."
- O guia oficial "How Next.js supports AI coding agents" afirma: "Framework knowledge comes from the bundled docs, not from Skills. Benchmark results show that always-available context outperforms on-demand retrieval." As skills ficaram para fluxos de trabalho, não para consulta.

*Placar público atual (`agent-results.json`, exportado em 2026-10-16), minha conta:*
- 37 pares modelo×harness (Claude Code, Codex, Cursor, Gemini CLI, OpenCode), 31 tarefas, pass@4 com parada no primeiro acerto.
- O braço com o bloco de dois parágrafos ganha **+9,9 pontos em média** (mediana +10, de 0 a +29) e **nunca perde**.
- Os maiores ganhos aparecem nos modelos mais fracos ou mais antigos:
  - Sonnet 4.6: 45 → 74;
  - Kimi K2.5: 16 → 45.
- Os modelos de topo já saturam: Opus 5.5 high e GPT-6 Sol ficam em 97 → 97.

*Minha análise das transcrições, por execução e não por pass@4:*

| Medida | Sem o bloco | Com o bloco |
|---|---|---|
| Sucesso por execução | 48,0% (812 de 1.691) | 70,3% (936 de 1.331) |
| Execuções que tocaram a documentação local | 12% | 82% |

- **A documentação estava instalada nas duas condições** (`next@canary` em ambas; confira `experiments/*.ts`). A única diferença é o parágrafo que aponta para ela.
- Logo, praticamente todo o ganho vem de **acionamento**, não de conteúdo novo.

*Context7 (repositório lido):* chegou à mesma conclusão por outro caminho. O `ctx7 setup` escreve, além da configuração MCP, uma **seção de regra** em AGENTS.md, GEMINI.md e CLAUDE.md (`packages/cli/README.md`, CHANGELOG 0.5.16 e 0.8.1, `rules/context7-mcp.md`).

*Contrapeso, ETH Zürich, "Evaluating AGENTS.md" (arXiv 2602.11988, fev/2026; só resumo):* em 138 tarefas Python (AGENTbench), arquivos de contexto gerados por LLM ou escritos por humanos **reduziram** o sucesso e aumentaram o custo em mais de 20%. A explicação dos autores é que exigências desnecessárias tornam a tarefa mais difícil. Conclusão: o bloco precisa ser mínimo.

**Por que importa para o NINA.**
- A distribuição atual do NINA é exatamente o modo que perdeu: uma skill sob demanda (`skills/nina-docs/SKILL.md`) mais instruções do servidor MCP (`INSTRUCTIONS` em `server.py`).
- Pelos números da Vercel, uma skill sem empurrão fica igual ao baseline.
- O D7 da rodada 3 já desconfiava disso: referência de API é 1,3% das leituras dos agentes. Agora há números causais, de um harness público e com transcrições.

**Proposta.**
1. **`nina init` (ou um trecho pronto no README e na skill).** Escreve no AGENTS.md (com `@AGENTS.md` no CLAUDE.md e no GEMINI.md) um bloco entre marcadores `<!-- BEGIN:nina -->` e `<!-- END:nina -->`, idempotente, preservando o resto do arquivo, como fazem o Next.js e o Context7.
2. **Conteúdo de 3 a 5 linhas:**
   - quando usar (pergunta que depende de documentação pública, de versão nova ou de um site que o agente não conhece);
   - a ferramenta (`assemble_context` com as URLs);
   - uma linha do tipo "sua memória sobre a API X pode estar desatualizada".
   - Sem regras de estilo, sem lista longa: o AGENTbench mostra que o excesso piora.
3. **Medir o acionamento** antes de qualquer M10 pago, com o teste barato do D7 (`claude plugin eval` ou um script equivalente) em três condições: sem nada, só a skill, e skill com o bloco no AGENTS.md. Mede-se só se o `assemble_context` foi chamado.

**Custo:** (1) e (2) são zero: um script fora do `app/`, mais o README e a skill. (3) é paga, mas pequena (centavos a poucos dólares).

**Evidência:** forte, porque o harness e as transcrições são públicos (MIT), os 37 modelos dão o mesmo sinal e a contagem é minha. Limitações: as tarefas são de um único framework (Next.js), há só 31 tarefas, e o próprio fornecedor escolheu tarefas de APIs novas.

### J2. O M10 não tem o braço que venceu na prática, "documentação local em arquivos + Read/Grep/Glob", e por desenho não mede acionamento (benchmark; validade externa; alto)

**O que a fonte mostra.**
- O braço vencedor da Vercel não é RAG nem resumo. São **arquivos Markdown no disco que o agente lê com as próprias ferramentas** (Read, Grep, Glob, `cat`).
- Nas transcrições do braço com o bloco, as chamadas que tocaram `dist/docs` foram 3.519 de leitura de arquivo, 2.357 de listagem e 1.564 de grep. **O agente lê páginas inteiras, não trechos.**
- O próprio MCP da Vercel desistiu de servir documentação. Pelo guia `mcp.md` embutido, a ferramenta `nextjs_docs` do `next-devtools-mcp` "doesn't do the work itself": só aponta o agente para `node_modules/next/dist/docs/`.
- É o mesmo "grep, não RAG" que o Claude Code adota no código.

**Por que importa.**
- O PREREG §3 tem `full_page` (`fetch_page` devolve a página inteira em Markdown), `strong_rag`, `nina`, `webfetch_live` e `context7_live`.
- Nenhum braço dá ao agente **o corpus como arquivos com grep**. É um comparador barato (sem embeddings nem reranker) e é o que um fornecedor grande publicou como melhor.
- O `full_page` chega perto, mas obriga a ler a página inteira, sem grep dentro dela nem lista do que existe.
- O Apêndice B manda "Read the documentation with your tools before answering" e entrega as URLs na tarefa. O M10 mede então **a qualidade condicionada ao acionamento**, que pelos dados do J1 é o fator menor no uso real.
- Isso não invalida o M10, mas restringe a conclusão: o M10 não pode afirmar "o NINA ajuda mais o agente do que um AGENTS.md".

**Proposta.**
- (a) Agora, sem custo: uma emenda que registre duas limitações de validade externa:
  - o acionamento é forçado pelo prompt;
  - não há braço de documentação local com grep.
- (b) Opcional e pago, decidido antes do piloto: um braço `local_docs`.
  - Os snapshots de `bench/corpus/` das URLs da tarefa vão para o diretório temporário da sessão, como `.md`, convertidos pelo mesmo trafilatura do `full_page`.
  - Ferramentas: `Read`, `Grep` e `Glob`, restritas a esse diretório.
  - Mesmo prompt; a frase "listed pages" vira "the files in ./docs".
  - Mesmo juiz e mesma métrica CPT.
  - É de 6 a 7 vezes mais barato de construir do que o `strong_rag` e é o adversário mais honesto para a tese do NINA, que é economizar tokens em páginas grandes.
- (c) Depois do M10: um braço "M10 sem acionamento forçado" (o prompt não manda usar ferramenta; o NINA entra só com a skill ou com o bloco do J1). Mede o produto como o usuário o recebe.

**Custo:** (a) zero; (b) bench/ com emenda e paga; (c) paga. **Evidência:** código e transcrições primários. O ganho relativo de `local_docs` contra o `nina` em perguntas do Stack Overflow é desconhecido; é exatamente o que falta medir.

### J3. Índice comprimido do site e das dependências: a peça que falta ao NINA é saber **quais URLs** passar, e o formato que venceu cabe em ~8 KB (produto; alto)

**O que a fonte mostra.**
- O índice de 8 KB da Vercel é só a lista de caminhos agrupada por diretório. O agente usa os nomes dos arquivos como mapa e lê o que precisa.
- Docs7, a plataforma de documentação do Context7 (`docs/docs7/agent-readiness.mdx`, lido), publica para cada site:
  - `/llms.txt`, com páginas da navegação primeiro, cada uma com URL `.md` e descrição;
  - `/llms-full.txt`;
  - `/<page>.md`;
  - `/ask?q=`, uma busca por site que devolve páginas, títulos e trechos em JSON;
  - `/.well-known/agent-skills/index.json`.
- O Next.js publica `/docs/llms.txt`, `.md` em toda página e `Accept: text/markdown` (guia embutido).

**Por que importa.**
- O contrato do NINA (`assemble(question, urls[1-20])`) supõe que o agente já tem as URLs certas. No M10, quem dá as URLs é a tarefa.
- No uso real, o agente adivinha a URL ou faz uma busca na web antes. Isso custa turnos, e as URLs inventadas viram `source_failed`.
- O Context7 resolve com `resolve-library-id` sobre um índice próprio pré-construído. A Vercel resolve com o índice no AGENTS.md.
- O NINA não tem nem um nem outro. Hoje ele já lê `llms.txt` como Markdown (SPEC §"Tipos aceitos"), mas trata o arquivo como página comum, não como mapa.

**Proposta, depois do M10, em ordem de custo.**
1. **Índice do site como ferramenta somente leitura** (`site_outline(url)` ou `resolve_url(url, format="outline")`).
   - Busca o `llms.txt` da raiz do site, passando pelo guard e pelo robots. Se não houver, usa o índice que o próprio NINA tem em `/v1/documents`, os títulos e âncoras da página pedida, ou o `sitemap.xml`.
   - Devolve só caminhos e títulos, comprimidos no estilo `dir:{a,b,c}`, com teto de ~2.000 tokens.
   - Não é crawler: lê **um** arquivo, uma vez, com cache.
2. **`nina index` para o projeto.** Lê `package.json`, `pyproject.toml` e afins, mapeia cada dependência para a raiz de documentação (`llms.txt` ou um registro curado pequeno) e escreve no bloco do J1 um índice de URLs **fixado na versão instalada**, como o codemod da Vercel faz com caminhos.
   - É determinístico, sem LLM. Respeita "sem crawler", porque lê só o índice publicado pelo autor.
3. **URL alternativa no `source_failed` 404.** O Docs7 devolve "smart suggestions" no 404, porque "agents often request URLs that do not exist" (adivinham pelo título, cortam um segmento, usam URL de versão antiga). O NINA pode pôr em `<nina_notes>` as 3 URLs do `llms.txt` do mesmo site mais próximas por caminho e título, por similaridade de caracteres e sem LLM. O texto vem do autor do site, então vai como URL, nunca como instrução.

**Custo:** app/ nas três. O item 2 pode nascer como script em `sdk/` sem tocar o `app/`. **Evidência:** desenho primário (código da Vercel e documentação do Docs7). O efeito do índice de URLs remotas, diferente de caminhos locais, **não foi medido por ninguém**. É hipótese a testar no braço (c) do J2.

### J4. Cada agente busca a web de um jeito, e o NINA vale mais onde o fetch nativo é pior: hoje o Codex, que o M10 não cobre (produto e posicionamento; médio-alto)

**O que a fonte mostra.**
- **Claude Code** ("WebFetch tool behavior", code.claude.com, lido):
  - converte para Markdown e, "for most fetches", roda o prompt num modelo separado; o Claude recebe o resultado, não a página. A própria documentação diz que é "lossy by design";
  - lê até 100.000 caracteres por chamada (desde a v2.1.290 avisa quanto ficou sem ler);
  - cache de 15 minutos;
  - envia `Accept` preferindo Markdown;
  - um conjunto de domínios de documentação pré-aprovados busca sem pedir permissão.
- **Gemini CLI** (`packages/core/src/tools/web-fetch.ts`, lido):
  - caminho principal: `urlContext` da API Gemini, com até 20 URLs;
  - fallback: `html-to-text`, que **descarta links e imagens**, com teto de 250.000 caracteres dividido entre as URLs por water-filling;
  - depois, uma chamada ao Gemini resume segundo o prompt do usuário;
  - limite de 10 requisições por minuto por host.
- **Codex** (agent-ecosystem-testing, `codex-test-findings-desktop.md`, lido; 261 execuções, 13 URLs):
  - a ferramenta `web` devolve uma janela de texto (`wordlim: 200`) que raramente começa na linha 0;
  - em 69% das execuções o agente escala para `curl`, com HTML cru de média de **88 mil tokens** (máximo de 835 mil);
  - páginas renderizadas por JavaScript chegam sem o texto;
  - 75% relatam truncamento de algum tipo, e os agentes "rarely distinguished between having fetched a URL and having read it".

**Por que importa.**
- O M10 roda só o Claude Code com Sonnet. É o agente cujo fetch nativo é o mais bem resolvido dos três.
- No Codex, o NINA substitui um `curl` de ~88 mil tokens por um recorte de 4.000, com JavaScript renderizado (Playwright) e com proveniência.
- O ganho de custo esperado é muito maior ali, mas não há número.
- Nada disso muda o M10. Muda onde o NINA deve ser lançado primeiro e o que medir depois.

**Proposta.**
- (1) Agora, sem custo: guia de instalação do MCP do NINA para Codex e Gemini CLI na skill e no README.
- (2) Depois do M10: um piloto pequeno com o Codex (mesmas tarefas, braços `nina` × fetch nativo), contando tokens de resultado de ferramenta. É pago; pode rodar com a assinatura do humano na máquina dele, se ele aceitar.

**Custo:** (1) zero; (2) paga. **Evidência:** código do Gemini CLI e documentação do Claude Code primários. Os números do Codex vêm de medições independentes publicadas no GitHub, mas parte da interpretação foi feita por GPT, como a própria autora avisa.

### J5. Quanto o agente consome quando lê à vontade: ~7 mil tokens de documentação por tarefa, quase sempre páginas inteiras e pequenas. O ganho do recorte depende do tamanho da página (benchmark e produto; médio-alto)

**O que a fonte mostra (minha análise das transcrições da Vercel).**
- Nos modelos Claude do braço com o bloco, os resultados de ferramenta que vieram da documentação somam por execução: mediana de **27 mil caracteres** (~7 mil tokens), p25 de 14,5 mil e p75 de 45 mil.
- As páginas do Next.js são pequenas: mediana de 3,4 KB, ou ~850 tokens. O agente lê algumas inteiras.
- O ganho de +22 pontos por execução (J1) veio **sem nenhum recorte**.

**Por que importa.**
- O orçamento padrão do NINA (4.000 tokens) está na mesma ordem do que um agente lê sozinho num site de páginas pequenas.
- Nesse regime, o argumento "NINA economiza 10 vezes" da skill ("a whole page is about 10 times the tokens") não vale.
- O argumento vale onde as páginas são grandes: referência de API monolítica, PDF, RFC. No corpus do M8, o `resolve_url` dava ~45 mil tokens em média.
- Se o M10 for publicado sem estratificar por tamanho de página, uma vitória em páginas grandes e um empate em páginas pequenas viram uma média que esconde as duas coisas.

**Proposta.**
- (a) Sem custo: análise secundária registrada no PREREG, estratificando CPT e acerto por tamanho das páginas da tarefa (tokens do `resolve`, que o NINA já guarda: `assemble.py:118`). Por exemplo, terços ou o corte de 8 mil tokens.
- (b) Produto, depois do M10: quando a página inteira cabe na fatia do orçamento, devolvê-la inteira com `excerpt="whole page"`. O agente lê a página completa pelo mesmo preço e não precisa de um segundo `resolve_url`.
- (c) Ajustar a frase da skill "about 10 times" para "on long pages", para não prometer o que não vale em páginas curtas.

**Custo:** (a) zero; (b) app/; (c) zero. **Evidência:** contagem própria sobre transcrições públicas. A razão de ~4 caracteres por token é estimativa.

### J6. Versão fixada na dependência instalada é a variável que mais rende, e o NINA só a trata de forma passiva (produto; médio)

**O que a fonte mostra.**
- Toda a estratégia da Vercel gira em torno de "documentation that matches the installed framework version":
  - a documentação vai dentro do pacote;
  - o `next upgrade` atualiza a documentação;
  - o bloco reescreve a si mesmo quando a versão muda.
- O Context7 aceita `/org/project/vX.Y.Z`. Os exemplos da 0.6.1 usam `/vercel/next.js/v16.2.9`.
- As tarefas em que o bloco mais ajudou são justamente APIs que mudaram, segundo o `newlyPassed` do placar:
  - `agent-031-proxy-middleware`: o middleware virou `proxy` no Next 16;
  - `agent-037-updatetag-cache`;
  - `agent-039-indirect-proxy`.

**Por que importa.**
- O NINA avisa `mixed_versions` e mostra `date`, mas não sabe que versão o projeto do usuário usa.
- Sem isso, ele pode montar o melhor recorte da página da versão errada.

**Proposta, depois do M10.** Parâmetro opcional `version` (ou `package@version`) no `assemble_context`. Ele não troca URL nenhuma, mas:
- (i) prefere fragmentos cujo documento ou seção declara aquela versão (o experimento U2 já existe);
- (ii) emite `version_mismatch` em `<nina_notes>` quando nenhum documento casa.

O `nina index` do J3 é quem saberia a versão. Junto com o D8 (aviso `deprecated`), cobre o caso que mais quebra agentes.

**Custo:** app/. **Evidência:** desenho primário de dois fornecedores e o placar público. O efeito específico no NINA não foi medido.

### J7. Harnesses públicos de tarefa de código reaproveitáveis, e o cuidado com números de fornecedor (benchmark; médio)

**O que a fonte mostra.**
- **`vercel/next-evals-oss` (MIT) com `@vercel/agent-eval`:**
  - ~31 a 50 tarefas Next.js com testes ocultos (`EVAL.ts`);
  - experimentos declarados em TypeScript (o braço com AGENTS.md é um `setup` que escreve dois arquivos);
  - pass@4 com parada antecipada;
  - exige Vercel Sandbox e chaves de modelo.
- **`supabase/evals` (Apache-2.0):**
  - suíte `benchmark` de 69 tarefas, com e sem skills;
  - na minha conta, a skill dá **+4,6 pontos em média**, de −1,4 (Opus 5.5) a +11,6 (GPT-6 Luna e Grok 4.7), com uma execução por par;
  - suíte `docs`, que **avalia a página de documentação, não o agente**: o prompt traz uma URL `.md` da documentação do Supabase e a tarefa, e o scorer verifica se a página levou o agente ao padrão certo. De 12 páginas, 4 levaram a 0% de acerto (RLS, chaves de API, conexão com Postgres, segredos de edge function).
- **Nia** (só resumo): +22 pontos (58 → 80) com Claude Code numa **cópia modificada** da suíte da Vercel, além de uma taxa de alucinação de 52,1% contra 63,4% do Context7. Sem harness público, desenho próprio, fornecedor interessado.

**Por que importa.**
- O M10 mede perguntas e respostas do Stack Overflow com juiz LLM.
- Os fornecedores publicam tarefa de código com teste executável, que dispensa juiz.
- Para o NINA dizer "no nível dos melhores", um dia vai precisar de um resultado nesse formato, que o mercado já entende.
- A suíte `docs` do Supabase é um molde de vitrine para o NINA: a mesma página, entregue crua, pelo WebFetch ou pelo NINA, mede se o recorte preserva o que importa.

**Proposta.**
- (1) Agora, sem custo: registrar no ROADMAP, depois do M10, um "M10-code" exploratório com a suíte da Vercel em três braços: base, bloco AGENTS.md e NINA via MCP com as URLs `.md` de nextjs.org.
  - O M10-code roda localmente com Docker em vez do Vercel Sandbox, se o `agent-eval` permitir. Não conferi; é preciso olhar `packages/agent-eval/src/lib/sandbox*`.
- (2) Não citar os números da Nia como referência.
- (3) As transcrições públicas da Vercel já servem para calibrar o M10 de graça: turnos por tarefa e tokens de documentação por tarefa (J5).

**Custo:** (1) e (3) zero para planejar; rodar é pago. **Evidência:** repositórios e dados primários. Os números da Nia vêm só de resumo.

### J8. O lado do site melhora depressa: dois terços do tráfego de documentação já são agentes, e os sites servem Markdown, índice e busca. O fosso do NINA migra para seleção, proveniência e cauda longa (posicionamento; médio)

**O que a fonte mostra (só resumos, salvo o Docs7).**
- **Mintlify, "State of Knowledge 2026":**
  - agentes eram 15,2% do tráfego dos sites hospedados no início de 2026 e 66% em julho;
  - em agosto, foram 257 milhões de requisições de agentes contra 131 milhões de páginas vistas por humanos;
  - os números variam entre as páginas do próprio relatório (61,9% a 66,9%). É dado de fornecedor, só dos sites dele.
- **Cloudflare "Markdown for Agents":** o famoso "−80% de tokens" é **um exemplo único** (16.180 → 3.150 tokens no próprio blog), não telemetria agregada.
- **Docs7** (lido): `/ask?q=`, feedback do agente por GET e conteúdo `<Visibility for="agents">` que o humano não vê.
- **Scores de "agent readiness":** Agent Score da Fern e score da Mintlify; o Supabase tira de 72 a 75 em 100. Avaliam presença de `llms.txt`, suporte a `.md` e tamanho das páginas.

**Por que importa.**
- Em sites modernos (Mintlify, GitBook, Docs7, Next.js), boa parte da vantagem de extração do NINA some, porque o site já entrega Markdown limpo, coisa que a rodada 3 (C_extracao) já notou.
- O diferencial do NINA nesses sites fica na seleção sob orçamento com proveniência e nos avisos.
- Nos sites legados, em PDFs, RFCs e páginas pesadas em JavaScript, o NINA mantém também a vantagem de extração.
- Um risco novo, de segurança: conteúdo "só para agentes" é um canal que o humano não audita. O NINA já trata a página como dado não confiável (`render.py`, `neutralize`), e isso fica mais importante.

**Proposta.**
- Posicionar o NINA para a cauda longa (documentação que não vem no pacote nem tem MCP próprio) e para páginas grandes.
- Na extração, seguir o L5 da rodada 3 (pedir `.md` ao site).
- Nada novo no `app/` por causa desta lição.

**Custo:** zero. **Evidência:** fraca a média (relatórios de fornecedor).

---

## 2. Onde o NINA já está igual ou melhor

- **Avisos explícitos contra truncamento silencioso.**
  - Codex: 75% de truncamento, mal relatado.
  - Gemini CLI: corta em 250 mil caracteres com uma linha de aviso e depois resume.
  - WebFetch: resume com modelo pequeno ("lossy by design").
  - O NINA avisa `budget_limited`, `low_coverage` e `source_failed` e diz no atributo `excerpt` quanto da página entregou. Nenhuma das três ferramentas nativas faz isso de forma confiável.
- **Seleção determinística e literal.** WebFetch e Gemini CLI passam a página por um LLM antes do agente, e o que chega depende do prompt de extração. O NINA entrega texto literal com `chunk_id` e página, o que permite citar e recuperar depois da compactação (D4).
- **Páginas com JavaScript.** O `curl` do Codex devolve só a casca do app. O NINA renderiza com Playwright atrás do guard.
- **Links preservados.** O fallback do Gemini CLI apaga os links (`ignoreHref`). O NINA mantém links e código no Markdown.
- **Tamanho de resposta.** Os 4.000 tokens padrão estão na faixa do que o agente consome sozinho com documentação local (J5) e muito abaixo dos 88 mil do `curl` do Codex.
- **`llms.txt` aceito como Markdown** (SPEC). Falta só tratá-lo como mapa (J3).

## 3. Onde o NINA está atrás

- **Acionamento (J1).** Só skill e instruções do servidor; nenhum bloco sempre carregado. A Vercel mostra que a skill sozinha fica igual ao baseline, e a Context7 já escreve regra no AGENTS.md.
- **Descoberta de URL (J3).** Não há índice do site, índice das dependências nem sugestão no 404. O Context7 tem o índice pré-construído; a Vercel tem o índice local.
- **Versão do projeto (J6).** O NINA não sabe que versão o usuário usa. A Vercel entrega a documentação dentro do pacote; a Context7 aceita `/vX.Y.Z`.
- **Evidência em tarefa de código executável (J7).** O mercado publica pass@k com teste oculto e harness aberto. O NINA tem só perguntas e respostas com juiz.
- **Cobertura de agentes (J4).** O M10 mede só o Claude Code, onde o fetch nativo é o melhor dos três.
- **Braço de documentação local com grep (J2).** É o adversário mais barato e, segundo o maior estudo público, o mais forte. O M10 não o tem.

## 4. Fontes lidas na íntegra

- Vercel, harness e dados das avaliações Next.js (README, `experiments/*.ts`, `agent-results.json`, transcrições em `results/`, analisadas por script próprio): https://github.com/vercel/next-evals-oss
- Vercel, `@vercel/agent-eval` (`packages/agent-eval/src/lib/config.ts`, `runner.ts`): https://github.com/vercel-labs/agent-eval
- Next.js, codemod `agents-md` (formato do índice de 8 KB): https://raw.githubusercontent.com/vercel/next.js/canary/packages/next-codemod/lib/agents-md.ts
- Next.js, `evals/eval.config.json` e `PROMPT.md` de `agent-031` e `agent-037`: https://github.com/vercel/next.js/tree/canary/evals
- Next.js 16.4.3, pacote npm (https://registry.npmjs.org/next/-/next-16.4.3.tgz): `dist/docs/01-app/02-guides/ai-agents.md`, `dist/docs/01-app/02-guides/mcp.md`, `dist/docs/01-app/03-api-reference/05-config/01-next-config-js/agentRules.md` e `dist/server/lib/generate-agent-files.js`
- Context7 (repositório): `rules/context7-mcp.md`, `packages/cli/README.md` e `CHANGELOG.md`, `docs/docs7/agent-readiness.mdx`, `docs/docs7/analytics.mdx`, `docs/howto/rules.mdx`, `docs/enterprise.mdx`: https://github.com/upstash/context7
- Supabase Evals (README, `experiments/presets.ts`, `evals/docs/build-docs-002-rls-guide/{PROMPT.md,README.md,EVAL.ts}`, `apps/web/src/data/eval-results.json` e `docs-eval-results.json`): https://github.com/supabase/evals
- Claude Code, "WebFetch tool behavior": https://code.claude.com/docs/en/tools-reference.md · permissões: https://code.claude.com/docs/en/permissions.md
- Gemini CLI, `web-fetch.ts`: https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/core/src/tools/web-fetch.ts
- Agent Ecosystem Testing, Codex desktop (limites e janelas de 261 execuções): https://github.com/rhyannonjoy/agent-ecosystem-testing/blob/main/docs/open-ai-codex/codex-test-findings-desktop.md

## 5. Fontes inacessíveis que valem a leitura completa (salvar em PDF)

1. **Vercel, "AGENTS.md outperforms skills in our agent evals"** (jan/2026): https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals. Desenho da suíte de janeiro (número de tarefas, quais APIs), a comparação de 40 KB contra 8 KB (só resumo secundário) e a redação exata que deixou a skill "frágil". Base do J1.
2. **Next.js evals, placar com metodologia**: https://nextjs.org/evals. Como o site apresenta o "docs impact" e se há custo por braço. Confirma a leitura do J1.
3. **Next.js 16.2 e 16.3, posts de IA**: https://nextjs.org/blog/next-16-2-ai e https://nextjs.org/blog/next-16-3-ai-improvements. A justificativa da troca do índice comprimido pelo bloco de dois parágrafos, que é o dado central do J1 e do J3.
4. **Gloaguen et al., "Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?"** (arXiv 2602.11988): https://arxiv.org/pdf/2602.11988. Os números por condição (gerado por LLM contra escrito por humano) definem quão curto deve ser o bloco do J1.
5. **Nia, "How Claude Code + Nia Achieved 38% Higher Pass Rate on Next.js Benchmarks"**: https://trynia.ai/blog/claude-code-nia-nextjs-benchmark. Que modificações fizeram na suíte da Vercel; serve para não repetir o mesmo vício num M10-code.
6. **Mintlify, "The 2026 State of Knowledge Report"**: https://www.mintlify.com/state-of-knowledge/2026 (há versão `.md` e PDF em https://www.mintlify.com/new/knowledge-report/state-of-knowledge-report-2026.pdf). A metodologia de contagem de agentes contra humanos e a parte sobre quais agentes e formatos dominam.
7. **Lance Martin, "Vibe code benchmark"** (abr/2025): https://rlancemartin.github.io/2025/04/03/vibe-code. O único teste publicado que compara `llms.txt`, `llms.txt` otimizado, banco vetorial e contexto inteiro com o mesmo agente; é o desenho mais próximo de um braço "índice" para o M10.
