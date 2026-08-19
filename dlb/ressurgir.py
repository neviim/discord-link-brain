"""Todo dia, três links antigos de volta no canal — com o resumo.

Combate direto ao esquecimento, e o recurso mais barato do projeto: **nenhuma
chamada de modelo**. O resumo já existe na nota; ressurgir é só escolher e postar.

Lê `50_LINKS/` e não `10_FONTES/` de propósito: é o resumo que faz um link
reaparecido valer a leitura. URL crua de volta no canal seria só ruído.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .corpus import LinkEntry

ARQUIVO = "ressurgir.json"


@dataclass
class Historico:
    """Quais links já ressurgiram, e quando.

    Mora em `.state/` e não no vault, e isso **não** contradiz a decisão contra o
    índice em SQLite. Aquela recusou uma segunda autoridade para um fato que o
    vault já possui ("esta URL está arquivada?") — podia divergir, e a divergência
    dava resposta *errada*. "Que links eu já ressurgi" é um fato sobre o qual o
    vault não tem opinião nenhuma: não há de que divergir. É a mesma categoria dos
    dois cursores. E o pior caso de perder o arquivo é **um** link repetido.
    """

    ultima_execucao: str = ""
    postados: dict[str, str] = field(default_factory=dict)   # url → dia

    @classmethod
    def carregar(cls, directory: Path) -> "Historico":
        path = directory / ARQUIVO
        if not path.is_file():
            return cls()
        try:
            return cls(**json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            return cls()

    def salvar(self, directory: Path) -> None:
        import os

        directory.mkdir(parents=True, exist_ok=True)
        destino = directory / ARQUIVO
        temporario = destino.with_suffix(".json.tmp")
        temporario.write_text(
            json.dumps(
                {"ultima_execucao": self.ultima_execucao, "postados": self.postados},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporario, destino)

    def podar(self, hoje: str, carencia: int) -> None:
        """Tira do histórico o que já passou da carência, para o arquivo não
        crescer para sempre."""
        limite = (date.fromisoformat(hoje) - timedelta(days=carencia)).isoformat()
        self.postados = {u: d for u, d in self.postados.items() if d >= limite}


def elegiveis(entradas: list[LinkEntry], hoje: str, dias: int) -> list[LinkEntry]:
    """Links que vale a pena relembrar, capturados há pelo menos `dias`.

    Dois filtros, e nenhum é arbitrário:

    **`triagem: descartado` sai.** Você já disse que aquilo não serviu; trazer de
    volta contradiz a sua decisão. É o eixo de triagem fazendo o trabalho para o
    qual existe.

    **Sem `porque_importa` fica para o fim.** Medido no acervo: esse campo está
    vazio em exatamente os 16 links que o modelo não conseguiu descrever — página
    que não abriu, link de canal sem vídeo, reel sem legenda. Ressurgir "não foi
    possível dizer do que trata" é ruído, não memória. Mas é *preferência* e não
    corte: se sobrar só isso, melhor lembrar de algo do que de nada.
    """
    corte = (date.fromisoformat(hoje) - timedelta(days=dias)).isoformat()
    velhos = [
        e for e in entradas
        if e.dia and e.dia <= corte and e.triagem != "descartado"
    ]
    substanciais = [e for e in velhos if e.porque_importa]
    return substanciais or velhos


def escolher(
    entradas: list[LinkEntry], historico: Historico, hoje: str, quantos: int, semente: str
) -> list[LinkEntry]:
    """Escolhe `quantos` links, preferindo o que não ressurgiu recentemente.

    A semente inclui o dia, então rodar duas vezes no mesmo dia escolhe os mesmos
    links — idempotência de graça, sem depender do histórico. Mas o histórico
    continua, porque semente determinística sozinha não dá carência: o mesmo link
    voltaria ao acaso a cada tanto.
    """
    if not entradas:
        return []
    frescos = [e for e in entradas if e.url not in historico.postados]
    # Poço seco (tudo em carência): volta o mais antigo em vez de não postar nada.
    # Não postar é falha silenciosa, e este recurso existe justamente para lembrar.
    pool = frescos or sorted(entradas, key=lambda e: historico.postados.get(e.url, ""))
    sorteador = random.Random(f"{semente}:{hoje}")
    return sorteador.sample(pool, min(quantos, len(pool)))


def _encurtar(texto: str, teto: int) -> str:
    if len(texto) <= teto:
        return texto
    return texto[:teto].rsplit(" ", 1)[0].rstrip(" .,;:—-") + "…"


def formatar(escolhidos: list[LinkEntry], hoje: str, resumo_max: int) -> str:
    """A mensagem. `<url>` suprime a prévia do Discord — com 3 links a mensagem
    viraria uma parede de cartões, e assim não precisa da permissão Embed Links."""
    if not escolhidos:
        return ""
    linhas = [
        f"🕰️ **Ressurgimento** — {len(escolhidos)} coisa(s) que você salvou e "
        "provavelmente esqueceu",
        "",
    ]
    for i, e in enumerate(escolhidos, 1):
        idade = (date.fromisoformat(hoje) - date.fromisoformat(e.dia)).days
        linhas.append(f"**{i}. {e.titulo}**")
        linhas.append(f"<sub>{e.dia} · há {idade} dias · {e.categoria}</sub>")
        if e.resumo:
            linhas.append(_encurtar(e.resumo, resumo_max))
        linhas.append(f"<{e.url}>")
        linhas.append("")
    return "\n".join(linhas).rstrip()
