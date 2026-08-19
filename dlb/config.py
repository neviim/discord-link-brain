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


def _pares(bruto: str) -> dict[str, str]:
    """Lê `chave:valor,chave:valor` do ambiente, descartando par incompleto.

    Sem descartar, um `:` solto ou uma vírgula sobrando entra no mapa como
    `{"": ""}` e vira um canal fantasma sem nome.
    """
    mapa: dict[str, str] = {}
    for par in bruto.split(","):
        chave, _, valor = par.partition(":")
        chave, valor = chave.strip(), valor.strip()
        if chave and valor:
            mapa[chave] = valor
    return mapa


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    discord_token: str
    discord_token_type: str          # "bot" ou "user"
    channel_ids: list[str]
    channel_names: dict[str, str] = field(default_factory=dict)
    # canal → servidor. O `sync` descobre isto pela API; esta config existe para
    # o `migrar`, que é offline por desenho e só tem os arquivos do vault.
    guild_ids: dict[str, str] = field(default_factory=dict)

    vault_path: Path = Path.home() / "Documents/00_Obsidian-dotcom/dotcom"
    timezone: str = "America/Sao_Paulo"

    llm_backend: str = "claude-cli"  # claude-cli | anthropic | none
    llm_model: str = "claude-opus-5"
    claude_bin: str = "claude"

    # A síntese periódica roda em backend próprio: são assinaturas diferentes, e
    # o agendador já disputa a janela de uso da Claude com a classificação diária.
    sintese_backend: str = "codex"   # codex | claude-cli | anthropic
    sintese_model: str = ""          # vazio = padrão do backend
    codex_bin: str = "codex"
    sintese_timeout: float = 900.0

    enrich: bool = True
    enrich_timeout: float = 10.0

    # Uma nota atômica por link em `50_LINKS/`. Desligar mantém o pipeline antigo
    # (bullet rico no diário) sem tocar em código — ver docs/DECISOES.md.
    link_notes: bool = True
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

        names = _pares(os.environ.get("DISCORD_CHANNEL_NAMES", ""))
        guilds = _pares(os.environ.get("DISCORD_GUILD_IDS", ""))

        vault = Path(
            os.environ.get("OBSIDIAN_VAULT", str(cls.vault_path))
        ).expanduser()

        return cls(
            discord_token=token,
            discord_token_type=os.environ.get("DISCORD_TOKEN_TYPE", "bot"),
            channel_ids=channel_ids,
            channel_names=names,
            guild_ids=guilds,
            vault_path=vault,
            timezone=os.environ.get("TIMEZONE", cls.timezone),
            llm_backend=os.environ.get("LLM_BACKEND", cls.llm_backend),
            llm_model=os.environ.get("LLM_MODEL", cls.llm_model),
            claude_bin=os.environ.get("CLAUDE_BIN", cls.claude_bin),
            sintese_backend=os.environ.get("SINTESE_BACKEND", cls.sintese_backend),
            sintese_model=os.environ.get("SINTESE_MODEL", cls.sintese_model),
            codex_bin=os.environ.get("CODEX_BIN", cls.codex_bin),
            sintese_timeout=float(os.environ.get("SINTESE_TIMEOUT", "900")),
            enrich=os.environ.get("ENRICH_LINKS", "1") not in ("0", "false", "no"),
            enrich_timeout=float(os.environ.get("ENRICH_TIMEOUT", "10")),
            link_notes=os.environ.get("LINK_NOTES", "1") not in ("0", "false", "no"),
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
