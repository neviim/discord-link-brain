#!/usr/bin/env bash
# Wrapper de container do discord-link-brain.
#
# A razão de existir: o docker compose não expande `~`, não sabe seu UID e não
# sabe onde mora o binário do claude. Este script resolve isso a partir do .env
# e chama o compose com o ambiente já pronto.
#
#   ./dlb.sh dev doctor              checa config, vault e Discord (vault :ro)
#   ./dlb.sh dev sync --dry-run      ensaio, não escreve nada
#   ./dlb.sh dev migrar --dry-run    nota atômica do que já está no vault
#   ./dlb.sh dev test                suíte de testes
#   ./dlb.sh dev shell               bash dentro do container
#   ./dlb.sh prod up                 sobe o agendador em background
#   ./dlb.sh prod logs               acompanha o log
#   ./dlb.sh prod run sync --since 7 uma execução avulsa, fora do agendador
#   ./dlb.sh prod status             estado e saúde do container
#   ./dlb.sh prod down               derruba
#
#   ./dlb.sh map                     mapa 3D do código (mindwalk)
#   ./dlb.sh sessions                navega as sessões de agente gravadas
#
# `./dlb.sh dev --rw <cmd>` monta o vault para escrita (perigoso — prefira
# DLB_DEV_VAULT apontando para uma cópia do vault).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

die() { printf '\033[31merro:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[2m%s\033[0m\n' "$*" >&2; }

[[ -f .env ]] || die "não há .env. Rode: cp .env.example .env && \$EDITOR .env"

# Lê uma chave do .env sem executá-lo (o valor pode conter espaço, # e aspas).
env_get() {
    local key="$1" line
    line="$(grep -E "^[[:space:]]*${key}=" .env | tail -1 || true)"
    [[ -z "$line" ]] && return 0
    line="${line#*=}"
    line="${line%\"}"; line="${line#\"}"
    line="${line%\'}"; line="${line#\'}"
    printf '%s' "$line"
}

expand_home() {
    local p="$1"
    case "$p" in
        "~") printf '%s' "$HOME" ;;
        "~/"*) printf '%s' "$HOME/${p#\~/}" ;;
        *) printf '%s' "$p" ;;
    esac
}

# ------------------------------------------------------------------- vault ---
VAULT_RAW="$(env_get OBSIDIAN_VAULT)"
[[ -n "$VAULT_RAW" ]] || die "OBSIDIAN_VAULT não está no .env"
DLB_HOST_VAULT="$(expand_home "$VAULT_RAW")"
[[ -d "$DLB_HOST_VAULT" ]] || die "vault não encontrado: $DLB_HOST_VAULT"
export DLB_HOST_VAULT

TIMEZONE="$(env_get TIMEZONE)"; export TIMEZONE="${TIMEZONE:-UTC}"

DLB_UID="$(id -u)"; DLB_GID="$(id -g)"; export DLB_UID DLB_GID

# ------------------------------------------------------------------ claude ---
# Só monta o login do host quando o backend realmente é o CLI. Com `anthropic`
# ou `none`, o container não recebe acesso nenhum ao seu ~/.claude.
LLM_BACKEND="${LLM_BACKEND:-$(env_get LLM_BACKEND)}"
LLM_BACKEND="${LLM_BACKEND:-claude-cli}"

