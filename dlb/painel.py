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

from . import agenda, chat as chat_mod, compose, env_io, gestor, journal
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
        # O chat reusa o memo do acervo: perguntar não relê 77 arquivos se nada
        # mudou, e a resposta sai do mesmo dado que a tela do acervo mostra.
        self.chat = chat_mod.Chat(cfg, self.links)

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

    # Os quatro comandos do ciclo do agendador. O painel mostra "última vez" de
    # cada um porque três deles não escrevem heartbeat nenhum — sem isto, um
    # backfill que falha todo dia falharia em silêncio.
    CICLO = ("sync", "backfill", "sintese", "ressurgir")

    def ultimos(self) -> dict:
        return {cmd: journal.ultimo(self.cfg.state_dir, cmd) for cmd in self.CICLO}

    def lock(self) -> dict | None:
        return journal.dono(self.cfg.state_dir, "canais")

    def agenda(self) -> dict:
        """A grade, com as duas colunas: o que o `.env` diz e o que o container tem.

        `restart` não relê o `.env` (commit `4ee85aa`), então as duas podem
        discordar — e quem manda é o container. Mostrar só o arquivo seria o
        painel mentir sobre a grade, que é o risco número 1 desta tela.
        """
        do_arquivo = env_io.ler(self.cfg.env_path)
        inspecao = compose.inspecionar()
        ativo, fonte = compose.ambiente_ativo(do_arquivo, inspecao)
        dados = agenda.visao(ativo)
        dados.update(
            {
                "fonte": fonte,
                "container": inspecao,
                "divergencias": compose.divergencias(do_arquivo, (inspecao or {}).get("env"), agenda.CHAVES),
                "arquivo": {k: do_arquivo.get(k, "") for k in agenda.CHAVES},
                "env_path": str(self.cfg.env_path),
                "duplicadas": env_io.duplicadas(self.cfg.env_path),
                "ultimos": self.ultimos(),
            }
        )
        return dados

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
            "ultimos": self.ultimos(),
            "lock": self.lock(),
        }

    def com_alertas(self) -> dict:
        """A visão mais o que as oito regras do gestor concluíram sobre ela.

        As regras são função pura sobre este dicionário — recebem exatamente o que
        a tela recebe, e por isso podem ser testadas fabricando estado.
        """
        visao = self.visao_geral()
        visao["alertas"] = [a.como_dict() for a in gestor.avaliar(visao)]
        return visao


# ------------------------------------------------------------------------ HTML


_CARTAO = """<article class="cartao $tom">
  <h3>$titulo</h3>
  <p class=numero>$valor</p>
  <p class=detalhe>$detalhe</p>
</article>"""


def _cartao(titulo: str, valor: object, detalhe: str = "", tom: str = "") -> Raw:
    return substituir(_CARTAO, titulo=titulo, valor=valor, detalhe=detalhe, tom=tom)


_ALERTA = """<article class="faixa faixa-$nivel">
  <h3>$titulo</h3>
  <p class=evidencia>$evidencia</p>
  <p class=acao>$acao</p>
</article>"""


