"""Cursor de sincronização por canal — para o `sync` ser incremental."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ChannelState:
    last_message_id: str | None = None
    last_run: str | None = None
    channel_name: str | None = None

    # O backfill anda no sentido contrário do sync: da mensagem mais antiga do
    # canal para frente. Por isso tem cursor próprio — os dois se encontram no
    # meio, e a deduplicação por URL cuida da sobreposição.
    backfill_after: str | None = None
    backfill_done: bool = False

    # Só o `responder` usa: instantes das respostas dadas na última hora, para o
    # teto de chamadas de modelo. Vive no arquivo dele, não no do sync — ver Store.
    respostas: list[float] = field(default_factory=list)


class Store:
    """Cursores em disco, um arquivo por canal.

    `prefix` dá arquivo separado para cada consumidor. O `responder` roda num
    processo próprio, de 60 em 60 segundos, enquanto o `sync` roda de 6 em 6
    horas — e **nenhum arquivo pode ter dois escritores**. Guardar o cursor do
    responder como um campo a mais no estado do sync criaria um
    read-modify-write concorrente, com perda silenciosa de atualização.
    """

    def __init__(self, directory: Path, prefix: str = ""):
        self.directory = directory
        self.prefix = prefix

    def _path(self, channel_id: str) -> Path:
        return self.directory / f"{self.prefix}{channel_id}.json"

    def load(self, channel_id: str) -> ChannelState:
        path = self._path(channel_id)
        if not path.is_file():
            return ChannelState()
        try:
            return ChannelState(**json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            return ChannelState()

    def save(self, channel_id: str, state: ChannelState) -> None:
        """Escreve o cursor. Atômico: arquivo temporário e `os.replace`.

        `write_text` direto deixa uma janela em que o arquivo existe truncado. O
        `load` tolera JSON corrompido e devolve estado vazio, o que significaria
        **reprocessar o canal desde o começo** — barato para o sync, e um convite
        a responder tudo de novo para o responder. Com `os.replace` a troca é
        atômica no mesmo sistema de arquivos e essa janela não existe.
        """
        state.last_run = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.directory.mkdir(parents=True, exist_ok=True)
        destino = self._path(channel_id)
        temporario = destino.with_suffix(".json.tmp")
        temporario.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporario, destino)
