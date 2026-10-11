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

## 2026-10-11: M1/D1 — conectar no IP validado via sni_hostname + override de Host
Contexto: a SPEC §3.1 exige fechar a janela de DNS rebinding conectando no IP já validado, sem uma segunda resolução. O risco é um resolvedor devolver um IP público na validação e um privado na conexão real.
Decisão: o guard resolve o hostname (resolvedor injetável), valida TODOS os A/AAAA e devolve um IP validado. O client (`app/fetch/client.py`, `_build_ip_url`) monta a URL de request com o IP literal no lugar do host (IPv6 entre colchetes), preservando path/query e carregando a porta só quando a URL original a declarou. A partir daí: (a) sobrescreve o header `Host` de volta para o hostname original; (b) define a extensão de request `sni_hostname` = hostname original; (c) mantém `verify=True`.

Peculiaridade httpx/httpcore que um revisor de segurança precisa saber (não óbvia; verificada em httpx 0.28.1 / httpcore 1.0.9): o httpx DERIVA o alvo de conexão e a identidade do host a partir da AUTHORITY da URL do request — ou seja, do host que colocamos na URL, que agora é o IP validado. Isso tem três efeitos encadeados que precisam ser domados juntos:
  1. Conexão: o httpcore conecta em `origin.host`, que é o IP literal da URL. É exatamente o que queremos — zero segunda resolução.
  2. Host/`:authority`: por padrão o httpx preencheria o header `Host` (HTTP/1.1) e o pseudo-header `:authority` (HTTP/2) a partir da authority da URL, o que vazaria o IP literal no lugar do nome e quebraria o virtual hosting do servidor. Por isso sobrescrevemos `Host` explicitamente para o hostname original; em HTTP/1.1 (o caminho atual) o `:authority` não existe e o `Host` manda. Se um marco futuro habilitar HTTP/2, é obrigatório garantir que o `:authority` também seja fixado no hostname (hoje o M1 roda sobre HTTP/1.1, então o override de `Host` basta).
  3. TLS/SNI: sem intervenção, o httpcore usaria a authority (o IP) como `server_hostname` do TLS e a verificação do certificado seria contra o IP — que falharia ou, pior, aceitaria um cert errado. A extensão `sni_hostname` força o `server_hostname` do handshake a ser o nome original, então com `verify=True` o certificado é validado contra o HOSTNAME, não contra o IP.
Alternativas: (a) `follow_redirects=True` do httpx — descartado porque re-resolveria e não passaria cada destino pelo guard; (b) registrar um transporte/resolver alternativo que mapeia host→IP ("register alternative" da SPEC) — não foi necessário, pois a forma com `sni_hostname` é viável nesta versão; fica como fallback se uma versão futura do httpcore mudar o comportamento de derivação da authority ou de SNI.
Consequências: o único ponto de resolução é o guard; `app/fetch/client.py` conecta no IP validado, com `trust_env=False` para proxies de ambiente não reabrirem SSRF, e credenciais `user:pass@` são descartadas porque a netloc é reconstruída só a partir do IP. O teste `test_dns_rebinding_uses_validated_ip` prova, via inspeção do request real no MockTransport, que `request.url.host` == IP validado (e != o IP privado do rebind), que `Host` e `sni_hostname` == hostname original e que o resolvedor é chamado uma única vez. `test_basic_fetch_connects_to_validated_ip` prova o mesmo trio no caminho feliz. A revisão de segurança marcou o comportamento de SNI/TLS como "provável, não confirmado" porque o MockTransport não abre socket real; essa lacuna foi fechada por `tests/unit/fetch/test_client_tls_sni.py` (adicionado em 2026-10-11), que sobe um servidor TLS de loopback com certificado emitido via `trustme` (dep de dev, MIT/Apache-2.0) só para o hostname, conecta no IP literal com `sni_hostname`+`Host`=hostname e `verify` contra o CA fake, e afirma que o servidor viu o SNI = hostname (não o IP) e que o handshake validou (200). Um teste de controle prova que, SEM o SNI, o handshake falha — confirmando que o sucesso vem do mecanismo, não por acaso. Esses dois testes usam `@pytest.mark.allow_network` (opt-out da fixture no-network, restrito a loopback).

