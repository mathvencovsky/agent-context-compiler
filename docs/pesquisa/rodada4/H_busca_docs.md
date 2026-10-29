# Frente H: como buscadores de documentação e de código ranqueiam e segmentam, e o que o NINA pode copiar sem LLM

Rodada 4, 2026-10-29. Fontes primárias: código e configs no GitHub (DocSearch, Typesense e Meilisearch scrapers, Sphinx, VitePress, MkDocs Material, Docusaurus local search, Read the Docs, MDN/yari, Pagefind, Zoekt). Blogs (GitHub Blackbird, Sourcegraph BM25F, docs da Algolia e do Elasticsearch) estavam bloqueados: os números deles vêm só de resumos de busca e estão marcados.

## 0. O que o NINA faz hoje (lido no repositório)

A brief fala em "Postgres tsvector + ts_rank". Não é isso que roda:

- `app/retrieve/lexical.py::overlap_ranks` carrega todos os pedaços das versões pedidas e pontua em Python. A nota é **quantas ocorrências de tokens do pedaço são palavras da pergunta** (`[a-z0-9]+` em minúsculas). Não tem IDF, não normaliza pelo tamanho, não satura a frequência e não distingue campos. Desempate por `(document_id, ord)`, top-50 (DECISIONS 2026-10-16). A coluna `tsv` (`to_tsvector('simple', text)`) e o índice GIN existem e ficam sem uso.
- O `chunk.text` é o prefixo `"A > B > C\n\n"` mais o Markdown (`app/index/chunker.py::_prefix`). Os títulos entram uma vez, com o mesmo peso de qualquer palavra do corpo. Uma palavra comum repetida 30 vezes num pedaço longo pesa 30.
- O tokenizador quebra `os.path.join` em `os`, `path`, `join` (três tokens comuns, perde a especificidade) e `snake_case` em `snake`, `case`. `useEffect` vira `useeffect`, que nunca casa com "use effect".
- O resto do caminho: vetor (pgvector, cosseno), RRF k=60 (`rrf.py`, peso léxico 1,0), rerank do top-30 (`rerank.py`), seleção gulosa por valor/√tokens com λ=0 (`select.py`). Nenhum sinal de nível de página (título, slug da URL) entra no ranking. Esses dados só aparecem em `hints.py::version_label`, para avisar de versões misturadas.
- Como os experimentos já medem (`bench/results/m10a/`): o `pool_A.md` olha se a resposta está no top-30 fundido que o reranker vê, só ranking, sem chamada paga. Atual: 87/139 com um pedaço ≥0,5 e 105/139 com o conjunto. BM25 de livro: 87 e 109. O `changes.md` item 2 conclui que o gargalo é a pergunta longa: dos 23 erros, 10 ficam fora das duas listas top-50.
- O que conferi na fatia dev (`bench/tasks/external.yaml`, 756 tarefas dev, rodado aqui sem custo):
  - toda tarefa tem 5 URLs: 567 têm 1 citada, 127 têm 2 e 62 têm 3 ou 4;
  - mediana de 152 palavras por pergunta;
  - 683/756 (90%) têm um token com cara de identificador (pontuado, camelCase, snake_case ou `f()`);
  - **um token do título da pergunta aparece no slug da URL em 28% das páginas citadas (287/1028) e em 9,6% das distratoras (263/2752)**. Isso é um sinal de página que o NINA ignora hoje.

Consequência: as quatro técnicas que todo buscador de docs usa (campos com peso por nível de título, IDF/saturação, tokens de identificador e prior de página) estão ausentes. O único teste já feito com elas foi BM25 sem campos, que empatou.

## 1. Lições acionáveis, por impacto esperado

