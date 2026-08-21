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

import hmac
import json
import secrets
import signal
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import acoes, agenda, chat as chat_mod, compose, env_io, gestor, journal, painel_dados, painel_schema, svg
from .acervo import Acervo, filtrar, resumo
from .config import Config
from .models import TRIAGENS
from .render import Raw, escapar, juntar, lista, pagina, substituir, tabela

ESTATICOS = Path(__file__).parent / "static"

# Só estes nomes são servidos de `static/`. Lista branca em vez de sanear o
# caminho: `..%2f` e amigos são um jogo que não se ganha por filtro.
ARQUIVOS_ESTATICOS = {
    "painel.css": "text/css; charset=utf-8",
    "painel.js": "application/javascript; charset=utf-8",
}

LOOPBACK = {"127.0.0.1", "::1", "localhost"}

COOKIE = "dlb_painel"

# Teto do corpo de um POST. O formulário de configuração inteiro, com as 55 chaves
# preenchidas, não passa de alguns KB — 256 KB é folga de duas ordens de grandeza
# e ainda impede que alguém prenda memória mandando um corpo infinito.
CORPO_MAXIMO = 256 * 1024

# Tempo real do dashboard. Cinco segundos porque nada aqui muda mais rápido: o
# heartbeat do sync é escrito a cada ciclo de horas, e o relógio da tela é
# atualizado no **cliente**, sem rede. Um segundo custaria cinco vezes mais e não
# mostraria nada novo.
SSE_INTERVALO_S = 5.0

