# Pesquisa, rodada 3 (2026-10-28): onde o NINA está, onde está atrás e o plano

O humano decidiu não financiar nenhuma rodada paga enquanto não houver certeza
de que o NINA está no nível das melhores ferramentas do mercado ou acima. Esta
rodada procurou tudo o que ainda não tínhamos aprendido. Foram sete frentes em
paralelo, mais uma auditoria de segurança do código feita pelo agente
`nina-engineer`.

Os relatórios completos ficam em `docs/pesquisa/rodada3/`:

| frente | arquivo |
|---|---|
| A. Concorrentes e produtos vizinhos | `A_concorrentes.md` |
| B. Recuperação (chunking, BM25, embeddings, rerank) | `B_recuperacao.md` |
| C. Extração (HTML, PDF, JS), com experimentos locais | `C_extracao.md` |
| D. Agentes, MCP e formato de saída | `D_agentes_mcp.md` |
| E. Segurança (SSRF, injeção indireta, MCP) | `E_seguranca.md` |
| F. Avaliação e publicação crível | `F_avaliacao.md` |
| G. Código-fonte do Firecrawl (só ideias; o código é AGPL) | `G_firecrawl_codigo.md` |
| Auditoria de segurança do `app/` | `docs/AUDITORIA_SEGURANCA.md` |

Este ambiente só alcança o GitHub e os sites da Anthropic. Por isso arXiv,
Hugging Face, blogs e documentação de concorrentes entraram só por resumos de
busca, e cada relatório marca o que veio assim. A lista do que vale ler
completo, para o humano salvar em PDF, está no fim deste arquivo.

## 1. O que foi conferido de forma independente

Cada achado abaixo foi refeito pela sessão principal, não só relatado:

- **SSRF no navegador (auditoria C1, A4):** reproduzido. Um 302 dentro do
  Chromium leva ao endereço de metadados e o conteúdo volta no documento; um
  popup chega ao alvo. É a mesma falha da CVE-2026-32857 do Firecrawl.
- **Guarda:** `fec0::/10` e `168.63.129.16` (Azure WireServer) passam;
  `::127.0.0.1` é bloqueado.
- **Resultados grandes do `assemble_context` (frente D, D1):** a ferramenta não
  declara `anthropic/maxResultSizeChars`, então o Claude Code salva em arquivo
  todo resultado acima de 50.000 caracteres, e o agente do M10 não lê arquivos.
- **PDF de LaTeX (frente C):** num PDF diferente do usado pelo pesquisador (o
  artigo do WebArena, 3 páginas), 187 de 529 palavras saem coladas
  ("WithadvancesingenerativeAI"), e cada linha vira um parágrafo (175
  parágrafos).

## 2. Em andamento (aprovado pelo humano em 2026-10-28)

O `nina-engineer` implementa, num único lote, com uma emenda de recongelamento
depois da revisão:

- todos os achados graves da auditoria: navegador por um proxy local de saída
  que aplica o guarda (é o desenho que o Firecrawl adotou depois da CVE),
  service workers bloqueados, bomba de gzip, limites de PDF com aviso, prazos
  reais, `ENV` sem padrão inseguro, faixas de IP que faltavam e os achados
  baixos baratos;
- recusar URLs que parecem carregar segredos (canal de exfiltração);
- remover Unicode invisível e avisar sobre texto escondido por CSS, sem apagar
  abas e blocos recolhíveis;
- `maxResultSizeChars` no `assemble_context`.

## 3. Onde o NINA já está igual ou melhor (com evidência)

- **Proteção de rede:** o guarda bloqueou os 39 truques de URL da matriz da
  auditoria, conecta no IP validado com Host e SNI e revalida redirecionamentos
  no caminho httpx. O Firecrawl não limita o corpo, aceita até 5.000
  redirecionamentos e trata o robots.txt como opcional.
