"""Cliente mínimo da REST API do Discord — só o que precisamos: ler mensagens."""

from __future__ import annotations

import time
from typing import Iterator
from urllib.parse import quote

import requests

API = "https://discord.com/api/v10"
USER_AGENT = "discord-link-brain/0.1 (+https://github.com/neviim)"

# Discord usa snowflakes: os 42 bits mais altos são o timestamp em ms desde
# 2015-01-01. Isso deixa a gente pedir "mensagens depois de <data>" sem cursor.
DISCORD_EPOCH_MS = 1420070400000


def snowflake_for(timestamp_ms: int) -> str:
    return str((timestamp_ms - DISCORD_EPOCH_MS) << 22)


def ja_marcada(message: dict, *emojis: str) -> bool:
    """A mensagem já recebeu **do próprio bot** alguma destas marcas?

    O campo `me` do Discord é justamente 'esta reação inclui o usuário
    autenticado'. Reação do humano com o mesmo emoji não conta — senão você
    marcaria um link à mão e ele nunca seria arquivado.

    Aceita várias marcas porque qualquer uma delas significa a mesma coisa para
    o pipeline: esta mensagem já foi resolvida, não precisa ser reprocessada.
    """
    alvo = set(emojis)
    for reacao in message.get("reactions") or []:
        if reacao.get("me") and (reacao.get("emoji") or {}).get("name") in alvo:
            return True
    return False


class DiscordError(RuntimeError):
    pass


class DiscordClient:
    def __init__(self, auth_header: str, timeout: float = 20.0):
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": auth_header, "User-Agent": USER_AGENT}
        )
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None) -> list | dict:
        url = f"{API}{path}"
        for attempt in range(6):
            resp = self.session.get(url, params=params, timeout=self.timeout)

            if resp.status_code == 429:
                # Respeita o rate limit em vez de martelar a API.
                retry_after = float(resp.headers.get("Retry-After", "1"))
                try:
                    retry_after = float(resp.json().get("retry_after", retry_after))
                except Exception:
                    pass
                time.sleep(min(retry_after, 30) + 0.25)
                continue

            if resp.status_code in (401, 403):
                raise DiscordError(
                    f"{resp.status_code} em {path}: token inválido ou sem permissão "
                    "(o bot precisa de View Channel + Read Message History no canal)."
                )
            if resp.status_code == 404:
                raise DiscordError(f"404 em {path}: canal não encontrado.")
            if resp.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue

            resp.raise_for_status()
            return resp.json()

        raise DiscordError(f"Desisti após várias tentativas em {path}.")

    def channel_name(self, channel_id: str) -> str:
        data = self._get(f"/channels/{channel_id}")
        return data.get("name", channel_id) if isinstance(data, dict) else channel_id

    def remove_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        """Tira uma reação do bot. Silencioso se ela não estava lá."""
        caminho = (
            f"/channels/{channel_id}/messages/{message_id}"
            f"/reactions/{quote(emoji, safe='')}/@me"
        )
        resp = self.session.delete(f"{API}{caminho}", timeout=self.timeout)
        if resp.status_code in (204, 404):
            return
        if resp.status_code in (401, 403):
            raise DiscordError(f"{resp.status_code} ao remover reação: sem permissão.")
        resp.raise_for_status()

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        """Marca a mensagem com um emoji, em nome do bot.

        Levanta DiscordError em 403 (falta a permissão *Adicionar reações*) —
        quem chama decide se isso interrompe algo. Aqui não interrompe: a
        marcação é sinal para humano, o cursor é que garante idempotência.
        """
        caminho = (
            f"/channels/{channel_id}/messages/{message_id}"
            f"/reactions/{quote(emoji, safe='')}/@me"
        )
        for tentativa in range(4):
            resp = self.session.put(f"{API}{caminho}", timeout=self.timeout)
            if resp.status_code == 429:
                espera = float(resp.headers.get("Retry-After", "1"))
                time.sleep(min(espera, 30) + 0.25)
                continue
            if resp.status_code in (401, 403):
                raise DiscordError(
                    f"{resp.status_code} ao reagir: o bot precisa da permissão "
                    "'Adicionar reações' no canal."
                )
            if resp.status_code >= 500:
                time.sleep(1.5 * (tentativa + 1))
                continue
            resp.raise_for_status()
            return
        raise DiscordError("Desisti de reagir após várias tentativas.")

    def iter_messages(
        self,
        channel_id: str,
        after: str | None = None,
        limit_total: int | None = None,
    ) -> Iterator[dict]:
        """Percorre as mensagens do canal em ordem cronológica crescente.

        `after` é um snowflake: só vêm mensagens posteriores a ele. O Discord
        devolve no máximo 100 por vez, da mais nova para a mais velha — por isso
        invertemos cada lote e avançamos o cursor pelo maior id visto.

        Sem `after` não há como paginar para trás aqui: você recebe só as 100
        mensagens mais recentes e o loop encerra. Passe sempre um snowflake
        (`snowflake_for`) ou o cursor salvo em state.
        """
        cursor = after
        yielded = 0

        while True:
            params: dict[str, str | int] = {"limit": 100}
            if cursor:
                params["after"] = cursor

            batch = self._get(f"/channels/{channel_id}/messages", params)
            if not isinstance(batch, list) or not batch:
                return

            batch.sort(key=lambda m: int(m["id"]))
            for message in batch:
                yield message
                yielded += 1
                if limit_total is not None and yielded >= limit_total:
                    return

            cursor = batch[-1]["id"]
            if len(batch) < 100:
                return
