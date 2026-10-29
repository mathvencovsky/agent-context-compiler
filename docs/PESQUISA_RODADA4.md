# Pesquisa, rodada 4 (2026-10-29): busca em docs, modelos locais, entrega a agentes e eficiência

Quatro frentes, sem repetir as rodadas anteriores. Os relatórios completos
ficam em `docs/pesquisa/rodada4/`:

| frente | arquivo |
|---|---|
| H. Como motores de busca de documentação e de código ranqueiam | `H_busca_docs.md` |
| I. Modelos de recuperação locais e gratuitos | `I_modelos_locais.md` |
| J. Como entregar documentação a agentes de código (evidência real) | `J_entrega_agentes.md` |
| K. Eficiência: latência, cache e compressão determinística | `K_eficiencia.md` |

Como nas outras rodadas, este ambiente só alcança o GitHub e os sites da
Anthropic; o que veio só de resumo de busca está marcado em cada relatório.

## 1. Conferido de forma independente pela sessão principal

- **A busca léxica do NINA não usa BM25.** `app/retrieve/lexical.py::overlap_ranks`
  conta quantas palavras da pergunta aparecem no trecho: sem IDF, sem
  normalizar pelo tamanho, sem peso de campo; o tokenizador `[a-z0-9]+` quebra
  `os.path.join` e não separa camelCase. A coluna `tsv` e o índice GIN existem
  e não são usados. (Frente H.)
- **O `assemble` resolve as URLs uma por vez** (`app/retrieve/assemble.py`,
  laço `for url in req.urls`), e **o cache do robots.txt morre a cada
  requisição** (`provide_robots_checker()` cria um `RobotsChecker` novo, e o
  cache vive na instância). (Frente K.)
- **O efeito de fazer o agente consultar a documentação.** No harness público da
  Vercel (`vercel/next-evals-oss`, MIT, 37 combinações de modelo e harness), a
  contagem independente da sessão principal dá **44,9% de acerto sem o bloco de
  dois parágrafos no AGENTS.md contra 65,4% com ele** (1.807 e 1.432 execuções;
  o pesquisador contou 48% e 70% com outra regra de repetição). A documentação
  estava instalada nas duas condições; o bloco só aponta para ela. (Frente J.)

## 2. O que muda no plano

### A. Recuperação: o maior ganho barato que sobrou

1. **BM25F na busca léxica** (H, L1): campos título do trecho, títulos acima,
   prosa e código, com IDF calculado no pedido. É o que DocSearch, Read the
   Docs, Sphinx, Pagefind e Zoekt fazem, cada um com sua variação.
2. **Sinal de página** (H, L2): na fatia dev, uma palavra do título da pergunta
   aparece no endereço da página citada em 28% dos casos, contra 9,6% nas
   outras páginas da tarefa.
3. **Identificadores** (H, L3): indexar o nome inteiro e as partes
   (`os.path.join`, `getElementById`) e dar bônus ao símbolo exato.
4. **Reranker e embedding locais e gratuitos** (I): Qwen3-Reranker-0.6B
   (Apache 2.0, 73,4 em código contra 59,0 do Jina v2, sem corte de pergunta
   longa) e, para embedding, modelos permissivos de até 0,6B com Recall@50 de
   0,39 a 0,48 no FreshStack contra 0,33 do atual. O caminho mais curto é o
   servidor local do llama.cpp (MIT, binário para Windows), que responde no
   formato das APIs que o NINA já usa. Exige: `embed_query()` separado no
   contrato de embedding, uma exceção registrada para o provedor local no guard
   de rede e a recalibração de `coverage_low/high`.

Tudo isso muda a seleção: entra desligado por padrão, com regra de aceite
escrita antes de medir, e é medido na fatia dev na máquina do humano, sem custo
de API.

### B. Eficiência: bugs e desperdícios a corrigir (não mudam resultado)

- `assemble` em paralelo, com limite por host e por render, e ordem de saída
  determinística.
