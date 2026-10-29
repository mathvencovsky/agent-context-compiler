# Análise do guia técnico v2.0, da auditoria v2.0 e do pacote de engenharia (2026-10-29)

O humano trouxe três documentos escritos por um revisor externo **sem acesso ao
repositório** (eles mesmos dizem isso): o "Guia Técnico de Qualidade e
Usabilidade" (75 páginas, versões 1.1 a 2.0 acumuladas), o "Adendo de correções
técnicas v2.0" e o "Engineering Pack v2.0" (spec, 4 JSON Schemas, validador
semântico, avaliador de retrieval, backlog de 27 itens, matriz de 15 casos de
SSRF). Foram lidos na íntegra. O valor deles está em cruzar cada recomendação
com o código real: muitas já estão feitas, algumas não se aplicam ao desenho do
NINA, e poucas revelaram lacunas de verdade.

## 1. Lacunas reais encontradas (corrigidas no branch `claude/m10b-provenance`)

| recomendação do guia | o que o NINA fazia | o que mudou |
|---|---|---|
| "Cada fragmento pertence a exatamente um snapshot e a uma versão de parser" (§6.3) | `version_id` era só o hash dos bytes; depois de uma mudança no extrator, uma página com os mesmos bytes continuava servindo a extração antiga para sempre (cache e deduplicação) | versão do extrator persistida e parte da identidade da versão; reextração a partir dos bytes guardados |
| "O corpus/estado do experimento deve ser fixo e registrado" (§9, §10.4) | a Emenda 17 diz que o banco do NINA começa vazio no M10, mas o harness reaproveitava um banco existente | o harness garante o banco vazio numa rodada nova e registra a versão do extrator |
| `request_id` em toda resposta e erro (§11, §13.2) | o `request_id` existia só nos logs | cabeçalho `X-Request-ID` e o id no erro |
| "Falha silenciosa" como métrica (§10.2) | regra do CLAUDE.md, sem teste que cobrisse todos os caminhos | teste de contrato: 200 sem conteúdo sempre traz aviso |
| Matriz de SSRF com evidência de conexão (pacote, 15 casos) | testes existiam, sem mapa caso → teste | `docs/security/SSRF_MATRIX.md` e os testes que faltavam |

Ao corrigir o primeiro item, o agente achou mais três defeitos que os
documentos não previam: o mesmo conteúdo em duas URLs colidia no id da versão;
uma página que voltava a bytes já vistos (A → B → A) dava 500; e um corpo HTML
vazio dava 500 em vez de um documento vazio com aviso. Os três estão
corrigidos e testados. O teste de contrato de "200 sem conteúdo" também achou
um caminho do `assemble` que só emitia a dica de cobertura baixa, sem aviso.

## 2. Já feito antes destes documentos (com onde está)

- **SSRF na conexão, redirects, IPv4/IPv6, notações de IP, userinfo, esquemas,
  DNS rebinding, navegador por proxy de saída, service workers, popups,
  descompressão, prazos:** `app/fetch/guard.py`, `client.py`,
  `egress_proxy.py`, `render.py`; auditoria em `docs/AUDITORIA_SEGURANCA.md`. Os
  testes confirmam que o destino proibido não recebeu nada (o critério que o
  guia exige), não só que a função recusou.
- **Estados explícitos:** `coverage` (sufficient/partial/insufficient),
  `sources[].status`, `warnings` com código estável, `hints` com ação
  (`budget_limited`, `low_coverage`, `source_failed`, `mixed_versions`), 422
  quando todas as fontes falham. O guia pede algo equivalente com outros nomes.
- **Proveniência:** `document_id`, `version_id`, `block_id`, `chunk_id`,
  `source_url`, página em PDF e citação literal; `<document>`/`<fragment>` que a
  página não forja.
- **Orçamento com tokenizer declarado** (`cl100k_base (approximate)`) e curva
  qualidade × orçamento no M8; o risco de Claude contar diferente é medido por
  `bench/tokenizer_ratio.py`.
- **Benchmark:** corpus congelado, perguntas reais, pré-registro com emendas,
  bootstrap pareado e por grupo, não inferioridade com condição de validade,
  juiz validado contra humanos, contaminação; régua de extração por estrato
  (WCXB-docs + PDFs) com dev/test fixos.
- **Extração:** PDF por geometria com aviso de páginas sem texto, truncamento
  explícito (`pdf_truncated`), Markdown e texto puro; HTML com código, tabelas e
  avisos.
- **MCP:** loopback por padrão, `Origin`/`Host`, ids validados, anotações de só
  leitura, descrições curtas (teto de 800 tokens).

## 3. Não se aplica ao desenho do NINA (e por quê)

- **Validação claim → evidência na resposta final:** o NINA não gera resposta;
  quem afirma é o agente. O NINA entrega citação literal e ids, e o M10 mede a
  citação do agente (`citation_correct`). Um verificador de claims seria outro
  produto.
- **OCR e roteador para layout/OCR:** fora do escopo do MVP (CLAUDE.md); o NINA
  avisa (`pages_without_text`) em vez de falhar calado.
- **Multi-tenant, isolamento de cache entre tenants:** o NINA só lê páginas
  públicas sem credenciais do usuário; o cache compartilhado é por URL pública.
  Fica registrado como premissa: se um dia ler páginas autenticadas, a chave de
  cache precisa incluir a identidade.
- **JSON Schemas e validador semântico do pacote:** o contrato do NINA já é
  tipado por Pydantic e testado; os schemas do pacote descrevem outro modelo
  (capture/extraction/evidence-package) que não corresponde ao nosso. Adotar
  exigiria reescrever o contrato sem ganho medido.
- **Avaliador de retrieval do pacote (Recall@k, MRR, nDCG):** o NINA tem os
  seus (`bench/section_recall.py`, `anchor_qrels`, `m10a_*`), ligados ao corpus
  real; o do pacote usa dados sintéticos.

## 4. Fica para depois (registrado)

- **Frescor exposto na resposta** (capturado em, validado em, stale): proposta
  `max_age_seconds`/`allow_stale` já registrada no PROGRESS; muda o contrato.
- **Requisição condicional (ETag/If-None-Match):** o NINA faz GET completo e
  deduplica por hash; economiza banda, não muda resultado.
- **Spans por etapa (OpenTelemetry), SLOs por workload, retenção e exclusão,
  canary e rollback:** escopo do M11 (deploy e observabilidade).
- **Cobertura por facetas** (perguntas compostas): ideia boa, mas mexe na
  seleção; precisa da régua do dev, que roda na máquina do humano.
