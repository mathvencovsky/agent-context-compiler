# Frente K: engenharia de eficiência (latência e custo de fetch, render e extração; compressão determinística do contexto)

Rodada 4, 2026-10-29. Código lido: `main` em `d00da0c`. Arquivos inspecionados: `app/fetch/{client,render,egress_proxy,detect,robots}.py`, `app/api/{documents,assemble}.py`, `app/retrieve/{assemble,select,render}.py`, `app/index/{indexer,tokens}.py`, `app/store/{repo,blobs}.py`, `app/config.py`, `bench/results/m10a/changes.md`, `bench/{anchor_qrels,section_recall,snapshot}.py`, `bench/sites.yaml`.

**Medições locais feitas nesta rodada** (contêiner com 4 vCPU, `.venv` do repo, sem rede para fora, sem instalar nada):

| medida | resultado |
|---|---|
| `httpx.Client(verify=True, trust_env=False)`, a construção que `fetch()` faz a cada chamada | **50,8 ms** mediana, 53,0 ms p95 (30 repetições) |
| a mesma construção com um `SSLContext` já pronto (`verify=ctx`) | 1,1 ms |
| `httpx.create_ssl_context()` sozinho | 27 ms |
| `BlobStore()` (cria um cliente boto3), feito a cada requisição | 9,2 ms |
| `count_tokens(raw_html)` de `tests/fixtures/html/pydantic_changelog.html` (1,24 MB, 550 mil tokens) | **419 ms** |
| o mesmo em `fastapi_first_steps.html` (145 KB, 41 mil tokens) | 24 ms |
| `extract_html` nos mesmos arquivos | 1.994 ms e 193 ms |
| `to_markdown` + `count_tokens(md)` | 74 ms e 3 ms |

O Chromium do Playwright não está instalado neste contêiner. Por isso não medi o custo de lançar o navegador contra o de criar um contexto: isso fica como medição na máquina do humano (L4).

---

## 1. Lições acionáveis para o NINA (ordenadas por impacto esperado)

### L1. Resolver as URLs do `assemble` em paralelo, com limite por host

- **O que as fontes mostram.** O playwright-service do Firecrawl atende páginas em paralelo atrás de um semáforo (`MAX_CONCURRENT_PAGES`, padrão 10; `apps/playwright-service-ts/api.ts:25-27,134,402,538`). O Crawl4AI e o Jina Reader também paralelizam (o Jina mantém páginas pré-aquecidas, ver L4). Nenhum leitor de página atende várias URLs em série.
- **No NINA.** `app/retrieve/assemble.py:201` percorre `req.urls` (até 20) **em série**: `resolve_document`, depois `index_version` (que chama embeddings quando o pedaço é novo), uma URL por vez. Numa chamada fria, a latência é a **soma** dos fetches, renders e lotes de embedding. Com 5 URLs a 1,5 a 3 s cada (faixa típica das medições públicas da L8), a chamada leva de 8 a 15 s; em paralelo levaria o tempo da URL mais lenta. Os handlers já são `def` síncronos no threadpool do FastAPI (DECISIONS 2026-10-11), então um `ThreadPoolExecutor` local cabe sem reescrever nada em async.
- **Mudança.** Em `_resolve_and_index`, usar um `ThreadPoolExecutor(max_workers=settings.assemble_fetch_concurrency)`, com padrão 6. Regras:
  - no máximo 2 requisições simultâneas por host (cortesia; o robots.txt do host é consultado uma vez);
  - no máximo 1 ou 2 renders simultâneos (semáforo de processo, pela memória do Chromium);
  - uma sessão de banco por thread;
  - a ordem de `sources[]` continua sendo a ordem do pedido (determinismo da saída);
  - cada falha continua isolada, como hoje.
- **Custo.** Mudança em `app/`, pequena a média, sem dependência nova. A cota de render por chave (M9) precisa ser cobrada de forma thread-safe.
- **Evidência.** Forte quanto ao mecanismo (código). O ganho é aritmético; precisa ser medido como latência p50/p95 a frio com 1, 5 e 10 URLs.

### L2. Parar de recriar objetos caros a cada fetch ou requisição (inclui um bug: o cache do robots.txt não sobrevive entre requisições)

