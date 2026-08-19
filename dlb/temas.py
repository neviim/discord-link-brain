"""Que conceitos já viraram tema seu — um relatório, nunca um MOC automático.

O `plan/plan.md` enquadrava isto por contagem de tag ("quando uma tag passar de
~15 links"). Não sobrevive ao contrato do vault: tag de assunto é proibida, e a
relação real são wikilinks para páginas de `20_WIKI/`. O sinal está em
`conceitos`, que o pipeline já emite como `[[X]]` ou `X *(candidato)*`.

O limiar padrão é **8**, e não 15: é o número que o próprio `MOC - Índice` do vault
usa. Onde os dois discordam, vale o do vault.

E o relatório **não cria nada**: nem MOC, nem stub em `20_WIKI/`. Compilar é
trabalho curado — é a decisão registrada em DECISOES.md, e uma pasta cheia de MOC
vazio é pior que uma pasta sem MOC nenhum.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import frontmatter
from .corpus import LinkEntry

# Os líderes brutos do acervo são *plataformas*, não temas: onde o link mora, não
# do que ele trata. Sem esta lista o relatório é ruído — medido: Instagram 7,
# YouTube 6, contra n8n 4. Ignorado aparece no relatório, nunca sai em silêncio.
PLATAFORMAS = {
    "youtube", "instagram", "tiktok", "twitter", "x (twitter)", "x", "github",
    "gitlab", "reddit", "linkedin", "facebook", "medium", "substack", "discord",
    "spotify", "twitch", "kaggle", "hugging face", "huggingface", "google",
    "podcast", "newsletter", "blog",
}


@dataclass
class Tema:
    nome: str                       # nome canônico
    links: list[LinkEntry] = field(default_factory=list)
    variantes: set[str] = field(default_factory=set)
    tem_pagina: bool = False
    backlinks: int = 0

    @property
    def total(self) -> int:
        return len(self.links)

    @property
    def dias(self) -> int:
        return len({e.dia for e in self.links})

    @property
    def primeiro(self) -> str:
        return min((e.dia for e in self.links), default="")

    @property
    def ultimo(self) -> str:
        return max((e.dia for e in self.links), default="")


@dataclass
class Relatorio:
    maduros: list[Tema] = field(default_factory=list)     # >= limiar
    chegando: list[Tema] = field(default_factory=list)    # limiar-2 .. limiar-1
    resto: int = 0
    descartados: list[tuple[str, int]] = field(default_factory=list)
    limiar: int = 8


def _chave(nome: str) -> str:
    """Normaliza para agrupar variantes: sem acento, caixa baixa, espaço colapsado.

    **Não** mexe em plural. Cortar `s` por tamanho de palavra mutila sigla —
    `SaaS` viraria `saa`. O colapso de plural acontece em `_juntar_plurais`, que
    tem o acervo inteiro à vista e só junta quando as duas formas existem.
    """
    limpo = "".join(
        c for c in unicodedata.normalize("NFD", nome.strip())
        if unicodedata.category(c) != "Mn"
    ).casefold()
    return re.sub(r"\s+", " ", limpo)


def _juntar_plurais(temas: dict[str, "Tema"]) -> dict[str, "Tema"]:
    """Junta `X` e `Xs` **quando as duas formas aparecem** no acervo.

    Esta é a diferença entre uma regra previsível e um stemmer: não adivinha
    plural, observa. `Transformer` + `Transformers` viram um tema; `SaaS` sozinho
    fica intacto porque `saa` não existe.

    O nome canônico é o da forma mais frequente — e a que tem página no wiki
    ganha de qualquer jeito, porque o relatório deve concordar com o grafo.
    """
    for chave in sorted(temas, key=len, reverse=True):
        if not chave.endswith("s"):
            continue
        singular = chave[:-1]
        if singular not in temas or singular == chave:
            continue
        plural_tema, singular_tema = temas[chave], temas[singular]
        if plural_tema.tem_pagina and not singular_tema.tem_pagina:
            fica, sai, sai_chave = plural_tema, singular_tema, singular
        elif singular_tema.tem_pagina and not plural_tema.tem_pagina:
            fica, sai, sai_chave = singular_tema, plural_tema, chave
        elif plural_tema.total >= singular_tema.total:
            fica, sai, sai_chave = plural_tema, singular_tema, singular
        else:
            fica, sai, sai_chave = singular_tema, plural_tema, chave

        fica.variantes |= sai.variantes
        for e in sai.links:
            if e not in fica.links:
                fica.links.append(e)
        temas.pop(sai_chave)
        temas[_chave(fica.nome)] = fica
    return temas


def _aliases_do_wiki(root: Path) -> dict[str, str]:
    """chave → nome real da página, incluindo os `aliases` dela.

    Assim um conceito que casa um alias conta para a página canônica: o relatório
    concorda com o grafo em vez de concordar com as strings cruas.
    """
    mapa: dict[str, str] = {}
    for md in (root / "20_WIKI").glob("*.md"):
        mapa[_chave(md.stem)] = md.stem
        try:
            meta, _ = frontmatter.split(md.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        valor = meta.get("aliases")
        for alias in (valor if isinstance(valor, list) else [valor] if valor else []):
            mapa[_chave(str(alias))] = md.stem
    return mapa


def _contar_backlinks(root: Path, nome: str) -> int:
    """Quantas notas apontam para a página, lendo arquivo.

    `obsidian backlinks total` é a autoridade, mas é ferramenta **de host**: fala
    com o app rodando e não existe dentro do container. Isto é a substituição por
    leitura de arquivo, e conta também link em propriedade — o ESQUEMA provou que
    ali é aresta de verdade. Pode divergir da contagem do app quando há alias.
    """
    padrao = re.compile(r"\[\[" + re.escape(nome) + r"(?:\||#|\]\])")
    total = 0
    for md in root.rglob("*.md"):
        if md.stem == nome:
            continue
        try:
            if padrao.search(md.read_text(encoding="utf-8", errors="replace")):
                total += 1
        except OSError:
            continue
    return total


def levantar(
    root: Path,
    entradas: list[LinkEntry],
    limiar: int = 8,
    ignorar: set[str] | None = None,
    com_backlinks: bool = False,
) -> Relatorio:
    ignorar = {_chave(x) for x in (ignorar or PLATAFORMAS)}
    wiki = _aliases_do_wiki(root)

    temas: dict[str, Tema] = {}
    descartados: dict[str, int] = {}
    for entrada in entradas:
        for bruto in entrada.conceitos:
            chave = _chave(bruto)
            if not chave:
                continue
            if chave in ignorar:
                descartados[bruto] = descartados.get(bruto, 0) + 1
                continue
            tema = temas.get(chave)
            if tema is None:
                # Nome canônico: o da página do wiki quando existe, senão a
                # primeira grafia vista.
                real = wiki.get(chave)
                tema = temas[chave] = Tema(
                    nome=real or bruto.strip(), tem_pagina=real is not None
                )
            tema.variantes.add(bruto.strip())
            if entrada not in tema.links:
                tema.links.append(entrada)

    temas = _juntar_plurais(temas)

    if com_backlinks:
        for tema in temas.values():
            if tema.tem_pagina:
                tema.backlinks = _contar_backlinks(root, tema.nome)

    ordenados = sorted(temas.values(), key=lambda t: (-t.total, -t.dias, t.nome))
    rel = Relatorio(limiar=limiar)
    for tema in ordenados:
        if tema.total >= limiar:
            rel.maduros.append(tema)
        elif tema.total >= max(2, limiar - 2):
            rel.chegando.append(tema)
        else:
            rel.resto += 1
    rel.descartados = sorted(descartados.items(), key=lambda p: -p[1])
    return rel


def _bloco_moc(tema: Tema) -> list[str]:
    """Bloco pronto para colar num MOC novo. A prosa é trabalho seu."""
    linhas = [
        f"# MOC - {tema.nome}",
        "",
        f"> {tema.total} link(s) capturados entre {tema.primeiro} e {tema.ultimo}.",
        "> Escreva aqui, com suas palavras, o que você entendeu do tema.",
        "",
        "## Material",
        "",
    ]
    for e in sorted(tema.links, key=lambda x: x.dia):
        linhas.append(f"- [[{e.nome}]] — {e.resumo[:120]}" if e.resumo else f"- [[{e.nome}]]")
    linhas.append("")
    return linhas


def formatar(rel: Relatorio, markdown: bool = False) -> str:
    linhas: list[str] = []
    if not markdown:
        linhas += [f"Temas com {rel.limiar}+ links:", ""]
        if not rel.maduros:
            linhas.append("  nenhum ainda.")
        for tema in rel.maduros:
            marca = "página existe" if tema.tem_pagina else "sem página"
            extra = f" · {tema.backlinks} backlink(s)" if tema.backlinks else ""
            linhas.append(
                f"  {tema.total:3} links · {tema.dias:2} dia(s) · {tema.nome} "
                f"({marca}{extra})"
            )
            if len(tema.variantes) > 1:
                linhas.append(f"        grafias: {', '.join(sorted(tema.variantes))}")

        linhas += ["", f"Chegando ({max(2, rel.limiar - 2)}–{rel.limiar - 1} links):", ""]
        for tema in rel.chegando:
            linhas.append(f"  {tema.total:3} links · {tema.nome}")
        if not rel.chegando:
            linhas.append("  nenhum.")

        linhas += ["", f"Outros {rel.resto} conceito(s) com menos que isso."]
        if rel.descartados:
            linhas += ["", "Descartados como plataforma (ajuste com TEMAS_IGNORAR):", ""]
            linhas.append(
                "  " + ", ".join(f"{n} ({c})" for n, c in rel.descartados[:12])
            )
        linhas += [
            "",
            "Nada foi criado no vault. Compilar é trabalho curado — se um destes",
            "virou tema seu de verdade, escreva o MOC com as suas palavras.",
        ]
        return "\n".join(linhas)

    for tema in rel.maduros:
        linhas += _bloco_moc(tema)
        linhas += ["---", ""]
    return "\n".join(linhas) or "Nenhum tema passou do limiar."
