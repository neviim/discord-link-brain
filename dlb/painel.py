"""O painel: segunda face da CLI que já existe, servida pela biblioteca padrão.

Quatro dos cinco objetivos do painel são **projeção de dado que o sistema já
produz** — o acervo está em `50_LINKS/`, o cursor em `.state/`, a agenda no
`docker/scheduler.sh`, a configuração no `.env`. O único que era buraco de
verdade, o histórico de execução, ganhou `dlb/journal.py`. Daí este arquivo não
ter regra de negócio: ele lê, monta HTML e sai da frente.

**Roda no host, não em container.** Disparar `docker compose up` de dentro de um
container exigiria montar `/var/run/docker.sock`, o que é root no host — por um
botão de conveniência, num processo que também vai ter chat de LLM. O painel roda
onde o `dlb.sh` roda.

**Bind em `127.0.0.1`.** `http.server` não tem timeout de leitura nem limite de
conexão: como servidor de borda é inaceitável, e isso não se disfarça. Expor no IP
da tailnet exige proxy real na frente, e o comando **recusa** subir fora de
loopback justamente para essa decisão não ser tomada por descuido.

Somente leitura nesta fase: qualquer método que não seja GET ou HEAD é 405. O que
escreve entra depois, junto da fronteira de mutação (`Origin`, CSRF, token) — e
não antes, para não haver uma janela em que exista botão e não exista guarda.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import journal
from .acervo import Acervo, filtrar, resumo
from .config import Config
from .render import Raw, juntar, pagina, substituir, tabela

ESTATICOS = Path(__file__).parent / "static"

# Só estes nomes são servidos de `static/`. Lista branca em vez de sanear o
# caminho: `..%2f` e amigos são um jogo que não se ganha por filtro.
ARQUIVOS_ESTATICOS = {
    "painel.css": "text/css; charset=utf-8",
    "painel.js": "application/javascript; charset=utf-8",
}

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class Erro(RuntimeError):
    """Falha que o comando reporta como mensagem, não como traceback."""


# --------------------------------------------------------------------- leitura


def _idade(caminho: Path) -> float | None:
    """Segundos desde o instante gravado no arquivo de heartbeat.

    O agendador escreve `date +%s` **só depois de um sync com código 0**, então
    heartbeat velho significa "o último sync falhou ou o agendador morreu" — não
    "o container está de pé". É a diferença entre monitorar processo e monitorar
    resultado, e é por isso que o painel usa este arquivo em vez de perguntar ao
    Docker se o container existe.
    """
    try:
        return max(0.0, time.time() - float(caminho.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return None


def _humano(segundos: float | None) -> str:
    if segundos is None:
        return "nunca"
    if segundos < 90:
        return f"{int(segundos)} s"
    if segundos < 5400:
        return f"{int(segundos // 60)} min"
    if segundos < 172800:
        return f"{segundos / 3600:.1f} h"
    return f"{int(segundos // 86400)} d"


class Estado:
    """Tudo que o painel mostra, lido do disco a cada requisição.

    Sem cache além do memo do acervo: o valor do painel é dizer a verdade agora.
    Um cache de cinco segundos economizaria leitura de uns poucos JSON e criaria
    a classe de bug em que a tela discorda do disco.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.acervo = Acervo(cfg.vault_path)
        self._trava = threading.Lock()

    # `ThreadingHTTPServer` atende em paralelo, e o memo do acervo é um dict
    # compartilhado. A leitura em si é barata; serializar evita duas threads
    # relerem o mesmo arquivo e brigarem pelo `del` do que foi apagado.
    def links(self):
        with self._trava:
            return self.acervo.links()

    def heartbeats(self) -> dict:
        state = Path(self.cfg.state_dir)
        return {
            "sync": _idade(state / "heartbeat"),
            "responder": _idade(state / "heartbeat-responder"),
        }

    def cursores(self) -> list[dict]:
        """Um por canal configurado, com o que o cursor sabe.

        Lê pelo `Store` em vez de abrir o JSON à mão para o painel nunca
        discordar da CLI sobre o formato do estado.
        """
        from .state import Store

        store = Store(Path(self.cfg.state_dir))
        saida = []
        for channel_id in self.cfg.channel_ids:
            st = store.load(channel_id)
            saida.append(
                {
                    "canal": channel_id,
                    "nome": self.cfg.channel_names.get(channel_id) or st.channel_name or "",
                    "ultima_mensagem": st.last_message_id,
                    "ultimo_run": st.last_run,
                    "backfill_cursor": st.backfill_after,
                    "backfill_concluido": st.backfill_done,
                }
            )
        return saida

    def execucoes(self, limite: int | None = 30, comando: str | None = None) -> list[dict]:
        return journal.listar(self.cfg.state_dir, limite=limite, comando=comando)

    def lock(self) -> dict | None:
        return journal.dono(self.cfg.state_dir, "canais")

    def visao_geral(self) -> dict:
        entradas = self.links()
        bat = self.heartbeats()
        return {
            "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "vault": str(self.cfg.vault_path),
            "state_dir": str(self.cfg.state_dir),
            "heartbeats": bat,
            "tolerancia_s": {
                "sync": self.cfg.health_tolerance,
                "responder": self.cfg.responder_health_tolerance,
            },
            "acervo": resumo(entradas),
            "leitura": vars(self.acervo.ultima),
            "cursores": self.cursores(),
            "ultimas_execucoes": self.execucoes(limite=10),
            "lock": self.lock(),
        }


