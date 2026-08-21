# Operação

Rodar, agendar, recuperar histórico e resolver problema.

## Os dois modos

| | dev | prod |
|---|---|---|
| Código | bind mount do host — editou, rodou | assado na imagem |
| Vault | **somente leitura** (`--rw` libera) | leitura e escrita |
| Ciclo de vida | efêmero, um comando por vez | `restart: unless-stopped` |
| Sem argumento | `doctor` | agendador |
| Healthcheck | não | sim |

O vault entra somente-leitura em dev de propósito: `--dry-run` funciona inteiro
e um comando errado não escreve no seu segundo cérebro.

```bash
./dlb.sh dev doctor              # config, vault e Discord
./dlb.sh dev sync --dry-run      # ensaio; não escreve nada
./dlb.sh dev test                # a suíte dentro do container
./dlb.sh dev shell               # bash lá dentro

./dlb.sh prod up                 # sobe o agendador em background
./dlb.sh prod logs               # acompanha
./dlb.sh prod status             # estado + saúde
./dlb.sh prod run sync --since 7 # execução avulsa, fora do agendador
./dlb.sh prod down               # derruba
```

Para experimentar escrita sem risco, aponte para uma cópia:

```bash
DLB_DEV_VAULT=/tmp/vault-copia ./dlb.sh dev --rw sync --since 1
```

> **`prod restart` não aplica mudança de configuração.** Ele reaproveita o
> container existente com o ambiente antigo. Depois de mexer no `.env`, no
> `compose` ou no código, use `./dlb.sh prod up` — que reconstrói e recria.

## O agendador

Por padrão roda **de 6 em 6 horas ancorado na meia-noite**: 00:00, 06:00, 12:00
e 18:00 no fuso de `TIMEZONE`. Ancorado importa — reiniciar o container às 14h47
não desloca a grade para 20h47.

```
[2026-08-16 00:00:00 -03] sync concluída.
[2026-08-16 06:00:00 -03] sync concluída.
[2026-08-16 12:00:00 -03] sync concluída.
[2026-08-16 18:00:00 -03] sync concluída.
```

Uma sync que falha **não derruba o agendador** — a API do Discord fora do ar por
um dia não pode matar o container. Quem sinaliza é o healthcheck: lê um
heartbeat gravado só em sync bem-sucedida e marca `unhealthy` depois de 8h sem
nenhuma.

