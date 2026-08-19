"""Estruturas de dados que atravessam o pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class LinkHit:
    """Um link cru, como saiu do Discord. Nada interpretado ainda."""

    url: str
    channel_id: str
    message_id: str
    author: str
    posted_at: datetime          # já convertido para o fuso configurado
    context: str                 # texto da mensagem, sem o link
    jump_url: str

    @property
    def day(self) -> str:
        return self.posted_at.strftime("%Y-%m-%d")

    @property
    def hhmm(self) -> str:
        return self.posted_at.strftime("%H:%M")


@dataclass
class PageMeta:
    """O que conseguimos ler da própria página (best-effort)."""

    title: str = ""
    description: str = ""
    site: str = ""
    error: str = ""
    author: str = ""             # canal do YouTube, autor do artigo
    text: str = ""               # texto central da página, para o LLM resumir
    kind: str = ""               # "youtube" | "html" | ""
    legivel: bool = True         # False quando nada da página pôde ser lido


@dataclass
class LinkNote:
    """O link já classificado — é isto que vira nota no diário."""

    hit: LinkHit
    meta: PageMeta = field(default_factory=PageMeta)
    titulo: str = ""
    resumo: str = ""
    categoria: str = "outro"
    area: str = "area/ia"
    conceitos: list[str] = field(default_factory=list)
    porque_importa: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["hit"]["posted_at"] = self.hit.posted_at.isoformat()
        return d


# Vocabulário fechado de categorias. Manter curto de propósito: categoria demais
# é o mesmo que categoria nenhuma.
CATEGORIAS = [
    "artigo",        # texto de opinião, análise, blog post
    "documentacao",  # docs oficiais, referência, spec
    "ferramenta",    # produto, app, serviço
    "repositorio",   # código no GitHub/GitLab
    "paper",         # publicação acadêmica
    "video",         # YouTube, talk, screencast
    "discussao",     # thread, fórum, issue, post em rede social
    "lancamento",    # anúncio de release, changelog, notícia de produto
    "curso",         # material didático, tutorial longo
    "outro",
]

# Tags de área do vault (ver 90_META/ESQUEMA.md — namespace fechado).
# Para acrescentar uma área nova (ex.: area/dev), altere o ESQUEMA primeiro.
AREAS = ["area/ia", "area/buzz", "area/pkm", "area/gestao"]
