# Frente G: o código aberto do Firecrawl (AGPL-3.0) e o que ele ensina ao NINA

Data: 2026-10-28. Código lido: `firecrawl/firecrawl` na `main` em `90fe962ea` (28/out/2026), com o
histórico aprofundado (`git fetch --depth=4000`) para datar as correções de segurança. Também li o
fork MIT do conversor HTML→Markdown (`firecrawl/html-to-markdown`), o `firecrawl-mcp-server`, o
`nodesig` (AGPL), o README e a documentação Python do `pdf-inspector` (MIT) e os registros CVE no
`CVEProject/cvelistV5`.

**Regra de licença:** o Firecrawl e o `nodesig` são AGPL. Este texto só descreve ideias e
heurísticas, com links para os arquivos. Não copia código. Qualquer coisa que o NINA adotar deve
ser escrita do zero (clean-room). As exceções são o `html-to-markdown` (fork MIT de Johannes
Kaufmann) e o `pdf-inspector` (MIT), que podem até virar dependência.

Atalho de links: `FC` = `https://github.com/firecrawl/firecrawl/blob/90fe962ea2033aae3751b33010d93331848c0bcd`.

---

## 0. Resposta direta ao pedido do coordenador: SSRF no navegador e a CVE-2026-32857

### Linha do tempo (datada pelos commits e pelos registros CVE)

| data | o que aconteceu | fonte |
|---|---|---|
| dez/2024 | **CVE-2024-56800** (GHSA-vjp8-2wgg-p734): SSRF por redirecionamento para IP local. Os motores de fetch foram corrigidos, mas os mantenedores declararam o serviço Playwright **"un-patchable"** e mandaram os auto-hospedados configurarem um `PROXY_SERVER` que bloqueasse link-local. | registro CVE-2024-56800, commit `4d1f92f` |
| 2026-03-13 | PR #3143, "harden against SSRF": validação por `context.route("**/*")` (DNS resolvido e cacheado por 30 s, `ipaddr.range() !== "unicast"` bloqueia), `serviceWorkers: 'block'` e `localhost`/`*.localhost` recusados. | `a2de2bc5e` |
| 2026-03-26 | **CVE-2026-32857** (VulnCheck; CVSS 3.1 8.6 / CVSS 4.0 7.8; afeta ≤ 2.8.0): a validação do #3143 valia só para a URL inicial. **Um 3xx para um destino interno era seguido sem revalidação**, porque o `route` do Playwright não vê os saltos de redirecionamento. É exatamente o achado A3/C1 da auditoria do NINA. | `cves/2026/32xxx/CVE-2026-32857.json` |
| 2026-06-25 | PR #3885, "route scrapes through a local egress proxy": todo contexto passa a sair por um **proxy HTTP local** (`proxy-chain` em `127.0.0.1`, porta efêmera). O hook `prepareRequestFunction` do proxy resolve o host e recusa com 403 qualquer endereço não unicast. Esta é a correção real da CVE. | `ada260dbe` |
| 2026-09-30 | PR #4864: no motor `fetch` (undici), recusa IPs literais privados e `localhost` **antes** de despachar, em cada salto. Motivo: com um proxy upstream, a checagem no socket só via o IP do proxy, nunca o do alvo. | `8838973b4` |
| 2026-10-21 | PR #5261 (issue #5256): o proxy local resolvia o nome uma vez para checar e o `proxy-chain` resolvia **de novo** para conectar. Isso abria DNS rebinding. A correção resolve uma vez e entrega um `dnsLookup` "fixado" que só responde com os endereços já checados. Vieram junto testes unitários, inclusive um caso de rebinding. | `5e74ff37b` |

Curiosidade: o registro da CVE-2026-32857 aponta para o advisory antigo (GHSA-vjp8). Não há GHSA
própria pública para ela.

### Como o playwright-service atual cobre cada canal

Arquivos: `FC/apps/playwright-service-ts/api.ts` e `FC/apps/playwright-service-ts/helpers/ssrf.ts`.

