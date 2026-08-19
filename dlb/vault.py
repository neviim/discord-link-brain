"""Escrita no vault Obsidian, respeitando 90_META/ESQUEMA.md.

Divisão de camadas que este projeto assume:

  10_FONTES/  captura crua e imutável do dia (todos os links, com autor e hora)
  40_DIARIO/  o resumo classificado do dia — registro cronológico
  30_MOC/     um MOC de navegação que junta as capturas e as liga ao índice

O que este projeto NÃO faz: criar páginas em 20_WIKI/. Compilar conceito é
trabalho curado (ver a skill obsidian-segundo-cerebro). Aqui os conceitos
saem listados como candidatos, marcados com #acao/expandir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import frontmatter
from .models import LinkNote

MOC_NAME = "MOC - Captura Discord"
FONTE_TRAILER = "## Páginas compiladas desta fonte"
SECTION_MARK = "<!-- discord-link-brain -->"
CANDIDATOS_HEADER = "### Candidatos a página no wiki"


@dataclass
class WriteResult:
    path: Path
    action: str  # "criado" | "atualizado" | "pulado"
    detail: str = ""

    def __str__(self) -> str:
        suffix = f" ({self.detail})" if self.detail else ""
        return f"{self.action:<10} {self.path.name}{suffix}"


def sanitize(name: str) -> str:
    """Nome de arquivo legível: sem os caracteres que quebram no Obsidian."""
    return re.sub(r'[\\/:*?"<>|#\[\]]', "", name).strip() or "canal"


def fonte_name(channel_label: str, day: str) -> str:
    return f"FONTE - Discord {sanitize(channel_label)} - {day}"


class Vault:
    def __init__(self, root: Path, dry_run: bool = False):
        self.root = root
        self.dry_run = dry_run

    # ------------------------------------------------------------------ util

    def _write(self, path: Path, text: str) -> None:
        if self.dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def archived_urls(self) -> set[str]:
        """URLs que já estão em alguma captura de `10_FONTES/`.

        A fonte é a autoridade sobre o que já foi arquivado — não um índice
        paralelo em `.state`, que sairia de sincronia com o vault e morreria
        junto com o cursor. Ler os arquivos é barato: são poucos e pequenos.
        """
        encontradas: set[str] = set()
        for path in (self.root / "10_FONTES").glob("FONTE - Discord *.md"):
            try:
                # `errors="replace"`: um arquivo corrompido não pode derrubar a
                # execução das 6h. Byte inválido vira �, as URLs em volta ainda
                # são lidas, e o pior caso é um link reprocessado.
                texto = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # write_fonte grava cada link como <url> em linha própria.
            encontradas.update(re.findall(r"^<(https?://[^>]+)>$", texto, re.MULTILINE))
        return encontradas

    def wiki_pages(self) -> dict[str, str]:
        """Mapa nome-normalizado → nome real das páginas de 20_WIKI/."""
        pages: dict[str, str] = {}
        for md in (self.root / "20_WIKI").glob("*.md"):
            pages[md.stem.casefold()] = md.stem
        return pages

    def link_or_candidate(self, conceito: str) -> tuple[str, bool]:
        """Wikilink só para página que existe — link pendurado derruba o lint."""
        real = self.wiki_pages().get(conceito.casefold())
        if real:
            return f"[[{real}]]", True
        return f"{conceito} *(candidato)*", False

    # ------------------------------------------------------------- 10_FONTES

    def write_fonte(
        self,
        channel_label: str,
        day: str,
        notes: list[LinkNote],
        force: bool = False,
    ) -> WriteResult:
        name = fonte_name(channel_label, day)
        path = self.root / "10_FONTES" / f"{name}.md"

        if path.exists() and not force:
            # 10_FONTES é imutável quanto ao que já está escrito: nada é
            # reinterpretado nem reescrito. Mas a captura do dia não termina às
            # 00:06 — numa grade de 6h chegam lotes novos ao longo do dia, e
            # eles têm que entrar. Acrescentar não é reescrever.
            return self._append_fonte(path, notes)

        areas = sorted({n.area for n in notes})
        meta = {
            "tipo": "fonte",
            "status": "maduro",
            "criado": day,
            "atualizado": day,
            "autor": f"Discord #{sanitize(channel_label)}",
            "url": "",
            "capturado": day,
            "tags": areas,
            "parte-de": [f"[[{MOC_NAME}]]"],
        }

        lines = [
            f"# {name}",
            "",
            "> ⚠️ Camada imutável. Agente lê, nunca reescreve. Entendimento novo vira",
            "> página em `20_WIKI/`, não edição aqui.",
            "",
            "## Material",
            "",
            f"Captura automática de {len(notes)} link(s) postados no canal "
            f"`#{sanitize(channel_label)}` do Discord em {day}, na ordem em que "
            "apareceram. Gerado por `discord-link-brain`.",
            "",
        ]
        lines += self._render_fonte_entries(notes)
        lines += [FONTE_TRAILER, "", "<!-- confie nos backlinks -->", ""]

        self._write(path, frontmatter.compose(meta, "\n".join(lines)))
        return WriteResult(path, "criado", f"{len(notes)} links")

    # ------------------------------------------------------------- 40_DIARIO

    @staticmethod
    def _render_fonte_entries(notes: list[LinkNote]) -> list[str]:
        lines: list[str] = []
        for note in notes:
            hit = note.hit
            lines += [f"### {hit.hhmm} — {hit.author}", "", f"<{hit.url}>", ""]
            if hit.context:
                lines += [f"> {hit.context}", ""]
            if note.meta.title:
                lines += [f"Título da página: {note.meta.title}", ""]
            if note.meta.description:
                lines += [f"Descrição: {note.meta.description}", ""]
            lines += [f"Mensagem original: {hit.jump_url}", ""]
        return lines

    def _append_fonte(self, path: Path, notes: list[LinkNote]) -> WriteResult:
        """Acrescenta capturas novas do mesmo dia, sem tocar nas que já estão.

        As entradas entram antes do rodapé `## Páginas compiladas desta fonte`,
        mantendo a ordem cronológica do arquivo.
        """
        meta, body = frontmatter.split(path.read_text(encoding="utf-8", errors="replace"))

        # Idempotência: reescrever a mesma captura não pode duplicar entrada.
        ja_tem = set(re.findall(r"^<(https?://[^>]+)>$", body, re.MULTILINE))
        novas = [n for n in notes if n.hit.url not in ja_tem]
        if not novas:
            return WriteResult(path, "pulado", "fonte já tem estes links")
        notes = novas

        entradas = "\n".join(self._render_fonte_entries(notes)).rstrip() + "\n\n"

        if FONTE_TRAILER in body:
            corpo, _, rodape = body.partition(FONTE_TRAILER)
            novo_body = corpo.rstrip() + "\n\n" + entradas + FONTE_TRAILER + rodape
        else:
            novo_body = body.rstrip() + "\n\n" + entradas

        for area in sorted({n.area for n in notes}):
            frontmatter.add_to_list(meta, "tags", area)
        self._write(path, frontmatter.compose(meta, novo_body))
        return WriteResult(path, "atualizado", f"+{len(notes)} links")

    def _append_diario(
        self, path: Path, day: str, notes: list[LinkNote], header: str
    ) -> WriteResult:
        """Acrescenta um lote posterior do mesmo dia na seção já existente.

        Entra antes do bloco de candidatos, para que ele continue sendo a última
        coisa da seção. As categorias podem aparecer mais de uma vez no dia —
        é o preço de não reinterpretar o que já foi escrito, e é barato perto de
        perder o link.
        """
        meta, body = frontmatter.split(path.read_text(encoding="utf-8", errors="replace"))

        # Recorta a seção do canal para inserir dentro dela, não depois.
        inicio = body.index(header)
        resto = body[inicio + len(header):]
        fim_rel = resto.find("\n## ")
        fim = len(body) if fim_rel == -1 else inicio + len(header) + fim_rel
        secao, depois = body[inicio:fim], body[fim:]

        # Idempotência: link já listado na seção não entra de novo.
        ja_tem = set(re.findall(r"\]\((https?://[^)\s]+)\)", secao))
        novas = [n for n in notes if n.hit.url not in ja_tem]
        if not novas:
            return WriteResult(path, "pulado", "seção do dia já tem estes links")
        bloco, candidatos = self._render_lote(day, novas)
        notes = novas

        if CANDIDATOS_HEADER in secao:
            antes, _, cauda = secao.partition(CANDIDATOS_HEADER)
            # Os candidatos do lote novo se juntam aos que já estavam listados,
            # senão o conceito deste lote nunca ganharia #acao/expandir.
            nova_secao = (
                antes.rstrip() + "\n\n" + bloco
                + self._merge_candidatos(cauda, candidatos)
            )
        else:
            nova_secao = secao.rstrip() + "\n\n" + bloco
            if candidatos:
                nova_secao += self._render_candidatos(sorted(set(candidatos)))

        for area in sorted({n.area for n in notes}):
            frontmatter.add_to_list(meta, "tags", area)
        meta["atualizado"] = day
        self._write(path, frontmatter.compose(meta, body[:inicio] + nova_secao + depois))
        return WriteResult(path, "atualizado", f"+{len(notes)} links")

    def _render_lote(self, day: str, notes: list[LinkNote]) -> str:
        """(bloco, candidatos) de um lote, sem o cabeçalho da seção."""
        por_cat: dict[str, list[LinkNote]] = {}
        for note in notes:
            por_cat.setdefault(note.categoria, []).append(note)

        linhas = [f"*Mais {len(notes)} link(s) capturados em {day}.*", ""]
        candidatos: list[str] = []
        for categoria in sorted(por_cat):
            linhas += [f"### {categoria}", ""]
            linhas += self._render_notes(por_cat[categoria], candidatos)
            linhas.append("")
        return "\n".join(linhas).rstrip() + "\n\n", candidatos

    @staticmethod
    def _render_candidatos(nomes: list[str]) -> str:
        return "\n".join([
            CANDIDATOS_HEADER,
            "",
            "Conceitos que apareceram e ainda não têm página em `20_WIKI/`. "
            "Compilar é trabalho curado — não crie stub vazio só para zerar a lista.",
            "",
            "- " + "\n- ".join(nomes),
            "",
            "#acao/expandir",
            "",
        ])

    def _merge_candidatos(self, cauda: str, novos: list[str]) -> str:
        """Refaz o bloco de candidatos com a união do que já havia e o que veio."""
        existentes = re.findall(r"^- (.+)$", cauda, re.MULTILINE)
        return self._render_candidatos(sorted(set(existentes) | set(novos)))

    def write_diario(
        self,
        channel_label: str,
        day: str,
        notes: list[LinkNote],
        force: bool = False,
    ) -> WriteResult:
        path = self.root / "40_DIARIO" / f"{day}.md"
        fonte = fonte_name(channel_label, day)
        section = self._render_section(channel_label, day, notes, fonte)
        header = f"## Links do Discord — #{sanitize(channel_label)}"

        if path.exists():
            text = path.read_text(encoding="utf-8")
            meta, body = frontmatter.split(text)
            if header in body:
                if not force:
                    # Lote posterior do mesmo dia: acrescenta dentro da seção
                    # que já existe. Pular aqui perderia o link em silêncio —
                    # e ele ainda sairia marcado como arquivado no Discord.
                    return self._append_diario(path, day, notes, header)
                body = self._drop_section(body, header)
            if not meta:
                meta = self._diario_meta(day, notes)
            meta["atualizado"] = day
            for area in sorted({n.area for n in notes}):
                frontmatter.add_to_list(meta, "tags", area)
            frontmatter.add_to_list(meta, "relacionado", f"[[{fonte}]]")
            new_body = body.rstrip() + "\n\n" + section
            self._write(path, frontmatter.compose(meta, new_body))
            return WriteResult(path, "atualizado", f"{len(notes)} links")

        meta = self._diario_meta(day, notes)
        frontmatter.add_to_list(meta, "relacionado", f"[[{fonte}]]")
        body = f"# {day}\n\n{section}"
        self._write(path, frontmatter.compose(meta, body))
        return WriteResult(path, "criado", f"{len(notes)} links")

    @staticmethod
    def _diario_meta(day: str, notes: list[LinkNote]) -> dict:
        return {
            "tipo": "diario",
            "status": "crescendo",
            "criado": day,
            "atualizado": day,
            "tags": sorted({n.area for n in notes}),
            "relacionado": [],
        }

    @staticmethod
    def _drop_section(body: str, header: str) -> str:
        """Remove a seção antiga (do header até o próximo `## ` no mesmo nível)."""
        pattern = re.compile(
            rf"^{re.escape(header)}\s*$.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL
        )
        return pattern.sub("", body).rstrip() + "\n"

    def _render_notes(self, notes: list[LinkNote], candidates: list[str]) -> list[str]:
        """Os bullets de uma categoria. `candidates` acumula conceito sem página."""
        lines: list[str] = []
        for note in notes:
            titulo = note.titulo.replace("[", "(").replace("]", ")")
            lines.append(f"- **[{titulo}]({note.hit.url})** — {note.resumo}")
            if note.porque_importa:
                lines.append(f"  *Por que importa:* {note.porque_importa}")

            if note.conceitos:
                rendered = []
                for conceito in note.conceitos:
                    text, exists = self.link_or_candidate(conceito)
                    rendered.append(text)
                    if not exists:
                        candidates.append(conceito)
                lines.append(f"  Conceitos: {', '.join(rendered)}")

            lines.append(f"  <sub>{note.hit.hhmm} · {note.hit.author} · #{note.area}</sub>")
        return lines

    def _render_section(
        self, channel_label: str, day: str, notes: list[LinkNote], fonte: str
    ) -> str:
        by_cat: dict[str, list[LinkNote]] = {}
        for note in notes:
            by_cat.setdefault(note.categoria, []).append(note)

        candidates: list[str] = []
        lines = [
            f"## Links do Discord — #{sanitize(channel_label)}",
            "",
            SECTION_MARK,
            "",
            f"{len(notes)} link(s) capturados em {day}. Fonte crua e cronológica: "
            f"[[{fonte}]].",
            "",
        ]

        for categoria in sorted(by_cat):
            lines += [f"### {categoria}", ""]
            lines += self._render_notes(by_cat[categoria], candidates)
            lines.append("")

        if candidates:
            unique = sorted(set(candidates))
            lines += [
                "### Candidatos a página no wiki",
                "",
                "Conceitos que apareceram e ainda não têm página em `20_WIKI/`. "
                "Compilar é trabalho curado — não crie stub vazio só para zerar a lista.",
                "",
                "- " + "\n- ".join(unique),
                "",
                "#acao/expandir",
                "",
            ]

        return "\n".join(lines)

    # ---------------------------------------------------------------- 30_MOC

    def ensure_moc(self) -> WriteResult:
        path = self.root / "30_MOC" / f"{MOC_NAME}.md"
        if path.exists():
            return WriteResult(path, "pulado", "MOC já existe")

        today = _today()
        meta = {
            "tipo": "moc",
            "status": "crescendo",
            "criado": today,
            "atualizado": today,
            "tags": ["area/pkm"],
            "parte-de": ["[[MOC - Índice]]"],
        }
        body = "\n".join(
            [
                f"# {MOC_NAME}",
                "",
                "> Onde mora o que foi jogado num canal do Discord durante o dia.",
                "> Cada captura é uma fonte crua em `10_FONTES/`; o recorte já",
                "> classificado do dia fica na nota de `40_DIARIO/`.",
                "",
                "## Panorama",
                "",
                "O canal do Discord é captura de baixa fricção: o link entra sem",
                "processamento. Este MOC existe para que essa pilha vire trilha —",
                "dá para ver o que entrou em que dia e o que ainda não virou página",
                "no wiki.",
                "",
                "Pipeline: `discord-link-brain` (em `~/Developer/discord-link-brain`).",
                "",
                "## Capturas por dia",
                "",
                "<!-- linhas abaixo são acrescentadas pelo pipeline, mais nova primeiro -->",
                "",
                "## Lacunas",
                "",
                "- Conceitos marcados como candidatos nas notas de diário ainda não",
                "  viraram página em `20_WIKI/`.",
                "",
            ]
        )
        self._write(path, frontmatter.compose(meta, body))
        return WriteResult(path, "criado", "hub de navegação")

    def register_capture(
        self, channel_label: str, day: str, count: int
    ) -> WriteResult:
        path = self.root / "30_MOC" / f"{MOC_NAME}.md"
        if not path.exists():
            return WriteResult(path, "pulado", "MOC ausente")

        fonte = fonte_name(channel_label, day)
        text = path.read_text(encoding="utf-8")
        if f"[[{fonte}]]" in text:
            return WriteResult(path, "pulado", "captura já listada")

        entry = (
            f"- [[{fonte}]] — {count} link(s) de `#{sanitize(channel_label)}`; "
            f"recorte classificado em [[{day}]]."
        )
        meta, body = frontmatter.split(text)
        meta["atualizado"] = _today()
        marker = "<!-- linhas abaixo são acrescentadas pelo pipeline, mais nova primeiro -->"
        if marker in body:
            body = body.replace(marker, f"{marker}\n\n{entry}", 1)
        else:
            body = body.rstrip() + f"\n\n{entry}\n"
        self._write(path, frontmatter.compose(meta, body))
        return WriteResult(path, "atualizado", "captura listada no MOC")

    def register_moc_in_index(self) -> WriteResult:
        """Deixa o MOC novo achável a partir de MOC - Índice."""
        path = self.root / "30_MOC" / "MOC - Índice.md"
        if not path.exists():
            return WriteResult(path, "pulado", "índice ausente")

        text = path.read_text(encoding="utf-8")
        if f"[[{MOC_NAME}]]" in text:
            return WriteResult(path, "pulado", "já indexado")

        entry = (
            f"- [[{MOC_NAME}]] — links jogados no Discord durante o dia, "
            "capturados e classificados automaticamente."
        )
        meta, body = frontmatter.split(text)
        meta["atualizado"] = _today()
        match = re.search(r"^## MOCs de tema\s*$", body, re.MULTILINE)
        if not match:
            return WriteResult(path, "pulado", "seção 'MOCs de tema' não encontrada")

        # Entra no fim da lista de bullets já existente da seção.
        rest = body[match.end() :]
        next_section = re.search(r"^## ", rest, re.MULTILINE)
        block_end = match.end() + (next_section.start() if next_section else len(rest))
        block = body[match.end() : block_end].rstrip()
        body = body[: match.end()] + block + f"\n{entry}\n\n" + body[block_end:]
        self._write(path, frontmatter.compose(meta, body))
        return WriteResult(path, "atualizado", "MOC indexado")


def _today() -> str:
    from datetime import date

    return date.today().isoformat()
