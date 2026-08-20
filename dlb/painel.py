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
import secrets
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import acoes, agenda, chat as chat_mod, compose, env_io, gestor, journal, painel_schema
from .acervo import Acervo, filtrar, resumo
from .config import Config
from .models import TRIAGENS
from .render import Raw, juntar, lista, pagina, substituir, tabela

ESTATICOS = Path(__file__).parent / "static"

# Só estes nomes são servidos de `static/`. Lista branca em vez de sanear o
# caminho: `..%2f` e amigos são um jogo que não se ganha por filtro.
ARQUIVOS_ESTATICOS = {
    "painel.css": "text/css; charset=utf-8",
    "painel.js": "application/javascript; charset=utf-8",
}

LOOPBACK = {"127.0.0.1", "::1", "localhost"}

# Teto do corpo de um POST. O formulário de configuração inteiro, com as 55 chaves
# preenchidas, não passa de alguns KB — 256 KB é folga de duas ordens de grandeza
# e ainda impede que alguém prenda memória mandando um corpo infinito.
CORPO_MAXIMO = 256 * 1024


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

        # Token de CSRF por processo. Sem cookie e sem sessão: o token vai no
        # formulário, e uma página de outro site não consegue lê-lo por causa da
        # política de mesma origem. É o que impede um POST cego de fora — que é o
        # modelo de ameaça real aqui (navegador hostil na mesma máquina), não DoS.
        self.csrf = secrets.token_urlsafe(32)

        # Resultado do último POST, mostrado no GET seguinte. POST-redirect-GET
        # existe para F5 no navegador não reenviar o formulário ao recarregar.
        self.ultimo_aviso: Raw = Raw("")

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

    def config(self) -> dict:
        """A configuração como a tela precisa: valor atual, pendência e schema.

        `pendentes` é a lista de chaves em que o `.env` já mudou e o container
        ainda não sabe. É o estado de produto **salvo ≠ aplicado**, e ele não é
        cosmético: `restart` não relê o `.env`, então sem esta lista o Dotcom teria
        de adivinhar se o valor que ele digitou está valendo.
        """
        do_arquivo = env_io.ler(self.cfg.env_path)
        inspecao = compose.inspecionar()
        no_container = (inspecao or {}).get("env")
        chaves = tuple(campo.chave for campo in painel_schema.CONFIG_SCHEMA)
        pendentes = compose.divergencias(do_arquivo, no_container, chaves)

        # Segredo é write-only, e isso vale para a API igual à tela: o valor sai
        # do dicionário aqui, no único lugar que monta os dois. Redigir só na
        # renderização deixaria `/api/config` vazando o token do Discord — foi o
        # que o teste pegou.
        visiveis = dict(do_arquivo)
        preenchidos = {}
        for campo in painel_schema.CONFIG_SCHEMA:
            if campo.segredo:
                preenchidos[campo.chave] = bool(visiveis.get(campo.chave))
                visiveis.pop(campo.chave, None)

        return {
            "env_path": str(self.cfg.env_path),
            "valores": visiveis,
            "segredos_preenchidos": preenchidos,
            "duplicadas": env_io.duplicadas(self.cfg.env_path),
            "container": inspecao,
            "pendentes": pendentes,
            "tem_backup": bool(env_io.tem_backup(self.cfg.env_path)),
        }

    def pegar_aviso(self) -> Raw:
        """Devolve e **limpa** a faixa do último POST.

        Consumir na leitura é o que faz o POST-redirect-GET funcionar: a mensagem
        aparece uma vez, na tela para onde a ação levou, e não fica grudada.
        """
        aviso, self.ultimo_aviso = self.ultimo_aviso, Raw("")
        return aviso

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
$aviso
<h2>Rodar agora</h2>
<p class=detalhe>Dispara <code>python3 -m dlb &lt;comando&gt;</code> — o mesmo caminho
do container, então passa pelo mesmo lock. Se outra execução estiver no ar, a sua é
recusada em vez de corromper o cursor.</p>
<div class=acoes>$botoes</div>
<nav class=filtros>$filtros</nav>
$tabela""",
        aviso=estado.pegar_aviso(),
        botoes=juntar(
            [
                substituir(
                    """<form method=post action="/executar">
  <input type=hidden name=csrf value="$csrf">
  <input type=hidden name=comando value="$comando">
  <input type=hidden name=volta value="/execucoes">
  <button type=submit class=secundario title="$ajuda">$comando</button>
