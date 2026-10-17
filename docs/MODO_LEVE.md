# Modo leve sem Postgres e S3: opções (C2, 2026-10-17)

Escrito para decisão; nada foi construído. Motivo: instalar e hospedar é o maior
tema das issues livres do Firecrawl (25%) e do Crawl4AI (13%)
(`docs/CONCORRENTES.md`), e a pilha do NINA (Postgres com pgvector, S3,
Playwright) pesa o mesmo. Hoje a primeira instalação baixa ~1,4 GB de imagens
(`docs/ONBOARDING.md`).

## O que o banco e o S3 fazem hoje

| peça | guarda | quem usa |
|---|---|---|
| Postgres `sources`, `document_versions` | versões por hash do conteúdo, cache de 24 h | `resolve`, `assemble` |
| Postgres `chunks` (+ pgvector, HNSW) | pedaços, embeddings, `chunk_id` estáveis | `assemble`, `exclude_chunk_ids` |
| Postgres `api_keys`, `usage_events` | chaves (hash) e uso por chave | autenticação, limites, `/v1/usage` |
| S3 (SeaweedFS em dev) | HTML/PDF bruto e IR de cada versão | `resolve`, `get_document`, indexação |

## Opções

### (a) Pipeline em memória, sem persistência

O que já existe: `bench/runners/ours.py` roda extração, pedaços, busca léxica
(a mesma `overlap_ranks` do produto), busca vetorial em Python, RRF, rerank e
seleção, tudo em memória.

- Forma: um processo único (`nina serve --light`, por exemplo) com as rotas
  `resolve` e `assemble`, cache de páginas em disco local e embeddings
  calculados a cada pedido.
- Perde: cache de embeddings entre processos (cada página nova paga embeddings
  de novo depois de reiniciar), `chunk_id` estável entre reinícios (afeta
  `exclude_chunk_ids`), chaves de API, limites e `/v1/usage`, `get_document`.
- Dependências novas: nenhuma.
- Esforço: pequeno a médio. Risco: duas implementações do pipeline divergirem;
  o produto e o bench já compartilham a busca léxica, e o resto precisaria do
  mesmo cuidado.

### (b) SQLite + sqlite-vec + arquivos locais

- Forma: as mesmas tabelas em SQLite, vetores com `sqlite-vec` (MIT/Apache-2.0)
  e blobs numa pasta local (uma variante da classe `BlobStore`).
- Mantém: cache, versões, `chunk_id` estáveis, chaves e uso.
- Perde: o índice HNSW (o sqlite-vec faz busca exata; com dezenas de URLs por
  pedido isso é rápido) e a coluna `tsv` (já sem uso desde que a busca léxica
  passou para Python).
- Custo: um segundo dialeto no SQLAlchemy e nas migrações, e testes de
  integração rodando nos dois bancos. Esforço médio a grande.

### (c) Postgres sem S3

- Forma: blobs numa pasta local quando `S3_ENDPOINT_URL` estiver vazio.
- Ganho: some o SeaweedFS (181 MB) e o `s3-init` (567 MB); fica só o Postgres.
- Perde: nada de funcionalidade; em produção continua o S3.
- Esforço: pequeno (uma variante da classe `BlobStore` que grava em disco).

## Comparação

| | download | perde funcionalidade | esforço | mexe na SPEC |
|---|---|---|---|---|
| (a) em memória | 0 | sim (cache, ids, chaves, uso) | pequeno–médio | sim, modo novo |
| (b) SQLite | 0 | quase nada | médio–grande | sim, armazenamento |
| (c) sem S3 | −750 MB | nada | pequeno | pouco (armazenamento local em dev) |

## Recomendação, para decisão do humano

(c) primeiro: é o menor passo e tira mais da metade do download sem perder
nada. (a) depois, se a demanda for "experimentar em um minuto" (somado ao
`PROVIDERS=fake`, roda sem Docker e sem chaves). (b) só se houver quem queira
hospedar sem Postgres de verdade.
