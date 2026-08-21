"""Gráficos em SVG montados no servidor, sem uma linha de JavaScript de biblioteca.

O painel entrega **markup pronto**: o navegador só desenha. Isso não é preferência
estética, é o requisito do projeto — dashboard ágil, que não pese. Uma
biblioteca de gráfico custaria centenas de KB baixados, parse, layout e um segundo
modelo de dados no cliente; um `<svg>` de 2 KB custa o que custa desenhar.

As especificações de marca estão fixadas aqui, uma vez, em vez de repetidas em cada
gráfico — é o que faz cinco gráficos diferentes parecerem um sistema:

- barra/coluna **no máximo 24 px** de espessura, ponta de dado arredondada em 4 px e
  quadrada na linha de base;
- **2 px de vão na cor da superfície** entre marcas que se tocam (segmento de pilha,
  barra vizinha) — o vão separa, nunca uma borda desenhada em volta;
- grade e eixo em **fio de 1 px sólido**, um passo fora da superfície, recessivos;
- rótulo **seletivo**: o extremo e o último, nunca um número em cada ponto.

## Cor

Nenhuma cor é escolhida aqui. As duas famílias vêm de fora, por variável CSS, e
foram validadas com `scripts/validate_palette.js` da skill de dataviz **contra as
superfícies deste painel** (`#fbfbfa` claro, `#16171a` escuro), não contra as de
referência:

- **um tom só** (`--serie-1`) para toda barra nominal — categoria de link, domínio,
  captura por dia. Colorir barra nominal por valor gastaria o canal de identidade
  reencodando o que o comprimento da barra já mostra;
- **rampa ordinal de quatro passos** (`--ord-1..4`) para a triagem, que **é** ordenada
  (`inbox → lido → aplicado | descartado`): a ordem aparece na cor.

Status (`ok`, `falha`, `recusado`) usa a paleta de estado reservada, e nunca sozinha:
sempre com o rótulo em texto ao lado, porque dois dos quatro tons ficam abaixo de
3:1 na superfície clara.

## Acessibilidade

Todo valor plotado aparece **também em texto** — no rótulo direto, na legenda ou na
tabela ao lado. `<title>` dá a dica nativa do navegador em cada marca, sem custo de
JavaScript: é menos rico que um tooltip desenhado, e é honesto sobre isso.
"""

from __future__ import annotations

from dataclasses import dataclass

from .render import Raw, escapar

# --- especificações de marca, fixas ------------------------------------------
ESPESSURA_MAX = 24      # px — barra nunca preenche a faixa inteira; a folga é ar
PONTA = 4               # px — raio da ponta de dado
VAO = 2                 # px — vão na cor da superfície entre marcas que se tocam
FIO = 1                 # px — grade e eixo


@dataclass
class Fatia:
    """Um pedaço de dado com rótulo. A unidade que todo gráfico daqui consome."""

    rotulo: str
    valor: float
    dica: str = ""


def _num(x: float) -> str:
    """Coordenada curta: SVG não precisa de dezesseis casas, e o HTML fica menor."""
    return f"{x:.1f}".rstrip("0").rstrip(".")


def _t(texto: str) -> str:
    return escapar(texto)


def vazio(mensagem: str) -> Raw:
    return Raw(f'<p class="viz-vazio">{_t(mensagem)}</p>')


# ---------------------------------------------------------------- barras H


