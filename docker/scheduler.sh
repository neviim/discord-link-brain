#!/bin/sh
# Agendador de produção: mantém o processo vivo e chama `dlb sync` na hora certa.
#
# Preferi isto a cron dentro do container por três motivos: cron não herda o
# ambiente do container (a dor clássica), os logs não vão para o stdout do
# PID 1, e um SIGTERM do `docker stop` não chega ao job em execução.
#
# Três modos, nesta ordem de precedência:
#   DLB_SYNC_INTERVAL     segundos corridos entre execuções (sem ancoragem)
#   DLB_SYNC_EVERY_HOURS  de N em N horas **ancorado na meia-noite** (padrão 6,
#                         ou seja 00:00, 06:00, 12:00, 18:00 no fuso TZ)
#   DLB_SYNC_AT           HH:MM, uma vez por dia
#
# Outras:
#   DLB_SYNC_ARGS      argumentos extras para o sync (ex.: "--no-enrich")
#   DLB_RUN_ON_START   1 = roda uma vez ao subir (padrão 1)
#   DLB_BACKFILL       1 = depois de cada sync, mastiga um pedaço do histórico
#   DLB_BACKFILL_DAYS  dias de histórico por execução (padrão 2)
#   DLB_SINTESE        1 = síntese semanal (padrão 0)
#   DLB_SINTESE_DIA    dia da semana, 1=segunda … 7=domingo (padrão 1)
#   DLB_SINTESE_MES    1 = no dia 1 do mês, também faz o rollup mensal
#   DLB_RESSURGIR      1 = posta links antigos uma vez por dia
set -eu

SYNC_AT="${DLB_SYNC_AT:-}"
EVERY_HOURS="${DLB_SYNC_EVERY_HOURS:-}"
INTERVAL="${DLB_SYNC_INTERVAL:-}"

# Sem nenhum dos três configurados, o padrão é de 6 em 6 horas desde 00:00.
if [ -z "$INTERVAL" ] && [ -z "$EVERY_HOURS" ] && [ -z "$SYNC_AT" ]; then
    EVERY_HOURS=6
fi
RUN_ON_START="${DLB_RUN_ON_START:-1}"
SYNC_ARGS="${DLB_SYNC_ARGS:-}"
HEARTBEAT="${DLB_HEARTBEAT:-/app/.state/heartbeat}"

sleep_pid=""

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"
}

on_term() {
    log "SIGTERM recebido — encerrando."
    [ -n "$sleep_pid" ] && kill "$sleep_pid" 2>/dev/null || true
    exit 0
}
trap on_term TERM INT

# Dorme de forma interrompível: `sleep` em background + `wait` deixa o trap
# rodar na hora, em vez de o docker stop ter que esperar o timeout de 10s.
nap() {
    sleep "$1" &
    sleep_pid=$!
    wait "$sleep_pid" 2>/dev/null || true
    sleep_pid=""
}

seconds_until() {
    python - "$1" <<'PY'
import datetime, sys

hh, mm = (int(p) for p in sys.argv[1].split(":"))
now = datetime.datetime.now()
target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
if target <= now:
    target += datetime.timedelta(days=1)
print(int((target - now).total_seconds()))
PY
}