Regra comum a todas (a mesma de `changes.md`): ajuste na metade A pela métrica de pool (sem custo, cache de embeddings, nenhuma consulta nova embedada porque só o lado léxico muda). Confirmação na metade B pela regra geral (DECISIONS 2026-10-16: IC 95% pareado do acerto no orçamento-alvo acima de zero; nenhum orçamento cai mais de 2 pts; cobertura por mil tokens acima da do strong_rag; latência mediana no máximo +50%). Grade e limiar fixados **antes** da primeira rodada, uma mudança por vez. A confirmação na B precisa de rerank para os pedaços novos no pool: ou o reranker aberto local do passo de recuperação da Fase P (custo zero, máquina do humano) ou o cache Jina mais poucas chamadas novas, o que o humano não quer pagar. Toda mudança vai para `app/retrieve/lexical.py` (função compartilhada com `bench/runners/ours._lexical_ranks`, para manter a paridade benchmark/produto) e entra na emenda de recongelamento.

Métrica auxiliar proposta (sem custo, mede o estágio léxico isolado): Recall@10/@50 e MRR do pedaço-resposta **só na lista léxica**. Hoje o pool fundido dilui o efeito: com RRF k=60, subir de 40º para 5º na lista léxica muda pouco a soma. O aceite continua sendo a métrica de pool. A auxiliar serve para diagnóstico.

### L1. BM25F com campos por nível de título (o modelo "hierarchy lvl0–lvl6 + content")

**O que as fontes mostram.** Todos os buscadores de docs separam títulos do corpo e dão mais peso ao título. Os números são públicos e estão no código:

| Sistema | Peso título vs corpo | Fonte |
|---|---|---|
| Algolia DocSearch (scraper v2 e crawler v3) | critério `attribute` lexicográfico: `hierarchy_radio.lvlN` (o título do próprio registro) > `hierarchy.lvlN` (ancestrais) > `content`; o `customRanking` desempata por `desc(weight.level)` (lvl0=100, lvl1=90 … content=0) e `asc(weight.position)` | `algolia_settings.py`, `abstract_strategy.py::get_level_weight`, `templates.mdx` |
| Meilisearch docs-scraper | mesma ordem: `words, typo, attribute, proximity, exactness, page_rank:desc, level:desc, position:asc` | `meilisearch_helper.py` |
| Read the Docs (Elasticsearch) | `title^1.5`, `sections.title^2`, `sections.content^1`; a página vale a nota da **melhor** seção (`score_mode="max"`, "o padrão (média) penaliza páginas cujas outras seções casam fraco") | `faceted_search.py` |
| Sphinx `searchtools.js` | título 15, termo 5 (3×); parcial 7 contra 2 | `Scorer` |
| Pagefind | h1 7,0 … h6 2,0, corpo 1,0, escala quadrática ("2,0 ≈ 4× o impacto"); título da página 5× | `weighting.md`, `ranking.md` |
| MkDocs Material | `title` boost 1e3, `text` 1e0 | `plugin.py` |
| Zoekt (Sourcegraph), BM25 | casamento em nome de arquivo ou **definição de símbolo** conta 5× na frequência do termo ("interpretação BM25F"); k1=1,2, b=0,75 (Lucene) | `index/score.go::calculateTermFrequency` |
| Sourcegraph (blog, **só resumo de busca**) | BM25F com campos nome de arquivo/símbolo/conteúdo: cerca de **+20% em todas as métricas internas**, no release 6.2 | sem harness público |

**Por que importa para o NINA.** O chunker já guarda `heading_path` por pedaço (o equivalente de lvl0–lvlN). Hoje esses títulos entram no `text` com peso 1, igual ao corpo. As perguntas do dev são longas (152 palavras): uma pergunta com "state", "component" e "reset" casa em dezenas de pedaços pelo corpo. O pedaço cujo **título** é "Resetting state at the same position" deveria ganhar, e é exatamente o que DocSearch, RTD e Sphinx fazem. O BM25 sem campos (`m10a_pool.bm25_ranks`) empatou em 87. Ele corrige o IDF e o tamanho, mas não o campo.

