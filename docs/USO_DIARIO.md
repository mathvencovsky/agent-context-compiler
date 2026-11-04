# NINA no dia a dia (custo zero de API)

Para usar o NINA nas suas sessões do Claude Code no seu computador, com modelos
locais gratuitos (Qwen3, Apache 2.0, via llama.cpp, MIT). Nada chama API paga: sem
`ALLOW_PAID_CALLS=true`, o NINA recusa OpenAI e Jina mesmo com chaves no `.env`.

## Ligar (um comando)

No PowerShell, na pasta do NINA:

```powershell
git pull
uv sync
powershell -ExecutionPolicy Bypass -File scripts\nina-local.ps1 start -Project C:\caminho\do\seu\projeto
```

O script:
1. instala o llama.cpp pelo winget, se faltar;
2. sobe o modelo de embedding (porta 8080) e o reranker (porta 8081), só em
   `127.0.0.1`; na primeira vez baixa uns 1,3 GB;
3. testa se o reranker ordena de verdade (algumas conversões GGUF devolvem notas
   planas); se falhar, para com uma mensagem: mande-a para o Claude;
4. cria o banco `nina_dev`, separado do banco do benchmark (Emenda 17);
5. sobe a API em `http://localhost:8000`;
6. registra o servidor MCP `nina` no Claude Code;
7. com `-Project`, escreve o bloco do NINA no `AGENTS.md` do projeto.

Depois abra o Claude Code: `/mcp` deve listar `nina`.

## Ligar sozinho ao entrar no Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts\nina-local.ps1 autostart
```

Isso cria um atalho na pasta Inicializar do seu usuário, sem pedir administrador. Ao entrar no
Windows, ele abre o Docker Desktop se preciso e sobe o NINA escondido, na branch que estiver
ativa na pasta. O registro fica em `.nina-local\boot.log`. Para desfazer, use `autostart-off`.
`GET /health` mostra a versão em execução (`version`, o commit).

## Ver o uso no painel local

Com o NINA ligado, abra <http://127.0.0.1:8000/painel>. A página se atualiza a cada 5 segundos, sem
gastar limite nem API, e mostra cada chamada: quando, origem (chat ou Claude Code), site, tokens
entregues, tamanho da página, tempo e erros ou recusas. Ela não mostra as leituras que o chat fez sem
o NINA nem o custo da conversa, e a pergunta não é guardada. A origem aparece depois que o app do
Claude reinicia; chamadas anteriores ficam como "Sem origem".

## Contar o uso real por tarefa

```powershell
uv run python -m scripts.nina_usage --tasks tarefas.json --since 2026-11-05
```

Lê o histórico do Claude Code e mostra, entre as tarefas que leram alguma página, quantas chamaram
o NINA sem ser pedido no texto, quantas também leram pelo leitor nativo, quantas tiveram recusa e o
tamanho das páginas. Deixa de fora o desenvolvimento do próprio NINA e as execuções de teste. O
chat do app não entra, porque não grava neste histórico: dele só há o total de `GET /v1/usage`.

## Desligar e conferir

```powershell
powershell -ExecutionPolicy Bypass -File scripts\nina-local.ps1 status
powershell -ExecutionPolicy Bypass -File scripts\nina-local.ps1 stop
```

Os logs ficam em `.nina-local\` (fora do git).

## O que esperar

- O reranker em CPU é lento: um `assemble` pode levar de 10 s a 1 min. Com GPU,
  segundos.
- O embedding de 1024 dimensões é completado com zeros até a coluna de 1536
  (`EMBEDDINGS_PAD_TO_COLUMN`); zeros não mudam a distância de cosseno.
- Achou algo errado (página mal extraída, trecho irrelevante)? Anote a URL e a
  pergunta e mande para o Claude: vira teste.
