"""HTML no servidor, com escape por construção.

O painel não traz dependência nova (ver `docs/DECISOES.md`), e o preço honesto
dessa escolha é este arquivo: `string.Template` **não** escapa nada, enquanto o
Jinja escapa por padrão. Essa era a única vantagem de segurança real que um
framework de template traria, então ela entra como requisito e não como cuidado.

A regra, e ela não tem exceção silenciosa:

    todo valor interpolado é escapado. HTML cru exige `Raw(...)`.

`Raw` é explícito e grep-ável — `grep -rn 'Raw(' dlb/` lista, em segundos, todo
lugar do painel onde HTML não escapado entra numa página. Um `html.escape`
espalhado por chamada não dá essa lista, e é por isso que a única forma de
esquecer aqui é escrevendo `Raw` de propósito.

O que entra no painel vem de fora: título de página raspada da web, nome de canal
do Discord, resumo escrito por um LLM, mensagem de erro de subprocess. Nenhum
deles é confiável, e o modelo de ameaça é browser hostil na mesma máquina — não
DoS.
"""

from __future__ import annotations

import html
from string import Template


class Raw(str):
    """Marca uma string como HTML já pronto, que não deve ser escapado.

    Subclasse de `str` para poder ser concatenada e comparada como texto normal;
    o que muda é só o que `substituir` faz com ela.
    """

    __slots__ = ()


def escapar(valor: object) -> str:
    """Texto pronto para ir dentro de HTML — inclusive dentro de atributo.

    `quote=True` é o default de `html.escape`, e é ele que faz a aspa dupla virar
    `&quot;`. Sem isso, um título com aspas fecha o atributo e o resto vira
    marcação: `<a title="x" onmouseover=...>`.
    """
    if valor is None:
        return ""
    if isinstance(valor, Raw):
        return str(valor)
    return html.escape(str(valor), quote=True)


def substituir(template: str, /, **valores: object) -> Raw:
    """`$chave` no template, valor escapado no lugar. Devolve `Raw`.

    Devolve `Raw` de propósito: o resultado de um template já é HTML montado, e
    aninhar pedaço em pedaço (linha dentro de tabela, tabela dentro de página) é
    o uso normal. O que **não** é normal é texto vindo de fora chegar como `Raw`,
    e isso só acontece se alguém escrever `Raw` no caminho.

    Usa `substitute`, não `safe_substitute`: chave que falta é erro na hora do
    teste, e não um `$titulo` literal aparecendo na tela do Dotcom.
    """
    return Raw(Template(template).substitute({k: escapar(v) for k, v in valores.items()}))


def juntar(pedacos, separador: str = "\n") -> Raw:
    """Concatena pedaços de HTML já montados."""
    return Raw(separador.join(str(p) for p in pedacos))


def lista(itens, vazio: str = "—") -> Raw:
    """`<ul>` de textos escapados, ou um traço quando não há nada."""
    itens = list(itens)
    if not itens:
        return Raw(f"<p class=vazio>{escapar(vazio)}</p>")
    linhas = juntar(substituir("<li>$texto</li>", texto=i) for i in itens)
    return Raw(f"<ul>\n{linhas}\n</ul>")


def tabela(cabecalho: list[str], linhas: list[list[object]], vazio: str = "—") -> Raw:
    """`<table>` com tudo escapado. Célula que já é `Raw` passa intacta.

    É o único ponto em que uma célula pode conter marcação — um `<span
    class=selo>` de status, por exemplo — e ela tem de chegar aqui como `Raw`,
    montada por `substituir`.
    """
    if not linhas:
        return Raw(f"<p class=vazio>{escapar(vazio)}</p>")
    th = juntar((substituir("<th>$t</th>", t=c) for c in cabecalho), "")
    trs = []
    for linha in linhas:
        tds = juntar((substituir("<td>$v</td>", v=v) for v in linha), "")
        trs.append(Raw(f"<tr>{tds}</tr>"))
    return Raw(f"<table>\n<thead><tr>{th}</tr></thead>\n<tbody>\n{juntar(trs)}\n</tbody>\n</table>")


MENU = (
    ("/", "Painel"),
    ("/execucoes", "Execuções"),
    ("/acervo", "Acervo"),
)

_PAGINA = """<!doctype html>
<html lang=pt-BR>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>$titulo · dlb</title>
<link rel=stylesheet href="/static/painel.css">
<body>
<header>
  <strong>dlb</strong>
  <nav>$menu</nav>
  <span class=vault title="$vault">$vault_curto</span>
</header>
<main>
$corpo
</main>
<footer>$rodape</footer>
<script src="/static/painel.js"></script>
</body>
</html>
"""


def pagina(titulo: str, corpo: Raw, ativo: str = "/", vault: str = "", rodape: str = "") -> str:
    """Envelope de toda tela. `corpo` já vem montado por `substituir`."""
    menu = juntar(
        (
            substituir(
                '<a href="$href" class="$classe">$rotulo</a>',
                href=href,
                classe="ativo" if href == ativo else "",
                rotulo=rotulo,
            )
            for href, rotulo in MENU
        ),
        "",
    )
    vault_texto = str(vault)
    return substituir(
        _PAGINA,
        titulo=titulo,
        menu=menu,
        corpo=corpo,
        vault=vault_texto,
        vault_curto=vault_texto.rsplit("/", 1)[-1] or vault_texto,
        rodape=rodape,
    )
