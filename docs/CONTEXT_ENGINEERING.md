# Context engineering: o conceito dissecado e o que vale para o NINA

Pesquisa de 2026-10-28. Li na íntegra os textos da Anthropic (lista no fim) e,
por busca, os principais estudos e relatos de terceiros. Os textos da Anthropic
foram baixados e lidos inteiros; os de terceiros (Manus, LangChain, Breunig,
Chroma, Cognition, artigos do arXiv) só por resumos e abstracts, porque a rede
deste ambiente bloqueia esses sites. Números de fornecedores são afirmações
deles, marcados assim.

## 1. O conceito

**Definição da Anthropic:** contexto é o conjunto de tokens que o modelo vê ao
gerar. Engenharia de contexto é "o conjunto de estratégias para curar e manter
o conjunto ótimo de tokens durante a inferência", incluindo tudo o que entra
além do prompt: instruções, ferramentas, MCP, dados externos, histórico.

**Diferença para engenharia de prompt:** prompt é escrever bem as instruções,
uma vez. Contexto é decidir, **a cada turno** de um agente em laço, o que entra
na janela, a partir de um universo de informação que só cresce.

**Princípio central:** "achar o **menor** conjunto possível de tokens de alto
sinal que maximize a chance do resultado desejado". Mínimo não é curto: é o
suficiente e nada além.

**Por que importa: o "orçamento de atenção".**
- Todo modelo perde precisão de recuperação conforme o contexto cresce
  ("context rot"). Uns degradam mais devagar, mas todos degradam.
- Causa arquitetural: no transformer, cada token se relaciona com cada outro (n²
  pares), e a atenção "se espalha" conforme n cresce.
- Os modelos treinam mais em sequências curtas; têm menos prática com
  dependências longas. Técnicas de extensão de posição ajudam, com perda.
- Resultado: um gradiente, não um precipício. Cada token novo gasta um pouco do
  orçamento, então contexto é recurso finito com **retorno marginal
  decrescente**.

## 2. Anatomia de um contexto eficaz

| componente | o que a Anthropic recomenda | falhas comuns |
|---|---|---|
| prompt de sistema | linguagem direta na "altitude certa"; seções delimitadas (XML ou Markdown); começar mínimo com o melhor modelo e acrescentar só o que as falhas pedirem | lógica rígida e frágil no prompt, ou orientação vaga que supõe contexto compartilhado |
| ferramentas | autocontidas, robustas a erro, com propósito inequívoco; respostas eficientes em tokens; parâmetros sem ambiguidade (`user_id`, não `user`) | conjuntos inchados e sobrepostos: "se um engenheiro não sabe qual ferramenta usar, o agente também não" |
| exemplos | poucos, diversos e canônicos ("exemplos são as figuras que valem mil palavras") | lista de casos-limite tentando cobrir toda regra |
| histórico | ver seção 4 | resultados velhos de ferramentas que nunca saem |

Do guia de ferramentas (mesma série):
- Ferramentas que consolidam fluxos (`search_logs` em vez de `read_logs`;
  `get_customer_context` em vez de três listagens) poupam o contexto que
  resultados intermediários gastariam.
- Devolver só sinal alto; trocar identificadores crípticos (UUID, hashes) por
  nomes legíveis "melhora significativamente a precisão" e reduz alucinação.
- Parâmetro `response_format` (conciso ou detalhado); exemplo deles: 206 contra
  72 tokens.
- Paginação, filtros e truncamento com padrões sensatos; o Claude Code corta
  resposta de ferramenta em 25 mil tokens.
- Ao truncar ou errar, dizer ao agente o que fazer a seguir.
- Descrições de ferramenta são prompt: pequenas mudanças dão ganhos grandes (o
  SWE-bench Verified do Sonnet 3.5 veio de refinar descrições).

## 3. Recuperação: antes da inferência ou "na hora"

- **Antes (RAG clássico):** embeddings escolhem trechos antes de o modelo
  pensar.
- **Na hora ("just in time"):** o agente guarda **identificadores leves**
  (caminhos, consultas salvas, links) e carrega o conteúdo com ferramentas quando
  precisa. O Claude Code analisa bancos grandes com consultas e `head`/`tail` sem
  nunca pôr o objeto inteiro no contexto.
