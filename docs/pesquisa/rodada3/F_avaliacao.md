# Frente F: como avaliar o NINA de forma crível e chamativa, e o que ainda dá para aprender sem gastar

Pesquisa de 2026-10-28 (rodada 3). Daqui só se alcança o GitHub. arxiv.org,
huggingface.co, parallel.ai, exa.ai, upstash.com, openreview, aclanthology e os
sites de projeto (*.github.io) estão bloqueados, e o WebFetch também falha
neles. Por isso li na íntegra o que os autores publicam no GitHub (READMEs,
dados, código de avaliação e baselines). Artigos e posts de blog entraram só
por resumos de busca, e cada item diz qual foi o caso.

Arquivos do NINA conferidos para tornar as propostas concretas:
`bench/PREREG.md` (§1 a §12 e Emendas 1 a 16), `bench/m10_analysis.py`,
`bench/judge_external.py`, `bench/m10_agent.py` (estrutura), `bench/agreement.py`,
`bench/label_answers.py`, `bench/anchor_qrels.py`, `bench/section_recall.py`,
`bench/judge_probe.py`, `bench/prices.yaml`, `bench/m10_tools.py` e
`bench/tasks/external.yaml` (contagens).

---

## 1. Lições acionáveis para o NINA (por impacto esperado)

### L1. O teste de não inferioridade não tem condição de validade: H1 pode "passar" porque os dois braços falham

- **O que as fontes mostram:**
  - Em ensaios de não inferioridade, a regra consolidada (ICH E10; guia de
    não inferioridade da FDA, 2016) é a *assay sensitivity*. O estudo precisa
    mostrar que o comparador funcionou, isto é, que ele bate o "placebo". Sem
    isso, dois tratamentos igualmente inúteis passam no teste.
  - A margem deve preservar uma fração declarada do efeito do comparador
    sobre o placebo.
  - Isto vem de conhecimento consolidado de bioestatística. Não reli as
    fontes nesta rodada.
- **Por que importa para o NINA:**
  - Em `bench/m10_analysis.py`, H1 é só `delta_lo_task > -0.075`.
  - A população primária são as tarefas em que `no_docs` *falhou* na
    triagem (§2.2). Se as tarefas forem difíceis demais, ou o juiz estrito
    demais (Emenda 13 conta abstenção como erro), `full_page` e `nina` ficam
    ambos baixos. Aí Δ ≈ 0 com intervalo estreito, H1 passa, e o R de H2 vira
    a razão de dois números pequenos e instáveis.
  - O §7 permitiria a manchete mesmo assim. É a primeira crítica que um
    estatístico faria.
- **Mudança proposta (emenda antes do piloto; só `bench/` e o relatório):**
  1. **Condição de validade.** A manchete do §7 só sai se o limite inferior
     do IC 95% de success(`full_page`) estiver acima de um piso fixado agora,
     por exemplo 0,30. Se o piso não for atingido, os números saem sem
     manchete, como no caso de H1 falhar.
  2. **Retenção relativa.** Relatar também S(`nina`)/S(`full_page`), com
     bootstrap pareado. Uma margem absoluta de 7,5 pontos com `full_page` em
     40% admite uma perda relativa de 19%, e o leitor precisa ver isso.
  3. **Justificativa da margem.** No relatório, escrever a margem como fração
     do efeito da documentação: S(`full_page`) menos a taxa de acerto do
     `no_docs`. Atenção: a triagem selecionou tarefas em que o `no_docs`
     falhou uma vez, então rodar o `no_docs` de novo nelas mostraria
     regressão à média. Se não houver nova rodada (custaria pouco: sem
     ferramentas, só saída curta), dizer isso explicitamente.
- **Custo:** zero (emenda, mais um campo em `Verdicts`).
- **Evidência:** forte como princípio metodológico. A aplicação ao M10 é
  análise minha, feita sobre o código.

### L2. Os rótulos humanos têm cotas iguais por estrato, então κ e o portão de 0,6 não medem o juiz na população

