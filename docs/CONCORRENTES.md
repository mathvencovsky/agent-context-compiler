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
dentro do JSON. É a lição mais cara que ainda não aplicamos.

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
só um bloco nas páginas sem `<main>`. Falta transformar a medida de retenção por
site num teste de regressão, para o próximo site novo não cair no mesmo buraco.

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

## O que dá para fazer sem gastar

| # | mudança | lição | como medir sem custo | mexe no contrato? |
|---|---|---|---|---|
| 1 | `response_format` conciso no `assemble` e no MCP: Markdown com um cabeçalho por documento, caminho de seções, referência curta por fragmento; sem scores, sem evidência duplicada, sem ecoar a pergunta | 1 | tokens na metade A, com o mesmo conteúdo | sim (SPEC §8) |
| 2 | teste de regressão da retenção de texto por site no corpus congelado | 5 | roda local | não |
| 3 | dicas acionáveis quando a cobertura é baixa ou o orçamento corta | 9 | testes de unidade | sim, aditivo |
| 4 | excluir fragmentos já entregues (parâmetro opcional) | 3 | testes; ganho real só no M10 | sim, aditivo |

O item 1 é o de maior ganho econômico: o conteúdo é o mesmo, só muda a
embalagem. O risco é a qualidade da resposta mudar com o formato; sem gastar, a
verificação fica para as sessões de agente do M10, que já vão medir custo por
tarefa resolvida.

Fica para depois (custa ou exige medir respostas): rerank mais profundo,
orçamento adaptativo, modo local de provedores.

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
