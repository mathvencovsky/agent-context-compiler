# Frente I: modelos de recuperação rodando localmente, de graça (rodada 4, 2026-10-29)

Escopo: trocar os dois estágios mais fracos do NINA (reranker Jina v2 e embedding OpenAI `text-embedding-3-small`) por modelos de pesos abertos com licença permissiva, rodando na máquina do humano (Windows, CPU, talvez uma GPU de consumo), sem API paga. Inclui um plano de implementação concreto contra o código atual e um protocolo de medição de custo zero no dev.

Código do NINA lido para esta frente:
- `app/providers/embeddings/{base,fake,openai}.py`: o `EmbeddingProvider` tem só `model`, `dim` e `embed(texts)`. **Não há distinção entre consulta e documento.**
- `app/providers/rerank/{base,fake,jina}.py`: o `RerankProvider.rerank(query, documents)` devolve scores absolutos em [0, 1], na ordem de entrada.
- `app/retrieve/rerank.py`: top-30 (`_DEFAULT_TOP_N`), normalização min-max para o `value`, score absoluto para `_coverage`.
- `app/retrieve/vector.py`: cosseno restrito a `version_ids`, desempate por `(version_id, ord)`.
- `app/index/indexer.py`: reembute quando `embedding_model` muda e reaproveita vetores por texto.
- `app/store/models.py` e `migrations/versions/0003_chunks.py`: `vector(1536)` fixo, índice HNSW com `vector_cosine_ops`.
- `app/config.py`: `embedding_dim=1536`, `reranker_model`, `coverage_low/high` calibrados na distribuição do Jina v2. `app/api/assemble.py` escolhe o provedor real ou o fake.
- `bench/provider_cache.py`: cache endereçado por conteúdo, com chave `sha256(model + entradas)`. `bench/m10a_pool.py` mede o pool (só ranking). `bench/runners/ours.py` aceita `embeddings=` e `reranker=` injetados. `bench/m10_agent.py` confere `FROZEN_TREES` de `app/` e `migrations/` (Emenda 15).
- Rodada 3, `docs/pesquisa/rodada3/B_recuperacao.md` (L1 a L7). Aqui não repito as tabelas de lá; uso as mesmas fontes só onde elas decidem o que roda localmente.

Restrição de acesso: huggingface.co, arxiv.org e os blogs dos fornecedores estão bloqueados nesta sessão (o `getaddrinfo` falha). Li na íntegra o que está no GitHub (READMEs, código, listas de modelos com metadados de licença). Licenças que só confirmei por resumo de busca estão marcadas com "(resumo)".

---

## 0. Resposta curta

1. **Reranker:** o melhor candidato para a máquina do humano é o **Qwen3-Reranker-0.6B (Apache 2.0)**. Em código, a tabela do próprio Qwen dá 73,42 contra 58,98 do Jina v2. Ele não trunca a pergunta (janela de 32K). Em CPU é lento: na minha estimativa por FLOPs, ~10 a 100 s por pedido de 30 pares, contra ~1 a 3 s numa GPU de consumo. Para o benchmark offline isso serve; para a API interativa em CPU, não.
   - Alternativa barata: **gte-reranker-modernbert-base** (149M, Apache 2.0, 8K de contexto), uns 4 vezes mais barato. Os números de código dele são do fornecedor, com agregação ambígua.
   - **Não usar o bge-reranker-v2-m3** em código (41,38 e 36,28 em duas tabelas independentes), mesmo sendo o único reranker "forte" que o fastembed traz pronto.
2. **Embedding:** o CoIR e o MTEB-Code **não predizem** o FreshStack. O gte-modernbert-base tem CoIR 71,5 e FreshStack 0,330, igual ao 3-small. Escolher pelo FreshStack e depois pelo pool do dev do NINA. Os candidatos locais com licença permissiva e ≤ 0,6B, ordenados pelo FreshStack Recall@50:

   | Modelo | FreshStack Recall@50 | Licença |
   |---|---|---|
   | Octen-Embedding-0.6B | 0,475 | Apache 2.0, resumo |
   | voyage-4-nano | 0,438 | Apache 2.0, resumo |
   | Stella-400M v5 | 0,422 | MIT |
   | harrier-oss-v1-0.6b | 0,411 | MIT, resumo |
   | pplx-embed-v1-0.6b | 0,397 | MIT, resumo |
   | Qwen3-Embedding-0.6B | 0,394 | Apache 2.0 |
   | Arctic-Embed-M v2.0 | 0,385 | Apache 2.0 |
   | *(referência: OpenAI 3-small, o atual)* | 0,330 | API |

3. **Caminho de menor esforço:** os servidores locais **llama.cpp** (MIT) e **TEI** (Apache 2.0) expõem `/v1/rerank` no formato da Jina e `/v1/embeddings` no formato da OpenAI. Os adaptadores atuais (`jina.py` e `openai.py`) servem quase sem mudança: basta uma `base_url` configurável. Os modelos Qwen3 e similares precisam, porém, de **instrução só na consulta**, e a interface `EmbeddingProvider` não tem como expressar isso. Essa é a única mudança de contrato inevitável.
4. **Medição sem custo:** o `app/` está congelado, mas o `bench/` não. O provedor local nasce em `bench/` e é injetado no `OursRunner`. O pool e o pipeline completo (acerto de seção por orçamento, sem LLM) rodam nas metades A e B a custo zero, porque o Jina e a OpenAI já estão no cache e os modelos locais rodam na máquina. Só o vencedor sobe para `app/providers/`, com emenda.

---

## 1. Lições acionáveis para o NINA (ordenadas por impacto esperado)

### I1. Trocar o reranker por um local: Qwen3-Reranker-0.6B no benchmark e um cross-encoder de 149M como opção de CPU

**O que as fontes mostram**