</form>""",
                    csrf=estado.csrf,
                    comando=nome,
                    ajuda=ajuda,
                )
                for nome, ajuda in acoes.COMANDOS.items()
            ],
            "",
        ),
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


def _propostas_html(estado: Estado, registro) -> Raw:
    """O que o chat oferece depois de responder. Cada oferta é um formulário.

    Formulário, e não link: a ação escreve, então passa pela mesma fronteira de
    mutação do resto — CSRF, origem, 303. E o rótulo diz o que vai acontecer, em
    vez de um botão "aplicar" genérico.
    """
    ofertas = acoes.propostas(registro.pergunta, registro.texto, registro.usados)
    if not ofertas:
        return Raw("")
    blocos = []
    for oferta in ofertas:
        if oferta["tipo"] == "triagem":
            blocos.append(
                substituir(
                    """<form method=post action="/acervo/triar" class=proposta>
  <input type=hidden name=csrf value="$csrf">
  <input type=hidden name=nota value="$nota">
  <input type=hidden name=volta value="/chat">
  <span>$rotulo</span>
  <select name=para>$opcoes</select>
  <button type=submit class=secundario>marcar</button>
</form>""",
                    csrf=estado.csrf,
                    nota=oferta["nota"],
                    rotulo=oferta["rotulo"],
                    opcoes=juntar(
                        [substituir("<option value=\"$v\">$v</option>", v=o) for o in oferta["opcoes"]],
                        "",
                    ),
                )
            )
        else:
            blocos.append(
                substituir(
                    """<form method=post action="/executar" class=proposta>
  <input type=hidden name=csrf value="$csrf">
  <input type=hidden name=comando value="$comando">
  <input type=hidden name=volta value="/chat">
  <span>$rotulo</span>
  <button type=submit class=secundario>rodar $comando</button>
</form>""",
                    csrf=estado.csrf,
                    comando=oferta["comando"],
                    rotulo=oferta["rotulo"],
                )
            )
    return Raw("<div class=propostas>" + str(juntar(blocos, "")) + "</div>")


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
$aviso
<section class=trocas>$trocas</section>""",
        backend=situacao["backend"],
        pergunta=pergunta,
        situacao=(
            f"{situacao['chamadas_na_hora']} de {situacao['teto_por_hora']} chamadas na última hora"
            + (" · uma pergunta rodando agora" if situacao["ocupado"] else "")
            + " · uma chamada de modelo por vez no painel inteiro"
        ),
        aviso=estado.pegar_aviso(),
        trocas=juntar(
            [
                Raw(
                    str(_troca(r.como_dict()))
                    # As ofertas acompanham só a resposta mais recente: repetir
                    # botão em toda troca antiga transformaria a tela num painel
                    # de botões em vez de uma conversa.
                    + (str(_propostas_html(estado, r)) if i == 0 and not r.erro else "")
                )
                for i, r in enumerate(estado.chat.historico)
            ],
            "",
        )
        or Raw("<p class=vazio>Nenhuma pergunta ainda nesta sessão do painel.</p>"),
    )
    return pagina("Chat", corpo, ativo="/chat", vault=str(estado.cfg.vault_path))


_CAMPO_TEXTO = """<label class=campo>
  <span class=rotulo>$rotulo <code>$chave</code></span>
  <input type="$html_tipo" name="$chave" value="$valor" placeholder="$placeholder">
  <span class=ajuda>$ajuda</span>
</label>"""

_CAMPO_ESCOLHA = """<label class=campo>
  <span class=rotulo>$rotulo <code>$chave</code></span>
  <select name="$chave">$opcoes</select>
  <span class=ajuda>$ajuda</span>
</label>"""


def _placeholder(campo, guardado: bool) -> str:
    if not campo.segredo:
        return ""
    return "(guardado — deixe em branco para manter)" if guardado else "(vazio)"


