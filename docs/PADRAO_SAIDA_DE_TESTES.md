# Padrão de apresentação de testes

Estilo, formato e método da saída do `./testar.py` — escritos para serem
**reaproveitados em outro projeto**, inclusive fora de Python.

Como montar, em qualquer projeto, um runner de teste cuja saída responde **passou?**
a um metro de distância — e cujo silêncio não pode ser confundido com sucesso.

Referência extraída do `testar.py` na raiz deste projeto (586 linhas, stdlib
pura), depois de o padrão ser construído, quebrado três vezes e consertado.
Este documento é o **método**; o script é uma implementação dele.

---

## 1. Os cinco princípios, em ordem de importância

**1. Saída que mente é pior que saída feia.** É a regra que domina todas as
outras. A primeira versão do runner imprimia `✔ PASSOU · 0 testes` quando o
comando abortava antes de rodar a suíte — o wrapper do container saía por falta de
`.env` e o resumo dizia que estava tudo bem. Zero teste executado **não é sucesso**:
é um terceiro estado, e precisa de nome e de cor próprios.

**2. Três desfechos, não dois.** `passou` / `falhou` / `nada rodou`. O terceiro
cobre: dependência ausente, imagem não construída, nome de alvo errado, comando que
morreu antes de começar. Sem ele, o runner converte erro de operação em falso verde.

**3. Código de saída é parte da saída.** `0` / `1` / `2`. Sem isso a ferramenta não
serve em `&&`, em pre-commit nem em CI, e a pessoa passa a confiar no que lê em vez
do que o processo devolve.

**4. Zero dependência nova.** Um runner de teste não pode ser a porta de entrada de
uma biblioteca de terminal. Cor é meia dúzia de escapes ANSI; caixa são cinco
caracteres Unicode. Se o projeto já usa `rich`/`colorama`, use — mas não introduza.

**5. Progresso visível.** Com 30 módulos, imprimir tudo no fim é a diferença entre
"está rodando" e "travou". Cada unidade imprime seu resultado quando termina.

---

## 2. A gramática visual

Três elementos, e nada mais. Vocabulário pequeno é o que faz a tela parecer
desenhada em vez de acumulada.

### Caixa — para começo e fim

```
╭──────────────────────────────────────────────────────────╮
│ projeto · testes                                         │
├──────────────────────────────────────────────────────────┤
│ modo    por módulo                                       │
│ commit  dcf5ec0  (árvore com mudanças)                   │
│ python  3.12.3  ·  30 módulos de teste                   │
╰──────────────────────────────────────────────────────────╯
```

Caracteres: `╭ ╮ ╰ ╯ │ ─ ├ ┤`. Bordas arredondadas em vez de `┌`: menos peso
visual, e a mesma largura de célula.

Duas caixas por execução, no máximo: **cabeçalho** (contexto: modo, commit, versão)
e **desfecho**. Caixa no meio vira ruído.

O cabeçalho serve para uma pergunta específica que aparece toda hora: *o que eu
rodei, exatamente?* Commit, versão do runtime, e um aviso de **árvore suja** — porque
"passou no commit X" é falso se havia mudanças não commitadas.

### Régua — para separar fases

```
── suíte inteira, módulo por módulo ────────────────────────
```

Um título, e o resto preenchido até a largura. Mais leve que uma caixa e suficiente
para dizer "outra fase começou".

### Linha de item — uma por unidade

```
  ✔ test_painel          68 testes   2.19s
  ✘ test_chat            18 testes   0.18s  2 quebrado(s)
  ✔ test_gestor          37 testes   0.06s  1 pulado(s)
```

Colunas alinhadas por padding calculado a partir do **maior nome do conjunto**, não
de um número fixo. Glifos: `✔` `✘` `⚠`. Ordem fixa: glifo, nome, contagem, tempo, e
só então o que é anormal.

### Barra — para proporção, não para enfeite

```
  dlb/painel.py    ██████████████████░░  90%
  dlb/gestor.py    ████████████████████  99%
```

Vinte células, `█` cheio e `░` vazio. Serve quando o número tem escala comparável
(cobertura, contagem por arquivo). Não serve para tempo, que é assimétrico.

---

## 3. Cor: função, nunca decoração

| Papel | Cor | Onde |
|---|---|---|
| passou | verde `32` | glifo, borda do desfecho, número final |
| falhou | vermelho `31` | glifo, borda, lista do que quebrou |
| atenção | amarelo `33` | pulados, árvore suja, "nada rodou" |
| contexto | ciano `36` | borda do cabeçalho |
| secundário | dim `2` | rótulos, tempos, régua |

**Quatro cores no máximo.** A quinta deixa de significar algo.

### Degradação — obrigatória, não opcional