Qualidade em código, só números de fornecedor:

| Reranker | Parâmetros | Licença | Contexto | MTEB-Code (tabela Qwen, top-100) | CoIR (tabela Jina v3, resumo) |
|---|---|---|---|---|---|
| jina-reranker-v2-base-multilingual (atual) | 0,3B | **CC-BY-NC-4.0** | 1024 (consulta ≤ 512) | 58,98 | 56,14 |
| bge-reranker-v2-m3 | 0,6B | Apache 2.0 | 8K | 41,38 | 36,28 |
| gte-multilingual-reranker-base | 0,3B | Apache 2.0 | 8K | 54,18 | — |
| **Qwen3-Reranker-0.6B** | 0,6B | Apache 2.0 | 32K | **73,42** | 65,18 |
| mxbai-rerank-base-v2 | 0,5B | Apache 2.0 | 8K (32K) | — | 65,71 |
| mxbai-rerank-large-v2 | 1,5B | Apache 2.0 | 8K (32K) | — | 70,87 |
| Qwen3-Reranker-4B | 4B | Apache 2.0 | 32K | 81,20 | 73,91 |
| gte-reranker-modernbert-base | 149M | Apache 2.0 (resumo) | 8K | — | 79,99 no cabeçalho do card; média por tarefa 56,73 (resumo, agregação ambígua) |
| granite-embedding-reranker-english-r2 | 149M | Apache 2.0 (resumo) | 8K | — | sem número de código publicado |

- Duas tabelas de fornecedores diferentes (Qwen e Jina) concordam na ordem: Qwen3-0.6B ≈ mxbai-base-v2 > Jina v2 > bge-v2-m3. Em código, o bge-v2-m3 é o pior dos dois quadros.
- A licença do Jina v2 como CC-BY-NC-4.0 está confirmada no código do fastembed (`onnx_text_cross_encoder.py`: `license="cc-by-nc-4.0"`). **Rodá-lo localmente é proibido** pela regra de licenças; só o uso pela API era aceitável.

Custo em CPU e GPU. Nenhuma fonte publica latência de CPU para "30 a 50 passagens de ~400 tokens":
- **Benchmark oficial do sentence-transformers** (lido na íntegra, i7-13700K, rerankers de 22M a 568M, mediana sobre modelos e conjuntos):
  - em CPU, ONNX fp32 sai 0,99× e ONNX O3 1,17×;
  - OpenVINO fp32 sai 1,38×, e **OpenVINO int8 3,09×, com 100,58% da qualidade NanoBEIR**;
  - ONNX int8 sai 1,36×, com 98,77% da qualidade;
  - **fp16/bf16 em CPU roda a 0,17×: nunca usar meia precisão em CPU**;
  - em GPU, fp16 vale de 2 a 3 vezes nos rerankers grandes, e ONNX empata com PyTorch.
- Numa RTX 4090, o Qwen3-Reranker-0.6B levou ~1,08 s para 50 documentos (blog chinês, só resumo, não verificado). No SkillRet, foram 831 ms para o top-20 com hardware não informado (resumo).
- **Minha estimativa por FLOPs** (2 × parâmetros sem embedding × tokens; não é medição):
  - cada par (instrução + pergunta + pedaço) tem uns 1.100 tokens: a pergunta mediana tem 275, os pedaços chegam a 800 e a instrução tem ~40;
  - 30 pares ≈ 33 mil tokens;
  - Qwen3-Reranker-0.6B (~0,44B sem embedding): ≈ 3×10¹³ FLOPs, o que dá **~30 a 100 s em CPU fp32** de notebook ou desktop (0,3 a 1 TFLOPS efetivos), ~10 a 40 s em int8 e **~1,5 a 3 s numa RTX 3060 ou 4060 em fp16**;
  - gte-reranker-modernbert-base ou granite-reranker-r2 (~0,1B sem embedding): ~4 a 5 vezes menos, ou seja, ~6 a 25 s em CPU fp32 e ~2 a 8 s com OpenVINO int8.
- Memória:
  - o ONNX int8 do Qwen3 0.6B ocupa ~0,6 GB (pacote qwen3-embed: 573 MB);
  - o fp32 ocupa ~2,4 GB (Qdrant: 2,38 GB para o embedding, de tamanho igual);
  - o bge-reranker-v2-m3 tem 2,27 GB em fp32 e 0,57 GB em int8 (metadados do fastembed).

**Por que importa para o NINA**
- O rerank decide o `value` da seleção e o `_coverage`. No M8, depois da correção, ele ajudou só +0,049 no test e +0,062 no dev (DECISIONS, Etapa 2).
- O Jina v2 trunca a consulta em 512 tokens: 18,5% das perguntas do M10 passam disso (L1 da rodada 3).
- O Qwen3-Reranker e os cross-encoders ModernBERT aceitam 8K a 32K: a pergunta inteira e o pedaço inteiro entram, sem janelas.
- O Qwen3-Reranker devolve P("yes") ∈ [0, 1], o que já é o contrato do `RerankProvider` e dispensa sigmoid. Os cross-encoders devolvem logits e pedem sigmoid, como já diz o README do FlagEmbedding.

**Mudança proposta**
1. No `bench/` (sem tocar o `app/` congelado), criar um `LocalRerankProvider` com duas implementações:
   - **Qwen3** no formato oficial do README do Qwen (`<Instruct>: … <Query>: … <Document>: …`, logit de "yes" contra "no"), com uma instrução de desenvolvedor como "Given a developer's question, retrieve documentation passages that answer it";
   - **cross-encoder genérico** (gte-reranker-modernbert, granite-reranker-r2, mxbai-base-v2) via sentence-transformers `CrossEncoder`, com sigmoid.