- Cache do robots.txt compartilhado entre requisições; cliente HTTP, cliente S3
  e engine do banco reaproveitados (um cliente HTTP por hostname, para não
  vazar SNI entre hosts no mesmo CDN).
- O acerto de cache não recontar tokens da página inteira (0,4 s numa página de
  1,2 MB): gravar a contagem quando a versão nasce.
- Navegador de vida longa com contexto novo por pedido, mantendo o proxy de
  saída; espera do render pelo texto estável em vez de `networkidle`.
- Revalidação condicional (ETag/If-None-Match), que o NINA já guarda e não
  envia.

### C. Produto: o fator que mais pesa é o agente chamar o NINA

- **`nina init`** (J1): escrever no AGENTS.md do projeto um bloco mínimo entre
  marcadores dizendo quando usar o NINA. Hoje o NINA só tem skill e instruções
  do servidor, que é o modo que perdeu no estudo da Vercel. Mínimo porque
  arquivos de contexto longos pioram o resultado.
- **Índice do site a partir do `llms.txt`** (J3): o agente precisa saber quais
  URLs passar; ler um arquivo de índice não é crawler.
- **Versão da dependência** (J6): parâmetro `version` e aviso de versão
  diferente em `<nina_notes>`.
- **Modo de orçamento baixo** (K, L7): no dev o acerto cai de 0,59 com 4.000
  tokens para 0,29 com 1.000; poda determinística de sentenças dentro dos
  trechos (código e tabelas inteiros, citação literal preservada). Exige uma
  métrica complementar no PREREG antes de medir.

Estes mudam o contrato da API ou do MCP e por isso precisam da aprovação do
humano (CLAUDE.md, regra 6).

### D. Benchmark

- **Limitação a registrar no PREREG** (J2): o modo que venceu no estudo da
  Vercel é documentação em arquivos lida pelo próprio agente, e o M10 não tem
  esse braço; o Apêndice B manda o agente usar as ferramentas, então o M10
  mede a qualidade depois de o agente decidir consultar. Um braço `local_docs`
  seria pago e precisa ser decidido antes do piloto.
- **Estratificar por tamanho de página** (J5): com documentação local, os
  agentes leem em média páginas pequenas (mediana 3,4 KB).

## 3. Onde o NINA já está igual ou melhor

Trechos por seção com o caminho de títulos, busca híbrida com rerank e
orçamento, proveniência e citação literal, paridade entre produto e benchmark,
e a cauda longa que índices pré-montados não cobrem (PDFs, RFCs, sites
legados, páginas grandes). Os números públicos de latência dos concorrentes
divergem até 6 vezes e não têm harness.

## 4. Para o humano salvar em PDF (prioridade)

1. Vercel, "AGENTS.md outperforms skills in our agent evals":
   https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals
2. "Evaluating AGENTS.md" (arXiv 2602.11988): https://arxiv.org/pdf/2602.11988
3. Sourcegraph, "Keeping it boring (and relevant) with BM25F":
   https://sourcegraph.com/blog/keeping-it-boring-and-relevant-with-bm25f
4. GitHub, "The technology behind GitHub's new code search":
   https://github.blog/engineering/architecture-optimization/the-technology-behind-githubs-new-code-search/
5. Model card do Qwen3-Reranker-0.6B: https://huggingface.co/Qwen/Qwen3-Reranker-0.6B
6. Model card do Octen-Embedding-0.6B: https://huggingface.co/Octen/Octen-Embedding-0.6B
7. Next.js 16.2/16.3, posts de IA: https://nextjs.org/blog/next-16-2-ai e
   https://nextjs.org/blog/next-16-3-ai-improvements
8. Lance Martin, "Vibe code benchmark":
   https://rlancemartin.github.io/2025/04/03/vibe-code
9. RECOMP (arXiv 2310.04408): https://arxiv.org/pdf/2310.04408
10. Tombros & Sanderson, "Advantages of Query Biased Summaries" (SIGIR 1998):
    https://marksanderson.org/publications/my_papers/SIGIR98.pdf

As demais estão na seção 5 de cada relatório.
