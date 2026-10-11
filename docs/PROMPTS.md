# Prompts para o Claude Code

Use uma sessão nova (ou `/clear`) a cada marco. O contexto do projeto vive nos arquivos, não na conversa.

## 1. Primeira sessão (antes do M0)

```
Leia CLAUDE.md, docs/SPEC.md e docs/ROADMAP.md por completo.

Não escreva código ainda. Quero que você:
1. Liste ambiguidades, contradições ou decisões faltando na SPEC e no ROADMAP que possam travar a implementação.
2. Aponte riscos técnicos que eu talvez não tenha visto.
3. Diga quais itens dependem de mim (chaves, contas, escolhas) e em qual marco.

Responda em português. Depois eu decido o que ajustar.
```

## 2. Início de cada marco

Troque `MX` pelo marco atual.

```
Vamos trabalhar no marco MX.

Leia CLAUDE.md, PROGRESS.md, a seção MX de docs/ROADMAP.md e as seções da docs/SPEC.md citadas nela.

Entre em modo de plano e me mostre:
- os arquivos que vai criar ou alterar;
- a abordagem para cada tarefa;
- como vai provar cada critério de aceite (qual teste, qual comando);
- dúvidas ou pontos em que a SPEC não é suficiente.

Não comece a implementar antes da minha aprovação.
```

## 3. Durante o marco (se ele se perder)

```
Pare. Releia a seção MX do ROADMAP e o PROGRESS.md.
Diga em 5 linhas: o que já está pronto, o que falta, e qual critério de aceite ainda não tem teste.
Depois continue pelo item mais importante que falta.
```

## 4. Fechamento do marco

```
Antes de considerar o MX concluído:
1. Rode make check e me mostre o resultado.
2. Para cada critério de aceite do MX, diga qual teste ou comando o comprova e o resultado.
3. Liste o que ficou de fora, simplificações feitas e dívidas técnicas.
4. Atualize PROGRESS.md e, se houver decisões novas, DECISIONS.md.
5. Faça o commit.
```

## 5. Revisão independente (recomendado em M1, M4, M7 e M9)

Rode em uma sessão nova, para que o revisor não tenha visto a implementação sendo feita.

```
Você é um revisor que não participou desta implementação.
Leia CLAUDE.md, docs/SPEC.md e a seção MX do ROADMAP. Depois leia o código alterado no marco MX (git diff <commit-inicial>..HEAD).

Procure, em ordem de prioridade:
1. Violações das regras inegociáveis do CLAUDE.md (segurança de rede, proveniência, falha explícita).
2. Critérios de aceite que parecem cobertos mas não estão (testes fracos, mocks que escondem o problema).
3. Bugs de borda.

Para cada achado: arquivo, linha, cenário concreto de falha e correção sugerida. Não corrija nada, só reporte.
```

Para o M1, acrescente: "Tente ativamente achar um jeito de fazer o fetcher acessar um endereço interno."

## 6. Rascunho de tarefas do benchmark (M2)

```
Rode bench/draft_tasks.py sobre o corpus para gerar até 150 tarefas candidatas, distribuídas entre as categorias.
Regras:
- cada tarefa deve ter resposta verificável no próprio snapshot;
- a quote em expected_evidence deve ser cópia literal do documento;
- prefira perguntas que exigem achar um detalhe específico (parâmetro, header, versão, limite, valor padrão), e não resumos;
- inclua cerca de 10% de perguntas cuja resposta NÃO está nas URLs, com expected: ["INSUFFICIENT"].
Marque tudo como author: ai_draft, validated: false. Eu valido depois com bench/review_tasks.py.
```

## 7. Leitura do resultado do M8

```
Leia o report.md mais recente em bench/results/.
Me responda em português, de forma direta:
1. Em quais categorias o ours vence o naive_rag com IC que não se sobrepõe? Em quais perde?
2. Qual componente (ablação) mais contribui e qual não se paga?
3. Qual o custo por tarefa concluída de cada estratégia?
4. Quais padrões aparecem nas falhas?
Não recomende seguir ou parar; só apresente a evidência.
```