```python
def decidir_cor(forcar: str) -> bool:
    if forcar == "sempre":  return True
    if forcar == "nunca":   return False
    if os.environ.get("NO_COLOR"):      return False   # convenção de fato
    if os.environ.get("TERM") == "dumb": return False
    return sys.stdout.isatty()                          # pipe/arquivo → sem cor
```

E uma flag `--cor auto|sempre|nunca`, porque CI às vezes quer cor e terminal às
vezes não quer.

**Cor vazando para log é defeito.** Quando `--cor nunca`, também limpe ANSI que vem
de **outros programas** cuja saída você repassa (o wrapper, o docker). Passei por
isso: o erro do wrapper aparecia como `[31merro:[0m` num modo sem cor.

---

## 4. As três armadilhas de implementação

Custaram tempo. Nenhuma é óbvia lendo o código de outra pessoa.

### 4.1 Largura visível ≠ `len()`

Escape ANSI não ocupa coluna. Alinhar com `len()` numa string colorida entorta tudo.

```python
_ANSI = re.compile(r"\033\[[0-9;]*m")

def _vis(texto: str) -> int:
    return len(_ANSI.sub("", texto))
```

Use `_vis` em **todo** cálculo de padding. E confira a aritmética da régua: `"── "`
+ texto + `" "` + enfeite tem de somar a largura exata. Errar por um deixa a régua
estourando a caixa, e a tela parece torta sem motivo aparente.

### 4.2 `\r` não apaga

`print(..., end="\r")` volta o cursor e **deixa o texto antigo**. A linha
"rodando…" ficava colada no resultado. Precisa de `\033[K` (limpa até o fim da
linha):

```python
def _provisorio(c, texto):
    if c.ativo:                      # sem terminal, linha provisória é só ruído
        print(f"{texto}\033[K", end="\r", flush=True)
```

E **não escreva linha provisória quando não há terminal** — em arquivo de log ela
vira lixo duplicado.

### 4.3 Nunca corte a saída que você vai interpretar

Eu passava a saída do runner por `tail -3` para pegar o `Ran N tests`. Resultado: o
resumo dizia "4 quebrados" e o bloco "o que quebrou" vinha **vazio**, porque as
linhas `FAIL:`/`ERROR:` estavam no pedaço cortado. Capture tudo; corte só na
apresentação.

---

## 5. Método: os modos que valem existir

Não é "quantos modos couberem". Cada modo responde uma pergunta diferente, e um modo
sem pergunta própria é opção a mais para decorar.

| Modo | Pergunta | Nota |
|---|---|---|
| **padrão** (por unidade) | *qual parte quebrou?* | um processo por módulo/pacote |
| `--um-processo` | *o mais rápido possível* | sem isolamento; é o caminho do dia a dia curto |
| `-m <unidade>` | *mexi aqui, isso ainda passa?* | repetível; aceite nome curto e completo |
| `-t <alvo>` | *por que **este** teste falha?* | mostre a saída crua, sem filtrar |
| `--repetir N` | *este teste é instável?* | lista quem falhou em quantas voltas |
| `--cobertura` | *o que não está coberto?* | onde a ferramenta de cobertura estiver |
| `--docker` | *passa como o CI roda?* | o caminho oficial do projeto |
| `--listar` | *quantos testes tem cada arquivo?* | **não roda nada** |
| `--tudo` | *posso commitar isso?* | encadeia padrão + repetir + cobertura |

### Isolamento por processo é decisão de projeto

Vale a pena quando os testes **trocam globais de módulo por dublê** (o caso comum em
Python: substituir `modulo.funcao` no `setUp`). Aí um `tearDown` esquecido num
arquivo aparece como falha em outro, e horas se perdem no arquivo errado. Custo: uns
segundos por inicialização de runtime. Ofereça `--um-processo` para quem quer
velocidade.

### `--repetir` não é luxo

Foi o modo que faltava no dia em que um teste falhou **uma vez em cinco** e eu quase
aceitei como azar. Teste instável não é ruído: é ambiguidade — ele para de dizer se o
código está certo. O modo roda N voltas e imprime quem falhou em quantas, com uma
frase dizendo isso.

### `--listar` não pode rodar a suíte

A primeira versão rodava tudo só para contar: 29 processos e dez segundos para
produzir uma lista. Toda ferramenta de teste sabe contar sem executar
(`countTestCases()`, `--collect-only`, `-list`). Use.

---

## 6. O desfecho, em detalhe

É o único bloco que a maioria vai ler. Ele tem três formas.

```
╭──────────────────────────────────╮   ╭──────────────────────────────────╮
│ ✔  PASSOU                        │   │ ✘  FALHOU                        │
├──────────────────────────────────┤   ├──────────────────────────────────┤
│ 811 testes, nenhuma falha · 8.6s │   │ 2 de 811 testes quebrados em 8.6s│
╰──────────────────────────────────╯   │ · test_que_falha                 │
                                       │ · test_que_estoura               │
                                       ╰──────────────────────────────────╯
```

