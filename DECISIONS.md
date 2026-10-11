# DECISIONS

Formato de cada entrada:

```
## AAAA-MM-DD: título curto
Contexto: o problema.
Decisão: o que foi escolhido.
Alternativas: o que foi descartado e por quê.
Consequências: o que muda na SPEC ou no código.
```

## 2026-10-11: escopo inicial do MVP
Contexto: o estudo de viabilidade recomenda provar vantagem em um recorte antes de escalar.
Decisão: documentação técnica pública (HTML e PDF digital), modo sob demanda, sem LLM no caminho de extração e seleção.
Alternativas: crawler, indexação contínua e grafo de conhecimento ficaram para depois do M8.
Consequências: ver "Fora do escopo" em CLAUDE.md.

## 2026-10-11: nome de distribuição 'nina' com código em app/
Contexto: o projeto passou a se chamar NINA, mas a SPEC seção 2 fixa a árvore de pacotes sob app/.
Decisão: o nome da distribuição em pyproject.toml é "nina", enquanto todo o código da aplicação continua no pacote app/ (não há pacote importável 'nina').
Alternativas: renomear app/ para nina/ foi descartado para não divergir da SPEC seção 2 e não reescrever todos os imports previstos.
Consequências: imports usam `app.*`; se um marco futuro precisar empacotar/instalar a distribuição, adicionar [build-system] + mapeamento de pacotes então.

## 2026-10-11: boto3 como cliente S3
Contexto: o armazenamento de blobs (HTML/PDF bruto e IR) fica em S3/MinIO local e, em produção, em S3 gerenciado.
Decisão: usar boto3 como cliente S3 (com boto3-stubs[s3] para o mypy strict), apontando para o endpoint configurável via S3_ENDPOINT_URL.
Alternativas: SDKs específicos de MinIO foram descartados por acoplar o código a um fornecedor; boto3 funciona igual contra MinIO, SeaweedFS e AWS.
Consequências: app/store/blobs.py (M1) e o check de /health usam boto3 (head_bucket) contra o endpoint configurado, sem alteração entre dev e produção.

## 2026-10-11: Postgres via pgvector/pgvector:pg16 (fixado)
Contexto: a busca vetorial do assemble exige a extensão pgvector sobre Postgres 16.
Decisão: usar a imagem oficial pgvector/pgvector:pg16 (tag fixada) como serviço postgres do docker-compose.
Alternativas: instalar pgvector sobre a imagem postgres base foi descartado por adicionar passos de build; a imagem já traz a extensão.
Consequências: docker-compose.yml fixa pgvector/pgvector:pg16; migrações (M0/FEAT-003) podem habilitar a extensão sem instalação manual.

## 2026-10-11: EMBEDDING_DIM=1024 provisório
Contexto: a coluna vetorial e o índice HNSW precisam de uma dimensão fixa, mas o provedor de embeddings só é escolhido no M6.
Decisão: fixar EMBEDDING_DIM=1024 como valor provisório no config/.env.example.
Alternativas: deixar sem default foi descartado por quebrar a inicialização; outros valores dependem do provedor ainda não definido.
Consequências: ao escolher o provedor no M6, revisar EMBEDDING_DIM e migrar o esquema se a dimensão mudar.

## 2026-10-11: object storage local = SeaweedFS (Apache-2.0), não MinIO
Contexto: o plano original usava MinIO (minio/minio) e o cliente mc (minio/mc) como S3 local. Em outubro de 2025 a MinIO parou de publicar imagens community gratuitas no Docker Hub e no quay.io e arquivou os repositórios upstream; confirmei que `docker pull minio/minio:...` falha com "pull access denied" e o quay.io responde 401, o que torna `docker compose up` inviável em máquina limpa. Além disso, MinIO e o mirror community maxio-release são licenciados sob AGPL-3.0, e o CLAUDE.md proíbe GPL/AGPL.
Decisão: usar SeaweedFS (chrislusf/seaweedfs:3.80, Apache-2.0, imagem oficial puxável anonimamente — confirmado com docker pull) como armazenamento S3-compatível local, com `server -s3 -dir=/data` expondo o gateway S3 na porta 8333. A criação do bucket é feita por um container one-shot amazon/aws-cli:2.17.0 (Apache-2.0) via `aws s3 mb` apontando para o endpoint do SeaweedFS, de forma idempotente, substituindo o passo baseado em mc.
Alternativas: (a) manter minio/minio exigindo `docker login` no registry pago da MinIO — descartado porque quebra `docker compose up` em máquina limpa e vai contra o critério de aceite do M0; (b) mirror AGPL maxio-release — descartado por violar a regra de licenças do CLAUDE.md.
Consequências: SeaweedFS no modo dev aceita acesso S3 anônimo (sem arquivo de credenciais estáticas), portanto S3_ACCESS_KEY/S3_SECRET_KEY são ignoradas localmente e só valem contra AWS em produção. boto3 e os contratos S3 (endpoint_url, access_key, secret_key, bucket, region) ficam inalterados; o check de blobs do /health continua via boto3 head_bucket. Isto vale só para dev/local — em produção (M10) o alvo é S3/AWS gerenciado. docker-compose.yml fixa chrislusf/seaweedfs:3.80 e amazon/aws-cli:2.17.0.

## 2026-10-11: estratégia de object storage no CI = docker compose dentro do job
Contexto: o CI (GitHub Actions) precisa de Postgres e de um S3 local para rodar `make migrate` e o teste de integração do /health. A opção padrão para dependências no Actions são os service containers, mas eles não permitem sobrescrever o comando da imagem, e o entrypoint da imagem chrislusf/seaweedfs não sobe o gateway S3 por padrão (precisa de `weed server -s3 ...`). Sem o comando certo o healthcheck do service container nunca fica saudável e o job trava.
Decisão: no CI, subir toda a infraestrutura com `docker compose up` dentro do job (o mesmo docker-compose.yml já verificado na FEAT-002): `docker compose up -d --wait postgres seaweedfs` e depois `docker compose up s3-init` para criar o bucket uma vez. Assim o CI reusa exatamente o comando (`server -s3 -ip.bind=0.0.0.0`), o healthcheck e a criação de bucket (aws-cli) já validados localmente, sem duplicar configuração.
Alternativas: (a) SeaweedFS como service container do Actions — descartado porque o Actions não deixa definir o `command` da imagem e o entrypoint padrão não inicia o S3; (b) rodar o `weed server -s3` via `docker exec` dentro do service container após o start — descartado por ser frágil (depende do service sobreviver a um healthcheck que falha e de descobrir o container pelo ancestor). Postgres funcionaria como service container, mas por coerência toda a stack sobe pelo compose no mesmo passo.
Consequências: o teste de integração do /health depende de Postgres e SeaweedFS alcançáveis no CI; ambos são publicados em localhost pelas portas do compose (5432 e 8333), e as variáveis de ambiente do job espelham o Settings/.env.example 1:1. O passo final do job faz `docker compose down -v` com `if: always()`.