- **Falha explícita:** páginas de erro nunca viram documento, e os avisos são
  estruturados. O Firecrawl devolve 404 e 500 como sucesso e tem um único aviso
  em texto livre.
- **Recall de extração:** no WCXB, com 131 páginas reais de documentação, o
  NINA tem o maior recall entre os extratores medidos (0,954).
- **Tabelas e código:** o chunker repete o cabeçalho das tabelas e preserva
  blocos de código. A Jina admite no próprio código que, com filtro por
  pergunta, tabelas e código ficam inalcançáveis.
- **Formato para agentes:** as tags não podem ser forjadas pela página e as
  dicas não dão ordens ao agente; os ataques ContextCrush e "notas ocultas" no
  Context7 mostram por que isso importa. O NINA tem 3 ferramentas, ids
  explícitos e é stateless, como a especificação do MCP de julho de 2026
  recomenda.
- **Método:** corpus congelado, perguntas reais, cálculo de poder,
  pré-registro e validação do juiz. Os benchmarks de fornecedores (Exa, Tavily,
  Perplexity) coroam o próprio fornecedor, quase sempre com n entre 20 e 59, sem
  intervalo de confiança.

## 4. Onde o NINA está atrás

1. **Reranker:** o Jina v2 está descontinuado, corta a pergunta em 512 tokens
   (18,5% das tarefas do M10 passam disso) e, pela tabela publicada no
   relatório do Qwen3, fica abaixo de modelos abertos Apache 2.0 em recuperação
   de código (B).
2. **Embedding:** no FreshStack (perguntas do Stack Overflow sobre
   documentação, NeurIPS 2025), o `text-embedding-3-small` empata com o BM25
   (Recall@50 0,330 contra 0,316), contra 0,51 a 0,61 dos melhores. Bate com as
   medições do próprio dev (B).
3. **PDF:** palavras coladas, um parágrafo por linha, tabelas vazias espúrias e
   sumário ignorado. O pypdfium2 (BSD/Apache, já instalado) tem 95% de
   qualidade de texto contra 74% do pdfplumber no py-pdf/benchmarks (C).
4. **Precisão da extração HTML:** 9,2% de boilerplate vazado contra 3,4% do
   trafilatura, porque o fallback da readability dispara em 75% das páginas e
   traz widgets como "Copy for LLM" e "On this page" (C).
5. **Código:** 87% dos blocos saem sem linguagem, os números de linha do Sphinx
   e do Pygments vazam ("1import os"), o prefixo `hljs-` vira a linguagem
   "keyword", o código aparece duplicado em listas e admonitions e palavras
   colam nas fronteiras de bloco (C, G).
6. **Markdown servido pelo site:** GitHub Docs, Mintlify, GitBook, Redis e
   Cloudflare servem a página em Markdown (`rel=alternate type=text/markdown`,
   `.md`, `Accept: text/markdown`). O NINA rejeita `text/markdown` e
   `text/plain` (C, A).
7. **Orçamento:** os concorrentes entregam de 140 a 900 tokens; o padrão do
   NINA é 4000, e nunca medimos abaixo de 2000 (A).
8. **Interface MCP:** as ferramentas não têm `title` nem `readOnlyHint`. Com
   eles, o Claude Code roda as chamadas em paralelo e sem pedir aprovação a cada
   uma, e o diretório de conectores da Anthropic exige os dois. O formato
   também não diz que é um trecho ("N de M seções"); agentes tratam trecho como
   página completa (D).
9. **Frescor e data:** o Parallel Extract aceita `max_age_seconds` por chamada e
   devolve a data da página; o NINA extrai a data, mas não a entrega (A).
10. **Detalhes de fetch:** PDF só reconhecido pelo Content-Type exato (o
    Firecrawl olha `%PDF-`), rotas por hash (`#/`) colapsam num documento só,
    `<base href>` ignorado, cache vencido com origem fora do ar dá erro em vez
    da versão guardada com aviso (G).

