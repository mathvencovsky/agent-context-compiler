# O que as grandes empresas de IA publicaram, aplicado ao NINA e ao M10

Pesquisa de 2026-10-28, antes de qualquer chamada paga do M10. Objetivo: achar
onde o projeto quebra antes de gastar dinheiro.

## 1. O que foi lido e o que ficou de fora

Este ambiente só alcança alguns domínios.

**Lido na íntegra:**
- **Anthropic** (anthropic.com e documentação):
  - avaliação de agentes ("Demystifying evals for AI agents");
  - ruído de infraestrutura em avaliações;
  - abordagem estatística de avaliações ("Adding error bars to evals");
  - consciência de avaliação no BrowseComp;
  - uso avançado de ferramentas;
  - contextual retrieval;
  - prompting de contexto longo;
  - os dois postmortems de qualidade;
  - documentação de prompt caching, search results, citações e boas práticas
    de prompt.
- **Claude Code** (code.claude.com): variáveis de ambiente, MCP, modelo e
  esforço, custos.
- **OpenAI** (via GitHub): guia de prompts do GPT-4.1 e os avaliadores
  oficiais do SimpleQA e do BrowseComp.
- **Google DeepMind**: relatórios técnicos do Gemini 1.5 e do Gemini 2.5
  (PDF); blog de avaliação de RAG do Google Cloud.
- **Benchmarks** (READMEs no GitHub): BRIGHT, NoLiMa, RULER, Lost in the
  Middle, CRAG, LLMLingua, Lost in Conversation, DocPrompting, CodeRAG-Bench,
  LOFT, HELMET, LongBench.

**Só por resumos de busca** (o domínio está bloqueado aqui):
- arxiv.org, openai.com, ai.meta.com, microsoft.com, huggingface.co;
- documentação do Vertex AI;
- os artigos OP-RAG (NVIDIA), "Long-Context LLMs Meet RAG", "Sufficient
  Context" (Google), "Why Language Models Hallucinate" (OpenAI), Spotlighting
  (Microsoft), AgentDojo, autopreferência de juízes, CodeRAG-Bench,
  DocPrompting e LLMLingua.

Para uma segunda rodada com leitura completa, libere esses domínios nas
configurações de rede do ambiente.

## 2. Achados, por gravidade

### Críticos: invalidariam o M10

**C1. Páginas longas nunca chegariam ao comparador principal.** Corrigido em
parte.
- **Fonte:** documentação de MCP do Claude Code, "MCP output limits";
  conferido no código do CLI 2.1.301.
- **O problema:**
  - Um resultado de ferramenta MCP com mais de 50 mil caracteres é salvo em
    arquivo, qualquer que seja o `MAX_MCP_OUTPUT_TOKENS`.
  - A exceção é a ferramenta declarar `_meta["anthropic/maxResultSizeChars"]`,
    e esse valor tem teto fixo de 500 mil.
  - Nenhum braço tem ferramenta para ler arquivo. Toda página acima de ~12 mil
    tokens (cerca do percentil 90 do corpus) viraria um aviso ilegível no braço
    `full_page`.
  - O NINA ganharia por defeito do harness, e quem reproduzisse o teste
    descobriria isso.
- **Feito:**
  - `fetch_page` e `search_docs` declaram 500 mil (teste pelo protocolo real,
    em stdio).
  - Emenda 12 do PREREG.
  - `bench/m10_result_limits.py` conta as páginas acima de 500 mil.
- **Decisão sua:** o que fazer com as tarefas acima de 500 mil (seção 3).

**C2. Padrões do Claude Code que mudam sem atualização.** Corrigido.
- **Fontes:** postmortem de abril da Anthropic (o esforço padrão foi trocado
  e revertido); documentação de variáveis de ambiente; código do CLI.