2. Medir no dev, com o protocolo de I5: Jina v2 (do cache, custo zero), sem rerank, Qwen3-0.6B, gte-modernbert e mxbai-base-v2.
3. Se o vencedor for o Qwen3-0.6B e a API precisar rodar em CPU, decidir com dados entre (a) exigir GPU no deploy local e (b) usar o cross-encoder de 149M no produto e o Qwen3 só no benchmark. A opção (b) mede um sistema diferente do produto e pede emenda clara.
4. Toda troca de reranker **recalibra `coverage_low` e `coverage_high`** no dev, porque são quantis do Jina v2 (L1 e L9 da rodada 3).

**Custo:** zero em dinheiro. Exige CPU e horas na máquina do humano, ou minutos com GPU (I5 traz a conta). No produto, é mudança no `app/` com emenda.

**Força da evidência**
- Qualidade: média. São duas tabelas de fornecedores que concordam entre si, mas nenhuma é em documentação do tipo do M10.
- Velocidade relativa entre backends: forte (benchmark oficial, harness público).
- Latência absoluta: fraca. É estimativa minha, e o humano mede em 10 minutos com o script de I5.

---

### I2. Embedding local escolhido pelo FreshStack, não pelo CoIR

**O que as fontes mostram**
- Releitura do `leaderboard_data.json` do FreshStack (59 linhas, lido na íntegra), agora filtrando o que **roda localmente e tem licença compatível**:

  | Modelo | Tamanho | Recall@50 | α-nDCG@10 | Licença dos pesos | Dimensão |
  |---|---|---|---|---|---|
  | Octen-Embedding-0.6B (ajuste fino do Qwen3-0.6B) | 0,6B | **0,475** | 0,325 | Apache 2.0 (resumo; card e conversões GGUF e ONNX) | 1024 |
  | voyage-4-nano | 340M | 0,438 | 0,280 | Apache 2.0 (resumo; blog da Voyage: "first open-weight model") | 2048 (MRL) |
  | Stella-400M v5 | 400M | 0,422 | 0,276 | MIT (metadados do fastembed) | 1024 |
  | harrier-oss-v1-0.6b (Microsoft) | 0,6B | 0,411 | 0,284 | MIT (resumo, sem o card oficial) | ? |
  | pplx-embed-v1-0.6b | 0,6B | 0,397 | 0,290 | MIT (resumo; anúncio da Perplexity) | 1024 |
  | Qwen3-Embedding-0.6B | 0,6B | 0,394 | 0,262 | Apache 2.0 | 1024 (MRL 32 a 1024) |
  | Arctic-Embed-M v2.0 | 305M | 0,385 | 0,274 | Apache 2.0 (fastembed) | 768 |
  | Granite-Embedding-English-R2 Small | 47M | 0,352 | 0,221 | Apache 2.0 (README IBM) | 384 |
  | Granite-Embedding-English-R2 | 149M | 0,342 | 0,225 | Apache 2.0 | 768 |
  | **OpenAI text-embedding-3-small (atual)** | — | **0,330** | 0,208 | API | 1536 |
  | GTE ModernBERT-base | 149M | 0,330 | 0,222 | Apache 2.0 | 768 |
  | BM25 | — | 0,316 | 0,218 | — | — |
  | Nomic Embed Code | 7B | 0,348 | 0,218 | — | — |
  | CodeRankEmbed | 137M | 0,162 | 0,104 | — | — |

  **Fora pela licença:**
  - Nemotron-3-Embed-1B (0,468): a NVIDIA cita licenças conflitantes, NVIDIA Open Model License ou OpenMDW-1.1 (resumo);
  - Jina v3, v4 e v5 e jina-code-embeddings: CC-BY-NC, confirmado no catálogo do Infinity e no fastembed para o v3;
  - EmbeddingGemma: licença Gemma (o fastembed marca `license="gemma"`).
- **O CoIR não transfere.** Na tabela da IBM (README do granite), o gte-modernbert-base tem CoIR 71,5 e o granite-r2, 54,8. No FreshStack ficam com 0,330 e 0,342. Os modelos "de código" (CodeRankEmbed, Nomic Embed Code) vão mal no FreshStack: perguntas do Stack Overflow contra documentação não são busca de função por docstring.
- O MRL do Qwen3 vai de 32 a 1024 (README oficial). O granite-311m-multilingual-r2 perde só 0,4 pt de 768 para 256 dimensões (README IBM).

**Por que importa para o NINA:** no `pool_A.md`, o vetor sozinho põe o pedaço da resposta no top-30 em 81 de 139 tarefas, o BM25 em 87 e o híbrido em 87. O lado denso quase não soma, e o FreshStack mostra o mesmo para o 3-small. Um denso melhor é o que pode subir o teto do pool.

**Mudança proposta**
1. Medir o pool no dev (I5, experimento E2) com 3 ou 4 candidatos: Octen-0.6B, Qwen3-0.6B, Stella-400M e voyage-4-nano.
   - O Octen-0.6B lidera, mas é de uma organização pequena e o FreshStack é um conjunto público. Vale conferir contra o Qwen3-0.6B base: se o Octen ganhar no FreshStack e não no NINA, é sinal de sobreajuste.
   - Todos com a instrução de consulta do FreshStack: `Instruct: Given a technical question, retrieve relevant code snippets or technical documentation that best answer the question\nQuery:`.
2. O vencedor só sobe para o `app/` se ganhar nas duas metades.

**Custo**
- Zero em dinheiro.
- Reembutir o corpus do dev em CPU com um modelo de 0,6B leva horas (conta em I5); com GPU, minutos.
- No produto: migração do pgvector (I4) e reindexação. O indexador já reembute quando o `embedding_model` muda.

**Força da evidência:** forte para a ordem relativa no FreshStack (leaderboard público, harness aberto). As licenças marcadas "resumo" pedem que o humano confira no card do HF antes de adotar.

---

