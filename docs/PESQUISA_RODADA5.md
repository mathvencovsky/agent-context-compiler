# Pesquisa, rodada 5 (2026-10-29): pesquisas de big techs por assunto

Fontes primárias encontradas por busca (resumos; os PDFs devem ser salvos pelo
humano). Cada item diz o que muda no NINA. Nada aqui foi implementado ainda.

| assunto | pesquisa | o que muda no NINA | custo de API |
|---|---|---|---|
| Agente usar as ferramentas | Anthropic, "Writing effective tools for agents — with agents" (2025), anthropic.com/engineering/writing-tools-for-agents; Hsieh et al. (Google), "Tool Documentation Enables Zero-Shot Tool-Usage with LLMs", arXiv 2308.00675 | revisar as descrições do MCP: dizer quando usar `assemble` e quando aprofundar com `get_document` (como o `search_documents` do Google Developer Knowledge faz) | zero |
| Contexto sob demanda | Anthropic, "Effective context engineering for AI agents" | confirma o desenho (ids leves, busca na hora); sem mudança | zero |
| Docs geram código melhor | Zhou et al., "DocPrompting", arXiv 2207.05987 (ICLR 2023) | argumento de produto; tldr/CoNaLa como teste extra possível | zero |
| URL duplicada | Bar-Yossef et al., "Do Not Crawl in the DUST" (WWW 2007); Manku et al. (Google), "Detecting Near-Duplicates for Web Crawling" (WWW 2007, simhash); Microsoft, "A Pattern Tree-based Approach to Learning URL Normalization Rules" (WWW 2010) | **lacuna confirmada no código:** o NINA não remove `utm_*`/`authuser` nem lê `rel=canonical`; a mesma página vira dois documentos. Proposta: normalizar a URL (sem tocar `?hl=`, que muda o idioma) e registrar o canônico; muda identidade → teste, DECISIONS e emenda | zero |
| Versão da biblioteca | Google Research, "GitChameleon 2.0" (NeurIPS 2025, 48–51% de acerto); "LibEvolutionEval" (NAACL 2025); "VersiCode", arXiv 2406.07411 | testes gratuitos e executáveis para o parâmetro `version` | zero (só local) |
| Página por layout | Microsoft Research, "VIPS: a Vision-based Page Segmentation Algorithm" (MSR-TR-2003-79) | referência para abas e conteúdo escondido (páginas do Google têm abas de linguagem); criar casos no extract-eval | zero |
| Injeção indireta | Hines et al. (Microsoft), "Defending Against Indirect Prompt Injection Attacks With Spotlighting", arXiv 2403.14720 | confirma `<document>` + `untrusted_source`; avaliar datamarking como opção | zero |
| Ordem do contexto | Liu et al., "Lost in the Middle", arXiv 2307.03172 | o NINA já ordena por relevância; medir colocar o melhor fragmento no começo e no fim | zero (dev) |
| Compressão | Jiang et al. (Microsoft), "LongLLMLingua", arXiv 2310.06839 | referência para o modo compacto (o nosso é determinístico) | zero |
| Frescor | Vu et al. (Google), "FreshLLMs", arXiv 2310.03214 | número e ordem das evidências mudam o acerto; apoia `max_age_seconds` | zero |
| Embedding menor | Kusupati et al. (Google), "Matryoshka Representation Learning", arXiv 2205.13147 | base da migração para 1024 dimensões | zero |
| Concorrente | Google Developer Knowledge API/MCP (prévia, fev. 2026): `search_documents` (trechos + documento pai), `get_documents` (até 20 páginas), só sites do Google, Markdown sem estrutura | o NINA cobre qualquer URL, PDFs e citação literal; copiar a instrução explícita de aprofundar | zero |

## Ordem proposta

1. Normalização de URL e `rel=canonical` (lacuna real, determinística).
2. Descrições do MCP (instrução de aprofundar), dentro do teto de 800 tokens.
3. GitChameleon 2.0 como teste local do parâmetro `version`.
