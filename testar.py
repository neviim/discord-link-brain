#!/usr/bin/env python3
"""Rodar os testes deste projeto de todas as formas, com saída legível.

    ./testar.py                    a suíte inteira, módulo por módulo
    ./testar.py --help             todos os modos

Existe porque "rodar os testes" aqui tem mais de uma forma legítima — no host, no
container, com cobertura, um arquivo só, repetido para caçar teste instável — e cada
uma tinha uma linha de comando diferente para decorar. E porque a saída padrão do
`unittest` responde "passou?" com uma palavra no fim de uma parede de pontos: com 31
módulos e 810 testes, o que quebrou fica escondido.

**Sem dependência nova.** O projeto tem `requests` e `PyYAML` em produção e recusou
275 MB de Ollama por recall melhor; um runner de teste não vai ser a porta de entrada
do `rich`. Cor e caixa aqui são meia dúzia de escapes ANSI e cinco caracteres de
box-drawing, e degradam para texto puro quando a saída não é terminal — que é
exatamente o caso quando alguém redireciona para arquivo ou roda em CI.

Cada módulo roda no **próprio processo**. Custa uns segundos a mais e paga: os
testes deste repo trocam globais de módulo por dublê (`_run_claude_cli`, `busca.buscar`,
`compose.inspecionar`), e isolamento por processo impede que um esquecimento de
`tearDown` num arquivo apareça como falha em outro. Quem quer o caminho mais curto
usa `--um-processo`.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
TESTES = RAIZ / "tests"

# ------------------------------------------------------------------ aparência


class Cor:
    """Escapes ANSI, ou string vazia quando não há terminal para colorir.

    Respeita `NO_COLOR` (convenção de fato) e `TERM=dumb`, e desliga sozinho quando
    a saída é redirecionada — senão o arquivo de log fica cheio de `\\x1b[32m`.
    """

    def __init__(self, ativo: bool):
        def faz(codigo: str) -> str:
            return f"\033[{codigo}m" if ativo else ""

        self.ativo = ativo
        self.zero = faz("0")
        self.forte = faz("1")
        self.fraco = faz("2")
        self.verde = faz("32")
        self.vermelho = faz("31")
        self.amarelo = faz("33")
        self.azul = faz("36")
        self.roxo = faz("35")


def decidir_cor(forcar: str) -> bool:
    if forcar == "sempre":
        return True
    if forcar == "nunca":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty()


def largura() -> int:
    """Largura útil, com teto: linha de 200 colunas cansa mais do que ajuda."""
    return min(shutil.get_terminal_size((80, 24)).columns, 100)


class Tela:
    def __init__(self, cor: Cor):
        self.c = cor
        self.w = largura()

    def caixa(self, titulo: str, linhas: list[str], tom: str = "") -> None:
        c = self.c
        borda = tom or c.fraco
        print(f"{borda}╭{'─' * (self.w - 2)}╮{c.zero}")
        print(f"{borda}│{c.zero} {c.forte}{titulo}{c.zero}{' ' * max(0, self.w - 4 - _vis(titulo))} {borda}│{c.zero}")
        if linhas:
            print(f"{borda}├{'─' * (self.w - 2)}┤{c.zero}")
        for linha in linhas:
            print(f"{borda}│{c.zero} {linha}{' ' * max(0, self.w - 4 - _vis(linha))} {borda}│{c.zero}")
        print(f"{borda}╰{'─' * (self.w - 2)}╯{c.zero}")

    def regua(self, texto: str = "") -> None:
        c = self.c
        if not texto:
            print(f"{c.fraco}{'─' * self.w}{c.zero}")
            return
        # "── " + texto + " " + enfeite = self.w exatamente. Errar por um deixa a
        # régua estourando a caixa, e aí a tela parece torta sem motivo aparente.
        enfeite = "─" * max(0, self.w - _vis(texto) - 4)
        print(f"\n{c.fraco}──{c.zero} {c.forte}{texto}{c.zero} {c.fraco}{enfeite}{c.zero}")


_ANSI = re.compile(r"\033\[[0-9;]*m")


def _vis(texto: str) -> int:
    """Largura visível: desconta os escapes ANSI, que não ocupam coluna."""
    return len(_ANSI.sub("", texto))


# -------------------------------------------------------------------- modelo


@dataclass
class Resultado:
    nome: str
    total: int = 0
    falhas: int = 0
    erros: int = 0
    pulados: int = 0
    segundos: float = 0.0
    saida: str = ""
    quebrados: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.falhas == 0 and self.erros == 0

    @property
    def ruins(self) -> int:
        return self.falhas + self.erros


_RAN = re.compile(r"^Ran (\d+) tests? in ([\d.]+)s", re.M)
_CONTA = re.compile(r"(failures|errors|skipped)=(\d+)")
_QUEBRADO = re.compile(r"^(?:FAIL|ERROR): (\S+)", re.M)


def interpretar(nome: str, saida: str, segundos: float) -> Resultado:
    """Lê a saída do `unittest`. Formato estável há duas décadas."""
    r = Resultado(nome=nome, segundos=segundos, saida=saida)
    if (m := _RAN.search(saida)) :
        r.total = int(m.group(1))
    for chave, valor in _CONTA.findall(saida):
        setattr(r, {"failures": "falhas", "errors": "erros", "skipped": "pulados"}[chave], int(valor))
    r.quebrados = _QUEBRADO.findall(saida)
    return r


def rodar(argumentos: list[str], mostrar: bool = False) -> tuple[str, float, int]:
    t0 = time.monotonic()
    processo = subprocess.run(
        [sys.executable, "-m", "unittest", *argumentos],
        cwd=str(RAIZ),
        capture_output=not mostrar,
        text=True,
        check=False,
    )
    saida = "" if mostrar else (processo.stdout or "") + (processo.stderr or "")
    return saida, time.monotonic() - t0, processo.returncode


def modulos() -> list[str]:
    return sorted(p.stem for p in TESTES.glob("test_*.py"))


# -------------------------------------------------------------------- modos


def modo_por_modulo(tela: Tela, alvos: list[str]) -> list[Resultado]:
    """Um processo por módulo, com a linha saindo assim que cada um termina.

    Imprimir no fim seria mais fácil e pior: com 31 módulos, ver o progresso é a
    diferença entre "está rodando" e "travou".
    """
    c = tela.c
    resultados: list[Resultado] = []
    maior = max((len(a) for a in alvos), default=10)
    for alvo in alvos:
        _provisorio(c, f"  {c.fraco}·{c.zero} {alvo:<{maior}} {c.fraco}rodando…{c.zero}")
        saida, seg, _ = rodar([f"tests.{alvo}"])
        r = interpretar(alvo, saida, seg)
        resultados.append(r)
        print(f"  {_glifo(c, r.ok)} {alvo:<{maior}} {_barra(c, r)}")
    return resultados


def _provisorio(c: Cor, texto: str) -> None:
    """Escreve uma linha que vai ser sobrescrita pelo resultado.

    `\\r` sozinho volta o cursor e **não apaga** o que estava lá: o resultado
    ficava colado no "rodando…". `\\033[K` limpa até o fim da linha. Sem terminal
    não escreve nada — em arquivo de log, linha provisória é só ruído.
    """
    if c.ativo:
        print(f"{texto}\033[K", end="\r", flush=True)


def _glifo(c: Cor, ok: bool) -> str:
    return f"{c.verde}✔{c.zero}" if ok else f"{c.vermelho}✘{c.zero}"


def _barra(c: Cor, r: Resultado) -> str:
    partes = [f"{c.fraco}{r.total:>4} testes{c.zero}", f"{c.fraco}{r.segundos:>5.2f}s{c.zero}"]
    if r.pulados:
        partes.append(f"{c.amarelo}{r.pulados} pulado(s){c.zero}")
    if not r.ok:
        partes.append(f"{c.vermelho}{r.ruins} quebrado(s){c.zero}")
    return "  ".join(partes)


def modo_um_processo(tela: Tela) -> list[Resultado]:
    saida, seg, _ = rodar(["discover", "-s", "tests", "-t", "."])
    r = interpretar("suíte inteira", saida, seg)
    print(f"  {_glifo(tela.c, r.ok)} {r.total} testes em {r.segundos:.2f}s")
    return [r]


def modo_repetir(tela: Tela, vezes: int) -> list[Resultado]:
    """Roda a suíte N vezes e aponta quem falhou **em alguma** delas.

    É o modo que faltava quando um teste falhou uma vez em cinco e eu quase aceitei
    como azar: teste instável não é ruído, é ambiguidade — ele para de dizer se o
    código está certo.
    """
    c = tela.c
    resultados: list[Resultado] = []
    instaveis: dict[str, int] = {}
    for volta in range(1, vezes + 1):
        _provisorio(c, f"  {c.fraco}·{c.zero} volta {volta}/{vezes} {c.fraco}rodando…{c.zero}")
        saida, seg, _ = rodar(["discover", "-s", "tests", "-t", "."])
        r = interpretar(f"volta {volta}", saida, seg)
        resultados.append(r)
        for quebrado in r.quebrados:
            instaveis[quebrado] = instaveis.get(quebrado, 0) + 1
        print(f"  {_glifo(c, r.ok)} volta {volta}/{vezes}   {_barra(c, r)}")
    if instaveis:
        tela.regua("quem falhou em alguma volta")
        for nome, quantas in sorted(instaveis.items(), key=lambda kv: -kv[1]):
            print(f"  {c.vermelho}✘{c.zero} {nome} {c.fraco}({quantas}/{vezes} voltas){c.zero}")
        print(
            f"\n  {c.amarelo}Teste que falha às vezes não é azar: é ambiguidade.{c.zero}\n"
            f"  {c.fraco}Ele para de dizer se o código está certo — conserte o teste ou o código.{c.zero}"
        )
    return resultados


def modo_cobertura(tela: Tela) -> list[Resultado]:
    """Cobertura pela imagem `dev`, que é onde o `coverage` está instalado.

    `--source=dlb` não é opcional: sem ele, módulo com **zero** teste simplesmente
    não aparece na tabela, e foi assim que `classify.py`, `config.py` e `state.py`
    passaram despercebidos por um tempo (ver docs/DESENVOLVIMENTO.md).
    """
    c = tela.c
    if not shutil.which("docker"):
        print(f"  {c.amarelo}docker não está no PATH — a cobertura roda na imagem dev.{c.zero}")
        return []
    comando = (
        "pip install --quiet coverage 2>/dev/null; "
        # Sem `tail`: cortar a saída do unittest esconderia justamente as linhas
        # `FAIL:`/`ERROR:` que o desfecho precisa para dizer **o que** quebrou.
        # Aconteceu: a cobertura reportou "4 quebrados" e o bloco veio vazio.
        "python -m coverage run --source=dlb -m unittest discover -s tests -t . 2>&1; "
        "python -m coverage report -m"
    )
    t0 = time.monotonic()
    processo = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{RAIZ}:/app", "-w", "/app",
         "--entrypoint", "sh", "discord-link-brain:dev", "-c", comando],
        capture_output=True, text=True, check=False,
    )
    saida = (processo.stdout or "") + (processo.stderr or "")
    if processo.returncode != 0 and "Ran " not in saida:
        print(f"  {c.vermelho}não consegui rodar na imagem dev.{c.zero} "
              f"{c.fraco}Construa com `./dlb.sh dev build`.{c.zero}")
        print("\n".join(f"  {c.fraco}{l}{c.zero}" for l in saida.splitlines()[-5:]))
        return []
    r = interpretar("cobertura", saida, time.monotonic() - t0)
    print(f"  {_glifo(c, r.ok)} {r.total} testes em {r.segundos:.1f}s {c.fraco}(no container){c.zero}\n")
    _tabela_cobertura(tela, saida)
    return [r]


def _tabela_cobertura(tela: Tela, saida: str) -> None:
    """Reimprime a tabela do coverage ordenada e com cor por faixa."""
    c = tela.c
    linhas = []
    total = None
    for linha in saida.splitlines():
        m = re.match(r"^(dlb/\S+|TOTAL)\s+(\d+)\s+(\d+)\s+(\d+)%", linha)
        if not m:
            continue
        nome, _stmts, _faltando, pct = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if nome == "TOTAL":
            total = pct
        else:
            linhas.append((pct, nome))
    if not linhas:
        return
    maior = max(len(n) for _, n in linhas)
    for pct, nome in sorted(linhas):
        tom = c.verde if pct >= 90 else (c.amarelo if pct >= 70 else c.vermelho)
        cheio = round(pct / 5)
        barra = f"{tom}{'█' * cheio}{c.fraco}{'░' * (20 - cheio)}{c.zero}"
        print(f"  {nome:<{maior}}  {barra} {tom}{pct:>3}%{c.zero}")
    if total is not None:
        tom = c.verde if total >= 85 else c.amarelo
        print(f"  {c.forte}{'TOTAL':<{maior}}{c.zero}  {' ' * 20} {tom}{c.forte}{total:>3}%{c.zero}")


def modo_docker(tela: Tela) -> list[Resultado]:
    c = tela.c
    if not (RAIZ / "dlb.sh").is_file():
        print(f"  {c.vermelho}não achei ./dlb.sh{c.zero}")
        return []
    t0 = time.monotonic()
    processo = subprocess.run(["./dlb.sh", "dev", "test"], cwd=str(RAIZ),
                              capture_output=True, text=True, check=False)
    saida = (processo.stdout or "") + (processo.stderr or "")
    r = interpretar("container", saida, time.monotonic() - t0)
    if r.total == 0:
        # O wrapper aborta antes de chegar ao Python quando falta `.env` ou o vault
        # do host não existe. Mostrar a última linha dele é o que resolve em vez de
        # deixar o leitor adivinhando por que "0 testes".
        # A saída fica no `Resultado`; quem imprime é o `desfecho`, que é o único
        # dono da apresentação de falha. Imprimir aqui também duplicava a linha.
        print(f"  {c.vermelho}✘{c.zero} o container não rodou a suíte")
        return [r]
    print(f"  {_glifo(c, r.ok)} {r.total} testes em {r.segundos:.1f}s {c.fraco}(./dlb.sh dev test){c.zero}")
    return [r]


_CONTAR = (
    "import json, unittest;"
    "carregador = unittest.defaultTestLoader;"
    "print(json.dumps({n: carregador.loadTestsFromName('tests.' + n).countTestCases()"
    " for n in %r}))"
)


def modo_listar(tela: Tela) -> list[Resultado]:
    """Quantos testes cada arquivo tem, **sem rodar nada**.

    A primeira versão rodava a suíte inteira só para contar — 29 processos e uns
    dez segundos para produzir uma lista. `countTestCases()` responde a mesma
    pergunta lendo os módulos, num processo só.
    """
    c = tela.c
    tela.regua("módulos de teste")
    nomes = modulos()
    processo = subprocess.run(
        [sys.executable, "-c", _CONTAR % nomes],
        cwd=str(RAIZ), capture_output=True, text=True, check=False,
    )
    try:
        import json

        contagens = [(v, k) for k, v in json.loads(processo.stdout).items()]
    except (ValueError, TypeError):
        print(f"  {c.vermelho}não consegui contar:{c.zero} {processo.stderr.strip()[:200]}")
        return []
    maior = max(len(n) for _, n in contagens)
    for total, nome in sorted(contagens, reverse=True):
        largura_barra = round(total / max(t for t, _ in contagens) * 30)
        print(f"  {nome:<{maior}}  {c.azul}{'▇' * largura_barra}{c.zero} {total}")
    print(f"\n  {c.forte}{sum(t for t, _ in contagens)} testes{c.zero} em {len(contagens)} módulos")
    return []


# ------------------------------------------------------------------ desfecho


def desfecho(tela: Tela, resultados: list[Resultado], segundos: float) -> int:
    """O bloco final. Tem de responder "passou?" a um metro de distância."""
    c = tela.c
    if not resultados:
        return 0
    total = sum(r.total for r in resultados)
    ruins = sum(r.ruins for r in resultados)
    pulados = sum(r.pulados for r in resultados)
    quebrados = [q for r in resultados for q in r.quebrados]

    # Zero teste rodado não é sucesso — é o runner não tendo conseguido rodar.
    # A primeira versão imprimia "✔ PASSOU · 0 testes" quando o `./dlb.sh dev test`
    # abortava por falta de `.env`, e saída que mente é pior que saída feia.
    if total == 0 and not ruins:
        tela.caixa(
            "⚠  NADA RODOU",
            [
                f"{c.amarelo}nenhum teste foi executado{c.zero} em {segundos:.1f}s",
                f"{c.fraco}o comando não chegou a rodar a suíte — veja a saída acima{c.zero}",
            ],
            tom=c.amarelo,
        )
        for r in resultados:
            for linha in (r.saida or "").strip().splitlines()[-6:]:
                # A saída vem de outro programa (`dlb.sh`, docker) e pode trazer
                # cor própria. Deixá-la passar num modo `--cor nunca` contradiz a
                # promessa da flag.
                print(f"  {c.fraco}{_ANSI.sub('', linha) if not c.ativo else linha}{c.zero}")
        return 2

    if ruins:
        tela.regua("o que quebrou")
        for r in resultados:
            if r.ok:
                continue
            for linha in _trecho_da_falha(r.saida):
                print(f"  {linha}")
        print()
        tela.caixa(
            "✘  FALHOU",
            [
                f"{c.vermelho}{ruins} de {total} testes quebrados{c.zero} em {segundos:.1f}s",
                *[f"{c.vermelho}·{c.zero} {q}" for q in quebrados[:12]],
                *([f"{c.fraco}… e mais {len(quebrados) - 12}{c.zero}"] if len(quebrados) > 12 else []),
            ],
            tom=c.vermelho,
        )
        return 1

    detalhe = f"{c.verde}{total} testes, nenhuma falha{c.zero} · {segundos:.1f}s"
    if pulados:
        detalhe += f" · {c.amarelo}{pulados} pulado(s){c.zero}"
    tela.caixa("✔  PASSOU", [detalhe], tom=c.verde)
    return 0


def _trecho_da_falha(saida: str, maximo: int = 14) -> list[str]:
    """As linhas que importam de um traceback: o nome, o arquivo e a asserção.

    O `unittest` imprime o traceback inteiro, e o meio dele quase nunca é o que
    explica — o que explica é a última linha e o `File` mais próximo do teste.
    """
    guardar: list[str] = []
    dentro = False
    for linha in saida.splitlines():
        if re.match(r"^(FAIL|ERROR): ", linha):
            dentro = True
            guardar.append(linha)
            continue
        if dentro:
            if linha.startswith("Ran ") or linha.startswith("OK"):
                dentro = False
                continue
            if linha.strip() and not linha.startswith("=") and not linha.startswith("-"):
                guardar.append("  " + linha.strip())
        if len(guardar) >= maximo:
            guardar.append("  …")
            break
    return guardar


# --------------------------------------------------------------------- main


def cabecalho(tela: Tela, modo: str) -> None:
    c = tela.c
    commit = "?"
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(RAIZ),
                                capture_output=True, text=True, check=False).stdout.strip() or "?"
    except OSError:
        pass
    sujo = subprocess.run(["git", "status", "--porcelain"], cwd=str(RAIZ),
                          capture_output=True, text=True, check=False).stdout.strip()
    tela.caixa(
        "discord-link-brain · testes",
        [
            f"{c.fraco}modo{c.zero}    {modo}",
            f"{c.fraco}commit{c.zero}  {commit}" + (f"  {c.amarelo}(árvore com mudanças){c.zero}" if sujo else ""),
            f"{c.fraco}python{c.zero}  {sys.version.split()[0]}  ·  {len(modulos())} módulos de teste",
        ],
        tom=c.azul,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="./testar.py",
        description="Roda os testes do discord-link-brain e mostra o resultado de forma legível.",
        epilog=(
            "exemplos:\n"
            "  ./testar.py                       a suíte inteira, módulo por módulo\n"
            "  ./testar.py --um-processo         o mais rápido (uma chamada só)\n"
            "  ./testar.py -m painel -m chat     só esses módulos\n"
            "  ./testar.py -t tests.test_chat.TestFilaDeUm\n"
            "  ./testar.py --repetir 10          caça teste instável\n"
            "  ./testar.py --cobertura           cobertura, na imagem dev\n"
            "  ./testar.py --docker              a suíte dentro do container\n"
            "  ./testar.py --listar              quantos testes por arquivo\n"
            "  ./testar.py --tudo                host + repetição + cobertura\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-m", "--modulo", action="append", default=[], metavar="NOME",
                   help="um módulo (aceita `painel` ou `test_painel`, repetível)")
    p.add_argument("-t", "--teste", metavar="CAMINHO",
                   help="um teste ou classe: tests.test_chat.TestFilaDeUm[.test_x]")
    p.add_argument("--um-processo", action="store_true",
                   help="a suíte numa chamada só — o mais rápido, sem isolamento")
    p.add_argument("--repetir", type=int, metavar="N", help="roda a suíte N vezes e aponta os instáveis")
    p.add_argument("--cobertura", action="store_true", help="cobertura por linha, na imagem dev")
    p.add_argument("--docker", action="store_true", help="a suíte dentro do container (./dlb.sh dev test)")
    p.add_argument("--listar", action="store_true", help="só lista os módulos e quantos testes têm")
    p.add_argument("--tudo", action="store_true", help="host, três repetições e cobertura")
    p.add_argument("--cor", choices=("auto", "sempre", "nunca"), default="auto")
    args = p.parse_args(argv)

    tela = Tela(Cor(decidir_cor(args.cor)))
    t0 = time.monotonic()
    resultados: list[Resultado] = []

    if args.listar:
        cabecalho(tela, "listar")
        modo_listar(tela)
        return 0

    if args.teste:
        cabecalho(tela, f"um teste · {args.teste}")
        tela.regua("saída do unittest")
        _, seg, codigo = rodar([args.teste, "-v"], mostrar=True)
        tela.caixa(
            "✔  PASSOU" if codigo == 0 else "✘  FALHOU",
            [f"{args.teste} · {seg:.2f}s"],
            tom=tela.c.verde if codigo == 0 else tela.c.vermelho,
        )
        return codigo

    modo = "tudo" if args.tudo else (
        "cobertura" if args.cobertura else
        "container" if args.docker else
        f"repetir {args.repetir}" if args.repetir else
        "um processo" if args.um_processo else
        "por módulo"
    )
    cabecalho(tela, modo)

    if args.modulo:
        alvos = []
        for bruto in args.modulo:
            nome = bruto if bruto.startswith("test_") else f"test_{bruto}"
            if nome not in modulos():
                print(f"  {tela.c.vermelho}não achei tests/{nome}.py{tela.c.zero}")
                print(f"  {tela.c.fraco}use --listar para ver os nomes{tela.c.zero}")
                return 2
            alvos.append(nome)
        tela.regua("módulos escolhidos")
        resultados += modo_por_modulo(tela, alvos)
    elif args.cobertura:
        tela.regua("cobertura por linha")
        resultados += modo_cobertura(tela)
    elif args.docker:
        tela.regua("dentro do container")
        resultados += modo_docker(tela)
    elif args.repetir:
        tela.regua(f"{args.repetir} voltas da suíte")
        resultados += modo_repetir(tela, args.repetir)
    elif args.um_processo:
        tela.regua("suíte inteira, um processo")
        resultados += modo_um_processo(tela)
    elif args.tudo:
        tela.regua("suíte inteira, módulo por módulo")
        resultados += modo_por_modulo(tela, modulos())
        tela.regua("3 voltas, caçando instável")
        resultados += modo_repetir(tela, 3)
        tela.regua("cobertura por linha")
        resultados += modo_cobertura(tela)
    else:
        tela.regua("suíte inteira, módulo por módulo")
        resultados += modo_por_modulo(tela, modulos())

    print()
    return desfecho(tela, resultados, time.monotonic() - t0)


if __name__ == "__main__":
    raise SystemExit(main())
