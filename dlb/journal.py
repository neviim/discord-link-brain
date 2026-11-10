"""Histórico de execução: um diretório por run, e o lock que impede dois syncs.

O que o sistema fez ontem só existia no log do Docker — que rotaciona, não é
consultável e some quando o container é recriado. Qualquer tela de "o que rodou /
o que está pendente" precisa de um registro em disco, e é isto.

**Um diretório por run, escrito só pelo processo daquele run.** É a mesma regra de
`state.py` — *nenhum arquivo pode ter dois escritores* — aplicada de novo, e é o
motivo de não ser um `runs.jsonl` compartilhado: com dois processos anexando
(o laço do container e um run disparado à mão, ou pelo painel), a linha de um
aparece cortada dentro da do outro. Diretório separado por run torna a corrida
impossível por construção, em vez de administrada.

    .state/runs/20260820T001503Z-sync-4127/summary.json

`summary.json` é escrito duas vezes: na abertura com `status: "rodando"`, para
quem estiver olhando ver o run em andamento, e no fim com o resultado. As duas
escritas são do mesmo processo, atômicas via `os.replace`.

O diretório existe desde o dia 1 mesmo com um arquivo só dentro. É de propósito:
quando o passo a passo estruturado (`events.jsonl`) entrar, ele entra ao lado do
`summary.json` sem migrar nada e sem mudar quem lê o resumo.

O lock é o outro assunto do módulo, e está aqui porque nasce junto: se o painel
puder disparar `sync` enquanto o container roda o dele, os dois fazem
read-modify-write no mesmo cursor de canal e a atualização de um se perde — o
canal volta a ser lido do zero, ou pior, um trecho fica sem ser lido. `flock` é
do sistema de arquivos, então vale entre processos que não se conhecem: o do
container e o do host, sobre o mesmo bind mount. **Não vale em NFS** — se
`.state` mudar para rede, este desenho quebra e o aviso está aqui.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Versão do formato de `summary.json`. Quem lê deve tolerar versão maior lendo só
# os campos que conhece — daí o número estar no arquivo desde o primeiro.
VERSAO = 1

# Quantos runs ficam em disco. O ciclo do container roda quatro comandos, quatro
# vezes por dia: sem poda isto passa de 5 mil diretórios por ano, e a tela de
# execuções fica lendo lixo de meses atrás para mostrar as vinte últimas. Não é
# configurável de propósito — é detalhe de implementação do histórico, não
# política que alguém precise ajustar.
MANTER_RUNS = 500

# Qual lock cada comando precisa. `sync` e `backfill` compartilham o cursor de
# canal em `.state/<id>.json` e o dedup por `10_FONTES/`, então dividem o mesmo
# lock: rodar os dois juntos é read-modify-write no mesmo arquivo.
#
# `responder` fica de fora porque tem cursor próprio (`responder-<id>.json`,
# escritor próprio por desenho) e roda de 60 em 60 segundos — travá-lo junto do
# sync faria o bot ficar mudo durante um sync longo, que é caro e desnecessário.
# `migrar` também fica: é offline, manual, e escreve nota idempotente.
LOCK_DE_COMANDO = {"sync": "canais", "backfill": "canais"}

# Comandos que ficam de pé por desenho, em vez de terminar. O journal os marca
# para a tela não confundir "de pé desde ontem" com "job pendurado há 14 horas".
#
# A distinção nasceu de um caso concreto: matar o painel com SIGTERM deixa o
# `summary.json` aberto para sempre, porque sinal não é exceção e o `registrar`
# não o vê. Para `sync` isso é anomalia e a regra `run_pendurado` do gestor
# levanta alerta; para o painel é o normal, e alertar seria o painel reclamando
# de si mesmo.
LONGEVOS = {"painel", "responder"}


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _escrever_json(destino: Path, dados: dict) -> None:
    """Escrita atômica, como em `state.py`: temporário e `os.replace`.

    Sem isto quem lê pode pegar o arquivo truncado no meio da escrita e concluir
    que o run está corrompido — no exato instante em que ele está indo bem.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(destino.suffix + ".tmp")
    temporario.write_text(
        json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporario, destino)


