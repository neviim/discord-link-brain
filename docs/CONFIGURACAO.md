# Configuração

Tudo vem de variáveis de ambiente. O `.env` na raiz do projeto preenche o que
não estiver definido no ambiente — **o ambiente sempre vence**, e é isso que
permite ao container apontar `OBSIDIAN_VAULT=/vault` sem que o `.env` do
projeto (que traz o caminho do host) sobrescreva de volta.

```bash
cp .env.example .env
$EDITOR .env
./dlb.sh dev doctor      # confere vault, LLM e acesso ao Discord
```

## O Discord (a única parte manual)

**1. Criar o bot.** <https://discord.com/developers/applications> →
*New Application* → aba *Bot* → *Reset Token* e copie.

**2. Ligar o Message Content Intent.** Mesma aba *Bot* → *Privileged Gateway
Intents* → **Message Content Intent**. Sem isso o campo `content` das mensagens
chega vazio e o pipeline acha zero link num canal cheio deles.

**3. Convidar para o servidor.** Aba *OAuth2 → URL Generator*, escopo `bot`, e
exatamente três permissões:

| Permissão | Bit | Para quê |
|---|---|---|
| View Channel | 1024 | abrir o canal |
| Read Message History | 65536 | ler mensagens passadas — é o que o `sync` faz |
| Add Reactions | 64 | marcar ✅ / 📚 |

Soma: **66624**. Nada além disso — o bot só lê.

**4. Se o canal for privado**, entrar no servidor não basta. Botão direito no
canal → *Editar canal* → *Permissões* → **Adicionar membros ou cargos** →
escolha o bot → marque *Ver canal*, *Ler histórico de mensagens* e *Adicionar
reações*.

**5. Pegar o id do canal.** Discord → Configurações → Avançado → **Modo
desenvolvedor** ligado; botão direito **no canal** (não no servidor) → *Copiar ID*.

> **Token de usuário.** O código aceita `DISCORD_TOKEN_TYPE=user` para ler canais
> aos quais só você tem acesso, mas automatizar uma conta pessoal viola os Termos
> de Serviço do Discord e pode banir a conta. Use bot.

## Variáveis da aplicação

Lidas por `config.py`. Valem no host e dentro do container.

### Discord

| Variável | Padrão | O quê |
|---|---|---|
| `DISCORD_TOKEN` | — | **obrigatória.** Token do bot |
| `DISCORD_TOKEN_TYPE` | `bot` | `bot` ou `user` |
| `DISCORD_CHANNEL_IDS` | — | **obrigatória.** Ids separados por vírgula |
| `DISCORD_CHANNEL_NAMES` | — | apelidos legíveis: `id:nome,id:nome`. Usado no nome dos arquivos |
| `DISCORD_GUILD_IDS` | não | A que servidor cada canal pertence: `id_canal:id_servidor`. Só o `migrar` usa — o `sync` descobre pela API. |

### Vault

| Variável | Padrão | O quê |
|---|---|---|
| `OBSIDIAN_VAULT` | `~/Documents/00_Obsidian-dotcom/dotcom` | Raiz do vault. `~` é expandido |
| `TIMEZONE` | `America/Sao_Paulo` | Define a virada do dia. Link das 22h no Brasil é de hoje, não de amanhã em UTC |

### LLM

| Variável | Padrão | O quê |
|---|---|---|
| `LLM_BACKEND` | `claude-cli` | `claude-cli` · `anthropic` · `none` |
| `LLM_MODEL` | `claude-opus-5` | Modelo usado na classificação |
| `CLAUDE_BIN` | `claude` | Caminho do binário, se não estiver no `PATH` |
| `ANTHROPIC_API_KEY` | — | Só para `LLM_BACKEND=anthropic` |

| Backend | Como funciona |
|---|---|
| `claude-cli` | Chama `claude -p --output-format json`. Usa seu login existente, **sem API key** |
| `anthropic` | SDK oficial + `ANTHROPIC_API_KEY`. Requer `pip install anthropic` |
| `none` | Sem LLM: usa título e descrição da página. Bom para `--dry-run` e teste |

### Enriquecimento

| Variável | Padrão | O quê |
|---|---|---|
| `ENRICH_LINKS` | `1` | Abrir as páginas para descobrir do que tratam |
| `ENRICH_TIMEOUT` | `10` | Segundos por página |

### Notas atômicas

| Variável | Padrão | O quê |
|---|---|---|
| `LINK_NOTES` | `1` | Escrever uma nota por link em `50_LINKS/` |

Com `LINK_NOTES=1` o bullet do diário fica magro e aponta para a nota; com `0` ele
continua rico, com resumo e conceitos embutidos, como era antes da camada existir.
Desligar não apaga nada do que já foi escrito.