# `ThreadingHTTPServer` gasta uma thread por conexão, e SSE não fecha. Quatro é
# folga para um usuário com abas abertas; sem teto, aba recarregada mil vezes
# viraria mil threads.
SSE_MAXIMO = 4


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

        # Agregados do dashboard, memorizados por versão do acervo. É o que faz o
        # tempo real custar 0,3 ms por tick em vez de 5 ms: enquanto nenhuma nota
        # muda, o dicionário volta como está.
        self.agregado = painel_dados.Agregado()

        # Instante em que este processo subiu, para o painel dizer desde quando
        # está de pé sem depender do journal.
        self.subiu_em = time.time()

        # Token de CSRF por processo. Sem cookie e sem sessão: o token vai no
        # formulário, e uma página de outro site não consegue lê-lo por causa da
        # política de mesma origem. É o que impede um POST cego de fora — que é o
        # modelo de ameaça real aqui (navegador hostil na mesma máquina), não DoS.
        self.csrf = secrets.token_urlsafe(32)

        # Resultado do último POST, mostrado no GET seguinte. POST-redirect-GET
        # existe para F5 no navegador não reenviar o formulário ao recarregar.
        self.ultimo_aviso: Raw = Raw("")

        # Sessões abertas, em memória. Reiniciar o painel derruba todas, e isso é
        # aceitável: são segundos para entrar de novo, e guardar sessão em disco
        # pediria decidir expiração, rotação e onde o arquivo mora — peça a mais
        # para um painel de um usuário.
        self.sessoes: set[str] = set()

    @property
    def exige_token(self) -> bool:
        return bool(self.cfg.painel_token)

    def hosts_aceitos(self, endereco_de_bind: str = "") -> set[str]:
        """Nomes que este painel responde. Fora deles, 421.

        Três origens, e nenhuma é adivinhação:

        - **loopback e o endereço de bind** — o que o próprio servidor atende;
        - **o hostname da máquina** — é o que se digita numa tailnet com MagicDNS
          (`http://dotcom:8765`), e um atacante não controla esse nome, então
          aceitá-lo não abre o caminho de rebinding;
        - **`PAINEL_HOSTS`** — para nome completo de MagicDNS ou domínio de proxy,
          declarado por quem opera em vez de deduzido por mim.
        """
        aceitos = set(LOOPBACK) | {"0.0.0.0", "[::]"}
        if endereco_de_bind:
            aceitos.add(str(endereco_de_bind).lower().strip("[]"))
        for nome in (socket.gethostname(), socket.getfqdn()):
            if nome:
                aceitos.add(nome.lower().rstrip("."))
        for extra in (self.cfg.painel_hosts or "").split(","):
            limpo = extra.strip().lower().rstrip(".")
            if limpo:
                aceitos.add(limpo)
        return aceitos

    def entrar(self, token: str) -> str | None:
        """Confere o token e abre sessão. `None` se não bateu.

        `hmac.compare_digest` porque comparar segredo com `==` vaza o tamanho do
        prefixo comum pelo tempo de resposta. É barato de fazer certo.
        """
        if not self.exige_token:
            return None
        if not hmac.compare_digest(token or "", self.cfg.painel_token):
            return None
        sessao = secrets.token_urlsafe(32)
        self.sessoes.add(sessao)
        return sessao

    def sessao_valida(self, cookie: str) -> bool:
        return any(hmac.compare_digest(cookie or "", s) for s in self.sessoes)

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
        # Chave que só o painel lê não entra na comparação com o container:
        # mostrá-la como pendente mandaria recriar o container por causa de uma
        # configuração que ele não usa. Ela vale ao reiniciar o painel.
        chaves = tuple(
            campo.chave
            for campo in painel_schema.CONFIG_SCHEMA
            if campo.onde_vale != "painel"
        )
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

    def dashboard(self) -> dict:
        """Tudo que o dashboard mostra, num dicionário — a tela e a API leem o mesmo.

        Uma fonte para as duas saídas: se a API e o HTML calculassem separado,
        divergiriam no dia em que alguém mexesse num só.
        """
        entradas = self.links()
        agregado = self.agregado.de(entradas, tz=self.cfg.timezone)
        cursores = self.cursores()
        ultimos = self.ultimos()
        visao = {
            "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "vault": str(self.cfg.vault_path),
            "heartbeats": self.heartbeats(),
            "tolerancia_s": {
                "sync": self.cfg.health_tolerance,
                "responder": self.cfg.responder_health_tolerance,
            },
            "acervo": resumo(entradas),
            "cursores": cursores,
            "ultimas_execucoes": self.execucoes(limite=24),
            "ultimos": ultimos,
            "lock": self.lock(),
        }
        visao["alertas"] = [a.como_dict() for a in gestor.avaliar(visao)]
        visao["agregado"] = agregado
        visao["pendencias"] = painel_dados.pendencias(agregado, cursores, ultimos)
        visao["leitura"] = vars(self.acervo.ultima)
        visao["custo"] = self.agregado.estatisticas()
        visao["painel_de_pe_s"] = round(time.time() - self.subiu_em)
        visao["agenda"] = self._agenda_curta()
        return visao

    def _agenda_curta(self) -> dict:
        """Só o que o dashboard precisa da agenda: modo e próximo disparo.

        Não chama `docker inspect` — aqui o dado vem do arquivo. O `docker inspect`
        custa dezenas de milissegundos e é decisão certa na tela de Agenda, que é
        sobre exatidão; num dashboard que atualiza sozinho, seria um subprocess por
        tick.
        """
        do_arquivo = env_io.ler(self.cfg.env_path)
        modo = agenda.modo_de(do_arquivo)
        proximo = agenda.proximo(do_arquivo, datetime.now(agenda.fuso(do_arquivo)))
        return {
            "modo": modo.nome,
            "detalhe": modo.detalhe,
            "proximo": proximo.isoformat(timespec="seconds") if proximo else None,
            "avisos": modo.avisos,
        }

    def versao_do_acervo(self) -> str:
        """Assinatura curta do estado visível, para o cliente saber se vale recarregar."""
        entradas = self.links()
        quantos, mtime = painel_dados.versao(entradas)
        return f"{quantos}-{mtime}"

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


_HERO = """<section class="hero">
  <div class="hero-num">
    <p class=hero-rotulo>no acervo</p>
    <p class=hero-valor data-vivo=total>$total</p>
    <p class=hero-detalhe>$periodo</p>
  </div>
  <div class="hero-kpis">$kpis</div>
</section>"""

_KPI = """<article class="kpi $tom">
  <p class=kpi-rotulo>$rotulo</p>
  <p class=kpi-valor$atributo>$valor</p>
  <p class=kpi-detalhe>$detalhe</p>
</article>"""


def _kpi(rotulo: str, valor: object, detalhe: str = "", tom: str = "", vivo: str = "") -> Raw:
    return substituir(
        _KPI,
        rotulo=rotulo,
        valor=valor,
        detalhe=detalhe,
        tom=tom,
        atributo=Raw(f' data-vivo="{escapar(vivo)}"') if vivo else Raw(""),
    )