# ------------------------------------------------------------------------ HTML


_CARTAO = """<article class="cartao $tom">
  <h3>$titulo</h3>
  <p class=numero>$valor</p>
  <p class=detalhe>$detalhe</p>
</article>"""


def _cartao(titulo: str, valor: object, detalhe: str = "", tom: str = "") -> Raw:
    return substituir(_CARTAO, titulo=titulo, valor=valor, detalhe=detalhe, tom=tom)


def _selo(status: str) -> Raw:
    """Status como `<span>` com classe — a única célula que carrega marcação."""
    return substituir(
        '<span class="selo selo-$classe">$texto</span>',
        classe=status if status in {"ok", "falha", "erro", "rodando", "recusado"} else "outro",
        texto=status,
    )


def _tom_heartbeat(idade: float | None, tolerancia: int) -> str:
    if idade is None:
        return "alerta"
    return "ok" if idade <= tolerancia else "alerta"


def _linha_execucao(dados: dict) -> list[object]:
    contadores = dados.get("contadores") or {}
    resumo_contadores = ", ".join(f"{k}: {v}" for k, v in contadores.items()) or "—"
    return [
        (dados.get("inicio") or "")[:19].replace("T", " "),
        dados.get("comando") or "?",
        _selo(dados.get("status") or "?"),
        "—" if dados.get("duracao_s") is None else f"{dados['duracao_s']:.1f} s",
        resumo_contadores,
        dados.get("erro") or "",
    ]


def _tabela_execucoes(runs: list[dict]) -> Raw:
    return tabela(
        ["início (UTC)", "comando", "status", "duração", "contadores", "erro"],
        [_linha_execucao(r) for r in runs],
        vazio="Nenhuma execução registrada ainda. O histórico começa no próximo run.",
    )


