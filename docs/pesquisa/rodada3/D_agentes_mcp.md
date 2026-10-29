# Frente D: como os agentes usam ferramentas de documentação e como projetar o servidor MCP do NINA

Pesquisa de 2026-10-28, rodada 3. Arquivos do NINA inspecionados: `app/mcp_server/server.py`,
`skills/nina-docs/SKILL.md`, `app/retrieve/render.py`, `app/retrieve/hints.py`,
`bench/m10_tools.py`, `bench/m10_agent.py`, `bench/m10_result_limits.py`,
`bench/PREREG.md` (Emendas 14 e 15), `DECISIONS.md`.

Rede deste ambiente: só GitHub, anthropic.com, claude.com, platform.claude.com e
code.claude.com responderam. arxiv.org, cursor.com, modelcontextprotocol.io,
blog.cloudflare.com, developers.openai.com e as páginas dos estudos independentes
estão bloqueados. Tudo o que vem desses domínios está marcado como "só resumo".

Legenda de custo:
- **zero**: muda só `docs/`, `skills/` ou `bench/` sem rodada paga. O M10 não carrega a skill (conferi `bench/`: não há referência a `nina-docs`).
- **app/**: muda o `app/`, que a Emenda 15 congelou. Entra só num recongelamento com emenda, ou depois do M10.
- **paga**: exige chamadas de modelo.

---

## 1. Lições acionáveis, por impacto esperado

### D1. Assimetria no M10: o `assemble_context` não declara o limite de tamanho que o `search_docs` declara (validade; alto)

**O que a fonte mostra.** A documentação do Claude Code (MCP, "MCP output limits",
lida na íntegra) diz que todo resultado MCP de texto acima de 50.000 caracteres vai
para um arquivo, a menos que a ferramenta declare `_meta["anthropic/maxResultSizeChars"]`.
O `bench/m10_tools.py` declara 500.000 no `fetch_page` e no `search_docs`
(DECISIONS.md, Emenda 12). O `assemble_context` do servidor congelado
(`app/mcp_server/server.py`) não declara nada. A Emenda 14 tratou só de
`resolve_url` e `get_document`.

**Por que importa para o NINA.**
- O `max_tokens` do `assemble_context` vai até 32.000.
- O hint `budget_limited` (`hints.py`) sugere `min(2 × orçamento, 32000)`. Isso leva a 8.000 e depois a 16.000.
- Com cerca de 3 a 4 caracteres por token, o texto conciso passa de 50 mil caracteres entre ~12,5 mil e ~16,7 mil tokens.
- Nesse ponto o braço `nina` recebe um aviso de arquivo que não consegue ler, porque nenhum braço tem ferramenta de leitura de arquivo.
- O `strong_rag`, com o mesmo pedido, recebe o texto inteiro. É um defeito do harness contra o NINA, simétrico ao C1 que já foi corrigido a favor do `full_page`.

**Proposta.**
- (a) Agora, sem custo: medir no dev a razão caracteres/token do formato conciso e contar quantas respostas de 16k e 32k passariam de 50 mil caracteres.
- (b) Escolher, numa emenda antes do piloto, entre duas saídas:
  - declarar `anthropic/maxResultSizeChars` no `assemble_context` (muda o `app/`, então recongela);
  - ou registrar a assimetria como limitação, com a contagem do item (a).
- No produto, a declaração também vale: no Claude Code real o arquivo é legível, mas custa um turno a mais.

**Custo:** (a) zero; (b) app/. **Evidência:** documentação primária lida na íntegra, mais o código do repo. A razão caracteres/token é estimativa minha e precisa da medição do item (a).

### D2. O concorrente mais forte não está no M10: o web fetch da própria Anthropic com filtragem dinâmica (estratégico; alto)

**O que a fonte mostra.**
- **Anthropic (claude.com, 2026-02-17, lido na íntegra):**
  - Os tools `web_search_20260209` e `web_fetch_20260209`, e as versões seguintes até `_20260318`, deixam o Claude escrever código que filtra a página antes de ela entrar no contexto.
  - Em BrowseComp e DeepsearchQA: +11% de acerto em média, com 24% menos tokens de entrada.
  - No BrowseComp, o Opus 4.6 foi de 45,3% para 61,6%.
  - O próprio texto cita "sifting through technical documentation" como caso de uso.
  - O custo ponderado por preço caiu no Sonnet 4.6 e **subiu** no Opus 4.6.
  - A filtragem vem ligada por padrão nos modelos 4.6 e posteriores.
- **GitHub Copilot:** a ferramenta `fetch_webpage` recebe `query` e `urls` e devolve trechos ranqueados por relevância, separados por `...`. A média foi de 7.313 tokens em 55 execuções (agent-ecosystem-testing, GitHub, lido na íntegra). É essencialmente um `assemble_context` embutido e grátis.
- **Windsurf:** devolve um índice de pedaços com resumos, e o agente lê cada pedaço com `view_content_chunk`.

**Por que importa.**
- O PREREG compara com o WebFetch do Claude Code (`webfetch_live`), que resume a página com o Haiku, e com o Context7.
- Ele não compara com a filtragem dinâmica nem com a extração por consulta do Copilot.
- A meta do humano ("no nível de qualquer empresa ou acima") não pode ser declarada contra a Anthropic sem esse braço.
- A tese do NINA (seleção determinística, barata e com proveniência) contra a filtragem por código (gerativa, sem proveniência, com custo variável) é exatamente o que precisa de número.

**Proposta.**
- (1) Agora, sem custo: registrar no PREREG/README que o M10 não cobre a filtragem dinâmica, para não declarar superioridade geral.
- (2) Depois do M10, se o humano aprovar o gasto: um braço exploratório pela API (`web_fetch_20260318` com e sem filtragem, mesmo modelo, mesmas tarefas e mesmo juiz). Ele mede custo por resposta certa (H2). O argumento de custo do NINA é mais forte justamente no Opus, onde a filtragem encareceu.

**Custo:** (1) zero; (2) paga. **Evidência:**
- Anthropic: post primário lido na íntegra, mas os números são do fornecedor e o harness interno não é público.
- Copilot: medições independentes publicadas no GitHub, lidas na íntegra; a ferramenta em si não tem documentação.

### D3. Anotações de ferramenta: o NINA não declara nenhuma, e isso custa paralelismo, nova tentativa e entrada no diretório (produto; alto, custo baixíssimo)

**O que a fonte mostra.**
- **Spec MCP 2026-07-28** (`schema.ts`, lido): `ToolAnnotations` tem `title`, `readOnlyHint`, `destructiveHint`, `idempotentHint` e `openWorldHint`.
- **Documentação do Claude Code** ("Mark tools that are safe to call twice"), com duas consequências de `readOnlyHint: true`:
  - o Claude Code roda as chamadas da ferramenta em paralelo com outras chamadas somente leitura;
  - pode repetir uma vez uma chamada pendente quando a conexão HTTP cai. Sem a anotação, a chamada falha com "connection closed before the tool call was answered".
- **Checklist do diretório de conectores da Anthropic** (claude.com/docs, lido): toda ferramenta **precisa** ter `title` e o hint aplicável. Sem isso, a submissão é rejeitada. Os hints decidem a autoaprovação: ferramentas somente leitura rodam sem confirmação a cada chamada.
- O guia `build-mcp-server` do plugin oficial `mcp-server-dev` (anthropics/claude-plugins-official, lido) repete a regra.

**Por que importa.**
- As três ferramentas do NINA só leem.
- Hoje cada chamada pede aprovação ao usuário (atrito real na adoção), não roda em paralelo com Read/Grep/WebFetch e não se recupera de queda de conexão.
- O SDK instalado (`mcp` 2.3.0) aceita `annotations=` e `title=` no `@server.tool` (conferido em `.venv/.../mcpserver/server.py`).

**Proposta.** Nas três ferramentas: `title` legível ("Answer from documentation pages", "Read a whole documentation page", "Stored documentation page"), `readOnlyHint=True` e `openWorldHint=True` (o NINA lê a web aberta). Teste unitário sobre o `tools/list`.

**Efeito no M10.** O paralelismo muda a latência, não os turnos nem os tokens. Ainda assim muda o que o agente do braço `nina` vê. Fazer no próximo recongelamento ou depois do M10, e conferir se as ferramentas MCP do `bench/m10_tools.py` ficam com as mesmas anotações (simetria).

**Custo:** app/. **Evidência:** spec e documentação primárias, lidas na íntegra.

### D4. Depois da compactação, os fragmentos do NINA viram resumo de LLM; a skill deve mandar citar os identificadores (produto; médio-alto, custo zero)

**O que a fonte mostra.** Pela documentação do Claude Code ("Explore the context window", "What survives compaction", lida na íntegra), a compactação faz isto com cada tipo de conteúdo:
- Arquivos lidos: até cinco são relidos do disco.
- Corpos de skill: reinjetados com teto de 5.000 tokens por skill, cortando o fim.
- Resultados de ferramentas MCP: entram no resumo da conversa e perdem o texto literal.

Outras duas fontes reforçam:
- A API faz `clear_tool_uses` com marcador no lugar do resultado (context editing, lido).
- O texto da Anthropic sobre Managed Agents (lido na íntegra) diz que descartar contexto de forma irreversível causa falhas e defende contexto recuperável fora da janela.

**Por que importa.**
- A promessa do NINA é evidência literal com origem. Depois da compactação, o agente fica só com o resumo.
- Se o agente tiver anotado `chunk_id` e URL, pode pedir de volta: chamar sem `exclude_chunk_ids` refaz a busca. A busca por id, proposta maior do P4, faria isso direto.

**Proposta (skill, fora do congelamento).** No passo 5 da `SKILL.md`, pedir que a resposta cite a URL **e o `chunk`** de cada trecho usado, para que o resumo da compactação guarde os identificadores. Manter a skill curta e o essencial no topo, porque a reinjeção corta o fim. Hoje são 46 linhas, então está bem.

**Custo:** zero. **Evidência:** documentação primária lida na íntegra. O efeito no acerto não foi medido.

### D5. Dizer ao agente que ele recebeu trechos, não a página, e quanto ficou de fora (produto; médio)

**O que a fonte mostra.**
- O agent-docs-spec (v0.6.0, 2026-09-13) e as medições de plataformas (agent-ecosystem-testing, ambos lidos no GitHub) concluem que os agentes se declaram completos quando o conteúdo está incompleto, ou atribuem o corte à causa errada.
- No Copilot, o modelo confunde as elisões `...` do ranqueamento com truncamento.
- No Windsurf, o agente diz ter lido tudo sem provar.
- O Claude API web fetch atribui ao JavaScript um corte que era de limite de caracteres.

**Por que importa.**
- O formato conciso (`render.py`) diz cobertura, tokens e fontes, e o `budget_limited` avisa quando o orçamento acabou.
- Ele não diz por documento que aquilo são N trechos de uma página de M seções e ~T tokens.
- Sem isso o agente não calibra quando vale pagar o `resolve_url` (~45 mil tokens em média) e pode tratar o recorte como a página inteira.

**Proposta.** No atributo do `<document>`, por exemplo, `excerpt="5 of 37 sections; page ~45k tokens"`, só com dados que o NINA já tem (IR e contagem de tokens). Sem texto da página, então não abre vetor de injeção.

**Custo:** app/, depois do M10. **Evidência:** medições independentes publicadas (GitHub), lidas na íntegra. O ganho no NINA é hipótese.

### D6. Não engordar as descrições: mais texto melhora pouco e custa muitos passos (produto; médio, decide o que NÃO fazer)

**O que a fonte mostra.**
- **"MCP Tool Descriptions Are Smelly!"** (arXiv 2602.14878, fev/2026; só resumo):
  - 856 ferramentas de 103 servidores; 97,1% com algum "smell" e 56% sem propósito claro;
  - completar todos os componentes da descrição subiu o sucesso em +5,85 pt (mediana), mas aumentou os passos em 67,46% e piorou 16,67% dos casos.
- **"From Docs to Descriptions"** (arXiv 2602.18914; só resumo): os smells de funcionalidade e precisão são os que mais afetam a escolha da ferramenta.
- **Documentação do Claude Code (limites de corte das descrições):**
  - instruções do servidor e descrições de ferramentas carregadas de início: 4.096 caracteres;
  - ferramentas carregadas pelo tool search: 16.384 caracteres;
  - a busca casa só com o trecho dentro do limite menor.

**Por que importa.** As descrições do NINA já dizem propósito, custo, quando usar a irmã e o que cada parâmetro faz, e estão bem abaixo dos limites. O estudo mostra que acrescentar mais (exemplos, limitações longas) tende a aumentar os passos, que é justamente o custo que o NINA vende.

**Proposta.** Congelar o tamanho atual. Mudança futura só com medição de passos e acerto. Uma limpeza barata: o MCP 2026-07-28 diz que as `instructions` "não devem duplicar as descrições", e o `INSTRUCTIONS` repete o aviso de conteúdo não confiável e a regra do `resolve_url`, que já estão nas descrições.

**Custo:** zero (decisão) / app/ (limpeza). **Evidência:** só resumos dos preprints; documentação do Claude Code lida.

### D7. A adoção depende de o agente chamar a ferramenta, e os agentes quase não consultam referência de API sozinhos (produto e posicionamento; médio)

**O que a fonte mostra (só resumos).**
- **Gao e Chen** (arXiv 2608.20195, ago/2026), 557 sessões do SWE-chat com 3.033 interações com documentação e 33.097 PRs de agentes (AIDev):
  - arquivos de instrução e notas do agente são 60,5% das leituras, documentação técnica 10,6% e referência de API 1,3%;
  - a consulta é 70,2% por iniciativa própria e 7,5% depois de falha;
  - consultar documentação se associa a **menos** teste logo em seguida (OR ajustado 0,39).
- **Borysenko** (arXiv 2604.02544): os agentes comprimem a navegação de várias páginas em uma ou duas requisições.
- **Marginlab (abr/2026):** Codex com GPT-5.4 no Terminal-Bench 2.0 com e sem Context7, "sem melhora significativa" (63/89 sem; ~64/89 com, pelo resumo).
- **mcpbr (Georgia Tech, fev/2026):** um MCP de grafo de código derrubou a resolução no SWE-bench Verified de 49,8% para 42,4%.

**Por que importa.**
- (a) Em benchmarks gerais de código, ferramentas de documentação raramente mudam o placar. O M10 usa perguntas do Stack Overflow, onde a documentação decide, e isso está certo. Não vale migrar para o Terminal-Bench como vitrine.
- (b) Uma ferramenta MCP pode piorar o agente, o que reforça o desenho de não inferioridade (H1).
- (c) No produto, quem decide se o NINA é usado é a skill, as instruções do servidor e o momento da chamada, não a qualidade da busca.

**Proposta.**
- Antes de qualquer M10 pago, um teste de acionamento barato: o `claude plugin eval` (documentação do Claude Code, lida) roda casos com e sem o plugin, três vezes cada, e informa o custo. Isso exige empacotar NINA, skill e servidor como plugin (D9).
- Casos: perguntas que **devem** acionar o `assemble_context` (URL de documentação dada) e casos que **não devem**.
- Mede-se só se a ferramenta foi chamada. É um custo de centavos a poucos dólares, mas é **pago**: decisão do humano.

**Custo:** paga (pequena). **Evidência:** só resumos (arXiv bloqueado); a documentação do plugin eval foi lida na íntegra.

### D8. Recorte por versão e API obsoleta: evidência nova reforça um aviso determinístico de "deprecated" (produto; médio)

**O que a fonte mostra (só resumos).**
- **Wang et al.** (ICSE 2025), 7 LLMs, 145 mapeamentos de API e 28.125 prompts:
  - os modelos usam APIs obsoletas;
  - trocar a chamada e regerar (ReplaceAPI) corrige mais de 85%;
  - **só pedir no prompt** que use a API nova (InsertPrompt) não é eficaz o bastante.
- **GitChameleon 2.0:** 48 a 51% de acerto, 59% com recuperação.
- **Spracklen et al.** (USENIX Security 2025): pacotes inventados em ≥5,2% das amostras de modelos comerciais e 21,7% dos abertos; 43% se repetem sempre, o que viabiliza o slopsquatting.
- **Twist et al.** (arXiv 2509.22202): erros de digitação do usuário e nomes falsos de bibliotecas induzem alucinação.

**Por que importa.**
- Mandar o agente "usar a versão certa" funciona mal; o que funciona é pôr o trecho certo no contexto.
- O NINA já avisa `mixed_versions` e tem o experimento U2 de preferência por versão.
- Falta sinalizar quando o **próprio fragmento** diz que algo é obsoleto, por exemplo os blocos "Deprecated since version X" do Sphinx, a classe `.deprecated` e as notas de remoção do MDN. O texto vem da página, então o aviso cita o fato estrutural e não segue instrução.

**Proposta.** Atributo `deprecated="true"` no `<fragment>` quando a IR marcar o bloco, detectado de forma determinística por classe ou padrão de admonição. Medir no dev, nas tarefas do tipo `version`, se o atributo aparece onde a resposta aceita fala de obsolescência.

**Custo:** app/, depois do M10; a medição no dev é zero. **Evidência:** só resumos.

### D9. Distribuição: plugin do Claude Code, skill servida pelo MCP, registro e cartões de servidor (produto; médio-baixo agora, alto no lançamento)

**O que a fonte mostra.**
- **Plugins do Claude Code (documentação lida):** empacotam servidor MCP e skill numa instalação. O diretório da Anthropic aceita plugins e conectores (checklist lido).
- **SEP-2640 "Skills Extension"** (Final, 2026-04-23, lido no repositório da spec):
  - serve skills por MCP como resources `skill://<nome>/SKILL.md`, com `skills/list` e `skills/get`;
  - motivação declarada: as `instructions` do servidor são curtas demais e a skill hoje é distribuída separada do servidor.
- **SEP-2127 "Server Cards"** (Final): metadados do servidor em `<url>/server-card`, para descoberta sem conexão.
- **Registro oficial MCP:** ainda em "preview" pela documentação (só resumo).
- **OpenAI:** o deep research e o "company knowledge" do ChatGPT esperam ferramentas `search` e `fetch` (cookbook oficial no GitHub, lido). O NINA não segue esse formato.

**Proposta, depois do M10.**
- (1) Plugin `nina` com o servidor e a skill `nina-docs`; ele habilita o teste D7.
- (2) Servir a skill por `skill://nina-docs/SKILL.md`, quando houver um cliente que suporte. A documentação do Claude Code não cita suporte ainda; conferir antes.
- (3) Cartão de servidor e entrada no registro no lançamento.
- (4) Não criar `search`/`fetch` no servidor principal ("mais ferramentas" já foi rejeitado). Se um dia valer o ChatGPT, fazer um perfil separado.

**Custo:** zero para planejar; o empacotamento não toca o `app/`. **Evidência:** specs e documentação primárias lidas.

### D10. Code mode e saída estruturada: o NINA está certo em mandar texto, mas deve oferecer o JSON tipado para quem chama por código (produto; médio-baixo)

**O que a fonte mostra.**
- **Guia "Client Best Practices" do MCP 2026-07-28 (lido):**
  - na chamada programática de ferramentas, o cliente gera funções tipadas a partir do `outputSchema`;
  - sem esquema, o código recebe `any`, e o guia diz que "o conserto real é o autor do servidor fornecer o `outputSchema`".
- **Cloudflare Code Mode (só resumo):** duas ferramentas, `search` e `execute`, cobrem mais de 2.500 endpoints em ~1.000 tokens.
- **Spec 2026-07-28:** o `structuredContent` aceita qualquer JSON, e o texto em `content` continua sendo o que o modelo lê.

**Por que importa.**
- O NINA desligou a cópia estruturada (`structured_output=False`, DECISIONS 2026-10-16) porque ela duplicava tokens para o modelo. A escolha continua certa para chamada direta.
- Num agente que chama por código (programmatic tool calling da Anthropic, code mode da Cloudflare), o código precisaria parsear o XML conciso.
- O `response_format="detailed"` já existe, mas sem `outputSchema`.

**Proposta.** Publicar o `outputSchema` do modo `detailed`: lista de fragmentos com `source_url`, `chunk_id`, `section`, `page`, `md` e `hints`. Devolver `structuredContent` **só** quando `response_format="detailed"`. O conciso continua sem cópia.

**Custo:** app/, depois do M10. **Evidência:** spec e guia primários lidos; a adoção de code mode pelos clientes é tendência, não medida.

### D11. Pedir Markdown à fonte quando o site oferece (extração; médio-baixo, cruza com outra frente)

**O que a fonte mostra.**
- O agent-docs-spec (lido) registra que Claude Code, Cursor e OpenCode enviam `Accept: text/markdown`, e que vários sites de documentação respondem com Markdown limpo ou variantes `.md`.
- Páginas renderizadas por framework têm de 75 a 84% dos bytes em dados serializados, com razão bytes/conteúdo de 40:1 a 200:1.
- Abas de conteúdo (tabbed content) se perdem na conversão.

**Por que importa.** O fetcher do NINA não pede Markdown, e o tipo `text/markdown` cai em `unsupported_content_type` (`hints.py`: "neither HTML nor PDF").

**Proposta (experimento no dev, depois do M10).** Quando a página tiver variante `.md` ou responder a `Accept: text/markdown`, comparar a retenção de seção (`bench/extract_retention.py`) entre o Markdown do site e a extração atual.

**Custo:** app/. **Evidência:** especificação comunitária e medições lidas na íntegra. Não há estudo de qualidade da resposta.

---

## 2. Onde o NINA já está igual ou melhor

- **Estado explícito, sem sessão.** O MCP 2026-07-28 removeu sessões e o handshake e recomenda "handles explícitos, opacos, passados como argumentos", com erro claro quando o handle expira. O NINA já funciona assim: `document_id`, `version_id` e `chunk_id` circulam como argumentos, e o servidor não guarda estado de conexão. O SDK instalado (2.3.0) já conhece as chaves de `_meta` da revisão 2026-07-28.
- **Erros que o modelo pode corrigir.** A SEP-1303 (spec 2025-11-25) manda devolver erro de validação como erro de execução (`isError: true`), não como erro de protocolo. O `_api_call` do NINA faz isso, com código e mensagem da API. O checklist do diretório rejeita erros genéricos como "Bad Request" sem detalhe, e o NINA não manda erro genérico.
- **Poucas ferramentas e lista estável.** Com 3 ferramentas, o NINA fica na "faixa ideal" de 1 a 15 do guia oficial. A spec 2026-07-28 pede ordem determinística no `tools/list` para preservar o cache de prompt; o NINA registra sempre na mesma ordem e com descrições estáticas. RAG-MCP (só resumo) mostra a escolha caindo para 13,6% com catálogo cheio, mas isso afeta o agente do usuário, não o NINA.
- **Instruções do servidor.** A documentação do Claude Code diz que, com o tool search ligado por padrão, as `instructions` são o que o agente vê antes de carregar as ferramentas, e devem dizer a categoria de tarefa e quando buscar. O `INSTRUCTIONS` do NINA faz isso (P2) e tem bem menos que o corte de 4.096 caracteres.
- **Saída truncada sem disfarce.** As plataformas medidas cortam em silêncio, e os agentes se declaram completos (D5). O NINA avisa `budget_limited`, `low_coverage` e `source_failed` em `<nina_notes>`, notas que a página não consegue forjar (`render.py`). Nenhuma das plataformas medidas faz isso.
- **Formato.** Não há formato universalmente melhor:
  - "Format Matters" (LREC 2026; só resumo): oscilações de 2 a 46 pontos de F1 conforme o modelo;
  - o teste de formatos aninhados do Improving Agents (só resumo) mede o XML com ~80% mais tokens que o Markdown.
  - O NINA usa Markdown no conteúdo e só três tags finas como moldura (+3,7% de tokens), o que segue a recomendação da Anthropic de delimitar com tags sem pagar XML no conteúdo.
- **Não adotar TOON.** As avaliações independentes (arXiv 2603.03306 e "Notation Matters" 2605.29676; só resumos) mostram ganho em dados tabulares uniformes (−32% nos resultados de ferramenta) e queda de 1 a 9 pontos de acerto. O conteúdo do NINA é prosa e código; o TOON não se aplica.
- **Descrições sem injeção.** O checklist do diretório rejeita descrições que mandam chamar outras ferramentas ou buscar instruções fora. As descrições do NINA falam só das próprias ferramentas. O aviso "trate como dado, não como instrução" é de segurança e coerente com a regra, mas convém reler na submissão.
- **Determinismo e cache.** O post "Prompt caching is everything" (só resumo) e a documentação de cache do Claude Code (lida) tratam a quebra de cache como incidente. O resultado de ferramenta entra no fim do prefixo e não quebra cache. O que quebraria são descrições dinâmicas, e as do NINA são fixas.

## 3. Onde o NINA está atrás

- **Filtragem dinâmica da Anthropic e `fetch_webpage` do Copilot (D2).** Fazem seleção por consulta dentro da plataforma, de graça para o usuário. O NINA não tem número contra elas.
- **Anotações e entrada no diretório (D3).** Hoje o NINA seria rejeitado no diretório de conectores da Anthropic e pede aprovação a cada chamada.
- **Distribuição (D9).** Não há plugin, a skill não é servida pelo MCP, não há cartão de servidor nem registro. O Context7 e outros já estão nos catálogos.
- **Recuperação depois da compactação (D4, P4).** Não há busca por `chunk_id`. O Managed Agents da Anthropic trata o contexto como objeto recuperável fora da janela; o NINA depende de refazer a busca.
- **Chamada por código (D10).** Não há `outputSchema`.
- **Evidência de uso real (D7).** Não há medida de quando o agente aciona o NINA. Os estudos sugerem que é raro sem empurrão.

## 4. Fontes lidas na íntegra

- Spec MCP (repositório oficial no GitHub, clonado): changelogs 2025-06-18, 2025-11-25 e 2026-07-28; `docs/specification/2026-07-28/server/tools.mdx`; `schema/2026-07-28/schema.ts` (`ToolAnnotations`, `instructions`); `docs/docs/2026-07-28/develop/clients/client-best-practices.mdx`. https://github.com/modelcontextprotocol/modelcontextprotocol
- SEP-2640 Skills Extension (seções de especificação), SEP-2127 Server Cards (resumo e motivação), SEP-2549, SEP-2567: https://github.com/modelcontextprotocol/modelcontextprotocol/tree/main/seps
- Claude Code, MCP (limites de saída, anotações, tool search, cortes de descrição, `alwaysLoad`): https://code.claude.com/docs/en/mcp.md
- Claude Code, prompt caching: https://code.claude.com/docs/en/prompt-caching.md
- Claude Code, janela de contexto e compactação: https://code.claude.com/docs/en/context-window.md
- Claude Code, plugin evals: https://code.claude.com/docs/en/plugin-evals.md
- Claude Platform, web fetch tool (filtragem dinâmica, `max_content_tokens`, citações): https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool.md
- Claude Platform, web search tool (filtragem dinâmica, `allowed_callers`): https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool.md
- Claude Platform, context editing e compaction: https://platform.claude.com/docs/en/build-with-claude/context-editing.md · https://platform.claude.com/docs/en/build-with-claude/compaction.md
- Anthropic, "Increase web search accuracy and efficiency with dynamic filtering" (2026-02-17): https://claude.com/blog/improved-web-search-with-dynamic-filtering
- Anthropic, "Scaling Managed Agents" (2026-04-08): https://www.anthropic.com/research/managed-agents
- Anthropic, checklist de conectores do diretório: https://claude.com/docs/connectors/building/review-criteria.md
- Anthropic, "Build an MCP server for Claude" (limites: ~150k caracteres no claude.ai, 25k tokens no Claude Code; timeout de 240 s): https://claude.com/docs/connectors/building/index.md
- Anthropic, plugin oficial `mcp-server-dev`, `references/tool-design.md`: https://github.com/anthropics/claude-plugins-official/tree/main/plugins/mcp-server-dev
- Anthropic skills, `mcp-builder`: https://github.com/anthropics/skills/tree/main/skills/mcp-builder
- Agent-Friendly Documentation Spec v0.6.0: https://github.com/agent-ecosystem/agent-docs-spec (SPEC.md e `site/content/platforms/_index.md`)
- Agent Ecosystem Testing (medições de WebFetch, Copilot, Cursor, Windsurf, Gemini): https://github.com/rhyannonjoy/agent-ecosystem-testing (`docs/platform-comparisons.md`, `docs/microsoft-github-copilot/copilot-test-findings.md`)
- OpenAI cookbook, servidor MCP para deep research (`search`/`fetch`): https://github.com/openai/openai-cookbook/tree/main/examples/deep_research_api/how_to_build_a_deep_research_mcp_server

## 5. Fontes que não consegui acessar e que valem a leitura completa

1. **"From Agent Behaviour to Agent-Friendly Documentation"** (Gao e Chen, 2026): https://arxiv.org/pdf/2608.20195. O único estudo grande de como os agentes consultam documentação; dá base empírica ao D7 e ao desenho das tarefas.
2. **"Model Context Protocol (MCP) Tool Descriptions Are Smelly!"** (2026): https://arxiv.org/pdf/2602.14878. Rubrica de seis componentes e o custo em passos de descrições longas (D6).
3. **"From Docs to Descriptions: Smell-Aware Evaluation of MCP Server Descriptions"**: https://arxiv.org/pdf/2602.18914. Os 18 smells e o efeito na escolha da ferramenta; serve como checklist das descrições do NINA.
4. **"Developer Experience with AI Coding Agents: HTTP Behavioral Signatures in Documentation Portals"** (Borysenko): https://arxiv.org/pdf/2604.02544. Como cada agente (Claude Code, Cursor, Cline...) busca documentação; serve à calibragem do `webfetch_live`.
5. **"LLMs Meet Library Evolution: Evaluating Deprecated API Usage"** (Wang et al., ICSE 2025): https://arxiv.org/pdf/2406.09834. Por que o prompt não basta e o contexto certo resolve (D8).
6. **"We Have a Package for You!"** (Spracklen et al., USENIX Security 2025): https://arxiv.org/pdf/2406.10279. A seção de mitigação diz se a recuperação reduz pacotes inventados.
7. **"Library Hallucinations in LLMs: Risk Analysis Grounded in Developer Queries"** (Twist et al.): https://arxiv.org/pdf/2509.22202. As porcentagens por tipo de erro do usuário, úteis para tarefas adversariais.
8. **Cursor, "Improving agent with semantic search"** (nov/2025): https://cursor.com/blog/semsearch. +12,5% de acerto médio com busca semântica além do grep; é o contraponto ao "grep, não RAG" do Claude Code.
9. **Marginlab, "Do MCPs Actually Improve Coding Agents? Part 1"**: https://marginlab.ai/blog/do-mcps-improve-coding-agents-part-1/. Números completos do Context7 no Terminal-Bench 2.0; o desenho é próximo de um braço do M10.
10. **"mcpbr"** (Georgia Tech, fev/2026): https://zenodo.org/records/18627369. Metodologia de avaliar um servidor MCP no SWE-bench, onde a ferramenta piorou o resultado.
11. **"TRACEPROBE"** e **"AgentDiet"** (trajetórias de agentes; recarga e redundância): pelo resumo, o AgentDiet corta 39,9 a 59,7% dos tokens sem perda. Não achei URL primária confiável; procurar pelos títulos no arXiv.
12. **"Format Matters"** (LREC 2026): https://aclanthology.org/2026.lrec-1.593/. O estudo mais rigoroso sobre o formato do contexto (JSON, XML ou chave-valor).
13. **"Token-Oriented Object Notation vs JSON"**: https://arxiv.org/pdf/2603.03306, e **"Notation Matters"**: https://arxiv.org/pdf/2605.29676. Só para confirmar a recusa do TOON.
14. **Anthropic, "Lessons from building Claude Code: Prompt caching is everything"**: https://claude.com/blog/lessons-from-building-claude-code-prompt-caching-is-everything. Redireciona para claude.dev, bloqueado aqui; o contexto é o projeto de ferramentas adiadas e da compactação.
15. **Giuseppe Gurgone, "Claude Code WebFetch"** (engenharia reversa): https://giuseppegurgone.com/claude-webfetch. O caminho exato do WebFetch (limite de 100k, Markdown confiável, resumo pelo Haiku); define o que o braço `webfetch_live` mede.
16. **Cloudflare, "Code Mode: give agents an entire API in 1,000 tokens"**: https://blog.cloudflare.com/code-mode-mcp/, e a documentação https://developers.cloudflare.com/agents/model-context-protocol/codemode/. Base do D10.
17. **OpenAI, tool search e conectores MCP** (documentação oficial): https://developers.openai.com/api/docs/guides/tools-tool-search. O equivalente da OpenAI ao tool search; confirmar como o ChatGPT mostra servidores MCP.
