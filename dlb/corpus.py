"""Leitura estruturada da camada `50_LINKS/`.

O pipeline escreve a nota de link; este módulo a lê de volta. Existe porque a
síntese periódica — e depois a busca e o ressurgimento — precisam do acervo como
dado, não como markdown.

A autoridade aqui é `50_LINKS/`, não `40_DIARIO/`: a nota é a versão canônica do
link, e é ela que carrega a triagem e a avaliação humanas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import frontmatter

LINKS_DIR = "50_LINKS"

# O corpo é escrito por `Vault._corpo_link`, então o formato é conhecido e
# estável. Ancorar nos headings, não em posição de linha.
RESUMO_RE = re.compile(r"^> (?P<texto>.+)$", re.MULTILINE)
PORQUE_RE = re.compile(r"^## Por que importa\n\n(?P<texto>.+?)\n\n", re.MULTILINE | re.DOTALL)
CANDIDATOS_RE = re.compile(
    r"^## Conceitos sem página\n\n(?P<bloco>(?:- .+\n)+)", re.MULTILINE
)
WIKILINK_RE = re.compile(r"^\[\[(?P<nome>.+)\]\]$")


@dataclass
class LinkEntry:
    """Um link do acervo, já estruturado. O que a síntese e a busca consomem."""

    path: Path
    url: str = ""
    titulo: str = ""
    dia: str = ""                 # `capturado`, no formato AAAA-MM-DD
    dominio: str = ""
    categoria: str = "outro"
    area: str = ""
    triagem: str = ""
    autor: str = ""
    resumo: str = ""
    porque_importa: str = ""
    conceitos: list[str] = field(default_factory=list)      # todos, resolvidos ou não
    sem_pagina: list[str] = field(default_factory=list)     # só os candidatos
    nota: int | None = None
    fonte: str = ""               # nome da nota de 10_FONTES do dia

    @property
    def nome(self) -> str:
        """O nome do arquivo sem extensão — é o texto do wikilink."""
        return self.path.stem

    @property
    def semana(self) -> str:
        return semana_de(self.dia)


def semana_de(dia: str) -> str:
    """`2026-08-18` → `2026-W34`, na numeração ISO.

    Usa `isocalendar()[0]`, **não** `.year`: 2026-12-31 pertence à semana
    2027-W01, e usar o ano do calendário produz um erro silencioso uma vez por
    ano. Tem teste na fronteira.
    """
    try:
        ano_iso, semana, _ = date.fromisoformat(dia).isocalendar()
    except ValueError:
        return ""
    return f"{ano_iso}-W{semana:02d}"


def mes_de(dia: str) -> str:
    """`2026-08-18` → `2026-08`."""
    return dia[:7] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dia or "") else ""


def _lista(valor) -> list[str]:
    if valor is None:
        return []
    return [str(v) for v in valor] if isinstance(valor, list) else [str(valor)]


def _ler_nota(path: Path) -> LinkEntry | None:
    """Uma nota de `50_LINKS/` → `LinkEntry`. `None` se não for nota de link."""
    try:
        texto = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    meta, corpo = frontmatter.split(texto)
    if meta.get("tipo") != "link":
        # Arquivo humano largado na pasta. Não é erro, só não é nosso.
        return None

    # O corpo é lido só até `## Minhas notas`: o que você escreveu abaixo é seu,
    # e não entra no prompt do LLM sem você pedir.
    gerado = corpo.split("## Minhas notas", 1)[0]

    entry = LinkEntry(
        path=path,
        url=str(meta.get("url") or ""),
        dia=str(meta.get("capturado") or meta.get("criado") or ""),
        dominio=str(meta.get("dominio") or ""),
        categoria=str(meta.get("categoria") or "outro"),
        triagem=str(meta.get("triagem") or ""),
        autor=str(meta.get("autor") or ""),
    )

    areas = [t for t in _lista(meta.get("tags")) if t.startswith("area/")]
    entry.area = areas[0] if areas else ""

    valor_nota = meta.get("nota")
    if isinstance(valor_nota, int):
        entry.nota = valor_nota

    # O título completo mora em `aliases` quando o nome do arquivo é versão
    # encurtada dele; senão o próprio nome é o título.
    aliases = _lista(meta.get("aliases"))
    entry.titulo = aliases[0] if aliases else re.sub(r"^\d{4}-\d{2}-\d{2} ", "", path.stem)

    for valor in _lista(meta.get("fonte")):
        m = WIKILINK_RE.match(valor.strip())
        if m:
            entry.fonte = m.group("nome")
            break

    # Conceito com página vive em `relacionado`; o resto do que está ali é a
    # daily e o MOC, que não são conceito.
    for valor in _lista(meta.get("relacionado")):
        m = WIKILINK_RE.match(valor.strip())
        if not m:
            continue
        nome = m.group("nome")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", nome) or nome.startswith("MOC - "):
            continue
        entry.conceitos.append(nome)

    m = RESUMO_RE.search(gerado)
    if m:
        entry.resumo = m.group("texto").strip()
    m = PORQUE_RE.search(gerado)
    if m:
        entry.porque_importa = " ".join(m.group("texto").split())
    m = CANDIDATOS_RE.search(gerado)
    if m:
        entry.sem_pagina = [
            l[2:].strip() for l in m.group("bloco").splitlines() if l.startswith("- ")
        ]
        entry.conceitos += entry.sem_pagina

    return entry


def ler_links(root: Path) -> list[LinkEntry]:
    """Todo o acervo de `50_LINKS/`, ordenado por dia e depois por nome."""
    entradas = []
    for path in sorted((root / LINKS_DIR).glob("*.md")):
        entry = _ler_nota(path)
        if entry is not None:
            entradas.append(entry)
    entradas.sort(key=lambda e: (e.dia, e.path.stem))
    return entradas


def da_semana(entradas: list[LinkEntry], semana: str) -> list[LinkEntry]:
    return [e for e in entradas if e.semana == semana]


def do_mes(entradas: list[LinkEntry], mes: str) -> list[LinkEntry]:
    return [e for e in entradas if mes_de(e.dia) == mes]


def semana_atual(hoje: str) -> str:
    return semana_de(hoje)


def semana_anterior(hoje: str) -> str:
    """A semana ISO anterior à de `hoje`. Sete dias para trás resolve a virada
    de ano de graça, porque `semana_de` já usa o ano ISO."""
    d = date.fromisoformat(hoje)
    return semana_de((d.fromordinal(d.toordinal() - 7)).isoformat())


def semanas_do_mes(entradas: list[LinkEntry], mes: str) -> list[str]:
    """As semanas ISO que têm link naquele mês, em ordem.

    Semana que cruza a virada do mês aparece nos dois meses — é o que se quer:
    o rollup mensal cita a semana inteira, não um pedaço dela.
    """
    vistas = {e.semana for e in do_mes(entradas, mes) if e.semana}
    return sorted(vistas)
