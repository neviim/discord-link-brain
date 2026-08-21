# Desenvolvimento

Mexer no código, rodar teste, estender.

## Rodando os testes

```bash
./testar.py            # a suíte inteira, módulo por módulo, com resumo legível
./testar.py --help     # todos os modos
```

`./testar.py` é um invólucro sobre o `unittest` — não substitui nada, e cada modo
abaixo mostra a linha crua equivalente. Ele existe por dois motivos: "rodar os
testes" aqui tem seis formas legítimas, e a saída padrão responde *passou?* com uma
palavra no fim de uma parede de pontos. Sem dependência nova: cor e caixa são
escapes ANSI, e degradam para texto puro quando a saída não é terminal.

### As sete formas

| Comando | Quando usar | Equivalente cru |
|---|---|---|
| `./testar.py` | o de todo dia — vê qual **módulo** quebrou | um `python3 -m unittest tests.X` por módulo |
| `./testar.py --um-processo` | o mais rápido (~5 s), sem isolamento | `python3 -m unittest discover -s tests -t .` |
| `./testar.py -m painel -m chat` | mexendo em uma parte | `python3 -m unittest tests.test_painel` |
| `./testar.py -t tests.test_chat.TestFilaDeUm` | uma classe ou um teste só, com saída completa | `python3 -m unittest tests.test_chat.TestFilaDeUm -v` |
| `./testar.py --repetir 10` | **caçar teste instável** | rodar a suíte dez vezes na mão |
| `./testar.py --cobertura` | cobertura por linha, com barra por módulo | o `docker run` com `coverage` de baixo |
| `./testar.py --docker` | como o container roda | `./dlb.sh dev test` |
| `./testar.py --listar` | quantos testes cada arquivo tem (não roda nada) | — |
| `./testar.py --tudo` | antes de um commit grande: host + 3 voltas + cobertura | os três acima em sequência |

`-m` aceita `painel` ou `test_painel`, e é repetível. `--cor sempre|nunca` força a
decisão de cor, que por padrão segue o terminal e respeita `NO_COLOR`.

> O **estilo, formato e método** desta apresentação estão em
> [PADRAO_SAIDA_DE_TESTES.md](PADRAO_SAIDA_DE_TESTES.md) — princípios, gramática
> visual, as três armadilhas de implementação e como refazer isso em outro projeto,
> inclusive fora de Python. Este `testar.py` é uma implementação daquele padrão.

### Como saber que passou

O último bloco responde isso a um metro de distância, e o **código de saída**
acompanha — dá para usar em script e em CI:

```
╭──────────────────────────────────────────────────────╮
│ ✔  PASSOU                                            │
├──────────────────────────────────────────────────────┤
│ 810 testes, nenhuma falha · 5.1s                     │
╰──────────────────────────────────────────────────────╯
```

| O que você vê | Código | Significa |
|---|---|---|
| `✔  PASSOU` (verde) | **0** | todos rodaram e nenhum quebrou |
| `✘  FALHOU` (vermelho) | **1** | tem teste quebrado, e os nomes vêm listados com o trecho da asserção |
| `⚠  NADA RODOU` (amarelo) | **2** | o comando não chegou a rodar a suíte — falta `.env`, imagem não construída, nome de módulo errado |

O terceiro caso existe porque a primeira versão deste script imprimia
`✔ PASSOU · 0 testes` quando o `./dlb.sh dev test` abortava por falta de `.env`.
**Saída que mente é pior que saída feia**: zero teste executado não é sucesso.

Por módulo, a linha diz o que interessa sem abrir nada:

```
  ✔ test_painel        68 testes   2.18s
  ✘ test_chat          18 testes   0.18s  2 quebrado(s)
  ✔ test_gestor        37 testes   0.06s  1 pulado(s)
```

