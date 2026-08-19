#!/bin/sh
# Traduz o argumento do container para o que deve rodar de fato.
#
#   (nada)              -> o CMD da imagem (dev: doctor, prod: scheduler)
#   scheduler           -> laço de agendamento (produção)
#   healthcheck         -> verifica o heartbeat do agendador
#   test | tests        -> suíte de testes
#   shell | bash | sh   -> shell interativo
#   sync | doctor | ... -> python -m dlb <args>
set -eu

HEARTBEAT="${DLB_HEARTBEAT:-/app/.state/heartbeat}"

case "${1:-}" in
    responder)
        # Laço próprio, rápido (60s), num serviço separado do agendador lento.
        exec python -m dlb responder
        ;;
    scheduler)
        exec scheduler.sh
        ;;

    healthcheck)
        # Considera saudável se a última sync bem-sucedida cabe na janela de
        # tolerância. Sem heartbeat ainda (container recém-subido), aceita:
        # o --start-period do HEALTHCHECK já cobre esse intervalo.
        [ -f "$HEARTBEAT" ] || exit 0
        now=$(date +%s)
        last=$(cat "$HEARTBEAT" 2>/dev/null || echo 0)
        # Tolerância = período + folga. Padrão 8h, para a grade de 6 em 6 horas.
        tolerance="${DLB_HEALTH_TOLERANCE:-28800}"
        age=$((now - last))
        if [ "$age" -gt "$tolerance" ]; then
            echo "última sync bem-sucedida há ${age}s (limite ${tolerance}s)" >&2
            exit 1
        fi
        echo "última sync bem-sucedida há ${age}s"
        exit 0
        ;;

    test|tests)
        exec python -m unittest discover -s tests -t . -v
        ;;

    shell|bash)
        shift
        exec /bin/bash "$@"
        ;;

    sh)
        shift
        exec /bin/sh "$@"
        ;;

    "")
        exec python -m dlb doctor
        ;;

    *)
        exec python -m dlb "$@"
        ;;
esac