@dataclass
class Run:
    """Uma execução em andamento. Só o processo que a abriu escreve nela."""

    id: str
    comando: str
    argv: list[str]
    diretorio: Path
    inicio: datetime
    _t0: float
    contadores: dict[str, int] = field(default_factory=dict)
    fechado: bool = False

    @property
    def summary(self) -> Path:
        return self.diretorio / "summary.json"

    def contar(self, chave: str, n: int = 1) -> None:
        """Soma no contador. Só memória — vai para o disco no `fechar`.

        Contador não vale um `os.replace` por link: são dezenas por execução, e
        um resumo pela metade não ajuda ninguém. O que precisa ser visível
        durante o run é *que ele está rodando*, e isso o `status: rodando` já diz.
        """
        if n:
            self.contadores[chave] = self.contadores.get(chave, 0) + n

    def _dados(self, status: str, exit_code: int | None, erro: str | None) -> dict:
        fim = None if status == "rodando" else _agora()
        return {
            "versao": VERSAO,
            "id": self.id,
            "comando": self.comando,
            "argv": self.argv,
            "pid": os.getpid(),
            "status": status,
            "longevo": self.comando in LONGEVOS,
            "inicio": self.inicio.isoformat(timespec="seconds"),
            "fim": fim.isoformat(timespec="seconds") if fim else None,
            "duracao_s": None if fim is None else round(time.monotonic() - self._t0, 3),
            "exit_code": exit_code,
            "erro": erro,
            "contadores": dict(sorted(self.contadores.items())),
        }

    def abrir_no_disco(self) -> None:
        _escrever_json(self.summary, self._dados("rodando", None, None))

    def fechar(
        self, exit_code: int | None = None, erro: BaseException | str | None = None
    ) -> None:
        """Grava o resultado. Idempotente — chamar duas vezes não duplica nada.

        Três status, e a diferença importa para quem lê o painel: `ok` é código 0,
        `falha` é o comando terminando com código diferente de zero (ele mesmo
        reportou o problema — dia de classificação que não deu, canal sem
        permissão), e `erro` é exceção que subiu até aqui, que é bug ou ambiente.
        """
        if self.fechado:
            return
        if erro is not None:
            texto = (
                erro
                if isinstance(erro, str)
                else f"{type(erro).__name__}: {erro}".strip()
            )
            status = "erro"
        else:
            texto = None
            status = "ok" if (exit_code or 0) == 0 else "falha"
        _escrever_json(self.summary, self._dados(status, exit_code, texto))
        self.fechado = True

    def recusar(self, motivo: str) -> None:
        """Fecha como `recusado`: nem rodou, porque o lock estava com outro.

        Status próprio em vez de `erro` porque a leitura é oposta — `erro` pede
        que alguém olhe, `recusado` é o sistema se protegendo e funcionando como
        desenhado. Somar os dois faria uma tela de alerta gritar sozinha.
        """
        if self.fechado:
            return
        _escrever_json(self.summary, self._dados("recusado", None, motivo))
        self.fechado = True


_atual: Run | None = None


def atual() -> Run | None:
    """O run aberto neste processo, se houver."""
    return _atual


def contar(chave: str, n: int = 1) -> None:
    """Soma num contador do run aberto. **No-op quando não há run.**

    É o que permite instrumentar `cmd_sync` sem mudar a assinatura dele nem
    obrigar os testes que o chamam direto a montar um journal.
    """
    if _atual is not None:
        _atual.contar(chave, n)