def _campo_html(campo, valor: str, pendente: bool, guardado: bool = False) -> Raw:
    """Um controle por chave. Segredo nunca volta preenchido."""
    ajuda = campo.ajuda
    if campo.onde_vale == "agendador":
        ajuda = (ajuda + " " if ajuda else "") + "Só vale depois de aplicar (recriar o container)."
    if pendente:
        ajuda = (ajuda + " " if ajuda else "") + "⚠ salvo no .env, ainda não aplicado."

    if campo.tipo == "escolha" or campo.tipo == "booleano":
        opcoes = ("0", "1") if campo.tipo == "booleano" else campo.opcoes
        rotulos = {"0": "0 — desligado", "1": "1 — ligado"} if campo.tipo == "booleano" else {}
        return substituir(
            _CAMPO_ESCOLHA,
            rotulo=campo.rotulo,
            chave=campo.chave,
            ajuda=ajuda,
            opcoes=juntar(
                [
                    substituir(
                        '<option value="$v"$sel>$rotulo</option>',
                        v=o,
                        sel=Raw(" selected") if o == valor else "",
                        rotulo=rotulos.get(o, o),
                    )
                    for o in opcoes
                ],
                "",
            ),
        )
    return substituir(
        _CAMPO_TEXTO,
        rotulo=campo.rotulo,
        chave=campo.chave,
        ajuda=ajuda,
        html_tipo="password" if campo.segredo else ("number" if campo.tipo == "numero" else "text"),
        valor="" if campo.segredo else valor,
        placeholder=_placeholder(campo, guardado),
    )


def _efeito_do_aplicar(dados: dict) -> str:
    """Avisa que recriar o container **dispara um ciclo na hora**.

    Descoberto aplicando de verdade: com `DLB_RUN_ON_START=1` — que é o padrão — o
    container recém-criado roda sync, backfill, síntese e ressurgir imediatamente.
    Num teste de três aplicares seguidos isso mastigou nove dias de histórico e
    gastou chamadas de modelo que ninguém agendou.

    O aviso lê o ambiente do container, não o arquivo: o que decide é o que está
    valendo agora.
    """
    ambiente = ((dados.get("container") or {}).get("env")) or {}
    if (ambiente.get("DLB_RUN_ON_START") or "1").strip() != "1":
        return ""
    liga = [
        nome
        for chave, nome in (
            ("DLB_BACKFILL", "backfill"),
            ("DLB_SINTESE", "síntese"),
            ("DLB_RESSURGIR", "ressurgir"),
        )
        if (ambiente.get(chave) or "0").strip() == "1"
    ]
    extra = f" — e {', '.join(liga)} junto" if liga else ""
    return (
        f" · atenção: DLB_RUN_ON_START=1, então recriar dispara um sync na hora{extra}, "
        "gastando chamada de modelo"
    )


def _view_config(estado: Estado, aviso: Raw = Raw(""), erros: tuple = ()) -> str:
    dados = estado.config()
    valores = dados["valores"]
    pendentes = {d["chave"] for d in dados["pendentes"]}

    grupos = []
    for grupo in painel_schema.GRUPOS:
        campos = [c for c in painel_schema.CONFIG_SCHEMA if c.grupo == grupo]
        grupos.append(
            substituir(
                "<fieldset><legend>$grupo</legend>$campos</fieldset>",
                grupo=grupo,
                campos=juntar(
                    [
                        _campo_html(
                            c,
                            valores.get(c.chave, ""),
                            c.chave in pendentes,
                            dados["segredos_preenchidos"].get(c.chave, False),
                        )
                        for c in campos
                    ],
                    "",
                ),
            )
        )

    faixa_pendente = (
        Raw("")
        if not dados["pendentes"]
        else substituir(
            """<article class='faixa faixa-atencao'>
  <h3>Salvo, ainda não aplicado</h3>
  <p class=evidencia>$quais</p>
  <p class=acao>Aplicar recria o container com <code>./dlb.sh prod up</code>.
  <code>restart</code> não relê o `.env` — por isso não existe esse botão aqui.</p>
</article>""",
            quais=", ".join(
                d["chave"]
                if painel_schema.POR_CHAVE[d["chave"]].segredo
                else f"{d['chave']}: arquivo {d['arquivo'] or '(vazio)'} × container {d['container'] or '(vazio)'}"
                for d in dados["pendentes"]
            ),
        )
    )

    faixa_erros = (
        Raw("")
        if not erros
        else substituir(
            "<article class='faixa faixa-alerta'><h3>Nada foi salvo</h3>$lista</article>",
            lista=lista(erros),
        )
    )

    faixa_dup = (
        Raw("")
        if not dados["duplicadas"]
        else substituir(
            "<article class='faixa faixa-alerta'><p>Chave repetida no arquivo: $quais. "
            "A primeira ocorrência é a que vale; salvar aqui remove a duplicata.</p></article>",
            quais=", ".join(
                f"{k} (linhas {', '.join(map(str, v))})" for k, v in dados["duplicadas"].items()
            ),
        )
    )

    desfazer = (
        Raw("")
        if not dados["tem_backup"]
        else substituir(
            """<form method=post action="/config/desfazer" class=acoes>
  <input type=hidden name=csrf value="$csrf">
  <button type=submit class=secundario>desfazer (volta o .env do backup)</button>
</form>""",
            csrf=estado.csrf,
        )
    )

    corpo = substituir(
        """<h1>Configuração</h1>
<p class=detalhe>Escrevendo em <code>$env_path</code>. O arquivo é a intenção; o
container é o que está valendo. Segredo é write-only: some da tela e não volta.</p>
$aviso
$faixa_erros
$faixa_dup
$faixa_pendente
<form method=post action="/config/salvar">
  <input type=hidden name=csrf value="$csrf">
  $grupos
  <div class=acoes>
    <button type=submit>salvar no .env</button>
  </div>
</form>
<form method=post action="/config/aplicar" class=acoes>
  <input type=hidden name=csrf value="$csrf">
  <button type=submit $desabilitado>aplicar (recria o container)</button>
  <span class=ajuda>$quantas$efeito</span>
</form>
$desfazer""",
        env_path=dados["env_path"],
        aviso=aviso,
        faixa_erros=faixa_erros,
        faixa_dup=faixa_dup,
        faixa_pendente=faixa_pendente,
        csrf=estado.csrf,
        grupos=juntar(grupos, ""),
        desabilitado=Raw("") if dados["pendentes"] else Raw("disabled"),
        quantas=(
            f"{len(dados['pendentes'])} chave(s) esperando"
            if dados["pendentes"]
            else "nada pendente"
        ),
        efeito=_efeito_do_aplicar(dados),
        desfazer=desfazer,
    )
    return pagina("Configuração", corpo, ativo="/config", vault=str(estado.cfg.vault_path))


