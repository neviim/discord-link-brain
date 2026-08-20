# discord-link-brain

Lê os links que você jogou num canal do Discord durante o dia, descobre do que
cada um trata, classifica, e arquiva tudo no vault Obsidian de forma que dê para
consultar depois — por dia, por categoria e por conceito.

O problema que resolve: o canal do Discord é ótimo para capturar (link entra sem
fricção) e péssimo para consultar (uma semana depois é uma pilha de URLs sem
contexto). Este pipeline transforma a pilha em trilha.

```
Discord  →  extrai links  →  lê a página   →  LLM classifica  →  vault Obsidian  →  marca ✅
            (dedup, limpa)   (oEmbed, og:,    (resumo, cat.)     (fonte + nota       (no Discord)
                              texto central)                      atômica + diário
                                                                  + MOC)
```

## Documentação

| Quero… | Leia |
|---|---|
| usar a nota atômica, triar, migrar, gerar a síntese semanal | [docs/LINKS.md](docs/LINKS.md) |
| entender como funciona por dentro | [docs/ARQUITETURA.md](docs/ARQUITETURA.md) |
| configurar Discord, vault e LLM | [docs/CONFIGURACAO.md](docs/CONFIGURACAO.md) |
| rodar, agendar, resolver problema | [docs/OPERACAO.md](docs/OPERACAO.md) |
| saber **por que** algo é assim | [docs/DECISOES.md](docs/DECISOES.md) |
| mexer no código | [docs/DESENVOLVIMENTO.md](docs/DESENVOLVIMENTO.md) |
| **acompanhar tudo pelo navegador** | [docs/PAINEL.md](docs/PAINEL.md) |

## O que ele escreve no vault

Respeitando o contrato em `90_META/ESQUEMA.md`:

| Camada | Arquivo | Conteúdo |
|---|---|---|
| `10_FONTES/` | `FONTE - Discord <canal> - AAAA-MM-DD.md` | Captura crua e **imutável**: todo link do dia, com autor, hora, comentário original e link para a mensagem no Discord. Só é reescrito com `--force`. |
| `50_LINKS/` | `AAAA-MM-DD <título>.md` | **Uma nota por link**: resumo, por que importa, conceitos, domínio, e um campo `triagem` para você classificar depois. Cada link vira nó de primeira classe — aparece no grafo, tem backlink, é filtrável. |
| `40_DIARIO/` | `AAAA-MM-DD.md` | O recorte do dia: links agrupados por categoria, cada um apontando para sua nota. Se a nota do dia já existir, a seção é **acrescentada** — nada do que você escreveu é perdido. |
| `30_MOC/` | `MOC - Captura Discord.md` + `Links.base` | Hub de navegação: as capturas por dia, as views do Bases, e se registra em `MOC - Índice`. |

A nota de link é o único artefato que **você edita por desenho**. `triagem`
(`inbox` → `lido` → `aplicado` \| `descartado`), `nota` (1–5) e tudo abaixo de
`## Minhas notas` são seus, e nem `--force` reescreve. Ver o ritual de triagem em
[docs/OPERACAO.md](docs/OPERACAO.md).

**O que ele deliberadamente NÃO faz: criar páginas em `20_WIKI/`.** Compilar
conceito é trabalho curado (ver a skill `obsidian-segundo-cerebro`). Os conceitos
que o LLM identifica saem de duas formas:

- conceito que **já tem página** → vira wikilink de verdade (`[[Obsidian CLI]]`)
- conceito que **ainda não tem** → sai como texto `Nome *(candidato)*` e entra
  numa lista marcada com `#acao/expandir`

Isso é de propósito: link para nota inexistente derruba dois invariantes do
ESQUEMA de uma vez (o 4, e o 2 porque link não-resolvido não conta como aresta).

Exemplo de saída real:

```markdown
### documentacao

- **[Obsidian CLI](https://obsidian.md/cli)** — Documentação da linha de comando do
  Obsidian, descrita como um "playground programático para texto puro": permite
  executar pelo terminal tudo o que se faz na interface do app.
  *Por que importa:* Abre caminho para automatizar operações no vault por script.
  Conceitos: [[Obsidian]], [[Obsidian CLI]]
  <sub>16:55 · Dotcom · #area/pkm</sub>
```

## Instalação

```bash
cd ~/Developer/discord-link-brain
pip install -r requirements.txt        # requests, PyYAML
cp .env.example .env                   # e preencha (ver abaixo)
python3 -m dlb doctor                  # checa vault, LLM e acesso ao Discord
```

Dependências são só `requests` e `PyYAML`. O LLM roda pelo binário `claude` que
você já tem logado — **nenhuma API key necessária**.

## Configuração

O único passo que exige trabalho manual é o acesso ao Discord.

**1. Criar o bot** (5 min): <https://discord.com/developers/applications> →
*New Application* → aba *Bot* → *Reset Token* e copie. Ative
**Message Content Intent** (sem isso o `content` das mensagens vem vazio).

**2. Convidar para o servidor**: aba *OAuth2 → URL Generator*, escopo `bot`,
permissões **View Channel** + **Read Message History** + **Add Reactions**
(soma `66624`). Abra a URL gerada.