def podar(state_dir: Path, manter: int = MANTER_RUNS) -> int:
    """Apaga os runs mais antigos além do teto. Devolve quantos saíram.

    Só remove diretório cujo nome tem a cara de um run (`<instante>-<cmd>-<pid>`)
    e cujo conteúdo é só o que este módulo escreve. `.state/` é do sistema, mas
    apagar recursivamente pasta alheia porque ela apareceu ali é o tipo de coisa
    que se descobre tarde.
    """
    raiz = Path(state_dir) / "runs"
    if not raiz.is_dir():
        return 0
    diretorios = sorted(
        (p for p in raiz.iterdir() if p.is_dir() and len(p.name.split("-")) == 3),
        key=lambda p: p.name,
        reverse=True,
    )
    removidos = 0
    for d in diretorios[manter:]:
        conhecidos = {"summary.json", "summary.json.tmp", "events.jsonl"}
        try:
            if any(f.name not in conhecidos or f.is_dir() for f in d.iterdir()):
                continue
            for f in d.iterdir():
                f.unlink(missing_ok=True)
            d.rmdir()
        except FileNotFoundError:
            continue  # outro processo (prod/responder) ja removeu este run
        removidos += 1
    return removidos


def abrir(state_dir: Path, comando: str, argv: list[str] | None = None) -> Run:
    """Cria o diretório do run e marca `status: rodando`."""
    global _atual
    inicio = _agora()
    rid = f"{inicio.strftime('%Y%m%dT%H%M%SZ')}-{comando}-{os.getpid()}"
    run = Run(
        id=rid,
        comando=comando,
        argv=list(argv or []),
        diretorio=Path(state_dir) / "runs" / rid,
        inicio=inicio,
        _t0=time.monotonic(),
    )
    run.abrir_no_disco()
    podar(state_dir)
    _atual = run
    return run


@contextmanager
def registrar(state_dir: Path, comando: str, argv: list[str] | None = None):
    """Envolve uma execução: abre, deixa rodar, fecha com o que aconteceu.

    Exceção é registrada e **reerguida** — o journal observa, não engole. Um
    painel que mostrasse "erro" enquanto o terminal mostrasse sucesso (ou o
    contrário) seria pior que não ter histórico.
    """
    global _atual
    anterior, run = _atual, abrir(state_dir, comando, argv)
    try:
        yield run
    except BaseException as exc:  # inclui KeyboardInterrupt: Ctrl-C é resultado
        run.fechar(erro=exc)
        raise
    finally:
        _atual = anterior


# --------------------------------------------------------------------- leitura


def ler(diretorio: Path) -> dict | None:
    """Resumo de um run, ou `None` se ilegível.

    Ilegível não é exceção: um run que morreu entre o `mkdir` e o `os.replace`
    deixa diretório sem `summary.json`, e um painel que estourasse por causa
    disso ficaria inútil justamente quando algo deu errado.
    """
    caminho = Path(diretorio) / "summary.json"
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return dados if isinstance(dados, dict) else None


