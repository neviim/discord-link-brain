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

`docker inspect` é leitura, roda no host com o usuário que subiu o painel, e **não** precisa
do socket montado em lugar nenhum — é o mesmo acesso que ele já tem no terminal.
Se o docker não estiver instalado, o container não existir, ou o comando demorar,
a resposta é `None` e a tela mostra só o arquivo, dizendo que mostrou só o arquivo.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Mapping

from . import env_io

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

# Chaves que o compose **sobrescreve de propósito** dentro do container, então o
# arquivo e o container discordam para sempre e isso é o desenho funcionando:
#
#   OBSIDIAN_VAULT: /vault   (o vault entra por bind mount; o caminho do host não
#                             existe lá dentro)
#
# Compará-las produziria uma linha de "pendente" que nunca sai da tela — e alarme
# falso permanente é como se ensina alguém a ignorar o painel. Apareceu no primeiro
# teste contra o container de verdade.
#
# Para comandos rodados **no host** o valor do arquivo continua valendo, e vale já
# na próxima execução: é o que o texto de ajuda do campo diz.
SOBRESCRITAS_PELO_COMPOSE = {"OBSIDIAN_VAULT"}


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
        if chave in SOBRESCRITAS_PELO_COMPOSE:
            continue
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


# ------------------------------------------------------------------- aplicar


# `./dlb.sh prod up` roda `docker compose --profile prod up -d --build`. O wrapper
# existe porque o compose precisa de meia dúzia de caminhos do host exportados
# (vault, binário do claude, UID) e falha alto sem eles — chamar `docker compose`
# direto daqui seria reimplementar essa resolução, com uma segunda chance de errar.
COMANDO_APLICAR = ("./dlb.sh", "prod", "up")

# `up` recria o container e relê o `.env`; `restart` reaproveita o antigo e não relê
# nada, sem erro nenhum (commit `4ee85aa`). O painel nunca usa `restart` — um botão
# "salvar e reiniciar" não faria efeito e não avisaria.
TIMEOUT_APLICAR_S = 600.0


def aplicar(raiz: Path, timeout: float = TIMEOUT_APLICAR_S) -> dict:
    """Recria o container para o `.env` novo valer. Nunca levanta.

    Demora: o `up` do wrapper passa `--build`, então a primeira vez depois de
    mexer no código reconstrói a imagem. Com cache, são segundos.
    """
    raiz = Path(raiz)
    script = raiz / COMANDO_APLICAR[0]
    if not script.is_file():
        return {
            "ok": False,
            "codigo": None,
            "saida": f"não achei {script} — o painel precisa rodar na raiz do projeto.",
            "comando": " ".join(COMANDO_APLICAR),
        }
    if not disponivel():
        return {
            "ok": False,
            "codigo": None,
            "saida": "docker não está no PATH deste usuário.",
            "comando": " ".join(COMANDO_APLICAR),
        }
    try:
        saida = subprocess.run(
            list(COMANDO_APLICAR),
            cwd=str(raiz),
            env=env_io.ambiente_limpo(raiz / ".env"),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "codigo": None,
            "saida": f"o `up` passou de {int(timeout)}s e foi abandonado. "
            "O container pode ter subido de qualquer forma — confira o estado acima.",
            "comando": " ".join(COMANDO_APLICAR),
        }
    except OSError as exc:
        return {"ok": False, "codigo": None, "saida": str(exc), "comando": " ".join(COMANDO_APLICAR)}

    texto = ((saida.stdout or "") + (saida.stderr or "")).strip()
    return {
        "ok": saida.returncode == 0,
        "codigo": saida.returncode,
        # A saída do compose é verbosa e o que interessa é o fim.
        "saida": "\n".join(texto.splitlines()[-25:]),
        "comando": " ".join(COMANDO_APLICAR),
    }