**3. Pegar o id do canal**: Discord → Configurações → Avançado → **Modo
desenvolvedor** ligado; botão direito no canal → *Copiar ID*.

> **Canal privado** precisa de um passo a mais: entrar no servidor não dá
> acesso a ele. Botão direito no canal → *Editar canal* → *Permissões* →
> adicione o bot com *Ver canal*, *Ler histórico de mensagens* e
> *Adicionar reações*.

**4. Preencher o `.env`**:

```ini
DISCORD_TOKEN=<token do bot>
DISCORD_CHANNEL_IDS=123456789
DISCORD_CHANNEL_NAMES=123456789:estudos
OBSIDIAN_VAULT=~/Documents/00_Obsidian-dotcom/dotcom
```

> **Nota sobre token de usuário.** O código aceita `DISCORD_TOKEN_TYPE=user`
> para ler canais aos quais só você tem acesso, mas automatizar uma conta
> pessoal viola os Termos de Serviço do Discord e pode banir a conta. Use bot.

## Painel

Seis telas no navegador — o que rodou, o que está pendente, a agenda, o acervo, um
chat sobre ele e a configuração. Roda no host, sem dependência nova:

```bash
python3 -m dlb painel                 # http://127.0.0.1:8765
```

Sem `PAINEL_TOKEN` no `.env` ele só sobe em `127.0.0.1`, de propósito. Para acessar
de outra máquina pela tailnet, e para subir junto com o sistema pelo systemd, o
passo a passo está em **[docs/PAINEL.md](docs/PAINEL.md)**.

## Uso

```bash
# o de todo dia: pega o que entrou desde a última execução
python3 -m dlb sync

# primeira carga / recuperar atraso
python3 -m dlb sync --since 7

# um dia específico
python3 -m dlb sync --day 2026-08-15

# cria a nota atômica dos links já capturados — sem LLM, sem rede
python3 -m dlb migrar --dry-run
python3 -m dlb migrar

# síntese do período: temas recorrentes, conexões, perguntas em aberto
python3 -m dlb sintese --semana anterior
python3 -m dlb sintese --mes 2026-08

# consultar o acervo, ressurgir o esquecido, ver o que virou tema
python3 -m dlb buscar "o que eu salvei sobre agentes de IA?"
python3 -m dlb ressurgir --dry-run
python3 -m dlb temas

# ver o que faria sem escrever nada
python3 -m dlb sync --since 3 --dry-run

# acompanhar tudo pelo navegador (ver docs/PAINEL.md)
python3 -m dlb painel

# apanhado da semana, lendo o que já está no vault
python3 -m dlb semana --days 7
python3 -m dlb semana --days 7 --out ~/.buzz/OUTBOX/SEMANA.md
```

Depois de um `sync`, o índice do Obsidian tem latência — rode
`obsidian reload`, espere ~3s, e só então o lint (`obsidian orphans`,
`deadends`, `unresolved verbose`).

### Flags úteis do `sync`

| Flag | Efeito |
|---|---|
| `--dry-run` | Não escreve nada; imprime o que sairia |
| `--force` | Reescreve fonte e seção do diário já existentes (implica `--ignore-marks`) |
| `--ignore-marks` | Reprocessa mesmo o que já está marcado com ✅ no Discord |
| `--no-enrich` | Não abre as páginas (mais rápido, resumo mais fraco) |
| `--channel <id>` | Só este canal (repetível) |
| `--include-bots` | Inclui links postados por bots |
| `--no-index` | Não mexe em `MOC - Índice.md` |

## Testes

```bash
./testar.py                # a suíte inteira, com resumo legível
./testar.py --help         # os sete modos: um módulo, repetir, cobertura, container…
```

Termina com `✔ PASSOU` (código 0) ou `✘ FALHOU` (código 1) — e `⚠ NADA RODOU`
(código 2) quando o comando não chegou a rodar a suíte, porque zero teste executado
não é sucesso.

**810 testes, 89% de cobertura** das linhas de `dlb/`. Nenhum toca a rede, o Discord
ou o vault real. Os sete modos, o mapa de o-que-cobre-o-quê e como medir cobertura
estão em [docs/DESENVOLVIMENTO.md](docs/DESENVOLVIMENTO.md).

Sem o script, as linhas cruas continuam valendo:

```bash
python3 -m unittest discover -s tests -t .   # no host, só precisa de PyYAML
./dlb.sh dev test                            # dentro do container
```

## Limitações conhecidas

- **Primeira execução sem cursor** pega só as últimas 24h. Use `--since N` para
  carregar mais.
- **Histórico profundo** entra em fatias, não de uma vez: são muitos dias e é
  uma chamada de LLM por dia. Use `dlb backfill` — ver
  [docs/OPERACAO.md](docs/OPERACAO.md#recuperar-o-histórico).
- **Enriquecimento falha em sites com anti-bot** (Cloudflare, X/Twitter) e em
  páginas renderizadas só por JavaScript. O link entra assim mesmo, e o modelo é
  instruído a dizer que não leu a página em vez de inventar.
- **Uma chamada de LLM por dia** — um dia com 200 links pode estourar o contexto.
  Na prática não chega perto disso.

---

Documentação completa em [`docs/`](docs/README.md): arquitetura, configuração,
operação, decisões de projeto e desenvolvimento.
