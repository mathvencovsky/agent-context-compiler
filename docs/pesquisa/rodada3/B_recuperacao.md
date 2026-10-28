# Frente B: qualidade de recuperação para perguntas sobre documentação (rodada 3, 2026-10-28)

Escopo: chunking, busca léxica, densa, rerankers, seleção e compressão de contexto, tipo de pergunta. Tudo avaliado contra o código atual do NINA:
- `app/index/chunker.py`: por seção de `heading_path`, alvo de 200 a 500 tokens, máximo de 800, prefixo `A > B > C`.
- `app/retrieve/lexical.py`: contagem de sobreposição de palavras `[a-z0-9]+`, sem IDF, sem normalização por tamanho.
- `app/retrieve/vector.py`: pgvector com OpenAI `text-embedding-3-small`.
- `app/retrieve/rrf.py`: RRF k=60, `lexical_weight` 1.0.
- `app/retrieve/rerank.py`: Jina `jina-reranker-v2-base-multilingual`, só o top-30.
- `app/retrieve/select.py`: guloso `V/sqrt(t)`, λ=0.
- `app/config.py`: limiares de cobertura calibrados sobre a distribuição de scores do Jina v2.

Restrição de acesso: nesta sessão só o GitHub (`raw.githubusercontent.com`) respondeu. arxiv.org, jina.ai, huggingface.co, alphaxiv, semantic scholar, blogs de fornecedores e github.io estão bloqueados. Quando um número vem só de resumo de busca, digo isso.

---

## 1. Lições acionáveis para o NINA (ordenadas por impacto esperado)

### L1. O reranker atual é o elo mais fraco, e o formato das perguntas do M10 piora o caso

**O que as fontes mostram**
- **Tabela oficial do Qwen3-Embedding** (README do repositório, lida na íntegra). Avaliação no top-100 de um retriever denso, com código de avaliação público. O reranker que o NINA usa aparece na tabela pelo nome:

  | Modelo | Parâmetros | MTEB-R | MTEB-Code | MLDR (doc longo) |
  |---|---|---|---|---|
  | Jina-multilingual-reranker-v2-base (o do NINA) | 0,3B | 58,22 | **58,98** | 39,66 |
  | BGE-reranker-v2-m3 | 0,6B | 57,03 | 41,38 | 59,51 |
  | Qwen3-Reranker-0.6B | 0,6B | 65,80 | **73,42** | 67,28 |
  | Qwen3-Reranker-4B | 4B | 69,76 | **81,20** | 69,97 |
  | Qwen3-Reranker-8B | 8B | 69,02 | 81,22 | 70,19 |
  | *(sem rerank: só o Qwen3-Embedding-0.6B)* | 0,6B | 61,82 | **75,41** | 50,26 |

  Em código, o Jina v2 fica **abaixo do próprio primeiro estágio sem rerank** (58,98 contra 75,41). Ou seja, reordenar com ele piora a lista que recebe.
- **Model card oficial do Jina v2** (via resumo de busca; não consegui abrir o HF):
  - contexto total de 1024 tokens;
  - `max_query_length=512`;
  - documentos maiores viram janelas deslizantes com scores combinados;
  - o fornecedor marca o modelo como *deprecated*;
  - os pesos são CC-BY-NC (o uso por API é permitido; rodá-lo localmente seria problema de licença).
- **Medido agora, sem custo, nas 3.054 tarefas do M10** (`bench/tasks/external.yaml`, título + corpo em tokens cl100k):
  - mediana de 275, p75 de 438, p90 de 673;
  - **18,5% passam de 512 tokens** e já perdem parte da pergunta no reranker;
  - o tokenizador XLM-R do Jina gera mais tokens que o cl100k em código, então a proporção real é maior.