- **Medido no NINA:**
  1. `app/fetch/client.py` cria um `httpx.Client` novo a cada `fetch()`. Isso custa **~51 ms** só para montar o contexto TLS (tabela acima) e impede reutilizar a conexão (keep-alive) entre a página, o robots.txt e o alternate Markdown do mesmo host. Cada URL fria faz até 3 fetches (robots, página, alternate), ou seja, cerca de 150 ms de CPU e 3 handshakes TCP+TLS.
  2. `provide_robots_checker()` (`app/api/documents.py:128-130`) devolve `RobotsChecker()` **novo a cada requisição**, e o cache vive na instância (`app/fetch/robots.py:70`, `self._cache`). Logo, `robots_cache_ttl_seconds = 86400` só vale dentro de uma mesma chamada: toda requisição HTTP com cache miss busca o robots.txt de novo. Isso contraria a intenção da SPEC, custa um fetch extra por URL fria e é carga desnecessária para os sites.
  3. `provide_blob_store()` cria um cliente boto3 por requisição (9 ms). `provide_session_factory()` cria um `Engine` do SQLAlchemy por requisição (`make_engine` em `app/store/repo.py:24`), então o pool de conexões do Postgres nunca é reaproveitado, e engines descartados só liberam conexões quando o coletor de lixo passa.
- **Mudança:**
  - (a) Um `ssl.SSLContext` de processo (`functools.cache`), passado como `verify=ctx`. Mantém `verify=True` na semântica e economiza ~50 ms por fetch, sem mexer no caminho de conexão auditado.
  - (b) `RobotsChecker`, `BlobStore` e o engine como singletons de processo (`functools.cache` nos `provide_*`, como `get_settings`). Os testes continuam trocando tudo via `dependency_overrides`.
  - (c) Opcional, só depois de (a) e (b): reaproveitar conexões por host dentro de uma chamada. **Cuidado de segurança:** o cliente conecta no IP validado e põe o hostname em `sni_hostname`. O pool do httpcore indexa conexões por `(scheme, host, port)`, onde host é o **IP**. Dois hostnames atrás do mesmo IP de CDN reutilizariam uma conexão TLS aberta com o SNI do outro host (virtual host errado ou 421). Se houver reaproveitamento, a chave tem de ser `(hostname, validated_ip, port)`, isto é, um cliente por hostname. HTTP/2 (exige `h2`, MIT) só compensa depois disso, quando houver vários pedidos simultâneos ao mesmo host. Hoje não.
- **Custo.** (a) e (b) são pequenos, em `app/`. (a) toca o "cliente auditado", então precisa de uma linha em DECISIONS.
- **Evidência.** Medição local direta, e o bug do robots foi verificado no código.

### L3. O cache hit ainda tokeniza o HTML bruto inteiro e baixa o blob bruto do S3

- **No NINA.** Em todo cache hit, `resolve_document` (`app/api/documents.py:446-454`) faz `blobs.get_raw()` e `_build_response()` chama `count_tokens(raw_text)` só para preencher `stats.tokens_raw_html`. O `assemble` chama com `formats=[]` e não usa esse número, mas paga **419 ms** numa página de 1,2 MB (changelogs e referências de API grandes são comuns no corpus) e 24 ms numa página média, além do download do bruto. O mesmo acontece nos ramos 304 e "hash igual". Depois, `index_version` relê o IR, re-fatia e compara assinaturas a cada `assemble` (correto, mas é a segunda leitura do IR).
- **Mudança.**
  - Gravar `tokens_raw_html` e `tokens_markdown` na linha `document_versions`, ou no IR, quando a versão é criada, e lê-los no hit.
  - No `assemble`, não montar Markdown nem baixar o bruto, e passar o IR já lido para o indexador.
  - Isso pede uma migração Alembic de duas colunas inteiras.
- **Custo.** Pequeno, em `app/`, com migração.
- **Evidência.** Medição local direta. É o caminho quente: com cache quente, a maior parte do tempo de `assemble` deveria ser busca e rerank, não tokenização de HTML.

### L4. Navegador de vida longa, contexto novo por render, sem perder o proxy de saída

- **O que as fontes mostram:**
  - **Firecrawl playwright-service** (`api.ts:161-241`): um único `chromium.launch` por processo e um `browser.newContext()` **por requisição**, com `proxy: {server: http://127.0.0.1:<ssrfProxyPort>}` apontando para um proxy SSRF (`proxy-chain`) que sobe uma vez no boot (`api.ts:78-91,543`). Também usa `serviceWorkers: 'block'` e `context.route('**/*')` que valida cada URL e aborta domínios de anúncio. É o mesmo desenho do NINA, só que com o navegador e o proxy reaproveitados.
  - **Jina Reader** (`src/services/puppeteer.ts:771,800-808,1025-1052`): navegador único; cada página nasce num `createBrowserContext()` dedicado e é **pré-aquecida** (fila `__loadedPage`, repõe quando sobra ≤ 1). Ao fim do uso, página e contexto são fechados (`ditchPage`), e nenhuma página vive mais de 5 min.
  - **Crawl4AI** (`browser_manager.py:761-762`; `async_configs.py:1030-1033`): reaproveita contextos por "assinatura de configuração" (menos isolamento que o NINA aceitaria) e recicla o processo do navegador a cada N páginas (`max_pages_before_recycle`, recomendado de 500 a 1000) para recuperar memória vazada.
  - **Documentação do Playwright** (`docs/src/browser-contexts.md`): contextos "são rápidos e baratos de criar e completamente isolados, mesmo rodando num único navegador". O proxy pode ser definido no lançamento ou por contexto (`params.md`, `context-option-proxy`).