### I3. Mudança de contrato inevitável: consulta e documento são embutidos de formas diferentes

**O que as fontes mostram**
- Qwen3-Embedding (README oficial): "Each query must come with a one-sentence instruction… No need to add instruction for retrieval documents". Sem a instrução, perde de 1% a 5% (README e card do Qdrant).
- O mesmo vale para outros modelos:
  - Nomic: `search_query: ` e `search_document: `, conforme o catálogo do Infinity;
  - Arctic e E5: prefixo de consulta;
  - Octen e pplx: herdam o formato do Qwen3.

**Por que importa para o NINA**
- O `EmbeddingProvider.embed(texts)` serve aos dois usos:
  - a consulta, em `assemble.py:387`: `embeddings.embed([req.query])[0]`;
  - os pedaços, em `indexer.py:93`.
- Os runners do benchmark repetem o padrão (`ours.py:275`, `strong_rag.py:187`).
- Com modelos assimétricos, ou a consulta sai sem instrução (perde qualidade), ou os documentos saem com ela (errado).

**Mudança proposta (contrato novo, então precisa de SPEC e DECISIONS)**
- Acrescentar ao protocolo `embed_query(text: str) -> list[float]`.
- Default dos provedores simétricos: `embed([text])[0]`. Assim o fake e o OpenAI não mudam de comportamento, e os snapshots continuam iguais.
- No `CachingEmbeddingProvider`, usar a chave `_digest("embed_query", model, instrução, text)`, separada da chave dos documentos.
- Trocar as três chamadas de consulta (app e runners).
- Alternativa sem mudar o protocolo: um wrapper que prefixa a instrução só na consulta. Fica frágil, porque cada chamador precisaria lembrar dele.

**Custo:** pequena mudança no `app/` e nos testes do fake. O fake mantém a determinação por sha256.

**Força da evidência:** forte (documentação oficial dos modelos).

---

### I4. Trocar o embedding muda a dimensão: migração do pgvector sem dor

**O que as fontes mostram:** o README do pgvector (lido) responde à pergunta "Can I store vectors with different dimensions in the same column?":
- pode-se usar `vector` sem dimensão;
- o índice só funciona por expressão e parcial: `CREATE INDEX … USING hnsw ((embedding::vector(1024)) vector_cosine_ops) WHERE (model_id = …)`;
- a consulta faz `ORDER BY embedding::vector(1024) <=> …`.

**Por que importa para o NINA**
- Hoje há `vector(1536)` fixo em três lugares: `models.py:_EMBEDDING_DIM`, `0003_chunks.py` e `config.embedding_dim`.
- A busca é sempre restrita a `version_ids` de 1 a 20 páginas, ou seja, centenas a poucos milhares de pedaços. Uma varredura exata é barata, e o HNSW mal pesa.
- No `bench/`, a dimensão não importa: o runner calcula o cosseno em memória.

**Mudança proposta (só quando um embedding local vencer)**
- Migração `0006`:
  - apagar o índice HNSW;
  - `ALTER COLUMN embedding TYPE vector` (sem dimensão);
  - criar um índice parcial por expressão para o modelo ativo (ou nenhum índice, depois de medir).
- No ORM: `Vector()` sem dimensão. O `vector_search` faz cast para a dimensão do provedor.
- O indexador já reembute por `embedding_model`. Com isso, alternar entre OpenAI 1536 e local 1024 vira configuração, sem migração destrutiva.
- Alternativa mínima: migração destrutiva `vector(1536)` → `vector(1024)` com `DELETE` dos chunks (o cache de resolve no S3 continua válido e a reindexação é sob demanda).

**Custo:** mudança no `app/` e em `migrations/` (ambos congelados pela Emenda 15).

**Força da evidência:** forte (documentação do pgvector).

---

### I5. Medir tudo no dev a custo zero, antes de tocar no `app/`

**Por que é possível agora**
- `OursRunner(embeddings=…, reranker=…)` aceita provedores injetados.
- O `CachingEmbeddingProvider` e o `CachingRerankProvider` funcionam com qualquer delegado (a chave inclui `model`), e o `bench/` não está em `FROZEN_TREES`.
- O baseline Jina e OpenAI já está no cache: rodá-lo de novo dá 0 chamadas pagas, e os scripts imprimem o contador.
- A métrica de acerto de seção (`bench/section_recall.py`, `m10a_baseline`) é determinística e não usa LLM.

**Protocolo (com a regra das metades)**
- **E0, latência e sanidade** (10 min): rodar `bench/local_smoke.py`, um script e não um teste, com 20 pares reais do dev. Ele mede:
  - s/pedido para 30 e para 50 pares em CPU fp32, OpenVINO int8 e, se houver, GPU fp16;
  - a correlação de Spearman entre backends do mesmo modelo, que deve ser ≥ 0,99; se não for, o backend está quebrado.
  - Para o llama.cpp com GGUF, comparar com a implementação de referência em transformers: houve conversões do Qwen3-Reranker com scores quase zero e ranking errado, que o card de uma conversão relata (resumo).
- **E1, só o rerank, pool fixo** (metade A): o pool é o top-30 fundido atual, com embeddings OpenAI do cache e o léxico atual. Variantes: Jina v2 (cache), sem rerank, Qwen3-R-0.6B, gte-reranker-modernbert, mxbai-base-v2 e granite-reranker-r2. Métricas:
  - acerto de seção em 1k, 2k, 4k e 8k (pipeline completo, sem LLM);
  - posição do pedaço da resposta depois do rerank.
  - Confirmar o melhor na metade B.
- **E2, só o embedding** (metade A): a tabela do `pool_A.md` (pedaço no top-30 fundido) para Octen-0.6B, Qwen3-0.6B, Stella-400M e voyage-4-nano, todos com instrução de consulta. Confirmar na B.
- **E3, combinado:** o melhor embedding + o melhor reranker, com profundidade de rerank 30 contra 50 (L3 da rodada 3), e recalibração de `coverage_*` e `lexical_weight`.