- **O problema:**
  - O harness não fixava o esforço (o padrão documentado do Sonnet 5.5 é
    `medium`, mas pode vir da organização), a validade do cache (5 min contra
    1 h, com gravação 60% mais cara) nem a troca automática de modelo quando um
    classificador marca a requisição.
  - Se qualquer um mudasse entre o piloto e a rodada principal, o tamanho de
    amostra e o custo por sessão do piloto deixariam de valer.
- **Feito:** `CLAUDE_CODE_EFFORT_LEVEL=medium`, `FORCE_PROMPT_CACHING_5M=1` e
  `CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1`, com teste; Emenda 12.

**C3. Uma página pode forjar as notas do NINA.** Não corrigido: muda o produto.
- **Fontes:** Spotlighting (Microsoft): delimitadores cortam o sucesso de
  injeção indireta pela metade e marcação de dados o leva a menos de 3%. Guia
  do GPT-4.1: Markdown é o delimitador mais fraco quando os documentos também
  são Markdown. Boas práticas da Anthropic: delimitar cada documento com tags.
- **O problema, testado aqui:**
  - O texto de uma página que contenha `---` seguido de `NINA notes (not from
    the pages):` sai idêntico às notas verdadeiras.
  - Cabeçalhos `##` da página se misturam aos cabeçalhos do NINA.
  - Isso fere a regra inegociável de que conteúdo de página nunca vira
    instrução.
- **Proposta:** devolver pelo MCP um bloco de conteúdo por fragmento e um bloco
  separado para as notas, como os blocos `search_result` da API da Anthropic.
  Uma página não atravessa a fronteira entre blocos. Alternativa: tags
  `<fragment source="…">`, com as tags neutralizadas dentro do texto. Muda a
  SPEC §8/§9 e entra no recongelamento.

### Altos: distorcem os números

**A1. Tarefas agrupadas por página.**
- **Fonte:** "Adding error bars to evals" (Anthropic): com perguntas
  agrupadas, o erro-padrão ingênuo pode ser 3× menor que o real.
- **Medido aqui:** no `holdout`, 65% das tarefas dividem a primeira página
  citada com outra (a `functions.html` do Python aparece em 48 tarefas). Numa
  amostra de 349, cerca de 35% dividem.
- **Proposta:** acrescentar, como análise de sensibilidade, o bootstrap por
  agrupamento (reamostrar páginas em vez de tarefas). Emenda, antes de
  qualquer dado.

**A2. Respostas que listam várias soluções.**
- **Fonte:** o avaliador oficial do SimpleQA (OpenAI) marca como incorreta a
  resposta que lista alternativas ("pode ser X, ou pode ser Y").
- **O problema:** nossa rubrica aceita "uma solução diferente que resolve",
  sem tratar o chute em lista.
- **Proposta:** é a Proposta A de `docs/AUDITORIA_ABC.md`, agora com
  precedente: incluir na rubrica que resposta com alternativas em que alguma
  contradiz a referência é incorreta. Emenda.

**A3. O dev mede consultas diferentes das que o agente manda.**
- **Fonte:** BRIGHT (perguntas do StackExchange e as páginas citadas nas
  respostas): reescrever a pergunta com raciocínio antes de buscar melhora a
  recuperação em até 12 pontos de nDCG.
- **O problema:**
  - No dev do M10a, a consulta é o título mais o corpo inteiro da pergunta.
    No M10 e no uso real, quem escreve a consulta é o agente, e a descrição da
    ferramenta pede uma pergunta curta.
  - Os ajustes aceitos (λ = 0 e outros) foram medidos numa distribuição de
    consultas que o produto não vê.
- **Proposta:** depois do piloto, tirar as consultas reais das transcrições e
  remedir o dev com elas (só embeddings novos das consultas, centavos).
- **Lado bom:** o agente faz de graça a reescrita que o BRIGHT recomenda, sem
  ferir a regra de nenhum LLM dentro do NINA.