#### O link para a mensagem original

`GET /channels/{id}/messages` **não** devolve `guild_id` em cada mensagem. O `sync`
resolve isso com uma consulta por canal por execução; o `migrar` é offline por
desenho e depende de `DISCORD_GUILD_IDS`.

Sem essa config, captura antiga fica com `channels/@me/...` — que não abre num canal
de servidor. Com ela, `dlb migrar --force` reconstrói o link certo nas notas de
`50_LINKS/`. As notas de `10_FONTES/` **continuam** com o valor como foi capturado:
a fonte não se corrige (invariante 9 do ESQUEMA), a correção mora na camada derivada.

A pasta `50_LINKS/` **não** entra em `validate_vault()`: um vault que ainda não foi
emendado continua funcionando, e a pasta é criada na primeira escrita. Mas antes de
ligar isto num vault novo, emende o `90_META/ESQUEMA.md` dele — `tipo: link` e as
propriedades `triagem`, `dominio` e `categoria` precisam estar registradas.

### Marcação no Discord

| Variável | Padrão | O quê |
|---|---|---|
| `MARK_PROCESSED` | `1` | Reagir nas mensagens resolvidas |
| `PROCESSED_EMOJI` | `✅` | "entrou no vault agora" |
| `DUPLICATE_EMOJI` | `📚` | "você já tinha isto" |

Para o pipeline, **qualquer uma das duas** significa "mensagem resolvida". A
distinção é para o seu olho. Reação de humano nunca conta — só a do próprio bot.

Booleanos aceitam `0`, `false`, `no` como falso; qualquer outra coisa é
verdadeiro.

## Variáveis do container

Lidas pelo `docker-compose.yml` e pelo `docker/scheduler.sh`. Não valem fora do
Docker.

### Agendamento

| Variável | Padrão | O quê |
|---|---|---|
| `DLB_SYNC_EVERY_HOURS` | `6` | De N em N horas **ancorado na meia-noite** |
| `DLB_SYNC_AT` | — | `HH:MM` uma vez por dia (usado só se `EVERY_HOURS` estiver vazio) |
| `DLB_SYNC_INTERVAL` | — | Segundos corridos, sem ancoragem. Vence os dois acima |
| `DLB_SYNC_ARGS` | — | Flags extras para o `sync`, ex.: `--no-enrich` |
| `DLB_RUN_ON_START` | `1` | Roda uma vez ao subir |
| `DLB_HEALTH_TOLERANCE` | `28800` | Segundos sem sync bem-sucedida até `unhealthy` (8h) |
| `DLB_HEARTBEAT` | `/app/.state/heartbeat` | Arquivo que o healthcheck lê |

Precedência: `INTERVAL` > `EVERY_HOURS` > `AT`.

### Histórico

| Variável | Padrão | O quê |
|---|---|---|
| `DLB_BACKFILL` | `0` | Depois de cada sync, mastigar um pedaço do passado |
| `DLB_BACKFILL_DAYS` | `2` | Dias de histórico por execução |

### Caminhos do host (o wrapper resolve sozinho)

O `./dlb.sh` calcula tudo isto a partir do `.env` e do seu `id`. Você só precisa
exportar à mão se for chamar `docker compose` direto.

| Variável | De onde sai |
|---|---|
| `DLB_HOST_VAULT` | `OBSIDIAN_VAULT` do `.env`, com o `~` expandido |
| `DLB_DEV_VAULT` | igual ao anterior, ou uma cópia que você indicar |
| `DLB_DEV_VAULT_MODE` | `ro` por padrão; `rw` com `./dlb.sh dev --rw` |
| `DLB_UID` / `DLB_GID` | seu `id -u` / `id -g` |
| `DLB_CLAUDE_BIN` | `readlink -f` do binário do `claude` |
| `DLB_CLAUDE_HOME` | `~/.claude` — só montado se `LLM_BACKEND=claude-cli` |
| `MINDWALK_BIN` | opcional, para `./dlb.sh map` |

## Conferindo

```bash
./dlb.sh dev doctor
```

```
vault           /vault
                estrutura OK (10_FONTES, 20_WIKI, 30_MOC, 40_DIARIO, 90_META)
llm backend     claude-cli (modelo claude-opus-5)
fuso            America/Sao_Paulo
enriquecimento  ligado
marcação        ✅ arquivado agora · 📚 já documentado  (precisa de 'Adicionar reações')
canal 1414…734  OK — #🔗_arquivar
cursor 1414…734 last_message_id=1538… last_run=2026-08-17T21:54:34+00:00

OK
```

`OK` no fim significa que dá para rodar. Qualquer linha com `FALHA` diz o que
está faltando.