- **No NINA.** `render_url` (`app/fetch/render.py:358-433`) sobe **um `EgressProxy` novo, um `sync_playwright()` novo e um `chromium.launch()` novo a cada render**, e fecha tudo no fim. O custo de lançar o Chromium (centenas de ms a alguns segundos, conforme a máquina; não medido aqui) é pago em toda página renderizada.
- **Mudança, mantendo cada garantia da auditoria de 2026-10-28:**
  1. `EgressProxy` e o processo Chromium vivem no processo da API ou num worker, criados uma vez com as **mesmas flags globais**: `--proxy-server` no lançamento (todos os contextos herdam, e nenhum contexto consegue sair sem ele), `--host-resolver-rules="MAP * ~NOTFOUND"`, `--disable-quic`, WebRTC sem UDP fora do proxy, e a checagem de `PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK`.
  2. Cada render ganha `browser.new_context(accept_downloads=False, service_workers="block")`, com `context.route` e `route_web_socket` registrados antes da primeira página, como hoje. Contextos do Playwright são incógnitos: cookies, storage e cache HTTP não passam de um render para outro.
  3. Reciclagem: fechar e relançar o Chromium após N renders (começar com 100; Crawl4AI usa de 500 a 1000) ou T minutos, **e sempre** que o watchdog matar o processo (hoje o watchdog mata o Chromium por PID; com o navegador compartilhado, isso derrubaria renders concorrentes, então renders concorrentes exigem um processo por slot do semáforo ou a aceitação de que o timeout de um derruba os outros, que são refeitos).
  4. O proxy é o ponto de aplicação e não muda: ele valida cada CONNECT com `validate_url` e conecta só no IP validado. O `resolver` injetável dos testes continua chegando pelo construtor. Mantenha o caminho "um navegador por chamada" atrás de uma flag para os testes de segurança existentes e para o modo `bench`.
  - **Risco residual a registrar:** um exploit no renderer que sobreviva no processo afetaria renders seguintes até a reciclagem. Mitigações: reciclagem frequente, sandbox do Chromium ligado (não usar `--no-sandbox`, como faz o Firecrawl) e o proxy continuar sendo o único caminho de rede.
- **Custo.** Mudança média em `app/fetch/render.py`, sem dependência nova. Medição na máquina do humano: tempo de `chromium.launch()` contra `new_context()`, 50 repetições, mediana e p95, além do p50/p95 de render ponta a ponta nas páginas `render: always` do `bench/sites.yaml` (angular.dev, react.dev).
- **Evidência.** Média: o padrão aparece em três produtos abertos e na documentação oficial. O número de ganho não tem fonte pública confiável (a busca só achou uma afirmação sem método de "contexto em milissegundos de um dígito").

### L5. Trocar a espera `networkidle` (até 15 s) por "conteúdo estável" e bloquear analytics

- **O que as fontes mostram:**
  - **Playwright** (`docs/src/api/params.md`): `'networkidle'` é **DISCOURAGED** ("nenhuma conexão de rede por pelo menos 500 ms").
  - **Crawl4AI**: espera padrão `wait_until="domcontentloaded"` (`async_configs.py:1892`); `networkidle` só aparece com timeout de 5 s; e há `avoid_ads` (lista de domínios de anúncio e rastreamento) e `avoid_css`.
  - **Firecrawl**: `waitUntil: 'load'`, mais `wait_after_load` e `check_selector` opcionais; aborta `AD_SERVING_DOMAINS` no `context.route`.
  - **Jina Reader** (`puppeteer.ts:240-268,1002-1013`): injeta um `MutationObserver` que dispara `mutationIdle` depois de **200 ms sem mutações no DOM** a partir do `DOMContentLoaded`, e tira o snapshot nesse sinal, não ao fim da rede.
- **No NINA.** `render.py:385-400` espera `domcontentloaded` e depois `networkidle` com teto de `render_networkidle_timeout_ms = 15000`. Sites de documentação com analytics, long-polling, chat de suporte ou beacons nunca ficam ociosos, e o render paga os 15 s inteiros sem ganhar conteúdo. Hoje o NINA bloqueia só `image`, `media` e `font`.
- **Mudança:**
  1. Depois de `domcontentloaded`, medir a cada 250 ms o tamanho do texto de `main, article, [role=main]` ou, na falta deles, de `body`. Parar quando duas leituras seguidas forem iguais **e** o texto tiver ≥ `render_min_text_chars`, com teto de 5 s. Se o teto vencer, seguir em frente, como já se faz com `networkidle`.
  2. Abortar também os tipos `ping` e `eventsource` e uma lista curta e versionada de hosts de analytics (googletagmanager, google-analytics, segment, hotjar, intercom, doubleclick...). Não bloquear `script`, `xhr` e `fetch`, que trazem o conteúdo das SPAs.
  3. Registrar em `warnings` qual condição encerrou a espera (`stable`, `cap`).
