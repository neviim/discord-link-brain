"""Extrai links das mensagens do Discord e normaliza as URLs."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo

from .models import LinkHit

URL_RE = re.compile(r"https?://[^\s<>\"'`\)\]]+", re.IGNORECASE)

# Pontuação que quase sempre é da frase, não da URL. Fechamentos de parênteses
# e colchetes ficam de fora: são tratados pela contagem de balanceamento abaixo.
TRAILING = ".,;:!?'\"”’»>"

# Parâmetros de rastreamento: sujam o link e quebram a deduplicação.
TRACKING_PARAMS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref_src", "ref_url",
    "si", "spm", "yclid", "_hsenc", "_hsmi", "vero_id", "wt_mc",
}

IGNORED_HOSTS = {
    "tenor.com",
    "giphy.com",
    "media.discordapp.net",
    "cdn.discordapp.com",
}

YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "music.youtube.com",
}
YOUTUBE_PATH_RE = re.compile(r"^/(?:shorts|embed|live|v)/([^/?#]+)")

# O X manteve o domínio antigo funcionando, então o mesmo tweet circula nas duas
# formas. `t` e `s` são rastreadores do botão de compartilhar.
X_HOSTS = {"twitter.com", "www.twitter.com", "mobile.twitter.com",
           "x.com", "www.x.com", "mobile.x.com"}
X_TRACKING = {"t", "s"}


def youtube_id(url: str) -> str:
    """Id do vídeo em qualquer variante de URL do YouTube; "" se não for vídeo.

    Página de canal (`/@nome`) e playlist não têm id — caem fora de propósito,
    porque não são o mesmo tipo de conteúdo que um vídeo.
    """
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host not in YOUTUBE_HOSTS:
        return ""
    if host == "youtu.be":
        return parts.path.strip("/").split("/")[0]
    if parts.path == "/watch":
        return dict(parse_qsl(parts.query)).get("v", "")
    match = YOUTUBE_PATH_RE.match(parts.path)
    return match.group(1) if match else ""


def normalize_url(url: str) -> str:
    """Tira pontuação final, parâmetros de rastreamento e a barra sobrando.

    Também **canoniza vídeo do YouTube** para `watch?v=<id>`. Sem isso, o mesmo
    vídeo compartilhado como `/shorts/ID` e como `/watch?v=ID` — que é o que o
    botão de compartilhar do app faz — entra duas vezes no vault.
    """
    url = url.strip().rstrip(TRAILING)

    # Parênteses balanceados fazem parte da URL (Wikipédia adora); os órfãos, não.
    # Alterna entre tirar fechamento órfão e tirar pontuação para pegar "…(bar))."
    changed = True
    while changed:
        changed = False
        for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
            while url.endswith(closing) and url.count(closing) > url.count(opening):
                url = url[:-1]
                changed = True
        stripped = url.rstrip(TRAILING)
        if stripped != url:
            url, changed = stripped, True

    video = youtube_id(url)
    if video:
        return f"https://www.youtube.com/watch?v={video}"

    parts = urlsplit(url)
    host = parts.netloc.lower()

    descartar = set(TRACKING_PARAMS)
    if host in X_HOSTS:
        host = "x.com"
        descartar |= X_TRACKING

    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in descartar and not k.lower().startswith("utm_")
    ]
    path = parts.path
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")

    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query), ""))


def host_of(url: str) -> str:
    return urlsplit(url).netloc.removeprefix("www.")


def _clean_context(content: str) -> str:
    """O texto da mensagem sem as URLs — é isso que dá pista da intenção."""
    text = URL_RE.sub("", content)
    text = re.sub(r"<a?:\w+:\d+>", "", text)      # emojis customizados
    text = re.sub(r"[<>]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def urls_in_message(message: dict) -> list[str]:
    found: list[str] = []
    found.extend(URL_RE.findall(message.get("content") or ""))

    for embed in message.get("embeds") or []:
        for key in ("url",):
            if embed.get(key):
                found.append(embed[key])
    for attachment in message.get("attachments") or []:
        if attachment.get("url"):
            found.append(attachment["url"])

    return found


def day_of_message(message: dict, tz: str = "UTC") -> str:
    """Dia local da mensagem — o mesmo agrupamento que `LinkHit.day` usa."""
    return (
        datetime.fromisoformat(message["timestamp"].replace("Z", "+00:00"))
        .astimezone(ZoneInfo(tz))
        .strftime("%Y-%m-%d")
    )


def extract_links(
    messages: list[dict],
    channel_id: str,
    guild_id: str | None = None,
    tz: str = "UTC",
    include_bots: bool = False,
) -> list[LinkHit]:
    """Mensagens cruas → links deduplicados, em ordem cronológica."""
    zone = ZoneInfo(tz)
    seen: set[str] = set()
    hits: list[LinkHit] = []

    for message in messages:
        author = message.get("author") or {}
        if author.get("bot") and not include_bots:
            continue

        raw_urls = urls_in_message(message)
        if not raw_urls:
            continue

        posted_at = datetime.fromisoformat(
            message["timestamp"].replace("Z", "+00:00")
        ).astimezone(zone)
        context = _clean_context(message.get("content") or "")
        guild = guild_id or message.get("guild_id") or "@me"

        for raw in raw_urls:
            url = normalize_url(raw)
            if not url or url in seen or host_of(url) in IGNORED_HOSTS:
                continue
            seen.add(url)
            hits.append(
                LinkHit(
                    url=url,
                    channel_id=channel_id,
                    message_id=message["id"],
                    author=author.get("global_name")
                    or author.get("username")
                    or "desconhecido",
                    posted_at=posted_at,
                    context=context,
                    jump_url=(
                        f"https://discord.com/channels/{guild}/{channel_id}/{message['id']}"
                    ),
                )
            )

    hits.sort(key=lambda h: h.posted_at)
    return hits


def group_by_day(hits: list[LinkHit]) -> dict[str, list[LinkHit]]:
    days: dict[str, list[LinkHit]] = {}
    for hit in hits:
        days.setdefault(hit.day, []).append(hit)
    return days


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