**Mudança proposta.** `bm25f_ranks(items, query)` em `app/retrieve/lexical.py`, com IDF calculado sobre os pedaços do próprio pedido (5 URLs). Quatro campos por pedaço:
1. `leaf` = `heading_path[-1]`, o "radio" do DocSearch;
2. `ancestors` = `heading_path[:-1]`, que inclui o título da página quando o extrator o põe em `heading_path[0]`;
3. `prose` = Markdown sem blocos de código;
4. `code` = conteúdo dos blocos ``` do `md`.

TF ponderada no estilo BM25F: `tf~ = Σ_campo w_campo · tf_campo`, saturada uma vez com k1=1,2, e b=0,75 sobre o tamanho do corpo.
Grade fixada antes: `w_leaf ∈ {1, 3, 5}`, `w_ancestors = w_leaf/2`, `w_prose = w_code = 1`. `w_leaf = 1` é o BM25 de controle, que deve reproduzir os 87/109.
**Aceite na A:** melhor variante com um pedaço ≥0,5 em pelo menos 91/139 (+4, acima da faixa de ruído 84–90 das variantes já testadas) e conjunto ≥ 105. Senão para, sem levar à B.
**Custo:** zero para medir (máquina do humano, só ranking). Muda `app/` (lexical.py). Não precisa de migração: `heading_path` já está na tabela.
**Força da evidência:** alta como prática (todos os sistemas convergem e os pesos são públicos); média como ganho (só o Sourcegraph publica número, +20%, sem harness, em código e não em docs).

### L2. Prior de página: título e slug da URL contra o título da pergunta

**O que as fontes mostram.** No DocSearch, `lvl0`/`lvl1` são o título da página e da seção de topo. Eles vêm primeiro em `searchableAttributes`, e `weight.page_rank` é o primeiro critério do `customRanking`. O MDN (yari, `search.tsx`) busca só em título e "slugTail" e põe no topo o casamento exato de título ou slug. O RTD dá `title^1.5` no nível da página e multiplica por um `rank` de página. O Docusaurus local search mantém índices separados para título, título de seção, descrição e conteúdo, e ordena resultados de conteúdo logo depois do título da própria página (`sortSearchResults.ts`). O Zoekt dá até 7000 pontos para casamento no nome do arquivo (`scoreBase`) contra 500 para palavra no conteúdo (`scoreWordMatch`).

**Por que importa para o NINA.** Toda tarefa do dev tem 5 URLs e quase sempre só 1 ou 2 são citadas. Medi aqui que um token do título da pergunta aparece no slug da URL **2,9× mais** nas citadas (28%) do que nas distratoras (9,6%). O NINA joga isso fora: o ranking é por pedaço, sem nenhum sinal de página. Os 10 erros "fora das duas listas top-50" do `changes.md` são candidatos naturais: a página certa está lá, mas nenhum pedaço dela casa forte com a pergunta inteira.

**Mudança proposta.** Por página:
- `page_text` = título (`Metadata.title`) + tokens do slug da URL + todos os `heading_path` distintos;
- `page_score` = BM25 de `question_title` contra `page_text` (IDF sobre as 5 páginas). O título da pergunta está em `ExternalTask.question_title`; no produto não existe, então a variante de produto usa as primeiras N palavras da consulta, ou o próprio campo `query` quando curto. Medir as duas.

Variante A: uma terceira lista no `rrf_fuse` (já aceita `weights`), em que cada pedaço herda a posição da sua página, com peso pré-fixado ∈ {0,25; 0,5}.
Variante B: multiplicar a nota L1 por `(1 + α·page_norm)`, com α ∈ {0,5; 1}.
**Diagnóstico primeiro (sem custo):** na A, em quantas tarefas a página citada fica em 1º entre as 5 pelo `page_score`. Se não passar de 40% (o acaso com 1–2 citadas é ~25–30%), para.
**Aceite na A:** igual ao L1.
**Custo:** zero para medir. Muda `app/` (lexical.py ou assemble.py, que já tem `url_by_version`; o título está na IR guardada).
**Força:** média. O sinal existe no dev (medido aqui), mas é um corte das tarefas mineradas do Stack Overflow: na pergunta real de um agente o "título" pode não existir. Por isso a variante de produto precisa ser medida separadamente.

### L3. Tokens de identificador: inteiro + subtokens, e bônus de símbolo exato

**O que as fontes mostram.**
- DocSearch indexa cada título duas vezes, `hierarchy` e `hierarchy_camel` (`camelCaseAttributes`), e os templates v3 põem `separatorsToIndex: '_'`.
- O Typesense DocSearch usa `token_separators: ["_", "-"]` por padrão (`typesense_helper.py`).
- O Elasticsearch `word_delimiter_graph` (doc oficial, só resumo de busca): `split_on_case_change` (PowerShot → Power, Shot), `preserve_original` (mantém o inteiro), `catenate_all`. A doc recomenda usar a expansão na consulta e o tokenizador whitespace/keyword, não o standard.
- O RTD usa o analisador `simple` para que `python.submodule` vire `[python, submodule]`.
- Sphinx: casar o nome completo do objeto **ou a última parte depois do ponto** vale `objNameMatch` 11; parcial na última parte, 6; somado à prioridade do objeto (+15/+5/−5).
- Zoekt: símbolo inteiro 7000, borda do símbolo 5500, sobreposição 4000, palavra inteira 500, palavra parcial 50. Com BM25, casar em definição de símbolo conta 5× na TF.
- A rodada 3 (`B_recuperacao.md` §L6) já recomendou o split. O que esta frente acrescenta é o **bônus de símbolo exato** e a regra da última parte do Sphinx.

**Por que importa para o NINA.** 90% das perguntas do dev têm identificadores. Hoje `os.path.join` vira três palavras comuns, e `useState` na pergunta não casa com "use state" no texto.

**Mudança proposta (duas variantes, medidas em separado).**
- L3a (tokenizador): para cada palavra separada por espaço, emitir o identificador inteiro em minúsculas (`os.path.join`, `use_state`, `usestate`) **e** os subtokens (quebra em `.`, `_`, `::`, `-`, `/` e na troca de caixa, **antes** de baixar a caixa). Mesma função na consulta e no pedaço. Sem L1, só ela, para isolar o efeito.
- L3b (bônus): extrair da consulta os símbolos (regex do diagnóstico acima, comprimento ≥ 4, fora de uma lista de palavras de linguagem como `import` e `return`). Ocorrência inteira de um símbolo no pedaço soma `5 ×` na TF (Zoekt). Casar a última parte depois do ponto vale metade (Sphinx). Se o símbolo está no `leaf` ou nos `ancestors`, o peso de campo do L1 multiplica.

**Aceite na A:** igual ao L1, medido sobre L1 aceito, ou sobre o atual se o L1 cair. Corte extra: nenhuma perda nas tarefas `fact` sem identificador.
**Custo:** zero para medir. Muda `app/`.
**Força:** alta como prática em código (Zoekt, GitHub e Sphinx convergem); média para documentação em prosa. Sem número público em docs.

### L4. Critério "words": termos distintos, não ocorrências

**O que as fontes mostram.** O primeiro critério da DocSearch e da Meilisearch é `words`: quantas palavras **distintas** da consulta o registro tem, "não quantas vezes a palavra aparece" (doc Algolia, só resumo de busca). Com `removeWordsIfNoResults: 'allOptional'`, nenhuma palavra é obrigatória. O MkDocs Material multiplica a nota por `(1 + boost²)`, com `boost` = fração dos termos da consulta presentes mais 1 se o documento é a raiz da página (`search/_/index.ts`). O Pagefind tem `termSaturation` (1,4) e `pageLength` (0,75) e documenta o caso "páginas que ranqueiam bem por um número extremamente alto de termos".

**Por que importa para o NINA.** O `overlap_ranks` faz o contrário: soma ocorrências. É o defeito apontado no `m10a_pool.py` ("palavras comuns e pedaços longos vencem"). O BM25 resolve em parte, porque satura. A cobertura ponderada por IDF é o que o DocSearch usa como primeiro critério e o BM25 não tem explicitamente.

**Mudança proposta.** Fator multiplicativo sobre a nota L1: `(1 + c²)`, com `c` = Σ IDF dos termos distintos casados / Σ IDF dos termos da consulta. É a fórmula do MkDocs, com IDF para não premiar casar "the".
**Aceite:** só depois do L1. Mesmo limiar.
**Custo:** zero. Muda `app/`.
**Força:** média. Sem número público. Barato de testar junto com o L1 (uma linha).

### L5. "Distinct" por página no pool do reranker (cota mínima)

**O que as fontes mostram.** DocSearch e Meilisearch usam `distinct: true` com `attributeForDistinct: 'url'`: uma entrada por página na lista. O RTD pontua a página pela melhor seção e devolve até 3 seções por página (`inner_hits size=3`).

**Por que importa para o NINA.** O NINA não precisa de "um por página", porque o contexto pode ter vários pedaços da página certa. Mas a pergunta que o `changes.md` deixou aberta é por que a resposta não entra no top-30. Uma cota mínima garante que o **melhor pedaço de cada página** com algum casamento chegue ao reranker, mesmo quando uma página longa e genérica ocupa as 30 vagas.

**Mudança proposta.** Antes de cortar o RRF em 30, reservar até 1 vaga por documento para o seu melhor pedaço fundido. São no máximo 5 vagas e as outras 25 seguem a ordem do RRF. Medir com e sem L2.
**Diagnóstico primeiro:** nos erros da A, em quantos a página do pedaço-resposta tem **zero** pedaços no top-30. Se forem menos de 4, não vale a mudança.
**Custo:** zero para medir. Muda `app/retrieve/assemble.py` (uma função antes do `rerank_candidates`).
**Força:** baixa a média. É prática de UI de busca, não de RAG. Útil só se o diagnóstico mostrar páginas inteiras excluídas.

### L6. Plurais e flexões (ignorePlurals / stemmer)

**O que as fontes mostram.** DocSearch: `ignorePlurals: true`, plurais contam como exatos. O Sphinx indexa com stemmer (Porter, no `searchindex`). Pagefind e MkDocs usam o pipeline do lunr, também com stemmer.
**Por que importa.** `'simple'`, e agora `[a-z0-9]+`, não junta "props" e "prop", nem "resetting" e "reset". Em perguntas longas de linguagem natural contra títulos curtos, isso perde casamentos de título, justamente onde o L1 põe peso.
**Mudança proposta.** Em vez do stemmer completo (que estraga identificadores), aplicar dobra de plural e de `-ing`/`-ed` só a tokens alfabéticos que não vieram de um identificador (L3). Regras próprias, sem dependência nova. Se for usar `snowballstemmer` (BSD-3), registrar em DECISIONS.
**Aceite:** igual ao L1. **Custo:** zero. Muda `app/`. **Força:** baixa a média: ganho pequeno típico, risco em nomes de API.

### L7. Duplicatas entre versões: colapsar no pool, não só na seleção

**O que as fontes mostram.** O DocSearch trata versões como faceta (`docsearch:version` na meta, `attributesForFaceting: ['version', 'language', ...]`) e filtra por versão no front. O RTD filtra por `project`+`version`.
**Situação no NINA.** Já há o `mixed_versions` (`hints.py`), o U2 de preferir a versão citada (`m10a_version.py`) e a deduplicação por Jaccard de 5-gramas na seleção (`m10a_order_dedup.py`). O que falta é colapsar **no pool**: duas cópias quase iguais de URLs diferentes ocupam 2 das 30 vagas do reranker.
**Mudança proposta.** Antes do corte em 30, juntar pedaços de documentos diferentes com o mesmo `heading_path[-1]` e Jaccard de 5-gramas ≥ 0,8, mantendo o de melhor RRF. Só vale se o diagnóstico de `m10a_order_dedup` mostrar pares desse tipo em pelo menos 5% das tarefas.
**Custo:** zero. **Força:** baixa: só ajuda as 48 tarefas `version` e as de páginas espelhadas.

### O que **não** copiar (e por quê)

- **Voltar ao `ts_rank`/`ts_rank_cd` do Postgres.** Não usam estatística global de IDF. O `setweight` A/B/C/D dá peso de campo, mas sem IDF repetiria o defeito atual. Para menos de mil pedaços por pedido, BM25F em Python é mais simples e fica idêntico ao benchmark.
- **Índice de trigramas ou sparse n-grams (Zoekt, Blackbird).** Servem para substring e regex em bilhões de documentos. O NINA pontua os pedaços de 5 páginas na memória, então o problema que eles resolvem não existe aqui.
- **Tolerância a erro de digitação.** Agentes raramente erram, e o typo transforma identificadores em outros (a própria Algolia desliga typo em números).
- **`position` como sinal forte.** O DocSearch usa como último desempate. O NINA já desempata por `ord`, e não há evidência de que o começo da página seja mais relevante para perguntas de código.
- **Pesos de título enormes (1e3 do MkDocs).** Servem para autocompletar com a consulta curta de um humano. Numa pergunta de 152 palavras, um peso de título muito alto premiaria qualquer seção cujo título repete uma palavra comum. Por isso a grade do L1 para em 5 (faixa de RTD, Sphinx e Zoekt).

## 2. Onde o NINA já está igual ou melhor

- **Segmentação.** Pedaços por seção com o caminho de títulos no texto (`chunker.py`) são o modelo de registro do DocSearch (hierarquia repetida em cada registro). São mais adequados a LLM: 200–500 tokens contra o parágrafo do DocSearch (`content: 'p, li'`), que serve para navegação e não para contexto. Tabelas, código e listas grandes são divididos repetindo os cabeçalhos, coisa que o DocSearch nem indexa (o `content` padrão exclui `pre` e `td`).
- **Híbrido com reranker e seleção por orçamento.** Nenhum buscador de docs open source combina léxico com denso nem tem orçamento. O RTD e o DocSearch são puramente léxicos.
- **Determinismo e paridade.** Desempate por chave e a mesma função no produto e no benchmark. Os buscadores comerciais não publicam harness. O único número de ganho (Sourcegraph, +20%) é interno.
- **Proveniência.** `block_id`, página e citação literal. O DocSearch dá só URL e âncora.
- **Versões.** O aviso `mixed_versions` e o U2 já cobrem o que a faceta de versão do DocSearch faz, sem pedir que o site marque `docsearch:version`.

## 3. Onde o NINA está atrás

1. **Sem IDF, sem saturação, sem normalização por tamanho:** a contagem de ocorrências é o pior ranqueador léxico entre todos os sistemas lidos. Até o Sphinx JS conta termos distintos com pesos por campo.
2. **Sem peso por campo:** o título vale o mesmo que o corpo. Todos os sistemas lidos dão de 1,5× a 7× (ou mais) ao título.
3. **Sem sinal de página:** título e slug da URL não entram no ranking, embora o dev mostre que o slug separa citadas de distratoras (28% contra 9,6%).
4. **Tokenizador cego para identificadores:** nem preserva o inteiro pontuado nem quebra camelCase. DocSearch, Typesense, RTD e Elasticsearch tratam isso explicitamente.
5. **Sem plural/flexão.**
6. **Fragmento sem âncora `#seção`:** o DocSearch devolve `url#anchor` em cada registro. Já está anotado como fora do escopo (PROGRESS, passo 5) porque muda a IR. Fica listado só como lacuna.