**Conta de tempo (estimativa por FLOPs, não medição)**
- E1, metade A: 139 tarefas × 30 pares × ~1,1 mil tokens ≈ 4,6 milhões de tokens por reranker.
  - Qwen3-R-0.6B: ~2 h em CPU (0,5 TFLOPS) ou ~10 min em GPU.
  - Cross-encoder de 149M: ~30 min em CPU.
- E2, metade A: ~700 páginas, com mediana de 3,5 mil tokens por página (DECISIONS) e muita repetição entre tarefas, dão ~2 a 5 milhões de tokens.
  - Modelo de 0,6B: ~1 a 4 h em CPU, minutos em GPU.
  - Granite-small (47M): minutos.
- Antes de começar, medir o número exato com `count_tokens` sobre o `CorpusStore`.

**Detalhes que evitam medir errado**
- A string `model` do provedor local deve carregar `nome@revisão-HF/backend/quantização`, por exemplo `Qwen3-Reranker-0.6B@<sha>/onnx-int8`. Assim o cache nunca mistura variantes.
- Caches dos modelos locais em `SqliteStore` separado (`bench/.cache/local/`).
- Mesmo pool para todas as variantes de E1, e só a metade A até decidir.

**Custo:** zero em dinheiro, e não toca o `app/`.

**Força da evidência:** é engenharia sobre código lido. O risco é o tempo de CPU.

---

### I6. Escolha de runtime e dependências: ONNX Runtime + tokenizers no produto, sentence-transformers no benchmark

**Bibliotecas e licenças** (licença lida no arquivo LICENSE do repositório, salvo indicação):

| Runtime | Licença | Dependência pesada | Qwen3-Reranker | Cross-encoders ModernBERT | Qwen3-Embedding | Windows |
|---|---|---|---|---|---|---|
| **ONNX Runtime** | MIT | não (~20 MB) | via ONNX da comunidade (onnx-community, qwen3-embed) | sim (exportar via optimum) | sim | sim; `onnxruntime-directml` para GPU AMD, Intel ou NVIDIA; CUDA EP pede as DLLs do CUDA 12 e do cuDNN |
| **fastembed** 0.9.1 (Qdrant) | Apache 2.0 | onnxruntime, tokenizers, huggingface-hub, loguru, mmh3, pillow, py-rust-stemmers | **não** (só cross-encoders) | por `add_custom_model` | **sim** (`Qwen/Qwen3-Embedding-0.6B` fp32 2,38 GB e `-Q` int8 1,12 GB, exige onnxruntime ≥ 1.23); também granite-small-r2 | sim (CPU) |
| **sentence-transformers** | Apache 2.0 | torch (BSD) + transformers (Apache) | sim, via conversão seq-cls (`tomaarsen/Qwen3-Reranker-0.6B-seq-cls`, resumo) | sim | sim | sim (torch CPU ~200 MB; CUDA ~2,5 GB) |
| FlagEmbedding | MIT | torch | — | bge | bge | sim |
| **llama.cpp** (servidor) | MIT | binário C++ | **sim**: o conversor oficial detecta "qwen3-reranker" e embute o template `yes`/`no` (`conversion/qwen.py`) | encoders | sim (GGUF) | **sim** (binários e winget, CUDA e Vulkan) |
| **TEI** (HF) | Apache 2.0 | Docker/Rust | não listado | **sim** (gte-reranker-modernbert, bge, gte-multilingual) | sim (Qwen3 0.6B a 8B, voyage-4-nano) | só Docker/WSL2; CPU com o backend ORT; GPU CUDA ≥ 7.5 |
| Infinity v3 | MIT | Rust + LibTorch | não listado | sim (gte-modernbert, bge-v2-m3, mxbai v1, nemotron) | sim | Docker NVIDIA SM80+ (Ampere ou mais nova); o v2 em Python roda em CPU |
| optimum / OpenVINO | Apache 2.0 / Apache 2.0 | — | — | int8 estático (3,09× em CPU) | — | sim |

**Consequências para o NINA**
- **No produto**, um `LocalRerankProvider`/`LocalEmbeddingProvider` em `onnxruntime` + `tokenizers` (os dois já leves) evita torch.
  - Deixar como **grupo opcional** `local` no `pyproject.toml`, fora do default. Assim `make check` e CI não baixam nada.
  - Import preguiçoso, como em `openai.py`.
  - No mypy: `ignore_missing_imports` só para `onnxruntime` e `tokenizers`.
- **No benchmark**, o sentence-transformers é mais simples e tem o caminho mais confiável para Qwen3 seq-cls, mxbai v2 e gte. O custo de puxar torch fica só no grupo `bench-local`.
- **Caminho de quase zero código:** subir o llama.cpp server (Qwen3-Reranker GGUF Q8_0 com `--reranking`; Qwen3-Embedding GGUF com `--embedding`) ou o TEI (CPU, `docker compose`, coerente com a stack) e apontar os adaptadores atuais para `localhost`.
  - O `/v1/rerank` do llama.cpp aceita o corpo da Jina (`query`, `documents`, `top_n`) e devolve `results[{index, relevance_score}]` em [0, 1], exatamente o que `jina.py` lê.
  - O `/v1/embeddings` é OpenAI-compatível, e o SDK da OpenAI aceita `base_url`.
  - Precisa de `reranker_base_url` e `embeddings_base_url` em `Settings`.
  - **Atenção às regras:** "httpx só em `app/fetch/`" já tem a exceção registrada para o Jina, e o mesmo raciocínio cobre `localhost`. Mas o guard de SSRF não pode ser usado aqui, porque bloqueia loopback. Registrar no DECISIONS.
