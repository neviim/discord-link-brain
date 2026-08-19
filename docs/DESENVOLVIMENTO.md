# Desenvolvimento

Mexer no código, rodar teste, estender.

## Rodando os testes

```bash
./dlb.sh dev test                            # dentro do container (recomendado)
python3 -m unittest discover -s tests -t .   # no host, só precisa de PyYAML
```

Só um pedaço, quando você está mexendo em uma parte:

```bash
python3 -m unittest tests.test_classify                     # um arquivo
python3 -m unittest tests.test_classify.TestParseJsonArray  # uma classe
python3 -m unittest tests.test_bordas.TestSnowflake.test_epoch_do_discord_e_o_snowflake_zero -v
```

`OK` no fim é o que importa. Falha aparece como `FAILED (failures=N)` com
arquivo, linha e o que era esperado.

## O que cada arquivo cobre

| Arquivo | Testes | Cobre |
|---|---|---|
| `test_migrar.py` | 41 | o parser do histórico: resumo com travessão, lote repetido, bloco de candidatos, reparo do link da mensagem |
| `test_nota_de_link.py` | 35 | **o contrato com o ESQUEMA**: vocabulário fechado, nome de arquivo, e o que `--force` preserva do humano |
| `test_vault.py` | 32 | as quatro camadas, imutabilidade, idempotência do lote posterior, autoridade do dedup |
| `test_classify.py` | 27 | **a fronteira do LLM**: resposta suja, vocabulário fechado, link pulado pelo modelo |
| `test_state_config.py` | 27 | os dois cursores, `.env` vs ambiente, as duas marcas, os mapas canal:valor |
| `test_bordas.py` | 24 | emoji em nome de canal, página ilegível, snowflake, paginação do Discord |
| `test_extract.py` | 16 | normalização de URL, canonização YouTube/X, dedup, fuso, `guild_id` no link da mensagem |
| `test_enrich.py` | 13 | id de vídeo, extração de texto, reconhecimento de marca |
| `test_frontmatter_yaml.py` | 6 | **a citação de escalar**: 24 classes de texto que quebravam o YAML em silêncio |
| **total** | **221** | |

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
dlb/state.py          100%
dlb/extract.py         97%
dlb/migrar.py          96%
dlb/vault.py           95%
dlb/models.py          93%
dlb/frontmatter.py     86%
dlb/classify.py        72%
dlb/discord_client.py  50%   ← o que falta é caminho de rede
dlb/enrich.py          35%   ← idem
dlb/cli.py              0%   ← orquestração; ver DECISOES.md
TOTAL                  64%
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