## 4. Fontes lidas na íntegra

- Algolia DocSearch scraper v2, configuração do índice: https://raw.githubusercontent.com/algolia/docsearch-scraper/master/scraper/src/strategies/algolia_settings.py
- DocSearch scraper, extração de registros (hierarquia, radio, weight, position, objectID): https://raw.githubusercontent.com/algolia/docsearch-scraper/master/scraper/src/strategies/default_strategy.py
- DocSearch scraper, `get_level_weight` e seletores: https://raw.githubusercontent.com/algolia/docsearch-scraper/master/scraper/src/strategies/abstract_strategy.py
- DocSearch scraper, `hierarchy_radio`: https://raw.githubusercontent.com/algolia/docsearch-scraper/master/scraper/src/strategies/hierarchy.py
- DocSearch crawler v3, templates de config (Docusaurus, VitePress, etc.): https://raw.githubusercontent.com/algolia/docsearch/main/packages/website/docs/templates.mdx
- Typesense DocSearch scraper, schema e `token_separators`: https://raw.githubusercontent.com/typesense/typesense-docsearch-scraper/master/scraper/src/typesense_helper.py
- Meilisearch docs-scraper, `rankingRules`/`distinctAttribute`: https://raw.githubusercontent.com/meilisearch/docs-scraper/main/scraper/src/meilisearch_helper.py
- Sphinx `searchtools.js` (Scorer, busca de objetos, termos e títulos): https://raw.githubusercontent.com/sphinx-doc/sphinx/master/sphinx/themes/basic/static/searchtools.js
- VitePress local search (minisearch, seções por título): https://raw.githubusercontent.com/vuejs/vitepress/main/src/node/plugins/localSearchPlugin.ts e https://raw.githubusercontent.com/vuejs/vitepress/main/src/client/theme-default/components/VPLocalSearchBox.vue
- MkDocs Material, plugin de busca (boosts de campo) e worker (boost pós-consulta): https://raw.githubusercontent.com/squidfunk/mkdocs-material/master/material/plugins/search/plugin.py e https://raw.githubusercontent.com/squidfunk/mkdocs-material/master/src/templates/assets/javascripts/integrations/search/_/index.ts
- Docusaurus local search (easyops): https://raw.githubusercontent.com/easyops-cn/docusaurus-search-local/master/docusaurus-search-local/src/server/utils/buildIndex.ts e https://raw.githubusercontent.com/easyops-cn/docusaurus-search-local/master/docusaurus-search-local/src/client/utils/sortSearchResults.ts
- Read the Docs, busca server-side: https://raw.githubusercontent.com/readthedocs/readthedocs.org/main/readthedocs/search/faceted_search.py e https://raw.githubusercontent.com/readthedocs/readthedocs.org/main/readthedocs/search/documents.py
- MDN yari, busca de títulos no cliente: https://raw.githubusercontent.com/mdn/yari/main/client/src/search.tsx
- Pagefind, ranking e pesos: https://raw.githubusercontent.com/CloudCannon/pagefind/main/docs/content/docs/ranking.md e https://raw.githubusercontent.com/CloudCannon/pagefind/main/docs/content/docs/weighting.md
- Zoekt, pontuação (BM25 com TF 5× para símbolo e nome de arquivo; constantes de linha): https://raw.githubusercontent.com/sourcegraph/zoekt/main/index/score.go e https://raw.githubusercontent.com/sourcegraph/zoekt/main/index/contentprovider.go
- Zoekt, design (trigramas): https://raw.githubusercontent.com/sourcegraph/zoekt/main/doc/design.md
- Zoekt, `SearchOptions.UseBM25Scoring`: https://raw.githubusercontent.com/sourcegraph/zoekt/main/api.go

