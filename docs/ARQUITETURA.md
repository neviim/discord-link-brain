# Arquitetura

Como o pipeline funciona por dentro: o caminho que um link percorre, quem faz o
quê, e onde o estado mora.

## O caminho de um link

Um link postado no Discord passa por sete etapas até virar nota. Cada uma pode
falhar sem derrubar as outras.

```
1. BUSCA        discord_client.iter_messages(canal, after=cursor)
                   ↓  mensagens novas desde a última execução
2. FILTRA        mensagem já marcada com ✅ ou 📚 sai fora
                   ↓
3. EXTRAI        extract.extract_links → URL normalizada, canonizada, deduplicada
                   ↓
4. DEDUPLICA     URL que já está em 10_FONTES/ sai fora (e ganha 📚 no Discord)
                   ↓
5. ENRIQUECE     enrich.fetch_meta → título, autor, descrição, texto do artigo
                   ↓
6. CLASSIFICA    classify.classify → 1 chamada de LLM por dia, não por link
                   ↓
7. ESCREVE       vault.write_fonte + write_diario + register_capture
                   ↓
8. MARCA         discord_client.add_reaction → ✅ no Discord
```

A ordem das etapas 7 e 8 importa: **só marca depois que a nota está no vault**.
Se algo explodir na escrita, a mensagem fica sem marca e volta na próxima
execução.

## Os módulos

| Módulo | Linhas | Responsabilidade |
|---|---|---|
| `cli.py` | 514 | Orquestração: os comandos `sync`, `backfill`, `semana`, `doctor` |
| `vault.py` | 375 | Escrita nas três camadas do vault e leitura das URLs já arquivadas |
| `enrich.py` | 204 | Descobrir do que a página trata (oEmbed, `og:`, texto do artigo) |
| `extract.py` | 204 | Achar, normalizar, canonizar e deduplicar URLs |
| `discord_client.py` | 166 | REST API do Discord: paginação, rate limit, reações |
| `config.py` | 125 | Configuração a partir do ambiente e do `.env` |
| `frontmatter.py` | 86 | YAML no dialeto do vault |
| `models.py` | 80 | As estruturas que atravessam o pipeline + vocabulário fechado |
| `state.py` | 46 | Os dois cursores por canal |

### `extract.py` — normalização

O trabalho invisível que evita nota duplicada. Cada URL passa por:

1. **Poda de pontuação final** — `.,;:!?` que pertencem à frase, não à URL. Os
   parênteses são tratados por balanceamento (a Wikipédia adora `(desambiguação)`).
2. **Remoção de rastreadores** — `utm_*`, `fbclid`, `gclid` e companhia.
3. **Canonização** — o mesmo conteúdo circula em formas diferentes:

   | Circula como | Vira |
   |---|---|
   | `youtu.be/ID`, `/shorts/ID`, `/embed/ID`, `/live/ID`, `watch?v=ID&t=90` | `youtube.com/watch?v=ID` |
   | `twitter.com/u/status/N`, `mobile.twitter.com/…`, `x.com/…?t=&s=` | `x.com/u/status/N` |

4. **Deduplicação** — por URL normalizada, dentro do lote processado.

Hospedeiros ignorados de propósito: `tenor.com`, `giphy.com` e os CDNs do
Discord — são GIF e anexo, não conteúdo para arquivar.

### `enrich.py` — três caminhos

| Link | Como | O que sai |
|---|---|---|
| YouTube | **oEmbed** (`/oembed`) — sem API key, sem bloqueio | título do vídeo e nome do canal |
| Site comum | `og:`/`<title>` **e o texto central** do artigo | resumo do conteúdo real |
| Nada legível | domínio e caminho da URL | rótulo fraco, mas identificável |

O texto central sai de `<article>`/`<main>` (ou da página toda), pegando `<p>`,
`<h1>`–`<h3>` e `<li>`, descartando `<script>`, `<nav>`, `<header>`, `<footer>`
e linhas com menos de 40 caracteres — que quase sempre são menu ou botão. Teto
de 3000 caracteres.

