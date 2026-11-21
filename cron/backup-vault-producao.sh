#!/usr/bin/env bash
# Backup do vault Obsidian de PRODUÇÃO do discord-link-brain para esta máquina.
#
# Origem : ghost-04 (192.168.15.17):~/Documents/Obsidian/vault
# Destino: /media/neviim/Daw&Samplers/__Obsidian-2026_OFICIAL/Backup-Producao-DLB/vault
#
# - Espelho fiel (rsync --delete): o destino fica idêntico à produção.
# - Nada se perde de vez: arquivo apagado ou alterado na produção tem a versão
#   anterior guardada em Backup-Producao-DLB/_versoes/<data-hora>/ e essas
#   pastas são removidas depois de DLB_BKP_RETENCAO_DIAS dias.
# - Só grava se o disco de destino estiver montado (o HD é removível; sem isso
#   o rsync encheria o disco do sistema em /media).
# - flock impede duas execuções simultâneas.
#
# Uso manual : cron/backup-vault-producao.sh
# Cron (6/6h): 15 */6 * * * /caminho/cron/backup-vault-producao.sh
#
# Variáveis opcionais (sobrescrevem os defaults):
#   DLB_BKP_HOST, DLB_BKP_USER, DLB_BKP_CHAVE, DLB_BKP_ORIGEM, DLB_BKP_MOUNT, DLB_BKP_DESTINO,
#   DLB_BKP_RETENCAO_DIAS, DLB_BKP_LOG
set -euo pipefail

HOST="${DLB_BKP_HOST:-192.168.15.17}"
USUARIO="${DLB_BKP_USER:-neviim}"
CHAVE="${DLB_BKP_CHAVE:-$HOME/.ssh/id_ed25519_dev}"
ORIGEM="${DLB_BKP_ORIGEM:-Documents/Obsidian/vault}"   # relativo ao $HOME remoto
MOUNT="${DLB_BKP_MOUNT:-/media/neviim/Daw&Samplers}"
BASE="${DLB_BKP_DESTINO:-$MOUNT/__Obsidian-2026_OFICIAL/Backup-Producao-DLB}"
RETENCAO_DIAS="${DLB_BKP_RETENCAO_DIAS:-30}"
LOG="${DLB_BKP_LOG:-$HOME/.local/state/dlb-backup/backup-vault.log}"

DESTINO="$BASE/vault"
VERSOES="$BASE/_versoes"
CARIMBO="$(date +%Y-%m-%d_%H%M%S)"
LOCK="${XDG_RUNTIME_DIR:-/tmp}/dlb-backup-vault.lock"

mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1   # cron não tem terminal: tudo vai para o log

log() { printf '%s [%s] %s\n' "$(date '+%d/%m/%Y %H:%M:%S')" "$1" "$2"; }
falha() { log ERRO "$1"; exit 1; }

exec 9>"$LOCK"
flock -n 9 || { log AVISO "outra execução em andamento — saindo"; exit 0; }

log INFO "início: ${USUARIO}@${HOST}:~/${ORIGEM} -> ${DESTINO}"

mountpoint -q "$MOUNT" || falha "disco de destino não montado em '$MOUNT' — backup não executado"

# Chave explícita: no cron não há ssh-agent, então a chave tem de ser sem senha.
[[ -r "$CHAVE" ]] || falha "chave SSH '$CHAVE' não encontrada"
SSH_OPTS=(-i "$CHAVE" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15
          -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
ssh "${SSH_OPTS[@]}" "${USUARIO}@${HOST}" "test -d ~/'${ORIGEM}'" \
    || falha "sem acesso SSH a ${HOST} ou origem ~/${ORIGEM} inexistente"

mkdir -p "$DESTINO" "$VERSOES"

# -rt em vez de -a: o destino é NTFS (fuseblk), que não guarda dono/grupo/permissão
# do Linux; --modify-window=1 tolera a diferença de resolução de data do NTFS.
set +e
rsync -rt --modify-window=1 --delete --delete-delay \
      --backup --backup-dir="$VERSOES/$CARIMBO" \
      --exclude='.obsidian/workspace*.json' --exclude='.trash/' \
      --stats --human-readable \
      -e "ssh ${SSH_OPTS[*]}" \
      "${USUARIO}@${HOST}:${ORIGEM}/" "$DESTINO/"
rc=$?
set -e
# 24 = arquivos sumiram na origem durante a cópia (o bot pode estar escrevendo): não é erro.
if [[ $rc -ne 0 && $rc -ne 24 ]]; then
    falha "rsync terminou com código $rc"
fi

# Remove versões antigas (só pastas com o formato de data deste script).
find "$VERSOES" -mindepth 1 -maxdepth 1 -type d -name '20??-??-??_??????' \
     -mtime +"$RETENCAO_DIAS" -exec rm -rf {} + || log AVISO "falha ao limpar versões antigas"

date '+%d/%m/%Y %H:%M:%S' > "$BASE/ULTIMO_BACKUP_OK.txt"
log INFO "concluído (rsync rc=$rc); arquivos no destino: $(find "$DESTINO" -type f | wc -l)"