- **Divulgação progressiva:** cada passo revela pistas para o próximo (tamanho
  de arquivo sugere complexidade, nome sugere propósito). O agente monta o
  entendimento em camadas e guarda só o necessário.
- **Custo:** explorar é mais lento que buscar pronto, e sem boas ferramentas o
  agente desperdiça contexto em becos sem saída.
- **Híbrido:** algo pronto de antemão (o `CLAUDE.md`) e o resto sob demanda
  (`glob`, `grep`). Conselho final: "faça a coisa mais simples que funciona".

As Agent Skills aplicam a mesma ideia em três níveis: nome e descrição no
prompt; o corpo do `SKILL.md` quando relevante; arquivos anexos só se
necessário. O conteúdo total de uma skill fica "efetivamente ilimitado".

## 4. Tarefas longas: quatro alavancas

1. **Compactação:** perto do limite, resumir o histórico e recomeçar com o
   resumo. O Claude Code guarda decisões, bugs abertos e detalhes, descarta
   saídas redundantes e reabre os cinco arquivos mais recentes. Ajustar o prompt
   de compactação primeiro para recall, depois para precisão.
2. **Limpeza de resultados de ferramenta:** a forma mais leve de compactação.
   Um resultado lido lá atrás raramente precisa ser visto cru de novo.
3. **Notas estruturadas (memória):** o agente escreve fora da janela (lista de
   tarefas, `NOTES.md`) e relê depois. O Claude jogando Pokémon mantém mapas e
   contagens por milhares de passos.
4. **Subagentes:** cada um explora com janela limpa (dezenas de milhares de
   tokens) e devolve um resumo de 1.000 a 2.000 tokens.

Na plataforma (números da Anthropic, avaliação interna):
- **Context editing** (`clear_tool_uses_20250919`): apaga os resultados mais
  antigos acima de um gatilho (padrão 100 mil tokens), mantém os N mais recentes
  (padrão 3), com `exclude_tools`, `clear_at_least` (para valer a quebra do
  cache) e um marcador no lugar do conteúdo apagado. Sozinho, +29% numa
  avaliação de busca; com a ferramenta de memória, +39%; numa busca de 100
  turnos, −84% de tokens.
- **Tool search:** as definições ficam adiadas (`defer_loading`) e o modelo
  busca as que precisa. Um conjunto típico de servidores gasta ~55 mil tokens de
  definições antes de começar; a escolha da ferramenta piora acima de 30 a 50
  ferramentas carregadas.
- **Execução de código com MCP:** ferramentas viram arquivos de código que o
  agente lê sob demanda e filtra resultados antes de entrarem no contexto (de
  150 mil para 2 mil tokens num exemplo deles).
- **Multiagente:** +90,2% numa avaliação de pesquisa, mas cerca de 15× os tokens
  de um chat. No BrowseComp, **o uso de tokens sozinho explica 80% da variância
  de desempenho**.

## 5. O que os estudos independentes mostram

| estudo | achado | leitura para o NINA |
|---|---|---|
| Liu et al., "Lost in the Middle" (TACL 2024) | acerto em U: melhor no começo e no fim, pior no meio, inclusive em modelos de contexto longo | a ordem dos fragmentos importa; é o item 4 da pesquisa anterior (`bench/m10a_order_dedup.py`) |
| Chroma, "Context Rot" (2025, 18 modelos) | o desempenho fica menos confiável com o comprimento, mesmo em tarefas simples; distratores parecidos com a resposta pioram mais; como a informação é apresentada importa tanto quanto estar presente | quase-duplicatas e outras versões da doc são os piores distratores (item 5) |
| Du et al., "Context Length Alone Hurts LLM Performance Despite Perfect Retrieval" (EMNLP 2025) | queda de 13,9% a 85% com o comprimento **mesmo com recuperação perfeita**, e mesmo com o texto irrelevante trocado por espaços ou mascarado; recitar a evidência antes de responder recupera parte | o contexto longo custa **acerto**, não só dinheiro: a página inteira com a resposta dentro pode perder para fragmentos menores. Reforça a previsão da Emenda 4 |
| Modarressi et al., NoLiMa (ICML 2025) | quando pergunta e resposta não compartilham palavras, 11 de 13 modelos caem abaixo de metade do desempenho em 32 mil tokens | busca puramente léxica falha nessas perguntas; o híbrido (vetor + léxico + rerank) é o caminho certo |
| Zhang et al., ACE (ICLR 2026) | reescrever contexto com LLM repetidamente causa "colapso": o resumo vai perdendo os detalhes específicos | argumento a favor de texto verbatim com proveniência, contra resumo por LLM (é o que o WebFetch faz com o Haiku) |