- **Custo.** Pequeno, em `app/fetch/render.py`. Na máquina do humano, comparar nas páginas renderizadas do corpus: latência p50/p95 e igualdade do texto extraído (o `bench/extract_eval` já mede retenção) entre a espera atual e a nova.
- **Evidência.** Média: três implementações abertas convergem e há o aviso oficial do Playwright. O ganho em sites de documentação ainda não foi medido.

### L6. Frescor: revalidação condicional, `max_age_seconds` por chamada e estado de cache visível

- **Quão comuns são os validadores:**
  - **HTTP Archive, Web Almanac 2021, capítulo Caching** (todas as respostas, não só HTML): 70,5% têm `Last-Modified`, 46,5% `ETag`, 41,6% os dois e **24,5% nenhum**. Quando o validador bate, a requisição condicional devolve 304 em **88%** dos casos com `If-None-Match` e em **86 a 94%** com `If-Modified-Since`.
  - **Almanac 2020:** 72 a 73,5% `Last-Modified` e 46 a 48% `ETag`; dos ETags, 34,4% fortes, 9,8% fracos e 2% inválidos; cerca de 1/3 das respostas HTML não é cacheável, e o HTML first-party é o recurso mais "jovem" (41,1% com menos de uma semana).
  - O Almanac não tem capítulo de caching depois de 2021; não achei recorte só de HTML de documentação.
- **Hospedagens de documentação:**
  - **Netlify** e **Cloudflare Pages** servem HTML com `Cache-Control: public, max-age=0, must-revalidate` e `ETag` (doc da Cloudflare "serving pages"; guia e fórum da Netlify, só pelo resumo da busca). Ou seja, o próprio host espera revalidação a cada acesso, e um 304 é barato.
  - **Vercel**: o padrão documentado é `public, max-age=0, must-revalidate`; o ETag em estáticos não foi confirmado.
  - **GitHub Pages**: `max-age=600` e `Last-Modified` (relatos antigos); o ETag não foi confirmado.
  - **Read the Docs**: Cloudflare na frente com `CDN-Cache-Control` e purga por cache tag a cada build de versão (código lido: `readthedocs/proxito/middleware.py:58-81,169-196`). O que chega ao NINA está sempre quase fresco no CDN.
  - Medido daqui (único acesso possível): `raw.githubusercontent.com` devolve `cache-control: max-age=300` e ETag forte; `www.anthropic.com` devolve `s-maxage=150, stale-while-revalidate=31535850`.
  - Os 10 hosts do `bench/sites.yaml` (docs.python.org, developer.mozilla.org, kubernetes.io, react.dev, angular.dev, docs.docker.com, postgresql.org, sqlite.org, rfc-editor.org, flask.palletsprojects.com) **não puderam ser medidos daqui** (proxy bloqueia). Ver o script de medição abaixo.
- **No NINA.** O estado de cache (`documents.py:422-560`) tem TTL fixo de 24 h e, vencido o TTL, faz um **GET completo** sem cabeçalhos condicionais (DECISIONS 2026-10-11: "o cliente auditado não carrega cabeçalhos do chamador"). O NINA **já grava** `etag` e `last_modified` na versão e **já trata** o 304 (passo 6). Falta só enviar `If-None-Match` e `If-Modified-Since`. A deduplicação compara o **sha256 dos bytes brutos**: páginas com nonce de CSP, timestamp de build ou token CSRF no HTML geram um hash novo a cada fetch, portanto uma nova `version_id` para o mesmo conteúdo. Os vetores são reaproveitados por texto (`indexer.py:57-63`), mas a proveniência muda à toa e o S3 cresce.
- **Mudança:**
  1. `fetch(..., conditional=(etag, last_modified))`, com lista fechada: só esses dois cabeçalhos podem ser adicionados (registrar em DECISIONS como emenda ao cliente auditado).
  2. Vencido o TTL, revalidar com GET condicional; um 304 mantém a `version_id` e atualiza `fetched_at`, como o passo 6 já faz.
  3. Novo campo `max_age_seconds` em resolve e assemble (padrão 86400; `0` = revalidar sempre). Com 304 barato, um agente pode pedir 0 a custo baixo. É o contrato do Parallel Extract já anotado na rodada 3 (item 9) e do `maxAge` do Firecrawl. **Contrato novo: precisa de SPEC e aprovação.**
  4. Expor por fonte `cache: "hit" | "revalidated" | "unchanged" | "new" | "stale"`, `age_seconds` e `fetched_at`; no formato conciso, um atributo `fetched` em `<document>`.
  5. Segundo nível de deduplicação: o hash do texto do IR. Se o bruto mudou mas o IR é idêntico, reaproveitar a versão.
  6. Stale-while-revalidate em segundo plano fica para depois: pede um worker, e "filas distribuídas" está fora do escopo. Uma thread local seria aceitável, mas o ganho só existe com tráfego repetido.
  7. O stale-if-error já foi proposto na rodada 3 (G, L5).