- **O que sobra para o pedaço:** de 1024 menos a pergunta, sobram uns 500 a 700 tokens, e os pedaços do NINA vão até 800 mais o prefixo. Uma fração relevante dos pares (pergunta, pedaço) é julgada por janelas, não pelo pedaço inteiro.
- **Histórico no próprio NINA:**
  - o rerank ajudou no M8, mas pouco: +0,049 no test e +0,062 no dev (DECISIONS, entrada do M8 pós-correção);
  - no M10a, o rerank score não serviu como sinal de parada (`adaptive_A.md`: "low-scored chunks still carry parts of the answer section").
- **Alternativas de 2025 e 2026:**
  - Qwen3-Reranker 0.6B, 4B e 8B: Apache 2.0, 32K de contexto, aceita instrução;
  - mxbai-rerank-v2 base e large: Apache 2.0, até 8K (32K compatível), alega ser forte em código; README lido;
  - zerank-1-small: Apache 2.0, 1,7B, 32K; Code 0,730 contra Cohere 3.5 0,724, primeiro estágio `text-embedding-3-small`; números do model card, só resumo;
  - jina-reranker-v3 e v3.5 (0,6B, *listwise*): CoIR 70,64 contra 73,91 do Qwen3-Reranker-4B na tabela da própria Jina; pesos CC-BY-NC, uso só pela API; só resumo;
  - Voyage rerank-2.5: 32K, +2,34% sobre o Qwen3-Reranker-8B segundo a própria Voyage; sem harness público;
  - Cohere Rerank 4 Pro e Fast: 32K; não achei número em código.

**Por que importa para o NINA**
- O rerank decide o `value` da seleção (`select.py`) e alimenta `_coverage` e `budget_limited` (`assemble.py`). Com um reranker fraco em código e truncando perguntas longas, toda a etapa final herda o ruído.
- É o componente mais antigo do pipeline: 2024, descontinuado pelo próprio fornecedor.

**Mudança proposta (experimento com a regra das metades A/B)**
1. Diagnóstico sem custo: medir, com o tokenizador do Jina (XLM-R) ou com o cl100k × 1,3 como aproximação, quantos pares do dev passam de 1024 tokens e quantas perguntas passam de 512.
2. Trocar o reranker, sem tocar mais nada, e medir o acerto nas metades A e B.
   - Candidatos de pesos abertos com licença compatível: Qwen3-Reranker-0.6B (roda em CPU para 30 a 100 pares, com latência a medir) e mxbai-rerank-base-v2.
   - Candidatos por API: Voyage rerank-2.5, Cohere Rerank 4 Fast e Jina v3 pela API (mesma conta, mudar só o `reranker_model`).
   - Usar instrução no Qwen3 e no mxbai, por exemplo: "Given a developer's question, retrieve documentation passages that answer it". O Qwen3 relata de 1% a 5% de ganho com instrução.
3. **Atenção:** `coverage_low` e `coverage_high` (0,6137 e 0,7746) são quantis da distribuição do Jina v2. Qualquer troca de reranker exige recalibrar esses limiares no dev, senão `low_coverage` e `budget_limited` ficam errados.

**Custo**
- Exige mudar o `app/`, que está congelado pela Emenda 15: só entra com emenda e recongelamento.
- Por API é run paga (centenas de chamadas de rerank por metade). Local é grátis em dinheiro, mas pede CPU ou GPU na máquina do humano e, no caso do Qwen3, uma dependência nova (transformers ou torch, Apache e BSD).

**Força da evidência**
- Tabela do Qwen3: fonte primária lida na íntegra, mas é o fornecedor avaliando, com código público.
- Limites do Jina v2: model card, visto só via resumo de busca.
- Comprimento das perguntas: medido por mim no repositório.

---

### L2. O embedding atual (`text-embedding-3-small`) empata com o BM25 no benchmark mais parecido com o M10

**O que as fontes mostram**
- **FreshStack** (Waterloo + Databricks, NeurIPS 2025 D&B; incluído no RTEB, o sucessor do MTEB):
  - é recuperação de pedaços de documentação técnica e código para perguntas reais do Stack Overflow, com 5 domínios (LangChain, YOLO, Laravel, Angular, Godot);
  - é, de longe, o benchmark público mais parecido com o M10 do NINA;
  - li o `leaderboard_data.json` oficial na íntegra (59 linhas, atualizado em 2026-10-23).