## 6. Taxonomias de quem constrói agentes

- **LangChain (escrever, selecionar, comprimir, isolar):** guardar fora da
  janela, trazer de volta o que serve, manter só o necessário, separar em
  contextos distintos. **O NINA é "selecionar + comprimir" como serviço**, sem
  LLM.
- **Breunig, quatro modos de falha:** *envenenamento* (um erro entra e é
  reusado), *distração* (o contexto longo faz o modelo esquecer o que sabe),
  *confusão* (informação supérflua piora a resposta) e *choque* (informações que
  se contradizem). O NINA ataca confusão e distração; choque aparece quando
  fragmentos de versões diferentes da doc se misturam.
- **Manus (lições de produção):** taxa de acerto do cache KV como métrica
  principal (entrada:saída de ~100:1; entrada em cache custa 10× menos); prefixo
  estável e JSON determinístico; não tirar ferramentas no meio da sessão
  (mascarar); sistema de arquivos como memória; compressão sempre
  **restaurável** (guardar a URL mesmo ao descartar o conteúdo); recitar a lista
  de tarefas para não perder o objetivo; manter os erros no contexto.
- **Cognition ("Don't build multi-agents"):** compartilhar o contexto inteiro;
  ações carregam decisões implícitas, e subagentes com contextos separados
  decidem coisas contraditórias. Subagentes funcionam quando só leem (busca),
  não quando escrevem.

## 7. O NINA visto por essa lente

### Onde ele se encaixa

O NINA é uma ferramenta de recuperação "na hora": o agente passa a pergunta e
as URLs quando precisa e recebe o menor conjunto de fragmentos de alto sinal,
com identificadores leves (URL, `chunk_id`) para pedir mais. É exatamente o
princípio central do texto da Anthropic, implementado de forma determinística.

### O que já está alinhado

| prática | no NINA |
|---|---|
| resposta enxuta, `response_format` | formato conciso, padrão no MCP (−42% a −48% de tokens) |
| ferramentas poucas, distintas e bem descritas | 3 ferramentas; descrições que dizem quando usar e quanto custa (O2) |
| erros e cortes acionáveis | `hints` e erros da API visíveis no MCP (A3) |
| não repetir o que o agente já viu | `exclude_chunk_ids` (A4) |
| conteúdo externo é dado, não instrução | `trust: untrusted_source`, notas do NINA separadas |
| saída determinística (cache estável) | sem LLM na extração e na seleção; sem carimbo de hora no texto |
| pedir pergunta curta ("comece amplo") | a descrição do `assemble_context` pede uma pergunta curta e focada |

### Tensões a encarar com honestidade

1. **Tokens explicam desempenho.** No BrowseComp, o uso de tokens explica 80% da
   variância. Cortar contexto pode cortar acerto. O H1 de não inferioridade do
   M10 existe exatamente para medir isso. Do outro lado, Du et al. mostram que
   contexto longo também tira acerto. O resultado do M10 decide qual efeito
   ganha nas perguntas de documentação.
2. **Context editing encolhe a vantagem em sessões longas.** A Emenda 4 modela
   o resultado de ferramenta relido do cache a cada turno. Quem liga a limpeza
   de resultados paga esse custo por poucos turnos. A calculadora de ROI precisa
   mostrar esse cenário.
3. **Subagente é um concorrente direto.** Um subagente lê a página inteira na
   própria janela e devolve 1 a 2 mil tokens. A janela principal fica pequena,
   mas os tokens do subagente são pagos e o resumo é feito por LLM (risco de
   colapso). O braço `webfetch_live` já mede esse padrão: o WebFetch resume a
   página com o Haiku.
