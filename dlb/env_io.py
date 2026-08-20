"""Leitura do `.env` como dado, com as mesmas regras que o `load_dotenv` do projeto.

O painel precisa do arquivo, não do ambiente do processo: `Config.from_env` usa
`os.environ.setdefault`, então o que ele vê é uma mistura de arquivo e de tudo que
já estava exportado no shell. Para a tela de agenda dizer "o arquivo diz X", ela
tem de ler o arquivo.

Duas regras não óbvias, e as duas são **cópia deliberada** de `config.load_dotenv`:

1. **A primeira ocorrência ganha.** `setdefault` não sobrescreve, então uma chave
   repetida vale pela primeira aparição — e é exatamente o defeito que estava no
   `.env.example` com `DISCORD_POST_ENABLED` em `1` na linha 15 e `0` na 118. Um
   leitor que resolvesse "a última ganha" mostraria um valor que o sistema não usa.
2. **Aspas são removidas nas pontas, sem interpretar escape.** O shell do compose
   e o `load_dotenv` fazem isso; um parser mais esperto aqui divergiria do que
   realmente chega ao processo.

Este módulo é somente leitura de propósito. Escrever no `.env` — atômico, com
backup e preservando comentário — é a fase de configuração, e vem com a validação
que ela exige.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Linha:
    """Uma linha do arquivo, preservada como está. Base para a escrita futura."""

    numero: int
    bruta: str
    chave: str = ""
    valor: str = ""
    comentada: bool = False

    @property
    def e_atribuicao(self) -> bool:
        return bool(self.chave) and not self.comentada


def linhas(caminho: Path) -> list[Linha]:
    """Todas as linhas classificadas, na ordem do arquivo.

    Preserva comentário e linha em branco porque a tela de configuração vai
    reescrever o arquivo mantendo tudo isso — um `.env` que perde os comentários
    perde a documentação que ele mesmo carrega.
    """
    try:
        texto = Path(caminho).read_text(encoding="utf-8")
    except OSError:
        return []
    saida: list[Linha] = []
    for numero, bruta in enumerate(texto.splitlines(), start=1):
        limpa = bruta.strip()
        if not limpa:
            saida.append(Linha(numero, bruta))
            continue
        if limpa.startswith("#"):
            corpo = limpa.lstrip("#").strip()
            chave, sep, valor = corpo.partition("=")
            saida.append(
                Linha(
                    numero,
                    bruta,
                    chave=chave.strip() if sep and chave.strip().isupper() else "",
                    valor=_limpar(valor) if sep else "",
                    comentada=True,
                )
            )
            continue
        if "=" not in limpa:
            saida.append(Linha(numero, bruta))
            continue
        chave, _, valor = limpa.partition("=")
        saida.append(Linha(numero, bruta, chave=chave.strip(), valor=_limpar(valor)))
    return saida


def _limpar(valor: str) -> str:
    return valor.strip().strip('"').strip("'")


def ler(caminho: Path) -> dict[str, str]:
    """`chave -> valor` do arquivo, com a **primeira** ocorrência valendo.

    Igual ao `load_dotenv`: quem usa `setdefault` faz a primeira ganhar. Ver o
    docstring do módulo.
    """
    valores: dict[str, str] = {}
    for linha in linhas(caminho):
        if linha.e_atribuicao and linha.chave not in valores:
            valores[linha.chave] = linha.valor
    return valores


def duplicadas(caminho: Path) -> dict[str, list[int]]:
    """Chaves atribuídas mais de uma vez, com as linhas — para a tela avisar.

    Não é hipótese: aconteceu neste repo, e o valor que vale é o da primeira
    linha, que não é o que a segunda (com comentário explicativo) sugere.
    """
    onde: dict[str, list[int]] = {}
    for linha in linhas(caminho):
        if linha.e_atribuicao:
            onde.setdefault(linha.chave, []).append(linha.numero)
    return {chave: nums for chave, nums in onde.items() if len(nums) > 1}