- **Médias nos 5 domínios:**

  | Modelo | Recall@50 | Coverage@20 | α-nDCG@10 |
  |---|---|---|---|
  | **OpenAI text-embedding-3-small (NINA)** | **0,330** | 0,480 | 0,208 |
  | BM25 | 0,316 | 0,448 | 0,218 |
  | OpenAI text-embedding-3-large | 0,373 | 0,537 | 0,248 |
  | Qwen3-Embedding-0.6B (Apache 2.0) | 0,394 | 0,543 | 0,262 |
  | voyage-4-nano (340M, pesos abertos) | 0,438 | 0,583 | 0,280 |
  | Qwen3-Embedding-4B | 0,490 | 0,656 | 0,347 |
  | voyage-4-large | 0,510 | 0,685 | 0,349 |
  | Qwen3-Embedding-8B | 0,525 | 0,689 | 0,365 |
  | Fusion (BM25+BGE+E5+Voyage), 2024 | 0,539 | 0,669 | 0,343 |
  | Cohere Embed v5.0 Pro | 0,572 | 0,727 | 0,415 |
  | Octen-Embedding-8B (aberto) | 0,609 | 0,741 | 0,407 |
  | Mixedbread Wholembed v4 (late interaction, proprietário) | 0,662 | 0,796 | 0,496 |

- **Mesmo padrão no próprio NINA** (`bench/results/m10a/pool_A.md`), na contagem de tarefas com o pedaço da resposta no top-30 fundido:
  - vetor sozinho: 81 de 139;
  - léxico BM25: 87;
  - híbrido atual: 87.

  O lado denso quase não acrescenta, o que bate com o FreshStack: o 3-small empata com o BM25 neste tipo de pergunta.
- O FreshStack recomenda modelos que aguentem **perguntas longas e código** (2048 tokens nos dois lados). O 3-small aceita 8K de entrada, então o problema dele é qualidade, não truncamento.

**Por que importa para o NINA**
- O changes.md do M10a concluiu que o gargalo do pool é "a pergunta longa, não o scorer léxico". O FreshStack mostra que a parte densa da recuperação é o que mais varia entre modelos nesse cenário: de 0,33 a 0,61 de Recall@50.
- Recuperar mais pedaços certos no pool é o teto de tudo que vem depois.

**Mudança proposta**
1. Experimento só de ranking (o pool do rerank), que é barato: reembutir os pedaços do dev com 1 ou 2 embedders melhores e medir "pedaço da resposta no top-30 fundido", como em `pool_A.md`.
   - Candidatos: voyage-4-large ou voyage-4-lite pela API (no HUMANO.md a Voyage já está listada como opção); Qwen3-Embedding-0.6B ou 4B local (Apache 2.0); Cohere Embed v5 pela API.
   - Usar o prompt de consulta que o próprio FreshStack usa no Qwen3: `Instruct: Given a technical question, retrieve relevant code snippets or technical documentation that best answer the question\nQuery: `.
2. Se o pool subir, levar ao pipeline completo nas metades A e B.
3. `embedding_dim` muda: 1536 vira, por exemplo, 1024. Isso pede migração da coluna pgvector. O indexador já reembute quando `embedding_model` muda (`indexer.py`).

**Custo**
- Embutir o corpus do dev pela API sai na casa de poucos dólares, porque o DECISIONS cita cerca de US$ 1 para o corpus inteiro com a OpenAI.
- Local não custa dinheiro, mas o 0.6B em CPU é lento para o corpus inteiro.
- No produto, é mudança no `app/`, que está congelado.

**Força da evidência**
- Leaderboard público com harness aberto (pacote `freshstack`, Apache 2.0) e dados CC-BY-SA: fonte primária lida na íntegra. É o dado mais forte desta frente.

---

### L3. Reranquear mais fundo que 30, mas só junto com um reranker melhor