- **Script de medição (máquina do humano, custo zero, fora do repositório, com curl).** Para cada URL de `bench/sites.yaml`: um `curl -sI -A "<UA do NINA>"`, guardando ETag, Last-Modified, Cache-Control e Age; depois `curl -s -o /dev/null -w "%{http_code} %{size_download} %{time_total}" -H "If-None-Match: <etag>"`, e o mesmo com `If-Modified-Since`. Repetir o GET completo depois de 1 min e comparar o sha256 do bruto (quantas páginas mudam sem mudar o conteúdo). Respeitar o robots e no máximo 1 requisição por segundo por host. O resultado decide os passos 2 e 5.
- **Custo.** Pequeno a médio, em `app/`. A mudança de contrato pede aprovação.
- **Evidência.** Forte para "validadores são comuns e o 304 funciona" (Almanac, com consultas SQL públicas). Média para o comportamento dos hosts (documentação oficial da Cloudflare e da Vercel; Netlify e GitHub Pages só por fontes secundárias). Falta a medição nos hosts do corpus.

### L7. Modo de orçamento baixo (500 a 1000 tokens): poda determinística dentro dos pedaços escolhidos

- **No próprio NINA** (`bench/results/m10a/changes.md`, metade B, λ = 0): acerto (cobertura de seção ≥ 0,5) de **0,290 com 1k** contra 0,438 com 2k e 0,591 com 4k. Os concorrentes entregam de 140 a 900 tokens (rodada 3). Com pedaços de até 800 tokens e a seleção gulosa por `valor/√tokens` (`select.py`), um orçamento de 1k cabe 1 ou 2 pedaços inteiros, e a maior parte de cada pedaço não é a resposta.
- **O que as fontes mostram:**
  - **OpenProvence** (Yuichi Tateno, 2025; código **e pesos MIT**; checkpoints de 30M a 310M, ModernBERT; o de 30M "roda com folga em CPU"). Avaliação no MLDR em inglês, medida como "Has Answer" por LLM: o baseline sem poda fica em 93,68%. O `xsmall` com limiar 0,05 **mantém 93,68%** e poda 82,18% das passagens positivas e 99,18% das negativas; o `base` mantém 93,68% com 90,05% e 99,62%. Nos conjuntos de QA (en, limiar 0,10), a compressão média vai de 33,8% a 41,7%, com 0,34 a 1,04 s por conjunto.
  - **Provence** (NAVER, ICLR 2025; o modelo é CC-BY-NC, **proibido**, mas os achados valem): a poda é por **sentença**, como rotulagem binária; corta de 50 a 80% conforme o conjunto, com queda desprezível; é robusta a pedaços de 2, 6 ou 10 sentenças; comprime mais quanto maior o contexto; e o número de sentenças relevantes varia de zero a todas. Por isso a regra é um limiar, não um "top-k fixo".
  - **RECOMP** (ICLR 2024; tabela de modelagem de linguagem, só pelo resumo da busca): a seleção de sentenças por **BM25** usa ~33 tokens, com perplexidade 36,63 no GPT-2. O compressor treinado usa 31 tokens (33,67), e o documento top-1 inteiro usa 141 tokens (32,90). O BM25 por sentença perde para o treinado com o mesmo número de tokens, mas entrega ~4× menos tokens que o documento inteiro.
  - **EXIT** (2024): o modelo é Gemma, LLM e portanto proibido, mas fixa um padrão útil: pontuar cada sentença **com o documento inteiro como contexto** e remontar as sentenças mantidas **na ordem original**.
  - **Lucene UnifiedHighlighter** (`PassageScorer.java`, Apache 2.0): pontuação determinística de trechos = BM25 dentro do trecho (k1 = 1,2, b = 0,75, pivô de 87 caracteres, "o tamanho típico de uma sentença em inglês") × bônus de posição `1 + 1/log(pivô + início)`. O próprio código avisa: "esta fórmula é completamente inventada". Serve de ponto de partida, não de evidência.
  - **LlamaIndex** `SentenceWindowNodeParser` (MIT): recupera por sentença e entrega uma janela de ±3 sentenças (`DEFAULT_WINDOW_SIZE = 3`).
  - **Tombros & Sanderson** (SIGIR 1998): resumos com viés de consulta (sentenças pontuadas por termos da consulta, posição e título) melhoram de forma significativa a precisão e a velocidade do julgamento humano de relevância contra "título + primeiras sentenças". Só pelo resumo: o PDF está inacessível.
