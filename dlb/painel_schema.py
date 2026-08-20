"""Uma tabela para as três perguntas da tela de configuração.

`CONFIG_SCHEMA` faz três trabalhos que costumam virar três fontes de verdade:

1. **Tipo** — o que é um valor válido para esta chave, e a mensagem de recusa.
2. **Formulário** — que controle renderizar (caixa, número, escolha, senha).
3. **Onde vale** — se mudar a chave tem efeito na próxima execução ou só depois de
   recriar o container. É a coluna que impede o botão de mentir.

Uma tabela só, e um teste de contrato afirmando que **toda chave de `.env.example`
está aqui exatamente uma vez, e vice-versa**. Esse par pega a classe de defeito que
já aconteceu neste repo — `DISCORD_POST_ENABLED` duplicado, com valores opostos — e
que validação de tipo não pegaria: os dois valores eram válidos.

A validação **não é coerção**. Quase nada aqui é "isto é um int": `DLB_SINTESE_DIA`
é 1–7 na convenção `date +%u`, `DISCORD_GUILD_IDS` é uma lista de pares
`canal:servidor`, `DLB_SYNC_AT` é grade de horário, `LLM_BACKEND` depende de existir
binário na máquina. É domínio, e domínio é o mesmo código com ou sem biblioteca de
validação — o que uma biblioteca pouparia são as ~30 linhas de `int()` e `bool()`,
ao preço de uma segunda fonte de verdade além desta tabela.

## A coluna "onde vale"

O commit `4ee85aa` documenta a assimetria, e ela é a razão de a tela existir com
uma faixa de aviso em vez de um botão só:

- `python` — lida por `Config.from_env()`, que relê o `.env` a cada execução. Um
  comando rodado **no host** pega o valor novo na próxima vez. Dentro do container,
  não: o ambiente dele foi fixado na criação.
- `agendador` — lida pelo `docker/scheduler.sh`, do ambiente do container. Só vale
  depois de `docker compose up -d`, que **recria** o container. `restart`
  reaproveita o antigo e não relê nada, sem erro nenhum.

Ou seja: toda chave precisa de `up` para valer no que roda sozinho. A distinção
importa para o texto ser honesto — `dlb doctor` no host pode mostrar o valor novo
enquanto o container usa o velho.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CAMINHO_EXEMPLO = Path(__file__).resolve().parent.parent / ".env.example"


class Invalido(ValueError):
    """Valor recusado. A mensagem é o que a tela mostra, então é em português."""


@dataclass
class Campo:
    chave: str
    grupo: str
    tipo: str                  # texto numero booleano escolha segredo lista pares hora emoji caminho
    rotulo: str
    onde_vale: str             # "python" | "agendador"
    ajuda: str = ""
    opcoes: tuple[str, ...] = ()
    minimo: float | None = None
    maximo: float | None = None
    obrigatorio: bool = False

    @property
    def segredo(self) -> bool:
        return self.tipo == "segredo"

    def validar(self, bruto: str) -> str:
        """Devolve o valor normalizado, ou levanta `Invalido` com o motivo.

        Vazio passa em quase tudo: no shell, vazio e ausente são a mesma coisa, e
        é assim que se volta ao padrão. As exceções são as marcadas
        `obrigatorio` — sem elas o pipeline não roda.
        """
        valor = (bruto or "").strip()
        if not valor:
            if self.obrigatorio:
                raise Invalido(f"{self.chave} não pode ficar vazio.")
            return ""
        return _VALIDADORES[self.tipo](self, valor)


def _v_texto(campo: Campo, valor: str) -> str:
    if "\n" in valor or "\r" in valor:
        raise Invalido(f"{campo.chave}: valor não pode ter quebra de linha.")
    return valor


def _v_numero(campo: Campo, valor: str) -> str:
    try:
        n = float(valor) if "." in valor else int(valor)
    except ValueError:
        raise Invalido(f"{campo.chave}: '{valor}' não é número.") from None
    if campo.minimo is not None and n < campo.minimo:
        raise Invalido(f"{campo.chave}: mínimo é {_num(campo.minimo)}, veio {valor}.")
    if campo.maximo is not None and n > campo.maximo:
        raise Invalido(f"{campo.chave}: máximo é {_num(campo.maximo)}, veio {valor}.")
    return valor


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def _v_booleano(campo: Campo, valor: str) -> str:
    """Só `0` e `1`.

    O shell do agendador compara com `= "1"` literalmente, então `true` liga
    nada. Aceitar `true` aqui e escrever `true` no `.env` produziria uma tela
    dizendo ligado e um agendador desligado.
    """
    if valor not in {"0", "1"}:
        raise Invalido(f"{campo.chave}: use 0 ou 1 (o agendador compara com \"1\" literal).")
    return valor


def _v_escolha(campo: Campo, valor: str) -> str:
    if valor not in campo.opcoes:
        raise Invalido(f"{campo.chave}: '{valor}' não está entre {', '.join(campo.opcoes)}.")
    return valor


def _v_lista(campo: Campo, valor: str) -> str:
    itens = [x.strip() for x in valor.split(",")]
    if any(not x for x in itens):
        raise Invalido(f"{campo.chave}: item vazio na lista (vírgula sobrando?).")
    return ",".join(itens)


def _v_pares(campo: Campo, valor: str) -> str:
    """`chave:valor,chave:valor`. Par incompleto é descartado em silêncio pelo
    `_pares` do `config.py` — então a tela recusa antes, em vez de o valor virar
    um canal fantasma sem nome."""
    for par in valor.split(","):
        chave, sep, dado = par.partition(":")
        if not sep or not chave.strip() or not dado.strip():
            raise Invalido(f"{campo.chave}: '{par.strip()}' não é um par id:valor.")
    return ",".join(p.strip() for p in valor.split(","))


def _v_hora(campo: Campo, valor: str) -> str:
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", valor)
    if not m:
        raise Invalido(f"{campo.chave}: use HH:MM em 24h (veio '{valor}').")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _v_emoji(campo: Campo, valor: str) -> str:
    if len(valor) > 8 or " " in valor:
        raise Invalido(f"{campo.chave}: um emoji só, sem espaço.")
    return valor


def _v_caminho(campo: Campo, valor: str) -> str:
    if valor.startswith("~"):
        return valor           # o `~` é expandido por quem lê; não é erro
    if not valor.startswith("/"):
        raise Invalido(f"{campo.chave}: use caminho absoluto ou começando com ~.")
    return valor


_VALIDADORES = {
    "texto": _v_texto,
    "numero": _v_numero,
    "booleano": _v_booleano,
    "escolha": _v_escolha,
    "segredo": _v_texto,
    "lista": _v_lista,
    "pares": _v_pares,
    "hora": _v_hora,
    "emoji": _v_emoji,
    "caminho": _v_caminho,
}


def _c(chave, grupo, tipo, rotulo, onde_vale, ajuda="", **kw) -> Campo:
    return Campo(chave=chave, grupo=grupo, tipo=tipo, rotulo=rotulo,
                 onde_vale=onde_vale, ajuda=ajuda, **kw)


CONFIG_SCHEMA: tuple[Campo, ...] = (
    # ---------------------------------------------------------------- Discord
    _c("DISCORD_TOKEN", "Discord", "segredo", "Token do bot", "python",
       "Nunca é exibido de volta. Deixar em branco mantém o valor atual.",
       obrigatorio=True),
    _c("DISCORD_TOKEN_TYPE", "Discord", "escolha", "Tipo do token", "python",
       "`bot` é o normal; `user` só para conta pessoal.", opcoes=("bot", "user")),
    _c("DISCORD_CHANNEL_IDS", "Discord", "lista", "Canais de captura", "python",
       "Ids separados por vírgula. Cada canal tem cursor próprio.", obrigatorio=True),
    _c("DISCORD_CHANNEL_NAMES", "Discord", "pares", "Apelido por canal", "python",
       "`id:nome`. Vira o nome do arquivo de captura."),
    _c("DISCORD_GUILD_IDS", "Discord", "pares", "Servidor por canal", "python",
       "`id_canal:id_servidor`. Só o `migrar` usa; o `sync` descobre pela API."),
    # --------------------------------------------------------------- Obsidian
    _c("OBSIDIAN_VAULT", "Obsidian", "caminho", "Caminho do vault", "python",
       "Vale para comando rodado no host, já na próxima execução. Dentro do container "
       "é sempre `/vault` (bind mount), então esta chave nunca aparece como pendente.",
       obrigatorio=True),
    _c("TIMEZONE", "Obsidian", "texto", "Fuso horário", "agendador",
       "Vira `TZ` dentro do container, e é o fuso em que a grade conta as horas."),
    # -------------------------------------------------------------------- LLM
    _c("LLM_BACKEND", "LLM", "escolha", "Backend da classificação", "python",
       "`claude-cli` usa o binário logado na máquina — sem chave de API.",
       opcoes=("claude-cli", "anthropic", "none")),
    _c("LLM_MODEL", "LLM", "texto", "Modelo", "python"),
    _c("CLAUDE_BIN", "LLM", "texto", "Binário do claude", "python",
       "Nome no PATH ou caminho absoluto."),
    _c("ANTHROPIC_API_KEY", "LLM", "segredo", "Chave da API Anthropic", "python",
       "Só necessária com backend `anthropic`. Com `claude-cli` o login do binário basta."),
    # ------------------------------------------------------- Síntese e busca
    _c("SINTESE_BACKEND", "Síntese", "escolha", "Backend da síntese", "python",
       "Separado do da classificação de propósito: prompts e limites diferentes.",
       opcoes=("codex", "claude-cli", "anthropic", "none")),
    _c("CODEX_BIN", "Síntese", "texto", "Binário do codex", "python"),
    _c("SINTESE_MODEL", "Síntese", "texto", "Modelo da síntese", "python",
       "Vazio = o padrão do backend escolhido."),
    _c("SINTESE_TIMEOUT", "Síntese", "numero", "Timeout do modelo (s)", "python",
       "É também o timeout efetivo do chat do painel.", minimo=30, maximo=7200),
    _c("BUSCA_BACKEND", "Síntese", "escolha", "Ranqueamento da busca", "python",
       "Sem embeddings: o corpus inteiro cabe num prompt.", opcoes=("lexico-llm",)),
    _c("BUSCA_MODEL_BACKEND", "Síntese", "escolha", "Modelo da busca", "python",
       opcoes=("codex", "claude-cli", "anthropic")),
    _c("BUSCA_MAX_CHARS", "Síntese", "numero", "Orçamento do prompt (caracteres)", "python",
       "`doctor` mostra quanto do orçamento o acervo já ocupa.", minimo=10000),
    # ------------------------------------------------------- Enriquecimento
    _c("ENRICH_LINKS", "Enriquecimento", "booleano", "Abrir as páginas", "python",
       "Desligado, a classificação usa só o seu comentário e o título."),
    _c("ENRICH_TIMEOUT", "Enriquecimento", "numero", "Timeout por página (s)", "python",
       minimo=1, maximo=120),
    _c("LINK_NOTES", "Enriquecimento", "booleano", "Nota atômica por link", "python",
       "Desligar mantém só o bullet no diário — o modo antigo."),
    # ----------------------------------------------------------- Marcação
    _c("MARK_PROCESSED", "Marcação", "booleano", "Reagir no Discord", "python",
       "A marca é o sinal visual de que o link já está no vault."),
    _c("PROCESSED_EMOJI", "Marcação", "emoji", "Emoji de capturado", "python"),
    _c("DUPLICATE_EMOJI", "Marcação", "emoji", "Emoji de já documentado", "python"),
    # -------------------------------------------------------- Agendamento
    _c("DLB_SYNC_EVERY_HOURS", "Agendamento", "numero", "Grade (horas)", "agendador",
       "Ancorada na meia-noite. Vence o horário fixo — esvazie para usar DLB_SYNC_AT.",
       minimo=1, maximo=168),
    _c("DLB_SYNC_AT", "Agendamento", "hora", "Horário fixo (HH:MM)", "agendador",
       "Só vale se a grade em horas estiver vazia."),
    _c("DLB_SYNC_INTERVAL", "Agendamento", "numero", "Intervalo corrido (s)", "agendador",
       "Sem ancoragem: conta do fim do ciclo anterior. Vence os outros dois.",
       minimo=60),
    _c("DLB_SYNC_ARGS", "Agendamento", "texto", "Argumentos extras do sync", "agendador",
       "Ex.: `--no-enrich`. Vai direto para a linha de comando."),
    _c("DLB_RUN_ON_START", "Agendamento", "booleano", "Rodar ao subir", "agendador"),
    _c("DLB_HEALTH_TOLERANCE", "Agendamento", "numero", "Tolerância do sync (s)", "agendador",
       "Sem sync bem-sucedido nesse tempo, o container fica `unhealthy` e o painel alerta.",
       minimo=60),
    _c("DLB_RESPONDER_HEALTH_TOLERANCE", "Agendamento", "numero",
       "Tolerância do responder (s)", "agendador", minimo=60),
    # ----------------------------------------------------------- Histórico
    _c("DLB_BACKFILL", "Histórico", "booleano", "Mastigar o passado", "agendador",
       "Uma fatia por ciclo, do começo do canal para frente."),
    _c("DLB_BACKFILL_DAYS", "Histórico", "numero", "Dias por fatia", "agendador",
       "Cada dia de histórico custa uma chamada de modelo.", minimo=1, maximo=30),
    _c("DLB_SINTESE", "Histórico", "booleano", "Síntese semanal", "agendador"),
    _c("DLB_SINTESE_DIA", "Histórico", "numero", "Dia da semana (1=seg … 7=dom)", "agendador",
       "Convenção do `date +%u`. Segunda porque a semana anterior já fechou.",
       minimo=1, maximo=7),
    _c("DLB_SINTESE_MES", "Histórico", "booleano", "Rollup mensal no dia 1", "agendador"),
    _c("DLB_RESSURGIR", "Histórico", "booleano", "Ressurgir links antigos", "agendador",
       "Posta no Discord — exige DISCORD_POST_ENABLED."),
    # ------------------------------------------------------- Ressurgimento
    _c("RESSURGIR_DIAS", "Ressurgimento", "numero", "Idade mínima (dias)", "python",
       minimo=1),
    _c("RESSURGIR_N", "Ressurgimento", "numero", "Quantos por vez", "python",
       minimo=1, maximo=20),
    _c("RESSURGIR_CARENCIA", "Ressurgimento", "numero", "Carência (dias)", "python",
       "Quanto tempo um link fica de fora depois de ressurgir.", minimo=1),
    _c("RESSURGIR_RESUMO_MAX", "Ressurgimento", "numero", "Tamanho do resumo", "python",
       minimo=50, maximo=2000),
    _c("RESSURGIR_CANAL", "Ressurgimento", "texto", "Canal onde postar", "python",
       "Vazio = o primeiro de DISCORD_CHANNEL_IDS."),
    # ---------------------------------------------------- Falar no Discord
    _c("DISCORD_POST_ENABLED", "Falar no Discord", "booleano", "Autorizar o bot a postar",
       "python",
       "Escrita no vault é corrigível reexecutando; mensagem que outro humano leu, não."),
    _c("RESPONDER_PREFIX", "Falar no Discord", "texto", "Prefixo da pergunta", "python"),
    _c("RESPONDER_INTERVALO", "Falar no Discord", "numero", "Intervalo da volta (s)", "python",
       minimo=10),
    _c("RESPONDER_MAX_POR_HORA", "Falar no Discord", "numero", "Respostas por hora", "python",
       minimo=1),
    _c("RESPONDER_MAX_PERGUNTA", "Falar no Discord", "numero", "Tamanho máximo da pergunta",
       "python", "O mesmo teto vale no chat do painel.", minimo=20, maximo=4000),
    _c("RESPONDER_CHANNEL_IDS", "Falar no Discord", "lista", "Canais onde responder", "python",
       "Vazio = os mesmos da captura."),
    _c("RESPONDER_EMOJI", "Falar no Discord", "emoji", "Emoji de respondido", "python",
       "Não pode colidir com os de marcação — o `doctor` falha alto se colidir."),
    _c("RESPONDER_PENDENTE_EMOJI", "Falar no Discord", "emoji", "Emoji de pendente", "python"),
    _c("RESPONDER_ERRO_EMOJI", "Falar no Discord", "emoji", "Emoji de erro", "python"),
    # --------------------------------------------------------------- Painel
    _c("PAINEL_CHAT_MAX_POR_HORA", "Painel", "numero", "Chamadas do chat por hora", "python",
       "O painel divide a janela de uso com a classificação diária.", minimo=1, maximo=200),
    _c("PAINEL_CHAT_ESPERA_S", "Painel", "numero", "Espera na fila (s)", "python",
       "Não é timeout do modelo — esse é o SINTESE_TIMEOUT.", minimo=5, maximo=900),
    _c("PAINEL_TOKEN", "Painel", "segredo", "Token de acesso ao painel", "python",
       "Vazio = sem autenticação, e o painel só sobe em 127.0.0.1. Preenchido, ele "
       "pede o token e guarda sessão em cookie. Vale ao reiniciar o painel."),
    # ---------------------------------------------------------------- Temas
    _c("TEMAS_LIMIAR", "Temas", "numero", "Links para 'tema maduro'", "python", minimo=2),
    _c("TEMAS_IGNORAR", "Temas", "lista", "Conceitos a ignorar", "python",
       "Somados à stoplist de plataformas."),
)

POR_CHAVE = {campo.chave: campo for campo in CONFIG_SCHEMA}

GRUPOS = tuple(dict.fromkeys(campo.grupo for campo in CONFIG_SCHEMA))


def chaves_do_exemplo(caminho: Path | None = None) -> set[str]:
    """As chaves **atribuídas** no `.env.example`."""
    texto = (caminho or CAMINHO_EXEMPLO).read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", texto, re.M))


def chaves_sugeridas(caminho: Path | None = None) -> set[str]:
    """As chaves que o exemplo mostra **comentadas** — opção documentada.

    `# DLB_SYNC_AT=03:30` é configuração que existe e funciona; estar comentada
    quer dizer "não é o padrão", não "não use". A tela oferece as duas famílias,
    e o teste de contrato cobra as duas — senão uma opção documentada ficaria
    invisível na interface, que é como ela deixa de existir na prática.
    """
    texto = (caminho or CAMINHO_EXEMPLO).read_text(encoding="utf-8")
    return set(re.findall(r"^#\s*([A-Z][A-Z0-9_]*)=", texto, re.M))


def chaves_configuraveis(caminho: Path | None = None) -> set[str]:
    return chaves_do_exemplo(caminho) | chaves_sugeridas(caminho)


def validar_tudo(valores: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Valida o formulário inteiro. Devolve (normalizados, erros).

    Valida tudo antes de escrever qualquer coisa: aplicar metade de um formulário
    deixaria o `.env` num estado que ninguém pediu.
    """
    limpos: dict[str, str] = {}
    erros: list[str] = []
    for chave, bruto in valores.items():
        campo = POR_CHAVE.get(chave)
        if campo is None:
            erros.append(f"{chave}: chave desconhecida — não está no .env.example.")
            continue
        try:
            limpos[chave] = campo.validar(bruto)
        except Invalido as exc:
            erros.append(str(exc))
    return limpos, erros