_TRIAR = """<form method=post action="/acervo/triar" class=triar>
  <input type=hidden name=csrf value="$csrf">
  <input type=hidden name=nota value="$nota">
  <input type=hidden name=volta value="$volta">
  <select name=para aria-label="triagem de $nota">$opcoes</select>
  <button type=submit>ok</button>
</form>"""


def _form_triagem(estado: Estado, nome: str, atual: str, volta: str) -> Raw:
    """O controle que grava julgamento humano — o único que escreve no vault.

    As quatro opções vêm de `models.TRIAGENS`, que é o vocabulário do `ESQUEMA` do
    vault. A atual aparece selecionada, e mandar a mesma é recusado com mensagem em
    vez de reescrever o arquivo por nada.
    """
    return substituir(
        _TRIAR,
        csrf=estado.csrf,
        nota=nome,
        volta=volta,
        opcoes=juntar(
            [
                substituir(
                    '<option value="$v"$sel>$v</option>',
                    v=opcao,
                    sel=Raw(" selected") if opcao == (atual or "inbox") else "",
                )
                for opcao in TRIAGENS
            ],
            "",
        ),
    )


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

    volta = "/acervo" + (f"?q={termo}" if termo else "") + (
        ("&" if termo else "?") + f"triagem={triagem}" if triagem else ""
    )
    linhas = [
        [
            e.dia or "—",
            substituir('<a href="$url" rel="noreferrer noopener">$titulo</a>', url=e.url, titulo=e.titulo or e.nome),
            e.dominio,
            e.categoria,
            _form_triagem(estado, e.nome, e.triagem, volta),
            "—" if e.nota is None else e.nota,
        ]
        for e in reversed(achados[-300:])
    ]

    corpo = substituir(
        """<h1>Acervo</h1>
<p class=detalhe>$total notas em <code>50_LINKS/</code>. O vault é a verdade: isto
é leitura sob demanda, sem índice em banco.</p>
$aviso
<form method=get action="/acervo" class=busca>
  <input type=search name=q value="$termo" placeholder="título, resumo, domínio, conceito…">
  <input type=hidden name=triagem value="$triagem">
  <button type=submit>buscar</button>
</form>
<nav class=filtros>$facetas</nav>
<p class=detalhe>$quantos</p>
$tabela""",
        total=dados["total"],
        aviso=estado.pegar_aviso(),
        termo=termo,
        triagem=triagem,
        facetas=facetas,
        quantos=(
            f"{len(achados)} nota(s) no filtro"
            + (" · mostrando as 300 mais recentes" if len(achados) > 300 else "")
        ),
        tabela=tabela(
            ["dia", "título", "domínio", "categoria", "triagem (edita)", "nota"],
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
            b'{"erro":"metodo nao suportado neste painel"}',
            "application/json; charset=utf-8",
            extras={"Allow": "GET, HEAD, POST"},
        )

    do_PUT = do_DELETE = do_PATCH = _metodo_nao_permitido

    # ------------------------------------------------------------- mutação

    def do_POST(self) -> None:
        """As três únicas rotas que escrevem. Fronteira antes de qualquer efeito.

        A ordem importa: limite de corpo, origem, CSRF, e só então a ação. Validar
        depois de ler um corpo sem teto já teria deixado alguém prender memória.
        """
        rota = urlparse(self.path).path.rstrip("/") or "/"
        if rota not in {
            "/config/salvar",
            "/config/aplicar",
            "/config/desfazer",
            "/acervo/triar",
            "/executar",
        }:
            self._metodo_nao_permitido()
            return
        try:
            corpo = self._ler_corpo()
        except ValueError as exc:
            self._json({"erro": str(exc)}, True, codigo=413)
            return
        if not self._origem_confiavel():
            self._json({"erro": "origem cruzada recusada"}, True, codigo=403)
            return
        campos = {k: v[0] if v else "" for k, v in parse_qs(corpo, keep_blank_values=True).items()}
        if not secrets.compare_digest(campos.get("csrf", ""), self.estado.csrf):
            self._json({"erro": "token de formulário inválido — recarregue a página"}, True, codigo=403)
            return
        campos.pop("csrf", None)
        try:
            if rota == "/config/salvar":
                self._salvar(campos)
            elif rota == "/config/aplicar":
                self._aplicar()
            elif rota == "/config/desfazer":
                self._desfazer()
            elif rota == "/acervo/triar":
                self._triar(campos)
                self._redirecionar(campos.get("volta") or "/acervo")
                return
            else:
                self._executar(campos)
                self._redirecionar(campos.get("volta") or "/execucoes")
                return
        except acoes.Recusado as exc:
            # Recusa é resposta, não erro: nome de nota que não existe, triagem
            # que já é a atual, comando fora da lista branca.
            self.estado.ultimo_aviso = substituir(
                "<article class='faixa faixa-atencao'><p>$erro</p></article>", erro=str(exc)
            )
            self._redirecionar(campos.get("volta") or "/acervo")
            return
        except Exception as exc:  # noqa: BLE001
            self.log_message("erro em %s: %s: %s", rota, type(exc).__name__, exc)
            self.estado.ultimo_aviso = substituir(
                "<article class='faixa faixa-alerta'><h3>Falhou</h3><p>$erro</p></article>",
                erro=f"{type(exc).__name__}: {exc}",
            )
        self._redirecionar("/config")

    def _ler_corpo(self) -> str:
        tamanho = self.headers.get("Content-Length")
        try:
            n = int(tamanho or 0)
        except ValueError:
            raise ValueError("Content-Length inválido") from None
        if n > CORPO_MAXIMO:
            raise ValueError(f"corpo de {n} bytes passa do teto de {CORPO_MAXIMO}")
        return self.rfile.read(n).decode("utf-8", errors="replace")

    def _origem_confiavel(self) -> bool:
        """`Origin` (ou `Referer`) tem de bater com o `Host` que atendeu.

        Navegador manda `Origin` em todo POST de formulário cross-site, então esta
        é a checagem que pega o caso real. Ausência de `Origin` é cliente que não é
        navegador (curl), e aí o CSRF token — que ele não tem como adivinhar — é
        quem barra.
        """
        origem = self.headers.get("Origin") or ""
        if not origem:
            referer = self.headers.get("Referer") or ""
            if not referer:
                return True
            origem = referer
        alvo = (self.headers.get("Host") or "").strip().lower()
        try:
            vinda = urlparse(origem).netloc.strip().lower()
        except ValueError:
            return False
        return bool(alvo) and vinda == alvo

    def _redirecionar(self, para: str) -> None:
        """303 depois de POST: recarregar a página não reenvia o formulário."""
        self.send_response(303)
        self.send_header("Location", para)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _salvar(self, campos: dict) -> None:
        estado = self.estado
        # Segredo em branco significa "mantenha o que está lá" — write-only. Sem
        # isto, abrir a tela e salvar apagaria o DISCORD_TOKEN.
        for campo in painel_schema.CONFIG_SCHEMA:
            if campo.segredo and not (campos.get(campo.chave) or "").strip():
                campos.pop(campo.chave, None)

        limpos, erros = painel_schema.validar_tudo(campos)
        if erros:
            # Nada é escrito: aplicar metade de um formulário deixaria o `.env`
            # num estado que ninguém pediu.
            estado.ultimo_aviso = substituir(
                "<article class='faixa faixa-alerta'><h3>Nada foi salvo</h3>$lista</article>",
                lista=lista(erros),
            )
            return
        resultado = env_io.escrever(estado.cfg.env_path, limpos)
        if not resultado.mudadas:
            estado.ultimo_aviso = Raw(
                "<article class='faixa faixa-info'><p>Nada mudou — os valores já eram esses.</p></article>"
            )
            return
        estado.ultimo_aviso = substituir(
            """<article class='faixa faixa-info'>
  <h3>Salvo em $arquivo</h3>
  <p class=evidencia>$quais</p>
  <p class=acao>Backup em $backup. Para o container passar a usar, clique em aplicar.</p>
</article>""",
            arquivo=str(estado.cfg.env_path),
            quais=", ".join(sorted(resultado.mudadas)),
            backup=str(resultado.backup) if resultado.backup else "(não havia arquivo antes)",
        )

    def _aplicar(self) -> None:
        estado = self.estado
        # Recriar o container é job: entra no histórico como qualquer outro, para
        # "por que o painel ficou fora do ar às 3h" ter resposta.
        with journal.registrar(estado.cfg.state_dir, "aplicar", ["config", "aplicar"]) as run:
            resultado = compose.aplicar(estado.cfg.env_path.parent)
            run.contar("ok" if resultado["ok"] else "falha")
            run.fechar(exit_code=0 if resultado["ok"] else 1, erro=None if resultado["ok"] else resultado["saida"][:300])
        estado.ultimo_aviso = substituir(
            """<article class='faixa faixa-$tom'>
  <h3>$titulo</h3>
  <p class=evidencia><code>$comando</code> → código $codigo</p>
  <pre class=saida>$saida</pre>
</article>""",
            tom="info" if resultado["ok"] else "alerta",
            titulo="Container recriado com o .env atual" if resultado["ok"] else "O aplicar falhou",
            comando=resultado["comando"],
            codigo="—" if resultado["codigo"] is None else resultado["codigo"],
            saida=resultado["saida"] or "(sem saída)",
        )

    def _desfazer(self) -> None:
        estado = self.estado
        voltou = env_io.restaurar(estado.cfg.env_path)
        estado.ultimo_aviso = Raw(
            "<article class='faixa faixa-info'><p>O <code>.env</code> voltou ao backup, "
            "byte a byte. O container segue com o que tinha até você aplicar.</p></article>"
        )
        if not voltou:
            estado.ultimo_aviso = Raw(
                "<article class='faixa faixa-alerta'><p>Não havia backup para voltar.</p></article>"
            )

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
            elif rota == "/config":
                self._html(_view_config(self.estado, aviso=self.estado.pegar_aviso()), corpo)
            elif rota == "/api/config":
                self._json(self.estado.config(), corpo)
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

    def _triar(self, campos: dict) -> None:
        estado = self.estado
        resultado = acoes.triar(estado.cfg, campos.get("nota", ""), campos.get("para", ""))
        # O memo do acervo é por `(mtime_ns, size)`: a nota mudou, então a próxima
        # leitura relê **só ela**. Não precisa invalidar nada à mão.
        estado.ultimo_aviso = substituir(
            "<article class='faixa faixa-info'><p><strong>$nota</strong>: "
            "triagem <code>$de</code> → <code>$para</code>. Já está no arquivo do vault; "
            "recarregue no Obsidian para ver.</p></article>",
            nota=resultado.nota,
            de=resultado.de,
            para=resultado.para,
        )

    def _executar(self, campos: dict) -> None:
        estado = self.estado
        comando = (campos.get("comando") or "").strip()
        resultado = acoes.rodar(estado.cfg, comando)
        tom = "info" if resultado.ok else ("atencao" if resultado.recusado else "alerta")
        titulo = (
            f"{comando} terminou"
            if resultado.ok
            else (f"{comando} foi recusado — outra execução está com o lock"
                  if resultado.recusado
                  else f"{comando} falhou")
        )
        estado.ultimo_aviso = substituir(
            """<article class='faixa faixa-$tom'>
  <h3>$titulo</h3>
  <p class=evidencia>código $codigo</p>
  <pre class=saida>$saida</pre>
</article>""",
            tom=tom,
            titulo=titulo,
            codigo="—" if resultado.codigo is None else resultado.codigo,
            saida=resultado.saida,
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
