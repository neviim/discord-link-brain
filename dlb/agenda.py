"""A grade do agendador, reimplementada em Python para o painel poder mostrá-la.

Isto é o lugar mais fácil de o painel **mentir**. O agendador de verdade é
`docker/scheduler.sh`, em shell, dentro do container; aqui existe uma segunda
implementação da mesma regra, e duas implementações da mesma regra divergem — em
silêncio, porque nada quebra quando a tela mostra "próximo sync às 18:00" e o
sync sai às 12:00.

Três defesas, e nenhuma delas é "tomar cuidado":

1. **Tabela de paridade.** `tests/test_agenda.py` tem a tabela `modo × config →
   próximo disparo`, escrita a partir da leitura do `scheduler.sh` linha por
   linha e **conferida rodando o snippet dele**, com os casos de fronteira que o
   shell tem: `EVERY_HOURS` que não divide 24 e desliza para o dia seguinte,
   `SYNC_AT` que já passou hoje, precedência entre os três modos.
2. **Quatro slots, não um.** O laço roda `sync → backfill → sintese → ressurgir`
   por ciclo. Uma tela que mostrasse só o sync esconderia três quartos do que
   acontece.
3. **A grade é lida do container quando ele está no ar**, não só do `.env` — ver
   `dlb/compose.py`. `restart` não relê o `.env`, então o arquivo e o processo
   podem discordar, e quem manda é o processo.

Precedência, copiada do shell: `DLB_SYNC_INTERVAL` > `DLB_SYNC_EVERY_HOURS` >
`DLB_SYNC_AT`. Sem nenhum dos três, o padrão é grade de 6 horas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterator, Mapping
from zoneinfo import ZoneInfo

# Os quatro comandos que o laço roda por ciclo, na ordem do `scheduler.sh`.
CICLO = ("sync", "backfill", "sintese", "ressurgir")

# As chaves que decidem a grade. É a lista que o painel compara entre `.env` e
# container — divergir em qualquer uma delas muda o que roda e quando.
CHAVES = (
    "DLB_SYNC_INTERVAL",
    "DLB_SYNC_EVERY_HOURS",
    "DLB_SYNC_AT",
    "DLB_SYNC_ARGS",
    "DLB_RUN_ON_START",
    "DLB_BACKFILL",
    "DLB_BACKFILL_DAYS",
    "DLB_SINTESE",
    "DLB_SINTESE_DIA",
    "DLB_SINTESE_MES",
    "DLB_RESSURGIR",
    "TZ",
)

PADRAO_EVERY_HOURS = 6


def _texto(env: Mapping[str, str], chave: str) -> str:
    return (env.get(chave) or "").strip()


def _inteiro(env: Mapping[str, str], chave: str, padrao: int) -> int:
    """Número do ambiente, com o mesmo desfecho do shell para lixo: o padrão.

    `${VAR:-2}` em shell não valida nada; `DLB_BACKFILL_DAYS=dois` chegaria ao
    `--days` e o argparse recusaria. Aqui o painel prefere mostrar o padrão a
    estourar — e o aviso dessa divergência é trabalho da tela de configuração.
    """
    try:
        return int(_texto(env, chave))
    except ValueError:
        return padrao


def _ligado(env: Mapping[str, str], chave: str, padrao: str = "0") -> bool:
    """O shell compara com `= "1"`, literalmente. `true` não liga nada."""
    valor = _texto(env, chave) or padrao
    return valor == "1"


@dataclass
class Modo:
    nome: str          # "intervalo" | "grade" | "diario"
    detalhe: str
    intervalo_s: int | None = None
    horas: int | None = None
    hora_do_dia: str = ""
    avisos: list[str] = field(default_factory=list)


def modo_de(env: Mapping[str, str]) -> Modo:
    """Qual dos três modos vale, e o que fica ignorado por causa da precedência.

    O aviso não é enfeite: `DLB_SYNC_AT` definido junto de `EVERY_HOURS` **não
    roda**, e nada avisa. É a classe de erro que faz alguém jurar que configurou
    3h30 da manhã e ver o sync saindo de 6 em 6 horas.
    """
    intervalo, horas, at = (
        _texto(env, "DLB_SYNC_INTERVAL"),
        _texto(env, "DLB_SYNC_EVERY_HOURS"),
        _texto(env, "DLB_SYNC_AT"),
    )
    avisos: list[str] = []

    if intervalo:
        for outro, nome in ((horas, "DLB_SYNC_EVERY_HOURS"), (at, "DLB_SYNC_AT")):
            if outro:
                avisos.append(f"{nome}={outro} está definido e é ignorado — DLB_SYNC_INTERVAL vence.")
        segundos = _inteiro(env, "DLB_SYNC_INTERVAL", 0)
        return Modo(
            "intervalo",
            f"a cada {segundos}s corridos, sem ancoragem",
            intervalo_s=segundos,
            avisos=avisos,
        )

    if horas:
        if at:
            avisos.append(
                f"DLB_SYNC_AT={at} está definido e é ignorado — DLB_SYNC_EVERY_HOURS vence. "
                "Para usar o horário fixo, DLB_SYNC_EVERY_HOURS precisa chegar vazio ao container."
            )
        n = _inteiro(env, "DLB_SYNC_EVERY_HOURS", PADRAO_EVERY_HOURS)
        if n > 0 and 24 % n:
            # Conferido rodando o próprio snippet do `scheduler.sh`: com passo de
            # 5h a sequência é 00,05,10,15,20 e depois **01:00 do dia seguinte**
            # (25h após a meia-noite anterior), estabilizando em 01,05,10,15,20.
            # A grade não volta para a meia-noite — ela desliza uma vez e fica.
            avisos.append(
                f"{n} não divide 24: depois do primeiro dia a grade desliza "
                f"{n - (24 % n)}h e passa a bater em horários deslocados da meia-noite."
            )
        return Modo("grade", f"de {n} em {n}h a partir de 00:00", horas=n, avisos=avisos)

    if at:
        return Modo("diario", f"uma vez por dia, às {at}", hora_do_dia=at, avisos=avisos)

    return Modo(
        "grade",
        f"de {PADRAO_EVERY_HOURS} em {PADRAO_EVERY_HOURS}h a partir de 00:00 (padrão)",
        horas=PADRAO_EVERY_HOURS,
        avisos=["nenhum dos três modos está configurado — o padrão do scheduler assume."],
    )


def proximo(env: Mapping[str, str], agora: datetime) -> datetime | None:
    """O próximo disparo depois de `agora`, no fuso do agendador.

    `None` no modo intervalo: o shell dorme N segundos **depois de o ciclo
    terminar**, então o próximo disparo depende de quando o último acabou — e o
    painel não inventa o que não sabe. Quem tem essa informação é o journal, e é
    a tela que a combina.
    """
    modo = modo_de(env)
    if modo.nome == "intervalo":
        return None
    if modo.nome == "diario":
        hh, mm = _hora(modo.hora_do_dia)
        alvo = agora.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return alvo if alvo > agora else alvo + timedelta(days=1)

    # Grade: a mesma aritmética do `seconds_until_slot` —
    # `(decorrido // passo) + 1` passos a partir da meia-noite local de hoje.
    passo = timedelta(hours=modo.horas or PADRAO_EVERY_HOURS)
    meia_noite = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    decorrido = (agora - meia_noite).total_seconds()
    quantos = int(decorrido // passo.total_seconds()) + 1
    # Sem recorte no fim do dia, de propósito: o shell deixa o alvo passar da
    # meia-noite (`25h` após ela, com passo de 5h) e recalcula do zero na volta
    # seguinte. Clampar aqui pareceria mais correto e **divergiria** — é
    # exatamente o erro que este módulo existe para não cometer.
    return meia_noite + passo * quantos


def _hora(texto: str) -> tuple[int, int]:
    hh, _, mm = texto.partition(":")
    return int(hh or 0), int(mm or 0)


def ciclos(env: Mapping[str, str], agora: datetime, quantos: int = 40) -> Iterator[datetime]:
    """Os próximos disparos do laço, um por um. Base para achar o slot da síntese."""
    atual = agora
    for _ in range(quantos):
        seguinte = proximo(env, atual)
        if seguinte is None:
            return
        yield seguinte
        atual = seguinte


@dataclass
class Slot:
    comando: str
    ativo: bool
    porque: str
    proximo: datetime | None
    observacao: str = ""


def slots(env: Mapping[str, str], agora: datetime) -> list[Slot]:
    """Os quatro comandos do ciclo: se rodam, quando, e por quê.

    Cada `porque` cita a variável que decide. É o que transforma a tela de
    "confie em mim" em "olhe o motivo" — e é o que permite conferir contra o
    `.env` sem abrir o shell script.
    """
    proximo_ciclo = proximo(env, agora)
    saida = [
        Slot(
            "sync",
            True,
            "sempre roda — é o laço",
            proximo_ciclo,
            f"argumentos extras: {_texto(env, 'DLB_SYNC_ARGS') or 'nenhum'}",
        )
    ]

    backfill = _ligado(env, "DLB_BACKFILL")
    saida.append(
        Slot(
            "backfill",
            backfill,
            "DLB_BACKFILL=1" if backfill else "DLB_BACKFILL=0 — o histórico não é mastigado sozinho",
            proximo_ciclo if backfill else None,
            f"--days {_inteiro(env, 'DLB_BACKFILL_DAYS', 2)} por ciclo" if backfill else "",
        )
    )

    sintese = _ligado(env, "DLB_SINTESE")
    dia = _inteiro(env, "DLB_SINTESE_DIA", 1)
    quando_sintese = None
    if sintese:
        # O shell compara `date +%u` (1=segunda … 7=domingo) com DLB_SINTESE_DIA
        # **no momento do ciclo**. Então o próximo disparo é o primeiro ciclo que
        # cair naquele dia da semana — não o próximo ciclo.
        quando_sintese = next(
            (c for c in ciclos(env, agora) if c.isoweekday() == dia), None
        )
    saida.append(
        Slot(
            "sintese",
            sintese,
            f"DLB_SINTESE=1, dia {dia} da semana" if sintese else "DLB_SINTESE=0",
            quando_sintese,
            (
                "o hash do período faz os outros ciclos do dia saírem em zero segundo, "
                "sem gastar modelo"
                + (" · rollup mensal no dia 1" if _ligado(env, "DLB_SINTESE_MES") else "")
            )
            if sintese
            else "",
        )
    )

    ressurgir = _ligado(env, "DLB_RESSURGIR")
    saida.append(
        Slot(
            "ressurgir",
            ressurgir,
            "DLB_RESSURGIR=1" if ressurgir else "DLB_RESSURGIR=0 — nada é postado de volta no canal",
            proximo_ciclo if ressurgir else None,
            "uma vez por dia: o próprio comando sai na hora se já postou hoje" if ressurgir else "",
        )
    )
    return saida


def fuso(env: Mapping[str, str], padrao: str = "UTC") -> ZoneInfo:
    """O fuso em que o agendador conta as horas.

    O `scheduler.sh` usa `date` e `datetime.now()` **locais do container**, e o
    compose define `TZ` a partir de `TIMEZONE`. Calcular a grade em UTC mostraria
    a hora certa deslocada em três horas, que é o tipo de erro que parece
    plausível na tela.
    """
    nome = _texto(env, "TZ") or _texto(env, "TIMEZONE") or padrao
    try:
        return ZoneInfo(nome)
    except Exception:  # noqa: BLE001 — fuso inválido não derruba o painel
        return ZoneInfo(padrao)


def visao(env: Mapping[str, str], agora: datetime | None = None) -> dict:
    """O que a tela de agenda mostra, já pronto para virar HTML ou JSON."""
    zona = fuso(env)
    agora = agora.astimezone(zona) if agora else datetime.now(zona)
    modo = modo_de(env)
    return {
        "fuso": str(zona),
        "agora": agora.isoformat(timespec="seconds"),
        "modo": modo.nome,
        "detalhe": modo.detalhe,
        "avisos": modo.avisos,
        "roda_ao_subir": _ligado(env, "DLB_RUN_ON_START", padrao="1"),
        "slots": [
            {
                "comando": s.comando,
                "ativo": s.ativo,
                "porque": s.porque,
                "proximo": s.proximo.isoformat(timespec="seconds") if s.proximo else None,
                "observacao": s.observacao,
            }
            for s in slots(env, agora)
        ],
    }