- **O que as fontes mostram:**
  - **Dorner, Nastl e Hardt** ("Limits to scalable evaluation at the
    frontier", ICLR 2025; só o resumo): quando o juiz não é mais preciso que
    o modelo avaliado, nenhuma técnica de correção (PPI e afins) reduz os
    rótulos humanos necessários a menos da metade. Na prática a economia é
    ainda menor.
  - **Thakur et al.** ("Judging the Judges", 2024/25; resumo): mesmo em QA
    simples só os juízes maiores se alinham aos humanos. A concordância
    percentual engana e o κ é preferível. Os juízes erram para o lado
    indulgente.
  - **Krumdick et al.** ("No Free Labels", 2025; resumo): a concordância
    agregada esconde que o juiz falha nas perguntas que ele mesmo não
    saberia responder. Uma referência humana corrige boa parte disso.
  - **`ppi_py`** (README lido): dá IC válido para a média combinando poucos
    rótulos humanos com muitas predições do juiz.
- **Por que importa para o NINA:**
  - `bench/label_answers.py` sorteia 100 respostas com 10 por estrato
    braço × veredito (Emenda 5). Metade da amostra é "juiz disse correto" e
    metade "juiz disse incorreto", qualquer que seja a prevalência real. O κ
    depende da prevalência.
  - `bench/agreement.cohen_kappa` calcula κ sem pesos, então o portão de 0,6
    do §9 é medido numa distribuição artificial.
  - O que enviesa Δ não é o erro médio do juiz, e sim o **erro diferencial
    entre braços**. Um exemplo: um juiz indulgente com respostas que listam
    várias APIs, coisa mais comum no braço com mais contexto. Com 10 rótulos
    por estrato, a sensibilidade e a especificidade por braço ficam com IC de
    ±30 pontos.
- **Mudança proposta (emenda antes de qualquer rótulo; custo só de tempo
  humano):**
  1. Relatar a concordância **ponderada pelo tamanho de cada estrato** (peso
     = N do estrato / n amostrado) e a sensibilidade e especificidade do juiz
     **por braço**, com IC.
  2. Concentrar os rótulos onde a manchete depende deles. Por exemplo,
     manter 100 no total, mas com 25 por estrato em `nina` e `full_page` e 0
     a 5 nos braços secundários. Ou subir para 160 se o humano aceitar.
  3. Sensibilidade (não decisão): um Δ corrigido pelo estimador estratificado
     ou por PPI com os rótulos humanos. Pela lição de Dorner, não esperar
     intervalo muito mais estreito. O ganho é detectar viés, não ganhar poder.
- **Custo:** zero em dinheiro; só código em `bench/` e horas do humano.
- **Evidência:** a consequência estatística é segura. Os trabalhos citados
  entraram só por resumo; o README do `ppi_py` foi lido na íntegra.

### L3. O CPT exclui o custo do próprio NINA (embeddings e rerank)

- **O que as fontes mostram:**
  - **Parallel** (posts do fornecedor; só resumo): o custo é declarado em USD
    por 1.000 perguntas, *incluindo tokens do LLM e chamadas de ferramenta*.
    É o formato que o mercado passou a usar para alegações de custo.
  - **Context Bench** (opactorai): relata só os tokens devolvidos.
  - **Upstash** (Context7 contra o web search do Claude Code: "−34,56% de
    custo", só resumo): mede o custo do próprio produto sem harness público.
    Foi exatamente esse tipo de alegação que perdeu credibilidade.
- **Por que importa para o NINA:**
  - `bench/prices.yaml` só tem modelos de LLM, e o §6 calcula o USD da sessão
    a partir do uso do agente.
  - Os custos de embeddings da OpenAI e do rerank da Jina, que cada
    `assemble_context` gera (`bench/m10_tools.py:246`, "paid only on a cache
    miss"), ficam fora do CPT do braço `nina`. O mesmo vale para o
    `strong_rag`.
  - O cache em `bench/.cache/` é compartilhado entre braços e execuções, de
    modo que nem o custo real pago corresponde ao de um cliente.
  - O WebFetch tem o custo do Haiku incluído (Emenda 8); o NINA não tem o
    equivalente. Um crítico vai apontar essa assimetria.
- **Mudança proposta (emenda; só `bench/`):**
  - O cliente do bench já conta as chamadas de provedor. Registrar por sessão
    os tokens embutidos e os documentos reranqueados *como se não houvesse
    cache*, e precificar em `prices.yaml`.
  - Relatar um "CPT completo" ao lado do CPT do §6, como sensibilidade, e
    dizer se R muda. A ordem de grandeza deve ser desprezível frente ao
    agente, mas isso precisa estar escrito.
  - Usar também a unidade "USD por 1.000 tarefas corretas".
- **Custo:** zero.
- **Evidência:** forte sobre o NINA (lido no código). A prática do mercado
  vem só de resumos.

### L4. O pré-registro não é verificável por terceiros

- **O que as fontes mostram:**
  - "The Leaderboard Illusion" (Cohere e outros, NeurIPS 2025; resumo): a
    divulgação seletiva (testar 27 variantes em privado e publicar a melhor)
    foi o que mais minou a confiança na Chatbot Arena.
  - A defesa padrão é um registro com data pública, anterior aos dados.
- **Por que importa para o NINA:**
  - O PREREG diz que "o hash e a data do commit são o carimbo de tempo".
  - Mas `github.com/mathvencovsky/agent-context-compiler` responde 404
    (privado). Um commit em repositório privado pode ser reescrito, então o
    carimbo vale para nós e não para quem ler o resultado.
- **Mudança proposta (custo zero, antes do piloto):**
  - Publicar o `PREREG.md` congelado num registro público com data, como OSF
    Registries ou AsPredicted (gratuitos), ou ao menos um gist público com o
    sha256 do arquivo e os tree ids de `app/` e `migrations/` da Emenda 15.
  - Opcionalmente, registrar esse hash no OpenTimestamps.
  - Repetir a cada emenda nova.
  - Publicar junto `bench/results/m10a/changes.md`, que já lista as
    variantes tentadas e rejeitadas no dev. É a resposta direta à crítica de
    "variantes escondidas".
- **Custo:** zero.
- **Evidência:** forte (a página do repositório confere; o princípio é
  consolidado).

### L5. Avaliar o NINA de graça no conjunto público de um concorrente: Exa WebCode

- **O que a fonte mostra** (repositório `exa-labs/benchmarks`, MIT, lido na
  íntegra: README, `data/webcode/*.jsonl`, `benchmarks/graders/rag.py`,
  `harness/statistics.py`):
  - **Highlights:** 250 tarefas `{query, expected_answer, citation_url,
    citation_excerpt}`, descrito como "in-document retrieval given a URL +
    query". É literalmente o `assemble(question, [url], max_tokens)` do
    NINA, com um trecho-ouro verbatim (mediana de 212 caracteres).
  - **RAG:** 307 tarefas `{query, expected_answer, source_url,
    citation_excerpt}`. Hosts mais comuns: gnu.org 40, w3.org 35,
    datatracker.ietf.org 15, docs.python.org 11, docs.spring.io 10.
  - Resultados publicados (Highlights): Exa com 94,8 de groundedness, 93,2 de
    correctness e 696 tokens em média; Parallel 85,6 / 86,4 / 858; Claude
    81,5 / 85,9 / 319.
  - O próprio README admite que as tabelas são "historical … no CIs".
  - O avaliador de groundedness é um LLM. O harness tem bootstrap de IC 95%
    por tarefa.
- **Por que importa para o NINA:**
  - É o único conjunto público, licenciado e com trecho-ouro por URL que
    testa exatamente o caso de uso do NINA, e foi montado por um concorrente.
  - Um resultado bom no conjunto do adversário chama mais atenção do que
    qualquer número em tarefas próprias. Um resultado ruim mostra uma lacuna
    real antes de gastar com o M10.
- **Protocolo sem LLM (só `bench/`; `app/` fica intocado):**
  1. Snapshot das 557 URLs com `bench/snapshot.py`, respeitando robots.txt.
     Descartar páginas que `bench/page_health.classify` marca, e tarefas cujo
     trecho-ouro não aparece mais no snapshot (validade do gabarito, como no
     §2.1).
  2. Métrica: `bench.section_recall.coverage` e `token_precision`, tratando o
     trecho como a "seção". Acerto = trecho contido no contexto. Orçamentos
     de 500, 700, 1.000 e 2.000 tokens; o de 700 é o ponto médio da Exa.
  3. Comparações:
     - página inteira em trafilatura (o custo em tokens de referência, com
       100% de recall por construção);
     - `naive_rag` e `strong_rag` dos runners de `bench/runners`;
     - o NINA completo.
     O único número comparável com a Exa sem LLM é a média de tokens; a
     correctness dela exige juiz.
  4. Pré-registrar antes de medir uma divisão metade A / metade B (como no
     dev). Se algum resultado motivar mudança em `app/`, a metade A vira
     dado de ajuste, e isso vai para uma emenda com novo congelamento.
- **Custo:** zero para os baselines lexicais. O NINA completo custa centavos
  de embeddings (cerca de 557 páginas) e uma chamada de rerank por tarefa.
  Pela regra do humano, isso precisa de autorização, ou se roda só com o que
  o cache já tiver.
- **Riscos:**
  - As perguntas parecem ter sido geradas por LLM a partir do trecho (o mesmo
    problema das tarefas sintéticas do M8): a sobreposição lexical favorece a
    busca lexical.
  - 36 das 250 URLs do Highlights são do github.com (arquivos de código) e
    várias são blogs (dev.to, medium); são casos fora do foco do NINA.
    Relatar à parte.
- **Evidência:** dados e código lidos na íntegra. O blog da Exa não foi
  acessível.

### L6. Publicar o gabarito "pergunta do Stack Overflow → seção da documentação oficial", que não existe publicamente, e estendê-lo ao pool de avaliação para o §8

- **O que as fontes mostram:**
  - **FreshStack** (Thakur, Lin, Havens, Carbin, Khattab e Drozdov;
    Waterloo e Databricks, 2025; README, código e leaderboard lidos):
    - monta benchmarks de recuperação a partir de perguntas do Stack
      Overflow, com "nuggets" gerados por LLM a partir da resposta e qrels
      sobre *pedaços de repositórios GitHub*;
    - dados sob CC BY-SA 4.0 e código Apache 2.0;
    - cinco tópicos, um deles **Angular 16 a 18**;
    - números: BM25 tem α-nDCG@10 de 0,218; Qwen3-8B 0,365; o "oracle"
      (busca guiada pelos nuggets) 0,541. Ou seja, até os melhores
      recuperadores ficam longe do teto.
  - A busca não achou nenhum conjunto que ligue respostas do SO a *trechos da
    documentação oficial*. A frase do resumo foi: "I didn't find a dataset
    that explicitly maps Stack Overflow answers to official API documentation
    passages as ground truth for RAG evaluation".
- **Por que importa para o NINA:**
  - `bench/anchor_qrels.py` faz exatamente isso, de forma determinística:
    pega a âncora que o autor da resposta aceita linkou e extrai a seção do
    HTML congelado. Pela Emenda 6, 328 das 756 tarefas do dev (43%) têm
    âncora válida.
  - Hoje só **176 das 3.054 tarefas (5,8%)** têm `expected_evidence`
    (contado em `external.yaml`). Assim a maior parte das falhas do §8 cairá
    em `unattributed`, e a análise de causas que o `decision.md` usa para
    recomendar mudanças ficará quase vazia.
- **Mudança proposta:**
  1. **Antes do piloto, por emenda (só `bench/`; olha HTML e respostas
     aceitas, nunca resultados):** rodar `anchor_qrels` também no pool de
     avaliação e usar a seção como evidência-ouro do §8, com critério de
     acerto fixado agora: `section_recall.coverage ≥ HIT`. A atribuição de
     falhas deve subir de cerca de 6% para cerca de 40% das tarefas.
  2. **Depois do M10:** publicar "SO→Docs anchors" (pergunta, resposta aceita
     com atribuição, URL, âncora, texto da seção, hash do snapshot) como
     dataset CC BY-SA. É uma contribuição original e citável, e serve de
     isca para pesquisadores rodarem o NINA e concorrentes no mesmo gabarito
     sem LLM.
- **Custo:** zero.
- **Evidência:** FreshStack lido no GitHub (o artigo não). A ausência de
  dataset equivalente vem de uma busca e não é prova.

### L7. Checagens de contaminação de custo zero, pré-registradas como exploratórias

- **O que as fontes mostram:**
  - **"Search-Time Data Contamination"** (Scale AI, 2025; resumo): agentes
    com busca acharam a pergunta junto com a resposta (datasets no
    HuggingFace) em cerca de 3% das perguntas do HLE, SimpleQA e GPQA.
    Bloquear o HF derrubou a acurácia nesse subconjunto em cerca de 15%.
  - **OpenAI, "Why we no longer evaluate SWE-bench Verified"** (fev. 2026;
    resumo):
    - modelos reproduzem de memória as correções originais;
    - 59,4% das 138 tarefas mais difíceis auditadas tinham defeito de
      enunciado ou de teste.
  - **Caspari et al.** (TMLR 2025; resumo): mede memorização comparando
    respostas do LLM a perguntas do SO dentro e fora do pré-treino pela
    sobreposição com a resposta aceita.
  - **ACL 2026 ("Test of Time"; resumo):** a queda pós-corte usada como sinal
    de contaminação depende de como a pergunta é construída.
- **Por que importa para o NINA:**
  - As tarefas são de 2023-01 a 2024-03 (campo `created`), bem antes do corte
    do `claude-sonnet-5-5`. A triagem `no_docs` remove o que o modelo já sabe,
    mas há dois vazamentos que ela não pega:
    1. **Braços ao vivo.** O `webfetch_live` só alcança os hosts do Apêndice C
       e os domínios de documentação que o Claude Code pré-aprova (Emenda 8).
       O `context7_live` pode servir trechos de terceiros que copiam a
       resposta do SO.
    2. **Memória ativada pelo contexto.** Um braço pode "lembrar" a resposta
       aceita ao ver a página.
- **Mudança proposta (emenda; análise pós-execução só em `bench/`):**
  - **Vazamento na ferramenta:** procurar nos resultados de ferramenta das
    transcrições (a) URLs de stackoverflow.com, stackexchange.com e espelhos
    conhecidos; (b) sobreposição de 8-gramas com a resposta aceita acima de
    um limiar fixado agora. Relatar por braço e repetir as comparações sem
    essas sessões, como já se faz na Emenda 16.
  - **Memorização na resposta:** razão entre a sobreposição de n-gramas da
    resposta final com a resposta aceita e a sobreposição com as páginas
    citadas. Relatar por braço, só descritivo.
  - **Data:** Δ por trimestre de criação da pergunta, exploratório, com o
    aviso de que não prova contaminação.
- **Custo:** zero.
- **Evidência:** só resumos. A aplicação ao M10 é análise minha.

### L8. FreshStack-Angular como segundo teste externo, que mede a diversidade da seleção

- **O que a fonte mostra:**
  - O `EvaluateRetrieval` do FreshStack (código lido) calcula três métricas:
    - **α-nDCG@10**: penaliza resultados que repetem o mesmo nugget;
    - **Coverage@20**: fração dos nuggets cobertos;
    - **Recall@50**.
  - Os rerankers não melhoraram a primeira etapa em 2 dos 5 tópicos (resumo
    do artigo).
- **Por que importa para o NINA:**
  - A seleção "redundancy-aware" é uma alegação do produto, mas desde a
    mudança de λ de 0,3 para 0,0 (Emenda 9) a penalidade de diversidade está
    desligada.
  - O α-nDCG e o Coverage são a métrica pública que mediria se a seleção
    cobre *aspectos distintos* da resposta. Hoje o `section_recall` mede uma
    seção só.
  - O tópico Angular é do mesmo domínio do Apêndice C (angular.dev).
- **Proposta:**
  - Converter os caminhos `angular/adev/src/content/...` dos docids em URLs
    angular.dev, dar ao NINA as URLs relevantes mais distratores, e medir
    Coverage@orçamento sobre os nuggets via qrels, sem LLM.
  - Os dados vêm do HuggingFace, o que exige rodar na máquina do humano.
- **Custo:** zero para o lexical; centavos de embeddings e rerank para o NINA
  completo. Esforço médio, por causa do mapeamento de caminhos.
- **Evidência:** README, código e leaderboard lidos. O artigo não foi
  acessível.

### L9. Medir quanto do contexto pago foi usado, sem LLM e nas transcrições que o M10 já guarda

- **O que as fontes mostram:**
  - **ContextBench** (Nanjing e UCL, fev. 2026; README lido): 1.136 tarefas
    com contexto-ouro anotado por humanos. Os LLMs "favor recall over
    precision" e há uma "substantial gap between explored and utilized
    context".
  - **BrowseComp-Plus** (Waterloo, NeurIPS 2025; README lido; números por
    resumo): com o mesmo agente (GPT-5), trocar BM25 por Qwen3-Embedding-8B
    sobe a acurácia para 70,1% *e reduz o número de buscas*. Recuperação
    melhor aparece também em menos chamadas.
- **Por que importa para o NINA:**
  - A tese do NINA é exatamente "o mesmo acerto com menos contexto". A
    métrica mais direta dessa tese é a fração dos tokens devolvidos pela
    ferramenta que contém as citações usadas na resposta final.
  - Os `quote` das citações já são verbatim (§4), e o casamento já existe em
    `bench.grade._grade_citations`.
- **Proposta (pré-registrar como exploratório):**
  - Por braço: tokens de ferramenta por sessão, a fração deles dentro de
    trechos citados, as chamadas de ferramenta por tarefa correta e a
    correlação com o acerto.
  - Se o `nina` tiver o mesmo acerto com menos chamadas, esse é o mesmo
    argumento do BrowseComp-Plus.
- **Custo:** zero.
- **Evidência:** READMEs lidos; números do BrowseComp-Plus só por resumo.

### L10. Detalhes estatísticos baratos que blindam o relatório

- **Intervalo de checagem para Δ pareado:**
  - O percentil do bootstrap para diferença de proporções pareadas cobre um
    pouco menos que o nominal quando há poucas discordâncias.
  - Relatar ao lado, só descritivo, o intervalo de escore de Tango ou o
    híbrido de Newcombe, o padrão de ensaios de não inferioridade pareados.
  - Já há McNemar exato descritivo no §6; é o mesmo espírito.
- **Efeito mínimo detectável por faixa de página (Emenda 4):**
  - Calcular agora, a partir das proporções de página curta, média e longa no
    pool, o n esperado por faixa e o MDE com ψ = 0,25.
  - Se a faixa longa tiver cerca de 40 tarefas, o IC terá cerca de ±20
    pontos. Escrever isso antes evita ler padrão em ruído.
- **Ordem fixa H1 → H2:** o §7 já só dá manchete de eficiência se H1 passar.
  Isso é *gatekeeping* e preserva o α global. Vale dizer explicitamente no
  relatório; é ponto a favor.
- **Respostas sem JSON:** `final_answer` usa a mensagem inteira quando falta
  o JSON. Relatar a taxa por braço, porque respostas longas tendem a ser
  julgadas de forma diferente.
- **Custo:** zero.
- **Evidência:** métodos consolidados; código do NINA lido.

### L11. Como apresentar o resultado para que chame atenção e resista a críticas

- **O que as fontes mostram:** cada harness de fornecedor coroa o próprio
  fornecedor.

  | harness | quem vence | números | lido |
  |---|---|---|---|
  | Exa (`exa-labs/benchmarks`) | Exa | Highlights: 94,8 contra 85,6 da Parallel | na íntegra |
  | Tavily (`tavily-search-evals`) | Tavily | SimpleQA: 93,3% contra 71,24% da Exa | na íntegra |
  | Perplexity (`search_evals`) | Perplexity | BrowseComp: 0,805 contra 0,38 da Exa | na íntegra |
  | Context Bench (Deepcon) | Deepcon | 18/20 contra 13/20 do Context7, 20 cenários, sem IC | na íntegra |
  | Nia | Nia | contra o Context7 | resumo |
  | Upstash | Context7 | contra o web search | resumo |

  - A exceção que ganhou credibilidade foi a Parallel publicar um ponto em que
    a Perplexity empata com custo menor (74%: US$ 275 contra US$ 399 por
    1.000 perguntas).
  - O que esses trabalhos têm em comum: n pequeno (20, 50 ou 59), perguntas
    geradas por LLM, juiz único e nenhum pré-registro.
- **Proposta para o `decision.md` e para os materiais:**
  1. **Um gráfico único de Pareto:** acerto (IC 95%) contra USD por 1.000
     tarefas (IC 95%), com todos os braços, incluindo `no_docs` como piso.
  2. **Publicar o harness com adaptadores**, como o `systems.toml` da Exa e a
     configuração com hash do `search_evals`, e convidar Context7, Ref e Exa
     a mandar a própria configuração. Os snapshots de terceiros têm licenças
     variadas; publicar os hashes e o script de snapshot.
  3. **Liderar com o pré-registro e com o que deu errado:** as variantes
     rejeitadas no M10a e qualquer hipótese que falhar.
  4. **`webfetch_live` como secundário "chave":**
     - Um crítico dirá que vencer `full_page` em custo é fácil. A comparação
       que interessa ao usuário do Claude Code é contra o WebFetch.
     - Sem tirar o rótulo de exploratório do §7, pré-registrar já a análise
       dele: o mesmo Δ, o mesmo R, e a sensibilidade sem as sessões
       bloqueadas da Emenda 16.
- **Custo:** zero.
- **Evidência:** quatro READMEs lidos na íntegra; os demais por resumo.

### L12. Juízes: o que a literatura de 2025–2026 acrescenta ao que já está feito

- **Viés de autopreferência.** O segundo juiz (`claude-opus-5-5`) é da mesma
  família do agente (`claude-sonnet-5-5`). Relatar κ(primário, segundo) **por
  braço**. Uma queda num braço específico sinaliza viés de estilo.
- **Ensemble como sensibilidade:**
  - O Context Bench usa maioria de 3 juízes, o umBRELA tem `EnsembleJudge`, e
    o trabalho de 2024/25 sobre referência e maioria em QA relatou κ
    "substantial to near-perfect".
  - Relatar Δ só no subconjunto em que os dois juízes concordam, como
    sensibilidade e não como decisão. Não exige chamada nova, porque os dois
    juízes já estão no plano.
- **O comprimento também engana humanos.** "Are We on the Right Way to
  Assessing LLM-as-a-Judge?" (dez. 2025; resumo) achou rótulos humanos que
  premiam respostas prolixas. Isso confirma o risco já anotado em
  `PESQUISA_LABS.md` 2b. O NINA já atenua isso ao julgar só o campo `answer`
  do JSON final.
- **Custo:** zero.
- **Evidência:** só resumos.

---

## 2. Onde o NINA já está igual ou melhor

| ponto | NINA | melhor prática pública encontrada |
|---|---|---|
| Corpus congelado nos braços principais | §3, SPEC §10.1 | BrowseComp-Plus surgiu justamente para trocar a web ao vivo por corpus fixo. Exa, Tavily, Perplexity e Parallel avaliam na web ao vivo. |
| Perguntas reais de humanos | Stack Overflow 2023–24, resposta aceita verbatim | FreshStack (as mesmas perguntas do SO). Os conjuntos da Exa (Highlights), do docs-mcp-server ("draft", escrito por LLM) e do Context Bench (20 feitos à mão) são sintéticos. |
| Tamanho com cálculo de poder | n = 349 no piso ψ = 0,25 (§5) | Context Bench: 20; Parallel BrowseComp: amostra de 50; docs-mcp search-eval: 59. Nenhum declara poder. |
| Pré-registro com emendas datadas e "dados vistos: nenhum" | §12 | Nenhum fornecedor faz. (Falta a parte pública: L4.) |
| IC por bootstrap, com versão por agrupamento | §6 e Emenda 13 | O harness da Exa tem bootstrap por tarefa (empate). Nenhum fornecedor usa bootstrap por agrupamento. |
| Juiz guiado por referência, raciocinando antes, com mascaramento, sondas e rótulos humanos com portão de κ | §9, Emendas 5 e 13, `judge_probe.py` | A Exa usa o avaliador do SimpleQA, sem humanos. O Context Bench usa 3 LLMs sem humanos. O FreshStack valida nuggets contra humanos no artigo (resumo). |
| Custo reconciliado com a fatura (±3%), com tokens de cache | §6 | A Parallel inclui tokens e chamadas, mas sem reconciliação. Os outros só relatam tokens. |
| Triagem de "responde de memória" | §2.2 | É a preocupação central do FreshStack, do SWE-bench Pro e da OpenAI sobre o SWE-bench Verified. Poucos benchmarks de ferramentas fazem isso. |
| Gabarito de seção sem LLM e determinístico | `anchor_qrels.py`, `section_recall.py` | O docs-mcp-server tem gabarito de trechos sem LLM, mas escrito por LLM ("draft"). Não há outro gabarito público derivado do humano que respondeu. |
| Julgar só a resposta concisa | `final_answer` (campo `answer`) | Atenua o viés de comprimento que a literatura de 2025 aponta até em humanos. |

## 3. Onde o NINA está atrás

1. **Ninguém de fora consegue verificar nem replicar.** O repositório é
   privado, o pré-registro não tem carimbo público e não há harness publicado
   (L4, L11). A Exa, a Perplexity, a Tavily, o FreshStack e o
   BrowseComp-Plus publicam código e dados.
2. **Nenhum resultado em conjunto externo.** Todo número do NINA vem de
   tarefas que o próprio projeto montou. A ponte do M8 (41 tarefas) foi
   redigida por LLM a partir da extração do próprio NINA, um viés a favor.
   Concorrentes publicam em conjuntos públicos (L5, L8).
3. **O custo do NINA não entra no CPT** (L3). O WebFetch tem o Haiku
   incluído; o NINA não tem embeddings nem rerank.
4. **A atribuição de falhas cobre 5,8% das tarefas** (`expected_evidence` em
   176 de 3.054), contra cerca de 40% possíveis com as âncoras (L6).
5. **Não há número de recuperação contra o Context7 com métrica
   determinística.** O docs-mcp-server já tem: no `search-eval` com 59
   consultas, MRR por página 0,747 contra 0,695 do Context7 e Hit@5 0,881
   contra 0,864. A diferença é pequena, num conjunto escrito por LLM, mas é
   um baseline público que o NINA não tem.
6. **A latência não está em nenhuma hipótese.** A Exa e a Cognition (SWE-grep)
   vendem velocidade. O M10 registra o tempo de relógio; basta relatá-lo por
   braço, com IC.
7. **O comparador principal pode ser lido como espantalho.** Vencer
   `full_page` em custo é esperado. Sem dar destaque pré-registrado ao
   `webfetch_live` (L11), a manchete fica vulnerável.

## 4. Fontes lidas na íntegra

- https://github.com/exa-labs/benchmarks: README, `data/webcode/highlights.jsonl`,
  `rag.jsonl`, `contents.jsonl`, `benchmarks/graders/rag.py`,
  `harness/statistics.py`, LICENSE (MIT).
- https://github.com/fresh-stack/freshstack: README (com o leaderboard de
  12/06/2025), `freshstack/retrieval/evaluation.py`, `pyproject.toml`.
- https://github.com/texttron/BrowseComp-Plus: README.
- https://github.com/perplexityai/search_evals: README.
- https://github.com/tavily-ai/tavily-search-evals: README.
- https://github.com/opactorai/context-bench: README.
- https://github.com/EuniAI/ContextBench: README.
- https://github.com/arabold/docs-mcp-server: `docs/guides/benchmarking.md`,
  `tests/search-accuracy/README.md`, `tests/search-eval/baseline.json`,
  `tests/search-eval/baseline.context7.json`, `tests/search-accuracy/baseline.json`
  e `golden.yaml` (contagens).
- https://github.com/aangelopoulos/ppi_py: README.
- https://github.com/castorini/umbrela e https://github.com/castorini/nuggetizer:
  READMEs (só a parte de juízes e ensemble; o resto já estava coberto).

Só por resumo de busca (sem leitura completa): Dorner et al.; Thakur et al.;
Krumdick et al.; Search-Time Contamination; o post da OpenAI sobre o SWE-bench
Verified; The Leaderboard Illusion; os posts da Parallel; Letta Context-Bench;
Caspari et al.; AutoNuggetizer; LibEvolutionEval; SWE-bench+; os artigos do
FreshStack, do BrowseComp-Plus e do ContextBench.

## 5. Fontes que não consegui acessar e que valem a leitura completa

1. **FreshStack** (artigo): https://arxiv.org/pdf/2504.13128. Como validaram
   os nuggets contra humanos e como escolheram as perguntas. Base para L6 e L8.
2. **Exa, "WebCode"** (blog): https://exa.ai/blog/webcode. Como as perguntas
   foram geradas e o protocolo do Highlights, para saber o viés antes de rodar
   L5.
3. **Dorner, Nastl e Hardt, "Limits to scalable evaluation at the frontier"**:
   https://arxiv.org/pdf/2410.13341. Teorema e experimentos sobre quantos
   rótulos humanos dá para economizar com o juiz (L2).
4. **Krumdick et al., "No Free Labels"**: https://arxiv.org/pdf/2503.05061.
   Números de concordância com e sem referência humana; apoia a rubrica do M10.
5. **Thakur et al., "Judging the Judges"**: https://arxiv.org/pdf/2406.12624.
   Indulgência dos juízes e κ contra concordância percentual em QA curta.
6. **Han et al. (Scale AI), "Search-Time Data Contamination"**:
   https://arxiv.org/pdf/2508.13180. As boas práticas propostas para braços
   com busca ao vivo (L7).
7. **OpenAI, "Why we no longer evaluate SWE-bench Verified"**:
   https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/.
   Método da auditoria de tarefas e do teste de memorização; modelo para a
   auditoria do Apêndice B.
8. **BrowseComp-Plus** (artigo): https://arxiv.org/pdf/2508.06600. Como
   separaram o efeito do recuperador do efeito do agente, e chamadas por
   tarefa como métrica (L9).
9. **ContextBench** (artigo): https://arxiv.org/pdf/2602.05892. Definições de
   recall, precisão e eficiência do contexto ao longo da trajetória (L9).
10. **"The Leaderboard Illusion"**: https://arxiv.org/pdf/2504.20879. Base da
    crítica de divulgação seletiva (L4).
11. **Parallel, "Search API benchmark"**:
    https://parallel.ai/blog/search-api-benchmark. O formato de custo por
    1.000 perguntas com tudo incluído (L3, L11).
12. **Caspari et al., memorização com respostas do Stack Overflow** (TMLR
    2025): https://mlanthology.org/tmlr/2025/caspari2025tmlr-studying/. O
    teste de sobreposição com a resposta aceita (L7).
13. **"The Great Nugget Recall" (AutoNuggetizer, grupo de Jimmy Lin, Waterloo)**:
    https://arxiv.org/pdf/2504.15068. Concordância por consulta é fraca e por
    sistema é forte; diz até onde métrica de nugget serve para o NINA.
14. **FDA, "Non-Inferiority Clinical Trials to Establish Effectiveness"**
    (2016): https://www.fda.gov/media/78504/download. *Assay sensitivity* e a
    escolha da margem (L1).
15. **Angelopoulos et al., "Prediction-Powered Inference"**:
    https://arxiv.org/pdf/2301.09633. Fundamento da correção da L2,
    principalmente com amostragem estratificada.
