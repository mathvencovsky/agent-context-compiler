# O que só você pode fazer

A IA escreve o código. Estas etapas dependem de você, e várias bloqueiam marcos.

## Antes do M0

- [ ] Instalar Docker, uv e Claude Code; criar o repositório no GitHub.
- [ ] Copiar este kit para a raiz do repositório e fazer o primeiro commit.
- [ ] Rodar o prompt 1 de PROMPTS.md e resolver as dúvidas que ele levantar.

## Em paralelo, desde o início (fora do código)

- [ ] Entrevistar de 15 a 20 desenvolvedores que constroem agentes. Perguntas-chave: onde a documentação quebra o agente, quanto gastam em tokens com isso, o que usam hoje (Firecrawl, Jina, Markdown da Cloudflare, scraping próprio) e o que pagariam.
- [ ] Conseguir de 3 a 5 pessoas dispostas a testar no M9.
- [ ] Pedir a cada uma de 5 a 10 perguntas reais que o agente delas erra. Essas perguntas valem mais no benchmark do que as rascunhadas pela IA.

## Para o M2 (bloqueante)

- [ ] Escolher de 10 a 15 sites de documentação técnica pública com estruturas variadas: referência de API com tabelas de parâmetros, guias longos, changelogs, pelo menos 2 sites em SPA e pelo menos 3 PDFs técnicos.
- [ ] Conferir os termos de uso e o robots.txt de cada site.
- [ ] Preencher `bench/sites.yaml` com as URLs (de 5 a 15 páginas por site).
- [ ] Preencher `bench/prices.yaml` com os preços atuais dos modelos que vai usar.
- [ ] Validar as tarefas com `bench/review_tasks.py`. Meta: **100 tarefas validadas**, sendo 70 `dev` e 30 `test`. Abrir a fonte e conferir a resposta de cada uma. Este é o passo mais importante do projeto inteiro: se as tarefas estiverem erradas, o benchmark mede o nada.
- [ ] Escolher o LLM respondedor e o LLM juiz do benchmark e colocar as chaves no `.env`.
- [ ] (Opcional) Chave do Firecrawl para o baseline comercial.

## Para o M6 e o M7 (bloqueante)

- [ ] Escolher e contratar o provedor de embeddings e o de reranking (Voyage, Jina, Cohere ou OpenAI, por exemplo). Usar o mesmo fornecedor para os dois simplifica o MVP.
- [ ] Definir um teto de gasto mensal nas contas dos provedores.

## No M8 (decisão)

- [ ] Ler o relatório e o resumo.
- [ ] Decidir: seguir, ajustar o nicho ou parar. Registrar o motivo em DECISIONS.md.

## No M10 (avaliação ponta a ponta)

- [ ] Fase 0: revisar e aprovar `bench/PREREG.md` antes do commit (e autorizar o push, que dá um carimbo de data externo).
- [ ] Fase 1: a fonte passou a ser o dump arquivado do Stack Overflow de 2024-04, porque a Acceptable Use Policy proíbe coletar pela API para benchmark de IA (DECISIONS 2026-10-15). Fica com você a decisão jurídica sobre usar esse conteúdo CC BY-SA num benchmark de produto comercial. Enviar transcrições de agente que possam ser compartilhadas (opcional).
- [ ] Fase 2: dois avaliadores humanos, cegos ao braço, por cerca de 2 a 3 horas cada (pelo menos 100 respostas).
- [ ] Fase 3: chave Anthropic num workspace dedicado só ao benchmark (para conciliar a fatura) e chave OpenAI do juiz; teto de gasto do piloto (cerca de US$ 30) e, com a estimativa medida no piloto, o teto da rodada principal; exportar o custo do workspace na janela da rodada. Depois da triagem e antes da rodada principal, auditar cerca de 20% da população primária (Emenda 2 do PREREG) com `uv run python -m bench.audit_external <arquivo de ids>`.
- [ ] Fase 5: uma máquina limpa e alguém para cronometrar do `git clone` ao primeiro resultado no Claude Code.

## No M11

- [ ] Escolher a hospedagem (Fly.io, Railway, Render ou AWS, por exemplo) e um Postgres gerenciado com pgvector (Neon, Supabase ou RDS).
- [ ] Criar as contas, o domínio e o bucket.
- [ ] Escrever uma política de privacidade e termos de uso mínimos antes de entregar chaves a terceiros.

## Custos a prever

- LLM do benchmark: cada execução completa no M8 faz por volta de 100 tarefas x 10 estratégias e ablações x 3 orçamentos, cerca de 3.000 chamadas ao respondedor, mais as do juiz. Rode primeiro no `dev` com orçamento único para medir o gasto.
- Avaliação do M10: uma sessão de agente custa muitas vezes uma chamada única (vários turnos, páginas inteiras, juízes). A rodada principal deve ficar na casa das centenas de dólares; o piloto mede o custo real antes de você fixar o teto.
- Embeddings e rerank: baixos no volume do MVP, mas meça desde o M6.
- Uso do Claude Code: os marcos M3 e M7 tendem a consumir mais.
