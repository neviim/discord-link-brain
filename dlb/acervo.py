"""O acervo de `50_LINKS/` lido sob demanda, com memo por arquivo.

**Não existe índice.** O vault é a verdade, e `docs/DECISOES.md` já mediu SQLite
contra ler arquivos e recusou o banco por *correção*, não por performance: um
índice vira segunda fonte de verdade que divergir do Obsidian. A mesma decisão
aponta o passo certo antes dele — cache por `mtime` — e é este arquivo.

O painel é o primeiro processo do projeto que **vive**: a CLI abre, lê o acervo
uma vez e morre, enquanto o painel serve dezenas de requisições. Reler 77 notas a
cada clique funcionaria hoje e é justamente o tipo de coisa que só dói quando o
acervo cresceu e ninguém lembra por quê.

A chave do memo é `(mtime_ns, size)`. Só `mtime` erraria em duas situações reais:
gravação dentro do mesmo tick do relógio, e restauração de backup que preserva
mtime. Tamanho junto não fecha o buraco teórico (edição do mesmo tamanho no mesmo
nanossegundo), mas `mtime_ns` tem resolução de nanossegundo — e o custo de errar
aqui é uma tela desatualizada até o próximo reload, não dado perdido.

Editar uma nota no Obsidian e ver o painel mudar no reload é o teste que prova
que a invalidação funciona; ele está em `tests/test_acervo.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import corpus
from .corpus import LINKS_DIR, LinkEntry


@dataclass
class Estatisticas:
    """Contadores da última leitura — o painel mostra, o teste confere."""

    total: int = 0
    lidos_do_disco: int = 0
    reusados_do_memo: int = 0
    ilegiveis: int = 0
    esquecidos: int = 0


@dataclass
class Acervo:
    """Leitor com memória. Um por processo; não é thread-safe por si."""

    vault: Path
    _memo: dict[Path, tuple[tuple[int, int], LinkEntry | None]] = field(default_factory=dict)
    ultima: Estatisticas = field(default_factory=Estatisticas)

    @property
    def diretorio(self) -> Path:
        return Path(self.vault) / LINKS_DIR

    def links(self) -> list[LinkEntry]:
        """Todo o acervo, relendo do disco só o que mudou.

        A ordem é a mesma de `corpus.ler_links` — dia e depois nome do arquivo —
        para o painel e a busca nunca discordarem sobre "os últimos dez".
        """
        stats = Estatisticas()
        entradas: list[LinkEntry] = []
        vistos: set[Path] = set()

        for path in sorted(self.diretorio.glob("*.md")):
            vistos.add(path)
            try:
                st = path.stat()
            except OSError:
                # Nota apagada entre o glob e o stat. Não é erro: é o Obsidian
                # sendo usado enquanto o painel roda.
                continue
            chave = (st.st_mtime_ns, st.st_size)
            cacheado = self._memo.get(path)
            if cacheado is not None and cacheado[0] == chave:
                entry = cacheado[1]
                stats.reusados_do_memo += 1
            else:
                # `_ler_nota` é do pacote: reimplementar o parser aqui criaria
                # duas leituras da mesma nota que podem divergir.
                entry = corpus._ler_nota(path)
                self._memo[path] = (chave, entry)
                stats.lidos_do_disco += 1
            if entry is None:
                stats.ilegiveis += 1
            else:
                entradas.append(entry)

        for path in set(self._memo) - vistos:
            del self._memo[path]
            stats.esquecidos += 1

        entradas.sort(key=lambda e: (e.dia, e.path.stem))
        stats.total = len(entradas)
        self.ultima = stats
        return entradas

    def esquecer(self) -> None:
        """Joga o memo fora. Existe para teste e para o botão de recarregar."""
        self._memo.clear()


def resumo(entradas: list[LinkEntry]) -> dict:
    """Os números que a tela inicial mostra sobre o acervo.

    Fila de triagem é o número que o Dotcom pediu para acompanhar: se `inbox` só
    cresce, a triagem não está acontecendo — e isso é informação sobre o *hábito*,
    não sobre o pipeline.
    """
    por_triagem: dict[str, int] = {}
    por_categoria: dict[str, int] = {}
    por_dominio: dict[str, int] = {}
    for e in entradas:
        por_triagem[e.triagem or "sem"] = por_triagem.get(e.triagem or "sem", 0) + 1
        por_categoria[e.categoria or "outro"] = por_categoria.get(e.categoria or "outro", 0) + 1
        if e.dominio:
            por_dominio[e.dominio] = por_dominio.get(e.dominio, 0) + 1
    dias = sorted({e.dia for e in entradas if e.dia})
    return {
        "total": len(entradas),
        "primeiro_dia": dias[0] if dias else None,
        "ultimo_dia": dias[-1] if dias else None,
        "por_triagem": dict(sorted(por_triagem.items())),
        "por_categoria": dict(sorted(por_categoria.items(), key=lambda kv: (-kv[1], kv[0]))),
        "dominios_frequentes": dict(
            sorted(por_dominio.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        ),
        "sem_pagina": sorted({c for e in entradas for c in e.sem_pagina}),
    }


def filtrar(
    entradas: list[LinkEntry],
    termo: str = "",
    triagem: str = "",
    categoria: str = "",
    area: str = "",
) -> list[LinkEntry]:
    """Filtro da tela do acervo: substring sem acento-sensibilidade e três facetas.

    Busca literal, não ranking. Quem quer relevância usa o chat, que reusa
    `busca.py` — dois mecanismos de busca com regras diferentes na mesma tela
    seria pior que um só honesto sobre o que faz.
    """
    termo = (termo or "").strip().lower()
    saida = []
    for e in entradas:
        if triagem and (e.triagem or "sem") != triagem:
            continue
        if categoria and e.categoria != categoria:
            continue
        if area and e.area != area:
            continue
        if termo:
            alvo = " ".join(
                (e.titulo, e.resumo, e.url, e.dominio, e.autor, " ".join(e.conceitos))
            ).lower()
            if termo not in alvo:
                continue
        saida.append(e)
    return saida