**O que as fontes mostram**
- **No próprio NINA** (changes.md, item 2): "Of the 23 tasks whose answer chunk misses the pool, 10 are outside both top-50 lists". Logo, cerca de 13 estão nas listas top-50, mas fora do top-30 fundido que vai ao rerank. Reranquear a união (até 100) recupera até esses 13 casos sem mexer na recuperação.
- **"Drowning in Documents"** (Jacob et al., Databricks e Stanford, 2024; só resumos):
  - rerankers cross-encoder têm retorno decrescente com mais candidatos;
  - além de certo ponto, pioram, dando nota alta a documentos sem relação com a pergunta.
- **A tabela do Qwen3 usa top-100**, e o fornecedor recomenda isso para os modelos dele.
- Um estudo de 2026 (arXiv 2602.10321, só resumo) achou o ótimo em K=50 no pipeline dele.

**Por que importa:** `_DEFAULT_TOP_N = 30` em `rerank.py`, com o reranker mais fraco da tabela. Aprofundar com o Jina v2 tende a cair no caso "Drowning". Com um reranker forte, o top-50 a 100 é o padrão de 2025 e 2026.

**Mudança proposta**
1. Sem custo: a partir do cache, medir o teto ("pedaço da resposta no top-K fundido" para K = 30, 50 e 100). É só ranking.
2. Depois, varrer K em {30, 50, 100} junto com L1, nunca isoladamente com o Jina v2.

**Custo**
- O teto sai de graça.
- O rerank mais fundo custa de 1,7 a 3,3 vezes mais tokens de rerank por pedido e é mudança no `app/`.

**Força da evidência:** média. O teto no NINA é medível. Sobre a degradação, só li resumos.

---

### L4. Extração de segmentos relevantes (RSE) em vez de vizinhos às cegas

**O que as fontes mostram**
- **dsRAG** (MIT; README e `dsrag/rse.py` lidos na íntegra):
  - Cada pedaço recebe o valor `exp(-rank/decay) × relevância_absoluta − penalidade_irrelevante`, ajustado pelo tamanho.
  - O algoritmo procura segmentos contíguos (no máximo N pedaços por segmento e um total máximo) que maximizem a soma dos valores, sem cruzar fronteira de documento e repetindo até acabar o orçamento.
  - A penalidade controla o tamanho dos segmentos: 0,2 dá de 4 a 10 pedaços e 0,4 dá de 1 a 3.
  - É determinístico e não usa LLM.
- **Resultado relatado pela própria dsRAG** (nota de 0 a 10, GPT-4o como juiz, benchmark próprio KITE):

  | Configuração | Nota |
  |---|---|
  | Top-k | 4,72 |
  | RSE | 6,73 |
  | Cabeçalhos contextuais (CCH) + top-k | 6,04 |
  | CCH + RSE | 8,42 |

  No subconjunto "Sourcegraph" (docs técnicos): 5,7 → 9,4 com CCH+RSE.

**Por que importa para o NINA**
- O perfil da metade A mostrou que, de 29 tarefas sem pedaço com metade da seção-resposta, **25 eram seções divididas em vários pedaços** (20 sob o mesmo heading).
- A expansão por vizinhos ±1 foi rejeitada na metade B porque expande sempre, sem olhar se o vizinho é relevante, e gasta orçamento à toa (−3,4 pts em 4k).
- O RSE só junta vizinhos que têm valor positivo e trata o segmento como unidade. É exatamente a diferença que faltou.

**Mudança proposta:** substituir, ou complementar, o guloso de `select.py` por uma seleção de segmentos.
- A relevância de cada pedaço vem do rerank. Para vizinhos fora do top-K, uma alternativa é reranquear também os vizinhos `ord±1..3` dos melhores candidatos, ou interpolar pelo RRF.
- O valor por token entra no lugar do ajuste de tamanho do dsRAG.
- Testar no dev, com a mesma régua de 4k e 8k usada para a expansão.

**Custo:** mudança no `app/` (congelado), mais chamadas de rerank (os vizinhos) e run paga para medir.