4. **Identificadores crípticos.** O guia de ferramentas recomenda nomes legíveis
   em vez de hashes. O formato conciso ainda mostra `doc_…` e `ver_…` por
   documento. Eles ficam, porque a proveniência é regra inegociável do projeto,
   mas é o único ponto em que não seguimos a recomendação.

## 8. Achados novos e propostas

Ordenados por urgência. Nada foi implementado: cada item espera aprovação.

### P1. Fixar o modo de carga das ferramentas no harness do M10 (urgente, validade)

**Achado:** o CLI 2.1.301, que vem dentro do `claude-agent-sdk`, adia as
ferramentas MCP por padrão (modo "tool search"). A decisão depende da variável
`ENABLE_TOOL_SEARCH` e de flags remotas que o CLI consulta. O harness não fixa
essa variável, e o `clean_environ` não a remove, porque só apaga `ANTHROPIC*` e
`CLAUDE*`.

**Risco:**
- Os braços congelados (`full_page`, `strong_rag`, `nina`) usam ferramentas MCP;
  o `webfetch_live` usa uma ferramenta embutida. Com adiamento, só os primeiros
  pagariam o turno extra de busca de ferramenta, e o limite é de 15 turnos.
- O `ToolSearch` não está entre as ferramentas permitidas, então o agente
  talvez nem consiga carregar o esquema.
- Uma flag remota pode mudar o comportamento no meio da rodada (ABC II.1).
- A conferência gratuita do `init` não mostra se as ferramentas estão adiadas.

**Proposta:** pôr `ENABLE_TOOL_SEARCH=false` no `env` de `session_options`
(todas as ferramentas carregadas de início, como o PREREG supõe) e conferir
isso num teste. Muda o harness, não o `app/`: registrar numa emenda antes do
piloto.

### P2. Instruções do servidor MCP e nomes fáceis de achar (produto)

**Achado:** no Claude Code dos usuários, com o adiamento, o agente vê de início
só o **nome** de cada ferramenta MCP e as **instruções do servidor** (o campo
`instructions` do MCP, que o Claude Code injeta no prompt de sistema; observei
isso na própria sessão desta pesquisa). O servidor do NINA não define
`instructions`. Os nomes `resolve_url`, `assemble_context` e `get_document` não
contêm "docs" nem "documentation", e é por essas palavras que o agente busca.