`pulado` não é falha: alguns testes se pulam quando a ferramenta que eles auditam
não está na máquina — o do `.gitignore` precisa do `git`, que **não existe na imagem
`dev`**. Pular é honesto; reimplementar a semântica do `.gitignore` para não depender
do `git` seria trocar a autoridade por um palpite.

### Ainda dá para chamar o `unittest` direto

O script não esconde nada. Estas linhas continuam valendo, e são o que ele executa:

```bash
python3 -m unittest discover -s tests -t .                  # tudo, no host
python3 -m unittest tests.test_classify                     # um arquivo
python3 -m unittest tests.test_classify.TestParseJsonArray  # uma classe
python3 -m unittest tests.test_bordas.TestSnowflake.test_epoch_do_discord_e_o_snowflake_zero -v
./dlb.sh dev test                                           # dentro do container
```

`OK` no fim é o que importa. Falha aparece como `FAILED (failures=N)` com arquivo,
linha e o que era esperado.

## O que cada arquivo cobre

| Arquivo | Testes | Cobre |
|---|---|---|
| `test_gestor.py` | 37 | **as oito regras do gestor**: uma classe por regra, e cada uma prova que dispara *e* que fica calada |
| `test_config_tela.py` | 78 | **o contrato schema ↔ `.env.example`**, a escrita atômica com rollback, e a fronteira de mutação (CSRF, origem, teto de corpo) |
| `test_dashboard.py` | 22 | **os agregados e as especificações de marca**: cauda somada em "outros", cache por versão, espessura com teto, vão entre segmentos |
| `test_migrar.py` | 41 | o parser do histórico: resumo com travessão, lote repetido, reparo do link da mensagem |
| `test_ressurgir_temas.py` | 38 | carência, semente determinística, stoplist de plataforma, e **não criar nada no vault** |
| `test_sintese.py` | 38 | **a fronteira do LLM na síntese**: id inventado, id vazando na prosa, regeneração |
| `test_nota_de_link.py` | 35 | **o contrato com o ESQUEMA**: vocabulário fechado, nome de arquivo, o que `--force` preserva |
| `test_vault.py` | 32 | as quatro camadas, imutabilidade, idempotência do lote posterior, autoridade do dedup |
| `test_cli.py` | 30 | **a orquestração**: o parser, a semana ISO, a guarda de postar, e o fio entre comando e módulo |
| `test_state_config.py` | 30 | os dois cursores, `.env` vs ambiente, as marcas, e o `.env.example` não ficar para trás |
| `test_responder.py` | 28 | **o bot falando em público**: nunca duas vezes, nunca a si mesmo, marca só depois de postar |
| `test_classify.py` | 27 | **a fronteira do LLM**: resposta suja, vocabulário fechado, link pulado pelo modelo |
| `test_bordas.py` | 24 | emoji em nome de canal, página ilegível, snowflake, paginação do Discord |
| `test_agenda.py` | 27 | **a paridade com o `scheduler.sh`**: executa o Python embutido no shell e compara |
| `test_journal.py` | 27 | **o histórico e o lock**: um escritor por run, recusa em vez de espera, e falha ≠ recusa |
| `test_painel.py` | 68 | **o servidor de verdade**: rota, XSS na ponta, 405 no que escreve, travessia de caminho, recusa fora de loopback |
| `test_render.py` | 17 | **o escape por construção** — a condição da escolha de não usar Jinja |
| `test_acervo.py` | 15 | o memo por `mtime`: editar no Obsidian relê **uma** nota, apagada sai do cache |
| `test_env_compose.py` | 34 | o `.env` lido como o `load_dotenv` lê, e `docker inspect` degradando sem derrubar a tela |
| `test_chat.py` | 18 | **a fila de um**: duas perguntas simultâneas não abrem dois `codex`, e o teto por hora sobrevive a reinício |
| `test_acoes.py` | 24 | **a escrita cirúrgica da triagem** (arquivo idêntico fora de duas linhas) e a lista branca de comandos |
| `test_corpus.py` | 24 | leitura de `50_LINKS/`, e a semana ISO na virada do ano |
| `test_busca.py` | 21 | acento na pergunta, IDF, e **a URL vindo do acervo e não do modelo** |
| `test_retry.py` | 21 | **a escada de 429/5xx**: esperar o certo, desistir na hora certa, e nunca mencionar ninguém |
| `test_extract.py` | 18 | normalização de URL, canonização, fuso, `guild_id`, e o laço do ressurgimento |
| `test_sync.py` | 18 | **o comando que roda sozinho**: a ordem escreve-depois-marca, e o que acontece quando algo explode |
| `test_enrich_rede.py` | 17 | **a superfície hostil**: página que não abre, 50 MB de nada, encoding errado, teto de bytes |
| `test_enrich.py` | 13 | id de vídeo, extração de texto, reconhecimento de marca |
| `test_frontmatter_yaml.py` | 6 | **a citação de escalar**: 24 classes de texto que quebravam o YAML |
| `test_documentacao.py` | 1 | o número de testes anunciado no README contra a suíte real |
| `test_backfill.py` | 4 | **o miolo do backfill**: o dia que vira nota, o servidor no link, e os dois cursores não se pisando |
| `test_segredos.py` | 12 | **o que não pode entrar num repo público**: padrão de credencial, `TOKEN=` com valor, `.gitignore` cobrindo `.env.bak`, rede privada, id real do Discord, padrão de fuso e de caminho, perfil em `User-Agent` |
| **total** | **845** | 89% das linhas de `dlb/` |

