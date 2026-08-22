"""Escrita no vault Obsidian, respeitando 90_META/ESQUEMA.md.

Divisão de camadas que este projeto assume:

  10_FONTES/  captura crua e imutável do dia (todos os links, com autor e hora)
  50_LINKS/   uma nota atômica por link — o cartão de referência e a fila de triagem
  40_DIARIO/  o recorte do dia, apontando para as notas atômicas
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
from .extract import host_of
from .models import TRIAGEM_INICIAL, LinkNote

MOC_NAME = "MOC - Captura Discord"
FONTE_TRAILER = "## Páginas compiladas desta fonte"
SECTION_MARK = "<!-- discord-link-brain -->"
CANDIDATOS_HEADER = "### Candidatos a página no wiki"
LINKS_DIR = "50_LINKS"

# Fronteira contratual na nota de link: daqui para baixo é do humano, e nem
# `--force` reescreve. É o que torna a triagem segura.
MINHAS_NOTAS = "## Minhas notas"

# Nome de arquivo cabe folgado nos 255 bytes do sistema com este teto — o maior
# título real medido no vault tem 82 caracteres.
TITULO_MAX = 100

# Cerca da síntese: só o que está entre os dois é regenerado. Texto humano fora
# dela sobrevive a `--force`, mesmo princípio do SECTION_MARK no diário.
SINTESE_ABRE = "<!-- dlb:sintese"
SINTESE_FECHA = "<!-- /dlb:sintese -->"
SINTESE_SECAO_MOC = "## Sínteses de período"


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


def titulo_de_arquivo(titulo: str, dominio: str = "") -> str:
    """Título de página como nome de arquivo legível.

    Não é `sanitize()`: ela **apaga** `/` e `:`, e aí
    `cosmtrek/mindwalk` viraria `cosmtrekmindwalk`. Aqui o separador é trocado,
    não removido — o nome é o texto do wikilink e precisa continuar legível.
    Medido no vault: 10 dos 70 títulos têm `:`, `|` ou `?`, então este caminho é
    a regra, não a exceção.
    """
    nome = titulo.replace("/", "-").replace("\\", "-")
    nome = nome.replace(":", " -")                 # "CS25: X" -> "CS25 - X"
    nome = re.sub(r'[*?"<>|#\[\]]', "", nome)      # quebram wikilink ou o FS
    nome = re.sub(r"\s+", " ", nome).strip()
    if len(nome) > TITULO_MAX:                     # corta em fronteira de palavra
        nome = nome[:TITULO_MAX].rsplit(" ", 1)[0]
    # Ponto ou espaço no fim quebra no Windows, e o vault sincroniza.
    nome = nome.strip(" -.\u2013\u2014")
    return nome or sanitize(dominio) or "link"


class Vault:
    def __init__(self, root: Path, dry_run: bool = False):
        self.root = root
        self.dry_run = dry_run
        self._wiki_pages: dict[str, str] | None = None
        self._link_notes: dict[str, Path] | None = None

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
        """Mapa nome-normalizado → nome real das páginas de 20_WIKI/.

        Memoizado: `link_or_candidate` chama isto **por conceito**, e a migração
        são 70 notas × ~3 conceitos. O `Vault` vive uma execução e o `20_WIKI/`
        não muda durante ela, então um glob basta.
        """
        if self._wiki_pages is None:
            self._wiki_pages = {
                md.stem.casefold(): md.stem
                for md in (self.root / "20_WIKI").glob("*.md")
            }
        return self._wiki_pages

    def link_or_candidate(self, conceito: str) -> tuple[str, bool]:
        """Wikilink só para página que existe — link pendurado derruba o lint."""
        real = self.wiki_pages().get(conceito.casefold())
        if real:
            return f"[[{real}]]", True
        return f"{conceito} *(candidato)*", False


    # -------------------------------------------------------------- 50_LINKS

    def link_notes_by_url(self) -> dict[str, Path]:
        """Mapa URL canônica → caminho da nota, lido de `50_LINKS/`.

        Por regex na linha `url:` e não com `yaml.safe_load`: é barato, e
        sobrevive a uma nota cujo frontmatter alguém quebrou editando à mão.

        É este índice — e não o nome do arquivo — que dá a idempotência. Você
        pode renomear a nota no Obsidian que ela continua sendo encontrada.
        """
        if self._link_notes is not None:
            return self._link_notes
        achadas: dict[str, Path] = {}
        for path in sorted((self.root / LINKS_DIR).glob("*.md")):
            try:
                texto = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = re.search(r"^url:\s*(\S+)\s*$", texto, re.MULTILINE)
            if m:
                achadas.setdefault(m.group(1).strip("\"'"), path)
        self._link_notes = achadas
        return achadas

    def _nome_da_nota(self, note: LinkNote, dominio: str, ocupados: set[str]) -> str:
        """Nome de arquivo estável para uma nota de link.

        A data na frente dá ordenação cronológica e desambigua o mesmo título
        capturado em dias diferentes. Medido: zero colisões nos 70 links atuais,
        então o sufixo é rede de segurança.

        Quando dois URLs distintos rendem o mesmo nome no mesmo dia, o sufixo
        vem de um hash da URL — **não** de um contador `(2)`, que dependeria da
        ordem de processamento e daria nome diferente a cada execução.
        """
        base = f"{note.hit.day} {titulo_de_arquivo(note.titulo, dominio)}"
        if base not in ocupados:
            return base
        import hashlib

        curto = hashlib.sha1(note.hit.url.encode("utf-8")).hexdigest()[:4]
        return f"{base} ({curto})"

    def _meta_link(self, note: LinkNote, channel_label: str, dominio: str) -> dict:
        fonte = fonte_name(channel_label, note.hit.day)
        meta = {
            "tipo": "link",
            "status": "semente",
            "triagem": TRIAGEM_INICIAL,
            "criado": note.hit.day,
            "atualizado": note.hit.day,
            "autor": note.hit.author,
            "url": note.hit.url,
            "capturado": note.hit.day,
            "dominio": dominio,
            "categoria": note.categoria,
            # Sai vazia de propósito: aparece no painel de propriedades do
            # Obsidian e fica preenchível com um clique. Ausente != zero, e o
            # pipeline nunca escreve valor aqui.
            "nota": None,
            "tags": [note.area],
            # Aresta para cima incondicional. É ela que garante o invariante 2
            # (zero dead-ends) mesmo numa nota sem conceito e sem relação.
            "parte-de": [f"[[{MOC_NAME}]]"],
            "fonte": [f"[[{fonte}]]"],
        }
        # O título completo como alias, só quando o nome do arquivo é versão
        # encurtada dele — senão é ruído. É o que mantém o wikilink escrevível
        # de cabeça, que é o motivo pelo qual o ESQUEMA proíbe id numérico.
        nome = f"{note.hit.day} {titulo_de_arquivo(note.titulo, dominio)}"
        if note.titulo and note.titulo != nome[len(note.hit.day) + 1:]:
            meta["aliases"] = [note.titulo]

        relacionados = [f"[[{note.hit.day}]]"]
        for conceito in note.conceitos:
            texto, existe = self.link_or_candidate(conceito)
            if existe:
                relacionados.append(texto)
        meta["relacionado"] = relacionados
        return meta

    def _corpo_link(self, note: LinkNote, channel_label: str) -> str:
        hit = note.hit
        fonte = fonte_name(channel_label, hit.day)
        linhas = [f"# {note.titulo or hit.url}", "", SECTION_MARK, ""]

        if note.resumo:
            linhas += [f"> {note.resumo}", ""]
        linhas += [f"<{hit.url}>", ""]

        if note.porque_importa:
            linhas += ["## Por que importa", "", note.porque_importa, ""]

        # Só os candidatos entram aqui. Conceito que já tem página vive em
        # `relacionado` — manter nos dois lugares seria a mesma aresta em duas
        # cópias, que é o que o ESQUEMA proíbe.
        candidatos = [c for c in note.conceitos if not self.link_or_candidate(c)[1]]
        if candidatos:
            linhas += ["## Conceitos sem página", ""]
            linhas += [f"- {c}" for c in candidatos]
            linhas += ["", "#acao/expandir", ""]

        linhas += [
            "## Contexto da captura",
            "",
            f"Postado por **{hit.author}** às **{hit.hhmm}** de **{hit.day}** "
            f"no canal `#{sanitize(channel_label)}`.",
            "",
            f"Fonte crua e cronológica: [[{fonte}]].",
            "",
        ]
        if hit.jump_url:
            linhas += [f"Mensagem original: {hit.jump_url}", ""]
        if hit.context:
            linhas += [f"> {hit.context}", ""]

        linhas += [
            MINHAS_NOTAS,
            "",
            "<!-- Daqui para baixo é seu. O pipeline nunca reescreve nada abaixo "
            "desta linha, nem com --force. -->",
            "",
        ]
        return "\n".join(linhas)

    def write_link_note(
        self, channel_label: str, note: LinkNote, force: bool = False
    ) -> WriteResult:
        """Uma nota atômica para um link. Idempotente por URL."""
        dominio = host_of(note.hit.url)
        existente = self.link_notes_by_url().get(note.hit.url)

        if existente is not None and not force:
            return WriteResult(existente, "pulado", "link já tem nota")

        if existente is not None:
            return self._reescrever_link(existente, note, channel_label, dominio)

        ocupados = {p.stem for p in (self.root / LINKS_DIR).glob("*.md")}
        nome = self._nome_da_nota(note, dominio, ocupados)
        path = self.root / LINKS_DIR / f"{nome}.md"
        meta = self._meta_link(note, channel_label, dominio)
        self._write(path, frontmatter.compose(meta, self._corpo_link(note, channel_label)))
        if self._link_notes is not None:
            self._link_notes[note.hit.url] = path
        return WriteResult(path, "criado", dominio)

    def _reescrever_link(
        self, path: Path, note: LinkNote, channel_label: str, dominio: str
    ) -> WriteResult:
        """`--force` refaz o miolo gerado e preserva tudo que é do humano."""
        texto = path.read_text(encoding="utf-8", errors="replace")
        meta_antiga, corpo_antigo = frontmatter.split(texto)

        if MINHAS_NOTAS not in corpo_antigo:
            # Nota escrita à mão, sem a fronteira. Não há como distinguir o que é
            # gerado do que é seu, então não se toca.
            return WriteResult(path, "pulado", "nota sem a fronteira do pipeline")

        meta = self._meta_link(note, channel_label, dominio)
        # O que é do humano sobrevive: triagem, avaliação, maturidade e as
        # relações que você acrescentou à mão.
        for chave in ("triagem", "status", "nota"):
            if meta_antiga.get(chave) is not None:
                meta[chave] = meta_antiga[chave]
        meta["criado"] = meta_antiga.get("criado", meta["criado"])
        for valor in meta_antiga.get("relacionado") or []:
            frontmatter.add_to_list(meta, "relacionado", valor)
        for valor in meta_antiga.get("aliases") or []:
            frontmatter.add_to_list(meta, "aliases", valor)

        cauda = corpo_antigo.split(MINHAS_NOTAS, 1)[1]
        corpo = self._corpo_link(note, channel_label)
        corpo = corpo.split(MINHAS_NOTAS, 1)[0] + MINHAS_NOTAS + cauda
        self._write(path, frontmatter.compose(meta, corpo))
        return WriteResult(path, "atualizado", dominio)

    def write_link_notes(
        self, channel_label: str, notes: list[LinkNote], force: bool = False
    ) -> list[WriteResult]:
        return [self.write_link_note(channel_label, n, force) for n in notes]

    def link_note_names(self, notes: list[LinkNote]) -> dict[str, str]:
        """URL → nome da nota (sem `.md`), para o diário montar o wikilink."""
        indice = self.link_notes_by_url()
        return {n.hit.url: indice[n.hit.url].stem for n in notes if n.hit.url in indice}


    def contar_triagem(self) -> dict[str, int]:
        """Quantas notas de link em cada estado de triagem, na ordem do ciclo."""
        from .models import TRIAGENS

        contagem = {k: 0 for k in TRIAGENS}
        for path in (self.root / LINKS_DIR).glob("*.md"):
            try:
                texto = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = re.search(r"^triagem:\s*(\S+)\s*$", texto, re.MULTILINE)
            valor = m.group(1).strip("\"'") if m else "?"
            contagem[valor] = contagem.get(valor, 0) + 1
        return {k: v for k, v in contagem.items() if v}

    def diario_sections(self) -> list[tuple[str, Path, str]]:
        """(dia, caminho, texto da seção) de cada seção escrita pelo pipeline.

        Só o que tem o `SECTION_MARK`: prosa que você escreveu na daily fica
        invisível para quem chama isto.
        """
        achadas: list[tuple[str, Path, str]] = []
        for path in sorted((self.root / "40_DIARIO").glob("*.md")):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem):
                continue
            try:
                texto = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            _, body = frontmatter.split(texto)
            for m in re.finditer(r"^## Links do Discord — #.*$", body, re.MULTILINE):
                resto = body[m.end():]
                fim = resto.find("\n## ")
                secao = resto if fim == -1 else resto[:fim]
                if SECTION_MARK in secao:
                    achadas.append((path.stem, path, secao))
        return achadas

    def links_sem_nota(self) -> list[tuple[str, str]]:
        """(dia, url) de link que está no diário e não tem nota em `50_LINKS/`.

        É o número que o `doctor` mostra e o `migrar` conserta. Existe porque a
        escrita não é transacional: se o processo morre entre a fonte e a nota
        atômica, o dedup por `10_FONTES` nunca mais recria aquele link.
        """
        com_nota = self.link_notes_by_url()
        pendentes: list[tuple[str, str]] = []
        vistos: set[str] = set()
        for dia, _path, secao in self.diario_sections():
            for url in re.findall(r"\]\((https?://[^)\s]+)\)", secao):
                if url not in com_nota and url not in vistos:
                    vistos.add(url)
                    pendentes.append((dia, url))
        return pendentes


    # ------------------------------------------------------- síntese de período

    def sintese_path(self, rotulo: str) -> Path:
        """`2026-W34` → `40_DIARIO/Semanal 2026-W34.md`; `2026-08` → `Mensal …`."""
        nome = f"Semanal {rotulo}" if "W" in rotulo else f"Mensal {rotulo}"
        return self.root / "40_DIARIO" / f"{nome}.md"

    def sintese_hash(self, rotulo: str) -> str:
        """O hash gravado na cerca, ou "" se a nota não existe."""
        path = self.sintese_path(rotulo)
        if not path.is_file():
            return ""
        m = re.search(
            re.escape(SINTESE_ABRE) + r"[^>]*?hash=(\w+)",
            path.read_text(encoding="utf-8", errors="replace"),
        )
        return m.group(1) if m else ""

    def sintese_bloco(self, rotulo: str) -> str:
        """O miolo gerado de uma síntese, sem as cercas.

        É o que o rollup mensal consome: o mês se faz das semanas, não dos links
        crus — é isso que faz ler o mês valer mais que reler trinta dias.
        """
        path = self.sintese_path(rotulo)
        if not path.is_file():
            return ""
        texto = path.read_text(encoding="utf-8", errors="replace")
        i = texto.find(SINTESE_ABRE)
        j = texto.find(SINTESE_FECHA)
        if i == -1 or j <= i:
            return ""
        # Pula o resto da linha do marcador de abertura.
        i = texto.find("\n", i)
        return texto[i + 1 : j].strip() if i != -1 else ""

    def _render_sintese(self, rotulo: str, sintese, entradas: list) -> str:
        """O miolo. `sintese` é um `sintese.Sintese`; `entradas`, `LinkEntry`."""
        linhas: list[str] = []
        if sintese.panorama:
            linhas += [sintese.panorama, ""]

        if sintese.temas:
            linhas += ["### Temas", ""]
            for tema in sintese.temas:
                linhas.append(f"**{tema.nome}**")
                if tema.texto:
                    linhas += ["", tema.texto]
                citados = [entradas[i] for i in tema.links if 0 <= i < len(entradas)]
                if citados:
                    linhas.append("")
                    for e in citados:
                        linhas.append(f"- [[{e.nome}]] — {e.dominio} · {e.dia}")
                linhas.append("")

        if sintese.conexoes:
            linhas += ["### Conexões", ""]
            for conexao in sintese.conexoes:
                linhas.append(f"- {conexao.texto}")
                citados = [
                    entradas[i] for i in conexao.links if 0 <= i < len(entradas)
                ]
                if citados:
                    alvos = " · ".join(f"[[{e.nome}]]" for e in citados)
                    linhas.append(f"  {alvos}")
            linhas.append("")

        if sintese.perguntas:
            linhas += ["### Perguntas em aberto", ""]
            linhas += [f"- {p}" for p in sintese.perguntas]
            linhas += ["", "#acao/revisar", ""]

        return "\n".join(linhas).rstrip() + "\n"

    def write_sintese(
        self,
        rotulo: str,
        sintese,
        entradas: list,
        fontes: list[str] | None = None,
        relacionado: list[str] | None = None,
        fechada: bool = False,
        force: bool = False,
    ) -> WriteResult:
        """Escreve `Semanal AAAA-Www` ou `Mensal AAAA-MM` em `40_DIARIO/`.

        Mora ali porque a camada é "humano + agente" e serve para registro
        cronológico — e síntese de período é exatamente isso. `20_WIKI/` está
        fora (o pipeline não escreve lá, e não é um conceito); `30_MOC/` também
        (52 notas/ano diluiriam a camada de navegação).

        Só o miolo entre as cercas é regenerado: o que você escrever fora
        sobrevive a `--force`.
        """
        from .sintese import hash_fonte

        path = self.sintese_path(rotulo)
        digest = hash_fonte(entradas) if entradas else rotulo
        miolo = (
            f"{SINTESE_ABRE} hash={digest} n={len(entradas)} -->\n\n"
            + self._render_sintese(rotulo, sintese, entradas)
            + f"\n{SINTESE_FECHA}\n"
        )
        titulo = f"Semanal {rotulo}" if "W" in rotulo else f"Mensal {rotulo}"

        if path.exists():
            meta, corpo = frontmatter.split(
                path.read_text(encoding="utf-8", errors="replace")
            )
            i, j = corpo.find(SINTESE_ABRE), corpo.find(SINTESE_FECHA)
            if i != -1 and j > i:
                novo_corpo = corpo[:i] + miolo + corpo[j + len(SINTESE_FECHA) :].lstrip("\n")
            else:
                # Nota feita à mão: acrescenta a cerca, nunca sobrescreve.
                novo_corpo = corpo.rstrip() + "\n\n" + miolo
            meta = meta or self._meta_sintese(titulo, rotulo, fechada)
            meta["atualizado"] = _today()
            meta["status"] = "maduro" if fechada else "crescendo"
            for valor in relacionado or []:
                frontmatter.add_to_list(meta, "relacionado", valor)
            for valor in fontes or []:
                frontmatter.add_to_list(meta, "fonte", valor)
            self._write(path, frontmatter.compose(meta, novo_corpo))
            return WriteResult(path, "atualizado", f"{len(entradas)} links")

        meta = self._meta_sintese(titulo, rotulo, fechada)
        for valor in relacionado or []:
            frontmatter.add_to_list(meta, "relacionado", valor)
        for valor in fontes or []:
            frontmatter.add_to_list(meta, "fonte", valor)
        corpo = f"# {titulo}\n\n{miolo}\n## Minhas notas\n\n"
        self._write(path, frontmatter.compose(meta, corpo))
        return WriteResult(path, "criado", f"{len(entradas)} links")

    @staticmethod
    def _meta_sintese(titulo: str, rotulo: str, fechada: bool) -> dict:
        return {
            "tipo": "diario",
            # Semana em curso ainda vai receber link; fechada não muda mais.
            "status": "maduro" if fechada else "crescendo",
            "criado": _today(),
            "atualizado": _today(),
            "tags": ["area/pkm"],
            "parte-de": [f"[[{MOC_NAME}]]"],
            "relacionado": [],
            "fonte": [],
        }

    def register_sintese(self, rotulo: str) -> WriteResult:
        """Lista a síntese no MOC, para ela não nascer órfã (invariante 3)."""
        path = self.root / "30_MOC" / f"{MOC_NAME}.md"
        if not path.exists():
            return WriteResult(path, "pulado", "MOC ausente")

        titulo = f"Semanal {rotulo}" if "W" in rotulo else f"Mensal {rotulo}"
        texto = path.read_text(encoding="utf-8", errors="replace")
        if f"[[{titulo}]]" in texto:
            return WriteResult(path, "pulado", "síntese já listada")

        meta, body = frontmatter.split(texto)
        meta["atualizado"] = _today()
        entry = f"- [[{titulo}]]"
        if SINTESE_SECAO_MOC in body:
            body = body.replace(
                SINTESE_SECAO_MOC, f"{SINTESE_SECAO_MOC}\n\n{entry}", 1
            )
        else:
            marcador = "## Capturas por dia"
            secao = f"{SINTESE_SECAO_MOC}\n\n{entry}\n\n{marcador}"
            body = (
                body.replace(marcador, secao, 1)
                if marcador in body
                else body.rstrip() + f"\n\n{SINTESE_SECAO_MOC}\n\n{entry}\n"
            )
        self._write(path, frontmatter.compose(meta, body))
        return WriteResult(path, "atualizado", "síntese listada no MOC")

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
        bloco, candidatos = self._render_lote(day, novas, self.link_note_names(novas))
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

    def _render_lote(
        self, day: str, notes: list[LinkNote], nomes: dict[str, str] | None = None
    ) -> str:
        """(bloco, candidatos) de um lote, sem o cabeçalho da seção."""
        por_cat: dict[str, list[LinkNote]] = {}
        for note in notes:
            por_cat.setdefault(note.categoria, []).append(note)

        linhas = [f"*Mais {len(notes)} link(s) capturados em {day}.*", ""]
        candidatos: list[str] = []
        for categoria in sorted(por_cat):
            linhas += [f"### {categoria}", ""]
            linhas += self._render_notes(por_cat[categoria], candidatos, nomes)
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
        # Os nomes vêm do índice de `50_LINKS/`, então as notas atômicas têm que
        # já estar escritas — é por isso que `cmd_sync` chama write_link_notes
        # antes daqui. Sem elas o dicionário sai vazio e o bullet cai na forma
        # rica, que é o comportamento correto para `LINK_NOTES=0`.
        nomes = self.link_note_names(notes)
        section = self._render_section(channel_label, day, notes, fonte, nomes)
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

    def _render_notes(
        self,
        notes: list[LinkNote],
        candidates: list[str],
        nomes: dict[str, str] | None = None,
    ) -> list[str]:
        """Os bullets de uma categoria. `candidates` acumula conceito sem página.

        Duas formas, e a escolha não é estética. Quando o link tem nota atômica
        em `50_LINKS/`, o bullet só aponta para ela: resumo, por-que-importa e
        conceitos moram lá, e repetir aqui seria a mesma prosa em duas cópias que
        divergem no primeiro `--force`. Quando não tem (`LINK_NOTES=0`), o bullet
        continua rico — senão esse conteúdo não existiria em lugar nenhum.

        Em **ambas** a URL sai dentro de `](...)`. Isso não é decoração: é a
        chave de idempotência que `_append_diario` usa para saber que este link
        já está na seção. Trocar por `<url>` ou por wikilink puro faz o segundo
        lote do dia ser escrito duas vezes ou sumir em silêncio — o bug que
        `TestLotePosteriorNoMesmoDia` existe para impedir.
        """
        nomes = nomes or {}
        lines: list[str] = []
        for note in notes:
            nome = nomes.get(note.hit.url)
            rendered = []
            for conceito in note.conceitos:
                text, exists = self.link_or_candidate(conceito)
                rendered.append(text)
                if not exists:
                    candidates.append(conceito)

            if nome:
                lines.append(f"- [[{nome}]] — {note.resumo}")
            else:
                titulo = note.titulo.replace("[", "(").replace("]", ")")
                lines.append(f"- **[{titulo}]({note.hit.url})** — {note.resumo}")
                if note.porque_importa:
                    lines.append(f"  *Por que importa:* {note.porque_importa}")
                if rendered:
                    lines.append(f"  Conceitos: {', '.join(rendered)}")

            rodape = f"{note.hit.hhmm} · {note.hit.author} · #{note.area}"
            if nome:
                rodape += f" · [abrir]({note.hit.url})"
            lines.append(f"  <sub>{rodape}</sub>")
        return lines

    def _render_section(
        self,
        channel_label: str,
        day: str,
        notes: list[LinkNote],
        fonte: str,
        nomes: dict[str, str] | None = None,
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
            lines += self._render_notes(by_cat[categoria], candidates, nomes)
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
                # Sem caminho: esta linha é escrita no vault de quem instalar, e
                # o caminho de quem escreveu o código não existe na máquina dele.
                # `dlb doctor` diz onde o pipeline está e como está configurado.
                "Pipeline: `discord-link-brain` — `python3 -m dlb doctor` mostra a",
                "configuração em uso.",
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
