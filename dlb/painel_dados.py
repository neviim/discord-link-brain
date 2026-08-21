"""Os números do dashboard, agregados numa passada e memorizados por versão.

O requisito do Dotcom foi explícito: dashboard que ele olhe todo dia e que **não
pese**. Isso decide o desenho inteiro deste módulo.

## Três camadas de economia, e o que cada uma custa

1. **O memo do acervo** (`dlb/acervo.py`) relê do disco só a nota cujo
   `(mtime_ns, size)` mudou. Medido no acervo real: 105 ms na primeira leitura,
   **2,2 ms** nas seguintes.
2. **Uma passada só.** Todos os agregados — por dia, por categoria, por domínio, por
   área, triagem, conceitos sem página — saem de um único `for` sobre as entradas.
   Seis passadas separadas seriam seis vezes o custo para o mesmo resultado.
3. **Cache por versão.** A versão é `(quantidade, maior mtime_ns)` do acervo: muda
   quando nota entra, sai ou é editada, e não muda em mais nada. Enquanto ela é a
   mesma, o dicionário agregado é devolvido como está — **zero cálculo**. É o que
   faz o polling de tempo real custar quase nada: dez visitas seguidas sem mudança
   no vault são um cálculo e nove devoluções.

O que **não** está aqui, de propósito: contagem de mensagem do Discord ainda não
capturada. Saber isso exigiria chamar a API a cada tick — caro, com limite de taxa,
e uma dependência de rede num painel que precisa abrir quando a rede está ruim. O
que o dashboard mostra como "pendente" é o que dá para saber de graça, do disco:
fila de triagem, histórico não concluído e conceito sem página.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .corpus import LinkEntry

JANELA_DIAS = 30
TOPO_DOMINIOS = 8
TOPO_CATEGORIAS = 8


def _hoje(tz: str = "") -> date:
    """Hoje no fuso configurado — a virada do dia decide em que coluna a nota cai."""
    if tz:
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(tz)).date()
        except Exception:  # noqa: BLE001 — fuso inválido não derruba o dashboard
            pass
    return datetime.now(timezone.utc).date()


def versao(entradas: list[LinkEntry]) -> tuple[int, int]:
    """`(quantidade, maior mtime_ns)` — muda exatamente quando o acervo muda.

    Barato: um `stat` por nota, que o memo do acervo já fez de qualquer forma nesta
    mesma requisição. Não abre arquivo nenhum.
    """
    maior = 0
    for e in entradas:
        try:
            m = e.path.stat().st_mtime_ns
        except OSError:
            continue
        if m > maior:
            maior = m
    return len(entradas), maior


class Agregado:
    """Guarda o último cálculo e o devolve enquanto a versão do acervo não muda."""

    def __init__(self) -> None:
        self._versao: tuple[int, int] | None = None
        self._dados: dict | None = None
        self.calculos = 0
        self.reusos = 0
        self.ultimo_custo_ms = 0.0

    def de(self, entradas: list[LinkEntry], tz: str = "") -> dict:
        v = versao(entradas)
        if v == self._versao and self._dados is not None:
            self.reusos += 1
            return self._dados
        t0 = time.perf_counter()
        self._dados = calcular(entradas, tz=tz)
        self.ultimo_custo_ms = round((time.perf_counter() - t0) * 1000, 2)
        self._versao = v
        self.calculos += 1
        return self._dados

    def estatisticas(self) -> dict:
        return {
            "calculos": self.calculos,
            "reusos": self.reusos,
            "ultimo_custo_ms": self.ultimo_custo_ms,
        }


TRIAGENS_ORDEM = ("inbox", "lido", "aplicado", "descartado")


def calcular(entradas: list[LinkEntry], tz: str = "") -> dict:
    """Todos os agregados do dashboard, numa passada sobre as entradas."""
    hoje = _hoje(tz)
    inicio = hoje - timedelta(days=JANELA_DIAS - 1)

    por_dia: dict[str, int] = {}
    por_categoria: dict[str, int] = {}
    por_dominio: dict[str, int] = {}
    por_area: dict[str, int] = {}
    por_triagem: dict[str, int] = {k: 0 for k in TRIAGENS_ORDEM}
    conceitos: dict[str, int] = {}
    dias_vistos: set[str] = set()
    avaliados = 0
    soma_notas = 0

    for e in entradas:
        if e.dia:
            dias_vistos.add(e.dia)
            if e.dia >= inicio.isoformat():
                por_dia[e.dia] = por_dia.get(e.dia, 0) + 1
        cat = e.categoria or "outro"
        por_categoria[cat] = por_categoria.get(cat, 0) + 1
        if e.dominio:
            por_dominio[e.dominio] = por_dominio.get(e.dominio, 0) + 1
        if e.area:
            por_area[e.area] = por_area.get(e.area, 0) + 1
        chave = e.triagem if e.triagem in por_triagem else "inbox"
        por_triagem[chave] += 1
        for c in e.sem_pagina:
            conceitos[c] = conceitos.get(c, 0) + 1
        if e.nota is not None:
            avaliados += 1
            soma_notas += e.nota

    # A janela tem 30 colunas mesmo nos dias sem captura: buraco na série temporal
    # mentiria sobre o ritmo, sugerindo que o dia não existiu.
    serie = [
        {"dia": (inicio + timedelta(days=i)).isoformat(),
         "quantos": por_dia.get((inicio + timedelta(days=i)).isoformat(), 0)}
        for i in range(JANELA_DIAS)
    ]
    ultimos7 = sum(d["quantos"] for d in serie[-7:])
    anteriores7 = sum(d["quantos"] for d in serie[-14:-7])
    total = len(entradas)
    triados = total - por_triagem["inbox"]

    return {
        "total": total,
        "dias_com_captura": len(dias_vistos),
        "primeiro_dia": min(dias_vistos) if dias_vistos else None,
        "ultimo_dia": max(dias_vistos) if dias_vistos else None,
        "hoje": por_dia.get(hoje.isoformat(), 0),
        "ultimos7": ultimos7,
        "anteriores7": anteriores7,
        "delta7": ultimos7 - anteriores7,
        "media_dia": round(ultimos7 / 7, 1),
        "serie": serie,
        "por_triagem": {k: por_triagem[k] for k in TRIAGENS_ORDEM},
        "triados": triados,
        "por_categoria": _topo(por_categoria, TOPO_CATEGORIAS),
        "por_dominio": _topo(por_dominio, TOPO_DOMINIOS),
        "por_area": _topo(por_area, 6),
        "conceitos_sem_pagina": _topo(conceitos, 6),
        "conceitos_sem_pagina_total": len(conceitos),
        "avaliados": avaliados,
        "nota_media": round(soma_notas / avaliados, 1) if avaliados else None,
    }


def _topo(contagem: dict[str, int], quantos: int) -> list[dict]:
    """Os N maiores, e o resto somado em "outros".

    Dobrar o número de classes não torna o gráfico mais informativo: passando de
    ~7 faixas, as vizinhas embaçam. Somar a cauda é mais honesto que cortá-la em
    silêncio — e o rótulo diz quantas foram somadas.
    """
    ordenado = sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0]))
    cabeca = ordenado[:quantos]
    cauda = ordenado[quantos:]
    saida = [{"rotulo": k, "quantos": v} for k, v in cabeca]
    if cauda:
        saida.append(
            {"rotulo": f"outros ({len(cauda)})", "quantos": sum(v for _, v in cauda)}
        )
    return saida


def pendencias(agregado: dict, cursores: list[dict], ultimos: dict) -> list[dict]:
    """O que está esperando trabalho, com quem faz cada um.

    Distingue **pendência de máquina** (histórico não mastigado — o pipeline resolve
    sozinho no próximo ciclo) de **pendência humana** (triar, escrever página no
    wiki). Misturar as duas produz uma lista que ninguém sabe atacar.
    """
    itens: list[dict] = []
    inbox = agregado["por_triagem"]["inbox"]
    if inbox:
        itens.append({
            "o_que": "notas esperando triagem",
            "quantos": inbox,
            "de_quem": "você",
            "onde": "/acervo?triagem=inbox",
        })
    incompletos = [c for c in cursores if c.get("backfill_cursor") and not c.get("backfill_concluido")]
    if incompletos:
        itens.append({
            "o_que": "canais com histórico incompleto",
            "quantos": len(incompletos),
            "de_quem": "o pipeline",
            "onde": "/agenda",
        })
    sem_cursor = [c for c in cursores if not c.get("backfill_cursor") and not c.get("backfill_concluido")]
    if sem_cursor:
        itens.append({
            "o_que": "canais em que o histórico nunca começou",
            "quantos": len(sem_cursor),
            "de_quem": "o pipeline",
            "onde": "/agenda",
        })
    if agregado["conceitos_sem_pagina_total"]:
        itens.append({
            "o_que": "conceitos citados sem página no wiki",
            "quantos": agregado["conceitos_sem_pagina_total"],
            "de_quem": "você",
            "onde": "python3 -m dlb temas",
        })
    sintese = ultimos.get("sintese")
    if not sintese:
        itens.append({
            "o_que": "síntese de período nunca gerada",
            "quantos": 1,
            "de_quem": "o pipeline",
            "onde": "/execucoes",
        })
    return itens