- **Mudança proposta: `compress: "off" | "sentences"`, ligado por padrão quando `max_tokens ≤ 1500`.** Contrato novo, precisa de SPEC.
  1. Selecionar candidatos como hoje, mas com um orçamento de pool de cerca de 4×B (os pedaços inteiros já rerankeados).
  2. Dentro de cada pedaço, as unidades são os **blocos do IR**. Parágrafos e itens de lista viram sentenças (o splitter de `assemble.py:_SENTENCE_RE` já existe). **Código, tabelas e linhas de assinatura de API são atômicos**: nunca cortar dentro deles.
  3. Pontuação determinística de cada unidade, sem modelo: BM25 dos termos da pergunta, com IDF calculado sobre o pool de candidatos (fórmula do Lucene); bônus para identificadores da pergunta presentes literalmente (`foo_bar`, `--flag`, `Class.method`), já que o NINA tem um detector de pergunta de código (`code_A.md`, recall 0,93); bônus pequeno para a primeira sentença da seção (ideia de `always_select_title` do OpenProvence); e uma janela de ±1 unidade em volta de cada unidade com acerto (sentence window).
  4. Encher o orçamento por pontuação/tokens entre **todos** os pedaços do pool, para que entrem as sentenças de resposta de 3 a 5 pedaços, e não 1 pedaço inteiro. Remontar na ordem `(document_id, ord, posição)` com um marcador de elisão `[…]`. O `heading_path` vai no atributo `section`.
  5. A proveniência não muda: cada unidade é uma substring literal de um bloco, então `evidence` lista os `block_id` mantidos, e a citação continua literal (regra da SPEC §8). Uma nota em `nina_notes` diz que o fragmento foi podado e como pedir o pedaço inteiro (`chunk_id`).
  6. Nível B, só se o nível A não bastar: pontuar as unidades com um modelo encoder fixo, o que cabe na regra "sem LLM" do mesmo jeito que o reranker:
     - o reranker atual (custo de API por unidade, o que é ruim);
     - um Qwen3-Reranker local (Apache 2.0, na máquina do humano);
     - ou o OpenProvence-xsmall (MIT). **Ressalvas:** exige `trust_remote_code` (teria de ser vendorizado e auditado); os dados de treino incluem MS MARCO (licença de dados não comercial; os pesos são MIT, mas a cadeia de origem precisa de decisão humana); e o determinismo exige CPU fp32 e versões fixas.
- **Atenção à métrica.** `bench/section_recall.py` mede a **fração das unidades da seção** presentes no contexto. Podar sentenças irrelevantes **dentro** da seção-chave reduz essa cobertura por construção, mesmo quando a resposta sobrevive. Antes de medir, registrar no PREREG (emenda; o M10a é dev) uma métrica complementar: "alguma unidade-chave com ≥ N palavras presente", mais `token_precision` e IoU, que já existem. A régua é: acerto e precisão por token em 500 e 1000 contra o NINA atual e contra `strong_rag`, metade A para afinar e metade B para confirmar (regra de DECISIONS 2026-10-16).
- **Custo.** O nível A é zero (pools e providers no cache de disco; "Paid calls: 0", como no M10a), mais uma mudança média em `app/retrieve/`. O nível B roda na máquina do humano.
- **Evidência.** Forte de que a poda por sentença com modelo mantém a resposta (Provence, OpenProvence). Fraca a média para a versão puramente lexical (RECOMP: BM25 < treinado). A pergunta "o nível A sobe o acerto@1k de 0,29?" só o dev do NINA responde.

### L8. Publicar métricas de eficiência com harness e separar frio de quente

- **O que se publica por aí (tudo de fornecedor, sem harness neutro):**
  - o Firecrawl diz P95 de 3.387 ms, com o harness ponta a ponta "ainda não publicado";
  - o fastCRW (concorrente do Firecrawl, sobre o conjunto público de 1.000 URLs do Firecrawl, uma passada de 3.000 requisições em 2026-05-08) mostra p50 de 1.914 ms (fastCRW), 2.305 ms (Firecrawl) e 1.916 ms (Crawl4AI);
  - um teste de blog com 10 URLs mostra mediana e p95 de 1,2 e 3,4 s (Jina Reader), 2,1 e 5,2 s (Firecrawl) e 2,8 e 7,1 s (Crawl4AI);
  - uma página do Firecrawl atribui ao Jina "7,9 s de média".
  - Os números divergem de 6× entre fontes. Sem harness, nenhum deles é comparável.
