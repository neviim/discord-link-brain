"""As ações que o painel pode executar no acervo e no pipeline.

Duas, e as duas são **propostas que o humano confirma**: triar uma nota e disparar
um comando do ciclo. O chat pode sugerir; nada acontece sem clique.

Por que proposta e não execução direta: a única coisa que o painel escreve no vault
é a triagem, que é **julgamento humano**. Um modelo decidindo que um link é
`descartado` apagaria a informação que o acervo existe para guardar — e apagaria em
silêncio, porque `descartado` é estado terminal.

## A escrita da triagem é cirúrgica, de propósito

O caminho óbvio seria `frontmatter.split` → mexer no dicionário →
`frontmatter.compose`. Funciona, e reescreve o frontmatter inteiro: ordem
normalizada, escalares recitados, listas reformatadas. Para uma mudança de uma
palavra, isso põe em risco tudo que foi editado à mão no Obsidian.

Então a troca é de **uma linha**: acha `triagem:` dentro do bloco de frontmatter e
substitui só ela. O resto do arquivo — inclusive tudo abaixo de
`## Minhas notas` — sai byte a byte igual. O teste compara o arquivo antes e depois
para provar que a única diferença é a linha esperada.

Disparar comando é `python3 -m dlb <cmd>` em subprocess, e não chamada de função.
É o mesmo caminho do container e do terminal, então passa pelo mesmo `flock` e pelo
mesmo journal — um só lugar decide quem pode rodar, e o painel não é exceção.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import journal
from .config import Config
from .corpus import LINKS_DIR
from .models import TRIAGENS

MINHAS_NOTAS = "## Minhas notas"

# Comandos que o painel pode disparar. Lista branca curta e explícita: `sync` e
# `backfill` alimentam o acervo, `sintese` e `temas` só leem. Fora daqui,
# nada — `responder` é laço infinito, `migrar` reescreve nota em massa, e
# `ressurgir` **posta no Discord**, que é a única ação irreversível do projeto.
COMANDOS = {
    "sync": "captura o que chegou desde o último cursor",
    "backfill": "mastiga uma fatia do histórico",
    "sintese": "gera a síntese do período, se houver material novo",
    "temas": "relatório de conceitos — não escreve nada",
}

TIMEOUT_PADRAO_S = 1800.0


class Recusado(RuntimeError):
    """Ação que não vai acontecer, com o motivo em português."""


@dataclass
class Triagem:
    nota: str
    de: str
    para: str
    caminho: Path


def _bloco_frontmatter(texto: str) -> tuple[int, int]:
    """Posições de início e fim do bloco `---`. `(-1, -1)` se não houver.

    Só o primeiro bloco conta: um `---` no meio do corpo é linha horizontal em
    markdown, e mexer nele seria editar o texto do humano.
    """
    if not texto.startswith("---\n"):
        return -1, -1
    fim = texto.find("\n---\n", 4)
    if fim == -1:
        return -1, -1
    return 4, fim + 1


def caminho_da_nota(cfg: Config, nome: str) -> Path:
    """Resolve o nome da nota dentro de `50_LINKS/`, recusando fuga de diretório.

    O nome vem de requisição HTTP. `../../.ssh/id_rsa` como "nota" não pode virar
    caminho — e a checagem é por `resolve()` e comparação de pai, não por filtro de
    substring, porque filtro de substring é jogo que não se ganha.
    """
    if not nome or nome != nome.strip() or "/" in nome or "\\" in nome:
        raise Recusado(f"nome de nota inválido: {nome!r}")
    diretorio = (Path(cfg.vault_path) / LINKS_DIR).resolve()
    alvo = (diretorio / f"{nome}.md").resolve()
    if alvo.parent != diretorio:
        raise Recusado("a nota tem de estar em 50_LINKS/")
    if not alvo.is_file():
        raise Recusado(f"não achei a nota {nome!r}")
    return alvo


def triar(cfg: Config, nome: str, para: str, hoje: str | None = None) -> Triagem:
    """Muda `triagem` de uma nota de link. Uma linha, nada mais.

    Também atualiza `atualizado`, que é o que o `ESQUEMA` do vault pede de quem
    mexe numa nota — e é o que faz o memo do painel reler só este arquivo.
    """
    if para not in TRIAGENS:
        raise Recusado(f"triagem '{para}' não existe. Use: {', '.join(TRIAGENS)}.")
    caminho = caminho_da_nota(cfg, nome)
    texto = caminho.read_text(encoding="utf-8")
    inicio, fim = _bloco_frontmatter(texto)
    if inicio == -1:
        raise Recusado("a nota não tem frontmatter — não sei onde escrever a triagem.")

    cabeca, meio, cauda = texto[:inicio], texto[inicio:fim], texto[fim:]
    if MINHAS_NOTAS in meio:
        # Só pode acontecer com arquivo corrompido, mas o custo de conferir é zero
        # e o custo de errar é escrever dentro do texto do humano.
        raise Recusado("frontmatter estranho: a fronteira do pipeline está dentro dele.")

    achado = re.search(r"^triagem:[ \t]*(.*)$", meio, re.M)
    if achado is None:
        raise Recusado("a nota não tem a propriedade `triagem` — não é nota de link.")
    de = achado.group(1).strip() or "(vazio)"
    if de == para:
        raise Recusado(f"{nome} já está em `{para}`.")

    novo_meio = meio[: achado.start()] + f"triagem: {para}" + meio[achado.end() :]
    novo_meio = re.sub(
        r"^atualizado:[ \t]*.*$",
        f"atualizado: {hoje or date.today().isoformat()}",
        novo_meio,
        count=1,
        flags=re.M,
    )

    temporario = caminho.with_suffix(".md.tmp")
    temporario.write_text(cabeca + novo_meio + cauda, encoding="utf-8")
    os.replace(temporario, caminho)
    return Triagem(nota=nome, de=de, para=para, caminho=caminho)


@dataclass
class Execucao:
    comando: str
    ok: bool
    codigo: int | None
    saida: str
    recusado: bool = False


def rodar(cfg: Config, comando: str, timeout: float = TIMEOUT_PADRAO_S) -> Execucao:
    """Dispara um comando do ciclo por subprocess. Nunca levanta.

    `python3 -m dlb <cmd>`, e não `cmd_sync(cfg, args)`: assim o job passa pelo
    `main()`, que é onde vivem o `flock` e o journal. Chamar a função direto daqui
    seria um segundo caminho de execução — e o segundo caminho é o que não tem
    proteção, sempre.

    Código 3 é recusa pelo lock, e recusa não é falha: é o sistema se protegendo
    porque outra execução está no ar.
    """
    if comando not in COMANDOS:
        return Execucao(comando, False, None, f"comando '{comando}' não é permitido pelo painel.")
    raiz = Path(cfg.env_path).parent
    try:
        saida = subprocess.run(
            [sys.executable, "-m", "dlb", comando],
            cwd=str(raiz),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Execucao(
            comando, False, None,
            f"o comando passou de {int(timeout)}s e foi abandonado. O journal tem o run.",
        )
    except OSError as exc:
        return Execucao(comando, False, None, str(exc))

    texto = ((saida.stdout or "") + (saida.stderr or "")).strip()
    return Execucao(
        comando=comando,
        ok=saida.returncode == 0,
        codigo=saida.returncode,
        saida="\n".join(texto.splitlines()[-40:]) or "(sem saída)",
        recusado=saida.returncode == 3,
    )


def propostas(pergunta: str, resposta_texto: str, usados: list[dict]) -> list[dict]:
    """O que o chat pode oferecer depois de responder. Só oferece.

    Heurística deliberadamente boba, e boba é a qualidade certa aqui: o que decide
    é o clique do humano, então esforço em adivinhar melhor só produziria sugestão
    mais convincente para uma decisão que não é da máquina.

    - notas citadas na resposta → oferecer marcar como `lido` ou `aplicado`;
    - pergunta que fala de captura recente → oferecer rodar `sync`.
    """
    saida: list[dict] = []
    for u in usados[:5]:
        saida.append(
            {
                "tipo": "triagem",
                "nota": u.get("nome"),
                "rotulo": f"marcar “{(u.get('titulo') or u.get('nome') or '')[:60]}” como",
                "opcoes": ["lido", "aplicado", "descartado"],
            }
        )
    gatilhos = ("hoje", "agora", "última", "ultima", "recente", "novo", "novos", "atualiz")
    if any(g in (pergunta or "").lower() for g in gatilhos):
        saida.append(
            {
                "tipo": "comando",
                "comando": "sync",
                "rotulo": "a pergunta é sobre o que chegou agora — rodar um sync antes?",
            }
        )
    return saida