Nenhum teste toca a rede, o Discord ou o vault real — todos usam diretório
temporário e dublês.

## Cobertura

```bash
docker run --rm -v "$PWD:/app" -w /app --entrypoint sh discord-link-brain:dev -c \
  "pip install --quiet coverage; \
   python -m coverage run --source=dlb -m unittest discover -s tests -t .; \
   python -m coverage report -m"
```

Estado atual:

```
dlb/config.py         100%
dlb/chat.py           100%
dlb/acoes.py           97%
dlb/env_io.py          99%
dlb/painel_schema.py   95%
dlb/compose.py        100%
dlb/render.py         100%
dlb/agenda.py          99%
dlb/gestor.py          99%
dlb/journal.py         97%
dlb/acervo.py          95%
dlb/painel.py          90%
dlb/ressurgir.py      100%
dlb/state.py          100%
dlb/extract.py         98%
dlb/corpus.py          97%
dlb/enrich.py          96%
dlb/migrar.py          96%
dlb/vault.py           96%
dlb/busca.py           94%
dlb/temas.py           93%
dlb/models.py          93%
dlb/frontmatter.py     91%
dlb/responder.py       89%
dlb/discord_client.py  88%
dlb/classify.py        72%
dlb/sintese.py         68%   ← o que falta é caminho de subprocess
dlb/cli.py             68%   ← falta doctor/laço; ver DECISOES.md
TOTAL                  89%
```

Use `--source=dlb`, não o padrão: sem isso o coverage só reporta módulos que
foram importados, e um módulo com **zero** teste simplesmente não aparece na
tabela — foi assim que `classify.py`, `config.py` e `state.py` passaram
despercebidos por um tempo.

## Convenções

**Testes em português, nomeados pelo comportamento garantido.** `test_link_pulado_pelo_modelo_ainda_vira_nota` diz o que quebra se falhar;
`test_classify_2` não diz nada.

**Cada teste com uma razão de existir.** Se você não consegue escrever numa
frase o que ele impede, provavelmente ele não impede nada.

**Comentário explica o porquê, não o quê.** O código já diz o que faz.

**Dublê em vez de rede.** Para testar o LLM, troque `_run_claude_cli` por uma
função que devolve o JSON que você quer testar:

```python
def setUp(self):
    import dlb.classify as mod
    self.mod, self._original = mod, mod._run_claude_cli

def tearDown(self):
    self.mod._run_claude_cli = self._original
```

