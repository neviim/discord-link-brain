"""Configuração — lida de variáveis de ambiente e de um arquivo .env opcional."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """Carrega KEY=VALUE de um .env sem sobrescrever o ambiente já definido."""
    path = path or PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    discord_token: str
    discord_token_type: str          # "bot" ou "user"
    channel_ids: list[str]
    channel_names: dict[str, str] = field(default_factory=dict)

    vault_path: Path = Path.home() / "Documents/00_Obsidian-dotcom/dotcom"
    timezone: str = "America/Sao_Paulo"

    llm_backend: str = "claude-cli"  # claude-cli | anthropic | none
    llm_model: str = "claude-opus-5"
    claude_bin: str = "claude"

    enrich: bool = True
    enrich_timeout: float = 10.0
    state_dir: Path = PROJECT_ROOT / ".state"

    # Marca no Discord o que já virou nota. Ver README: marcar, nunca apagar.
    # Dois emojis porque são dois estados diferentes para quem olha o canal:
    # "esta captura entrou agora" e "isto você já tinha".
    mark_processed: bool = True
    processed_emoji: str = "✅"      # virou nota nesta execução
    duplicate_emoji: str = "📚"      # já estava documentado no Obsidian

    @property
    def auth_header(self) -> str:
        if self.discord_token_type.lower() == "bot":
            return f"Bot {self.discord_token}"
        return self.discord_token

    def channel_label(self, channel_id: str) -> str:
        return self.channel_names.get(channel_id, channel_id)

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        token = os.environ.get("DISCORD_TOKEN", "").strip()
        if not token:
            raise ConfigError(
                "DISCORD_TOKEN não definido. Copie .env.example para .env e preencha."
            )

        raw_channels = os.environ.get("DISCORD_CHANNEL_IDS", "").strip()
        if not raw_channels:
            raise ConfigError(
                "DISCORD_CHANNEL_IDS não definido (ids separados por vírgula)."
            )
        channel_ids = [c.strip() for c in raw_channels.split(",") if c.strip()]

        names: dict[str, str] = {}
        raw_names = os.environ.get("DISCORD_CHANNEL_NAMES", "").strip()
        for pair in raw_names.split(","):
            if ":" in pair:
                cid, _, label = pair.partition(":")
                names[cid.strip()] = label.strip()

        vault = Path(
            os.environ.get("OBSIDIAN_VAULT", str(cls.vault_path))
        ).expanduser()

        return cls(
            discord_token=token,
            discord_token_type=os.environ.get("DISCORD_TOKEN_TYPE", "bot"),
            channel_ids=channel_ids,
            channel_names=names,
            vault_path=vault,
            timezone=os.environ.get("TIMEZONE", cls.timezone),
            llm_backend=os.environ.get("LLM_BACKEND", cls.llm_backend),
            llm_model=os.environ.get("LLM_MODEL", cls.llm_model),
            claude_bin=os.environ.get("CLAUDE_BIN", cls.claude_bin),
            enrich=os.environ.get("ENRICH_LINKS", "1") not in ("0", "false", "no"),
            enrich_timeout=float(os.environ.get("ENRICH_TIMEOUT", "10")),
            mark_processed=os.environ.get("MARK_PROCESSED", "1")
            not in ("0", "false", "no"),
            processed_emoji=os.environ.get("PROCESSED_EMOJI", cls.processed_emoji),
            duplicate_emoji=os.environ.get("DUPLICATE_EMOJI", cls.duplicate_emoji),
        )

    @property
    def marcas(self) -> tuple[str, ...]:
        """Todos os emojis que significam 'esta mensagem já foi resolvida'."""
        return (self.processed_emoji, self.duplicate_emoji)

    def validate_vault(self) -> None:
        missing = [
            sub
            for sub in ("10_FONTES", "20_WIKI", "30_MOC", "40_DIARIO", "90_META")
            if not (self.vault_path / sub).is_dir()
        ]
        if missing:
            raise ConfigError(
                f"Vault em {self.vault_path} não tem as pastas: {', '.join(missing)}"
            )
