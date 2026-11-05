# Harness para tarefas delegadas

Objetivo: uma tarefa entregue ao agente deixa três coisas.

- um resultado que você abre;
- evidência que você inspeciona;
- progresso salvo para continuar amanhã.

Este guia adapta ao NINA a proposta "Make Opus 5.5 Finish Long Tasks: A Reusable Harness Engineering Setup" (beamnxw, 6 out). A proposta usa como exemplo um fluxo de artigos. Aqui o trabalho é código, benchmark e análise.

O método de trabalho dos agentes vem de "Critical thinking when agents do the work" (Addy Osmani, 3 nov). Os agentes fazem o trabalho e a responsabilidade continua sendo do humano.

## As sete camadas no repositório

| Camada | Onde está |
|---|---|
| 1. Fatos estáveis | `CLAUDE.md`, seção "Tarefas delegadas e longas" |
| 2. Procedimento | skill `/delegate-task` (`.claude/skills/delegate-task/SKILL.md`) |
| 3. Fontes externas (MCP) | nenhum conector no projeto; ver abaixo |
| 4. Regras de ação | `.claude/settings.json` (permissões e hook) e `.claude/hooks/paid_calls_guard.py` |
| 5. Revisor com evidência | `.claude/agents/nina-critic.md` (tabela de evidência, esforço `high`) |
| 6. Esforço | sessão principal em `medium`; o crítico em `high` pelo frontmatter |
| 7. O que a execução prova | `/goal`, modelo abaixo; o aceite de cada tarefa fica em `task.md` |

Os dois agentes têm papéis separados:

- **`nina-worker`** faz o trabalho e registra hipótese, aceite, decisões e evidência.
- **`nina-critic`** revisa em contexto novo, só lendo.

O padrão técnico e de segurança continua no `nina-engineer.md`, que os dois leem.

## Antes da primeira execução

Comece na raiz do repositório:

```
claude --effort medium
```

Escolha o modelo com `--model` se quiser. Confira o modelo e o esforço no cabeçalho da sessão.

Depois confira o que foi carregado:

- `/context`: o `CLAUDE.md` está carregado e nada grande ocupa o contexto à toa.
- `/agents`: `nina-worker`, `nina-critic` e `nina-engineer` aparecem.
- `/permissions`: as regras de `.claude/settings.json` estão ativas. Configurações gerenciadas ou locais podem mudar o resultado efetivo.
- `/hooks`: o hook `PreToolUse` de `Bash|PowerShell` aparece.

Se faltar a skill, confira o caminho e o frontmatter. Se faltar um agente, confira o `name` e a `description`.

## Rodar uma tarefa

1. Escreva o pedido com suas palavras: para quem é, o que fica fora, como saber que terminou. Se não conseguir escrever, a ideia ainda não está clara.
2. Formule sua hipótese antes de rodar, nem que seja um palpite ruim.
3. Rode com um objetivo. Por exemplo:

```
/goal Use /delegate-task para <tarefa>. Conclusão exige que .agents/tasks/<slug>/task.md, checks.md, review.md e progress.md existam; que achados confirmados estejam corrigidos ou marcados como não resolvidos em checks.md; que make check tenha rodado com o resultado mostrado; e que os caminhos dos arquivos e os resultados da verificação apareçam na conversa. Pare após 12 turnos se a condição não for atendida e relate o bloqueio.
```

Sobre o `/goal`:

- O limite de turnos é avaliado pelo modelo. Limite rígido de tempo ou gasto exige controle de execução.
- `/goal clear` encerra o objetivo.
- O comando existe no Claude Code 2.1.318, mas a habilitação depende da conta. Se `/goal` não aparecer, use só a skill.

Sem `/goal`, basta `/delegate-task <tarefa>`.

## Ao terminar

Leia na ordem abaixo. A evidência vem antes do resumo.

1. **Os comandos e as saídas**, antes do resumo.
2. **`checks.md`**: escolha duas linhas e confira contra o arquivo ou o comando.
3. **`review.md`**: veja o que o crítico achou e o que ficou não resolvido.
4. **`progress.md`**: confira se bate com `git log` e `git status`.
5. **O quiz**: só faça o merge se acertar todas as perguntas.
6. **Anote uma coisa que nenhum check cobriu.**

## Retomar em outra sessão

Na mesma pasta de trabalho, cole:

```
/delegate-task <slug>
```

Ou, sem a skill:

```
Leia .agents/tasks/<slug>/progress.md e inspecione os arquivos e commits que ele cita.
Continue da próxima ação registrada e atualize a nota de progresso.
```

A sessão deve reconhecer o trabalho salvo e continuar dali. Se recomeçar do zero, falta algo na nota: acrescente a decisão ou o caminho que faltou.

O `.agents/` fica fora do git. Em sessão na nuvem ele some com o contêiner, por isso o relatório final traz `task.md`, `checks.md` e `progress.md` inteiros. Para retomar numa sessão nova, cole esse relatório junto com `/delegate-task <slug>`: a skill recria a pasta a partir do texto e confere os commits citados.

## Regras de ação (camada 4)