- **Pacotes de um mantenedor só**, como o `qwen3-embed` (fork do fastembed, Apache, ONNX INT8/Q4F16 e GGUF): úteis como referência de exportação, mas arriscados como dependência.

**Custo:** grupo opcional novo no `pyproject.toml`; servidor local fora do Python, se for esse o caminho.

**Força da evidência:** forte (licenças e código lidos).

---

### I7. Determinismo com modelos locais: fixar o que dá e arredondar o resto

**O que as fontes mostram**
- O fastembed ordena os lotes por comprimento (`iter_length_sorted_batches`). A composição do lote, e portanto o padding, muda conforme o conjunto de documentos.
- O ONNX Runtime divide as matmuls por threads (`intra_op_num_threads` = núcleos físicos por padrão). A ordem de redução pode mudar com o número de threads. A documentação não garante saída idêntica entre contagens de threads (resumo).
- GPU contra CPU e versões diferentes de kernel dão diferenças na última casa (benchmark do sentence-transformers: de 99,4% a 100,6% da qualidade entre backends).

**Por que importa para o NINA:**
- A regra é "extração determinística" e o cache do benchmark assume score determinístico por `(model, query, docs)`.
- O `vector_search` já desempata por `(version_id, ord)`.
- Já o `rerank_candidates` faz min-max sobre os scores e o `select.py` ordena por valor. Um empate numérico instável pode trocar a ordem.

**Mudança proposta**
- Fixar:
  - a revisão do HF (sha) e a versão do `onnxruntime` e do `tokenizers` no `uv.lock`;
  - `intra_op_num_threads` (por exemplo 4) e `inter_op_num_threads=1`;
  - o backend e a quantização dentro do `model` (assim a chave de cache muda junto).
- **Rerank com lote fixo, em ordem canônica**: ordenar os pedaços por `(document_id, ord)` antes de montar os lotes e reordenar na saída; ou lote de tamanho 1 em CPU, onde o padding só desperdiça.
- Arredondar os scores para 6 casas antes da normalização e desempatar por chave estável no seletor. É uma linha em `rerank.py`, se ainda não houver.
- Rodar o benchmark sempre no mesmo dispositivo. Registrar no manifesto do run: CPU, GPU, backend e threads.
- Baixar os modelos num passo explícito (`make models`, com `huggingface_hub.snapshot_download(revision=sha)`) e rodar com `HF_HUB_OFFLINE=1`. O download é rede de saída para fornecedor confiável, como a Jina hoje, e não passa pelo guard de páginas. No Docker de produção, o modelo vai embutido na imagem.

**Testes offline**
- O provedor local recebe por injeção uma "sessão" (`Callable[[list[tuple[str, str]]], list[float]]`).
- Os testes unitários usam um stub e verificam:
  - o formato do prompt do Qwen3;
  - o mapeamento logit → [0, 1];
  - a ordem de entrada preservada;
  - o desempate, o lote canônico e o comportamento com `documents=[]`.
- Um teste confere que importar `app` não importa `onnxruntime`.
- Nenhum teste baixa modelo, e o fake continua sendo o provedor de todos os testes.
- O teste com modelo real fica fora do pytest (o `local_smoke` de I5), para não haver teste pulado condicionalmente.

**Custo:** pequeno, junto com I1 e I2.

**Força da evidência:** média (prática conhecida; a ausência de garantia do ORT vem de resumo).

---

### I8. Busca léxica local: BM25 em Python por pedido basta; esparso aprendido não compensa

**O que as fontes mostram**
- **bm25s** (MIT; só numpy e scipy, numba opcional): README lido. Variantes Lucene, BM25+ e BM25L, stopwords e stemmer (PyStemmer). Bem mais rápido que o `rank-bm25` que o NINA já tem no grupo `bench`.
- **tantivy-py** (MIT, Rust): índice com BM25 e consultas de frase. Útil para frases de erro copiadas, mas exige índice por pedido.
- **Pyserini** (Apache 2.0, segundo o repositório; o LICENSE não abriu): precisa de JDK 21 e é pesado no Windows. Não se justifica para centenas de pedaços.
- **SPLADE da Naver**: CC-BY-NC-SA-4.0 (LICENSE do repositório). **Proibido.**
- **Esparsos da OpenSearch** (`opensearch-neural-sparse-encoding-*`): Apache 2.0 (resumo). No FreshStack, o único esparso aprendido (SlideOn, 0,284) fica abaixo do BM25 (0,316).
- **Postgres tsvector:** o `ts_rank` não é BM25 (sem IDF por coleção). O `pg_textsearch` exige PG17, e ParadeDB e VectorChord são AGPL (rodada 3).

**Por que importa para o NINA**
- O `app/retrieve/lexical.py` é contagem de sobreposição em Python, não tsvector.
- O `bench/m10a_pool.py` já tem um `bm25_ranks` próprio, que deu 87/109 contra 87/105 do atual.
- Rodando localmente, a escolha não muda o custo, porque o léxico já é local.

**Mudança proposta:** quando o `app/` for descongelado, BM25 por pedido (implementação própria de ~40 linhas, como em `m10a_pool`, ou `bm25s`) com tokenização ciente de código (L6 da rodada 3), recalibrado **depois** do novo denso (L7). Não adotar esparso aprendido nem Pyserini.

**Custo:** zero de dependência na implementação própria; `bm25s` é MIT, se preferirem.

**Força da evidência:** média.

---

### I9. Perguntas longas: só modelos de janela grande no rerank; no embedding, nenhum risco

- As perguntas do M10 têm mediana de 275 tokens cl100k, p90 de 673, e 18,5% passam de 512 (rodada 3). Somadas à instrução e ao pedaço de até 800 tokens, a entrada de rerank vai até ~1,6k tokens no p90 e ~2,5k no pior caso.
- Janelas:
  - cabem sem truncar: Qwen3-Reranker e Qwen3-Embedding (32K), mxbai v2 (8K/32K), gte-reranker-modernbert e granite-r2 (8K);
  - **truncam:** Jina v2 (1024) e MiniLM (512).
