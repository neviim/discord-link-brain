"""Cursor de sincronização por canal — para o `sync` ser incremental."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
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


class Store:
    def __init__(self, directory: Path):
        self.directory = directory

    def _path(self, channel_id: str) -> Path:
        return self.directory / f"{channel_id}.json"

    def load(self, channel_id: str) -> ChannelState:
        path = self._path(channel_id)
        if not path.is_file():
            return ChannelState()
        try:
            return ChannelState(**json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            return ChannelState()

    def save(self, channel_id: str, state: ChannelState) -> None:
        state.last_run = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(channel_id).write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