## 5. Furos no protocolo do M10 (frente F, sem custo)

- **H1 sem condição de validade:** se as tarefas forem difíceis demais, `nina` e
  `full_page` empatam por baixo e a hipótese "passa". Falta um piso de acerto do
  `full_page` e relatar S(nina)/S(full_page).
- **Concordância do juiz numa amostra artificial:** `label_answers` sorteia
  cotas iguais por braço e veredito. É preciso ponderar pelo tamanho de cada
  estrato e medir o erro do juiz por braço.
- **CPT do `nina` sem embeddings nem rerank**, enquanto o WebFetch inclui o
  Haiku. Favorece o NINA; precisa de um "CPT completo".
- **Pré-registro sem carimbo público:** o repositório é privado. Basta publicar
  o sha256 do PREREG e os tree ids num lugar público antes do piloto (ação do
  humano).
- **Diagnóstico das falhas só em 5,8% das tarefas:** estender `anchor_qrels` ao
  pool de avaliação leva a cerca de 40%.
- **O comparador mais forte não é um braço:** o `web_fetch` da API da Anthropic
  com filtragem dinâmica. O PREREG precisa declarar essa lacuna.

## 6. Benchmarks externos que dá para usar sem LLM

- **Exa WebCode** (MIT): 250 tarefas "URL + pergunta → trecho-ouro", exatamente
  o `assemble` com uma URL. A Exa publica números dela, do Parallel e do
  `web_fetch` da Anthropic. Métrica sem LLM: o trecho-ouro aparece nos
  fragmentos a 500, 1k, 2k e 4k tokens? Custo: centavos de embeddings e rerank
  (ou zero com modelos locais).
- **WCXB-docs** (CC-BY): extração em 131 páginas de documentação; já rodado
  pela frente C.
- **FreshStack** (CC BY-SA): recuperação em perguntas reais de documentação
  técnica, com métricas de diversidade e cobertura.

## 7. Plano proposto

Cada fase termina com `make check` verde e, quando muda o `app/`, uma emenda de
recongelamento. Toda mudança de extração ou seleção segue a disciplina das
metades A/B: regra de aceite fixada antes de medir, medida na metade A e
confirmada na B.

- **Fase S (em andamento):** segurança e as propostas aprovadas. Emenda 17.
- **Fase V (só `bench/` e PREREG, sem custo):** os furos da seção 5, o
  carimbo público (o humano publica o hash) e a lacuna do comparador declarada.
  Emenda 18.
- **Fase P (produto, M10b), na ordem do impacto esperado:**
  1. PDF: palavras, parágrafos, tabelas espúrias, sumário. Medido em PDFs de
     teste com "fatos" verificáveis.
  2. Extração HTML e código: linguagem, números de linha, duplicação,
     fronteiras de bloco, seletores de chrome. Medido no WCXB-docs com
     dev e test separados.
  3. Markdown servido pelo site e `text/markdown`/`text/plain`; spec OpenAPI
     no lugar de Swagger UI e Redoc.
  4. Recuperação: reranker e embedding melhores, título no prefixo do pedaço,
     profundidade do rerank, seleção por segmentos, recalibrar `coverage_*`.
     Modelos abertos (Qwen3-Embedding e Qwen3-Reranker, Apache 2.0) rodam
     localmente sem custo de API, na máquina do humano.
  5. Interface: `title`/`readOnlyHint`, aviso de trecho, data da página, uso da
     âncora `#seção`, skill que pede ao agente para citar URL e `chunk`
     (sobrevive à compactação).
  6. Orçamentos baixos (500 a 2000) medidos no WebCode e no dev.
- **Depois da Fase P:** recongelar, rodar os benchmarks externos e só então
  decidir, com o humano, se o NINA está no nível exigido para financiar o M10.

## 8. Para o humano salvar em PDF

Em ordem de prioridade. Cada item diz por que importa.

