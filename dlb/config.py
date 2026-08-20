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

    # Busca no acervo. `lexico-llm` manda o corpus inteiro para o modelo — a 70
    # notas isso tem recall melhor que top-k, e sem dependência nova. O corte por
    # orçamento existe para quando o acervo passar de umas 300 notas; `doctor`
    # mostra o quanto do orçamento já está em uso.
    busca_backend: str = "lexico-llm"    # lexico-llm | (embeddings, quando doer)
    busca_model_backend: str = "codex"   # codex | claude-cli | anthropic
    busca_max_chars: int = 120_000

    # Falar no Discord é opt-in. Escrita no vault é idempotente e corrigível
    # reexecutando; mensagem que outro humano leu, não é.
    post_enabled: bool = False
    responder_prefix: str = "?buscar"
    responder_emoji: str = "💬"          # respondida
    responder_pendente_emoji: str = "⏳"  # vista, pensando
    responder_erro_emoji: str = "❌"      # desisti depois de N tentativas
    responder_intervalo: float = 60.0
    responder_max_pergunta: int = 500
    responder_max_por_hora: int = 10
    responder_channel_ids: list[str] = field(default_factory=list)
    # O healthcheck do container lê este arquivo; o laço o toca a cada volta
    # sem erro, mesmo quando não havia pergunta nenhuma.
    heartbeat_responder: str = ""

    ressurgir_dias: int = 30
    ressurgir_n: int = 3
    ressurgir_carencia: int = 180
    ressurgir_resumo_max: int = 220
    ressurgir_canal: str = ""

    temas_limiar: int = 8

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

    # Quanto tempo sem heartbeat até considerar parado. Os mesmos valores que o
    # healthcheck do container usa: o painel não pode ter uma opinião própria
    # sobre "está vivo" e mostrar verde enquanto o Docker mostra `unhealthy`.
    health_tolerance: int = 28800               # 8h — a grade padrão é de 6h
    responder_health_tolerance: int = 300       # 5 min — a volta é de 60 em 60 s

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
            busca_backend=os.environ.get("BUSCA_BACKEND", cls.busca_backend),
            busca_model_backend=os.environ.get(
                "BUSCA_MODEL_BACKEND", cls.busca_model_backend
            ),
            busca_max_chars=int(os.environ.get("BUSCA_MAX_CHARS", "120000")),
            post_enabled=os.environ.get("DISCORD_POST_ENABLED", "0")
            not in ("0", "false", "no"),
            responder_prefix=os.environ.get("RESPONDER_PREFIX", cls.responder_prefix),
            responder_emoji=os.environ.get("RESPONDER_EMOJI", cls.responder_emoji),
            responder_pendente_emoji=os.environ.get(
                "RESPONDER_PENDENTE_EMOJI", cls.responder_pendente_emoji
            ),
            responder_erro_emoji=os.environ.get(
                "RESPONDER_ERRO_EMOJI", cls.responder_erro_emoji
            ),
            responder_intervalo=float(os.environ.get("RESPONDER_INTERVALO", "60")),
            responder_max_pergunta=int(
                os.environ.get("RESPONDER_MAX_PERGUNTA", "500")
            ),
            responder_max_por_hora=int(
                os.environ.get("RESPONDER_MAX_POR_HORA", "10")
            ),
            responder_channel_ids=[
                c.strip()
                for c in os.environ.get("RESPONDER_CHANNEL_IDS", "").split(",")
                if c.strip()
            ] or channel_ids,
            ressurgir_dias=int(os.environ.get("RESSURGIR_DIAS", "30")),
            ressurgir_n=int(os.environ.get("RESSURGIR_N", "3")),
            ressurgir_carencia=int(os.environ.get("RESSURGIR_CARENCIA", "180")),
            ressurgir_resumo_max=int(os.environ.get("RESSURGIR_RESUMO_MAX", "220")),
            ressurgir_canal=os.environ.get("RESSURGIR_CANAL", ""),
            temas_limiar=int(os.environ.get("TEMAS_LIMIAR", "8")),
            health_tolerance=int(os.environ.get("DLB_HEALTH_TOLERANCE", "28800")),
            responder_health_tolerance=int(
                os.environ.get("DLB_RESPONDER_HEALTH_TOLERANCE", "300")
            ),
            heartbeat_responder=os.environ.get("DLB_HEARTBEAT", "")
            if os.environ.get("DLB_MODE") == "responder"
            else os.environ.get("DLB_HEARTBEAT_RESPONDER", ""),
            enrich=os.environ.get("ENRICH_LINKS", "1") not in ("0", "false", "no"),
            enrich_timeout=float(os.environ.get("ENRICH_TIMEOUT", "10")),
            link_notes=os.environ.get("LINK_NOTES", "1") not in ("0", "false", "no"),
            mark_processed=os.environ.get("MARK_PROCESSED", "1")
            not in ("0", "false", "no"),
            processed_emoji=os.environ.get("PROCESSED_EMOJI", cls.processed_emoji),
            duplicate_emoji=os.environ.get("DUPLICATE_EMOJI", cls.duplicate_emoji),
        )

    @property
    def marcas_responder(self) -> tuple[str, ...]:
        """Emojis que significam 'o responder já resolveu esta mensagem'.

        Vocabulário **separado** de `marcas`. Se os dois se misturassem, uma
        pergunta que contivesse um link seria pulada pelo `sync` para sempre — e
        uma mensagem legitimamente arquivada não poderia mais ser perguntada.
        """
        return (self.responder_emoji, self.responder_pendente_emoji,
                self.responder_erro_emoji)

    @property
    def marcas(self) -> tuple[str, ...]:
        """Todos os emojis que significam 'esta mensagem já foi resolvida'."""
        return (self.processed_emoji, self.duplicate_emoji)

    def validar_marcas(self) -> None:
        """As duas marcações não podem colidir. Um typo aqui é invisível."""
        comuns = set(self.marcas) & set(self.marcas_responder)
        if comuns:
            raise ConfigError(
                f"emoji usado nas duas marcações: {', '.join(sorted(comuns))}. "
                "PROCESSED_EMOJI/DUPLICATE_EMOJI marcam o que o sync arquivou; "
                "RESPONDER_* marcam o que o responder já respondeu. Misturar faz "
                "pergunta com link ser ignorada pelo sync para sempre."
            )

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
