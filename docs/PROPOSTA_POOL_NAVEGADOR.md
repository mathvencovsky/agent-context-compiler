# Proposta: navegador de vida longa por processo (rodada 4, frente K, L4)

Status: **proposta, não implementada** (M10b, 2026-10-29). Implementado no
lugar: espera de texto estável, bloqueio de beacons/analytics e no máximo 2
renders por processo (DECISIONS 2026-10-29, eficiência).

## Medição local (contêiner de 4 vCPU, Chromium 1243 do Playwright)

| etapa por render, hoje | mediana |
|---|---|
| `sync_playwright()` (sobe o driver Node do Playwright) | ~556 ms |
| `chromium.launch()` | ~94 ms |
| `new_context()` + `new_page()` | ~58 ms |
| render inteiro de uma página curta e quieta (loopback) | ~1.380 ms |

O custo fixo evitável por render é ~650 ms (driver + lançamento), não o
lançamento sozinho como a pesquisa supunha. Na máquina do humano os números
podem ser outros: medir antes de decidir.

## Por que não foi feito agora

1. **A API síncrona do Playwright é presa à thread que a iniciou** (greenlet +
   loop asyncio por thread). Os handlers do FastAPI rodam em threads do
   threadpool, e o `assemble` agora também em threads próprias. Um navegador
   compartilhado exige uma thread dona (ou um processo) que receba pedidos de
   render por fila e devolva resultados, com cancelamento por prazo.
2. **O watchdog mata o Chromium inteiro** por PID quando o prazo estoura (um
   laço JS ocupado trava `page.content()`). Com processo compartilhado, isso
   derruba os outros renders em andamento.
3. **O proxy de egresso é definido no lançamento** (`--proxy-server`) e o
   resolver dos testes chega pelo construtor do proxy. Um proxy de processo
   precisa receber o resolver por render, ou cada contexto precisa do seu.

## Desenho proposto

- Um *worker* de render por slot (`render_concurrency`, hoje 2): cada um é uma
  thread dona de um `sync_playwright()` e de um Chromium, alimentada por uma
  fila. Um slot = um processo Chromium, então o watchdog continua matando só o
  render que estourou; o slot relança o navegador em seguida.
- Lançamento com as **mesmas flags globais** de hoje: `--host-resolver-rules=
  "MAP * ~NOTFOUND"` (+ `EXCLUDE 127.0.0.1`), `--disable-quic`, WebRTC sem UDP
  fora do proxy, checagem de `PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK`.
- Proxy: o lançamento aponta `--proxy-server` para um proxy "nega tudo"; cada
  render cria o seu `EgressProxy` (barato: uma thread e um socket) e o passa em
  `browser.new_context(proxy=...)`. Um contexto sem proxy próprio cairia no
  nega-tudo (fail closed). **Verificar antes** que o Chromium honra o proxy por
  contexto para todo tipo de conexão (o Playwright implementa isso no Chromium
  com um contexto de rede próprio): rodar `tests/unit/fetch/test_render_egress.py`
  inteiro contra o desenho novo, mais um teste que prove que um contexto sem
  proxy não sai.
- Cada render: `new_context(accept_downloads=False, service_workers="block",
  proxy=...)`, `route` e `route_web_socket` antes da primeira página, como hoje.
  Contextos são incógnitos: cookies, storage e cache HTTP não passam de um
  render para o outro.
- Reciclagem: relançar após N renders (começar com 100) ou T minutos, e sempre
  depois de um erro ou de o watchdog disparar. Sandbox do Chromium ligado.
- Manter o caminho atual ("um navegador por chamada") atrás de uma flag, para
  os testes de segurança e para o modo bench.

## Risco residual a registrar

Um exploit no renderer que sobreviva no processo afetaria os renders seguintes
do mesmo slot até a reciclagem. Mitigação: reciclagem frequente, sandbox
ligado, e o proxy de egresso continua sendo o único caminho de rede.

## Critério de aceite sugerido

`uv run pytest -m browser` inteiro verde (incluindo `test_render_egress.py`),
um teste novo de contexto sem proxy, e a mediana de render de página curta
caindo de ~1,4 s para perto de ~0,7 s nesta máquina, sem mudança no HTML
capturado das páginas de teste.
