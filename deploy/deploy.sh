#!/usr/bin/env bash
# Instala (primeira vez) ou atualiza (git pull + rebuild + restart) o
# discord-link-brain no servidor de produção, via SSH — sem sudo, sem senha,
# depois que deploy/bootstrap-servidor.sh rodou lá uma vez.
# Espelha deploy/deploy.sh do youtube-radar (mesmo host, mesmo usuário —
# reaproveita a mesma chave de deploy por padrão).
#
#   ./deploy/deploy.sh              atualiza (ou sobe, se ainda não estiver rodando)
#   ./deploy/deploy.sh --seco       mostra o que mudaria, sem tocar em nada
#   ./deploy/deploy.sh --status     só mostra o commit e a saúde dos containers remotos
#
# Idempotente: se o commit já é o mais novo mas algum dos containers não está
# rodando (primeiro deploy depois do bootstrap, ou container caído), sobe
# mesmo assim — não depende de ter commit novo pra agir.
#
# Se algo não commitado for encontrado na pasta remota, ou os containers
# (dlb-prod + dlb-responder) não ficarem saudáveis depois do rebuild, o
# deploy é abortado / revertido sozinho para o commit anterior — nunca deixa
# o servidor pela metade.
#
# Configuração (variáveis de ambiente, todas com default sensato pra este repo):
#   DLB_SSH_HOST      (default: 192.168.15.17)
#   DLB_SSH_USER      (default: neviim)
#   DLB_SSH_KEY       (default: ~/.ssh/ytr_deploy — mesma chave do youtube-radar,
#                      mesmo destino neviim@192.168.15.17, sem motivo pra duplicar)
#   DLB_DEPLOY_DIR    (default: /opt/discord-link-brain)
#   DLB_DEPLOY_BRANCH (default: master)
set -euo pipefail

SSH_HOST="${DLB_SSH_HOST:-192.168.15.17}"
SSH_USER="${DLB_SSH_USER:-neviim}"
SSH_KEY="${DLB_SSH_KEY:-$HOME/.ssh/ytr_deploy}"
DEPLOY_DIR="${DLB_DEPLOY_DIR:-/opt/discord-link-brain}"
BRANCH="${DLB_DEPLOY_BRANCH:-master}"

info() { printf '\033[2m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31merro:\033[0m %s\n' "$*" >&2; exit 1; }

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8)
[[ -f "$SSH_KEY" ]] && SSH_OPTS+=(-i "$SSH_KEY")
ssh_do() { ssh "${SSH_OPTS[@]}" "${SSH_USER}@${SSH_HOST}" "$@"; }

mode="${1:---up}"
case "$mode" in
    --status)
        info "status em ${SSH_HOST}:${DEPLOY_DIR}"
        ssh_do "cd '$DEPLOY_DIR' && git log -1 --oneline && ./dlb.sh prod status"
        exit 0
        ;;
    --seco|--up) : ;;
    *) die "modo desconhecido: $mode (--up | --seco | --status)" ;;
esac

info "conectando em ${SSH_USER}@${SSH_HOST}..."
ssh_do "test -d '$DEPLOY_DIR'" \
    || die "$DEPLOY_DIR não existe em ${SSH_HOST} — rode deploy/bootstrap-servidor.sh lá primeiro (uma vez só)"

DRY_RUN=0
[[ "$mode" == --seco ]] && DRY_RUN=1

# Só $DEPLOY_DIR e $BRANCH são substituídos aqui (localmente); tudo com \$ vira
# variável de verdade só quando o script roda do outro lado do SSH.
remote_script=$(cat <<REMOTE
set -euo pipefail
cd '$DEPLOY_DIR'

exec 9>.deploy.lock
flock -n 9 || { echo "outro deploy já em andamento em $DEPLOY_DIR" >&2; exit 1; }

if [[ -n "\$(git status --porcelain)" ]]; then
    echo "erro: há mudanças não commitadas em $DEPLOY_DIR — resolva na mão antes do deploy" >&2
    exit 1
fi

git fetch origin "$BRANCH"
PREV="\$(git rev-parse HEAD)"
NEW="\$(git rev-parse origin/$BRANCH)"
UP_TO_DATE=0
[[ "\$PREV" == "\$NEW" ]] && UP_TO_DATE=1

ALL_RUNNING=1
for c in dlb-prod dlb-responder; do
    running="\$(docker inspect --format '{{.State.Running}}' "\$c" 2>/dev/null || echo false)"
    [[ "\$running" == true ]] || ALL_RUNNING=0
done

if [[ "\$UP_TO_DATE" == 1 && "\$ALL_RUNNING" == 1 ]]; then
    echo "já está no commit mais novo (\${PREV:0:7}) e os containers estão rodando — nada pra fazer."
    exit 0
fi

if [[ "\$UP_TO_DATE" == 1 ]]; then
    echo "commit já é o mais novo (\${PREV:0:7}); nem todo container está rodando — subindo mesmo assim."
else
    echo "atualizando \${PREV:0:7} -> \${NEW:0:7}:"
    git log --oneline "\$PREV..\$NEW"
fi

if [[ "\$DRY_RUN" == 1 ]]; then
    echo "(--seco: parei aqui, nada foi tocado)"
    exit 0
fi

if [[ "\$UP_TO_DATE" != 1 ]]; then
    git checkout "$BRANCH"
    git merge --ff-only "origin/$BRANCH"
fi

if [[ ! -f .env ]]; then
    echo "erro: não há .env em $DEPLOY_DIR — copie de .env.example e preencha antes de subir" >&2
    exit 1
fi

./dlb.sh prod up

# dois containers no profile prod: dlb-prod (agendador) e dlb-responder.
ok=0
for _ in \$(seq 1 12); do
    sleep 5
    all_ok=1
    for c in dlb-prod dlb-responder; do
        health="\$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}sem-healthcheck{{end}}' "\$c" 2>/dev/null || echo indisponivel)"
        echo "saúde \$c: \$health"
        [[ "\$health" == healthy || "\$health" == sem-healthcheck ]] || all_ok=0
    done
    if [[ "\$all_ok" == 1 ]]; then ok=1; break; fi
done

if [[ "\$ok" != 1 ]]; then
    echo "containers não ficaram saudáveis depois do deploy — revertendo para \${PREV:0:7}" >&2
    git checkout "$BRANCH"
    git reset --hard "\$PREV"
    ./dlb.sh prod up
    exit 1
fi

echo "deploy ok, em \$(git rev-parse --short HEAD)"
REMOTE
)

ssh_do "DRY_RUN=$DRY_RUN bash -s" <<<"$remote_script"