if [[ "$LLM_BACKEND" == "claude-cli" ]]; then
    claude_bin="$(env_get CLAUDE_BIN)"
    claude_bin="${claude_bin:-claude}"
    if [[ "$claude_bin" != /* ]]; then
        claude_bin="$(command -v "$claude_bin" 2>/dev/null || true)"
    fi
    if [[ -n "$claude_bin" && -x "$claude_bin" ]]; then
        # readlink -f: o docker monta o alvo do link, e o link do claude aponta
        # para um caminho versionado que muda a cada atualização.
        DLB_CLAUDE_BIN="$(readlink -f "$claude_bin")"
        DLB_CLAUDE_HOME="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
        [[ -d "$DLB_CLAUDE_HOME" ]] \
            || die "não achei $DLB_CLAUDE_HOME — rode \`claude\` uma vez no host"
        export DLB_CLAUDE_BIN DLB_CLAUDE_HOME
    else
        info "aviso: LLM_BACKEND=claude-cli mas não achei o binário do claude;"
        info "       a classificação vai falhar dentro do container."
    fi
fi

# Mesma história para o Codex, que é o backend padrão da síntese periódica. O
# binário é ELF estático, então roda no container sem runtime nenhum.
SINTESE_BACKEND="${SINTESE_BACKEND:-$(env_get SINTESE_BACKEND)}"
SINTESE_BACKEND="${SINTESE_BACKEND:-codex}"

if [[ "$SINTESE_BACKEND" == "codex" ]]; then
    codex_bin="$(env_get CODEX_BIN)"
    codex_bin="${codex_bin:-codex}"
    if [[ "$codex_bin" != /* ]]; then
        codex_bin="$(command -v "$codex_bin" 2>/dev/null || true)"
    fi
    if [[ -n "$codex_bin" && -x "$codex_bin" ]]; then
        DLB_CODEX_BIN="$(readlink -f "$codex_bin")"
        DLB_CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
        [[ -d "$DLB_CODEX_HOME" ]] \
            || die "não achei $DLB_CODEX_HOME — rode \`codex\` uma vez no host"
        export DLB_CODEX_BIN DLB_CODEX_HOME
    else
        info "aviso: SINTESE_BACKEND=codex mas não achei o binário do codex;"
        info "       a síntese vai falhar dentro do container."
    fi
fi

mkdir -p .state

# nota: `exec` ignora funções do shell, então as chamadas abaixo usam `docker compose` direto.

usage() { sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

mode="${1:-help}"; shift || true

case "$mode" in
# --------------------------------------------------------------------- dev ---
dev)
    vault_mode=ro
    if [[ "${1:-}" == "--rw" ]]; then vault_mode=rw; shift; fi
    export DLB_DEV_VAULT="${DLB_DEV_VAULT:-$DLB_HOST_VAULT}"
    export DLB_DEV_VAULT_MODE="$vault_mode"
    [[ -d "$DLB_DEV_VAULT" ]] || die "DLB_DEV_VAULT não existe: $DLB_DEV_VAULT"

    info "dev · vault ${DLB_DEV_VAULT} (${vault_mode}) · llm ${LLM_BACKEND} · tz ${TIMEZONE}"
    case "${1:-doctor}" in
        build) shift; exec docker compose --profile dev build "$@" ;;
        down|ps) exec docker compose --profile dev "$@" ;;
        *) exec docker compose --profile dev run --rm --build dev "$@" ;;
    esac
    ;;

# -------------------------------------------------------------------- prod ---
prod)
    # O compose valida os dois serviços mesmo com --profile; estes calam o `:?`
    # do serviço dev sem montar nada além do que prod já monta.
    export DLB_DEV_VAULT="$DLB_HOST_VAULT" DLB_DEV_VAULT_MODE=ro
    sub="${1:-status}"; shift || true
    info "prod · vault ${DLB_HOST_VAULT} (rw) · llm ${LLM_BACKEND} · tz ${TIMEZONE}"
    case "$sub" in
        up)      exec docker compose --profile prod up -d --build "$@" ;;
        down)    exec docker compose --profile prod down "$@" ;;
        restart) exec docker compose --profile prod restart "$@" ;;
        build)   exec docker compose --profile prod build "$@" ;;
        logs)    exec docker compose --profile prod logs -f --tail 200 "$@" ;;
        status|ps)
            docker compose --profile prod ps
            docker inspect --format \
                'saúde: {{if .State.Health}}{{.State.Health.Status}}{{else}}sem healthcheck{{end}} · desde {{.State.StartedAt}}' \
                dlb-prod 2>/dev/null || true
            ;;
        run)     exec docker compose --profile prod run --rm prod "$@" ;;
        exec)    exec docker compose --profile prod exec prod "$@" ;;
        *) die "subcomando desconhecido: $sub (up|down|restart|build|logs|status|run|exec)" ;;
    esac
    ;;

build)
    export DLB_DEV_VAULT="$DLB_HOST_VAULT" DLB_DEV_VAULT_MODE=ro
    exec docker compose --profile dev --profile prod build "$@"
    ;;

# --------------------------------------------------------------------- map ---
# Mapa 3D do código (mindwalk). Roda no host, não no container: é uma ferramenta
# de quem desenvolve, não parte do pipeline.
map)
    mindwalk="${MINDWALK_BIN:-$(command -v mindwalk || echo "$HOME/.local/bin/mindwalk")}"
    [[ -x "$mindwalk" ]] || die "mindwalk não instalado. Veja a seção 'Mapa 3D' do README."
    info "abrindo o citymap de ${PROJECT_DIR}"
    exec "$mindwalk" map "$PROJECT_DIR" "$@"
    ;;

sessions)
    mindwalk="${MINDWALK_BIN:-$(command -v mindwalk || echo "$HOME/.local/bin/mindwalk")}"
    [[ -x "$mindwalk" ]] || die "mindwalk não instalado. Veja a seção 'Mapa 3D' do README."
    exec "$mindwalk" serve "$@"
    ;;

help|-h|--help) usage ;;
*) die "modo desconhecido: $mode (use: dev | prod | build | map | sessions | help)" ;;
esac