**A4. Preços de cache do Sonnet 5.5.**
- **O que falta:** era pendência sua conferir esses preços na página oficial.
  Lida aqui (tabela de prompt caching, 2026-10-28): gravação US$ 2,50 por
  milhão (5 min) ou US$ 4 (1 h), leitura US$ 0,10.
- **Proposta:** pôr `cache_write_usd_per_1m: 2.50` e
  `cache_read_usd_per_1m: 0.10` no `bench/prices.yaml`, depois que você
  conferir.

**A5. Abster-se conta como erro.**
- **Fonte:** "Why Language Models Hallucinate" (OpenAI): nota binária faz o
  chute valer mais que admitir que não sabe.
- **O problema:**
  - O prompt manda o agente dizer quando as páginas não têm a resposta, e a
    rubrica conta isso como incorreto.
  - É igual para todos os braços, mas um braço que se abstém mais (o NINA com
    a nota `low_coverage`) seria punido como se errasse.
- **Proposta:** relatar à parte a taxa de abstenção por braço (descritivo, sem
  mudar o H1), como a categoria "não tentou" do SimpleQA. Emenda.

### Médios

**M1. Uma execução por tarefa.**
- **Fonte:** "Demystifying evals" (Anthropic): cada tarefa tem a própria taxa
  de acerto; uma execução é ruidosa.
- **Leitura:** o desenho pareado do PREREG trata isso como ruído, não como
  viés. Basta relatar que é uma execução, com o piso de ruído do Apêndice A.

**M2. Ordem dos fragmentos.** Regra revista antes de medir.
- **Fontes:** Lost in the Middle (as pontas); Jin et al. 2024 (os melhores
  trechos nas duas pontas); OP-RAG da NVIDIA (ordem da página dentro do
  documento); estudo antigo da Anthropic (Claude melhor perto do fim).
- **Feito:** variante `edges` e critério "resposta numa ponta" no
  `bench/m10a_order_dedup.py`; DECISIONS.

**M3. `anthropic/searchHint`.**
- **Fonte:** código do CLI 2.1.301.
- **O que é:** além das instruções do servidor (P2), o Claude Code lê uma dica
  de busca por ferramenta.
- **Proposta:** acrescentar "documentation docs reference API" no `_meta` das
  três ferramentas do NINA. Muda o `app/`.

**M4. Agente com mais de 100 mil tokens de contexto.**
- **Fonte:** relatório do Gemini 2.5 (DeepMind).
- **Achado:** acima de 100 mil tokens, o agente passou a repetir ações em vez
  de planejar.
- **Leitura:** reforça a previsão da Emenda 4 (vantagem maior em páginas
  longas) e as tarefas com páginas enormes (uma do piloto cita 494 mil tokens).

**M5. Abster-se quando o contexto não basta.**
- **Fonte:** "Sufficient Context" (Google, ICLR 2025): modelos fechados
  respondem errado em vez de se abster quando o contexto não basta.
- **Leitura:** é argumento de produto para a cobertura e a nota `low_coverage`
  do NINA.

**M6. Ruído de infraestrutura.**
- **Fonte:** Anthropic: até 6 pontos de diferença só por configuração; o
  acerto varia com a hora do dia.
- **Leitura:** o PREREG já sorteia a ordem dos braços por tarefa. Falta só
  relatar datas, horários e configuração.

### Confirmações: já está como as empresas recomendam

- Busca híbrida (BM25 com embeddings) e rerank. A Anthropic mostra que os
  ganhos se somam; o NINA reranqueia 30 candidatos, contra 150 deles.
- Análise pareada e análise de poder (texto estatístico da Anthropic).
- Juiz calibrado com humanos, autoconsistência e resposta de referência
  (`bench/judge_probe.py`).
- Sessões isoladas, leitura obrigatória de transcrições no piloto, uma
  variável por vez nas metades A e B.

## 3. Decisões suas