def _view_painel(estado: Estado) -> str:
    dados = estado.visao_geral()
    bat, tol = dados["heartbeats"], dados["tolerancia_s"]
    acervo = dados["acervo"]
    triagem = acervo["por_triagem"]

    cartoes = juntar(
        [
            _cartao(
                "Agendador",
                _humano(bat["sync"]),
                f"último sync com sucesso · tolera {_humano(tol['sync'])}",
                _tom_heartbeat(bat["sync"], tol["sync"]),
            ),
            _cartao(
                "Responder",
                _humano(bat["responder"]),
                f"última volta do bot · tolera {_humano(tol['responder'])}",
                _tom_heartbeat(bat["responder"], tol["responder"]),
            ),
            _cartao(
                "Acervo",
                acervo["total"],
                f"de {acervo['primeiro_dia'] or '—'} a {acervo['ultimo_dia'] or '—'}",
            ),
            _cartao(
                "Fila de triagem",
                triagem.get("inbox", 0),
                "notas em `inbox` — se só cresce, a triagem não está acontecendo",
                "alerta" if triagem.get("inbox", 0) > 40 else "",
            ),
        ],
        "",
    )

    lock = dados["lock"]
    aviso_lock = (
        substituir(
            "<p class=aviso>$comando está rodando agora (pid $pid, desde $desde).</p>",
            comando=lock.get("comando"),
            pid=lock.get("pid"),
            desde=lock.get("desde"),
        )
        if lock
        else Raw("")
    )

    corpo = substituir(
        """<h1>Painel</h1>
$aviso_lock
<section class=cartoes>$cartoes</section>

<h2>Canais</h2>
$cursores

<h2>Últimas execuções</h2>
$execucoes
<p><a href="/execucoes">todas as execuções →</a></p>

<h2>Leitura do acervo</h2>
<p class=detalhe>$leitura</p>""",
        aviso_lock=aviso_lock,
        cartoes=cartoes,
        cursores=tabela(
            ["canal", "nome", "última mensagem", "último run", "histórico"],
            [
                [
                    c["canal"],
                    c["nome"] or "—",
                    c["ultima_mensagem"] or "—",
                    (c["ultimo_run"] or "—")[:19].replace("T", " "),
                    "concluído" if c["backfill_concluido"] else (c["backfill_cursor"] or "não começou"),
                ]
                for c in dados["cursores"]
            ],
            vazio="Nenhum canal configurado em DISCORD_CHANNEL_IDS.",
        ),
        execucoes=_tabela_execucoes(dados["ultimas_execucoes"][:5]),
        leitura=(
            f"{dados['leitura']['total']} notas · "
            f"{dados['leitura']['lidos_do_disco']} lidas do disco, "
            f"{dados['leitura']['reusados_do_memo']} reusadas do memo"
            + (
                f" · {dados['leitura']['ilegiveis']} ilegível(is)"
                if dados["leitura"]["ilegiveis"]
                else ""
            )
        ),
    )
    return pagina("Painel", corpo, ativo="/", vault=dados["vault"])


def _view_execucoes(estado: Estado, consulta: dict) -> str:
    comando = (consulta.get("comando") or [""])[0] or None
    runs = estado.execucoes(limite=200, comando=comando)
    filtros = juntar(
        [
            substituir(
                '<a href="$href" class="$classe">$rotulo</a>',
                href="/execucoes" if not c else f"/execucoes?comando={c}",
                classe="ativo" if (comando or "") == c else "",
                rotulo=c or "todos",
            )
            for c in ("", "sync", "backfill", "sintese", "ressurgir", "responder")
        ],
        " ",
    )
    corpo = substituir(
        """<h1>Execuções</h1>
<p class=detalhe>Uma linha por run, do mais recente para o mais antigo. Vem de
<code>.state/runs/</code> — não do log do Docker, que rotaciona.</p>
<nav class=filtros>$filtros</nav>
$tabela""",
        filtros=filtros,
        tabela=_tabela_execucoes(runs),
    )
    return pagina("Execuções", corpo, ativo="/execucoes", vault=str(estado.cfg.vault_path))