**Força da evidência:** código lido na íntegra. Os números são do próprio fornecedor, com juiz LLM, sem intervalo de confiança e num domínio diferente do nosso.

---

### L5. Pôr título do documento e produto/versão no prefixo do pedaço (cabeçalho contextual determinístico)

**O que as fontes mostram**
- dsRAG: cabeçalhos contextuais com título do documento e da seção sobem a nota de 4,72 para 6,04 sozinhos, com os mesmos vieses citados em L4.
- voyage-context-3, que embute o pedaço com o contexto do documento inteiro: +6,76% sobre "contextual retrieval" e +23,66% sobre late chunking segundo a própria Voyage, só via resumo.
- O Contextual Retrieval da Anthropic já foi lido em rodadas anteriores. A novidade aqui é a variante **sem LLM**: título e cabeçalhos.

**Por que importa:** `chunker._prefix` usa só o `heading_path` do conteúdo. O `<title>` da página não entra no texto indexado nem no rerank. Ele costuma trazer o produto e a versão (por exemplo "Flask 3.0.x", "PostgreSQL 16") e às vezes a classe ou o módulo. Em pedidos com 5 a 20 URLs (o M10 usa 5), isso ajuda o vetor e o rerank a distinguir "Array.prototype.findLast" de "parseInt" quando o pedaço sozinho é genérico.

**Mudança proposta:** prefixo = `metadata.title` (normalizado) + ` > ` + `heading_path`, sem duplicar quando o H1 já for o título.

**Custo**
- Mudança no `app/` (congelado).
- Muda todos os `text`, então reembute tudo e reranqueia de novo (custo da run de confirmação). Também gasta alguns tokens a mais por pedaço entregue.

**Força da evidência:** fraca a média. É coerente com várias fontes, mas não há medição pública em documentação técnica com título determinístico.

---

### L6. Busca léxica: IDF, identificadores e mensagens de erro

**O que as fontes mostram**
- Preprint de maio de 2026 (arXiv 2605.18561, só resumo):
  - com tokenizador genérico fixo, o gargalo do BM25 em código é o IDF;
  - quando se controla o analisador, o certo é emitir **o identificador inteiro e as subpalavras**;
  - mudar o IDF levou o CodeSearchNet-Go de 0,2575 para 0,4874 de nDCG@10.
- Implementações abertas (ripvec, tldr-core) fazem split de camelCase e snake_case mantendo o token inteiro. Não publicam benchmark.
- BM25S (MIT, README lido): Lucene, ATIRE, BM25L e BM25+, só com numpy. As variantes diferem pouco entre si (Kamphuis et al., 2020).

**O que o NINA faz hoje** (`lexical.overlap_ranks`):
- conta toda ocorrência de toda palavra da pergunta, incluindo "the", "to" e "how";
- não tem IDF nem saturação de frequência, e favorece pedaços longos;
- `.lower()` acontece antes do regex, então `getElementById` vira um token só (`getelementbyid`) e não casa com "element by id";
- `max_tokens` vira `max` + `tokens` e perde o composto exato;
- não há bônus de frase, que é o que importa para mensagens de erro copiadas ("TypeError: Cannot read properties of undefined").

**Já medido no NINA:** BM25 contra a contagem empatou no pedaço único (87 contra 87) e subiu no conjunto (109 contra 105, `pool_A.md`). `lexical_weight` 0,25 deu 90. Nada foi levado à metade B.

**Mudança proposta (prioridade média e baixa):**
- BM25 com tokens inteiros + subtokens de camelCase, snake_case e nomes com ponto (split antes de baixar a caixa) + bônus de bigrama ou frase para trechos de erro;
- testar junto com um `lexical_weight` recalibrado depois de L2, porque, com um denso melhor, o peso ótimo do léxico muda.
- Não usar ParadeDB `pg_search` (AGPL) nem VectorChord-bm25 (AGPL ou ELv2): a regra de licenças proíbe.
- `pg_textsearch` (Timescale) tem licença PostgreSQL, mas exige PG17 ou mais novo, e o NINA usa 16.
- Para poucas URLs por pedido, BM25 em Python sobre os pedaços do pedido (`bm25s`, MIT) basta. O IDF sai do conjunto do pedido, o que é ruidoso com 1 página, mas aceitável com 5 ou mais.

