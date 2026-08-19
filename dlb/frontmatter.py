"""Leitura e escrita do frontmatter YAML no dialeto que o vault exige.

A regra que mais quebra na prática: wikilink dentro de propriedade precisa de
aspas (`- "[[Nota]]"`). Escrevendo arquivo direto em disco, a responsabilidade
é nossa — só a UI do Obsidian coloca as aspas sozinha.
"""

from __future__ import annotations

import yaml

# Ordem canônica das propriedades (ver 90_META/ESQUEMA.md). O que não estiver
# aqui vai depois, em ordem alfabética.
KEY_ORDER = [
    # `triagem` vem junto de `status`: são os dois eixos de estado da nota,
    # maturidade e fluxo, e ler um sem o outro engana.
    "tipo", "status", "triagem", "criado", "atualizado",
    "autor", "url", "capturado", "dominio", "categoria", "nota",
    "aliases", "tags",
    "parte-de", "relacionado", "depende-de", "fonte",
]


def split(text: str) -> tuple[dict, str]:
    """Devolve (frontmatter, corpo). Sem frontmatter → ({}, texto inteiro)."""
    if not text.startswith("---"):
        return {}, text
    # maxsplit=1: um `---` que apareça no corpo (régua horizontal) fica no corpo.
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        return {}, text
    head = parts[0][3:]
    body = parts[1].lstrip("\n")
    try:
        data = yaml.safe_load(head) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return data, body


# Caracteres a que o YAML dá significado especial quando abrem um escalar:
# `-` item de lista, `[{` coleção em linha, `#` comentário, `&*` âncora e alias,
# `!` tag, `|>` bloco, `?:` chave explícita, `,` separador, `%` diretiva,
# `'"` citação, `@` e a crase são reservados para uso futuro pela spec.
#
# Sem esta lista o estrago é pior que perder o metadado: `safe_load` levanta,
# `split()` devolve ({}, arquivo inteiro), e a escrita seguinte trata o
# frontmatter antigo como corpo e prepende um segundo bloco `---`.
INICIO_ESPECIAL = "-?:,[]{}#&*!|>'\"%@`"


def _scalar(value) -> str:
    text = "" if value is None else str(value)
    if text == "":
        return ""
    # Espaço nas pontas some sem aspas, e aí o valor lido não é o escrito.
    if (
        text[0] in INICIO_ESPECIAL
        or any(ch in text for ch in ':#"\'')
        or text != text.strip()
    ):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def render(data: dict) -> str:
    ordered = [k for k in KEY_ORDER if k in data]
    ordered += sorted(k for k in data if k not in KEY_ORDER)

    lines = ["---"]
    for key in ordered:
        value = data[key]
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            lines.extend(f"  - {_scalar(item)}" for item in value)
        else:
            rendered = _scalar(value)
            lines.append(f"{key}: {rendered}" if rendered else f"{key}:")
    lines.append("---")
    return "\n".join(lines) + "\n"


def compose(data: dict, body: str) -> str:
    return render(data) + "\n" + body.lstrip("\n")


def add_to_list(data: dict, key: str, value: str) -> bool:
    """Acrescenta `value` à propriedade-lista, sem duplicar. True se mudou."""
    current = data.get(key)
    if current is None:
        current = []
    elif not isinstance(current, list):
        current = [current]
    if value in current:
        return False
    current.append(value)
    data[key] = current
    return True
