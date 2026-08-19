"""Descobre do que a página trata. Best-effort: falhar aqui nunca interrompe.

Três caminhos, do mais confiável para o menos:

  YouTube   oEmbed devolve título e canal sem API key e sem bloqueio. A página
            `/watch` é tentada em seguida para pegar a descrição, mas o YouTube
            responde 429 para requisição de servidor com frequência — por isso
            ela é extra, nunca a base.
  HTML      og:/<title> como antes, e agora também o **texto central** do
            artigo. É o que permite ao LLM resumir o conteúdo de fato em vez de
            parafrasear a meta description de marketing.
  nada      devolve o que ainda identifica o link: domínio e caminho da URL.
            Melhor um rótulo fraco do que uma nota vazia.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse

import requests

from .extract import host_of, youtube_id
from .models import PageMeta

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 discord-link-brain/0.1"
)
MAX_BYTES = 512 * 1024
MAX_TEXT = 3000  # o bastante para o LLM entender o assunto sem inchar o prompt

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# Blocos que nunca são conteúdo — tirados antes de procurar o texto do artigo.
RUIDO_RE = re.compile(
    r"(?is)<(script|style|nav|header|footer|aside|noscript|svg|form|iframe)\b.*?</\1>"
)
CORPO_RE = re.compile(r"(?is)<(article|main)\b[^>]*>(.*?)</\1>")
PARAGRAFO_RE = re.compile(r"(?is)<(p|h1|h2|h3|li)\b[^>]*>(.*?)</\1>")
TAG_RE = re.compile(r"(?s)<[^>]+>")

def _meta_content(body: str, *names: str) -> str:
    for name in names:
        pattern = re.compile(
            r"""<meta[^>]+(?:property|name)\s*=\s*["']"""
            + re.escape(name)
            + r"""["'][^>]*>""",
            re.IGNORECASE,
        )
        tag = pattern.search(body)
        if not tag:
            continue
        content = re.search(
            r"""content\s*=\s*["'](.*?)["']""", tag.group(0), re.IGNORECASE | re.DOTALL
        )
        if content:
            return html.unescape(content.group(1)).strip()
    return ""


def main_text(body: str) -> str:
    """Texto corrido do conteúdo principal, sem menu, rodapé nem script.

    Deliberadamente ingênuo: parágrafos e títulos de dentro de <article>/<main>,
    ou da página toda se não houver. Site renderizado só por JavaScript devolve
    vazio, e aí os metadados og: assumem.
    """
    limpo = RUIDO_RE.sub(" ", body)
    corpo = CORPO_RE.search(limpo)
    alvo = corpo.group(2) if corpo else limpo

    pedacos = []
    for _, bruto in PARAGRAFO_RE.findall(alvo):
        texto = html.unescape(TAG_RE.sub(" ", bruto))
        texto = re.sub(r"\s+", " ", texto).strip()
        if len(texto) > 40:  # linha curta costuma ser menu, botão ou legenda
            pedacos.append(texto)
        if sum(len(p) for p in pedacos) > MAX_TEXT:
            break
    return " ".join(pedacos)[:MAX_TEXT]


def _baixar(url: str, timeout: float) -> tuple[str, str]:
    """Devolve (content_type, corpo). Levanta exceção em qualquer problema."""
    with requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json",
            "Accept-Language": "pt-BR,pt,en",
        },
        stream=True,
        allow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        chunks: list[bytes] = []
        size = 0
        for chunk in resp.iter_content(8192):
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_BYTES:
                break
        corpo = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
    return content_type, corpo


def _fallback(url: str, erro: str = "") -> PageMeta:
    """Sem conseguir ler a página, ainda dá para dizer o que o link é.

    Domínio e caminho da URL já identificam muita coisa
    ('github.com/user/projeto', 'arxiv.org/abs/2401.12345') e o LLM aproveita
    isso junto com o comentário de quem postou.
    """
    parsed = urllib.parse.urlparse(url)
    trilha = urllib.parse.unquote(parsed.path).strip("/").replace("-", " ").replace("_", " ")
    return PageMeta(
        site=host_of(url),
        description=f"Caminho da URL: {trilha}" if trilha else "",
        error=erro[:200],
        legivel=False,
    )


def _fetch_youtube(url: str, video_id: str, timeout: float) -> PageMeta:
    # `legivel=False` explícito: o default de PageMeta é True, e as duas
    # atribuições abaixo estão dentro de `try`. Se as duas buscas falharem, um
    # vídeo que ninguém conseguiu ler sairia marcado como lido, com título vazio
    # — e o prompt do classify deixaria de instruir o modelo a dizer que não leu.
    meta = PageMeta(site="YouTube", kind="youtube", legivel=False)

    # 1) oEmbed: sem chave, sem bloqueio, devolve título e canal.
    try:
        alvo = f"https://www.youtube.com/watch?v={video_id}"
        endpoint = (
            "https://www.youtube.com/oembed?url="
            + urllib.parse.quote(alvo, safe="")
            + "&format=json"
        )
        _, corpo = _baixar(endpoint, timeout)
        dados = json.loads(corpo)
        meta.title = str(dados.get("title") or "")[:300]
        meta.author = str(dados.get("author_name") or "").strip()[:120]
        meta.legivel = bool(meta.title)
    except Exception as exc:
        meta.error = f"oembed: {str(exc)[:120]}"

    # 2) Descrição do vídeo. O YouTube costuma responder 429 para servidor;
    #    quando vem, é lucro — quando não vem, título e canal já bastam.
    try:
        _, pagina = _baixar(f"https://www.youtube.com/watch?v={video_id}", timeout)
        bruto = re.search(r'"shortDescription":"(.*?)","', pagina, re.DOTALL)
        if bruto:
            texto = bruto.group(1).encode().decode("unicode_escape", errors="replace")
            meta.description = texto.strip()[:600]
        else:
            meta.description = _meta_content(pagina, "og:description")[:600]
        if not meta.title:
            meta.title = _meta_content(pagina, "og:title")[:300]
            meta.legivel = bool(meta.title)
    except Exception as exc:
        if not meta.description:
            meta.error = (meta.error + f" | descricao: {str(exc)[:100]}").strip(" |")

    if not meta.legivel:
        alternativa = _fallback(url, meta.error)
        alternativa.kind = "youtube"
        alternativa.site = "YouTube"
        return alternativa
    return meta


def fetch_meta(url: str, timeout: float = 10.0) -> PageMeta:
    video_id = youtube_id(url)
    if video_id:
        return _fetch_youtube(url, video_id, timeout)

    try:
        content_type, corpo = _baixar(url, timeout)
    except Exception as exc:  # rede é rede: seguimos sem metadados
        return _fallback(url, str(exc))

    if "html" not in content_type.lower():
        return _fallback(url, f"content-type {content_type}")

    titulo = _meta_content(corpo, "og:title", "twitter:title")
    if not titulo:
        achado = TITLE_RE.search(corpo)
        titulo = (
            html.unescape(re.sub(r"\s+", " ", achado.group(1))).strip() if achado else ""
        )

    descricao = _meta_content(corpo, "og:description", "twitter:description", "description")
    texto = main_text(corpo)

    return PageMeta(
        title=titulo[:300],
        description=descricao[:600],
        site=_meta_content(corpo, "og:site_name") or host_of(url),
        author=_meta_content(corpo, "author", "article:author")[:120],
        text=texto,
        kind="html",
        legivel=bool(titulo or descricao or texto),
    )
