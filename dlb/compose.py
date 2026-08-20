"""O que o container está *realmente* rodando, contra o que o `.env` diz.

Existe porque de um fato documentado no commit `4ee85aa`: **`restart` não relê o
`.env`; `up` relê.** O `env_file` do compose é aplicado na criação do container,
não no restart do processo. A falha é silenciosa — nada dá erro, e o container
segue com o valor velho.

Consequência para o painel: mostrar o `.env` como se fosse a configuração ativa é
mentir em metade dos casos. Um painel que dissesse "sync de 3 em 3 horas" enquanto
o container roda de 6 em 6 seria pior que não ter tela de agenda, porque a pessoa
pararia de conferir.

Então a agenda mostra **as duas colunas** e marca a divergência. A autoridade é o
container quando ele está no ar; o arquivo é a intenção.

`docker inspect` é leitura, roda no host com o usuário do Dotcom, e **não** precisa
do socket montado em lugar nenhum — é o mesmo acesso que ele já tem no terminal.
Se o docker não estiver instalado, o container não existir, ou o comando demorar,
a resposta é `None` e a tela mostra só o arquivo, dizendo que mostrou só o arquivo.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Mapping

# Nome fixo no `docker-compose.yml` (`container_name: dlb-prod`), então não é
# preciso descobrir nada nem depender de rótulo do compose.
CONTAINER_PADRAO = "dlb-prod"

# `docker inspect` local costuma responder em dezenas de milissegundos. Cinco
# segundos é o teto para o caso patológico (daemon travando) — e a tela precisa
# responder mesmo nele.
TIMEOUT_S = 5.0


def disponivel() -> bool:
    return shutil.which("docker") is not None


def inspecionar(container: str = CONTAINER_PADRAO) -> dict | None:
    """Estado e ambiente do container, ou `None` se não deu para saber.

    Nunca levanta: o painel tem de abrir numa máquina sem docker, com o container
    parado, ou com o daemon indisponível.
    """
    if not disponivel():
        return None
    try:
        saida = subprocess.run(
            ["docker", "inspect", container],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if saida.returncode != 0:
        return None
    try:
        dados = json.loads(saida.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(dados, list) or not dados:
        return None

    bruto = dados[0]
    config = bruto.get("Config") or {}
    estado = bruto.get("State") or {}
    ambiente: dict[str, str] = {}
    for linha in config.get("Env") or []:
        chave, _, valor = str(linha).partition("=")
        ambiente[chave] = valor
    return {
        "container": container,
        "rodando": bool(estado.get("Running")),
        "status": estado.get("Status") or "?",
        "saude": ((estado.get("Health") or {}).get("Status")) or None,
        "desde": estado.get("StartedAt"),
        "criado": bruto.get("Created"),
        "imagem": config.get("Image"),
        "env": ambiente,
    }


# Chaves que trocam de nome no caminho: o compose define `TZ` no container a
# partir de `TIMEZONE` no `.env`. Sem este mapa, a tela reportaria divergência em
# toda instalação — e alarme falso permanente é como se ensina alguém a ignorar o
# painel.
ALIAS = {"TZ": "TIMEZONE"}


def divergencias(
    arquivo: Mapping[str, str],
    container: Mapping[str, str] | None,
    chaves: tuple[str, ...],
    alias: Mapping[str, str] | None = None,
) -> list[dict]:
    """Chaves em que o arquivo e o container discordam.

    Compara texto puro, sem normalizar: `6` e `6 ` **são** diferentes para o
    shell, e esconder isso seria repetir o erro que a função existe para expor.
    Vazio e ausente são tratados como a mesma coisa, porque para o `[ -z ]` do
    shell também são.
    """
    if container is None:
        return []
    alias = ALIAS if alias is None else alias
    fora: list[dict] = []
    for chave in chaves:
        no_arquivo = (arquivo.get(chave) or arquivo.get(alias.get(chave, chave)) or "").strip()
        no_container = (container.get(chave) or "").strip()
        if no_arquivo != no_container:
            fora.append(
                {
                    "chave": chave,
                    "arquivo": no_arquivo,
                    "container": no_container,
                }
            )
    return fora


def ambiente_ativo(
    arquivo: Mapping[str, str], inspecao: dict | None
) -> tuple[dict[str, str], str]:
    """O ambiente que vale para calcular a grade, e de onde ele veio.

    Container no ar manda. Container parado ou ausente: vale o arquivo, e a tela
    diz que é o arquivo — nunca as duas coisas ao mesmo tempo, para não haver
    dúvida sobre qual número está sendo mostrado.
    """
    if inspecao and inspecao.get("rodando") and inspecao.get("env"):
        return dict(inspecao["env"]), "container"
    return dict(arquivo), "arquivo"