**Custo:** mudança no `app/`. O ranking sai barato (sem rerank). A confirmação no pipeline completo é paga.

**Força da evidência:** fraca. O preprint não foi revisado e o NINA já mediu ganho pequeno.

---

### L7. Fusão: o híbrido vale mais quando os dois lados são bons; a combinação convexa supera o RRF

**O que as fontes mostram**
- FreshStack:
  - a fusão de 2024 (BM25+BGE+E5+Voyage) dá 0,539 de Recall@50, acima de cada componente;
  - o oráculo com fusão dá 0,755.
- Bruch, Gai e Ingber (ACM TOIS; só resumo; o PDF está em arXiv, bloqueado):
  - a combinação convexa de scores normalizados supera o RRF dentro e fora do domínio;
  - o RRF é sensível ao k;
  - a combinação convexa precisa de poucos exemplos para ajustar seu único parâmetro.

**Por que importa:** o NINA usa RRF ponderado com k=60 fixo. Com o denso fraco de hoje, a fusão quase não acrescenta (L2).

**Mudança proposta:** depois de L2, testar a combinação convexa (score do BM25 normalizado com o cosseno) contra RRF k=60 no pool do dev. É barato, só ranking.

**Custo:** baixo em dinheiro; é mudança no `app/`.

**Força da evidência:** média (artigo revisado, mas lido só em resumo).

---

### L8. Compressão por sentença: hoje não, por licença e por regra

- Os modelos lidos estão fora da regra de licenças ou da regra de não usar LLM:
  - **XProvence** (Naver, ECIR 2026, base bge-reranker-v2-m3) e **Provence** são CC-BY-NC(-ND): proibidos;
  - **EXIT** (Findings ACL 2025) classifica cada sentença com um modelo de 2B parâmetros, um LLM pequeno: fere o espírito da regra "sem LLM na seleção" e tem licença Gemma.
- **Proposta:** registrar no DECISIONS como fora do escopo. Se um dia houver compressão, o caminho compatível é pontuar sentenças com o mesmo reranker de licença aberta (Qwen3-Reranker ou mxbai) e cortar sentenças de valor baixo dentro dos pedaços escolhidos. Isso pede emenda e evidência própria.
- **Força da evidência:** apenas resumos.

---

### L9. Sinal de suficiência: recalibrar depois de trocar o reranker; não confiar no score bruto como parada

- No NINA, o A1 já mostrou que cortar por rerank baixo perde cobertura.
- Rerankers baseados em LLM pequeno pontuado como sim/não (Qwen3-Reranker usa a probabilidade de "yes") tendem a dar scores mais bem calibrados que os cross-encoders antigos. Isso é hipótese: não achei estudo que meça isso para parada.
- **Proposta:** depois de L1, refazer a calibração de `coverage_low` e `coverage_high` e reavaliar se `low_coverage` acerta (é uma métrica que o PREREG pode registrar). Não tentar parada adaptativa de novo sem um reranker novo.
- **Custo:** junto com L1.
- **Força da evidência:** fraca.

---

### L10. Coisas que NÃO valem esforço agora

- **Matryoshka e quantização binária:** o NINA busca em no máximo alguns milhares de pedaços por pedido. Memória e latência do pgvector não são o gargalo.
- **Chunking semântico ou guiado por LLM:**
  - Fraunhofer 2025 (só resumo): o tamanho ideal depende do conjunto, de 64 a 128 tokens para fatos curtos e de 512 a 1024 para contexto;
  - Canberra 2026 (36 métodos, só resumo): heurísticas estruturais, como agrupar parágrafos, superam métodos semânticos caros;
  - Queensland 2026 (só resumo): métodos estruturais simples superam os guiados por LLM na recuperação dentro do corpus;
  - NVIDIA 2025 (só resumo): página inteira como unidade teve a melhor média (0,648) em 5 conjuntos.

  O chunker do NINA, guiado por headings, já está no lado certo. A melhoria está em L4 (segmentos na consulta), não em mudar o tamanho na indexação.
