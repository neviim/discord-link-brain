"""Linha de comando do discord-link-brain."""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .classify import ClassifyError, classify
from .config import Config, ConfigError
from .discord_client import (
    DiscordClient,
    DiscordError,
    ja_marcada,
    partir_para_discord,
    snowflake_for,
)
from .enrich import fetch_meta
from .extract import day_of_message, extract_links, group_by_day
from .corpus import (
    da_semana,
    do_mes,
    ler_links,
    semana_anterior,
    semana_atual,
    semanas_do_mes,
)
from .busca import BuscaError, buscar, formatar_para_discord
from .migrar import migrar
from .responder import uma_volta
from .ressurgir import Historico, elegiveis, escolher
from .ressurgir import formatar as formatar_ressurgir
from .temas import PLATAFORMAS, levantar
from .temas import formatar as formatar_temas
from .sintese import SinteseError, gerar, gerar_mes, hash_fonte
from .models import LinkHit, LinkNote, PageMeta
from .state import ChannelState, Store
from .vault import Vault


def _log(message: str) -> None:
    print(message, flush=True)


def _hoje_local() -> str:
    """Hoje no fuso configurado — a virada do dia define a semana ISO."""
    from zoneinfo import ZoneInfo

    import os as _os

    tz = _os.environ.get("TIMEZONE", "America/Sao_Paulo")
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def _since_snowflake(days: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return snowflake_for(int(cutoff.timestamp() * 1000))


def _enrich_all(cfg: Config, hits: list[LinkHit], enabled: bool) -> dict[str, PageMeta]:
    metas: dict[str, PageMeta] = {}
    if not enabled:
        return metas
    for index, hit in enumerate(hits, 1):
        meta = fetch_meta(hit.url, timeout=cfg.enrich_timeout)
        if meta.kind == "youtube":
            estado = f"vídeo: {meta.title[:60]}" if meta.legivel else "vídeo ilegível"
        elif not meta.legivel:
            estado = "não deu para ler"
        elif meta.text:
            estado = f"{len(meta.text)} chars de texto"
        else:
            estado = "só metadados"
        _log(f"    [{index}/{len(hits)}] {hit.url} — {estado}")
        metas[hit.url] = meta
    return metas


def _marcar(
    cfg: Config,
    client: DiscordClient,
    channel_id: str,
    hits: list[LinkHit],
    emoji: str,
) -> None:
    """Reage nas mensagens resolvidas, para dar sinal visível no Discord.

    São dois emojis, dois significados: `processed_emoji` quando a captura
    entrou nesta execução, `duplicate_emoji` quando o link já estava
    documentado. Olhando o canal você distingue "isto é novo no vault" de
    "isto eu já tinha".

    Falhar aqui não invalida nada: a nota já está no vault e o cursor já
    impede reprocessamento. Por isso só avisa e segue.
    """
    if not cfg.mark_processed:
        return
    for message_id in dict.fromkeys(h.message_id for h in hits):
        try:
            client.add_reaction(channel_id, message_id, emoji)
        except DiscordError as exc:
            print(f"    aviso: não consegui marcar {message_id}: {exc}", file=sys.stderr)
            return  # falhou uma, vai falhar todas — não martela a API


def _guild_de(client: DiscordClient, cache: dict, channel_id: str) -> str | None:
    """Resolve e memoiza o servidor do canal. Falha não derruba a execução.

    `GET /channels/{id}/messages` não devolve `guild_id` por mensagem, e sem ele
    `extract_links` cai no default `@me` — que produz link de mensagem que não
    abre em canal de servidor.
    """
    if channel_id not in cache:
        try:
            cache[channel_id] = client.guild_id(channel_id)
        except DiscordError:
            cache[channel_id] = None
    return cache[channel_id]


def cmd_sync(cfg: Config, args: argparse.Namespace) -> int:
    cfg.validate_vault()
    client = DiscordClient(cfg.auth_header)
    store = Store(cfg.state_dir)
    vault = Vault(cfg.vault_path, dry_run=args.dry_run)
    guilds: dict[str, str | None] = {}

    if args.dry_run:
        _log("MODO SECO — nada será escrito no vault.\n")

    # MOC e índice só são tocados quando existe nota nova para registrar. Sem
    # link novo, a execução não encosta no vault.
    estado = {"moc_pronto": False, "arquivadas": None}
    guilds: dict[str, str | None] = {}

    def preparar_moc() -> None:
        if estado["moc_pronto"]:
            return
        _log("    " + str(vault.ensure_moc()))
        if not args.no_index:
            _log("    " + str(vault.register_moc_in_index()))
        estado["moc_pronto"] = True

    def ja_arquivadas() -> set[str]:
        if estado["arquivadas"] is None:
            estado["arquivadas"] = vault.archived_urls()
        return estado["arquivadas"]

    channels = args.channel or cfg.channel_ids
    exit_code = 0

    for channel_id in channels:
        state = store.load(channel_id)
        label = cfg.channel_names.get(channel_id) or state.channel_name

        try:
            if not label:
                label = client.channel_name(channel_id)
            after = state.last_message_id
            if args.since is not None:
                after = _since_snowflake(args.since)
            elif args.day:
                start = datetime.fromisoformat(args.day).replace(tzinfo=timezone.utc)
                after = snowflake_for(int(start.timestamp() * 1000))
            elif after is None:
                after = _since_snowflake(1)
                _log(f"[#{label}] sem cursor salvo — buscando as últimas 24h.")

            _log(f"\n[#{label}] buscando mensagens…")
            messages = list(client.iter_messages(channel_id, after=after))
        except DiscordError as exc:
            print(f"[#{label or channel_id}] ERRO: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        if not messages:
            _log(f"[#{label}] nada novo.")
            continue

        # O cursor avança para o fim do que foi *buscado*, não do que sobrou
        # depois dos filtros — senão mensagem descartada é rebuscada para sempre.
        ultimo_id = messages[-1]["id"]

        # Segunda barreira contra reprocessar: mesmo que o cursor se perca
        # (`.state` apagado, `--since` largo), o que já foi marcado fica de fora.
        # `--force` existe para reescrever o que já foi feito; se as marcas
        # filtrassem antes, ele não teria o que reescrever.
        if cfg.mark_processed and not (args.ignore_marks or args.force):
            antes = len(messages)
            messages = [m for m in messages if not ja_marcada(m, *cfg.marcas)]
            marcadas = antes - len(messages)
            if marcadas:
                _log(f"[#{label}] {marcadas} mensagem(ns) já marcada(s) — ignorando.")
            if not messages:
                _log(f"[#{label}] nada novo depois das marcas.")
                if not args.dry_run:
                    state.last_message_id, state.channel_name = ultimo_id, label
                    store.save(channel_id, state)
                continue

        hits = extract_links(
            messages,
            channel_id,
            guild_id=_guild_de(client, guilds, channel_id),
            tz=cfg.timezone,
            include_bots=args.include_bots,
        )
        if args.day:
            hits = [h for h in hits if h.day == args.day]

        # Link que já está numa captura de 10_FONTES não vira nota de novo,
        # mesmo repostado meses depois em outra mensagem.
        if hits and not args.force:
            arquivadas = ja_arquivadas()
            repetidos = [h for h in hits if h.url in arquivadas]
            hits = [h for h in hits if h.url not in arquivadas]
            if repetidos:
                _log(f"[#{label}] {len(repetidos)} link(s) já arquivado(s) antes — ignorando.")
                # Marca mesmo sem virar nota nova: o sentido da marca é você
                # olhar o Discord e saber que aquele link já está no Obsidian.
                if not args.dry_run:
                    _marcar(cfg, client, channel_id, repetidos, cfg.duplicate_emoji)

        _log(f"[#{label}] {len(messages)} mensagem(ns), {len(hits)} link(s) novo(s).")
        if not hits:
            if not args.dry_run:
                state.last_message_id, state.channel_name = ultimo_id, label
                store.save(channel_id, state)
            continue

        for day, day_hits in sorted(group_by_day(hits).items()):
            _log(f"  {day}: {len(day_hits)} link(s)")
            metas = _enrich_all(cfg, day_hits, cfg.enrich and not args.no_enrich)

            try:
                notes = classify(cfg, day_hits, metas)
            except ClassifyError as exc:
                print(f"  {day}: ERRO na classificação: {exc}", file=sys.stderr)
                exit_code = 1
                continue

            preparar_moc()
            _log("    " + str(vault.write_fonte(label, day, notes, force=args.force)))
            # Nota atômica antes do diário: o bullet do diário aponta para ela por
            # wikilink, e o nome do arquivo só existe depois de escrita.
            if cfg.link_notes:
                for r in vault.write_link_notes(label, notes, force=args.force):
                    _log("    " + str(r))
            _log("    " + str(vault.write_diario(label, day, notes, force=args.force)))
            _log("    " + str(vault.register_capture(label, day, len(notes))))

            # Só marca depois que a nota está no vault: se algo acima explodiu,
            # a mensagem continua sem marca e volta na próxima execução.
            if not args.dry_run:
                _marcar(cfg, client, channel_id, day_hits, cfg.processed_emoji)

            if args.dry_run:
                _preview(notes)

        if not args.dry_run:
            state.last_message_id, state.channel_name = ultimo_id, label
            store.save(channel_id, state)

    if not args.dry_run:
        _log("\nPronto. Rode `obsidian reload`, espere ~3s, e depois o lint.")
    return exit_code


def _preview(notes: list[LinkNote]) -> None:
    for note in notes:
        _log(f"      · [{note.categoria}] {note.titulo}")
        _log(f"        {note.resumo}")


def cmd_backfill(cfg: Config, args: argparse.Namespace) -> int:
    """Recupera o histórico em fatias: poucos dias por execução, do mais antigo.

    Separado do `sync` porque o objetivo é outro. O `sync` corre atrás do que
    chegou agora e precisa ser barato. O backfill mastiga o passado devagar,
    porque é 1 chamada de LLM por dia de histórico e a assinatura tem janela de
    uso. Dois cursores independentes: o sync anda do presente para frente, o
    backfill do começo do canal para frente, e eles se encontram no meio — a
    deduplicação por URL cuida da sobreposição.
    """
    cfg.validate_vault()
    client = DiscordClient(cfg.auth_header)
    store = Store(cfg.state_dir)
    vault = Vault(cfg.vault_path, dry_run=args.dry_run)

    if args.dry_run:
        _log("MODO SECO — nada será escrito no vault.\n")

    arquivadas = vault.archived_urls()
    moc_pronto = False
    exit_code = 0

    for channel_id in (args.channel or cfg.channel_ids):
        state = store.load(channel_id)
        label = cfg.channel_names.get(channel_id) or state.channel_name

        if state.backfill_done and not args.restart:
            _log(f"[#{label or channel_id}] histórico já concluído.")
            continue

        try:
            if not label:
                label = client.channel_name(channel_id)
            depois = state.backfill_after
            if args.restart:
                depois = None
            if depois is None:
                depois = (
                    snowflake_for(
                        int(
                            datetime.fromisoformat(args._from)
                            .replace(tzinfo=timezone.utc)
                            .timestamp()
                            * 1000
                        )
                    )
                    if args._from
                    else "0"  # "0" é o começo do Discord: pega o canal inteiro
                )
                _log(f"[#{label}] começando o histórico do início do canal.")

            _log(f"[#{label}] lendo até {args.batch} mensagem(ns) a partir do cursor…")
            messages = list(
                client.iter_messages(channel_id, after=depois, limit_total=args.batch)
            )
        except DiscordError as exc:
            print(f"[#{label or channel_id}] ERRO: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        if not messages:
            _log(f"[#{label}] histórico esgotado — nada mais para trás.")
            if not args.dry_run:
                state.backfill_done, state.channel_name = True, label
                store.save(channel_id, state)
            continue

        candidatas = messages
        if cfg.mark_processed and not args.ignore_marks:
            candidatas = [m for m in messages if not ja_marcada(m, *cfg.marcas)]

        hits = extract_links(
            candidatas,
            channel_id,
            guild_id=_guild_de(client, guilds, channel_id),
            tz=cfg.timezone,
            include_bots=args.include_bots,
        )
        repetidos = [h for h in hits if h.url in arquivadas]
        hits = [h for h in hits if h.url not in arquivadas]
        if repetidos and not args.dry_run:
            # Link que já está no vault leva a marca de "já documentado" — o
            # histórico inteiro acaba marcado, tenha gerado nota nova ou não.
            _marcar(cfg, client, channel_id, repetidos, cfg.duplicate_emoji)

        por_dia = group_by_day(hits)
        dias = sorted(por_dia)[: args.days]

        if not dias:
            # Trecho sem link novo: anda o cursor sem gastar LLM nenhum.
            state.backfill_after = messages[-1]["id"]
            state.channel_name = label
            _log(
                f"[#{label}] {len(messages)} mensagem(ns) sem link novo — "
                f"cursor avançou até {messages[-1]['timestamp'][:10]}."
            )
            if not args.dry_run:
                store.save(channel_id, state)
            continue

        _log(f"[#{label}] processando {len(dias)} dia(s): {', '.join(dias)}")

        ultimo_dia = dias[-1]
        for day in dias:
            day_hits = por_dia[day]
            _log(f"  {day}: {len(day_hits)} link(s)")
            metas = _enrich_all(cfg, day_hits, cfg.enrich and not args.no_enrich)

            try:
                notes = classify(cfg, day_hits, metas)
            except ClassifyError as exc:
                print(f"  {day}: ERRO na classificação: {exc}", file=sys.stderr)
                exit_code = 1
                # Não avança o cursor além do que deu certo: o dia volta na
                # próxima execução em vez de virar buraco no histórico.
                ultimo_dia = dias[dias.index(day) - 1] if dias.index(day) else None
                break

            if not moc_pronto:
                _log("    " + str(vault.ensure_moc()))
                if not args.no_index:
                    _log("    " + str(vault.register_moc_in_index()))
                moc_pronto = True

            _log("    " + str(vault.write_fonte(label, day, notes, force=False)))
            if cfg.link_notes:
                for r in vault.write_link_notes(label, notes, force=False):
                    _log("    " + str(r))
            _log("    " + str(vault.write_diario(label, day, notes, force=False)))
            _log("    " + str(vault.register_capture(label, day, len(notes))))
            arquivadas.update(h.url for h in day_hits)

            if not args.dry_run:
                _marcar(cfg, client, channel_id, day_hits, cfg.processed_emoji)

        if ultimo_dia is None:
            continue

        # O cursor vai até a última mensagem do último dia processado — não até
        # o fim do lote, senão os dias que não coubemos nesta fatia sumiriam.
        limite = max(
            (
                int(m["id"])
                for m in messages
                if day_of_message(m, cfg.timezone) <= ultimo_dia
            ),
            default=int(messages[-1]["id"]),
        )
        state.backfill_after, state.channel_name = str(limite), label
        if not args.dry_run:
            store.save(channel_id, state)
        _log(f"[#{label}] cursor do histórico agora em {ultimo_dia}.")

    return exit_code


def cmd_semana(cfg: Config, args: argparse.Namespace) -> int:
    """Junta as capturas dos últimos N dias num apanhado para leitura."""
    cfg.validate_vault()
    fontes_dir = cfg.vault_path / "10_FONTES"
    cutoff = (datetime.now() - timedelta(days=args.days)).date().isoformat()

    entries: list[tuple[str, Path]] = []
    for path in sorted(fontes_dir.glob("FONTE - Discord *.md")):
        day = path.stem[-10:]
        if day >= cutoff:
            entries.append((day, path))

    if not entries:
        _log(f"Nenhuma captura desde {cutoff}.")
        return 0

    diario_dir = cfg.vault_path / "40_DIARIO"
    chunks = [f"# Apanhado dos últimos {args.days} dias\n"]
    for day, path in entries:
        chunks.append(f"\n## {day} — [[{path.stem}]]\n")
        diario = diario_dir / f"{day}.md"
        if diario.is_file():
            text = diario.read_text(encoding="utf-8")
            marker = "## Links do Discord"
            if marker in text:
                chunks.append(text[text.index(marker) :].strip() + "\n")

    report = "\n".join(chunks)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        _log(f"Escrito em {args.out}")
    else:
        print(report)
    return 0


def cmd_migrar(cfg: Config, args: argparse.Namespace) -> int:
    """Cria a nota atômica dos links que já estão no vault. Sem LLM, sem rede."""
    cfg.validate_vault()
    vault = Vault(cfg.vault_path, dry_run=args.dry_run)

    rel = migrar(
        vault,
        timezone=cfg.timezone,
        dia=args.day,
        guilds=cfg.guild_ids,
        force=args.force,
        tocar_diario=not args.no_diario,
        limite=args.limite,
        log=_log,
    )

    _log(
        f"\n{rel.criados} criada(s) · {rel.atualizados} atualizada(s) · "
        f"{rel.pulados} pulada(s) · {rel.diarios_tocados} diário(s) referenciado(s)"
    )
    if rel.avisos:
        _log(f"\n{len(rel.avisos)} aviso(s):")
        for aviso in rel.avisos:
            print(f"  {aviso}", file=sys.stderr)
    if args.dry_run:
        _log("\n(--dry-run: nada foi escrito)")
    else:
        _log("\nRode `obsidian reload`, espere ~3s, e depois o lint.")
    # Aviso não é falha: um bullet editado à mão não invalida os outros 69.
    return 0


def _rotulo_semana(args) -> str:
    """Resolve `--semana` para um rótulo ISO. Padrão: a semana corrente."""
    hoje = _hoje_local()
    pedido = (args.semana or "atual").strip().lower()
    if pedido in ("atual", "corrente", "esta"):
        return semana_atual(hoje)
    if pedido in ("anterior", "passada", "ultima", "última"):
        return semana_anterior(hoje)
    if re.fullmatch(r"\d{4}-w\d{2}", pedido):
        return pedido.upper().replace("W", "W")
    raise ConfigError(
        f"--semana não entendido: {args.semana!r} (use atual, anterior ou AAAA-Www)"
    )


def cmd_sintese(cfg: Config, args: argparse.Namespace) -> int:
    """Uma chamada de LLM por período: o que a semana foi, não o que ela teve."""
    cfg.validate_vault()
    vault = Vault(cfg.vault_path, dry_run=args.dry_run)
    acervo = ler_links(cfg.vault_path)
    if not acervo:
        _log("Nenhuma nota em 50_LINKS/. Rode `dlb migrar` primeiro.")
        return 0

    if args.mes:
        return _sintese_mes(cfg, vault, acervo, args)

    rotulo = _rotulo_semana(args)
    entradas = da_semana(acervo, rotulo)
    if not entradas:
        _log(f"{rotulo}: nenhum link capturado nesta semana.")
        return 0

    # Semana em curso ainda vai receber link, então a nota nasce `crescendo` e
    # vira `maduro` quando a semana passa.
    fechada = rotulo < semana_atual(_hoje_local())

    # Hash do material: regenerar semana que não mudou gastaria uma chamada de
    # 30-60s para produzir o mesmo texto.
    digest = hash_fonte(entradas)
    if not args.force and vault.sintese_hash(rotulo) == digest:
        _log(f"{rotulo}: {len(entradas)} link(s), material inalterado — nada a fazer.")
        _log("       (use --force para regenerar de todo modo)")
        return 0

    _log(f"{rotulo}: {len(entradas)} link(s) · backend {cfg.sintese_backend}")
    try:
        resultado = gerar(cfg, rotulo, entradas)
    except SinteseError as exc:
        print(f"  ERRO na síntese: {exc}", file=sys.stderr)
        return 1

    _escrever(cfg, vault, rotulo, resultado, entradas, args)
    if args.dry_run:
        _preview_sintese(resultado)
    return 0


def _sintese_mes(cfg: Config, vault: Vault, acervo, args) -> int:
    mes = args.mes.strip()
    if not re.fullmatch(r"\d{4}-\d{2}", mes):
        raise ConfigError(f"--mes não entendido: {mes!r} (use AAAA-MM)")

    # O rollup se faz das sínteses semanais, não dos links crus: é isso que faz
    # ler o mês valer mais que reler trinta dias.
    blocos, faltando = {}, []
    for semana in semanas_do_mes(acervo, mes):
        bloco = vault.sintese_bloco(semana)
        if bloco:
            blocos[semana] = bloco
        else:
            faltando.append(semana)

    for semana in faltando:
        _log(f"       {semana} sem síntese — pulada. Rode `dlb sintese --semana {semana}`.")
    if not blocos:
        _log(f"{mes}: nenhuma síntese semanal para consolidar.")
        return 1 if faltando else 0

    _log(f"{mes}: {len(blocos)} semana(s) · backend {cfg.sintese_backend}")
    try:
        resultado = gerar_mes(cfg, mes, blocos)
    except SinteseError as exc:
        print(f"  ERRO no rollup: {exc}", file=sys.stderr)
        return 1

    entradas = do_mes(acervo, mes)
    relacionado = [f"[[Semanal {s}]]" for s in sorted(blocos)]
    _escrever(cfg, vault, mes, resultado, entradas, args, relacionado=relacionado)
    if args.dry_run:
        _preview_sintese(resultado)
    return 0


def _escrever(cfg, vault, rotulo, resultado, entradas, args, relacionado=None):
    if relacionado is None:
        # Só daily que existe de verdade: link não-resolvido derruba o invariante 4.
        dias = sorted({e.dia for e in entradas})
        relacionado = [
            f"[[{d}]]" for d in dias if (cfg.vault_path / "40_DIARIO" / f"{d}.md").is_file()
        ]
    fontes = sorted({f"[[{e.fonte}]]" for e in entradas if e.fonte})
    fechada = "W" not in rotulo or rotulo < semana_atual(_hoje_local())

    _log("    " + str(vault.ensure_moc()))
    _log("    " + str(vault.write_sintese(
        rotulo, resultado, entradas,
        fontes=fontes, relacionado=relacionado, fechada=fechada, force=args.force,
    )))
    _log("    " + str(vault.register_sintese(rotulo)))
    if not args.dry_run:
        _log("\nRode `obsidian reload`, espere ~3s, e depois o lint.")


def _preview_sintese(s) -> None:
    _log("\n--- prévia ---")
    _log(f"  {s.panorama}")
    for tema in s.temas:
        _log(f"  · {tema.nome} ({len(tema.links)} link(s))")
    for pergunta in s.perguntas:
        _log(f"  ? {pergunta}")


def _exigir_post(cfg: Config) -> None:
    """Falar é opt-in. Escrita no vault é idempotente e corrigível reexecutando;
    mensagem que outro humano leu, não é."""
    cfg.validar_marcas()
    if not cfg.post_enabled:
        raise ConfigError(
            "DISCORD_POST_ENABLED=0 — o bot não vai postar. Ligue no .env e "
            "confira a permissão 'Enviar mensagens' (inteiro 68672)."
        )


def cmd_buscar(cfg: Config, args: argparse.Namespace) -> int:
    """Pergunta ao acervo pela linha de comando. Não toca o Discord."""
    cfg.validate_vault()
    acervo = ler_links(cfg.vault_path)
    pergunta = " ".join(args.pergunta).strip()
    if not pergunta:
        _log("Nada a perguntar.")
        return 2
    try:
        resposta = buscar(cfg, pergunta, acervo)
    except BuscaError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    print(formatar_para_discord(resposta))
    _log(f"\n(confiança {resposta.confianca} · {resposta.considerados} item(ns) considerados)")
    return 0


def cmd_responder(cfg: Config, args: argparse.Namespace) -> int:
    """Vigia o canal e responde `?buscar`. Uma volta, ou laço."""
    _exigir_post(cfg)
    cfg.validate_vault()
    client = DiscordClient(cfg.auth_header)
    store = Store(cfg.state_dir, prefix="responder-")

    _log(
        f"responder · prefixo {cfg.responder_prefix!r} · marca {cfg.responder_emoji} · "
        f"canais {', '.join(cfg.responder_channel_ids)}"
    )
    if args.uma_vez:
        conta = uma_volta(cfg, client, store, log=_log)
        for erro in conta.erros:
            print(f"  {erro}", file=sys.stderr)
        _log(f"{conta.respondidas} respondida(s) · {conta.recusadas} recusada(s)")
        return 1 if conta.erros else 0

    _log(f"laço de {cfg.responder_intervalo:.0f}s. Ctrl-C para sair.")
    while True:
        try:
            conta = uma_volta(cfg, client, store, log=_log)
            for erro in conta.erros:
                print(f"  {erro}", file=sys.stderr)
            if cfg.heartbeat_responder:
                # Uma volta sem erro é sinal de vida, mesmo sem pergunta nenhuma.
                _bater(cfg.heartbeat_responder)
        except KeyboardInterrupt:
            _log("encerrando.")
            return 0
        except Exception as exc:                     # noqa: BLE001
            # O laço não morre por um erro isolado: é PID 1 de um container.
            print(f"  erro na volta: {exc}", file=sys.stderr)
        time.sleep(cfg.responder_intervalo)


def _bater(caminho: str) -> None:
    try:
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        Path(caminho).write_text(f"{int(time.time())}\n", encoding="utf-8")
    except OSError:
        pass


def cmd_ressurgir(cfg: Config, args: argparse.Namespace) -> int:
    """Posta N links antigos com o resumo. Nenhuma chamada de modelo."""
    cfg.validate_vault()
    hoje = _hoje_local()
    historico = Historico.carregar(cfg.state_dir)

    if historico.ultima_execucao == hoje and not args.force:
        _log(f"já ressurgiu hoje ({hoje}). Use --force para repetir.")
        return 0

    acervo = ler_links(cfg.vault_path)
    pool = elegiveis(acervo, hoje, args.dias if args.dias is not None else cfg.ressurgir_dias)
    if not pool:
        _log(f"nenhum link com {cfg.ressurgir_dias}+ dias no acervo.")
        return 0

    quantos = args.n if args.n is not None else cfg.ressurgir_n
    escolhidos = escolher(pool, historico, hoje, quantos, semente="dlb")
    if not escolhidos:
        _log("nada a ressurgir.")
        return 0

    mensagem = formatar_ressurgir(escolhidos, hoje, cfg.ressurgir_resumo_max)
    canal = cfg.ressurgir_canal or (cfg.channel_ids[0] if cfg.channel_ids else "")

    _log(f"{len(escolhidos)} link(s) de {len(pool)} elegível(is) · canal {canal}")
    for e in escolhidos:
        _log(f"    {e.dia} · {e.titulo[:70]}")

    if args.dry_run:
        print()
        print(mensagem)
        _log("\n(--dry-run: nada foi postado)")
        return 0

    _exigir_post(cfg)
    client = DiscordClient(cfg.auth_header)
    try:
        for pedaco in partir_para_discord(mensagem):
            client.post_message(canal, pedaco)
    except DiscordError as exc:
        print(f"ERRO ao postar: {exc}", file=sys.stderr)
        return 1

    for e in escolhidos:
        historico.postados[e.url] = hoje
    historico.ultima_execucao = hoje
    historico.podar(hoje, cfg.ressurgir_carencia)
    historico.salvar(cfg.state_dir)
    return 0


def cmd_temas(cfg: Config, args: argparse.Namespace) -> int:
    """Que conceitos já viraram tema. Relatório — não cria nada no vault."""
    cfg.validate_vault()
    acervo = ler_links(cfg.vault_path)
    if not acervo:
        _log("Nenhuma nota em 50_LINKS/. Rode `dlb migrar` primeiro.")
        return 0

    ignorar = set(PLATAFORMAS)
    extra = os.environ.get("TEMAS_IGNORAR", "").strip()
    if extra:
        ignorar |= {x.strip() for x in extra.split(",") if x.strip()}

    rel = levantar(
        cfg.vault_path,
        acervo,
        limiar=args.limiar if args.limiar is not None else cfg.temas_limiar,
        ignorar=ignorar,
        com_backlinks=args.backlinks,
    )
    saida = formatar_temas(rel, markdown=args.markdown)
    if args.out:
        Path(args.out).write_text(saida + "\n", encoding="utf-8")
        _log(f"Escrito em {args.out}")
    else:
        print(saida)
    return 0


def cmd_doctor(cfg: Config, args: argparse.Namespace) -> int:
    ok = True

    _log(f"vault           {cfg.vault_path}")
    try:
        cfg.validate_vault()
        _log("                estrutura OK (10_FONTES, 20_WIKI, 30_MOC, 40_DIARIO, 90_META)")
    except ConfigError as exc:
        _log(f"                FALHA: {exc}")
        ok = False

    _log(f"llm backend     {cfg.llm_backend} (modelo {cfg.llm_model})")
    _log(f"fuso            {cfg.timezone}")
    _log(f"enriquecimento  {'ligado' if cfg.enrich else 'desligado'}")

    if cfg.link_notes:
        vault = Vault(cfg.vault_path)
        indice = vault.link_notes_by_url()
        triagens = vault.contar_triagem()
        resumo = " · ".join(f"{v} {k}" for k, v in triagens.items()) or "nenhuma"
        _log(f"notas de link   ligado · 50_LINKS/ ({len(indice)} notas)")
        _log(f"triagem         {resumo}")
        # O número que diz se a camada está viva ou virou depósito: link no
        # diário que nunca ganhou nota atômica.
        pendentes = vault.links_sem_nota()
        if pendentes:
            _log(
                f"                {len(pendentes)} link(s) no diário sem nota "
                "atômica — rode `dlb migrar`"
            )
    else:
        _log("notas de link   desligado (LINK_NOTES=0)")

    # O tamanho do corpus contra o orçamento é o instrumento que diz quando a
    # busca léxica deixa de bastar — em vez de virar julgamento de gosto.
    acervo = ler_links(cfg.vault_path)
    if acervo:
        chars = sum(
            len(e.titulo) + len(e.resumo) + len(e.porque_importa) + 80 for e in acervo
        )
        pct = 100 * chars / max(cfg.busca_max_chars, 1)
        _log(
            f"busca           {cfg.busca_backend} → {cfg.busca_model_backend} · "
            f"corpus {chars} chars ({pct:.0f}% de BUSCA_MAX_CHARS)"
        )
        if pct >= 100:
            _log("                acima do orçamento — o ranking já está cortando;")
            _log("                é o momento de considerar embeddings.")

    _log(f"síntese         {cfg.sintese_backend}")

    try:
        cfg.validar_marcas()
        _log(
            f"responder       {cfg.responder_prefix!r} · {cfg.responder_emoji} "
            f"respondida · sem colisão com {' '.join(cfg.marcas)}"
        )
    except ConfigError as exc:
        _log(f"responder       FALHA: {exc}")
        ok = False

    hist = Historico.carregar(cfg.state_dir)
    pool = elegiveis(acervo, _hoje_local(), cfg.ressurgir_dias)
    _log(
        f"ressurgir       {len(pool)} elegível(is) · {len(hist.postados)} em carência · "
        f"última {hist.ultima_execucao or 'nunca'}"
    )
    _log(
        f"postar          {'ligado' if cfg.post_enabled else 'desligado'} "
        f"(permissão 68672: View + Read History + Reactions + Send Messages)"
    )
    _log(
        f"marcação        {cfg.processed_emoji} arquivado agora · "
        f"{cfg.duplicate_emoji} já documentado  (precisa de 'Adicionar reações')"
        if cfg.mark_processed
        else "marcação        desligada"
    )

    client = DiscordClient(cfg.auth_header)
    for channel_id in cfg.channel_ids:
        try:
            name = client.channel_name(channel_id)
            _log(f"canal {channel_id}  OK — #{name}")
        except DiscordError as exc:
            _log(f"canal {channel_id}  FALHA: {exc}")
            ok = False

    store = Store(cfg.state_dir)
    for channel_id in cfg.channel_ids:
        state = store.load(channel_id)
        _log(
            f"cursor {channel_id}  last_message_id={state.last_message_id} "
            f"last_run={state.last_run}"
        )

    if getattr(args, "testar_envio", False):
        # A única forma honesta de conferir 'Enviar mensagens' sem calcular
        # permissão efetiva a partir de guild + roles: postar e apagar. O bot
        # pode apagar a própria mensagem sem permissão extra.
        for channel_id in cfg.channel_ids:
            try:
                msg = client.post_message(channel_id, "teste do dlb — pode ignorar")
                _log(f"envio {channel_id}   OK — postou e vai apagar")
                if msg.get("id"):
                    client.delete_message(channel_id, msg["id"])
            except DiscordError as exc:
                _log(f"envio {channel_id}   FALHA: {exc}")
                ok = False

    _log("\nOK" if ok else "\nHá problemas acima.")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dlb",
        description="Lê links de canais do Discord, classifica e arquiva no vault Obsidian.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="captura, classifica e escreve no vault")
    sync.add_argument("--channel", action="append", help="id do canal (repetível)")
    sync.add_argument("--since", type=int, metavar="DIAS", help="ignora o cursor e volta N dias")
    sync.add_argument("--day", metavar="AAAA-MM-DD", help="processa apenas este dia")
    sync.add_argument("--dry-run", action="store_true", help="não escreve nada")
    sync.add_argument("--force", action="store_true", help="reescreve fonte/seção existente")
    sync.add_argument("--no-enrich", action="store_true", help="não abre as páginas")
    sync.add_argument("--include-bots", action="store_true", help="inclui links de bots")
    sync.add_argument("--no-index", action="store_true", help="não mexe em MOC - Índice")
    sync.add_argument(
        "--ignore-marks",
        action="store_true",
        help="reprocessa mesmo o que já está marcado com ✅ no Discord",
    )
    sync.set_defaults(func=cmd_sync)

    back = sub.add_parser(
        "backfill",
        help="recupera o histórico em fatias, do mais antigo para o mais novo",
    )
    back.add_argument("--channel", action="append", help="id do canal (repetível)")
    back.add_argument(
        "--days", type=int, default=2,
        help="quantos dias de histórico processar nesta execução (padrão 2)",
    )
    back.add_argument(
        "--batch", type=int, default=200,
        help="teto de mensagens lidas do Discord por execução (padrão 200)",
    )
    back.add_argument("--from", dest="_from", metavar="AAAA-MM-DD",
                      help="começa daqui em vez do início do canal")
    back.add_argument("--restart", action="store_true",
                      help="recomeça o histórico do zero, ignorando o cursor")
    back.add_argument("--dry-run", action="store_true", help="não escreve nada")
    back.add_argument("--no-enrich", action="store_true", help="não abre as páginas")
    back.add_argument("--include-bots", action="store_true")
    back.add_argument("--no-index", action="store_true")
    back.add_argument("--ignore-marks", action="store_true")
    back.set_defaults(func=cmd_backfill)

    semana = sub.add_parser("semana", help="apanhado dos últimos dias, a partir do vault")
    semana.add_argument("--days", type=int, default=7)
    semana.add_argument("--out", help="arquivo de saída (padrão: stdout)")
    semana.set_defaults(func=cmd_semana)

    mig = sub.add_parser(
        "migrar",
        help="cria a nota atômica dos links já capturados (sem LLM, sem rede)",
    )
    mig.add_argument("--day", metavar="AAAA-MM-DD", help="migra apenas este dia")
    mig.add_argument("--dry-run", action="store_true", help="não escreve nada")
    mig.add_argument(
        "--force", action="store_true",
        help="refaz o miolo de nota já existente (preserva triagem, nota e '## Minhas notas')",
    )
    mig.add_argument(
        "--no-diario", action="store_true",
        help="não insere a referência à nota no bullet do diário (deixa as notas órfãs)",
    )
    mig.add_argument("--limite", type=int, metavar="N", help="para depois de N notas")
    mig.set_defaults(func=cmd_migrar)

    sint = sub.add_parser(
        "sintese",
        help="síntese de período com LLM: temas, conexões e perguntas em aberto",
    )
    sint.add_argument(
        "--semana", metavar="QUAL",
        help="atual (padrão) · anterior · AAAA-Www (ex.: 2026-W34)",
    )
    sint.add_argument("--mes", metavar="AAAA-MM", help="rollup mensal dos semanais")
    sint.add_argument("--dry-run", action="store_true", help="não escreve no vault")
    sint.add_argument(
        "--force", action="store_true",
        help="regenera mesmo se o material do período não mudou",
    )
    sint.set_defaults(func=cmd_sintese)

    busca = sub.add_parser("buscar", help="pergunta ao acervo, pela linha de comando")
    busca.add_argument("pergunta", nargs="+", help="a pergunta")
    busca.set_defaults(func=cmd_buscar)

    resp = sub.add_parser(
        "responder", help="vigia o canal e responde `?buscar` (exige postar)"
    )
    resp.add_argument(
        "--uma-vez", action="store_true", help="uma passada em vez do laço"
    )
    resp.set_defaults(func=cmd_responder)

    ress = sub.add_parser(
        "ressurgir", help="posta links antigos com o resumo (sem chamar modelo)"
    )
    ress.add_argument("--dias", type=int, metavar="N", help="idade mínima (padrão 30)")
    ress.add_argument("--n", type=int, metavar="N", help="quantos (padrão 3)")
    ress.add_argument("--dry-run", action="store_true", help="mostra, não posta")
    ress.add_argument("--force", action="store_true", help="posta de novo no mesmo dia")
    ress.set_defaults(func=cmd_ressurgir)

    tem = sub.add_parser(
        "temas", help="conceitos que já viraram tema — relatório, não cria nada"
    )
    tem.add_argument("--limiar", type=int, metavar="N", help="links para 'maduro' (padrão 8)")
    tem.add_argument("--backlinks", action="store_true", help="conta backlinks (mais lento)")
    tem.add_argument("--markdown", action="store_true", help="bloco pronto para colar num MOC")
    tem.add_argument("--out", help="arquivo de saída (padrão: stdout)")
    tem.set_defaults(func=cmd_temas)

    doctor = sub.add_parser("doctor", help="checa configuração, vault e acesso ao Discord")
    doctor.add_argument(
        "--testar-envio", action="store_true",
        help="posta uma mensagem de teste e apaga — confere 'Enviar mensagens'",
    )
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        print(f"Configuração: {exc}", file=sys.stderr)
        return 2
    return args.func(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