def barras(
    fatias: list[Fatia],
    largura: int = 520,
    altura_faixa: int = 26,
    rotulo_largura: int = 132,
    sufixo: str = "",
) -> Raw:
    """Barras horizontais, um tom só, ordenadas por valor.

    Horizontal porque os rótulos são nomes longos (`repositorio`, `youtube.com`) —
    na vertical eles precisariam girar 90°, e texto girado não se lê de passagem.

    O valor vai **fora** da ponta da barra, sempre: dentro, ele seria recortado
    justamente nas barras curtas, que são as que mais precisam do número.
    """
    if not fatias:
        return vazio("nada para mostrar ainda")
    maior = max(f.valor for f in fatias) or 1
    espessura = min(ESPESSURA_MAX, altura_faixa - 8)
    plot = largura - rotulo_largura - 52
    altura = len(fatias) * altura_faixa
    linhas = []
    for i, f in enumerate(fatias):
        y = i * altura_faixa + (altura_faixa - espessura) / 2
        w = max(2.0, f.valor / maior * plot)
        meio = y + espessura / 2
        linhas.append(
            f'<g><title>{_t(f.dica or f"{f.rotulo}: {f.valor:g}{sufixo}")}</title>'
            f'<text class="viz-rot" x="{rotulo_largura - 8}" y="{_num(meio)}" '
            f'text-anchor="end" dominant-baseline="central">{_t(f.rotulo)}</text>'
            f'<rect class="viz-barra" x="{rotulo_largura}" y="{_num(y)}" '
            f'width="{_num(w)}" height="{espessura}" rx="{PONTA}"/>'
            f'<text class="viz-val" x="{_num(rotulo_largura + w + 8)}" y="{_num(meio)}" '
            f'dominant-baseline="central">{f.valor:g}{_t(sufixo)}</text></g>'
        )
    return Raw(
        f'<svg class="viz" viewBox="0 0 {largura} {altura}" width="100%" '
        f'height="{altura}" role="img" preserveAspectRatio="xMinYMin meet">'
        + "".join(linhas)
        + "</svg>"
    )


# --------------------------------------------------------------- colunas


def colunas(
    fatias: list[Fatia],
    largura: int = 560,
    altura: int = 120,
    destacar_ultimo: bool = True,
) -> Raw:
    """Colunas para série temporal curta — um tom só, uma linha de base.

    Rótulo em **três** pontos no máximo: o maior, o último, e o primeiro dia do eixo.
    Número em cima de cada coluna vira ruído e ninguém lê.
    """
    if not fatias:
        return vazio("sem captura no período")
    maior = max(f.valor for f in fatias) or 1
    faixa = largura / len(fatias)
    espessura = min(ESPESSURA_MAX, max(3.0, faixa - VAO))
    base = altura - 18
    idx_maior = max(range(len(fatias)), key=lambda i: fatias[i].valor)
    marcas = []
    for i, f in enumerate(fatias):
        h = 0 if not f.valor else max(2.0, f.valor / maior * (base - 14))
        x = i * faixa + (faixa - espessura) / 2
        y = base - h
        ultimo = destacar_ultimo and i == len(fatias) - 1
        marcas.append(
            f'<g><title>{_t(f.dica or f"{f.rotulo}: {f.valor:g}")}</title>'
            f'<rect class="viz-barra{" viz-barra-viva" if ultimo else ""}" '
            f'x="{_num(x)}" y="{_num(y)}" width="{_num(espessura)}" '
            f'height="{_num(h)}" rx="{PONTA}"/></g>'
        )
        if i in (idx_maior, len(fatias) - 1) and f.valor:
            marcas.append(
                f'<text class="viz-val" x="{_num(x + espessura / 2)}" y="{_num(y - 4)}" '
                f'text-anchor="middle">{f.valor:g}</text>'
            )
    eixo = (
        f'<line class="viz-eixo" x1="0" y1="{base}" x2="{largura}" y2="{base}"/>'
        f'<text class="viz-rot" x="0" y="{altura - 4}">{_t(fatias[0].rotulo)}</text>'
        f'<text class="viz-rot" x="{largura}" y="{altura - 4}" text-anchor="end">'
        f'{_t(fatias[-1].rotulo)}</text>'
    )
    return Raw(
        f'<svg class="viz" viewBox="0 0 {largura} {altura}" width="100%" '
        f'height="{altura}" role="img" preserveAspectRatio="none">'
        + "".join(marcas)
        + eixo
        + "</svg>"
    )


# -------------------------------------------------------------- empilhada


