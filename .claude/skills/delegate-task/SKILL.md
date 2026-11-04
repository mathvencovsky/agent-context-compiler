---
name: delegate-task
description: Run a delegated NINA task end to end. nina-worker does the work, nina-critic reviews it in a fresh context, and the task leaves a brief, an evidence table, the review and a progress note in .agents/tasks/<slug>/, so the result can be inspected and resumed in another session. Use when the human hands off a task, or asks to resume one from its progress note.
argument-hint: "[task, or the slug of a task to resume]"
---

Task: $ARGUMENTS

You are the main session. You coordinate; `nina-worker` does the work and
`nina-critic` reviews it. Every step leaves a file or a result you can show.
The files for a task live in `.agents/tasks/<slug>/`, outside git
(`.gitignore`).

## 0. Start or resume

If the task names an existing `.agents/tasks/<slug>/`, resume it:

1. Read its `progress.md`.
2. Open the files and the commits it references, and check that they match
   the note (`git log`, `git status`). Report any mismatch before going on.
3. Continue from its **Próxima ação**.

The folder may be missing while the human pasted a previous report holding
`task.md`, `checks.md` and `progress.md`. A cloud container loses
`.agents/`, so this is the normal way to resume there. In that case:

1. Recreate the folder from the pasted text.
2. Check the commits it names exist (`git fetch` the branch if needed).
3. Resume as above.

Never start over silently. If neither the folder nor pasted files exist for a
named slug, say so and ask the human for the last report.

Otherwise, choose a short slug for the task and create the folder.

## 1. Brief: `task.md`

Write `.agents/tasks/<slug>/task.md` with the template below. Copy the human's
request into it verbatim: the exact words and any pasted document, not a
summary. Every later step reads this file, not a summary of it.

The **Aceite** field must be checkable: a command and its expected output, a
test that goes from red to green, a file that must exist. If you cannot write
it in checkable terms, ask the human with `AskUserQuestion` before going on.
Do not fill the gap with your own guess.

```
# Tarefa: <title>
## Pedido (verbatim)
## Para quem
## Entrega
## Aceite (checável; definido antes de começar)
## Restrições (fora de escopo, custo, áreas congeladas)
## Risco: baixo | alto (e por quê)
```

## 2. Work: `nina-worker`

Launch `nina-worker` and give it:

- the path of `task.md`;
- the request, verbatim;
- this sentence: "You were launched by the `/delegate-task` skill: write your
  evidence table to `checks.md`, update `progress.md` after each stage, and do
  not launch nina-critic, because the main session runs it."

If it comes back with questions, with a plan for approval (`CLAUDE.md`
rule 2), or with a high-stakes decision, relay it to the human and stop there.
Record that stop in `progress.md`.

## 3. Review: `nina-critic`

Launch `nina-critic` and give it:

- the request, verbatim;
- `task.md`;
- the worker's hypothesis, its definition of "verified" and its decision log;
- the commit range;
- the verification commands.

Do not pass the worker's summary as fact. Save the critic's report, unedited,
to `.agents/tasks/<slug>/review.md`.

## 4. Fix

Send the confirmed findings back to the same `nina-worker` with
`SendMessage`, so it keeps its context. It fixes the in-scope findings, reruns
the checks and updates `checks.md`. A fix that changes behavior goes back to
`nina-critic` for that part only. Any finding left unresolved stays visibly
marked in `checks.md`, with what is missing to resolve it.

Check every finding against its evidence before acting on it. A confident
review still needs usable evidence, and the critic can be wrong.

## 5. Check the outcome, not the transcript

What counts is what the task left in the repository, not what the agents said.

- Pick two rows of `checks.md`. Rerun each command, or open its
  `path:line`, and confirm it matches the row.
- Confirm that `progress.md` agrees with `git log` and `git status`.

## 6. Hand back

Reply to the human with:

1. the paths of `task.md`, `checks.md`, `review.md` and `progress.md`;
2. the commits;
3. each acceptance item with `passou`, `falhou` or `não verificado`, and the
   command behind it;
4. the open findings and the decisions that wait for the human;
5. the worker's quiz.

In a cloud session the container, and `.agents/` with it, is discarded. In
that case also paste `task.md`, `checks.md` and `progress.md` in full, so the
next session can recreate the folder (step 0). `review.md` is covered by
item 4.

## File formats

`checks.md`, the evidence table, one row per claim or acceptance item:

```
| Afirmação | Veredito | Evidência | Correção ou o que falta |
|---|---|---|---|
| make check passa | verificado | `make check` → exit 0, "<N> passed" | - |
```

- **Veredito** is one of `verificado`, `incorreto` or `não resolvido`.
- **Evidência** is a command and its output, a `path:line`, or a URL with the
  date it was read.
- A `não resolvido` row names the missing evidence.

`progress.md`, the handoff note for the next session:

```
Tarefa:
Saídas: (paths and commits)
Concluído: (finished stages and the checks they passed)
Decisões: (confirmed choices, and who confirmed them)
Pendências: (missing evidence, blockers, questions for the human)
Próxima ação: (one concrete step)
```

## When the procedure fails

If something keeps failing the same way, such as a check the worker forgets or
a finding the critic repeats, propose a change to this skill or to the agent
files with the exact text. Those files belong to the human, so do not apply
the change without approval.