```
╭──────────────────────────────────────────────────────────╮
│ ⚠  NADA RODOU                                            │
├──────────────────────────────────────────────────────────┤
│ nenhum teste foi executado em 0.0s                       │
│ o comando não chegou a rodar a suíte — veja acima        │
╰──────────────────────────────────────────────────────────╯
```

Regras:

- **O glifo vem antes da palavra.** `✔  PASSOU` é lido de canto de olho; `PASSOU ✔`
  não.
- **Liste os nomes que quebraram**, com teto (12) e um "… e mais N". Nome de teste é
  o que a pessoa vai copiar para rodar de novo.
- **Antes da caixa vermelha, o trecho da falha** — nome, arquivo, linha e a linha da
  asserção. Não o traceback inteiro: o meio dele quase nunca explica.
- **Pulado não é falha.** Conte separado, em amarelo, e diga por quê quando souber.
  Teste que se pula porque a ferramenta auditada não existe naquela máquina é honesto
  — o meu do `.gitignore` precisa de `git`, que não existe na imagem do container.

---

## 7. Como portar para outro projeto

O que é **genérico** (copie o método): princípios, gramática visual, cor e
degradação, as três armadilhas, taxonomia de modos, formato do desfecho, códigos de
saída.

O que é **específico** (reescreva): como invocar a suíte, como interpretar o
resultado, e o que conta como "unidade".

| Ferramenta | Invocar | Interpretar | Contar sem rodar |
|---|---|---|---|
| `unittest` | `python -m unittest tests.X` | regex em `Ran N tests`, `failures=`, `errors=` | `loadTestsFromName(...).countTestCases()` |
| `pytest` | `pytest tests/X.py` | `-q --tb=line`, ou `--junitxml` para dado estruturado | `--collect-only -q` |
| `go test` | `go test ./pkg/...` | `-json` (dado estruturado de fábrica) | `go test -list '.*'` |
| `cargo test` | `cargo test --lib` | `-- --format json -Z unstable-options` | `-- --list` |
| `jest`/`vitest` | `npx jest path` | `--json --outputFile` | `--listTests` |

**Prefira saída estruturada quando existir** (`-json`, `--junitxml`). Regex em texto
funciona — o formato do `unittest` é estável há duas décadas — mas é a segunda
escolha, e ela obriga o cuidado da armadilha 4.3.

> Escopo do que foi medido: **só a linha do `unittest`** foi verificada nesta
> máquina (`countTestCases()` devolveu 37 para um módulo, sem executar nada, e o
> runner reportou os mesmos 37 ao rodar). As outras quatro linhas são conhecimento
> de ferramenta, não medição — confira a flag na versão que você tem antes de
> confiar. `cargo test --format json` em particular exige nightly.

### Checklist de porte

- [ ] Um arquivo, executável, na raiz do projeto, sem dependência nova.
- [ ] `--help` com **exemplos**, não só a lista de flags.
- [ ] Os três desfechos e os códigos 0/1/2.
- [ ] `_vis()` usado em todo padding.
- [ ] `NO_COLOR`, `TERM=dumb`, `isatty()`, e `--cor auto|sempre|nunca`.
- [ ] Linha provisória com `\033[K`, e só em terminal.
- [ ] Captura completa da saída; corte só ao apresentar.
- [ ] `--listar` sem executar testes.
- [ ] Cada modo documentado **com a linha crua equivalente** — o runner não pode
      esconder a ferramenta de baixo, senão vira dependência de si mesmo.
- [ ] Teste com uma falha de propósito antes de dizer que está pronto.

---

## 8. Duas lições que não são sobre visual

**Documente o equivalente cru de cada modo.** Um runner que esconde o comando real
cria dependência: no dia em que ele quebra, ninguém sabe rodar os testes. A tabela
de modos do `DESENVOLVIMENTO.md` tem uma coluna "equivalente cru" por isso.

**Teste o runner com uma falha de verdade.** Escrevi um `tests/test_zz_provisorio.py`
com um `assertEqual(2 + 2, 5)` e um `raise`, olhei a tela, e só então apaguei. Foi aí
que apareceram duas das três armadilhas. Runner de teste que nunca viu uma falha é
runner não testado — e o modo que mais importa é justamente o que você menos exercita.

---

## Onde está a implementação de referência

`testar.py`, na raiz deste projeto — Python 3, stdlib pura, 586 linhas, comentada
com o **porquê** de cada decisão acima. Os modos e o equivalente cru de cada um
estão em [DESENVOLVIMENTO.md](DESENVOLVIMENTO.md).

Este documento é o **método**; o script é uma implementação dele. Se um dia o script
for reescrito, o que vale é o que está aqui.