## 2026-10-11: M1 — correção do LOW-1 (literal packed com ponto final) e postura de interpretação estrita
Contexto: a revisão de segurança independente (adversarial, SPEC §3.1/§3.2) deu veredito APPROVED sem findings HIGH/MEDIUM, mas apontou o LOW-1: um literal IPv4 em notação packed COM ponto final (`http://2130706433.`, `http://0x7f000001.`) contornava a checagem pré-resolução de literal em `_is_forbidden_ip_literal` — `host.isdigit()` dava `False` por causa do ponto e o ramo dotted era pulado por `host.endswith(".")`. O backstop de validação por IP (camada 2) ainda rejeitava qualquer loopback/privado resolvido, então não havia alcance interno real; o problema era um enfraquecimento da camada 1 (defense-in-depth), que ficava dependente só do comportamento do resolvedor (um resolvedor estilo `inet_aton` poderia expandir `2130706433.` para `127.0.0.1`).
Decisão: adotar a interpretação ESTRITA e fechar a camada 1. Em `validate_url`, logo após baixar o host para minúsculo, remover um único ponto final do hostname (`hostname[:-1]` quando termina em `.` e não é só `.`). A remoção acontece ANTES da checagem de literal e da resolução, então as formas packed com ponto final voltam a ser rejeitadas como `invalid_url` pré-resolução, e de quebra o FQDN legítimo com ponto final (`example.com.`) passa a normalizar para a forma sem ponto (uma única grafia canônica, sem resolução duplicada).
Alternativas: (a) tornar `_is_forbidden_ip_literal` insensível a ponto via `host.rstrip(".")` só dentro da função — descartado por consertar só o sintoma e deixar o resto do pipeline (resolução, netloc normalizada) ainda vendo o ponto; (b) não corrigir, confiando no backstop da camada 2 — descartado porque a postura do projeto para rede é falha fechada e defesa em profundidade, e o custo da correção é trivial. Esta é a mesma postura estrita já aplicada em pontos ambíguos da SPEC no M1: um único IP não global rejeita o host inteiro; falha do fetch do robots é `robots_unavailable` (nunca cacheada como permitido); proxies de ambiente são ignorados (`trust_env=False`).
Consequências: a política de literais fica num único ponto e cobre a grafia com ponto final. Travado por testes em `tests/unit/fetch/test_guard.py`: `test_blocklist` ganhou `http://2130706433.` e `http://0x7f000001.` (=> `invalid_url`); `test_alternate_notation_rejected_before_resolving` ganhou as mesmas duas (resolvedor com zero chamadas, provando rejeição pré-resolução); e o positivo `test_trailing_dot_fqdn_normalized` prova que `http://public.example.com./x` resolve sob `public.example.com` e normaliza sem o ponto. A suíte de guard foi de 25 para 30 casos. Os findings INFO-1 (415 não aplicado no M1) e INFO-2 (3xx sem `Location` devolvido como sucesso) seguem como dívida documentada, sem impacto de segurança no M1.

## 2026-10-11: M1 — validação de IP com falha fechada e desembrulho de IPv6
Contexto: a SPEC §3.1 manda rejeitar se QUALQUER IP resolvido não for global, e `ipaddress.is_global` sozinho não cobre todos os casos de SSRF (ex.: IPv4 mapeado em IPv6, metadata da nuvem, CGNAT).
Decisão: (a) um único IP não global entre os resolvidos rejeita o host inteiro (`blocked_destination`), nunca "escolher o bom"; (b) além de `not is_global`, rejeitar explicitamente loopback, link-local, privado, reservado, multicast, unspecified, `169.254.169.254` e `100.64.0.0/10`; (c) desembrulhar formas IPv6 que embutem IPv4 (mapeado `::ffff:x`, NAT64 `64:ff9b::/96`, 6to4 `2002::/16`) e revalidar o IPv4 embutido; (d) rejeitar literais IPv4 em notação alternativa (decimal, octal, hex, formas curtas/empacotadas) como `invalid_url` ANTES de qualquer resolução.
Alternativas: confiar só em `is_global` — descartado por defesa em profundidade; aceitar literais em notação alternativa e deixar a validação de IP pegar — descartado porque a SPEC pede rejeição explícita e pré-resolução dessas notações.
Consequências: a política fica auditável e em um único lugar (`app/fetch/guard.py`); o teste parametrizado em `test_guard.py` cobre cada URL de aceite do ROADMAP.

