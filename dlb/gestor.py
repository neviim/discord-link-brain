"""As oito regras que decidem o que merece a sua atenção. Sem LLM.

O "gestor" que o painel oferece é **função pura**: recebe o estado que o painel já
lê do disco e devolve uma lista de alertas, cada um com evidência e ação. Não
chama modelo.

Três razões, e a terceira é a que decide:

1. As oito regras são comparação de número e de data. Um LLM daria a mesma
   resposta com latência de dezenas de segundos e variação entre execuções.
2. Regra tem teste; prosa de modelo não tem. Cada regra aqui tem um teste que
   fabrica exatamente o estado que a dispara — e outro que confirma que ela fica
   calada quando não deve falar.
3. **Custa zero token e funciona quando o modelo está sem quota** — que é
   exatamente quando o painel importa mais, porque a classificação diária acabou
   de falhar por isso.

O LLM entra depois, opcional, para **narrar** alerta que as regras já decidiram —
nunca para decidir qual alerta existe. A ordem importa: um narrador não pode
inventar um problema, e um decisor pode.

Cada alerta carrega:

- `nivel` — `alerta` (algo está quebrado agora), `atencao` (vai quebrar, ou está
  acumulando), `info` (vale saber, não pede ação).
- `evidencia` — o dado que fez a regra disparar, com número. Alerta sem evidência
  é palpite, e palpite ensina o leitor a ignorar o painel.
- `acao` — o comando ou a tela que resolve. Alerta sem ação é ansiedade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# Um run "rodando" mais velho que isto sem ninguém segurando o lock morreu sem
# fechar o resumo. Um sync do acervo real leva segundos; um backfill de dois dias
# de histórico, minutos. Duas horas não é lentidão, é óbito.
PENDURADO_S = 2 * 3600

# Quantas notas em `inbox` até virar assunto. Não é o tamanho do acervo: é o
# tamanho da fila. O ESQUEMA do vault diz que triagem que não acontece transforma
# a camada em depósito, e 40 é onde uma sessão de triagem deixa de caber numa
# sentada.
FILA_LIMIAR = 40

# Falhas seguidas do mesmo comando. Uma é a API do Discord tendo um dia ruim;
# três é configuração, permissão ou bug — coisa que não se resolve esperando.
FALHAS_SEGUIDAS = 3

# Conceito citado como candidato em N notas diferentes já pagou uma página no
# wiki. Abaixo disso é vocabulário passageiro.
CONCEITO_LIMIAR = 3

# Histórico parado: backfill começado, não concluído, e sem execução recente.
HISTORICO_PARADO_S = 48 * 3600

NIVEIS = {"alerta": 0, "atencao": 1, "info": 2}


@dataclass
class Alerta:
    chave: str
    nivel: str
    titulo: str
    evidencia: str
    acao: str
    dados: dict = field(default_factory=dict)

    def como_dict(self) -> dict:
        return {
            "chave": self.chave,
            "nivel": self.nivel,
            "titulo": self.titulo,
            "evidencia": self.evidencia,
            "acao": self.acao,
            "dados": self.dados,
        }


def _agora() -> float:
    return datetime.now(timezone.utc).timestamp()


def _idade_iso(texto: str | None, agora: float) -> float | None:
    """Segundos desde um instante ISO, tolerando o que vier torto.

    Data ilegível não pode derrubar o gestor: o painel é o que se abre quando algo
    já está estranho, e um `summary.json` truncado é justamente um dos sintomas.
    """
    if not texto:
        return None
    try:
        quando = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=timezone.utc)
    return max(0.0, agora - quando.timestamp())


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


# ------------------------------------------------------------------- as regras
#
# Toda regra tem a mesma forma: recebe a visão do painel, devolve `Alerta` ou
# `None`. Nenhuma lê disco, nenhuma chama rede, nenhuma depende de relógio além
# do `agora` que recebe — é o que as torna testáveis fabricando estado.


def regra_agendador_parado(v: dict, agora: float) -> Alerta | None:
    """O laço do container não fecha um ciclo com sucesso desde a tolerância.

    O heartbeat é escrito **depois** de um sync com código 0, então velho aqui não
    significa "container morreu": significa que o resultado não vem. As duas
    causas se resolvem no mesmo lugar, o log, e é para lá que a ação aponta.
    """
    idade = (v.get("heartbeats") or {}).get("sync")
    tolerancia = (v.get("tolerancia_s") or {}).get("sync") or 28800
    if idade is not None and idade <= tolerancia:
        return None
    return Alerta(
        chave="agendador_parado",
        nivel="alerta",
        titulo="O agendador não conclui um ciclo",
        evidencia=(
            f"último sync com sucesso: {_humano(idade)} "
            f"(tolerância {_humano(tolerancia)})"
        ),
        acao="./dlb.sh prod logs — e `./dlb.sh prod up` recria o container com o .env atual",
        dados={"idade_s": idade, "tolerancia_s": tolerancia},
    )


def regra_responder_parado(v: dict, agora: float) -> Alerta | None:
    """O bot que responde `?buscar` no canal parou de dar voltas.

    Só dispara se ele **já rodou alguma vez**: heartbeat que nunca existiu quer
    dizer que o serviço não está ligado, e alertar sobre serviço desligado ensina
    a ignorar o painel.
    """
    idade = (v.get("heartbeats") or {}).get("responder")
    if idade is None:
        return None
    tolerancia = (v.get("tolerancia_s") or {}).get("responder") or 300
    if idade <= tolerancia:
        return None
    return Alerta(
        chave="responder_parado",
        nivel="alerta",
        titulo="O responder parou de dar voltas",
        evidencia=f"última volta: {_humano(idade)} (tolerância {_humano(tolerancia)})",
        acao="./dlb.sh prod logs responder — quem pergunta no canal está sem resposta",
        dados={"idade_s": idade, "tolerancia_s": tolerancia},
    )


def regra_ultimo_ciclo_falhou(v: dict, agora: float) -> Alerta | None:
    """A execução mais recente de sync ou backfill terminou mal.

    Separada da regra de heartbeat de propósito: o backfill não escreve heartbeat
    nenhum, então sem esta regra ele poderia falhar todo dia em silêncio.
    """
    for comando in ("sync", "backfill"):
        run = (v.get("ultimos") or {}).get(comando)
        if not run or run.get("status") not in {"falha", "erro"}:
            continue
        return Alerta(
            chave="ultimo_ciclo_falhou",
            nivel="alerta",
            titulo=f"O último {comando} terminou em {run.get('status')}",
            evidencia=(
                f"{run.get('id')} · há {_humano(_idade_iso(run.get('fim') or run.get('inicio'), agora))}"
                + (f" · {run.get('erro')}" if run.get("erro") else "")
            ),
            acao="/execucoes — a linha tem o erro e os contadores do que deu tempo de fazer",
            dados={"comando": comando, "run": run.get("id"), "status": run.get("status")},
        )
    return None


def regra_falhas_seguidas(v: dict, agora: float) -> Alerta | None:
    """Três ou mais falhas em sequência do mesmo comando.

    Uma falha é a API do Discord tendo um dia ruim, e o próprio agendador tenta
    de novo. Três seguidas é configuração, permissão ou bug: esperar não resolve,
    e é por isso que este alerta é separado do anterior em vez de ser o mesmo com
    contador.
    """
    por_comando: dict[str, int] = {}
    fim: dict[str, bool] = {}
    for run in v.get("ultimas_execucoes") or []:
        cmd = run.get("comando") or "?"
        if fim.get(cmd):
            continue
        if run.get("status") in {"falha", "erro"}:
            por_comando[cmd] = por_comando.get(cmd, 0) + 1
        elif run.get("status") in {"ok", "recusado"}:
            fim[cmd] = True     # a sequência quebrou aqui
    piores = [(c, n) for c, n in por_comando.items() if n >= FALHAS_SEGUIDAS]
    if not piores:
        return None
    comando, quantas = max(piores, key=lambda cn: cn[1])
    return Alerta(
        chave="falhas_seguidas",
        nivel="alerta",
        titulo=f"{comando} falhou {quantas} vezes seguidas",
        evidencia=f"{quantas} execuções consecutivas em falha ou erro, sem nenhuma bem-sucedida entre elas",
        acao="python3 -m dlb doctor — e /execucoes para ver se o erro é sempre o mesmo",
        dados={"comando": comando, "quantas": quantas},
    )


def regra_run_pendurado(v: dict, agora: float) -> Alerta | None:
    """Run marcado como `rodando` há horas, e ninguém segurando o lock.

    É a assinatura de processo morto de `kill -9` ou container derrubado no meio:
    o resumo ficou aberto para sempre. Sem esta regra o painel mostraria um sync
    fantasma eternamente em andamento — pior que não mostrar nada, porque parece
    que o sistema está trabalhando.
    """
    if v.get("lock"):
        return None
    for run in v.get("ultimas_execucoes") or []:
        if run.get("status") != "rodando":
            continue
        # O painel é um run "rodando" legítimo: ele fica de pé por design.
        if run.get("comando") in {"painel", "responder"}:
            continue
        idade = _idade_iso(run.get("inicio"), agora)
        if idade is None or idade < PENDURADO_S:
            continue
        return Alerta(
            chave="run_pendurado",
            nivel="atencao",
            titulo=f"{run.get('comando')} ficou marcado como rodando",
            evidencia=f"{run.get('id')} aberto há {_humano(idade)}, e o lock está livre",
            acao="nada a fazer no sistema — o run morreu sem fechar; a próxima execução é independente",
            dados={"run": run.get("id"), "idade_s": idade},
        )
    return None


def regra_fila_de_triagem(v: dict, agora: float) -> Alerta | None:
    """A fila `inbox` passou do que se tria numa sentada.

    Não é problema do pipeline — é informação sobre **hábito**, e o painel existe
    também para isso. O `ESQUEMA` do vault é explícito: o que nunca é triado não é
    acervo, é entulho.
    """
    acervo = v.get("acervo") or {}
    por_triagem = acervo.get("por_triagem") or {}
    inbox = por_triagem.get("inbox", 0)
    if inbox < FILA_LIMIAR:
        return None
    total = acervo.get("total") or 0
    proporcao = f" ({inbox * 100 // total}% do acervo)" if total else ""
    return Alerta(
        chave="fila_de_triagem",
        nivel="atencao",
        titulo=f"{inbox} notas esperando triagem",
        evidencia=f"{inbox} de {total} notas em `triagem: inbox`{proporcao}",
        acao="/acervo?triagem=inbox — marcar `aplicado` ou `descartado` é o que impede a camada de virar depósito",
        dados={"inbox": inbox, "total": total},
    )


def regra_conceitos_sem_pagina(v: dict, agora: float) -> Alerta | None:
    """Conceitos que o classificador marcou como candidatos e ninguém escreveu.

    É a "Lacuna" que o próprio MOC do vault lista. Vira `info` e não `atencao`:
    nada está quebrado, é trabalho disponível.
    """
    candidatos = (v.get("acervo") or {}).get("sem_pagina") or []
    if len(candidatos) < CONCEITO_LIMIAR:
        return None
    amostra = ", ".join(candidatos[:6])
    return Alerta(
        chave="conceitos_sem_pagina",
        nivel="info",
        titulo=f"{len(candidatos)} conceitos sem página no wiki",
        evidencia=f"citados nas notas e ainda sem nota em `20_WIKI/`: {amostra}"
        + ("…" if len(candidatos) > 6 else ""),
        acao="python3 -m dlb temas — o relatório diz quais já viraram tema de verdade",
        dados={"quantos": len(candidatos), "amostra": candidatos[:20]},
    )


def regra_historico_parado(v: dict, agora: float) -> Alerta | None:
    """Backfill começou, não terminou, e não roda há dois dias.

    O histórico anda em fatias, uma por ciclo — se as fatias pararam, o passado
    fica pela metade sem nada avisar. O cursor do backfill não é o do sync, então
    o painel pode estar todo verde com o histórico congelado.
    """
    pendentes = [
        c
        for c in (v.get("cursores") or [])
        if c.get("backfill_cursor") and not c.get("backfill_concluido")
    ]
    if not pendentes:
        return None
    run = (v.get("ultimos") or {}).get("backfill")
    idade = _idade_iso((run or {}).get("inicio"), agora)
    if idade is not None and idade < HISTORICO_PARADO_S:
        return None
    nomes = ", ".join(c.get("nome") or c.get("canal") or "?" for c in pendentes)
    return Alerta(
        chave="historico_parado",
        nivel="atencao",
        titulo="O histórico está pela metade e parado",
        evidencia=(
            f"canal(is) {nomes} com backfill incompleto · último backfill: {_humano(idade)}"
        ),
        acao="DLB_BACKFILL=1 no .env (e `./dlb.sh prod up`), ou `python3 -m dlb backfill --days 2` à mão",
        dados={"canais": [c.get("canal") for c in pendentes], "idade_s": idade},
    )


REGRAS = (
    regra_agendador_parado,
    regra_responder_parado,
    regra_ultimo_ciclo_falhou,
    regra_falhas_seguidas,
    regra_run_pendurado,
    regra_historico_parado,
    regra_fila_de_triagem,
    regra_conceitos_sem_pagina,
)


def avaliar(visao: dict, agora: float | None = None) -> list[Alerta]:
    """Roda as oito regras sobre a visão do painel. Mais grave primeiro.

    Uma regra que estoure não pode calar as outras sete: o estado que faz uma
    regra falhar é exatamente o estado sobre o qual as outras têm algo a dizer.
    """
    agora = _agora() if agora is None else agora
    alertas: list[Alerta] = []
    for regra in REGRAS:
        try:
            resultado = regra(visao, agora)
        except Exception as exc:  # noqa: BLE001
            alertas.append(
                Alerta(
                    chave="regra_quebrada",
                    nivel="atencao",
                    titulo=f"A regra {regra.__name__} falhou",
                    evidencia=f"{type(exc).__name__}: {exc}",
                    acao="é bug do gestor, não do pipeline — as outras regras seguem valendo",
                    dados={"regra": regra.__name__},
                )
            )
            continue
        if resultado is not None:
            alertas.append(resultado)
    alertas.sort(key=lambda a: (NIVEIS.get(a.nivel, 9), a.chave))
    return alertas


def tudo_quieto(alertas: list[Alerta]) -> bool:
    """Nada que peça ação. `info` não conta — é trabalho disponível, não problema."""
    return not any(a.nivel in {"alerta", "atencao"} for a in alertas)