Para testar a paginação do Discord, troque `client._get`:

```python
c = DiscordClient("Bot x")
c._get = lambda path, params=None: lote_falso
```

> **Cuidado com o `.env` real.** `Config.from_env()` lê o `.env` do projeto, e
> `load_dotenv` usa `setdefault` — então teste que espera uma variável **ausente**
> passa na sua máquina e falha em CI, ou o contrário. Neutralize
> `dlb.config.load_dotenv` nesses casos; `TestConfig` mostra como.

## Estendendo

### Acrescentar uma área do vault

`AREAS` em `models.py` é o vocabulário fechado. Mas **mude o `ESQUEMA.md` do
vault primeiro** — a regra é dele, não desta ferramenta. Valor que o LLM
inventar fora da lista cai para `area/ia`.

### Acrescentar uma propriedade à nota de link

Três passos, nesta ordem, e o primeiro não é código:

1. **Emende o `90_META/ESQUEMA.md` do vault.** Nome, tipo, se é obrigatória e o
   vocabulário. Sem isso você está abrindo exceção silenciosa num contrato.
2. `frontmatter.KEY_ORDER` — a ordem canônica de escrita. Chave que não está lá
   sai no fim, em ordem alfabética.
3. `Vault._meta_link()` em `vault.py`. Se for propriedade que o **humano** edita,
   acrescente-a à lista preservada em `_reescrever_link()`, senão `--force`
   apaga o trabalho dele.

Se o valor vier do LLM, valide contra vocabulário fechado em `classify.py`, como
`categoria` e `area` já fazem. Se der para computar localmente — `dominio` sai de
`host_of()` — **compute**, não peça ao modelo.

### Acrescentar um site à canonização

Em `extract.py`, junto de `YOUTUBE_HOSTS` e `X_HOSTS`. Duas coisas: o conjunto
de domínios equivalentes e a forma canônica. Escreva o teste primeiro — é uma
função pura, o teste sai em três linhas.

### Acrescentar um backend de síntese

Uma função `(cfg, prompt, system) -> str` em `sintese.py` e um `elif` em
`_despachar`. O parsing, a validação de `id` e a montagem dos wikilinks já valem
para todos os backends — é ali que a fronteira do LLM mora, não no runner.

Os runners da síntese são separados dos de `classify.py` de propósito: lá o
`SYSTEM_PROMPT` está embutido na função, e parametrizá-lo quebraria os 27 testes
que trocam `_run_claude_cli` como global do módulo. Quando aparecer um terceiro
consumidor de LLM, é a hora de extrair um `llm.py` — não antes.

### Acrescentar um backend de LLM

`classify.py` tem o despacho em `classify()`. Um backend novo é uma função
`(cfg, prompt) -> str` que devolve o texto bruto; o parsing e a validação de
vocabulário já estão prontos e valem para todos.

### Acrescentar um canal

`DISCORD_CHANNEL_IDS` aceita vários separados por vírgula, e cada canal tem
cursor próprio em `.state/`. Nada mais precisa mudar.

## Fluxo sugerido

```bash
./dlb.sh dev test                        # verde antes de mexer
# … edita …
python3 -m unittest tests.test_arquivo   # o teste do que você mexeu
./dlb.sh dev test                        # a suíte inteira
./dlb.sh dev sync --dry-run --since 1    # contra o Discord de verdade, sem escrever
./dlb.sh prod up                         # reconstrói a imagem e recria o container
```

O último passo importa: `prod restart` **não** aplica mudança de código ou de
configuração — reaproveita o container antigo.

## Mapa 3D do código

```bash
./dlb.sh map        # citymap deste repositório
./dlb.sh sessions   # sessões de agente gravadas
```

Requer [mindwalk](https://github.com/cosmtrek/mindwalk) instalado. Ele associa
sessão a repositório pelo `cwd` — sessão conduzida de outro diretório aparece na
lista, mas mostra o mapa do *cwd* dela.
