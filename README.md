# Agent Context Compiler: kit de construção do MVP

Este kit transforma o estudo de viabilidade em instruções executáveis por um agente de código (pensado para o Claude Code).

## Arquivos

| Arquivo | Para quem | Função |
|---|---|---|
| `CLAUDE.md` | Agente | Regras permanentes, lidas automaticamente em toda sessão |
| `docs/SPEC.md` | Agente | Arquitetura, contratos, esquemas e algoritmos |
| `docs/ROADMAP.md` | Agente e você | 11 marcos com tarefas e critérios de aceite |
| `docs/PROMPTS.md` | Você | O que colar no Claude Code em cada etapa |
| `docs/HUMANO.md` | Você | O que só você pode fazer e quando |
| `PROGRESS.md` | Agente | Estado atual, atualizado a cada marco |
| `DECISIONS.md` | Agente e você | Decisões técnicas registradas |

## Como usar

1. Coloque estes arquivos na raiz de um repositório vazio.
2. Leia `docs/HUMANO.md` e comece as entrevistas e a escolha dos sites em paralelo.
3. No Claude Code, rode o prompt 1 de `docs/PROMPTS.md` e ajuste a SPEC com o que ele apontar.
4. Para cada marco: sessão nova, prompt 2, aprovar o plano, deixar implementar, prompt 4 para fechar.
5. Nos marcos M1, M4, M7 e M9, rode também a revisão independente (prompt 5).
6. Não pule o M8. Ele existe para você descobrir cedo se o produto tem vantagem.

## O que esperar

- O código anda rápido; o gargalo provável é humano: validar 100 tarefas do benchmark, revisar cada marco e conseguir pilotos.
- Os marcos mais difíceis para a IA são o M3 (HTML real é bagunçado) e o M7 (seleção e calibração).
- Revise de verdade os testes do M1. Segurança de rede é onde um erro silencioso custa mais.