- **No NINA.** O `/v1/usage` já devolve `latency_p50_ms` e `latency_p95_ms` por rota (`app/api/schemas.py:257-311`). O `changes.md` já reporta "coverage per 1k tokens" e a mediana de tempo, e os resultados do M10a são reprodutíveis. Falta: (a) o tempo por etapa na resposta; (b) a separação frio/quente; (c) "tokens por resposta certa".
- **Mudança.**
  - No formato detalhado, `timings_ms: {resolve, render, extract, embed, search, rerank, select, total}` e, por fonte, `cache` e `age_seconds` (L6). No conciso, uma linha opcional em `nina_notes`.
  - No relatório do bench, publicar sempre:
    - p50 e p95 de latência **a frio** (cache vazio, providers reais ou locais) e **a quente** (cache cheio), por URL e por chamada de 1, 5 e 10 URLs;
    - taxa de sucesso e fração renderizada;
    - tokens entregues por tarefa e **tokens por acerto** (tokens totais ÷ tarefas com acerto);
    - cobertura por 1k tokens (já existe);
    - tudo com o harness e o corpus congelado.
  - Isso torna a afirmação "NINA entrega a resposta com X tokens a Y ms" verificável, ao contrário das afirmações acima.
- **Custo.** Pequeno em `app/` e no `bench/`. A medição a frio usa providers locais ou o cache para continuar com custo zero.
- **Evidência.** Média. É prática de publicação, e já está alinhada à rodada 3 (F, avaliação crível).

### L9. Ajustes menores (baixo impacto, registrar e não priorizar)

- **Cache de DNS no guard.** Cada URL fria resolve o mesmo host até 3 vezes (robots, página, alternate) via `SocketResolver`. Um cache de resultado **validado** com TTL ≤ 60 s é seguro, porque a conexão é sempre feita no IP validado (o rebinding não reabre). O ganho é de poucos ms quando o SO já tem cache.
- **HTTP/2.** Não vale agora: precisa de `h2` e de reaproveitamento de conexão por hostname (ver L2c).
- **Brotli e zstd.** Não são necessários; gzip e deflate cobrem as hospedagens de documentação.
- **Timeouts.** `total_timeout = 20 s` e `connect_timeout = 5 s` estão dentro do usual. O teto de render de 60 s pode cair para ~25 s depois da L5.

---

## 2. Onde o NINA já está igual ou melhor

- **Segurança de rede no render.** O proxy de saída valida **cada conexão** (CONNECT) no nível TCP e conecta só no IP validado, com o Chromium impedido de resolver nomes. O Firecrawl tem um desenho parecido (proxy SSRF + `context.route`), mas roda o Chromium com `--no-sandbox` e sem as flags de resolver. O Crawl4AI e o Jina não têm nada equivalente no código lido. O pooling da L4 preserva essa vantagem.
- **Economia de recursos no render.** O NINA já aborta `image`, `media` e `font` no contexto, bloqueia service workers e fecha popups. Isso é igual ou melhor que o padrão do Firecrawl (`BLOCK_MEDIA` desligado por padrão) e do Crawl4AI (`avoid_ads` e `avoid_css` desligados).
- **Limites rígidos.** O NINA tem watchdog que mata o Chromium, teto de DOM e prazo total com timeouts por operação limitados pelo restante (`client.py`). O Jina limita a vida da página a 5 min, mas não o prazo do pedido do mesmo jeito.
- **Reaproveitamento de embeddings por texto e dedup por hash.** Um fetch repetido de conteúdo igual não paga embeddings de novo (`indexer.py:57-80`).
- **Métrica de eficiência honesta.** "Coverage per 1k tokens" acima de `strong_rag` em todos os orçamentos (0,334 contra 0,297 em 1k, metade B), com harness e regra de confirmação. Nenhum concorrente lido publica algo equivalente com harness.
- **Infraestrutura de 304.** O 304 já é tratado, e ETag e Last-Modified já são guardados por versão: a revalidação condicional (L6) é uma mudança pequena.

## 3. Onde o NINA está atrás

- **Latência a frio estrutural:** resolução das URLs em série (L1); Chromium e proxy lançados a cada render (L4); espera de até 15 s por `networkidle` (L5).
- **Custos fixos por requisição:** ~51 ms de TLS por fetch; cache do robots.txt perdido entre requisições (bug); cliente boto3 e engine do SQLAlchemy recriados (L2); tokenização do HTML bruto em todo cache hit, 419 ms numa página grande (L3).
- **Frescor:** GET completo depois de 24 h, sem condicional; nenhum `max_age` por chamada; estado de cache e idade invisíveis para o agente; dedup por bytes brutos sensível a nonces (L6).
- **Orçamento baixo:** acerto@1k de 0,29 contra 0,59 em 4k. Não há poda dentro do pedaço, e os concorrentes entregam 140 a 900 tokens (L7).
- **Métricas publicadas:** não há separação frio/quente, tempo por etapa nem tokens por acerto (L8).

## 4. Fontes lidas na íntegra (URLs)