- **Armadilha:** o `compute_score` do FlagEmbedding e vários exemplos usam `max_length=512` por padrão. Num cross-encoder, sempre definir `max_length` ≥ 2048 explicitamente.
- Para o bge-reranker-v2-m3 no llama.cpp, a entrada inteira precisa caber no `--ubatch-size` (README do servidor). Os rerankers decoder (Qwen3) não têm essa limitação.
- Custo: o tempo cresce de forma linear a quadrática com o comprimento. A pergunta inteira em cada um dos 30 pares é o principal custo de CPU.
  - Mitigação sem LLM e determinística, a medir: pôr no rerank só o título + os primeiros N tokens do corpo da pergunta, mais os blocos de código e de erro.
  - **Mas** a rodada 3 rejeitou a "limpeza da pergunta" na metade B. Testar apenas como otimização de latência, aceitando no máximo 1 pt de perda.

---

## 2. Onde o NINA já está igual ou melhor

1. **A arquitetura de provedores está pronta para trocar de modelo.** Interfaces `Protocol`, fakes determinísticos, import preguiçoso e score absoluto separado do valor normalizado são o que um provedor local precisa. O cache endereçado por conteúdo (`provider_cache.py`) faz o baseline pago ser reutilizável de graça. Poucos projetos abertos (dsRAG, FreshStack) têm isso.
2. **O indexador já reembute por `embedding_model`** e reaproveita vetores por texto. A troca de embedding é uma operação suportada; só falta a dimensão flexível (I4).
3. **O desempate determinístico já existe no vetor** (`version_id, ord`), e a disciplina de metades A/B impede adotar um modelo local que só ganhe por ruído.
4. **O léxico já é local e gratuito.** Os rivais que usam busca gerenciada pagam por isso.

## 3. Onde o NINA está atrás

1. **O reranker atual não pode nem ser rodado localmente** (CC-BY-NC) e é o pior em código nas duas tabelas, depois do bge. Rerankers abertos de 0,15 a 0,6B, Apache 2.0, já o superam por 7 a 15 pontos em código (números de fornecedor).
2. **O embedding atual empata com o BM25 no FreshStack (0,330).** Seis modelos locais e permissivos de ≤ 0,6B ficam entre 0,385 e 0,475.
3. **A interface de embedding não separa consulta e documento** (I3), o que impede usar bem qualquer embedder moderno.
4. **A dimensão 1536 está fixa** no ORM e na migração (I4).
5. **Não há medição de latência nem de memória** de nenhum estágio de modelo; hoje tudo é chamada remota. Com modelos locais, isso vira requisito: a API interativa em CPU só aguenta modelos de ~150M.

## 4. Fontes lidas na íntegra

- fastembed:
  - README: https://raw.githubusercontent.com/qdrant/fastembed/main/README.md
  - LICENSE: https://raw.githubusercontent.com/qdrant/fastembed/main/LICENSE
  - pyproject: https://raw.githubusercontent.com/qdrant/fastembed/main/pyproject.toml
- fastembed, listas de modelos com licença:
  - https://raw.githubusercontent.com/qdrant/fastembed/main/fastembed/rerank/cross_encoder/onnx_text_cross_encoder.py
  - https://raw.githubusercontent.com/qdrant/fastembed/main/fastembed/text/onnx_embedding.py
  - https://raw.githubusercontent.com/qdrant/fastembed/main/fastembed/text/pooled_normalized_embedding.py
  - https://raw.githubusercontent.com/qdrant/fastembed/main/fastembed/text/pooled_embedding.py
  - https://raw.githubusercontent.com/qdrant/fastembed/main/fastembed/text/multitask_embedding.py
  - https://raw.githubusercontent.com/qdrant/fastembed/main/fastembed/text/builtin_sentence_embedding.py
  - https://raw.githubusercontent.com/qdrant/fastembed/main/fastembed/text/last_token_normalized_embedding.py
  - https://raw.githubusercontent.com/qdrant/fastembed/main/fastembed/common/preprocessor_utils.py
- sentence-transformers:
  - benchmark de backends de cross-encoder: https://raw.githubusercontent.com/UKPLab/sentence-transformers/master/docs/cross_encoder/usage/efficiency.rst
  - figura de CPU: https://raw.githubusercontent.com/UKPLab/sentence-transformers/master/docs/img/ce_backends_benchmark_cpu.png
  - LICENSE: https://raw.githubusercontent.com/UKPLab/sentence-transformers/master/LICENSE
- Infinity:
  - catálogo de modelos com notas de licença: https://raw.githubusercontent.com/michaelfeil/infinity/main/libs/infinity-v3/docs/supported-models.md
  - README: https://raw.githubusercontent.com/michaelfeil/infinity/main/README.md
  - LICENSE: https://raw.githubusercontent.com/michaelfeil/infinity/main/LICENSE
- TEI:
  - README: https://raw.githubusercontent.com/huggingface/text-embeddings-inference/main/README.md
  - LICENSE: https://raw.githubusercontent.com/huggingface/text-embeddings-inference/main/LICENSE
- llama.cpp:
  - servidor (`/v1/rerank`): https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md
  - conversor do Qwen3-Reranker: https://raw.githubusercontent.com/ggml-org/llama.cpp/master/conversion/qwen.py
  - LICENSE: https://raw.githubusercontent.com/ggml-org/llama.cpp/master/LICENSE
- Licenças avulsas:
  - ONNX Runtime: https://raw.githubusercontent.com/microsoft/onnxruntime/main/LICENSE
  - Optimum: https://raw.githubusercontent.com/huggingface/optimum/main/LICENSE