**Prioridade 1 (decidem mudanças de produto ou do protocolo):**
1. FreshStack (NeurIPS 2025): https://arxiv.org/pdf/2504.13128. É o benchmark
   mais próximo do M10, com ablações que o leaderboard não mostra.
2. Exa, "WebCode": https://exa.ai/blog/webcode. Como as 250 tarefas foram
   construídas, antes de usá-las como teste externo.
3. jina-reranker-v3: https://arxiv.org/pdf/2509.25085. Tabela completa v2 × v3
   × Qwen3 × bge em código.
4. Qwen3 Embedding (inclui o reranker): https://arxiv.org/pdf/2506.05176.
5. WCXB, benchmark de extração: https://arxiv.org/pdf/2605.21097.
6. "From Agent Behaviour to Agent-Friendly Documentation":
   https://arxiv.org/pdf/2608.20195. O único estudo grande de como agentes
   consultam documentação.
7. Drowning in Documents (profundidade do rerank):
   https://arxiv.org/pdf/2411.11767.
8. FDA, "Non-Inferiority Clinical Trials":
   https://www.fda.gov/media/78504/download. Escolha da margem e "assay
   sensitivity" para o H1.
9. Artificial Analysis, metodologia do Search Index:
   https://artificialanalysis.ai/methodology/search-api. Modelo de comparação
   externa e independente.
10. Parallel, "How we evaluate web search APIs":
    https://parallel.ai/articles/how-we-evaluate-web-search-apis. Custo por mil
    perguntas incluindo tokens do LLM, a métrica da H2.

**Prioridade 2 (segurança):**
11. Design Patterns for Securing LLM Agents against Prompt Injections:
    https://arxiv.org/pdf/2506.08837.
12. Spotlighting: https://arxiv.org/pdf/2403.14720.
13. Prompt Injection via HTML in Web Summarization:
    https://arxiv.org/pdf/2509.05831. Quais técnicas de ocultação funcionam.
14. AWS, "Defending LLM applications against Unicode character smuggling":
    https://aws.amazon.com/blogs/security/defending-llm-applications-against-unicode-character-smuggling/
15. VulnCheck, advisory da CVE-2026-32857 do Firecrawl:
    https://www.vulncheck.com/advisories/firecrawl-playwright-service-ssrf-protection-bypass-via-missing-post-redirect-validation

**Prioridade 3 (refinam decisões):**
16. Bruch et al., fusão para busca híbrida: https://arxiv.org/pdf/2210.11934.
17. BM25 para código com q-log IDF: https://arxiv.org/pdf/2605.18561.
18. Chunking, 36 métodos: https://arxiv.org/pdf/2603.06976.
19. MarginLab, "Do MCPs Actually Improve Coding Agents?":
    https://marginlab.ai/blog/do-mcps-improve-coding-agents-part-1/
20. "MCP Tool Descriptions Are Smelly!": https://arxiv.org/pdf/2602.14878.
21. "Format Matters" (formato do contexto): https://aclanthology.org/2026.lrec-1.593/
22. Cloudflare, "Markdown for Agents":
    https://developers.cloudflare.com/fundamentals/reference/markdown-for-agents/
23. Giuseppe Gurgone, engenharia reversa do WebFetch do Claude Code:
    https://giuseppegurgone.com/claude-webfetch. Define o que o braço
    `webfetch_live` mede.
24. Search-Time Data Contamination (Scale AI): https://arxiv.org/pdf/2508.13180.
25. Caspari et al., memorização de respostas do Stack Overflow:
    https://mlanthology.org/tmlr/2025/caspari2025tmlr-studying/

**Dados (baixar, não PDF):** o WebMainBench, no Hugging Face (subconjunto de 545
páginas citado na frente C), e o model card do Jina v2
(https://huggingface.co/jinaai/jina-reranker-v2-base-multilingual).

As demais fontes de cada frente estão na seção 5 de cada relatório.