def _alertas(lista: list[dict]) -> Raw:
    """Os alertas do gestor, mais grave primeiro.

    Silêncio é resposta: quando nada pede ação, a tela diz isso em uma linha em
    vez de mostrar oito verdinhos. Painel que sempre tem o que mostrar ensina a
    não olhar.
    """
    pedem_acao = [a for a in lista if a["nivel"] in {"alerta", "atencao"}]
    if not lista:
        return Raw('<p class=quieto>Nada pedindo atenção — as oito regras do gestor passaram.</p>')
    cabecalho = (
        Raw("")
        if pedem_acao
        else Raw('<p class=quieto>Nada quebrado. O que segue é trabalho disponível, não problema.</p>')
    )
    return juntar(
        [cabecalho]
        + [
            substituir(
                _ALERTA,
                nivel=a["nivel"],
                titulo=a["titulo"],
                evidencia=a["evidencia"],
                acao=a["acao"],
            )
            for a in lista
        ],
        "",
    )


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
    # Processo que fica de pé por desenho não é job em andamento. Mostrar
    # "rodando" para o painel faria a tela parecer ter um sync travado desde
    # ontem — e é o próprio painel.
    estado = dados.get("status") or "?"
    if estado == "rodando" and dados.get("longevo"):
        estado = "de pé"
    return [
        (dados.get("inicio") or "")[:19].replace("T", " "),
        dados.get("comando") or "?",
        _selo(estado),
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
    dados = estado.com_alertas()
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

<h2>Gestor</h2>
<section class=alertas>$alertas</section>

<h2>O ciclo</h2>
$ciclo

<h2>Canais</h2>
$cursores

<h2>Últimas execuções</h2>
$execucoes
<p><a href="/execucoes">todas as execuções →</a></p>

<h2>Leitura do acervo</h2>
<p class=detalhe>$leitura</p>""",
        aviso_lock=aviso_lock,
        cartoes=cartoes,
        alertas=_alertas(dados["alertas"]),
        ciclo=tabela(
            ["comando", "última vez", "status", "duração", "contadores"],
            [
                [
                    comando,
                    "nunca" if not run else (run.get("inicio") or "")[:19].replace("T", " "),
                    _selo((run or {}).get("status") or "sem registro"),
                    "—" if not run or run.get("duracao_s") is None else f"{run['duracao_s']:.1f} s",
                    ", ".join(f"{k}: {v}" for k, v in ((run or {}).get("contadores") or {}).items()) or "—",
                ]
                for comando, run in dados["ultimos"].items()
            ],
            vazio="Nenhum comando do ciclo registrado ainda.",
        ),
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


def _view_agenda(estado: Estado) -> str:
    dados = estado.agenda()
    inspecao = dados["container"]

    if dados["fonte"] == "container":
        origem = substituir(
            "<p class=detalhe>Os números abaixo vêm do <strong>container</strong> "
            "($container, $status$saude) — é ele que manda. O que o arquivo diz está "
            "na tabela de divergências.</p>",
            container=inspecao["container"],
            status=inspecao["status"],
            saude=f", saúde {inspecao['saude']}" if inspecao.get("saude") else "",
        )
    else:
        motivo = (
            "o container não está no ar"
            if inspecao
            else "não consegui falar com o docker (ou o container nunca foi criado)"
        )
        origem = substituir(
            "<p class=aviso>Mostrando o que <strong>$arquivo</strong> diz, porque $motivo. "
            "Se o container subir com outro valor, é o dele que vale — `restart` não relê o "
            "`.env`, só `up`.</p>",
            arquivo=dados["env_path"],
            motivo=motivo,
        )

    avisos = juntar(
        [substituir("<article class='faixa faixa-atencao'><p>$texto</p></article>", texto=a)
         for a in dados["avisos"]],
        "",
    ) or Raw("")

    duplicadas = dados["duplicadas"]
    aviso_dup = (
        substituir(
            "<article class='faixa faixa-alerta'><p>Chave repetida no `.env`: $quais. "
            "A <strong>primeira</strong> ocorrência é a que vale — `load_dotenv` usa "
            "`setdefault`.</p></article>",
            quais=", ".join(f"{k} (linhas {', '.join(map(str, v))})" for k, v in duplicadas.items()),
        )
        if duplicadas
        else Raw("")
    )

    ultimos = dados["ultimos"]
    linhas_slots = []
    for slot in dados["slots"]:
        run = ultimos.get(slot["comando"])
        linhas_slots.append(
            [
                slot["comando"],
                Raw("<span class='selo selo-ok'>ativo</span>")
                if slot["ativo"]
                else Raw("<span class=selo>desligado</span>"),
                slot["porque"],
                (slot["proximo"] or "—").replace("T", " ")[:16],
                "nunca" if not run else (run.get("inicio") or "")[:16].replace("T", " "),
                slot["observacao"] or "—",
            ]
        )

    divergencias = tabela(
        ["chave", "no .env", "no container"],
        [[d["chave"], d["arquivo"] or "(vazio)", d["container"] or "(vazio)"] for d in dados["divergencias"]],
        vazio="Nenhuma: o arquivo e o container dizem a mesma coisa.",
    )

    corpo = substituir(
        """<h1>Agenda</h1>
$origem
$aviso_dup
$avisos

<section class=cartoes>
$cartoes
</section>

<h2>Os quatro comandos do ciclo</h2>
<p class=detalhe>O laço roda os quatro em sequência a cada disparo, nesta ordem.
Cada um decide sozinho se tem trabalho — daí "ativo" não significar "vai fazer algo".</p>
$slots

<h2>.env contra container</h2>
<p class=detalhe>Aplicar mudança de agenda é <code>./dlb.sh prod up</code>, que
recria o container. <code>restart</code> reaproveita o antigo e o `.env` novo não
entra — sem erro nenhum.</p>
$divergencias""",
        origem=origem,
        aviso_dup=aviso_dup,
        avisos=avisos,
        cartoes=juntar(
            [
                _cartao("Modo", dados["modo"], dados["detalhe"]),
                _cartao(
                    "Próximo disparo",
                    (dados["slots"][0]["proximo"] or "não ancorado")[11:16]
                    if dados["slots"][0]["proximo"]
                    else "depende",
                    (dados["slots"][0]["proximo"] or "")[:10]
                    or "modo intervalo conta do fim do último ciclo",
                ),
                _cartao("Fuso do agendador", dados["fuso"], f"agora {dados['agora'][11:19]}"),
                _cartao(
                    "Ao subir",
                    "roda na hora" if dados["roda_ao_subir"] else "espera o slot",
                    "DLB_RUN_ON_START",
                ),
            ],
            "",
        ),
        slots=tabela(
            ["comando", "estado", "por quê", "próximo", "última vez", "observação"],
            linhas_slots,
            vazio="—",
        ),
        divergencias=divergencias,
    )
    return pagina("Agenda", corpo, ativo="/agenda", vault=str(estado.cfg.vault_path))


_CHAT_ITEM = """<article class="troca $classe">
  <p class=pergunta>$pergunta</p>
  <p class=resposta>$texto</p>
  <p class=detalhe>$rodape</p>
  $usados
</article>"""


def _troca(registro: dict) -> Raw:
    """Uma pergunta e sua resposta. Os links vêm do acervo, nunca do modelo."""
    usados = registro.get("usados") or []
    lista_usados = (
        Raw("")
        if not usados
        else Raw(
            "<ul class=usados>"
            + str(
                juntar(
                    [
                        substituir(
                            '<li><a href="$url" rel="noreferrer noopener">$titulo</a> '
                            "<span class=detalhe>· $dia</span></li>",
                            url=u.get("url"),
                            titulo=u.get("titulo") or u.get("nome"),
                            dia=u.get("dia"),
                        )
                        for u in usados
                    ],
                    "",
                )
            )
            + "</ul>"
        )
    )
    erro = registro.get("erro")
    rodape = (
        f"confiança {registro.get('confianca')} · {registro.get('considerados')} item(ns) "
        f"considerados · {registro.get('duracao_s')}s"
        + (f" · esperou {registro['esperou_s']}s na fila" if registro.get("esperou_s") else "")
        + (" · prompt truncado pelo orçamento" if registro.get("truncado") else "")
    )
    return substituir(
        _CHAT_ITEM,
        classe="troca-erro" if erro else "",
        pergunta=registro.get("pergunta"),
        texto=erro or registro.get("texto") or "(resposta vazia)",
        rodape="não chamou o modelo" if erro else rodape,
        usados=lista_usados,
    )


def _view_chat(estado: Estado, consulta: dict, pergunta: str = "") -> str:
    situacao = estado.chat.situacao()
    corpo = substituir(
        """<h1>Chat sobre o acervo</h1>
<p class=detalhe>A mesma função que <code>dlb buscar</code> usa, com o mesmo acervo:
a resposta aqui e no terminal são a mesma. O modelo é <code>$backend</code>, rodando
por binário local — sem chave de API.</p>
<form method=get action="/chat" class=busca>
  <input type=search name=q value="$pergunta" placeholder="o que você quer saber do que já passou por você?" autofocus>
  <button type=submit>perguntar</button>
</form>
<p class=detalhe>$situacao</p>
<section class=trocas>$trocas</section>""",
        backend=situacao["backend"],
        pergunta=pergunta,
        situacao=(
            f"{situacao['chamadas_na_hora']} de {situacao['teto_por_hora']} chamadas na última hora"
            + (" · uma pergunta rodando agora" if situacao["ocupado"] else "")
            + " · uma chamada de modelo por vez no painel inteiro"
        ),
        trocas=juntar([_troca(r.como_dict()) for r in estado.chat.historico], "")
        or Raw("<p class=vazio>Nenhuma pergunta ainda nesta sessão do painel.</p>"),
    )
    return pagina("Chat", corpo, ativo="/chat", vault=str(estado.cfg.vault_path))


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
            elif rota == "/agenda":
                self._html(_view_agenda(self.estado), corpo)
            elif rota == "/chat":
                self._chat(consulta, corpo, html=True)
            elif rota == "/acervo":
                self._html(_view_acervo(self.estado, consulta), corpo)
            elif rota == "/api/estado":
                self._json(self.estado.com_alertas(), corpo)
            elif rota == "/api/execucoes":
                self._json(
                    {"execucoes": self.estado.execucoes(limite=_inteiro(consulta, "limite", 100))},
                    corpo,
                )
            elif rota == "/api/agenda":
                self._json(self.estado.agenda(), corpo)
            elif rota == "/api/chat":
                self._chat(consulta, corpo, html=False)
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

    def _chat(self, consulta: dict, corpo: bool, html: bool) -> None:
        """A única rota que gasta modelo. Recusa requisição vinda de outro site.

        `Sec-Fetch-Site` é enviado pelo navegador e diz de onde a requisição
        partiu. Sem esta guarda, uma página hostil aberta na mesma máquina
        poderia queimar a janela de uso com `<img src="/chat?q=...">` — pedido que
        não manda `Origin` nenhum, então checar `Origin` não pegaria. Cliente que
        não manda o cabeçalho (curl, script) passa: a guarda é contra navegador,
        que é quem tem o problema.
        """
        origem = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if origem and origem not in {"same-origin", "none"}:
            self._json(
                {"erro": "requisição de outro site não pode gastar modelo", "sec_fetch_site": origem},
                corpo,
                codigo=403,
            )
            return

        pergunta = (consulta.get("q") or [""])[0].strip()
        if pergunta:
            registro = self.estado.chat.perguntar(pergunta)
        else:
            registro = None
        if html:
            self._html(_view_chat(self.estado, consulta, pergunta), corpo)
            return
        self._json(
            {
                "situacao": self.estado.chat.situacao(),
                "resposta": registro.como_dict() if registro else None,
            },
            corpo,
        )

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