## 2026-10-11: M1 — semântica e cache do robots.txt (falha fechada)
Contexto: a SPEC §3.2 define cache de 24h por domínio, respeito a `Disallow` para o UA próprio e `*`, 4xx = permitido e 5xx/timeout = `robots_unavailable`, sem detalhar precedência nem o que fazer quando o próprio fetch do robots é bloqueado.
Decisão: (a) buscar `/robots.txt` pelo MESMO guard; se esse fetch for bloqueado/invalid/timeout/erro de transporte, tratar como `robots_unavailable` (falha fechada, não cacheia como permitido); (b) 4xx => permitir tudo (cacheia regra vazia); 5xx => `robots_unavailable`; (c) usar `urllib.robotparser`, cuja `can_fetch` já dá precedência ao grupo do UA próprio sobre `*`; (d) relógio e fetch injetáveis para TTL determinístico e zero rede nos testes.
Alternativas: implementar o parser próprio — descartado por risco e por o stdlib já ter a precedência correta; cachear falhas como permitido — descartado por reabrir o risco de ignorar robots quando o servidor está instável.
Consequências: `app/fetch/robots.py` depende de `app/fetch/client.py` (fetch guardado) por padrão; o TTL vem de `settings.robots_cache_ttl_seconds` (24h). Testes provam cache (contador de chamadas), expiração, 4xx/5xx e as duas formas de Disallow.

## 2026-10-11: M1 — porta configurável via lista e UA computado
Contexto: a SPEC §3.1 pede portas permitidas configuráveis (80/443 por padrão) e um UA `AgentContextBot/0.1 (+<url de contato>)`.
Decisão: adicionar `allowed_ports: tuple[int, ...]` ao Settings, parseado de uma string CSV do ambiente (ex.: `ALLOWED_PORTS="80,443,8443"`) via `Annotated[..., NoDecode]` + `field_validator` (necessário porque o pydantic-settings tenta decodificar tuplas como JSON por padrão); e um método `Settings.user_agent()` que compõe o UA a partir de `fetcher_contact_url`, mantendo o literal e o contato em sincronia.
Alternativas: campo string única para portas — descartado por exigir parsing espalhado; manter só o literal `fetcher_user_agent` — mantido como campo, mas o client prefere a forma computada para o contato não divergir.
Consequências: `.env.example` ganha `ALLOWED_PORTS` e `ROBOTS_CACHE_TTL_SECONDS`; porta fora da lista => `blocked_destination`.

## 2026-10-11: M1 — fixture no-network escopada ao pacote de fetch
Contexto: a suíte de fetch não pode acessar a internet, então uma fixture autouse bloqueia `socket.create_connection`/`getaddrinfo`/`socket.socket.connect`. Colocada no conftest raiz, ela quebrava os testes M0 do /health, que usam o TestClient do FastAPI sobre socket loopback.
Decisão: mover a fixture autouse para `tests/unit/fetch/conftest.py`, escopando o bloqueio ao pacote SSRF-sensível; o conftest raiz só registra o marcador `allow_network`. Opt-out por `@pytest.mark.allow_network` para eventuais testes com servidor loopback.
Alternativas: manter no raiz e marcar todos os testes M0 com opt-out — descartado por ser frágil e invasivo; relaxar o bloqueio para só `getaddrinfo` — descartado por deixar passar conexões diretas a IP.
Consequências: `uv run pytest` roda a suíte inteira sem rede nos testes de fetch; os testes M0 do /health seguem usando loopback normalmente.