- **Late interaction (ColBERT):** lidera o FreshStack com o Mixedbread Wholembed v4 (0,662, proprietário, lançado em 2026-10-23) e o pplx-v2-preview (0,605). Pede índice multivetor fora do pgvector. Vale vigiar, não implementar no MVP.

---

## 2. Onde o NINA já está igual ou melhor

1. **Chunking guiado pela estrutura com prefixo de seção.** É o que os estudos de 2026 apontam como melhor custo-benefício (Canberra, Queensland), e o prefixo `heading_path` é a forma determinística dos cabeçalhos contextuais do dsRAG. Tabelas cortadas por linha repetem o cabeçalho, e código é cortado por linhas em branco (`chunker.py`). Isso já supera o "512 tokens com 10% de overlap" dos blogs.
2. **A arquitetura é a de referência de 2025 e 2026:** híbrido léxico+denso, fusão, rerank cross-encoder e seleção por orçamento. O `strong_rag` independente (janela com overlap, BM25+vetor, rerank) empatou com o NINA em tudo (DECISIONS 2026-10-14) e com cerca de metade dos tokens no M8.
3. **Disciplina de avaliação:** regra das metades A/B, rejeição de ganhos que não replicam (limpeza da pergunta, vizinhos, parada adaptativa) e métricas de pool separadas do pipeline. Os vendedores citados acima (dsRAG, Voyage, Jina, Qwen) publicam só o próprio número.
4. **Seleção por valor/token sob orçamento**, em vez do top-k fixo: na metade B, a cobertura por mil tokens ficou acima da do `strong_rag` em todos os orçamentos (changes.md, item 1).
5. **Proveniência e determinismo:** nenhum dos sistemas lidos (dsRAG, FreshStack, Provence) entrega `block_id` e citação literal por fragmento.

## 3. Onde o NINA está atrás

1. **Embedding:** o `text-embedding-3-small` fica no quartil de baixo do FreshStack (0,330 de Recall@50, empatado com o BM25). Opções abertas e baratas dão de +19% (Qwen3-0.6B) a +85% (Octen-8B).
2. **Reranker:** Jina v2 (2024, descontinuado), 1024 tokens de contexto, abaixo até do primeiro estágio em código na tabela do Qwen3, truncando a pergunta em pelo menos 18,5% das tarefas do M10.
3. **Profundidade do rerank** de 30, contra 50 a 100 nos sistemas atuais. Há cerca de 13 casos por metade em que o pedaço certo está nas listas, mas não chega ao reranker.
4. **Não há seleção por segmento:** seções divididas são o maior grupo de falhas do dev.
5. **A busca léxica não tem IDF** e não trata identificadores camelCase nem frases de erro.
6. **O título do documento não entra no texto indexado.**

## 4. Fontes lidas na íntegra

- FreshStack, README do repositório: https://raw.githubusercontent.com/fresh-stack/freshstack/main/README.md
- FreshStack, página do projeto (HTML do repositório): https://raw.githubusercontent.com/fresh-stack/fresh-stack.github.io/master/index.html
- FreshStack, dados do leaderboard (59 modelos, out/2026): https://raw.githubusercontent.com/fresh-stack/fresh-stack.github.io/master/leaderboard_data.json
- Qwen3-Embedding e Qwen3-Reranker, README com as tabelas: https://raw.githubusercontent.com/QwenLM/Qwen3-Embedding/main/README.md
- mxbai-rerank v2, README: https://raw.githubusercontent.com/mixedbread-ai/mxbai-rerank/main/README.md
- dsRAG, README e código do RSE: https://raw.githubusercontent.com/D-Star-AI/dsRAG/main/README.md e https://raw.githubusercontent.com/D-Star-AI/dsRAG/main/dsrag/rse.py
- BM25S, README: https://raw.githubusercontent.com/xhluca/bm25s/main/README.md
- pg_textsearch, README e licença: https://raw.githubusercontent.com/timescale/pg_textsearch/main/README.md
- VectorChord-bm25, README e licença: https://raw.githubusercontent.com/tensorchord/VectorChord-bm25/main/README.md
- ParadeDB, licença: https://raw.githubusercontent.com/paradedb/paradedb/main/LICENSE
- Chroma chunking_evaluation, README: https://raw.githubusercontent.com/brandonstarxel/chunking_evaluation/main/README.md
- No repositório: `app/index/*`, `app/retrieve/*`, `app/config.py`, `bench/results/m10a/{changes,pool_A,query_A,expand_A,code_A,adaptive_A}.md`, DECISIONS (entradas de 2026-10-14 e 2026-10-16) e `bench/tasks/external.yaml` (comprimento das perguntas medido).