def listar(state_dir: Path, limite: int | None = 50, comando: str | None = None) -> list[dict]:
    """Runs do mais recente para o mais antigo.

    A ordem sai do nome do diretório, que começa com o instante em ISO compacto
    — ordenação lexicográfica é ordenação cronológica, sem abrir arquivo nenhum
    para decidir a ordem. Por isso o `limite` corta antes de ler o JSON.
    """
    raiz = Path(state_dir) / "runs"
    if not raiz.is_dir():
        return []
    nomes = sorted((p for p in raiz.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    saida: list[dict] = []
    for d in nomes:
        # O nome é `<instante>-<comando>-<pid>`. Filtrar por ele descarta a maioria
        # dos diretórios sem abrir arquivo; o campo do JSON é quem decide de fato.
        if comando is not None and d.name.split("-")[1:2] != [comando]:
            continue
        dados = ler(d)
        if dados is None or (comando is not None and dados.get("comando") != comando):
            continue
        saida.append(dados)
        if limite is not None and len(saida) >= limite:
            break
    return saida


def ultimo(state_dir: Path, comando: str) -> dict | None:
    """O run mais recente de um comando — o que o painel mostra como 'última vez'."""
    encontrados = listar(state_dir, limite=1, comando=comando)
    return encontrados[0] if encontrados else None


# ------------------------------------------------------------------------ lock


class Ocupado(RuntimeError):
    """Outro processo está com o lock. Traz quem e desde quando."""

    def __init__(self, dono: dict, segundos: float):
        self.dono, self.segundos = dono, segundos
        comando = dono.get("comando", "algo")
        pid = dono.get("pid", "?")
        quanto = "menos de 1 s" if segundos < 1 else f"{int(segundos)} s"
        super().__init__(
            f"{comando} já está rodando há {quanto} (pid {pid}). "
            "Duas execuções ao mesmo tempo corrompem o cursor do canal."
        )


def _dir_locks(state_dir: Path) -> Path:
    return Path(state_dir) / "locks"


def dono(state_dir: Path, nome: str) -> dict | None:
    """Quem está com o lock, ou `None` se ninguém está.

    Quem pergunta não pode confiar só no arquivo de metadados: processo morto de
    `kill -9` deixa o arquivo para trás. A verdade é o `flock` — se ele estiver
    livre, o metadado é lixo de um run que morreu, e é assim que se distingue
    "rodando" de "morreu no meio" sem inventar timeout.
    """
    lock = _dir_locks(state_dir) / f"{nome}.lock"
    meta = _dir_locks(state_dir) / f"{nome}.json"
    if not lock.is_file() or not meta.is_file():
        return None
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            return json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"comando": "algo", "pid": None, "desde": None}
        finally:
            os.close(fd)
    # Conseguimos o lock: ninguém é dono. Solta e responde.
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    return None


@contextmanager
def travar(state_dir: Path, nome: str, comando: str, run_id: str | None = None):
    """Lock exclusivo entre processos. Levanta `Ocupado` em vez de esperar.

    Esperar seria pior: o laço do container roda de 6 em 6 horas e um `sync`
    pode levar minutos. Uma requisição do painel que ficasse pendurada
    aparentaria travamento, e enfileirar dois syncs seguidos não tem valor —
    o segundo não teria nada novo para ler. Recusar com "está rodando há N s" é
    a resposta honesta.
    """
    pasta = _dir_locks(state_dir)
    pasta.mkdir(parents=True, exist_ok=True)
    lock, meta = pasta / f"{nome}.lock", pasta / f"{nome}.json"
    lock.touch(exist_ok=True)

    fd = os.open(lock, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            atual_dono = {}
            try:
                atual_dono = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            desde = atual_dono.get("desde_epoch")
            segundos = max(0.0, time.time() - desde) if isinstance(desde, (int, float)) else 0.0
            raise Ocupado(atual_dono, segundos) from None

        _escrever_json(
            meta,
            {
                "comando": comando,
                "pid": os.getpid(),
                "run_id": run_id,
                "desde": _agora().isoformat(timespec="seconds"),
                # Epoch em segundos, para o "há N s" da recusa não depender de
                # parsear ISO nem de fuso. `time.monotonic` não serve: o valor
                # dele não tem sentido em outro processo.
                "desde_epoch": time.time(),
            },
        )
        try:
            yield
        finally:
            # Apaga o metadado antes de soltar o lock: na ordem inversa existe um
            # instante em que outro processo pega o lock e ainda lê o dono velho.
            meta.unlink(missing_ok=True)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def travar_comando(state_dir: Path, comando: str, run_id: str | None = None):
    """Trava o que precisa ser travado, e sai da frente do que não precisa.

    Um só lugar decide isso, para não haver comando protegido por um caminho de
    código e desprotegido por outro — é a razão de o painel disparar job pelo
    mesmo `python3 -m dlb`, em vez de chamar as funções direto.
    """
    nome = LOCK_DE_COMANDO.get(comando)
    if nome is None:
        yield
        return
    with travar(state_dir, nome, comando, run_id):
        yield
