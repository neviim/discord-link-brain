"""O bot responde no canal: `?buscar <pergunta>`.

Não é slash command nativo, e a escolha é deliberada. Gateway exigiria uma
biblioteca de websocket (`discord.py` traz 6+ dependências transitivas para um
projeto cujo runtime é `requests` + `PyYAML`) e um processo que mantém conexão
com estado — o oposto do "roda e sai" que o `scheduler.sh` assume, e que torna o
healthcheck por heartbeat sem sentido. Endpoint de Interactions exigiria URL
pública, TLS, PyNaCl e uma **porta de entrada** na máquina que guarda o seu
`~/.claude` — hoje só há conexão de saída.

Polling reaproveita `iter_messages()` inteiro (paginação, 429, 5xx já resolvidos e
testados) e o mesmo idioma de idempotência que o projeto já usa e confia: a
reação do próprio bot como segunda barreira. O custo é latência de um ciclo — e a
chamada de modelo leva 30-60s de todo modo, então o polling não é o gargalo.

Aqui também mora o primeiro uso legítimo de `remove_reaction`, implementado desde
o começo e nunca chamado: o ⏳ vira 💬 quando a resposta sai.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .busca import BuscaError, buscar, formatar_para_discord
from .config import Config
from .corpus import ler_links
from .discord_client import DiscordClient, DiscordError, ja_marcada, partir_para_discord
from .state import Store


@dataclass
class Contagem:
    respondidas: int = 0
    recusadas: int = 0
    erros: list[str] = field(default_factory=list)


def _perguntas(cfg: Config, mensagens: list[dict], bot_id: str) -> list[dict]:
    """As mensagens que são pergunta para nós, e ainda não foram resolvidas.

    Quatro barreiras, nesta ordem, e nenhuma é redundante:
      1. autor é bot  → nunca respondemos bot, nem a nós mesmos
      2. autor é NÓS  → cinto além do suspensório: a checagem que mais importa
                        não pode depender de o Discord ter marcado a flag `bot`
      3. o prefixo    → nossas respostas nunca começam com `?buscar`
      4. a marca      → mesmo um reinício no meio do voo não responde duas vezes
    """
    prefixos = (cfg.responder_prefix.lower(), "?b")
    achadas = []
    for m in mensagens:
        autor = m.get("author") or {}
        if autor.get("bot"):
            continue
        if bot_id and str(autor.get("id")) == bot_id:
            continue
        conteudo = (m.get("content") or "").strip()
        baixo = conteudo.lower()
        if not any(baixo.startswith(p + " ") or baixo == p for p in prefixos):
            continue
        if ja_marcada(m, *cfg.marcas_responder):
            continue
        achadas.append(m)
    return achadas


def _pergunta_de(cfg: Config, mensagem: dict) -> str:
    conteudo = (mensagem.get("content") or "").strip()
    for prefixo in (cfg.responder_prefix, "?b"):
        if conteudo.lower().startswith(prefixo.lower()):
            conteudo = conteudo[len(prefixo):]
            break
    return conteudo.strip()[: cfg.responder_max_pergunta]


def _dentro_do_limite(state, agora: float, teto: int) -> bool:
    """Cada `?buscar` gasta uma chamada de modelo na assinatura de alguém, e
    quem dispara é qualquer pessoa com escrita no canal. O teto é por hora."""
    recentes = [t for t in (state.respostas or []) if agora - t < 3600]
    state.respostas = recentes
    return len(recentes) < teto


def _marcar(client: DiscordClient, canal: str, mid: str, emoji: str) -> None:
    try:
        client.add_reaction(canal, mid, emoji)
    except DiscordError:
        pass  # marca é sinal para humano; a falta dela não muda a corretude


def _desmarcar(client: DiscordClient, canal: str, mid: str, emoji: str) -> None:
    try:
        client.remove_reaction(canal, mid, emoji)
    except DiscordError:
        pass


def uma_volta(cfg: Config, client: DiscordClient, store: Store, log=print) -> Contagem:
    """Uma passada por todos os canais vigiados."""
    conta = Contagem()
    try:
        bot_id = str((client.me() or {}).get("id") or "")
    except DiscordError as exc:
        conta.erros.append(f"não consegui identificar o bot: {exc}")
        return conta

    acervo = None  # lido sob demanda: a maioria das voltas não tem pergunta

    for canal in cfg.responder_channel_ids:
        state = store.load(canal)
        try:
            mensagens = list(client.iter_messages(canal, after=state.last_message_id))
        except DiscordError as exc:
            conta.erros.append(f"[{canal}] {exc}")
            continue
        if not mensagens:
            continue

        # O cursor sobe para o fim do lote **mesmo sem pergunta nenhuma** — senão
        # cada volta relê as mesmas mensagens para sempre.
        ultimo = mensagens[-1]["id"]
        perguntas = _perguntas(cfg, mensagens, bot_id)

        for mensagem in perguntas:
            mid = mensagem["id"]
            pergunta = _pergunta_de(cfg, mensagem)
            if not pergunta:
                continue

            agora = time.time()
            if not _dentro_do_limite(state, agora, cfg.responder_max_por_hora):
                log(f"[{canal}] limite por hora atingido — recusando {mid}")
                _marcar(client, canal, mid, cfg.responder_erro_emoji)
                conta.recusadas += 1
                continue

            # ⏳ antes da chamada: em um ciclo a pessoa já vê que foi lida, mesmo
            # que a resposta leve 60s.
            _marcar(client, canal, mid, cfg.responder_pendente_emoji)

            if acervo is None:
                acervo = ler_links(cfg.vault_path)

            log(f"[{canal}] respondendo: {pergunta[:60]}")
            try:
                resposta = buscar(cfg, pergunta, acervo)
                texto = formatar_para_discord(resposta)
                # Marca só DEPOIS de postar. Se o POST explodir, a mensagem fica
                # sem 💬 e volta na próxima volta — mesma regra do sync com o vault.
                for i, pedaco in enumerate(partir_para_discord(texto)):
                    client.post_message(
                        canal, pedaco, responder_a=mid if i == 0 else None
                    )
            except (BuscaError, DiscordError) as exc:
                conta.erros.append(f"[{canal}] {mid}: {exc}")
                _desmarcar(client, canal, mid, cfg.responder_pendente_emoji)
                continue

            _marcar(client, canal, mid, cfg.responder_emoji)
            _desmarcar(client, canal, mid, cfg.responder_pendente_emoji)
            state.respostas = (state.respostas or []) + [agora]
            conta.respondidas += 1

        state.last_message_id = ultimo
        store.save(canal, state)

    return conta