- Qwen3-Embedding e Qwen3-Reranker, README com uso, instruções e tabelas: https://raw.githubusercontent.com/QwenLM/Qwen3-Embedding/main/README.md
- mxbai-rerank:
  - README: https://raw.githubusercontent.com/mixedbread-ai/mxbai-rerank/main/README.md
  - LICENSE: https://raw.githubusercontent.com/mixedbread-ai/mxbai-rerank/main/LICENSE
- Granite Embedding R2, README com a tabela CoIR e os dados de MRL: https://raw.githubusercontent.com/ibm-granite/granite-embedding-models/main/README.md
- FlagEmbedding:
  - README: https://raw.githubusercontent.com/FlagOpen/FlagEmbedding/master/README.md
  - reranker: https://raw.githubusercontent.com/FlagOpen/FlagEmbedding/master/research/llm_reranker/README.md
  - BGE-M3: https://raw.githubusercontent.com/FlagOpen/FlagEmbedding/master/research/BGE_M3/README.md
  - LICENSE: https://raw.githubusercontent.com/FlagOpen/FlagEmbedding/master/LICENSE
- Snowflake Arctic Embed, README: https://raw.githubusercontent.com/Snowflake-Labs/arctic-embed/main/README.md
- FreshStack, `leaderboard_data.json` (reanalisado para os modelos locais): https://raw.githubusercontent.com/fresh-stack/fresh-stack.github.io/master/leaderboard_data.json
- pgvector, README (seção de dimensões diferentes): https://raw.githubusercontent.com/pgvector/pgvector/master/README.md
- bm25s:
  - README: https://raw.githubusercontent.com/xhluca/bm25s/main/README.md
  - LICENSE: https://raw.githubusercontent.com/xhluca/bm25s/main/LICENSE
- tantivy-py:
  - README: https://raw.githubusercontent.com/quickwit-oss/tantivy-py/master/README.md
  - LICENSE: https://raw.githubusercontent.com/quickwit-oss/tantivy-py/master/LICENSE
- SPLADE (Naver):
  - README: https://raw.githubusercontent.com/naver/splade/main/README.md
  - LICENSE: https://raw.githubusercontent.com/naver/splade/main/LICENSE
- PyPI `qwen3-embed` 1.8.0 (via WebFetch): https://pypi.org/project/qwen3-embed/1.8.0/
- No repositório:
  - `app/providers/{embeddings,rerank}/*`, `app/config.py`, `app/retrieve/{rerank,vector}.py`, `app/index/indexer.py`, `app/store/models.py`, `migrations/versions/0003_chunks.py`;
  - `bench/provider_cache.py`, `bench/m10a_pool.py`, `bench/runners/ours.py`, `bench/diagnose_rerank.py`, `bench/results/m10a/pool_A.md`;
  - DECISIONS (Emenda 15, Etapa 2 do M8, custos de embedding) e `pyproject.toml`.

Só por resumo de busca:
- licenças do Octen-0.6B, voyage-4-nano, harrier-oss, pplx-embed, Nemotron-3-Embed, gte-reranker-modernbert e granite-reranker-r2;
- tabela CoIR do blog do jina-reranker-v3;
- latências do Qwen3-Reranker (blog chinês, SkillRet, DoPR);
- conversão seq-cls do Qwen3-Reranker;
- falta de garantia de determinismo do ORT;
- licenças dos esparsos da OpenSearch.

## 5. Fontes inacessíveis que valem leitura completa (salvar como PDF)

1. **Model card do Qwen3-Reranker-0.6B**: https://huggingface.co/Qwen/Qwen3-Reranker-0.6B. Confirma a licença, o `max_length` recomendado e o código oficial de score. É a base do provedor local de I1.
2. **Model card do gte-reranker-modernbert-base**: https://huggingface.co/Alibaba-NLP/gte-reranker-modernbert-base. A tabela CoIR por tarefa resolve a ambiguidade 79,99 contra 56,73 e diz se há ONNX pronto. Decide se a opção barata de CPU é boa em código.
3. **Model card do Octen-Embedding-0.6B**: https://huggingface.co/Octen/Octen-Embedding-0.6B. Licença, dados de treino (há FreshStack ou Stack Overflow no treino? risco de contaminação) e formato de instrução. É o melhor embedder pequeno no FreshStack.
4. **Model card do voyage-4-nano**: https://huggingface.co/voyageai/voyage-4-nano. Licença, dimensões MRL (aceita 1536? se aceitar, não haveria migração) e prompts.
5. **Model card do microsoft/harrier-oss-v1-0.6b**: https://huggingface.co/microsoft/harrier-oss-v1-0.6b. Confirma o MIT, que vi só em agregadores.
6. **Model card do perplexity-ai/pplx-embed-v1-0.6b**: https://huggingface.co/perplexity-ai/pplx-embed-v1-0.6b. Confirma o MIT e o formato de consulta.
7. **Granite Embedding R2 Models** (IBM, 2025): https://arxiv.org/abs/2508.21085. Tabela 5 com o reranker de 149M e a velocidade em CPU, se houver.
8. **mxbai-rerank-v2 / ProRank**: https://arxiv.org/abs/2506.03487 e https://www.mixedbread.com/blog/mxbai-rerank-v2. O que é a coluna "Code Search" 31,73 do README e qual é a latência em CPU.
9. **Model card do Qdrant/Qwen3-Embedding-0.6B-onnx**: https://huggingface.co/Qdrant/Qwen3-Embedding-0.6B-onnx. Como foi feito o int8 (MatMulNBits) e se há comparação de qualidade com o fp32.
10. **Documentação de threading do ONNX Runtime**: https://onnxruntime.ai/docs/performance/tune-performance/threading.html. Confirma o que fixar para ter saída reprodutível em CPU.