# Próxima fatia de N horas contada a partir da meia-noite local — não a partir
# de quando o container subiu. Reiniciar às 14h47 não desloca a grade.
seconds_until_slot() {
    python - "$1" <<'PY'
import datetime, sys

passo = int(sys.argv[1]) * 3600
agora = datetime.datetime.now()
meia_noite = agora.replace(hour=0, minute=0, second=0, microsecond=0)
decorrido = (agora - meia_noite).total_seconds()
alvo = meia_noite + datetime.timedelta(seconds=(int(decorrido // passo) + 1) * passo)
print(int((alvo - agora).total_seconds()))
PY
}

run_sync() {
    log "dlb sync ${SYNC_ARGS}"
    set +e
    # shellcheck disable=SC2086  # SYNC_ARGS é uma lista de flags, quer split
    python -m dlb sync ${SYNC_ARGS}
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
        mkdir -p "$(dirname "$HEARTBEAT")"
        date +%s >"$HEARTBEAT"
        log "sync concluída."
    else
        # Não aborta o agendador: um dia com a API do Discord fora não pode
        # matar o container. O healthcheck é quem marca o estado ruim.
        log "sync FALHOU (código $rc) — seguindo para o próximo ciclo."
    fi
}

# O histórico anda em fatias pequenas, uma por ciclo. Como a grade é de 6h e a
# janela de uso da assinatura Claude renova a cada 5h, cada fatia cai numa
# janela nova — o passado é recuperado sem competir com o uso do dia.
run_backfill() {
    [ "${DLB_BACKFILL:-0}" = "1" ] || return 0
    dias="${DLB_BACKFILL_DAYS:-2}"
    log "dlb backfill --days ${dias}"
    set +e
    python -m dlb backfill --days "$dias"
    rc=$?
    set -e
    [ "$rc" -eq 0 ] || log "backfill falhou (código $rc) — tenta de novo no próximo ciclo."
}

# A síntese sai na segunda, e não na sexta como se poderia esperar: na sexta a
# semana ainda não fechou, e a nota nasceria sem os links do fim de semana — para
# depois ser regenerada, gastando uma segunda chamada. Na segunda a semana
# anterior está completa e a nota é final na primeira escrita.
#
# Não precisa de slot próprio no laço: `dlb sintese` guarda um hash do material
# do período e sai em zero segundo se nada mudou. Nos quatro ciclos de 6h do dia,
# só o primeiro gasta chamada de modelo.
run_sintese() {
    [ "${DLB_SINTESE:-0}" = "1" ] || return 0
    [ "$(date +%u)" = "${DLB_SINTESE_DIA:-1}" ] || return 0

    log "dlb sintese --semana anterior"
    set +e
    python -m dlb sintese --semana anterior
    rc=$?
    set -e
    [ "$rc" -eq 0 ] || log "sintese falhou (código $rc) — tenta de novo no próximo ciclo."

    # Rollup do mês que fechou, no primeiro dia do mês novo.
    if [ "${DLB_SINTESE_MES:-0}" = "1" ] && [ "$(date +%d)" = "01" ]; then
        mes=$(date -d "$(date +%Y-%m-01) -1 day" +%Y-%m 2>/dev/null || echo "")
        if [ -n "$mes" ]; then
            log "dlb sintese --mes ${mes}"
            set +e
            python -m dlb sintese --mes "$mes"
            set -e
        fi
    fi
}

# Uma vez por dia, e sem slot próprio: o `dlb ressurgir` guarda a última execução
# e sai na hora se já rodou hoje. Nos quatro ciclos de 6h, só o primeiro posta.
run_ressurgir() {
    [ "${DLB_RESSURGIR:-0}" = "1" ] || return 0
    log "dlb ressurgir"
    set +e
    python -m dlb ressurgir
    rc=$?
    set -e
    [ "$rc" -eq 0 ] || log "ressurgir falhou (código $rc) — tenta de novo no próximo ciclo."
}

if [ -n "$INTERVAL" ]; then
    log "modo intervalo: a cada ${INTERVAL}s (fuso ${TZ:-?})"
elif [ -n "$EVERY_HOURS" ]; then
    log "modo grade: de ${EVERY_HOURS} em ${EVERY_HOURS}h a partir de 00:00 (fuso ${TZ:-?})"
else
    log "modo diário: às ${SYNC_AT} (fuso ${TZ:-?})"
fi

if [ "$RUN_ON_START" = "1" ]; then
    run_sync
    run_backfill
    run_sintese
    run_ressurgir
fi

while true; do
    if [ -n "$INTERVAL" ]; then
        wait_for="$INTERVAL"
    elif [ -n "$EVERY_HOURS" ]; then
        wait_for=$(seconds_until_slot "$EVERY_HOURS")
    else
        wait_for=$(seconds_until "$SYNC_AT")
    fi
    log "próxima execução em ${wait_for}s."
    nap "$wait_for"
    run_sync
    run_backfill
    run_sintese
    run_ressurgir
done