def empilhada(fatias: list[Fatia], largura: int = 520, espessura: int = 22) -> Raw:
    """Uma barra horizontal de parte-para-todo, com rampa ordinal.

    Vão de 2 px na cor da superfície entre segmentos — o que separa é o vão, não
    borda. Segmento estreito não recebe rótulo dentro (seria recortado): quem
    carrega o valor é a legenda, que vem sempre, porque são quatro séries.
    """
    total = sum(f.valor for f in fatias)
    if not total:
        return vazio("acervo vazio")
    x = 0.0
    pedacos = []
    for i, f in enumerate(fatias):
        w = f.valor / total * largura
        if w <= 0:
            continue
        w_visivel = max(2.0, w - (VAO if i < len(fatias) - 1 else 0))
        pedacos.append(
            f'<g><title>{_t(f.dica or f"{f.rotulo}: {f.valor:g}")}</title>'
            f'<rect class="viz-ord viz-ord-{i + 1}" x="{_num(x)}" y="0" '
            f'width="{_num(w_visivel)}" height="{espessura}" '
            f'rx="{PONTA if i in (0, len(fatias) - 1) else 0}"/></g>'
        )
        x += w
    return Raw(
        f'<svg class="viz" viewBox="0 0 {largura} {espessura}" width="100%" '
        f'height="{espessura}" role="img" preserveAspectRatio="none">'
        + "".join(pedacos)
        + "</svg>"
    )


def legenda_ordinal(fatias: list[Fatia]) -> Raw:
    """Legenda obrigatória da empilhada: quatro séries, identidade nunca só por cor."""
    itens = "".join(
        f'<li><span class="viz-chip viz-ord-{i + 1}"></span>'
        f'{_t(f.rotulo)} <strong>{f.valor:g}</strong></li>'
        for i, f in enumerate(fatias)
    )
    return Raw(f'<ul class="viz-legenda">{itens}</ul>')


# ----------------------------------------------------------------- medidor


def medidor(feito: float, total: float, rotulo: str) -> Raw:
    """Uma razão contra um limite. Trilho e preenchimento na mesma rampa.

    É o que o heurístico manda usar para "uma razão contra um teto" — não um pizza
    de duas fatias.
    """
    pct = 0 if not total else feito / total * 100
    return Raw(
        f'<div class="viz-medidor"><div class="viz-medidor-topo">'
        f'<span>{_t(rotulo)}</span><strong>{pct:.0f}%</strong></div>'
        f'<svg class="viz" viewBox="0 0 200 8" width="100%" height="8" role="img" '
        f'preserveAspectRatio="none"><title>{_t(f"{rotulo}: {feito:g} de {total:g}")}</title>'
        f'<rect class="viz-trilho" x="0" y="0" width="200" height="8" rx="4"/>'
        f'<rect class="viz-medidor-fill" x="0" y="0" width="{_num(max(2, pct * 2))}" '
        f'height="8" rx="4"/></svg></div>'
    )


# ------------------------------------------------------------ faixa de status


def faixa_status(estados: list[tuple[str, str]], largura: int = 520) -> Raw:
    """Uma marca por execução recente, colorida por estado, mais antiga à esquerda.

    Status nunca sozinho: a legenda abaixo traz o **nome** de cada estado com a
    contagem. Dois dos quatro tons ficam abaixo de 3:1 na superfície clara, e o
    rótulo em texto é a compensação que a paleta exige.
    """
    if not estados:
        return vazio("nenhuma execução registrada")
    faixa = largura / max(len(estados), 12)
    w = max(3.0, faixa - VAO)
    marcas = "".join(
        f'<g><title>{_t(dica)}</title>'
        f'<rect class="viz-st viz-st-{estado}" x="{_num(i * faixa)}" y="0" '
        f'width="{_num(w)}" height="18" rx="2"/></g>'
        for i, (estado, dica) in enumerate(estados)
    )
    return Raw(
        f'<svg class="viz" viewBox="0 0 {largura} 18" width="100%" height="18" '
        f'role="img" preserveAspectRatio="none">{marcas}</svg>'
    )


def legenda_status(contagem: dict[str, int]) -> Raw:
    nomes = {
        "ok": "concluído",
        "falha": "falhou",
        "erro": "erro",
        "recusado": "recusado pelo lock",
        "rodando": "em andamento",
    }
    itens = "".join(
        f'<li><span class="viz-chip viz-st-{k}"></span>{_t(nomes.get(k, k))} '
        f"<strong>{v}</strong></li>"
        for k, v in contagem.items()
        if v
    )
    return Raw(f'<ul class="viz-legenda">{itens}</ul>' if itens else "")
