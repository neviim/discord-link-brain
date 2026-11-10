#!/usr/bin/env bash
# PD17: mantem uma copia ESTAVEL do binario do Claude para o dlb montar no docker.
# O claude real fica em ~/.local/share/claude/versions/<versao> e some quando o
# Claude Code atualiza; o dlb montava esse caminho e falhava no proximo restart.
# Este script copia a versao atual para um caminho fixo, so quando a versao muda.
set -euo pipefail
link="/home/neviim/.local/bin/claude"
dst="/opt/discord-link-brain/bin/claude"
src="$(readlink -f "$link" 2>/dev/null || true)"
[ -n "$src" ] && [ -x "$src" ] || exit 0
if [ ! -f "$dst" ] || [ "$src" != "$(cat "$dst.src" 2>/dev/null || true)" ]; then
  cp -f "$src" "$dst.tmp"
  chmod +x "$dst.tmp"
  mv -f "$dst.tmp" "$dst"
  printf "%s\n" "$src" > "$dst.src"
  echo "atualizado: $src -> $dst"
fi