_CARTA = """<article class="carta">
  <header><h3>$titulo</h3><span class=carta-nota>$nota</span></header>
  $corpo
</article>"""


def _carta(titulo: str, corpo: Raw, nota: str = "") -> Raw:
    """Um gráfico por carta, com título que **nomeia a série** — daí não haver legenda
    em gráfico de série única: a legenda repetiria o título e custaria espaço."""
    return substituir(_CARTA, titulo=titulo, nota=nota, corpo=corpo)


def _delta(n: int) -> str:
    if n > 0:
        return f"↑ {n} vs. os 7 dias anteriores"
    if n < 0:
        return f"↓ {abs(n)} vs. os 7 dias anteriores"
    return "igual aos 7 dias anteriores"


def _view_dashboard(estado: Estado) -> str:
    d = estado.dashboard()
    ag = d["agregado"]
    bat, tol = d["heartbeats"], d["tolerancia_s"]

    kpis = juntar([
        _kpi("últimos 7 dias", ag["ultimos7"], _delta(ag["delta7"]),
             tom="bom" if ag["delta7"] >= 0 else ""),
        _kpi("hoje", ag["hoje"], f"média de {ag['media_dia']}/dia na semana"),
        _kpi("agendador", _humano(bat["sync"]),
             f"tolera {_humano(tol['sync'])}",
             tom=_tom_heartbeat(bat["sync"], tol["sync"]), vivo="hb-sync"),
        _kpi("responder", _humano(bat["responder"]), "volta de 60 em 60 s",
             tom=_tom_heartbeat(bat["responder"], tol["responder"]), vivo="hb-resp"),
    ], "")

    hero = substituir(
        _HERO,
        total=ag["total"],
        periodo=(f"de {ag['primeiro_dia']} a {ag['ultimo_dia']} · "
                 f"{ag['dias_com_captura']} dias com captura"),
        kpis=kpis,
    )

    # --- ritmo: série temporal de uma série só, um tom só, rótulo no extremo
    serie = [
        svg.Fatia(rotulo=x["dia"][5:], valor=x["quantos"],
                  dica=f"{x['dia']}: {x['quantos']} nota(s)")
        for x in ag["serie"]
    ]
    ritmo = _carta(
        "Captura por dia, últimos 30",
        svg.colunas(serie),
        f"{sum(x['quantos'] for x in ag['serie'])} notas na janela",
    )

    # --- triagem: rampa ordinal, porque a ordem do ciclo é o significado
    fatias_tri = [
        svg.Fatia(rotulo=k, valor=v, dica=f"{k}: {v} nota(s)")
        for k, v in ag["por_triagem"].items()
    ]
    triagem = _carta(
        "Triagem do acervo",
        Raw(str(svg.empilhada(fatias_tri))
            + str(svg.legenda_ordinal(fatias_tri))
            + str(svg.medidor(ag["triados"], ag["total"], "triado"))),
        "inbox → lido → aplicado | descartado",
    )

    categorias = _carta(
        "Por categoria",
        svg.barras([
            svg.Fatia(rotulo=x["rotulo"], valor=x["quantos"]) for x in ag["por_categoria"]
        ]),
        "o que você mais guarda",
    )

    dominios = _carta(
        "De onde vem",
        svg.barras([
            svg.Fatia(rotulo=x["rotulo"], valor=x["quantos"]) for x in ag["por_dominio"]
        ]),
        "domínio da URL",
    )

    # --- execuções: faixa de status, com legenda nomeando cada estado
    execucoes = list(reversed(d["ultimas_execucoes"]))
    estados = [
        (r.get("status") or "?",
         f"{(r.get('inicio') or '')[:19].replace('T', ' ')} · {r.get('comando')} · {r.get('status')}")
        for r in execucoes
    ]
    contagem: dict[str, int] = {}
    for e, _ in estados:
        contagem[e] = contagem.get(e, 0) + 1
    faixa = _carta(
        "Últimas execuções",
        Raw(str(svg.faixa_status(estados)) + str(svg.legenda_status(contagem))),
        f"{len(estados)} runs · mais antigo à esquerda",
    )

    pend = d["pendencias"]
    pendencias = _carta(
        "Esperando trabalho",
        tabela(
            ["o que", "quantos", "de quem", "onde"],
            [[x["o_que"], x["quantos"], x["de_quem"],
              substituir('<a href="$onde">$onde</a>', onde=x["onde"])
              if x["onde"].startswith("/") else x["onde"]]
             for x in pend],
            vazio="Nada pendente. Raro e bom.",
        ),
        "máquina e humano separados",
    )

    ag_curta = d["agenda"]
    proximo = ag_curta["proximo"]
    corpo = substituir(
        """<h1>Painel</h1>
$hero
$aviso_lock

<h2>Precisa de atenção</h2>
<section class=alertas>$alertas</section>

<section class=grade>
$ritmo
$triagem
$categorias
$dominios
$pendencias
$faixa
</section>

<h2>Agora</h2>
$agora
<p class=detalhe>$custo</p>""",
        hero=hero,
        aviso_lock=(
            substituir(
                "<p class=aviso>$comando está rodando agora (pid $pid, desde $desde).</p>",
                comando=(d["lock"] or {}).get("comando"),
                pid=(d["lock"] or {}).get("pid"),
                desde=(d["lock"] or {}).get("desde"),
            ) if d["lock"] else Raw("")
        ),
        alertas=_alertas(d["alertas"]),
        ritmo=ritmo,
        triagem=triagem,
        categorias=categorias,
        dominios=dominios,
        pendencias=pendencias,
        faixa=faixa,
        agora=tabela(
            ["o quê", "estado"],
            [
                ["próximo disparo",
                 Raw(f'<span data-vivo="proximo" data-alvo="{escapar(proximo or "")}">'
                     f'{escapar((proximo or "não ancorado")[11:16])}</span>')],
                ["modo do agendador", f"{ag_curta['modo']} — {ag_curta['detalhe']}"],
                ["painel de pé há",
                 Raw(f'<span data-vivo="uptime">{escapar(_humano(d["painel_de_pe_s"]))}</span>')],
                ["notas lidas do disco nesta visita",
                 f"{d['leitura']['lidos_do_disco']} de {d['leitura']['total']}"],
            ],
        ),
        custo=(
            f"agregado recalculado {d['custo']['calculos']}× e reusado do cache "
            f"{d['custo']['reusos']}× · último cálculo {d['custo']['ultimo_custo_ms']} ms"
        ),
    )
    return pagina(
        "Painel", corpo, ativo="/", vault=d["vault"],
        rodape=Raw('<span class="vivo vivo-frio"><span class="vivo-pulso"></span>'
                   "ao vivo · atualiza sozinho</span>"),
    )


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


