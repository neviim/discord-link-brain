#!/usr/bin/env bash
# Preparo de UMA VEZ SÓ do servidor de produção para o discord-link-brain.
# Espelha deploy/bootstrap-servidor.sh do youtube-radar — os dois projetos
# compartilham o mesmo vault (~/Documents/Obsidian/vault) no mesmo host, e
# convém que ambos rodem como o mesmo usuário do sistema.
#
# Resolve as duas travas de permissão que existiam antes de rodar isto:
#   1. a pasta de deploy não era do usuário que faz deploy -> vira dele aqui
#      (chown único, com setgid: tudo que nascer lá dentro herda o grupo docker)
#   2. rodar docker exigia sudo -> usuário entra no grupo docker aqui
#
# Também garante o esqueleto de pastas que dlb/config.py:validate_vault()
# exige (10_FONTES, 20_WIKI, 30_MOC, 40_DIARIO, 90_META) e 50_LINKS/, onde o
# sync escreve uma nota por link — o youtube-radar só lê dali (:ro).
#
# Rodar UMA VEZ, no servidor, logado como o usuário que vai fazer deploy
# (ex: neviim) — pede sudo interativamente, por isso precisa de -t:
#
#   scp deploy/bootstrap-servidor.sh neviim@192.168.15.17:/tmp/dlb-bootstrap.sh
#   ssh -t neviim@192.168.15.17 'bash /tmp/dlb-bootstrap.sh'
#
# Depois de rodar com sucesso, pode apagar /tmp/dlb-bootstrap.sh.
set -euo pipefail

DEPLOY_DIR="${DLB_DEPLOY_DIR:-/opt/discord-link-brain}"
REPO_URL="${DLB_REPO_URL:-https://github.com/neviim/discord-link-brain.git}"
BRANCH="${DLB_DEPLOY_BRANCH:-master}"
VAULT_DIR="${DLB_VAULT_DIR:-$HOME/Documents/Obsidian/vault}"
DEPLOY_USER="$(whoami)"

info() { printf '\033[2m%s\033[0m\n' "$*"; }
die()  { printf '\033[31merro:\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$DEPLOY_USER" != root ]] || die "rode como o usuário de deploy (ex: neviim), não como root"

command -v docker >/dev/null \
    || die "docker não está instalado. Instale antes (ex: curl -fsSL https://get.docker.com | sudo sh) e rode este script de novo."
docker compose version >/dev/null 2>&1 \
    || die "docker compose (plugin v2) não encontrado — precisa da versão que traz 'docker compose', não o 'docker-compose' antigo."

info "== grupo docker =="
if id -nG "$DEPLOY_USER" | grep -qw docker; then
    info "$DEPLOY_USER já está no grupo docker"
else
    sudo usermod -aG docker "$DEPLOY_USER"
    NEEDS_RELOGIN=1
    info "adicionado ao grupo docker — precisa relogar (ssh de novo, ou 'newgrp docker') pra valer nesta sessão"
fi

info "== pasta de deploy: $DEPLOY_DIR =="
sudo mkdir -p "$DEPLOY_DIR"
sudo chown "$DEPLOY_USER:docker" "$DEPLOY_DIR"
sudo chmod 2775 "$DEPLOY_DIR"

if [[ -d "$DEPLOY_DIR/.git" ]]; then
    info "$DEPLOY_DIR já é um clone git — não vou recriar"
else
    info "== clonando $REPO_URL ($BRANCH) =="
    git clone --branch "$BRANCH" "$REPO_URL" "$DEPLOY_DIR"
fi

cd "$DEPLOY_DIR"
if [[ -f .env ]]; then
    info ".env já existe — não vou sobrescrever"
else
    cp .env.example .env
    info "criei $DEPLOY_DIR/.env a partir do .env.example — PRECISA editar antes do primeiro 'prod up'"
fi

info "== esqueleto do vault: $VAULT_DIR =="
mkdir -p "$VAULT_DIR"/{10_FONTES,20_WIKI,30_MOC,40_DIARIO,50_LINKS,90_META}
info "pastas conferidas: 10_FONTES 20_WIKI 30_MOC 40_DIARIO 50_LINKS 90_META"

echo
info "pronto. Daqui pra frente, deploy roda sem sudo e sem senha (SSH por chave)."
[[ "${NEEDS_RELOGIN:-0}" == 1 ]] && info "IMPORTANTE: relogue antes do primeiro deploy, pro grupo docker valer nesta sessão."
info "falta preencher: $DEPLOY_DIR/.env (DISCORD_TOKEN e o resto — veja docs/CONFIGURACAO.md)"
info "se já existir conteúdo real de vault em outra máquina, ele precisa ser copiado para $VAULT_DIR — este script só cria o esqueleto vazio."