def _view_acervo(estado: Estado, consulta: dict) -> str:
    def um(nome: str) -> str:
        return (consulta.get(nome) or [""])[0].strip()

    termo, triagem, categoria = um("q"), um("triagem"), um("categoria")
    entradas = estado.links()
    dados = resumo(entradas)
    achados = filtrar(entradas, termo=termo, triagem=triagem, categoria=categoria)

    facetas = juntar(
        [
            substituir(
                '<a href="$href" class="$classe">$rotulo ($n)</a>',
                href=f"/acervo?triagem={t}" if t else "/acervo",
                classe="ativo" if triagem == t else "",
                rotulo=t or "todas",
                n=n,
            )
            for t, n in [("", dados["total"])] + list(dados["por_triagem"].items())
        ],
        " ",
    )

    linhas = [
        [
            e.dia or "—",
            substituir('<a href="$url" rel="noreferrer noopener">$titulo</a>', url=e.url, titulo=e.titulo or e.nome),
            e.dominio,
            e.categoria,
            e.triagem or "sem",
            "—" if e.nota is None else e.nota,
        ]
        for e in reversed(achados[-300:])
    ]

    corpo = substituir(
        """<h1>Acervo</h1>
<p class=detalhe>$total notas em <code>50_LINKS/</code>. O vault é a verdade: isto
é leitura sob demanda, sem índice em banco.</p>
<form method=get action="/acervo" class=busca>
  <input type=search name=q value="$termo" placeholder="título, resumo, domínio, conceito…">
  <input type=hidden name=triagem value="$triagem">
  <button type=submit>buscar</button>
</form>
<nav class=filtros>$facetas</nav>
<p class=detalhe>$quantos</p>
$tabela""",
        total=dados["total"],
        termo=termo,
        triagem=triagem,
        facetas=facetas,
        quantos=(
            f"{len(achados)} nota(s) no filtro"
            + (" · mostrando as 300 mais recentes" if len(achados) > 300 else "")
        ),
        tabela=tabela(
            ["dia", "título", "domínio", "categoria", "triagem", "nota"],
            linhas,
            vazio="Nenhuma nota com esse filtro.",
        ),
    )
    return pagina("Acervo", corpo, ativo="/acervo", vault=str(estado.cfg.vault_path))


# ---------------------------------------------------------------------- rotas