_LOGIN = """<h1>Painel do dlb</h1>
<p class=detalhe>$motivo</p>
<form method=post action="/entrar" class=busca>
  <input type=password name=token placeholder="token do painel" autofocus>
  <button type=submit>entrar</button>
</form>"""


def _view_login(estado: Estado, motivo: str = "") -> str:
    return pagina(
        "Entrar",
        substituir(
            _LOGIN,
            motivo=motivo
            or "Este painel pede token (`PAINEL_TOKEN` no `.env`). A sessão vale até "
            "o painel ser reiniciado.",
        ),
        ativo="",
        vault=str(estado.cfg.vault_path),
    )


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
    elif campo.onde_vale == "painel":
        ajuda = (ajuda + " " if ajuda else "") + "Vale ao reiniciar o painel — o container não usa."
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
        if not self._host_esperado():
            self._json({"erro": "Host não atendido por este painel"}, True, codigo=421)
            return
        if rota == "/entrar":
            # Login vem antes de tudo: não há sessão para checar, e o CSRF token
            # da tela de login existe, mas exigi-lo aqui só criaria um caminho em
            # que ninguém consegue entrar depois de reiniciar o painel.
            try:
                corpo_bruto = self._ler_corpo()
            except ValueError as exc:
                self._json({"erro": str(exc)}, True, codigo=413)
                return
            if not self._origem_confiavel():
                self._json(self._diagnostico_de_origem(), True, codigo=403)
                return
            self._entrar(
                {k: v[0] if v else "" for k, v in parse_qs(corpo_bruto, keep_blank_values=True).items()}
            )
            return
        if not self._autorizado():
            self._json({"erro": "não autenticado"}, True, codigo=401)
            return
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
            self._json(self._diagnostico_de_origem(), True, codigo=403)
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

    def _host_esperado(self) -> bool:
        """O `Host` da requisição tem de ser um nome que este painel atende.

        Sem isto existe caminho de **DNS rebinding**: uma página em `evil.com`
        aponta o domínio para `127.0.0.1`, e o navegador manda
        `Host: evil.com:8765` com `Origin: http://evil.com`. Os dois batem entre
        si, então a checagem de origem passa — e, sendo mesma origem para o
        navegador, a página **lê a resposta** e com ela o token de CSRF.

        Comparar contra a lista do que o servidor atende quebra a cadeia no
        primeiro elo. É a defesa padrão para servidor local, e custa dez linhas.
        """
        bruto = (self.headers.get("Host") or "").strip().lower()
        if not bruto:
            # HTTP/1.1 exige `Host`. Ausente é cliente estranho, não navegador.
            return False
        nome = bruto.rsplit(":", 1)[0].strip("[]") if ":" in bruto else bruto
        # Ponto final é FQDN absoluto e legal no `Host`; `dotcom.ts.net.` e
        # `dotcom.ts.net` são o mesmo nome, e tratá-los como diferentes daria 421
        # em navegador que manda a forma absoluta.
        nome = nome.rstrip(".")
        porta = bruto.rsplit(":", 1)[1] if ":" in bruto else ""
        if porta and porta != str(self.server.server_port):   # type: ignore[attr-defined]
            return False
        return nome in self.estado.hosts_aceitos(
            getattr(self.server, "server_address", ("", 0))[0]
        )

    def _origem_confiavel(self) -> bool:
        """`Origin` (ou `Referer`) tem de ser um nome que este painel atende.

        A primeira versão comparava `Origin` com o `Host` **da própria
        requisição**, e isso quebra na hora em que existe proxy na frente: o
        navegador manda `Origin: https://maquina.SEU-TAILNET.ts.net` e o proxy
        reescreve o `Host` para `127.0.0.1:8765`. Os dois deixam de bater, e o
        painel recusava um login perfeitamente legítimo com "origem cruzada
        recusada" — foi o que aconteceu de verdade.

        Comparar com a **lista de nomes aceitos** (`hosts_aceitos`, que o operador
        declara em `PAINEL_HOSTS`) resolve os dois lados: não depende de cabeçalho
        que proxy reescreve, e continua recusando origem estranha.

        Compara só o nome, sem porta: atrás de proxy a porta pública (443) não é a
        interna. Isso deixa passar um POST vindo de outro serviço no **mesmo**
        nome, em outra porta — e aí quem barra é o token de CSRF, que aquela
        origem não consegue ler por causa da política de mesma origem.

        Ausência de `Origin` e de `Referer` é cliente que não é navegador (curl),
        e aí o CSRF token — que ele não tem como adivinhar — é quem barra.
        """
        origem = self.headers.get("Origin") or ""
        if origem.strip().lower() == "null":
            # Origem opaca: `file://`, iframe com sandbox, ou política de
            # referrer restritiva. Não é "origem hostil" nem "mesma origem" — é
            # ausência de informação, e o que decide nesse caso é o token de CSRF,
            # que uma página de outra origem não consegue ler (política de mesma
            # origem impede ler a resposta que o traz).
            origem = ""
        origem = origem or self.headers.get("Referer") or ""
        if not origem:
            return True
        try:
            nome = urlparse(origem).hostname or ""
        except ValueError:
            return False
        if not nome:
            return False
        return nome.strip().lower().rstrip(".") in self.estado.hosts_aceitos(
            getattr(self.server, "server_address", ("", 0))[0]
        )

    def _diagnostico_de_origem(self) -> dict:
        """A recusa diz **o que viu e o que aceita**, não só que recusou.

        "origem cruzada recusada" sozinho custou uma ida e volta de adivinhação
        com o Dotcom: sem o valor do `Origin`, nem eu nem ele sabíamos se era
        proxy, nome diferente ou processo velho. Estes campos são cabeçalhos do
        próprio navegador dele — não há segredo em devolvê-los, e há um dia de
        diferença em ter ou não.
        """
        return {
            "erro": "origem cruzada recusada",
            "origin_recebido": self.headers.get("Origin"),
            "referer_recebido": self.headers.get("Referer"),
            "host_recebido": self.headers.get("Host"),
            "nomes_aceitos": sorted(
                self.estado.hosts_aceitos(
                    getattr(self.server, "server_address", ("", 0))[0]
                )
            ),
            "como_resolver": "acrescente o nome em PAINEL_HOSTS no .env e reinicie o painel",
        }

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

    # ---------------------------------------------------------- autenticação

    def _cookie_sessao(self) -> str:
        """O valor do cookie de sessão, se houver. Parse à mão e sem tolerância.

        `http.cookies` resolveria, mas o formato aqui é um par só e o parser da
        stdlib tolera coisa que navegador não manda — menos superfície é melhor
        numa checagem de acesso.
        """
        bruto = self.headers.get("Cookie") or ""
        for pedaco in bruto.split(";"):
            nome, _, valor = pedaco.strip().partition("=")
            if nome == COOKIE:
                return valor.strip()
        return ""

    def _autorizado(self) -> bool:
        estado = self.estado
        if not estado.exige_token:
            # Sem token configurado o painel só sobe em loopback (ver `construir`),
            # então quem alcança a porta já está na máquina.
            return True
        return estado.sessao_valida(self._cookie_sessao())

    def _entrar(self, campos: dict) -> None:
        sessao = self.estado.entrar(campos.get("token", ""))
        if sessao is None:
            # Sem dizer se o token existe ou está errado, e sem tela de "tente
            # de novo" que conte tentativas: são dados que só ajudam quem adivinha.
            self._html(_view_login(self.estado, "Token não aceito."), True, codigo=401)
            return
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            # `HttpOnly`: JavaScript não lê, então um XSS não rouba a sessão.
            # `SameSite=Strict`: navegador não manda o cookie em requisição vinda
            # de outro site, o que fecha o CSRF por baixo do token de formulário.
            f"{COOKIE}={sessao}; HttpOnly; SameSite=Strict; Path=/",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _despachar(self, corpo: bool) -> None:
        alvo = urlparse(self.path)
        rota, consulta = alvo.path.rstrip("/") or "/", parse_qs(alvo.query)
        if not self._host_esperado():
            self._json(
                {"erro": "Host não atendido por este painel", "host": self.headers.get("Host")},
                corpo,
                codigo=421,
            )
            return
        # O CSS e o JS ficam abertos: a tela de login precisa deles, e não há nada
        # neles que não esteja no repositório.
        if not rota.startswith("/static/") and not self._autorizado():
            if rota.startswith("/api/"):
                self._json({"erro": "não autenticado"}, corpo, codigo=401)
            else:
                self._html(_view_login(self.estado), corpo, codigo=401)
            return
        try:
            if rota == "/":
                self._html(_view_dashboard(self.estado), corpo)
            elif rota == "/estado":
                # A tela anterior, tabular, continua existindo: o dashboard é para
                # olhar todo dia, esta é para conferir número por número.
                self._html(_view_painel(self.estado), corpo)
            elif rota == "/api/dashboard":
                self._json(self.estado.dashboard(), corpo)
            elif rota == "/api/ao-vivo":
                self._ao_vivo()
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

    def _ao_vivo(self) -> None:
        """Fluxo de eventos com os números que mudam sozinhos.

        O que **não** vai por aqui: SVG. Gráfico só muda quando o acervo muda, e aí o
        cliente recarrega a página inteira — uma vez por captura, algumas vezes ao
        dia. Mandar SVG a cada tick seria pagar kilobytes por uma imagem idêntica.

        O que vai: um punhado de números e a **versão do acervo**. O cliente compara
        a versão com a que ele tem e decide recarregar. Custo medido por tick, com o
        cache do agregado quente: **0,3 ms** de servidor.

        Teto de clientes porque `ThreadingHTTPServer` gasta uma thread por conexão
        aberta, e conexão de SSE não fecha. Num painel de um usuário, quatro abas é
        folga; sem teto, uma aba recarregada mil vezes viraria mil threads.
        """
        with self.server.trava_sse:                    # type: ignore[attr-defined]
            if self.server.sse_abertos >= SSE_MAXIMO:  # type: ignore[attr-defined]
                self._json({"erro": f"máximo de {SSE_MAXIMO} clientes ao vivo"}, True, codigo=503)
                return
            self.server.sse_abertos += 1               # type: ignore[attr-defined]
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            while True:
                dados = self._pulso()
                self.wfile.write(f"data: {json.dumps(dados, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                time.sleep(SSE_INTERVALO_S)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass    # aba fechada; é o fim normal de um SSE, não erro
        finally:
            with self.server.trava_sse:                # type: ignore[attr-defined]
                self.server.sse_abertos -= 1           # type: ignore[attr-defined]

    def _pulso(self) -> dict:
        estado = self.estado
        bat = estado.heartbeats()
        return {
            "versao": estado.versao_do_acervo(),
            "hb_sync": bat["sync"],
            "hb_responder": bat["responder"],
            "lock": (estado.lock() or {}).get("comando"),
            "chat_ocupado": estado.chat.ocupado,
        }

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
        # `same-origin`, não `no-referrer`. A diferença não é estética: com
        # `no-referrer` o navegador não manda `Referer` **e** serializa a origem
        # como opaca, mandando `Origin: null` no POST do formulário. O painel
        # recusava o próprio formulário por causa de um cabeçalho que eu tinha
        # posto por higiene — e nenhum teste pegou, porque teste manda os
        # cabeçalhos que o teste quer, não os que o navegador decide mandar.
        #
        # `same-origin` mantém a privacidade que interessa (nada vaza para
        # terceiro) e devolve `Origin` e `Referer` nas requisições do próprio
        # painel.
        self.send_header("Referrer-Policy", "same-origin")
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

    Fora de loopback **exige** `PAINEL_TOKEN`. Sem token, qualquer um que alcance a
    porta entra, e `http.server` não tem timeout de leitura nem teto de conexão:
    seria um servidor de borda sem autenticação e sem defesa contra conexão lenta.
    Com token ainda falta TLS, e o aviso na subida diz isso — token em HTTP puro só
    é aceitável dentro de rede já criptografada, como uma tailnet.
    """
    if host not in LOOPBACK and not cfg.painel_token:
        raise Erro(
            f"host {host!r} não é loopback e `PAINEL_TOKEN` está vazio. Sem token, quem "
            "alcança a porta entra. Defina PAINEL_TOKEN no .env, ou use "
            "`ssh -N -L 8765:127.0.0.1:8765` e continue em loopback."
        )
    servidor = ThreadingHTTPServer((host, porta), Handler)
    servidor.estado = Estado(cfg)          # type: ignore[attr-defined]
    servidor.sse_abertos = 0               # type: ignore[attr-defined]
    servidor.trava_sse = threading.Lock()  # type: ignore[attr-defined]
    servidor.silencioso = silencioso       # type: ignore[attr-defined]
    servidor.daemon_threads = True
    return servidor


def servir(cfg: Config, host: str = "127.0.0.1", porta: int = 8765) -> int:
    servidor = construir(cfg, host, porta)
    endereco = f"http://{host}:{servidor.server_port}"
    print(f"Painel em {endereco} — vault {cfg.vault_path}")
    if cfg.painel_token:
        print("Autenticação: PAINEL_TOKEN definido — o painel vai pedir o token.")
        if host not in LOOPBACK:
            print(
                "AVISO: sem TLS o token viaja em texto puro. Só faça isso dentro de "
                "rede criptografada (tailnet) ou atrás de um proxy com TLS."
            )
    else:
        print("Sem autenticação (PAINEL_TOKEN vazio) — por isso só em loopback.")
    print("Ctrl-C para parar.")

    # SIGTERM é como o systemd para o serviço, e sinal **não é exceção**: sem este
    # tratador, `systemctl stop` deixaria o run do painel eternamente "rodando" no
    # journal. Descoberto matando o painel à mão durante os testes.
    def parar(_sinal, _quadro):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, parar)
    except ValueError:
        # Não é a thread principal (acontece em teste). Não é motivo para não subir.
        pass

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nParando.")
    finally:
        servidor.server_close()
    return 0
