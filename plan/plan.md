> **Estado: fases 1–3 implementadas em 2026-08-18.**
>
> O que saiu diferente do proposto aqui, e por quê — o detalhe está em
> `docs/DECISOES.md`:
>
> - **`status: inbox` → propriedade `triagem`.** `status` no vault é eixo de
>   *maturidade* (`semente|crescendo|maduro`) e é usado pelas 14 páginas de
>   `20_WIKI/`. Triagem é outro eixo e ganhou propriedade própria.
> - **`tags: [rust, async]` → wikilink.** O `90_META/ESQUEMA.md` proíbe tag de
>   assunto ("assunto é trabalho de link"). O pipeline já emitia `conceitos` →
>   `[[X]]` quando a página existe, `X *(candidato)*` quando não. Reusado.
> - **A migração também insere referência no bullet antigo.** Sem isso as 70
>   notas nasceriam órfãs: `parte-de` é aresta saindo e o invariante 3 exige
>   aresta entrando.
> - **`dominio` confirmado antes de escrever:** 48 de 70 links (69%) são
>   `youtube.com` e 8 são `instagram.com` — 80% de duas fontes. A previsão aqui
>   era 40%; é o dobro.
> - **5 views no Bases, não 4** (entrou "Descartados": revisar o que se descartou
>   é trabalho legítimo).
>
> **Fase 4 (síntese semanal) implementada em 2026-08-18**, via `dlb sintese`, com
> backend `codex` por padrão — a classificação diária é o caminho crítico da
> captura e não divide a janela de uso da Claude com uma chamada por semana. Sai
> na **segunda** e não na sexta: na sexta a semana não fechou, e a nota nasceria
> sem o fim de semana. O rollup mensal come as sínteses semanais, não os links
> crus. Mora em `40_DIARIO/Semanal AAAA-Www.md` com `tipo: diario` — só a
> convenção de nome foi emendada no ESQUEMA, não o vocabulário fechado.
>
> **Fases 5 e 6 implementadas em 2026-08-18.** `dlb buscar` / `?buscar` no canal
> (polling, não slash command — gateway traria dependência de websocket e
> Interactions abriria porta de entrada na máquina), `dlb ressurgir` (sem chamada de
> modelo) e `dlb temas` (relatório, nunca MOC automático). Busca sem embeddings: o
> corpus inteiro cabe num prompt, e `dlb doctor` mostra o quanto do orçamento está
> em uso — 28% hoje.
>
> Reinterpretações necessárias: o limiar de tema é **8** (número do `MOC - Índice`
> do vault) e não 15; e conta-se `conceitos`, não tag de assunto, que o vault
> proíbe. Os líderes brutos eram plataformas (Instagram 7, YouTube 6), então há
> stoplist com os descartados visíveis.
>
> Todas as seis fases do plano estão implementadas.
> Decidido para elas: busca com `claude -p` sobre o corpus inteiro em vez de
> embeddings (Ollama não está instalado, e 70 notas ≈ 50KB cabem num contexto);
> `/buscar` por polling de `?buscar` reusando `iter_messages()` em vez de gateway.

---

Seu pipeline hoje resolve a **captura**, que é a parte fácil. O problema é o clássico "memória write-only": o link entra, nunca sai. Uma nota diária com 8 URLs cruas é praticamente invisível para busca — daqui a seis meses você não vai lembrar que salvou aquilo, e mesmo que lembre, não vai achar.

O que falta são as camadas entre capturar e usar:## 1. Enriquecer no momento da captura

Essa é de longe a mudança de maior impacto. Sua automação já roda — faça ela fazer mais três coisas antes de escrever no Obsidian: buscar o `<title>` e a meta description da página, extrair o texto principal, e mandar para um LLM gerar um resumo de 2-3 linhas + 3 a 5 tags.

Além disso, **pare de jogar tudo na nota diária**. Crie uma nota atômica por link em `50_LINKS/`, e deixe o diário apenas com os wikilinks do dia. Assim cada link vira um nó de primeira classe: aparece no grafo, tem backlinks, é filtrável.

Estrutura sugerida do frontmatter:

```yaml
---
url: https://exemplo.com/artigo
titulo: "Título real da página"
dominio: exemplo.com
capturado: 2026-08-18
origem: discord
tags: [rust, async, performance]
status: inbox
nota: 3           # sua avaliação depois de ler
resumo: "Explica como o runtime do Tokio faz work-stealing..."
---
```

O campo `dominio` parece bobo mas é ouro depois — você descobre em 3 meses que 40% do que salva vem de duas fontes, e pode simplesmente assinar o RSS delas.

## 2. Triagem: sem isso vira cemitério

O campo `status` é o que separa um sistema vivo de um depósito. Ciclo mínimo: `inbox` → `lido` → `aplicado` ou `descartado`. Descartar é uma ação legítima e importante — reconhecer que algo não serviu limpa o ruído.

Reserve 20 minutos numa sexta. Não para ler tudo, só para classificar. A maioria dos links vai virar `descartado` e tudo bem.

## 3. Bases e dashboards

O Bases virou plugin nativo do Obsidian no início de 2026, com views de lista, tabela e cards construídas em cima das propriedades das notas. Não precisa mais instalar Dataview para o básico. Crie umas 4 views:

- **Inbox** — `status = inbox`, ordenado por data. Sua fila de trabalho.
- **Por tema** — agrupado por tag, mostrando contagem.
- **Aplicados** — o que virou uso real. É o seu "acervo de valor comprovado".
- **Fontes** — agrupado por domínio.

## 4. Síntese periódica automatizada

Estenda a automação: toda sexta, ela pega os resumos dos links da semana e gera uma nota `Semanal 2026-W33` com os temas recorrentes, o que se conecta com o que, e 2-3 perguntas em aberto. Mensal, faz o rollup dos semanais.

Isso é o que converte fluxo em conhecimento. Ler 7 resumos semanais no fim do mês te dá uma leitura do seu próprio interesse que nenhuma nota diária dá.

## 5. Acesso rápido — a parte que você mais pediu

Três níveis, do mais barato ao mais poderoso:

**Busca semântica dentro do Obsidian.** O Smart Connections roda um modelo de embedding local, sem API key, e mostra notas relacionadas enquanto você escreve. Combinado com o Copilot for Obsidian apontando para um Ollama local, você pergunta "o que eu já salvei sobre observabilidade?" e recebe uma resposta sintetizada. A dupla Smart Connections + Copilot cobre a maior parte dos casos de "segundo cérebro" sem mandar nada para a nuvem — os embeddings ficam em `.smart-env/` dentro do vault.

**Um `/buscar` no próprio Discord.** Esse é o encaixe mais natural pro seu caso. Você já vive no Discord, é onde você joga os links. Faça o bot também *responder*: indexe as notas com embeddings (Ollama + `nomic-embed-text`, guardando em SQLite ou LanceDB) e adicione um slash command que busca no vault e responde no canal. Captura e recuperação no mesmo lugar, e funciona do celular sem abrir o Obsidian.

**Ressurgimento programado.** Todo dia de manhã o bot posta 3 links aleatórios de mais de 30 dias atrás, com o resumo. Combate direto ao esquecimento e custa 15 linhas de código. Na minha experiência esse é o recurso que mais surpreende as pessoas — você redescobre coisas que salvou e esqueceu completamente.

## 6. Fechar o ciclo: produzir

Quando uma tag passar de ~15 links, isso é sinal de que virou um tema seu de verdade. Crie um MOC (`MOC - Rust Async`) reunindo os melhores, escrevendo *com suas palavras* o que você entendeu. Os MOCs viram sua porta de entrada, não o diário.

E de vez em quando transforme um MOC num post, num snippet, num projeto. Conhecimento que nunca sai não é conhecimento, é acúmulo.

---

## Ordem que eu sugeriria

1. Enriquecimento na captura + nota atômica por link *(maior ganho, e retroativamente aplicável nos arquivos antigos)*
2. Script de migração para reprocessar o que já está em `40_DIARIO`
3. Bases com a view de inbox
4. Smart Connections
5. Síntese semanal automatizada
6. `/buscar` no Discord

---