## 5. Fontes que não consegui acessar e que valem a leitura completa

1. **FreshStack: Building Realistic Benchmarks for Evaluating Retrieval on Technical Documents** (NeurIPS 2025): https://arxiv.org/abs/2504.13128. É o benchmark mais próximo do M10. O artigo tem as ablações de comprimento da pergunta e de fusão que o leaderboard não mostra.
2. **Qwen3 Embedding: Advancing Text Embedding and Reranking Through Foundation Models**: https://arxiv.org/abs/2506.05176. Traz o método e o efeito das instruções, e as tabelas de código por subtarefa.
3. **jina-reranker-v3: Last but Not Late Interaction for Listwise Document Reranking**: https://arxiv.org/abs/2509.25085. A tabela completa compara v2, v3, Qwen3, mxbai e bge em BEIR e CoIR. Decide se a troca mais barata (mesma API, outro `reranker_model`) basta.
4. **Drowning in Documents: Consequences of Scaling Reranker Inference**: https://arxiv.org/abs/2411.11767. Mostra em que profundidade o rerank começa a piorar; é base para L3.
5. **An Analysis of Fusion Functions for Hybrid Retrieval** (Bruch et al., TOIS): https://arxiv.org/abs/2210.11934. Convexa contra RRF, com protocolo de ajuste de poucas amostras; base para L7.
6. **Still Fresh? Evaluating Temporal Drift in Retrieval Benchmarks** (Waterloo, 2026): https://arxiv.org/abs/2603.04532. Mostra como a documentação técnica migra entre versões e repositórios; útil para o `mixed_versions` e para recongelar o corpus.
7. **A Systematic Investigation of Document Chunking Strategies and Embedding Sensitivity** (Canberra, 2026, 36 métodos): https://arxiv.org/abs/2603.06976. Confirma ou refuta que o chunking estrutural basta.
8. **Rethinking Chunk Size for Long-Document Retrieval: A Multi-Dataset Analysis** (Fraunhofer, 2025): https://arxiv.org/abs/2505.21700. Mostra o tamanho ideal do pedaço por tipo de pergunta e por embedder.
9. **Improving BM25 code retrieval under fixed generic tokenization (q-log IDF)** (2026): https://arxiv.org/abs/2605.18561. Traz a ablação "identificador inteiro contra subtokens contra os dois"; base para L6.
10. **Model card do jina-reranker-v2-base-multilingual**: https://huggingface.co/jinaai/jina-reranker-v2-base-multilingual. Confirma os limites de 512 e 1024 tokens e como os scores das janelas são combinados (max ou média). É decisivo para o diagnóstico de L1.
11. **NVIDIA, Finding the Best Chunking Strategy for Accurate AI Responses**: https://developer.nvidia.com/blog/finding-the-best-chunking-strategy-for-accurate-ai-responses/. Os números por conjunto (página, seção, tokens) que vi só por resumo.
12. **Voyage, rerank-2.5 e voyage-4** (blogs): https://blog.voyageai.com/2025/08/11/rerank-2-5/. Candidato por API para L1 e L2; conferir os números e a metodologia.