**Proposta:** instruções de 2 a 3 linhas ("use `assemble_context` para
responder perguntas sobre páginas de documentação técnica pública; devolve só os
trechos relevantes, com a origem") e palavras de busca nas descrições. Medida
sem custo: os tokens das instruções e um teste de que o `ToolSearch` por
"documentation" acha o `assemble_context` no catálogo. Muda o que o braço `nina`
vê: entra na emenda de recongelamento.

### P3. Divulgação progressiva: fragmento, seção, página (produto, medível sem custo)

**Achado:** o item 3 da Fase 3 (puxar automaticamente os vizinhos de cada
fragmento) foi rejeitado na metade B: custava orçamento em toda chamada. A
lição da Anthropic é fazer isso **sob demanda**. Hoje o agente só tem dois
níveis: fragmentos (~4 mil tokens) ou a página inteira (`resolve_url`, ~45 mil
em média). Falta o meio: "a seção inteira deste fragmento".

**Proposta:** um modo de expandir para a seção (por `chunk_id` ou caminho de
seção), devolvendo só o trecho daquela seção.

**Antes de mexer no contrato, medir o teto sem custo no dev:** nas tarefas em
que o NINA entrega parte da seção (cobertura entre 0 e 0,5), quantas virariam
acerto se o agente expandisse o melhor fragmento para a seção, e com quantos
tokens a mais. Se o teto for pequeno, a proposta morre ali.

### P4. Restauração depois da limpeza de contexto (produto)

**Achado:** o Manus insiste em compressão **restaurável**. Quando o context
editing apaga um resultado do NINA, o agente fica com os `chunk_id` mas sem o
texto. Se passar esses ids em `exclude_chunk_ids`, nunca mais os recebe.

**Proposta mínima:** a descrição do `exclude_chunk_ids` diz que, para reaver
fragmentos apagados do contexto, basta chamar sem eles. Uma linha, sem mudança
de contrato.

**Proposta maior:** buscar fragmentos por `chunk_id`, sem refazer a busca. Fica
para depois do M10, se as transcrições mostrarem esse padrão.

### P5. Calculadora de ROI com e sem limpeza de contexto (Fase 5)

Acrescentar à extrapolação da Emenda 4 o cenário em que o cliente usa context
editing: o resultado da ferramenta sai depois de N turnos. Mostra a vantagem
real em vez da maior possível. Sem custo, só fórmula.

### P6. Argumentos de posicionamento (README e materiais)

Três frases com fonte (dos abstracts; conferir o texto completo antes de publicar):
- Contexto longo custa acerto mesmo com a resposta dentro (Du et al., 2025).
- Distratores parecidos com a resposta são os mais nocivos (Chroma, 2025).
- Resumo por LLM perde detalhe; o NINA entrega o texto original com proveniência
  (ACE, 2025; Cognition, 2025).

Só entram no material depois do M10, junto com os nossos números, nunca no lugar
deles.

### P7. Aviso de choque de versões (frente "versão e atualidade")

Quando os fragmentos vêm de versões diferentes da mesma doc (por exemplo,
`/3.11/` e `/3.12/` do Python), um `hint` avisa o agente. Ataca o modo de falha
"choque" e casa com a frente que já estava na fila.

### O que não fazer

- **Compactação ou resumo por LLM dentro do NINA:** viola a regra de extração
  determinística e cai no colapso descrito pelo ACE.
- **Multiagente dentro do NINA:** o ganho vem de gastar mais tokens, o oposto da
  tese.
- **Tirar `document_id` e `version_id` da saída:** a proveniência é inegociável;
  o custo em tokens é pequeno.
- **Mais ferramentas MCP:** a escolha piora acima de 30 a 50 ferramentas
  carregadas, e o agente do usuário já tem outras. Expandir parâmetros das três
  existentes é melhor que criar uma quarta.

## Fontes

Anthropic (lidas na íntegra):
- "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- "Writing effective tools for agents — with agents": https://www.anthropic.com/engineering/writing-tools-for-agents
- "Code execution with MCP": https://www.anthropic.com/engineering/code-execution-with-mcp
- "How we built our multi-agent research system": https://www.anthropic.com/engineering/multi-agent-research-system
- "Equipping agents for the real world with Agent Skills": https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- "Managing context on the Claude Developer Platform": https://www.anthropic.com/news/context-management
- Documentação: context editing (https://docs.claude.com/en/docs/build-with-claude/context-editing), memory tool (https://docs.claude.com/en/docs/agents-and-tools/tool-use/memory-tool), tool search (https://docs.claude.com/en/docs/agents-and-tools/tool-use/tool-search-tool)

Estudos (abstracts e resumos):
- Liu et al., "Lost in the Middle", TACL 2024: https://huggingface.co/papers/2307.03172
- Chroma, "Context Rot", 2025: https://research.trychroma.com/context-rot
- Du et al., "Context Length Alone Hurts LLM Performance Despite Perfect Retrieval", EMNLP 2025 Findings: https://arxiv.org/html/2510.05381v1
- Modarressi et al., "NoLiMa", ICML 2025: https://arxiv.org/html/2502.05167v3
- Zhang et al., "Agentic Context Engineering", ICLR 2026: https://arxiv.org/html/2510.04618v1

Relatos de quem constrói agentes (resumos):
- Manus, "Context Engineering for AI Agents: Lessons from Building Manus": https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus
- LangChain, "Context engineering for agents": https://langchain.com/blog/context-engineering-for-agents
- Drew Breunig, "How to Fix Your Context": https://www.dbreunig.com/2025/06/26/how-to-fix-your-context.html
- Cognition, "Don't Build Multi-Agents": https://cognition.ai/blog/dont-build-multi-agents

Achado de P1: lido no binário do CLI 2.1.301 embutido em
`claude_agent_sdk/_bundled/claude` (função que decide o modo `tst`/`standard` a
partir de `ENABLE_TOOL_SEARCH` e de flags remotas).