**Negado.** Nem a sua aprovação libera, porque `deny` vence `ask` e `allow`:

- ler e editar `.env` e `.env.*`, em qualquer profundidade;
- a exceção é `.env.example`, reaberto com `!.env.example` (negação no estilo gitignore).

**Pede aprovação a cada edição:**

- `bench/results/**`. Os resultados medidos vêm de scripts, mas o `bench/results/m10a/changes.md` é um registro mantido à mão. Por isso a regra pede aprovação em vez de negar.
- `bench/PREREG.md`;
- `CLAUDE.md`;
- `.claude/settings.json`;
- `.claude/agents/**`, `.claude/skills/**` e `.claude/hooks/**`.

**Hook `PreToolUse` em `Bash|PowerShell`.** Pede aprovação quando um comando liga `ALLOW_PAID_CALLS`: na linha de comando, com `export` ou `$env:`, ou editando o `.env` com `sed`. A ferramenta PowerShell entra porque vem ligada por padrão no Windows com Git Bash. O hook roda na forma com `args`, sem shell, como a documentação recomenda para comandos com `${CLAUDE_PROJECT_DIR}`. A trava principal continua no código (`ALLOW_PAID_CALLS`, padrão `false`). O hook é uma segunda camada.

Os testes em `tests/unit/test_claude_hooks.py` conferem o script e também a ligação em `settings.json`. O matcher precisa cobrir as duas ferramentas, e o comando configurado precisa rodar o script e devolver `ask`.

Testes sem risco:

- crie um arquivo qualquer em `bench/results/` e peça ao Claude para editá-lo: deve aparecer um pedido de aprovação; recuse e apague o arquivo;
- peça para ler `.env.production` (pode não existir): deve ser negado; `.env.example` deve abrir normalmente.

Limites conhecidos:

- **Scripts escapam das regras de arquivo.** Elas valem para as ferramentas do Claude e para os comandos de arquivo que ele reconhece no Bash (`cat`, `sed`, `tee`, redirecionamentos). Um script Python ou Node que abre arquivos por conta própria não passa por elas. Para isso existe o sandbox do sistema operacional.
- **O hook não vê tudo.** Ele não enxerga um valor ligado de outro jeito: um script que edita o `.env`, uma variável já exportada no shell ou `uv run --env-file`.
- **Falha do hook não bloqueia.** Se o hook falhar (por exemplo, sem `uv` no PATH), o comando segue, e a trava do código continua valendo. O teste da ligação falha nesse mesmo caso, o que serve de aviso.
- **Âncoras e worktrees.** Regras com `/` se ancoram no diretório de trabalho principal da sessão. Elas protegem o checkout principal, não as cópias em `.claude/worktrees/` quando a sessão roda na raiz. As regras sem barra (`.env`, `.env.*`) casam em qualquer profundidade abaixo do diretório atual, então cobrem os worktrees. Uma sessão aberta num subdiretório não protege o `.env` da raiz.

**Escrita externa.** Antes de aprovar, identifique o destino e o conteúdo exato. Se o conteúdo mudar, revise de novo. Depois de um timeout, inspecione o destino antes de repetir, porque a primeira tentativa pode ter funcionado.

## Fontes externas (camada 3)

Nenhum conector MCP está configurado no projeto (não há `.mcp.json`), porque as fontes das tarefas do NINA são arquivos locais: SPEC, código, DECISIONS e resultados do benchmark. Se você usa o NINA como ferramenta MCP para ler páginas, essa configuração fica fora do repositório.

Adicione um conector só quando um passo da tarefa precisar dele. Antes de depender dele numa tarefa longa:

- busque uma página conhecida e confira o conteúdo;
- diga à skill qual trecho usar e que ações o conector pode fazer;
- desligue em `/mcp` os servidores sem papel na tarefa.

## Recuperação

- `/rewind` restaura edições de arquivos rastreadas.
- Mudanças feitas pelo shell e a maioria das edições de subagentes não voltam pelo `/rewind`: use o git. (Os dois pontos vêm da proposta; não foram conferidos na documentação.)
- Commits pequenos por etapa (`CLAUDE.md`, regra 5) são o histórico durável.

## Medir em trabalho aceito

Por tarefa, registre:

- `/usage`, incluindo a revisão delegada e as retentativas;
- `/context` para ver o que ocupa o contexto;
- seu tempo de revisão;
- as correções que o resultado precisou.

Um resultado que precisou de muito conserto muda o valor da execução inteira.

Para testar uma mudança de configuração:

- mantenha a mesma tarefa e o mesmo aceite;
- mude um componente por vez;
- compare o resultado salvo e a evidência.

Ganhos anunciados por terceiros valem para os experimentos deles. A economia deste harness só se mede em tarefas concluídas aqui.

## Quando algo dá errado de novo

Se uma correção se repete (um check esquecido, um achado que volta), registre o motivo onde ele vale da próxima vez:

- um teste;
- uma regra de lint;
- um passo novo na skill;
- uma linha no `CLAUDE.md`.

Mudanças nos arquivos de agente, na skill, nos hooks e no `CLAUDE.md` passam pelo humano.
