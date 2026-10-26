#!/bin/bash
# 9AM9AM-TEAM — lanceur double-clic macOS.
# Finder : double-clic  -> interface web locale (recommandé), repli sur le menu si
#                          l'interface ne démarre pas.
# Terminal : ./9AM9AM-TEAM.command menu|plan|run|tick|retry|status|selftest (voir README).
set -u
cd "$(dirname "$0")" || exit 1

if [ ! -f mini_orchestrateur.py ]; then
  echo "mini_orchestrateur.py introuvable à côté de ce lanceur."
  read -n 1 -r -p "Appuie sur une touche pour fermer…"
  exit 1
fi

# L'interface est une page web locale (http.server de la bibliothèque standard) :
# aucun tkinter requis. On garde /usr/bin/python3 d'abord (le python système, comme
# le logiciel 9AM9AM-TEAM.app), puis le python3 du PATH, puis celui de Homebrew.
pick_python() {
  for p in /usr/bin/python3 python3 /opt/homebrew/bin/python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import http.server' >/dev/null 2>&1; then
      printf '%s' "$p"
      return 0
    fi
  done
  return 1
}

# Tous les arguments sont transmis : `plan "objectif"`, `run --model x`…
# (avant seul $1 était gardé → « error: the following arguments are required »).
if [ $# -gt 0 ]; then
  MODE="$1"
  shift
else
  MODE=gui
fi
PY="$(pick_python || true)"
PY="${PY:-python3}"

echo "===== 9AM9AM-TEAM ====="
if [ "$MODE" = "gui" ]; then
  # Interface en échec (port occupé, navigateur absent…) -> menu Terminal.
  "$PY" mini_orchestrateur.py gui "$@" || "$PY" mini_orchestrateur.py menu
else
  "$PY" mini_orchestrateur.py "$MODE" "$@"
fi
rc=$?

if [ "$rc" -ne 0 ]; then
  echo
  read -n 1 -r -p "Code retour $rc — appuie sur une touche pour fermer…"
fi
exit "$rc"
