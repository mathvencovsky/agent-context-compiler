# Do `git clone` ao primeiro resultado (C1, 2026-10-17)

Medido numa máquina Windows 11 com Docker Desktop: clone novo do `origin/main`,
cache do uv vazio e uma pilha Docker Compose isolada (projeto `nina_c1`, outras
portas). As imagens Docker já estavam baixadas; o tempo de download delas está
estimado pelo tamanho. Nenhuma chamada paga.

## Tempos

| passo | comando | tempo | resultado |
|---|---|---|---|
| 1 | `git clone` | 4 s | ok |
| 2 | `uv sync` (cache vazio) | 39–41 s | **falhou** com o cache num caminho longo; ok com caminho curto (123 pacotes) |
| 3 | `cp .env.example .env` | 0 s | ok |
| 4 | `make up` | 29 s | ok; baixa ~1,4 GB de imagens na primeira vez (Postgres com pgvector 631 MB, aws-cli 567 MB, SeaweedFS 181 MB): ~4 min a 50 Mbit/s |
| 5 | `make migrate` | 5 s | ok |
| 6 | `make dev` até `/health` | 11 s | ok |
| 7 | primeiro `resolve` | 2 s | ok |
| 8 | primeiro `assemble` | 1 s | **antes: 500 opaco** sem chaves; **agora: 503 explicativo**, ou 200 com `PROVIDERS=fake` |

Sem contar o download das imagens, o caminho todo leva cerca de 1,5 minuto de
máquina. O que trava é o que exige decisão de quem instala.

## Tropeços encontrados

1. **O primeiro `assemble` exigia duas chaves pagas (OpenAI e Jina).** Sem
   elas, a resposta era `500 internal server error`, sem dizer o motivo.
   - Corrigido: a falta de chave vira `503 provider_not_configured`, dizendo qual
     variável definir.
   - Corrigido: `PROVIDERS=fake` (só com `ENV=dev`) usa os provedores falsos dos
     testes; o `assemble` funciona sem chave, com o aviso `demo_providers`
     (a ordem dos fragmentos não tem significado nesse modo).
2. **`uv sync` falha no Windows com caminhos longos.** A dependência
   `jstyleson` (via `extruct`) só existe como código-fonte e é compilada na
   instalação; com o cache do uv numa pasta muito funda, o caminho passa de 260
   caracteres. No caminho padrão do uv (`%LOCALAPPDATA%\uv\cache`) não aconteceu.
   Saídas: habilitar caminhos longos no Windows ou definir `UV_CACHE_DIR` curto.
3. **Sem `make` no Windows.** O README já avisa; quem instala precisa traduzir
   cada alvo do `Makefile` à mão.
4. **567 MB só para criar o bucket.** O serviço `s3-init` usa a imagem do
   aws-cli para um único `s3 mb`.

## Cortes propostos (ainda não feitos)

| corte | ganho | custo |
|---|---|---|
| a API cria o bucket na subida, em dev, e o `s3-init` sai do compose | −567 MB de download | pequeno; mexe em `app/store/blobs.py` |
| `scripts/setup.ps1` e `make setup` (sync, `.env`, up, migrate) | um comando em vez de quatro, também no Windows | pequeno |
| nota no README sobre caminhos longos no Windows | evita a falha do passo 2 | nenhum |

## Como repetir

O roteiro está em `docs/ONBOARDING.md` (este arquivo): clone numa pasta nova,
`UV_CACHE_DIR` vazio, e `docker compose -p <nome> up -d --wait` com outras
portas em `.env` (`POSTGRES_PORT`, `S3_ENDPOINT_URL`) para não colidir com a
pilha de desenvolvimento.