1. **Tarefas com página acima de 500 mil caracteres.** Primeiro rode o
   `bench/m10_result_limits.py`. Opções:
   - (a) excluir essas tarefas de todos os braços, com contagem relatada;
   - (b) dar ao `full_page` uma ferramenta de leitura do arquivo salvo (como no
     Claude Code real, mas muda o braço);
   - (c) manter e relatar como limitação.

   Recomendação: (a) se forem poucas; (b) se forem muitas.
2. **As ferramentas de página inteira do NINA** (`resolve_url`,
   `get_document`) não declaram o limite: são o produto, e para o usuário
   salvar em arquivo é proteção. No M10, o agente do braço `nina` não consegue
   ler a página inteira acima de 50 mil caracteres. Opções: aceitar, porque
   o caminho principal do NINA é o `assemble_context`; ou declarar o limite
   também no produto.
3. **C3** (separação estrutural do conteúdo), **A1** (bootstrap por
   agrupamento), **A2** (rubrica), **A5** (abstenção) e **M3**
   (`searchHint`): aprovar ou não. Os quatro primeiros são emenda ou mudança
   de SPEC.
4. **A4:** conferir os preços e autorizar a gravação no `prices.yaml`.
5. **Tráfego não essencial:** desligar `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`
   também cortaria as flags remotas que ainda não fixamos. Mexe em várias
   coisas ao mesmo tempo, então não apliquei.

## 4. Para rodar (sua máquina, com corpus)

```
uv run python -m bench.m10_result_limits           # sem custo
uv run python -m bench.m10a_order_dedup --half A   # só cache (agora com edges)
```

## Fontes

Anthropic: https://www.anthropic.com/research/demystifying-evals-for-ai-agents ·
https://www.anthropic.com/research/infrastructure-noise ·
https://www.anthropic.com/research/statistical-approach-to-model-evals ·
https://www.anthropic.com/research/eval-awareness-browsecomp ·
https://www.anthropic.com/research/advanced-tool-use ·
https://www.anthropic.com/news/contextual-retrieval ·
https://www.anthropic.com/news/prompting-long-context ·
https://www.anthropic.com/research/april-23-postmortem ·
https://www.anthropic.com/research/a-postmortem-of-three-recent-issues ·
https://docs.claude.com/en/docs/build-with-claude/prompt-caching ·
https://docs.claude.com/en/docs/build-with-claude/search-results ·
https://docs.claude.com/en/docs/build-with-claude/prompt-engineering/claude-4-best-practices

Claude Code: https://code.claude.com/docs/en/mcp · https://code.claude.com/docs/en/env-vars ·
https://code.claude.com/docs/en/model-config

OpenAI: https://github.com/openai/openai-cookbook (examples/gpt4-1_prompting_guide.ipynb) ·
https://github.com/openai/simple-evals (simpleqa_eval.py, browsecomp_eval.py) ·
"Why Language Models Hallucinate", https://arxiv.org/pdf/2509.04664

Google: relatório Gemini 2.5, https://storage.googleapis.com/deepmind-media/gemini/gemini_v2_5_report.pdf ·
https://cloud.google.com/blog/products/ai-machine-learning/optimizing-rag-retrieval ·
"Sufficient Context", https://arxiv.org/pdf/2411.06037 ·
"Long-Context LLMs Meet RAG", https://arxiv.org/abs/2410.05983

Outros: BRIGHT, https://github.com/xlang-ai/BRIGHT ·
OP-RAG (NVIDIA), https://arxiv.org/abs/2409.01666 ·
Spotlighting (Microsoft), https://arxiv.org/pdf/2403.14720 ·
Lost in Conversation (Microsoft), https://github.com/microsoft/lost_in_conversation ·
LLMLingua (Microsoft), https://github.com/microsoft/LLMLingua ·
autopreferência de juízes, https://arxiv.org/pdf/2404.13076 ·
CodeRAG-Bench, https://arxiv.org/abs/2406.14497 ·
DocPrompting, https://arxiv.org/pdf/2207.05987