| canal | o que o Firecrawl faz | coberto? |
|---|---|---|
| navegação inicial | `assertSafeTargetUrl` antes de abrir a página, mais o proxy | sim |
| redirecionamento HTTP (3xx) | o `route` não vê; **o proxy vê**, porque cada salto é uma nova conexão ou `CONNECT` | sim, desde #3885 |
| popup (`window.open`, `target=_blank`) | o `route` é do **contexto** (não da página), então cobre popups. O proxy também é por contexto. | sim |
| service worker | `serviceWorkers: 'block'` no `newContext` | sim |
| WebSocket | sai pelo proxy (túnel `CONNECT`), passa pelo `prepareRequest` | sim, pelo proxy |
| loopback (`127.0.0.1`, `localhost`) | O Chromium por padrão **não** usa proxy para loopback. O Playwright força `<-loopback>` na bypass-list (proxyar o loopback também). Verifiquei isso no bundle do Playwright 1.63 do próprio `.venv` do NINA (`shouldProxyLoopback` em `driver/package/lib/coreBundle.js`, para proxy de launch e de contexto). A exceção é a variável de ambiente `PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK` ou um `bypass` que liste loopback. | sim, com essa ressalva |
| DNS rebinding | o proxy resolve uma vez e fixa o IP (desde #5261). Com proxy HTTP, o Chromium não resolve o nome do alvo; quem resolve é o proxy. | sim, desde 21/out/2026 |
| porta | **não restringe** porta. Qualquer porta de um IP público passa. | não |
| WebRTC/UDP (STUN/TURN) | nada; UDP não passa por proxy HTTP | não (risco baixo; não vi teste) |
| proxy upstream (`PROXY_SERVER`) | o proxy local só faz uma pré-checagem e não fixa o IP. O comentário admite que "o upstream deve aplicar sua própria política de egresso". | parcial |
| erro explícito | Quando o `route` bloqueia uma navegação, um `securityState` por contexto guarda a URL, e a falha do `goto` vira `InsecureConnectionError`, respondida como `pageStatusCode: 403` com `pageError`. Quando quem bloqueia é o proxy (caso dos redirecionamentos), o `goto` falha ou mostra a página de erro do proxy. O caminho genérico vira 500, sem dizer que foi bloqueio. | parcial |

Testes do Firecrawl: `helpers/ssrf.spec.ts` tem 6 testes (não unicast, "qualquer endereço interno
reprova", lookup fixado, 403 do proxy, rebinding, upstream sem pin). Em
`apps/api/src/__tests__/snips/v2/scrape.test.ts` há um e2e com um nome que resolve para local
(`localtest.me`). **Não há e2e de redirecionamento para IP privado, de popup nem de service worker.**
O `repro_render.py` da auditoria do NINA cobre mais casos que a suíte deles.

### Recomendações para o proxy de egresso que o NINA está construindo

Todas tocam `app/fetch/render.py` e o novo proxy. `app/` está congelado pela Emenda 15 do PREREG,
mas segurança é exceção óbvia a decidir com o humano.

1. **O proxy deve chamar o guard inteiro, não só a checagem de IP.** Porta, esquema, IP e notação
   alternativa de IP devem valer, e o proxy deve conectar no IP que o guard devolveu.
   `app/fetch/guard.py` já resolve e valida, então o pin sai de graça. O Firecrawl esqueceu a porta.
   Para `CONNECT`, aceitar só as portas que o guard aceita para `https`.
2. **Manter as duas camadas:** `context.route` e `context.route_web_socket` (no contexto, não na
   página; o `render.py:208-209` usa `page.*` hoje) mais `service_workers="block"` no `new_context`,
   além do proxy. Essa é a configuração final do Firecrawl. O `route` dá o erro tipado barato; o
   proxy é a garantia.
3. **Proxy por contexto, com estado de bloqueio por contexto:** o proxy registra "bloqueei host X"
   numa estrutura ligada ao contexto (uma porta efêmera por render, ou credencial por contexto no
   `Proxy-Authorization`). O `render_url` então converte em `BlockedDestination` qualquer `goto` que
   falhe, qualquer `final_url` que comece com `chrome-error://` e qualquer resposta 403 marcada pelo
   proxy. **Nunca extrair a página de erro do proxy como conteúdo.** No Firecrawl, um redirecionamento
   bloqueado pelo proxy vira 500 genérico; num `http://`, a resposta 403 do proxy pode virar o próprio
   "conteúdo".
4. **Afirmar no startup que o loopback é proxiado:** recusar subir se
   `PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK` estiver definida e nunca passar `bypass`.
   Vale um teste com um redirecionamento para `127.0.0.1` que confirme o bloqueio.
5. **Desligar o UDP que não passa pelo proxy:** argumento de lançamento do Chromium
   `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`. É defesa extra que o Firecrawl não
   tem. Não verifiquei neste ambiente; testar.
6. **Fechar a janela de rebinding documentada:** com o proxy fixando o IP, a "janela de DNS
   rebinding no navegador" (DECISIONS 2026-10-11) deixa de existir no caminho do navegador. Atualizar
   DECISIONS e SPEC §3.4 quando o proxy entrar.
7. **Sem proxy upstream sem pin:** se um dia o NINA usar um proxy de saída, o guard tem de rodar antes
   do despacho (lição do PR #4864), e o egresso do contêiner tem de ser restrito (o M11 já prevê).
8. **Testes:** portar para a suíte os quatro cenários do `repro_render.py` (redirecionamento da
   entrada, redirecionamento de sub-requisição, popup, service worker). Somar os testes unitários
   que o Firecrawl tem e o NINA ainda não: "um A público e um A privado reprova", "o proxy não resolve
   de novo no connect" e "`CONNECT` travado expira".

Força da evidência: código-fonte primário e registro CVE lidos na íntegra. A página do advisory na
VulnCheck e a do GitHub não abriram aqui (ver §5).

---

## 1. Lições acionáveis para o NINA (em ordem de impacto esperado)

### L1. Proxy de egresso no navegador (ver §0)
Impacto: alto (segurança). Custo: muda `app/`. Evidência: código primário, CVE e commits.

### L2. Detectar PDF pelos bytes, não só pelo Content-Type
- **O que a fonte mostra:** `FC/apps/api/src/scraper/scrapeURL/engines/utils/specialtyHandler.ts`
  diz que "servidores rotulam errado com frequência" (PDF como `application/octet-stream`,
  `image/jp2`; Office como `application/x-download`). Por isso o Firecrawl compara os primeiros
  bytes com assinaturas: `%PDF-`, ZIP `PK\x03\x04` (docx/xlsx), OLE `D0 CF 11 E0…` (doc/xls).
- **No NINA:** `app/api/documents.py:86,202` só trata como PDF se o Content-Type for exatamente
  `application/pdf`. Qualquer outro tipo fora da lista HTML gera `UnsupportedContentType`. PDFs de
  documentação servidos de S3, GitHub releases ou CDNs com `binary/octet-stream` falham.
- **Mudança:** se o tipo não for HTML/PDF, ou for `octet-stream`/`x-pdf`/`x-download`, olhar os
  primeiros 5 bytes. Com `%PDF-`, seguir para `extract_pdf` e emitir o warning
  `content_type_mismatch`. Teste com fixture.
- **Custo:** pequeno, em `app/` (congelado). **Evidência:** código primário.

### L3. Preservar fragmentos de rota (`#/` e `#!/`) na normalização
- **O que a fonte mostra:** `normalizeURLForIndex` (`FC/apps/api/src/services/index.ts:175`) tira o
  fragmento, **exceto** quando ele começa com `#/` ou `#!/` (rotas de SPA com hash).
- **No NINA:** `app/fetch/guard.py:228` "strip fragment" sempre. Sites de documentação em Docsify,
  em VuePress/Angular antigos com hash routing e várias wikis usam `…/#/guide/install`. Todas as
  páginas de um site assim colapsam no mesmo `document_id`, e o render mostra só a página inicial.
  O resultado é conteúdo errado com proveniência que parece certa.
- **Mudança:** manter o fragmento quando ele começar com `#/` ou `#!/`, tanto no `normalized_url`
  quanto no `document_id`. Forçar render nesses casos (o HTTP nunca traz a rota). O guard continua
  validando só o host.
- **Custo:** pequeno, em `app/`. **Evidência:** código primário. A incidência no corpus do M10 não
  foi medida e dá para medir de graça com um grep por `#/` nas URLs das tarefas.

### L4. Dois bugs concretos de bloco de código (verificados rodando as funções do NINA)
- **O que a fonte mostra:** o plugin `RobustCodeBlock` do fork MIT
  (`github.com/firecrawl/html-to-markdown/blob/main/plugin/robust_code_block.go`) ignora qualquer nó
  com classe contendo `gutter` ou `line-numbers`, pega a linguagem só de `language-*`/`lang-*` (no
  `<code>`, depois no `<pre>`) e quebra linha depois de `br`/`div`/`p`/`li`/`tr`. Foi criado em
  fev/2026 (`d5aaf05`) depois de `25e9840`, "extract content from div elements inside code blocks".
- **No NINA, medido agora:**
  - `_detect_language` (`app/extract/html.py:256-268`) aceita o prefixo `hljs-`, mas `hljs-keyword`
    e `hljs-number` são **classes de token** do highlight.js. Para
    `<code class="hljs"><span class="hljs-keyword">…`, a função devolve `'keyword'` como linguagem.
  - `_raw_code` (`html.py:271`) não remove numeração de linha **inline** do Pygments
    (`<span class="linenos">1</span>`). Saída medida: `'1print(1)\n2print(2)'`. O código que o
    agente copia vem corrompido. A forma em tabela (`highlighttable`) já é tratada; a inline não.
- **Mudança:** tirar `hljs-` dos prefixos (ou aceitar só quando o resto não for um token conhecido)
  e excluir de `_raw_code` os descendentes com classe `linenos`, `lineno`, `line-numbers`, `gutter`
  ou com `aria-hidden="true"`/`user-select:none` que só contenham dígitos. Somar fixtures do
  Pygments `linenos=inline`, do Prism line-numbers e do highlight.js sem classe de linguagem.
- **Custo:** pequeno, em `app/`. **Evidência:** bug reproduzido localmente; heurística primária
  (MIT).

### L5. Servir a versão antiga quando a origem falha (stale-if-error) e lembrar páginas vazias
- **O que a fonte mostra:** no cache do Firecrawl
  (`FC/apps/api/src/scraper/scrapeURL/engines/index/index.ts:252,500-509`), com
  `errorCountToRegister = 3`, entrega-se a última resposta 2xx até que **três** respostas de erro
  seguidas sejam as mais novas. Também há um cache negativo para "confirmado vazio" (`setCachedNegative`).
  O `maxAge` é padrão de 2 dias e pode variar por domínio ("dynamic_cached/db").
- **No NINA:** `app/api/documents.py:375-405`. Vencida a TTL de 24 h, um timeout ou 5xx da origem
  vira erro mesmo com uma versão boa guardada. Para o agente isso é pior que um texto de ontem com
  aviso.
- **Mudança:** se `default_fetch` falhar (rede, 5xx, 429) e existir `current`, devolver `current`
  com o warning `served_stale` (idade em horas e motivo). Nunca fazer isso para 404/410, que devem
  invalidar.
- **Custo:** pequeno, em `app/`. **Evidência:** código primário. O efeito no M10 não foi medido.

### L6. Lembrar por domínio se o render compensa (ideia do "engpicker", sem LLM)
- **O que a fonte mostra:** `FC/apps/api/native/src/engpicker.rs` e `FC/apps/api/src/lib/engpicker.ts`.
  Um job offline raspa até 100 URLs indexadas de um domínio com cliente HTTP e com Chrome e compara
  o Markdown pela similaridade de Levenshtein normalizada. O veredito é por domínio:
  `TlsClientOk` se ≥ 70% das URLs comparáveis têm similaridade ≥ 0,85; `Uncertain` se > 50% dos
  renders falharam; senão `ChromeCdpRequired`. Eles usam um LLM (gpt-4o-mini) só para julgar "o
  scrape deu certo?". O NINA não pode, nem precisa.
- **No NINA:** `should_render` (`app/fetch/detect.py`) decide página a página. No caminho de render
  o NINA paga duas extrações (HTTP, depois navegador) e a cota de render por chave. Nada é lembrado
  entre páginas do mesmo site.
- **Mudança (determinística):** ao renderizar, guardar por host a razão entre os caracteres
  extraídos via HTTP e via navegador. Depois de N ≥ 5 amostras, se o render ganhar sempre (razão
  < 0,2), renderizar direto, sem a primeira extração. Se nunca ganhar (similaridade ≥ 0,85), parar
  de renderizar páginas daquele host que só "parecem SPA". Expor isso como hint de diagnóstico.
- **Custo:** médio, em `app/`. Mede-se de graça no corpus guardado. **Evidência:** código
  primário; os limiares são do Firecrawl e não foram calibrados para o NINA.

### L7. Boilerplate entre páginas do mesmo site (ideia "OMCE")
- **O que a fonte mostra:** com `onlyMainContent` e o experimento `__experimental_omce`
  (`FC/apps/api/native/src/html.rs:459-501`), o Firecrawl busca no índice as "assinaturas" de nós
  vistas no domínio (janela de 2 dias) e remove os nós da página com assinatura igual. A assinatura
  (`nodesig`, AGPL) é um hash recursivo de tag, id/classe opcionais e texto opcional, ignorando
  subárvores com pouco texto. Em resumo: blocos que se repetem em muitas páginas do mesmo site são
  chrome. A geração das assinaturas é fechada (worker do índice).
- **No NINA:** o `assemble` recebe até 20 URLs, muitas vezes do mesmo site. Banners de versão,
  "Edit this page", rodapés de licença e caixas de "Note: this is the docs for v2" escapam da lista
  `_CHROME_CLASS_TOKENS` e viram fragmentos repetidos. A seleção com redundância
  (`app/retrieve/select.py`) atenua, mas gasta candidatos do rerank.
- **Mudança clean-room:** no indexador ou na hora do `assemble`, contar o hash do texto normalizado
  de cada bloco por host entre as versões guardadas. Blocos presentes em ≥ K páginas distintas do
  host (K = 3, digamos) e curtos (< 300 caracteres) ficam fora do índice lexical/vetorial, mas
  continuam no IR por proveniência. Determinístico e testável.
- **Custo:** médio, em `app/` (indexador). **Evidência:** código primário; a eficácia do Firecrawl
  não é publicada (experimento sem números).

### L8. `pdf-inspector` (MIT) como comparador, ou substituto, do pdfplumber
- **O que a fonte mostra:** o Firecrawl extrai PDFs com texto localmente pelo crate `pdf-inspector`
  1.21 (`FC/apps/api/native/src/pdf.rs`), que é MIT e tem wheel no PyPI (`pdf-inspector` 1.25.2,
  PyO3). Ele classifica `TextBased`/`Scanned`/`ImageBased`/`Mixed` em 10–50 ms olhando os operadores
  `Tj`/`TJ` contra `Do` em 8 páginas de amostra. Devolve `pages_needing_ocr` por página,
  `confidence`, `is_complex` (layout), **marca encodings de fonte quebrados** (o caso `(cid:123)`) e
  detecta tabelas por retângulos e por alinhamento. O README cita o opendataloader-bench (200 PDFs):
  overall 0,875 e tabelas TEDS 0,814, contra pymupdf4llm 0,735/0,401 e markitdown 0,589/0,273, com
  os artefatos num branch público. Sobre o pdfplumber o README não diz nada. O Firecrawl só serve o
  resultado do Rust quando ele é "elegível"; senão faz shadow-compare e cai para MinerU/OCR.
- **No NINA:** `app/extract/pdf.py` usa pdfplumber. Só há o warning de documento inteiro
  `low_text_content` (`pdf.py:509`), nada por página, e `Metadata.title=None` sempre (`pdf.py:521`),
  mesmo quando o PDF tem `/Title`.
- **Mudança:** (a) zero custo: rodar o `bench` de retenção do NINA com pdfplumber e pdf-inspector
  nos PDFs do corpus, fora de `app/`. (b) Em `app/`, somar um warning `pages_without_text` com a
  lista de páginas sem camada de texto (o NINA não faz OCR, então tem de dizer onde está o buraco),
  um warning de encoding quebrado e o título pelo dicionário `/Info`.
- **Custo:** (a) zero; (b) pequeno, em `app/`. Checar a licença das dependências transitivas
  (lopdf é MIT). **Evidência:** README do fornecedor com harness público; não reproduzi.

### L9. Respeitar `<base href>` ao resolver links
- **O que a fonte mostra:** `_extract_base_href_from_document` (`FC/apps/api/native/src/html.rs:20`)
  resolve o `<base href>` (relativo à página) e o usa para links e imagens. Há 7 testes só disso em
  `FC/apps/api/src/lib/__tests__/html-transformer.test.ts:99-190`, incluindo vários `<base>` (vale o
  primeiro) e `base` malformado (cai para a URL da página). Houve três commits de correção em julho
  de 2025.
- **No NINA:** `_collect_links` (`app/extract/html.py:236-245`) usa só `source.final_url`.
  Javadoc, Doxygen e muitos geradores estáticos emitem `<base>`, e os links dos fragmentos saem
  errados.
- **Mudança:** calcular a base efetiva uma vez (o primeiro `base[href]`, resolvido contra
  `final_url`; se falhar, `final_url`) e passá-la ao `_Walker`.
- **Custo:** mínimo, em `app/`. **Evidência:** código primário.

### L10. Itens menores de limpeza
Custo pequeno, em `app/`, se adotados.
- Remover o link "Skip to content" (o NINA já cobre por rótulo de texto) e escapar quebras de linha
  dentro do texto de links no Markdown. O Firecrawl faz isso em `post_process_markdown`,
  `html.rs:960-1041`. No NINA, conferir em `to_markdown.py` que um link com `\n` no texto não quebra
  a renderização.
- Seletores de chrome que o Firecrawl remove e o NINA não: `.cookie`/`#cookie`, `.modal`,
  `.popup`, `.overlay`, `.lang-selector`/`#language-selector`, `.social`/`.share`, `.ad`/`.ads`. Isso
  importa no caminho do **navegador**, onde banners de consentimento aparecem no DOM. Sugestão:
  somar ao `_CHROME_CLASS_TOKENS` só os tokens inteiros, como o NINA já faz. **Não** copiar a remoção
  cega de `header`, `.top`, `.side` e `.menu` (ver §3).
- Imagens: o NINA descarta imagens; se um dia guardar o `src`, usar o maior candidato do `srcset`,
  com `data-srcset`/`data-src` para lazy-load (`html.rs:528-619`), e trocar `data:` base64 por um
  marcador (`removeBase64Images.ts`).

### L11. MCP: `alwaysLoad`, anotações e descrição da ferramenta
- **O que a fonte mostra:** `firecrawl-mcp-server/src/server.ts:1700-1785`. A ferramenta principal
  `firecrawl_scrape` é registrada com `_meta: {'anthropic/alwaysLoad': true}` (não fica atrás da
  busca de ferramentas do Claude Code) e com `annotations` `readOnlyHint`, `openWorldHint: true` e
  `destructiveHint: false`. A descrição tem três partes: quando usar, quando usar a vizinha, e uma
  frase sobre frescor ("pode servir conteúdo indexado; `maxAge: 0` para buscar ao vivo; uma
  resposta bem-sucedida não confirma que a página ainda é atual"). Há também um formato `query` com
  `mode: directQuote | freeform`.
- **No NINA:** `app/mcp_server/server.py` usa `_meta["anthropic/searchHint"]` e
  `structured_output=False`, sem anotações nem `alwaysLoad`.
- **Mudança:** marcar `assemble_context` como `alwaysLoad` (é a ferramenta que o agente deve usar
  sempre), somar `readOnlyHint: true` e `openWorldHint: true` às três ferramentas e uma frase de
  frescor na descrição ("conteúdo pode vir do cache de até 24 h; `force_refresh` para ao vivo").
  Antes do M10, medir com o harness de seleção de ferramenta, porque a descrição faz parte do braço.
- **Custo:** zero ou pequeno (MCP), mas mexe no braço `nina` do M10. Coordenar com a Emenda 15.
  **Evidência:** código primário; nenhuma medição pública.

### L12. Seleção abaixo do pedaço que preserva estrutura (ideia, não urgente)
- **O que a fonte mostra:** `FC/apps/api/src/lib/highlight-spans.ts` e `transformers/query.ts`. O
  modo `directQuote` parte o Markdown em "frases" com tipo (heading/text/code/table) e `blockId`.
  Um gpt-oss-20b afinado escolhe índices de frases, e `assembleAnswer` remonta a resposta **verbatim
  e com estrutura**: uma linha de tabela escolhida puxa o cabeçalho da tabela, linhas de código
  consecutivas voltam a ser um bloco cercado, e frases do mesmo bloco voltam a ser parágrafo. A
  entrada é cortada por tokens em fronteira de linha, com o aviso "a página era longa demais;
  resposta gerada da primeira parte".
- **No NINA:** a seleção é por pedaço (≤ 800 tokens), com cabeçalho repetido nos pedaços de tabela
  (`app/index/chunker.py:155`). A remontagem estrutural do Firecrawl é a mesma ideia levada a nível
  de linha. Uma versão determinística (aparar linhas de tabela ou de lista longa que não casam com a
  pergunta, mantendo cabeçalho e caminho de seções) cortaria tokens nos pedaços tabulares.
- **Custo:** grande, em `app/`, e precisa de benchmark. Fica para depois do M10. **Evidência:**
  código primário; nenhuma avaliação pública do `directQuote`.

---

## 2. Onde o NINA já está igual ou melhor (com evidência)

1. **Conteúdo principal.** O `onlyMainContent` do Firecrawl é **só uma lista negra de seletores**
   (42 seletores mais 13 de "forçar inclusão", 12 deles de um cliente, o Swoogo), sem readability
   nem medida de densidade (`html.rs:335-394,509-526`). O único fallback, de jul/2025 (PR #1759),
   dispara quando o Markdown sai **vazio**. O NINA combina readability, `<main>`/`<article>`/
   `role=main`, o contêiner dominante e um fallback por contagem estrutural e por retenção de 80% do
   texto, com o warning `readability_fallback` (`app/extract/html.py:899-971`). Melhor.
2. **Chrome por token de classe inteiro e `role`.** O NINA casa tokens inteiros e roles ARIA
   (`navigation`, `banner`, `complementary`). O Firecrawl remove `header` em qualquer lugar (inclusive
   o `<header>` dentro do `<article>` que guarda o `<h1>`) e classes genéricas como `.top`, `.side`,
   `.bottom` e `.menu`, mas ignora `role`. Melhor.
3. **Fetch HTTP.** O motor `fetch` do Firecrawl lê o corpo inteiro sem limite
   (`engines/fetch/index.ts:163`, `arrayBuffer()`) e segue até **5000** redirecionamentos
   (`safeFetch.ts:90`). A checagem de IP acontece **depois** do connect (evento `connect` lendo
   `socket.remoteAddress`). O NINA limita o corpo a 15 MB em streaming, faz no máximo 5 saltos
   manuais revalidados pelo guard e conecta no IP validado (`app/fetch/client.py`). Melhor.
4. **robots.txt.** No Firecrawl a checagem no `/scrape` é **opcional** por time
   (`shouldCheckRobots.ts`: só com o flag `checkRobotsOnScrape` ou com o safe-mode), e falha ao baixar
   o robots libera o scrape. O NINA sempre checa. Melhor, e coerente com a regra inegociável.
5. **Status HTTP.** O Firecrawl considera sucesso qualquer status fora de 2xx, desde que algo tenha
   sido extraído. Uma página de erro 404/500 vem com `success: true` e `metadata.statusCode`, e o
   próprio comentário diz "what to do when status code is bad is tough" (`scrapeURL/index.ts:799`).
   O NINA recusa o que não é 2xx com `UpstreamStatus` (`documents.py:400`). Melhor para agentes.
6. **Avisos.** O Firecrawl tem **uma string** `warning` concatenada. O NINA tem warnings com `code`,
   `message` e `block_id`. Melhor.
7. **Recursos no navegador.** O Firecrawl bloqueia anúncios por substring de domínio (13 domínios) e
   mídia só sob `BLOCK_MEDIA`. O NINA aborta image/media/font por tipo de recurso. A espera do
   Firecrawl é `load` mais um `wait_after_load` opcional, enquanto o NINA usa `domcontentloaded` e
   depois `networkidle` com teto, mais adequado a SPAs.
8. **Tags que a página não forja.** O Firecrawl gera um nome de tag aleatório por chamada no guard de
   injeção. O NINA escapa todo `<` de tag NINA no texto (`app/retrieve/render.py:47-56`). Equivalente
   ou melhor, porque não depende do modelo conhecer o nonce.
9. **Versões e cache.** O NINA tem `content_hash`, dedup e embeddings reaproveitados por texto de
   pedaço (`app/index/indexer.py:57-77`). A "mudança" do Firecrawl compara o **multiconjunto ordenado
   de caracteres** sem espaços (`transformers/diff.ts:117`); uma troca de ordem não conta como mudança.
10. **Testes de extração.** O Firecrawl não tem snapshots (`toMatchSnapshot`: 0 ocorrências). A
    `test-suite/data/scrape.json` tem 23 perguntas sim/não de 2024 julgadas por LLM. O `test-site`
    é um blog Astro usado em e2e. O conversor Go tem 5 casos triviais. O NINA tem syrupy, retenção por
    site e fixtures por bug. Melhor.
11. **SSRF no navegador, nos testes.** A auditoria do NINA reproduziu redirecionamento,
    sub-redirecionamento, popup e service worker; o Firecrawl testa só nome→IP local e rebinding no
    proxy. Quando o proxy do NINA estiver pronto, a cobertura fica maior que a deles.

## 3. Onde o NINA está atrás

1. **SSRF no navegador:** até o proxy entrar, o NINA está onde o Firecrawl estava antes de jun/2026
   (CVE-2026-32857). Com o proxy, fica igual, e melhor se seguir as recomendações 1, 3 e 5 do §0.
2. **Tipos de arquivo:** o NINA não detecta PDF pelos bytes (L2) e não lê DOCX. O DOCX fica fora do
   escopo, mas vale um erro claro.
3. **SPA com hash routing:** fragmentos `#/` são descartados (L3). Bug de proveniência silencioso.
4. **Código:** numeração inline e linguagem errada (L4).
5. **Resiliência do cache:** sem stale-if-error (L5).
6. **Metadados:** o Firecrawl extrai description, og:*, `article:*`, Dublin Core, favicon,
   `og:locale:alternate` e qualquer `meta name/property/itemprop`, com fallback do título por
   og/twitter (`html.rs:144-313`). Devolve `statusCode`, `sourceURL`, `url` final, `contentType`,
   `cacheState` hit/miss, `cachedAt` e `numPages`. O NINA tem title, lang, published/modified e JSON-LD.
   Faltam `description` (barata e útil para o agente escolher entre URLs), `canonical` (o Firecrawl
   também não extrai) e o título de PDF.
7. **Bloqueio anti-bot:** o código aberto do Firecrawl não detecta páginas de desafio por conteúdo.
   Escala para proxy "stealth" quando vê 401/403/429, e o detector real fica no fire-engine fechado.
   Nenhum dos dois detecta "Just a moment…" do Cloudflare com status 200. Lacuna da categoria, não
   só do NINA.
8. **PDF por página:** o NINA não sabe nem conta quais páginas não têm texto (L8).

## 4. Fontes lidas na íntegra

Código (commit `90fe962ea`, 2026-10-28):
- `FC/apps/api/native/src/html.rs` (transformação, metadados, imagens, pós-processamento do Markdown)
- `FC/apps/api/native/src/engpicker.rs`, `FC/apps/api/src/lib/engpicker.ts`
- `FC/apps/api/native/src/pdf.rs`, `FC/apps/api/src/scraper/scrapeURL/engines/pdf/index.ts` (trechos de decisão)
- `FC/apps/api/src/scraper/scrapeURL/lib/removeUnwantedElements.ts`, `lib/extractMetadata.ts`, `lib/promptInjectionGuard.ts`
- `FC/apps/api/src/scraper/scrapeURL/index.ts` (critério de sucesso por motor 700-822, robots 1400-1480, redirecionamento e ameaças 1690-1800)
- `FC/apps/api/src/scraper/scrapeURL/engines/index.ts` (lista de motores, qualidades, fallback)
- `FC/apps/api/src/scraper/scrapeURL/engines/fetch/index.ts`, `engines/utils/safeFetch.ts`, `engines/utils/specialtyHandler.ts`
- `FC/apps/api/src/scraper/scrapeURL/engines/index/index.ts` (cache, maxAge, erros), `FC/apps/api/src/services/index.ts` (normalização, OMCE)
- `FC/apps/api/src/scraper/scrapeURL/transformers/{index,diff,query,removeBase64Images,promptInjectionGuard}.ts`, `FC/apps/api/src/lib/highlight-spans.ts`
- `FC/apps/api/src/lib/html-to-markdown.ts`, `FC/apps/api/src/scraper/scrapeURL/shouldCheckRobots.ts`
- `FC/apps/playwright-service-ts/api.ts`, `helpers/ssrf.ts`, `helpers/ssrf.spec.ts`
- `FC/apps/go-html-to-md-service/{converter.go,handler_test.go}`, `FC/apps/test-suite/data/scrape.json`, `FC/apps/api/src/lib/__tests__/html-transformer.test.ts`
- Commits: `a2de2bc5e` (#3143), `ada260dbe` (#3885), `5e74ff37b` (#5261), `8838973b4` (#4864), `46850d1e8` (#1759), `9cc32c282` (OMCE)
- https://github.com/firecrawl/html-to-markdown (MIT): `plugin/robust_code_block.go` e o log de commits
- https://github.com/firecrawl/nodesig (AGPL): `src/lib.rs`, só a ideia
- https://github.com/firecrawl/firecrawl-mcp-server: `src/server.ts` (scrape, parse, map)
- https://raw.githubusercontent.com/CVEProject/cvelistV5/main/cves/2026/32xxx/CVE-2026-32857.json
- https://raw.githubusercontent.com/CVEProject/cvelistV5/main/cves/2024/56xxx/CVE-2024-56800.json
- https://github.com/firecrawl/pdf-inspector (README, `Cargo.toml`, `docs/python.md`) e https://pypi.org/project/pdf-inspector/
- Playwright 1.63 (no `.venv` do NINA): `driver/package/lib/coreBundle.js`, função `shouldProxyLoopback` e `doCreateNewContext`

## 5. Fontes que não consegui acessar e valem a leitura completa

- **VulnCheck: "Firecrawl Playwright Service SSRF Protection Bypass via Missing Post-Redirect Validation"**
  https://www.vulncheck.com/advisories/firecrawl-playwright-service-ssrf-protection-bypass-via-missing-post-redirect-validation
  Pode ter a prova de conceito e a versão corrigida exata da CVE-2026-32857. A conexão foi recusada aqui.
- **GitHub advisories do Firecrawl:** https://github.com/firecrawl/firecrawl/security/advisories (e
  `/GHSA-vjp8-2wgg-p734`). Lista completa de advisories e o texto do "un-patchable" de 2024. Respondeu
  403 aqui; a API do GitHub não está liberada para este repositório na sessão.
- **Issue #5256 do Firecrawl:** https://github.com/firecrawl/firecrawl/issues/5256. É o relato do
  rebinding no proxy (o commit só a referencia) e pode trazer outros vetores. API bloqueada aqui.
- **opendataloader-bench, branch de resultados do pdf-inspector:**
  https://github.com/firecrawl/opendataloader-bench/tree/abi/pdf-parser-benchmark-results.
  Serve para checar o número do L8 antes de trocar o pdfplumber. Não tentei clonar, para não
  estourar o tempo; dá para clonar.