class Handler(BaseHTTPRequestHandler):
    """Rotas do painel. Somente leitura nesta fase."""

    server_version = "dlb-painel"
    sys_version = ""            # não anuncia a versão do Python
    protocol_version = "HTTP/1.1"

    @property
    def estado(self) -> Estado:
        return self.server.estado  # type: ignore[attr-defined]

    # `BaseHTTPRequestHandler` loga em stderr uma linha por requisição, com
    # formato próprio. Passa pelo `_log` do projeto para o painel rodando em
    # systemd ter a mesma cara do resto.
    def log_message(self, formato: str, *args) -> None:
        if getattr(self.server, "silencioso", False):
            return
        print(f"[painel] {self.address_string()} {formato % args}")

    def do_GET(self) -> None:
        self._despachar(corpo=True)

    def do_HEAD(self) -> None:
        self._despachar(corpo=False)

    def _metodo_nao_permitido(self) -> None:
        self._responder(
            405,
            b'{"erro":"somente leitura nesta versao do painel"}',
            "application/json; charset=utf-8",
            extras={"Allow": "GET, HEAD"},
        )

    do_POST = do_PUT = do_DELETE = do_PATCH = _metodo_nao_permitido

    def _despachar(self, corpo: bool) -> None:
        alvo = urlparse(self.path)
        rota, consulta = alvo.path.rstrip("/") or "/", parse_qs(alvo.query)
        try:
            if rota == "/":
                self._html(_view_painel(self.estado), corpo)
            elif rota == "/execucoes":
                self._html(_view_execucoes(self.estado, consulta), corpo)
            elif rota == "/acervo":
                self._html(_view_acervo(self.estado, consulta), corpo)
            elif rota == "/api/estado":
                self._json(self.estado.visao_geral(), corpo)
            elif rota == "/api/execucoes":
                self._json(
                    {"execucoes": self.estado.execucoes(limite=_inteiro(consulta, "limite", 100))},
                    corpo,
                )
            elif rota == "/api/acervo":
                self._api_acervo(consulta, corpo)
            elif rota.startswith("/static/"):
                self._estatico(rota[len("/static/"):], corpo)
            else:
                self._json({"erro": "rota inexistente", "rota": rota}, corpo, codigo=404)
        except BrokenPipeError:
            pass    # aba fechada no meio do download; não é erro do painel
        except Exception as exc:  # noqa: BLE001
            # Uma tela que estoura não pode derrubar o servidor: o painel é
            # justamente o que se abre quando algo já está errado.
            self.log_message("erro em %s: %s: %s", rota, type(exc).__name__, exc)
            self._json({"erro": f"{type(exc).__name__}: {exc}"}, corpo, codigo=500)

    def _api_acervo(self, consulta: dict, corpo: bool) -> None:
        def um(nome: str) -> str:
            return (consulta.get(nome) or [""])[0].strip()

        entradas = self.estado.links()
        achados = filtrar(
            entradas,
            termo=um("q"),
            triagem=um("triagem"),
            categoria=um("categoria"),
            area=um("area"),
        )
        limite = _inteiro(consulta, "limite", 0)
        recortado = achados[-limite:] if limite else achados
        self._json(
            {
                "total": len(entradas),
                "no_filtro": len(achados),
                "resumo": resumo(entradas),
                "links": [
                    {
                        "nome": e.nome,
                        "url": e.url,
                        "titulo": e.titulo,
                        "dia": e.dia,
                        "semana": e.semana,
                        "dominio": e.dominio,
                        "categoria": e.categoria,
                        "area": e.area,
                        "triagem": e.triagem,
                        "nota": e.nota,
                        "resumo": e.resumo,
                        "conceitos": e.conceitos,
                        "sem_pagina": e.sem_pagina,
                    }
                    for e in recortado
                ],
            },
            corpo,
        )

    # ------------------------------------------------------------- respostas

    def _responder(
        self, codigo: int, dados: bytes, tipo: str, corpo: bool = True, extras: dict | None = None
    ) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        # O painel mostra estado agora. Cache aqui produz tela mentindo.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # Nada de externo: sem CDN, sem fonte remota, sem inline. Fecha o
        # caminho fácil de um XSS virar exfiltração.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; form-action 'self'",
        )
        for chave, valor in (extras or {}).items():
            self.send_header(chave, valor)
        self.end_headers()
        if corpo:
            self.wfile.write(dados)

    def _html(self, texto: str, corpo: bool, codigo: int = 200) -> None:
        self._responder(codigo, texto.encode("utf-8"), "text/html; charset=utf-8", corpo)

    def _json(self, dados: object, corpo: bool, codigo: int = 200) -> None:
        bruto = json.dumps(dados, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        self._responder(codigo, bruto, "application/json; charset=utf-8", corpo)

    def _estatico(self, nome: str, corpo: bool) -> None:
        tipo = ARQUIVOS_ESTATICOS.get(nome)
        if tipo is None:
            self._json({"erro": "arquivo inexistente", "arquivo": nome}, corpo, codigo=404)
            return
        self._responder(200, (ESTATICOS / nome).read_bytes(), tipo, corpo)


def _inteiro(consulta: dict, nome: str, padrao: int) -> int:
    try:
        return max(0, int((consulta.get(nome) or [""])[0]))
    except (TypeError, ValueError):
        return padrao


# ------------------------------------------------------------------- servidor


def construir(cfg: Config, host: str = "127.0.0.1", porta: int = 8765, silencioso: bool = False):
    """Cria o servidor sem subir o laço — é o que o teste usa.

    Recusa host fora de loopback: `http.server` não tem timeout de leitura nem
    teto de conexão, então na borda um slowloris o derruba. Enquanto não houver
    autenticação e proxy, a resposta certa é não deixar subir.
    """
    if host not in LOOPBACK:
        raise Erro(
            f"host {host!r} não é loopback. Nesta fase o painel só sobe em 127.0.0.1: "
            "não há autenticação, e `http.server` não tem timeout nem limite de conexão. "
            "Para acessar de outra máquina, ponha um proxy com TLS na frente."
        )
    servidor = ThreadingHTTPServer((host, porta), Handler)
    servidor.estado = Estado(cfg)          # type: ignore[attr-defined]
    servidor.silencioso = silencioso       # type: ignore[attr-defined]
    servidor.daemon_threads = True
    return servidor


def servir(cfg: Config, host: str = "127.0.0.1", porta: int = 8765) -> int:
    servidor = construir(cfg, host, porta)
    endereco = f"http://{host}:{servidor.server_port}"
    print(f"Painel em {endereco} — vault {cfg.vault_path}")
    print("Ctrl-C para parar.")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nParando.")
    finally:
        servidor.server_close()
    return 0