Configuração em [CONFIGURACAO.md](CONFIGURACAO.md#agendamento).

## Recuperar o histórico

O `sync` corre atrás do que chegou agora e precisa ser barato. O passado é outro
problema: são muitos dias, e **é uma chamada de LLM por dia**. Puxar tudo de uma
vez seria uma rajada de dezenas de chamadas — exatamente o que esgota a janela
de uso da assinatura.

Por isso o `backfill` anda em fatias:

```bash
python3 -m dlb backfill --days 2       # 2 dias de histórico e para
python3 -m dlb backfill --dry-run      # o que faria na próxima fatia
python3 -m dlb backfill --from 2025-09-01
python3 -m dlb backfill --restart      # recomeça do início do canal
```

Detalhes que importam:

- **Trecho sem link novo não gasta LLM.** O cursor pula a região inteira numa
  execução só.
- **Erro de classificação não vira buraco.** O cursor só avança até o último dia
  que deu certo; o dia que falhou volta na próxima fatia.
- **Fatia limitada dos dois lados**: `--days` (dias processados) e `--batch`
  (teto de mensagens lidas do Discord).

### Deixando andar sozinho

```ini
DLB_BACKFILL=1
DLB_BACKFILL_DAYS=2
```

A conta fecha: a grade é de **6h** e a janela de uso da assinatura Claude renova
a cada **5h**, então cada fatia cai numa janela nova e nunca compete com o uso
do dia. A 2 dias por ciclo, 41 dias de histórico entram em ~5 dias.

## Flags do `sync`

| Flag | Efeito |
|---|---|
| `--dry-run` | Não escreve nada; imprime o que sairia |
| `--force` | Reescreve fonte e seção do diário já existentes (implica `--ignore-marks`) |
| `--ignore-marks` | Reprocessa mesmo o que já está marcado no Discord |
| `--no-enrich` | Não abre as páginas (mais rápido, resumo mais fraco) |
| `--channel <id>` | Só este canal (repetível) |
| `--include-bots` | Inclui links postados por bots |
| `--no-index` | Não mexe em `MOC - Índice.md` |
| `--since N` | Ignora o cursor e volta N dias |
| `--day AAAA-MM-DD` | Processa apenas este dia |

## Triagem — os 20 minutos que decidem se isto vale algo

A camada `50_LINKS/` só tem valor se for triada. Sem isso ela é um depósito com
frontmatter bonito.

Reserve 20 minutos numa sexta. **Não para ler tudo — só para classificar.** Abra
`30_MOC/MOC - Captura Discord`, a view **Inbox**, e mova cada link:

| De → para | Quando |
|---|---|
| `inbox` → `lido` | Você leu ou assistiu. Se rendeu ideia, escreva abaixo de `## Minhas notas`. |
| `inbox` → `aplicado` | Virou uso real: código, decisão, um hábito. Vale `nota: 4` ou `5`. |
| `inbox` → `descartado` | Não serviu. **É a transição mais importante.** |

Descartar é trabalho legítimo. A maioria dos links vai virar `descartado` e está
tudo bem — reconhecer que algo não serviu é o que limpa o ruído. O que nunca é
triado não é acervo, é entulho.

Dois números para olhar: `dlb doctor` mostra a contagem por estado, e a view
**Fontes** mostra de onde vem o que você salva. Se 69% vem de um único domínio,
talvez o caminho seja assinar o RSS dele em vez de recapturar link por link.

## Migrar o histórico

Cria a nota atômica dos links que já estão no vault. **Sem LLM e sem rede** — tudo
que o modelo produziu está nos bullets do diário, e tudo que é cru está na fonte.

```bash
python3 -m dlb migrar --dry-run          # confere: quantas notas, quantos avisos
python3 -m dlb migrar --day 2026-08-17   # um dia só, para inspecionar
python3 -m dlb migrar                    # tudo
```

Idempotente por URL: rodar duas vezes não duplica nada e não toca o diário de
novo. Além de criar as notas, insere uma linha `Nota: [[...]]` sob cada bullet
antigo — é isso que impede as notas migradas de nascerem órfãs, porque `parte-de` é
aresta saindo e o invariante 3 exige aresta entrando. `--no-diario` desliga a
inserção, ao preço das órfãs.

Também é o **caminho de reparo**. A escrita no vault não é transacional: se o
processo morrer entre a fonte e a nota atômica, o dedup por `10_FONTES/` nunca mais
recria aquele link. `dlb doctor` avisa quantos links estão nessa situação, e
`migrar` conserta.

### Reparar o link da mensagem original

Captura anterior a 2026-08-18 gravou `channels/@me/...`, que não abre em canal de
servidor. Ponha o mapa no `.env` e refaça:

```bash
# descubra o servidor: python3 -m dlb doctor mostra o canal; o id do servidor
# está na URL do Discord no navegador, ou em Configurações do servidor
echo 'DISCORD_GUILD_IDS=<id_canal>:<id_servidor>' >> .env
python3 -m dlb migrar --force
```

`--force` refaz o miolo gerado das notas e **preserva** `triagem`, `nota`, `status`,
relações que você acrescentou e tudo abaixo de `## Minhas notas`. Não duplica a
referência no diário. As notas de `10_FONTES/` seguem com o valor original — a fonte
não se corrige, e o link certo é derivável.

## Consultar o acervo

```bash
python3 -m dlb buscar "o que eu salvei sobre agentes de IA?"
```

Manda o acervo inteiro para o modelo e responde citando os links. Se não houver nada
sobre o assunto, ele diz isso — não improvisa. No Discord, o mesmo pela pergunta
`?buscar …` no canal do responder.

```bash
python3 -m dlb ressurgir --dry-run          # o que postaria hoje
python3 -m dlb ressurgir --dias 90 --n 5    # mais antigos, mais deles
python3 -m dlb temas --limiar 3             # olhar antes de virar tema
python3 -m dlb temas --markdown             # bloco pronto para colar num MOC
```

`ressurgir` não gasta modelo — o resumo já existe na nota. `--dias` muda a idade
mínima (padrão 30) e `--n` quantos voltam (padrão 3). `temas` não escreve nada no
vault, nunca.

## Mudei o `.env` — como aplicar no prod

**`restart` não serve.** O `env_file` do compose é aplicado quando o container é
**criado**; `restart` só reinicia o processo dentro do container que já existe, com
o ambiente antigo. A falha é silenciosa: nada dá erro, e o container segue com o
valor velho.

```bash
./dlb.sh prod up      # recria os containers quando a configuração mudou
```

Como conferir que pegou, em vez de torcer:

```bash
docker exec dlb-prod printenv DLB_SINTESE DLB_RESSURGIR
docker exec dlb-responder printenv RESPONDER_CHANNEL_IDS
./dlb.sh prod logs responder | head -2
```

Exceção que confunde: as variáveis lidas pelo **Python** (`LINK_NOTES`,
`SINTESE_BACKEND`, `RESSURGIR_*`, `BUSCA_*`…) valem na próxima execução do comando,
porque `Config.from_env()` relê o `.env` a cada vez. As do **agendador** (`DLB_*`)
só valem com container recriado. Por isso `dlb doctor` pode mostrar o valor novo
enquanto o container ainda usa o antigo.

### Trocou de canal? Confira a permissão antes

Canal novo não herda permissão de canal antigo. Antes de recriar:

```bash
python3 -m dlb doctor --testar-envio    # posta e apaga no canal de captura
```

Para os canais do responder e do ressurgimento, o `--testar-envio` ainda não cobre
— confira postando um `?buscar` de teste e olhando `./dlb.sh prod logs responder`.

## Apanhado da semana

```bash
python3 -m dlb semana --days 7
python3 -m dlb semana --days 7 --out ~/SEMANA.md
```

Lê o que já está no vault e junta num relatório — "o que estudei na semana, para
onde as coisas estão indo".

## Depois de uma sync

O índice do Obsidian tem latência. Rode `obsidian reload`, espere ~3s, e só
então o lint (`obsidian orphans`, `deadends`, `unresolved verbose`).

## Runbook

### `403 Missing Access` no canal

O bot não tem acesso. Diagnostique na ordem:

1. o bot está em **algum** servidor? (`/users/@me/guilds` devolve lista vazia se não)
2. está no servidor **certo**?
3. o canal é privado? entrar no servidor não dá acesso — ver
   [CONFIGURACAO.md](CONFIGURACAO.md#o-discord-a-única-parte-manual), passo 4.

### O sync acha zero link num canal cheio deles

Duas causas, nesta ordem de probabilidade:

1. **Message Content Intent desligado** — as mensagens chegam com o texto vazio.
2. **Os links são postados por um bot** — o extrator descarta bots por padrão.
   Confirme com `--include-bots`; se resolver, ponha `DLB_SYNC_ARGS=--include-bots`.

### `403 ao reagir`

Falta a permissão *Adicionar reações* no canal. **O sync não falha por isso** —
a nota é escrita normalmente, só a marca não sai.

### O container está `unhealthy`

Passaram-se mais de 8h sem uma sync bem-sucedida. `./dlb.sh prod logs` mostra o
motivo. Causas comuns: Discord fora, token revogado, vault desmontado.

### Arquivos com dono `root` no vault

O container está rodando com UID errado. Use o `./dlb.sh`, que passa o seu `id`
para o build e para o run — ou exporte `DLB_UID`/`DLB_GID` à mão.

### Reprocessar um dia inteiro

```bash
./dlb.sh prod run sync --force --day 2026-08-15
```

`--force` reescreve a fonte e a seção do diário, e ignora as marcas.

### O histórico travou

```bash
python3 -m dlb backfill --dry-run     # mostra em que ponto está
cat .state/<canal-id>.json            # backfill_after e backfill_done
python3 -m dlb backfill --restart     # recomeça do zero (o dedup evita duplicata)
```

## Rodar sem Docker

```cron
0 */6 * * *  cd "$HOME"/Developer/discord-link-brain && /usr/bin/python3 -m dlb sync >> /tmp/dlb.log 2>&1
```
