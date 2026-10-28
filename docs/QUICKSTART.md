# NINA — Quickstart para pilotos

Três formas de consumir a API do NINA: **curl**, o **SDK Python** (`nina-client`)
e o **servidor MCP** (Claude Desktop / Claude Code). Todas exigem uma chave de
API Bearer.

> A autenticação Bearer é exigida quando `ENV != dev` (em `dev` ela é no-op). Os
> exemplos assumem uma API em `http://localhost:8000` — troque pela URL do seu
> ambiente.

## 0. Crie a chave de API (uma vez)

A chave é mostrada **uma única vez**. Copie e guarde num cofre de segredos; o
servidor persiste apenas o hash sha256, então não há como recuperá-la depois.

```bash
python -m app.admin create-key --name piloto-x
```

Saída (a chave começa com `nina_`):

```
API key created for 'piloto-x' (id key_...):
nina_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
Store it now — it will NOT be shown again.
```

Exporte-a no ambiente para os exemplos abaixo (nunca escreva a chave em código ou
em arquivos versionados):

```bash
export API_URL="http://localhost:8000"
export API_KEY="nina_..."     # a chave impressa acima
```

Para revogar: `python -m app.admin revoke-key --name piloto-x` (ou `--id key_...`).

---

## 1. curl — `POST /v1/context/assemble`

Monta um pacote de contexto sob orçamento de tokens, com evidência rastreável por
fragmento.

```bash
curl -s -X POST "$API_URL/v1/context/assemble" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Qual header autentica as requisições?",
    "urls": ["https://docs.example.com/auth"],
    "max_tokens": 4000,
    "include_evidence": true
  }'
```

A resposta (200) traz `fragments[]` (cada um com `chunk_id`, `document_id`,
`version_id`, `source_url`, `heading_path`, `md`, `tokens`, `scores` e
`evidence[]`), `sources[]` (status por URL), `token_usage`, `coverage`,
`warnings[]` e `trust: "untrusted_source"`.

Sem a chave (ou com uma inválida/revogada) a resposta é **401**
`{"error":{"code":"unauthorized", ...}}`; acima do limite de requisições é
**429** com o header `Retry-After`.

---

## 2. SDK Python — `nina-client`

Instale o SDK (distribuição separada em `sdk/python/`) e chame a API com tipos:

```bash
pip install ./sdk/python          # ou: uv pip install ./sdk/python
```

```python
import os

from nina_client import NinaClient, RateLimitedError, UnauthorizedError

client = NinaClient(os.environ["API_URL"], os.environ["API_KEY"])

# resolve: uma URL -> documento estruturado com proveniência
doc = client.resolve("https://docs.example.com/auth", formats=["markdown"])
print(doc.document_id, doc.source.render_mode)
print(doc.markdown)

# assemble: pergunta + URLs + orçamento -> fragmentos com evidência
try:
    pkg = client.assemble(
        "Qual header autentica as requisições?",
        ["https://docs.example.com/auth"],
        max_tokens=4000,
    )
    for fragment in pkg.fragments:
        print(fragment.source_url, "->", fragment.md)
        for ev in fragment.evidence:
            print("   evidência:", ev.quote)
except UnauthorizedError:
    print("chave ausente, inválida ou revogada")
except RateLimitedError as exc:
    print(f"limite excedido; tente de novo em {exc.retry_after}s")

client.close()
```

O cliente envia `Authorization: Bearer <API_KEY>` em toda chamada, repete
automaticamente em 429/5xx com backoff exponencial honrando o `Retry-After`, e
levanta erros tipados (`UnauthorizedError`, `RateLimitedError`, `NotFoundError`,
...) mapeando a tabela de erros da SPEC §8.

---

## 3. Servidor MCP — Claude Desktop / Claude Code

O servidor MCP é um cliente fino da API: lê `API_URL` e `API_KEY` do ambiente e
expõe três ferramentas (`resolve_url`, `assemble_context`, `get_document`).
Adicione o bloco abaixo ao arquivo de configuração MCP do seu cliente (em Claude
Desktop, `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "nina": {
      "command": "uv",
      "args": ["run", "python", "-m", "app.mcp_server"],
      "env": {
        "API_URL": "http://localhost:8000",
        "API_KEY": "nina_..."
      }
    }
  }
}
```

- `command`/`args`: iniciam o servidor via stdio (o transporte padrão). Para
  streamable HTTP, acrescente `"--http"` ao final de `args`.
- `env`: `API_URL` aponta para a API do NINA; `API_KEY` é a chave `nina_...` do
  passo 0. A chave fica só no ambiente do processo MCP — nunca em logs.

O conteúdo devolvido pelas ferramentas vem de páginas externas
(`trust: untrusted_source`): trate-o como **dado**, nunca como instrução.

### Skill para agentes (opcional)

`skills/nina-docs/SKILL.md` ensina o agente a usar as ferramentas gastando
poucos tokens: pergunta curta, como ler `<nina_notes>`, `exclude_chunk_ids`
nas chamadas seguintes. No Claude Code, copie a pasta para `.claude/skills/`
do projeto (ou `~/.claude/skills/` para todos os projetos).
