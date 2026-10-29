# NINA no dia a dia (custo zero de API)

Para usar o NINA nas suas sessões do Claude Code no seu computador, com modelos
locais gratuitos (Qwen3, Apache 2.0, via llama.cpp, MIT). Nada chama API paga.

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
