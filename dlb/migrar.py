"""Reconstrói notas atômicas a partir do que o pipeline já escreveu no vault.

Não chama LLM e não toca a rede. Tudo que o modelo produziu está nos bullets de
`40_DIARIO/` — título, resumo, por que importa, conceitos, categoria, área — e
tudo que é cru está em `10_FONTES/` — hora, autor, link da mensagem, título e
descrição da página. Reclassificar seria gastar 26 chamadas para produzir um
resultado *diferente* do que você já leu no diário.

Divisão de autoridade, a mesma do resto do projeto: a **fonte** manda no dado
cru, o **diário** manda no dado interpretado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .extract import host_of
from .models import CATEGORIAS, LinkHit, LinkNote, PageMeta
from .vault import Vault, WriteResult

# O bullet rico, como `_render_notes` escreveu por 26 dias. O `.+?` do título é
# preguiçoso e o `[^)\s]+` da URL não atravessa `)`, então título com parêntese
# não engole o link — 8 dos 70 títulos reais têm parêntese.
BULLET_RICO = re.compile(
    r"^- \*\*\[(?P<titulo>.+?)\]\((?P<url>https?://[^)\s]+)\)\*\*"
    r"(?:\s+—\s*(?P<resumo>.*))?$"
)
# O bullet magro, que aponta para a nota atômica: já migrado.
BULLET_MAGRO = re.compile(r"^- \[\[(?P<nome>[^\]]+)\]\]")
PORQUE = re.compile(r"^  \*Por que importa:\* (?P<texto>.+)$")
CONCEITOS = re.compile(r"^  Conceitos: (?P<lista>.+)$")
SUB = re.compile(
    r"^  <sub>(?P<hhmm>\d{2}:\d{2}) · (?P<autor>.+?) · #(?P<area>[\w/]+)"
    r"(?: · \[abrir\]\([^)]*\))?</sub>$"
)
NOTA_LINE = re.compile(r"^  Nota: \[\[(?P<nome>[^\]]+)\]\]$")
HEADING = re.compile(r"^### (?P<titulo>.+)$")


@dataclass
class Bullet:
    """Um bullet do diário, já decomposto. `linha_fim` é onde inserir a nota."""

    url: str
    titulo: str
    resumo: str = ""
    porque_importa: str = ""
    conceitos: list[str] = field(default_factory=list)
    categoria: str = "outro"
    area: str = "area/ia"
    hhmm: str = "00:00"
    autor: str = ""
    ja_migrado: bool = False
    linha_fim: int = 0


@dataclass
class Relatorio:
    criados: int = 0
    pulados: int = 0
    atualizados: int = 0
    diarios_tocados: int = 0
    avisos: list[str] = field(default_factory=list)


def _limpa_conceito(bruto: str) -> str:
    """`[[Nostr]]` e `Nostr *(candidato)*` viram os dois `Nostr`."""
    nome = bruto.strip()
    nome = re.sub(r"\s*\*\(candidato\)\*\s*$", "", nome)
    nome = re.sub(r"^\[\[|\]\]$", "", nome.strip())
    return nome.strip()


def parse_secao(secao: str) -> tuple[list[Bullet], list[str]]:
    """Decompõe uma seção do diário em bullets. Devolve (bullets, avisos).

    Varredura com estado, não regex sobre o texto inteiro: as linhas de
    continuação pertencem ao bullet acima, e `_append_diario` faz a mesma
    `### categoria` aparecer várias vezes na seção com um `*Mais N link(s)*`
    entre os lotes. Chavear no bullet e não no heading é o que torna isso
    naturalmente correto.
    """
    bullets: list[Bullet] = []
    avisos: list[str] = []
    categoria = "outro"
    atual: Bullet | None = None
    linhas = secao.splitlines()

    for i, linha in enumerate(linhas):
        m = HEADING.match(linha)
        if m:
            # Vocabulário fechado, não nome: é o que descarta
            # "### Candidatos a página no wiki" e qualquer heading que você tenha
            # acrescentado à mão, sem depender do texto exato.
            titulo = m.group("titulo").strip()
            categoria = titulo if titulo in CATEGORIAS else None
            atual = None
            continue

        if categoria is None:
            continue  # dentro do bloco de candidatos ou de heading humano

        m = BULLET_MAGRO.match(linha)
        if m:
            atual = Bullet(
                url="", titulo=m.group("nome"), ja_migrado=True, linha_fim=i,
            )
            bullets.append(atual)
            continue

        m = BULLET_RICO.match(linha)
        if m:
            atual = Bullet(
                url=m.group("url"),
                titulo=m.group("titulo"),
                # O resumo é o resto da linha inteiro. Dividir no separador ` — `
                # cortaria errado 24 dos 70 bullets reais, que têm mais de um.
                resumo=(m.group("resumo") or "").strip(),
                categoria=categoria,
                linha_fim=i,
            )
            bullets.append(atual)
            continue

        if linha.startswith("- "):
            # Sob uma categoria válida, todo `- ` é bullet de link. Continuação
            # começa com dois espaços; candidato vive onde categoria é None.
            avisos.append(f"bullet não reconhecido: {linha[:70]}")
            atual = None
            continue

        if atual is None:
            continue

        m = PORQUE.match(linha)
        if m:
            atual.porque_importa = m.group("texto").strip()
            atual.linha_fim = i
            continue
        m = CONCEITOS.match(linha)
        if m:
            atual.conceitos = [
                c for c in (_limpa_conceito(x) for x in m.group("lista").split(", ")) if c
            ]
            atual.linha_fim = i
            continue
        m = SUB.match(linha)
        if m:
            atual.hhmm, atual.autor = m.group("hhmm"), m.group("autor").strip()
            atual.area = m.group("area")
            atual.linha_fim = i
            continue
        m = NOTA_LINE.match(linha)
        if m:
            atual.ja_migrado = True
            atual.linha_fim = i
            continue

    return bullets, avisos


def parse_fonte(texto: str) -> dict[str, dict[str, str]]:
    """URL → dado cru, lido de uma nota de `10_FONTES/`.

    A fonte é imutável e é a autoridade do que veio do Discord: hora exata,
    autor, link da mensagem, e o que a página respondeu quando foi lida.
    """
    dados: dict[str, dict[str, str]] = {}
    for bloco in texto.split("\n### ")[1:]:
        m = re.search(r"^<(https?://[^>]+)>$", bloco, re.MULTILINE)
        if not m:
            continue
        url = m.group(1)
        cab = re.match(r"(?P<hhmm>\d{2}:\d{2}) — (?P<autor>.+)$", bloco.splitlines()[0])
        campo = lambda nome: (  # noqa: E731 - lambda local, curta e óbvia
            re.search(rf"^{nome}: (.+)$", bloco, re.MULTILINE) or [None, ""]
        )[1]
        dados[url] = {
            "hhmm": cab.group("hhmm") if cab else "",
            "autor": cab.group("autor").strip() if cab else "",
            "jump_url": campo("Mensagem original"),
            "titulo_pagina": campo("Título da página"),
            "descricao": campo("Descrição"),
        }
    return dados


JUMP_ARROBA_ME = re.compile(r"^(https://discord\.com/channels/)@me/(\d+)/(\d+)$")


def reparar_jump(jump_url: str, guilds: dict[str, str]) -> str:
    """Troca `@me` pelo servidor do canal, quando o servidor é conhecido.

    `GET /channels/{id}/messages` não devolve `guild_id` por mensagem, e por um
    tempo o pipeline gravou todo link de mensagem como `/channels/@me/...` — que
    não abre em canal de servidor. As notas de `10_FONTES/` guardam o valor como
    foi capturado (invariante 9: a fonte não se corrige); a correção mora aqui,
    na camada derivada, que é exatamente o que o ESQUEMA prescreve.

    Sem `DISCORD_GUILD_IDS` configurado, devolve a URL intacta.
    """
    m = JUMP_ARROBA_ME.match(jump_url or "")
    if not m:
        return jump_url
    base, canal, msg = m.groups()
    guild = guilds.get(canal)
    return f"{base}{guild}/{canal}/{msg}" if guild else jump_url


def _fontes_do_dia(vault: Vault, dia: str) -> tuple[str, dict[str, dict[str, str]]]:
    """(rótulo do canal, dado cru por URL) das fontes daquele dia."""
    label, dados = "", {}
    for path in sorted((vault.root / "10_FONTES").glob(f"FONTE - Discord * - {dia}.md")):
        # "FONTE - Discord <label> - <dia>" → o label é o miolo.
        miolo = path.stem[len("FONTE - Discord "): -len(f" - {dia}")]
        label = label or miolo
        dados.update(parse_fonte(path.read_text(encoding="utf-8", errors="replace")))
    return label, dados


def _monta_nota(
    b: Bullet, dia: str, cru: dict[str, str], tz: str, guilds: dict[str, str]
) -> LinkNote:
    hhmm = cru.get("hhmm") or b.hhmm
    # O `posted_at` é reconstruído do horário local já convertido, então `.day` e
    # `.hhmm` batem com o original por construção.
    posted = datetime.fromisoformat(f"{dia}T{hhmm}:00").replace(tzinfo=ZoneInfo(tz))
    jump = reparar_jump(cru.get("jump_url", ""), guilds)
    hit = LinkHit(
        url=b.url,
        channel_id="",
        message_id=jump.rsplit("/", 1)[-1] if jump else "",
        author=cru.get("autor") or b.autor,
        posted_at=posted,
        context="",
        jump_url=jump,
    )
    meta = PageMeta(
        title=cru.get("titulo_pagina", ""),
        description=cru.get("descricao", ""),
        site=host_of(b.url),
        legivel=bool(cru.get("titulo_pagina")),
    )
    return LinkNote(
        hit=hit,
        meta=meta,
        titulo=b.titulo,
        resumo=b.resumo,
        categoria=b.categoria,
        area=b.area,
        conceitos=b.conceitos,
        porque_importa=b.porque_importa,
    )


def _inserir_referencias(
    path: Path, secao_original: str, insercoes: dict[int, str]
) -> bool:
    """Insere `Nota: [[...]]` sob os bullets migrados. Puramente aditivo.

    Sem esta linha as 70 notas migradas nascem **órfãs**: `parte-de` é aresta
    saindo, e o invariante 3 do ESQUEMA exige aresta *entrando* ou constar de um
    MOC. Reescrever o bullet rico em magro também resolveria, mas é a única
    operação capaz de destruir conteúdo — fica para uma decisão explícita.
    """
    if not insercoes:
        return False
    linhas = secao_original.splitlines()
    saida: list[str] = []
    for i, linha in enumerate(linhas):
        saida.append(linha)
        if i in insercoes:
            saida.append(f"  Nota: [[{insercoes[i]}]]")
    nova = "\n".join(saida)
    texto = path.read_text(encoding="utf-8", errors="replace")
    if secao_original not in texto:
        return False
    path.write_text(texto.replace(secao_original, nova, 1), encoding="utf-8")
    return True


def migrar(
    vault: Vault,
    timezone: str,
    dia: str | None = None,
    guilds: dict[str, str] | None = None,
    force: bool = False,
    tocar_diario: bool = True,
    limite: int | None = None,
    log=print,
) -> Relatorio:
    """Cria a nota atômica de cada link já capturado. Idempotente por URL."""
    rel = Relatorio()
    guilds = guilds or {}
    feitos = 0

    for dia_secao, path, secao in vault.diario_sections():
        if dia and dia_secao != dia:
            continue
        if limite is not None and feitos >= limite:
            break

        bullets, avisos = parse_secao(secao)
        rel.avisos += [f"{path.name}: {a}" for a in avisos]
        # Com `--force`, link já migrado volta à fila: é o que faz a flag valer
        # algo. Sem ela, `ja_migrado` sai antes de tocar o disco — a segunda
        # passada custa menos que a primeira.
        pendentes = [b for b in bullets if b.url and (force or not b.ja_migrado)]
        if not pendentes:
            continue

        label, cru = _fontes_do_dia(vault, dia_secao)
        if not label:
            rel.avisos.append(
                f"{dia_secao}: sem nota em 10_FONTES — não dá para declarar `fonte`, pulado"
            )
            continue

        log(f"[{dia_secao}] #{label} — {len(pendentes)} link(s)")
        insercoes: dict[int, str] = {}
        for b in pendentes:
            if limite is not None and feitos >= limite:
                break
            if b.url not in cru:
                rel.avisos.append(f"{dia_secao}: {b.url} não está na fonte do dia")
            nota = _monta_nota(b, dia_secao, cru.get(b.url, {}), timezone, guilds)
            r: WriteResult = vault.write_link_note(label, nota, force=force)
            log("    " + str(r))
            if r.action == "criado":
                rel.criados += 1
            elif r.action == "atualizado":
                rel.atualizados += 1
            else:
                rel.pulados += 1
            feitos += 1
            # Só insere onde ainda não há referência: em `--force` a linha
            # `Nota: [[...]]` já está lá e uma segunda seria duplicata.
            if not b.ja_migrado:
                insercoes[b.linha_fim] = r.path.stem

        if tocar_diario and not vault.dry_run:
            if _inserir_referencias(path, secao, insercoes):
                rel.diarios_tocados += 1

    return rel