- HTTP Archive, Web Almanac, capítulo Caching de 2019, 2020 e 2021 (fonte Markdown no GitHub): https://raw.githubusercontent.com/HTTPArchive/almanac.httparchive.org/main/src/content/en/2021/caching.md (e `/2020/caching.md`, `/2019/caching.md`)
- Firecrawl playwright-service: https://raw.githubusercontent.com/firecrawl/firecrawl/main/apps/playwright-service-ts/api.ts
- Jina Reader, serviço Puppeteer: https://raw.githubusercontent.com/jina-ai/reader/main/src/services/puppeteer.ts
- Crawl4AI: https://raw.githubusercontent.com/unclecode/crawl4ai/main/crawl4ai/browser_manager.py, https://raw.githubusercontent.com/unclecode/crawl4ai/main/crawl4ai/async_crawler_strategy.py (trechos de espera e bloqueio), https://raw.githubusercontent.com/unclecode/crawl4ai/main/crawl4ai/async_configs.py (padrões)
- Playwright, documentação: https://raw.githubusercontent.com/microsoft/playwright/main/docs/src/browser-contexts.md, https://raw.githubusercontent.com/microsoft/playwright/main/docs/src/api/params.md (`navigation-wait-until`, `context-option-proxy`)
- Read the Docs, middleware do proxito: https://raw.githubusercontent.com/readthedocs/readthedocs.org/main/readthedocs/proxito/middleware.py
- OpenProvence: https://raw.githubusercontent.com/hotchpotch/open_provence/main/README.md, https://raw.githubusercontent.com/hotchpotch/open_provence/main/docs/eval_reports/open_provence_v1_eval_report.md, https://raw.githubusercontent.com/hotchpotch/open_provence/main/LICENSE
- EXIT, README: https://raw.githubusercontent.com/ThisIsHwang/EXIT/main/README.md
- RECOMP, README (sem tabelas): https://raw.githubusercontent.com/carriex/recomp/main/README.md
- Lucene `PassageScorer`: https://raw.githubusercontent.com/apache/lucene/main/lucene/highlighter/src/java/org/apache/lucene/search/uhighlight/PassageScorer.java
- LlamaIndex `SentenceWindowNodeParser`: https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/node_parser/text/sentence_window.py
- Cabeçalhos medidos diretamente: https://raw.githubusercontent.com/unclecode/crawl4ai/main/README.md, https://www.anthropic.com/
- Só por resumo de busca (não lidos na íntegra): o guia e o fórum da Netlify, a documentação "serving pages" da Cloudflare, a documentação de cache-control da Vercel, as tabelas do RECOMP, a página do Provence, o blog do Zilliz Semantic Highlight (MIT, 0,6B, base bge-reranker-v2-m3), os benchmarks do fastCRW e do Purify e o resumo de Tombros & Sanderson.

## 5. Fontes inacessíveis que valem leitura completa (salvar em PDF)

1. **RECOMP: Improving Retrieval-Augmented LMs with Compression and Selective Augmentation** (Xu, Shi, Choi; ICLR 2024). https://arxiv.org/abs/2310.04408. A tabela de QA (NQ, TriviaQA, HotpotQA) com o baseline **BM25 por sentença** contra o top-5 inteiro, em tokens e EM: é o número mais próximo do nível A da L7.
2. **Provence: efficient and robust context pruning for retrieval-augmented generation** (Chirkova et al., ICLR 2025). https://arxiv.org/abs/2501.16214. As ablações de granularidade (sentença contra token, tamanho do pedaço) e de "zero a todas as sentenças", que fixam as regras da L7. Só os achados: o modelo é proibido.
3. **Advantages of Query Biased Summaries in Information Retrieval** (Tombros & Sanderson, SIGIR 1998). https://marksanderson.org/publications/my_papers/SIGIR98.pdf. As fórmulas exatas de pontuação de sentença (título, posição, termos da consulta), que servem de base clássica e determinística para a L7.
4. **Cloudflare Pages, "Serving Pages"**. https://developers.cloudflare.com/pages/platform/serving-pages/. A lista oficial de cabeçalhos (ETag sempre, `max-age=0, must-revalidate`), para fixar a L6.
5. **Vercel, Cache-Control headers**. https://vercel.com/docs/caching/cache-control-headers. Confirma o comportamento padrão de revalidação e se há ETag em estáticos.
6. **Netlify, "Advanced caching made easy"**. https://developers.netlify.com/guides/advanced-caching-made-easy/. O comportamento atual de ETag e 304 no HTML (as fontes de fórum são de 2019 a 2021).
7. **Modelo OpenProvence xsmall, model card** (dados de treino e licenças por conjunto). https://huggingface.co/hotchpotch/open-provence-reranker-xsmall-v1. Decide se a cadeia de origem (MS MARCO) é aceitável para o nível B da L7.
