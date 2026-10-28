# Auditoria do M10 contra o Agentic Benchmark Checklist (ABC)

Feita em 2026-10-28, antes de qualquer sessão de agente. Checklist: Zhu et al.,
"Establishing Best Practices in Building Rigorous Agentic Benchmarks", NeurIPS
2025 (arXiv 2507.02825), lista oficial em
`github.com/uiuc-kang-lab/agentic-benchmarks/ABC.md`. Os autores aplicaram o
ABC a 10 benchmarks conhecidos: 7 tinham falha de validade do resultado, 7 de
validade da tarefa e todos tinham lacunas de relato. Exemplos: o τ-bench contava
resposta vazia como sucesso, e o SWE-bench Verified tinha testes insuficientes.

Escopo: `bench/PREREG.md`, `bench/m10_agent.py`, `bench/m10_tools.py`,
`bench/judge_external.py`, `bench/label_answers.py`. Os itens de teste unitário,
fuzzing e estado (I.d a I.g) não se aplicam: o M10 julga respostas em texto.

## Resultado

| item ABC | o que pede | situação no M10 | ação |
|---|---|---|---|
| I.a.1, I.a.2 | aceitar respostas equivalentes e com palavras a mais | o juiz é um LLM com a rubrica "mesmo núcleo, mesmo que com outras palavras" | nenhuma |
| I.b.2 | resistir a quem lista todas as respostas possíveis | **lacuna**: a rubrica aceita "uma solução diferente que resolve"; uma resposta que lista cinco soluções pode passar, e braços com mais contexto tendem a listar mais | decisão do humano (proposta A abaixo) |
| I.c.1 | evidência de acurácia, autoconsistência e concordância com humanos | coberto: κ contra 100 rótulos humanos com portão de 0,6, segundo juiz, 10% julgados duas vezes | somado: viés por tamanho da resposta (`bench.agreement.disagreement_by_length`) |
| I.c.2 | resistir a entrada adversarial e a "reward hacking" | **lacuna**: o texto das páginas não é confiável, o agente pode copiá-lo para a resposta, e nada testava se o juiz obedece a uma instrução dentro do candidato | `bench/judge_probe.py` (candidato de injeção) |
| I.h.1 | formato da resposta especificado | coberto: JSON final no prompt de sistema; sem JSON, a mensagem inteira vira a resposta | nenhuma |
| II.1 | versões das ferramentas especificadas | coberto: `claude-agent-sdk` fixado no `uv.lock` (traz o CLI); modelo conferido na mensagem `init` | nenhuma |
| II.2, II.3 | ferramentas acessíveis; erro de API tratado | coberto: `init` confere ferramentas e servidores; falhas de infraestrutura repetidas até 2 vezes e excluídas de todos os braços; mais de 5% invalida a rodada | nenhuma |
| II.4 | sem estado residual entre execuções | sessão em diretório temporário novo, sem memória nem configuração; o banco `nina_m10` guarda índices entre sessões, mas o mesmo corpus congelado gera a mesma versão (dedup por hash), então a saída não muda | nenhuma |
| II.5 | agente isolado do gabarito | coberto: braços congelados só leem o corpus; WebFetch limitado aos domínios oficiais (Emenda 8), sem stackoverflow.com | nenhuma |
| II.6, II.7 | gabarito correto; tarefa resolvível | auditoria humana de 20% com o checklist do Apêndice B; acima de 10% inválidas, valida tudo | nenhuma |
| II.8 | um "oráculo" que resolve tudo | **lacuna**: nunca se mediu se o juiz aceita a própria resposta aceita | `bench/judge_probe.py` (candidato `reference`) |
| II.9 | sem brecha para passar sem fazer a tarefa | **bug corrigido**: o cegamento não mascarava nomes qualificados como `mcp__docs__assemble_context`, porque `\b` não separa palavras ligadas por `_`; o juiz e os avaliadores veriam o braço | `bench/judge_external._ARM_WORDS`, com teste |
| III.3 | proteção contra contaminação | coberto: perguntas de 2023–2024 provavelmente estão no treino, mas a triagem `no_docs` (§2.2) tira as que o modelo já sabe; `holdout` intocado | nenhuma |
| III.9 | medir o impacto de falhas inevitáveis | parcial: Apêndice A mede o piso de ruído (4 de 41 veredictos mudam entre execuções) | nenhuma |
| III.10 | intervalos de confiança | coberto: bootstrap pareado, 10.000 reamostragens | nenhuma |
| III.13 | resultado de um agente trivial | **lacuna**: a triagem cobre "responder de memória", não "não responder nada" | `bench/judge_probe.py` (`empty`, `unavailable`, `restated`) |

## O que foi feito

1. **Cegamento** (`bench/judge_external.py`): uma palavra agora termina em
   qualquer caractere não alfanumérico, `_` incluído. `mcp__docs__assemble_context`
   vira `mcp__docs__[tool]`; `ninax` continua intacto. Os avaliadores humanos
   usam o mesmo `judge_prompt`, então a correção vale para os dois. É conserto de
   harness em `bench/`, permitido pelo PREREG §10, e cumpre a Emenda 5 como ela
   foi escrita ("todo nome que revelaria o braço").
2. **Candidatos de controle** (`bench/judge_probe.py`): para cada tarefa do
   piloto e cada juiz, cinco candidatos com veredicto conhecido: a resposta
   aceita (tem de passar), vazia, "as páginas não têm a informação", a pergunta
   repetida e uma tentativa de injeção com veredicto forjado (todos têm de
   falhar). Saída em `bench/results/m10/judge_probe.{jsonl,md}`. Não compara
   braços e não entra na análise.
3. **Viés por tamanho** (`bench/agreement.py`): discordância juiz × humanos por
   faixa de tamanho da resposta, para a Fase 4.

## Decisões do humano

- **Rodar `bench.judge_probe` no piloto.** Custa 200 chamadas de juiz (20
  tarefas × 5 candidatos × 2 juízes), sem nenhuma sessão de agente. Se um juiz
  aprovar algum candidato de controle, ou reprovar a resposta aceita em mais de
  2 das 20 tarefas, a rubrica ou o juiz precisa mudar antes da rodada principal,
  por emenda.
- **Proposta A, respostas "espingarda" (I.b.2).** Acrescentar à rubrica: "Se o
  candidato oferece várias respostas centrais alternativas e alguma contradiz a
  referência, marque incorreto." Muda o texto congelado do Apêndice B, então só
  entra por emenda, antes de qualquer julgamento. Recomendação: decidir depois
  de ver o `judge_probe`, que não testa esse caso; se preferir medir primeiro,
  conto no piloto quantas respostas listam mais de uma solução.
- **Proposta B, delimitar o candidato.** Envolver o candidato em marcadores
  (`<candidate>…</candidate>`) torna a injeção mais difícil. Também muda o
  Apêndice B; só vale a pena se o candidato de injeção passar em algum juiz.