Só por resumo de busca (não lidos): o post do GitHub sobre o Blackbird, o post do Sourcegraph sobre BM25F (+20%), a doc de critérios de ranking da Algolia e a doc do `word_delimiter_graph` do Elasticsearch.

## 5. Fontes inacessíveis que valem leitura completa (salvar em PDF)

1. **Sourcegraph: "Keeping it boring (and relevant) with BM25F"**: https://sourcegraph.com/blog/keeping-it-boring-and-relevant-with-bm25f. É o único número público (+20%) de BM25F com campos símbolo/arquivo em busca de código, e deve trazer a metodologia de avaliação e os pesos. Base para calibrar a grade do L1/L3.
2. **GitHub: "The technology behind GitHub's new code search"**: https://github.blog/engineering/architecture-optimization/the-technology-behind-githubs-new-code-search/. Tokenização de identificadores e sinais de ranking (definição de símbolo, caminho). Confirma ou refuta o bônus de símbolo do L3b.
3. **Algolia, Ranking criteria (in depth)**: https://www.algolia.com/doc/guides/managing-results/relevance-overview/in-depth/ranking-criteria. Definição exata de `words`, `attribute`, `exact`, `proximity` e do `unordered()`, que o L1/L4 imitam.
4. **Elasticsearch, Word delimiter graph token filter**: https://www.elastic.co/guide/en/elasticsearch/reference/current/analysis-word-delimiter-graph-tokenfilter.html. Regras de split, `preserve_original` e o aviso sobre posições na indexação. Referência para o tokenizador do L3a.
5. **Robertson, Zaragoza, Taylor (2004), "Simple BM25 extension to multiple weighted fields"** (CIKM): a definição canônica do BM25F (TF ponderada por campo antes da saturação), para o L1 seguir a fórmula certa. A URL do PDF não foi confirmada nesta sessão: procurar pelo título no ACM DL (doi 10.1145/1031171.1031181).