Falhar aqui **nunca** interrompe: o pior caso devolve `legivel=False`, e o
prompt instrui o modelo a dizer que não leu em vez de inventar.

### `classify.py` — a fronteira do LLM

É a única entrada do pipeline que ninguém controla. Por isso tudo que vem de lá
passa por porteiro:

- resposta cercada em ` ```json `, ou com tagarelice antes e depois → o parser
  acha o array mesmo assim;
- categoria ou área fora do vocabulário fechado → cai para `outro` / `area/ia`;
- link que o modelo pulou → vira nota de fallback, nunca some;
- ordem das notas segue a dos **links**, não a que o modelo devolveu;
- conceitos limitados a 4, sem `[[colchetes]]`.

**Uma chamada de LLM por dia, não por link.** Mais barato, e o contexto do dia
inteiro melhora a classificação de cada item.

### `vault.py` — as três camadas

| Camada | Arquivo | Regra |
|---|---|---|
| `10_FONTES/` | `FONTE - Discord <canal> - AAAA-MM-DD.md` | **Imutável.** Captura crua, cronológica. Só reescreve com `--force`. |
| `40_DIARIO/` | `AAAA-MM-DD.md` | Seção acrescentada, nunca substituindo o que você escreveu. |
| `30_MOC/` | `MOC - Captura Discord.md` | Hub de navegação; registra-se sozinho em `MOC - Índice`. |

Tudo respeita o contrato em `90_META/ESQUEMA.md` do vault: vocabulário fechado
de `tipo`, `status`, `tags`, e wikilink em propriedade **sempre entre aspas**.

## Onde mora o estado

Três lugares, com donos diferentes:

| Onde | O quê | Quem é dono |
|---|---|---|
| `.state/<canal>.json` | os dois cursores | o pipeline |
| Reações no Discord | o que já foi resolvido | o pipeline (só as dele contam) |
| `10_FONTES/` no vault | quais URLs já foram arquivadas | **o vault** |

A última linha é a mais importante: **o vault é a autoridade sobre o que está
arquivado**, não um índice paralelo. Apagou a captura, o link volta a ser
capturado. Ver [DECISOES.md](DECISOES.md#por-que-não-um-índice-em-sqlite).

### Os dois cursores

```
início do canal ──backfill_after──▶ … ◀──last_message_id── agora
     (passado)                                    (presente)
```

O `sync` anda do presente para frente; o `backfill` anda do começo do canal para
frente. Eles se encontram no meio, e a deduplicação por URL cuida da
sobreposição. São independentes: um não apaga o outro.

## Formato dos dados

```python
LinkHit     # o link cru, como saiu do Discord — nada interpretado
  url, channel_id, message_id, author, posted_at, context, jump_url
  .day   →  "2026-08-15"   (no fuso configurado, não em UTC)
  .hhmm  →  "19:53"

PageMeta    # o que conseguimos ler da página (best-effort)
  title, description, site, author, text, kind, legivel, error

LinkNote    # o link já classificado — é isto que vira nota
  hit, meta, titulo, resumo, porque_importa, categoria, area, conceitos
```

`categoria` e `area` são vocabulário fechado, definido em `models.py`:

- **10 categorias**: `artigo` `documentacao` `ferramenta` `repositorio` `paper`
  `video` `discussao` `lancamento` `curso` `outro`
- **4 áreas**: `area/ia` `area/buzz` `area/pkm` `area/gestao` — vêm do
  `ESQUEMA.md` do vault. Para acrescentar uma, **mude o ESQUEMA primeiro**: a
  regra é dele, não desta ferramenta.

## O container

Duas metas de build sobre `python:3.12-slim`:

- **dev** — código por bind mount, vault somente-leitura, efêmero
- **prod** — código na imagem, agendador como PID 1, `restart: unless-stopped`

O agendador (`docker/scheduler.sh`) é um laço em `sh`, não cron. Ver
[DECISOES.md](DECISOES.md#agendador-próprio-em-vez-de-cron).

O container roda com o **seu** UID/GID, porque escreve num vault que vive no
host — rodar como root deixaria arquivos root no seu Obsidian.
