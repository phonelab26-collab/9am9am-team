"""Étapes d'outils d'un agent, lues dans le flux JSON d'opencode (jamais inventées).

Une étape = {tool, summary, status, ms}. On garde l'outil, UNE ligne d'argument (commande,
fichier, motif…), le statut et la durée — jamais la sortie de l'outil (volumineuse, et elle
peut contenir des secrets). La ligne d'argument est passée par `redact` AVANT d'être coupée, et seuls
les champs connus (SUMMARY_KEYS) sont lus : un outil inconnu n'expose jamais un champ au hasard.
Les chemins absolus sont raccourcis (projet → « . », home → « ~ »).
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from .redact import redact

MAX_STEPS = 50        # étapes gardées par exécution ; le reste est compté, pas stocké
SUMMARY_MAX = 140     # caractères d'argument gardés (+ « … »)
SCAN_MAX = 4000       # début de l'argument analysé : une commande géante ne coûte rien de plus
STATUS_MAX = 20
UNKNOWN = "unknown"
DEFAULT_TOOL = "outil"
# Premier champ non vide parmi ceux-ci = l'argument qui résume l'appel.
SUMMARY_KEYS = ("command", "filePath", "file_path", "path", "pattern", "url", "query",
                "description", "prompt", "file")

Step = Dict[str, Union[str, int, None]]


@lru_cache(maxsize=16)
def _path_bases(root: str) -> Tuple[Tuple[str, str], ...]:
    """(chemin, remplaçant) du plus long au plus court ; la forme résolue du projet compte aussi
    (macOS : /var/… s'écrit /private/var/…)."""
    root = root.rstrip("/")
    bases = {(b, ".") for b in (root, os.path.realpath(root) if root else "") if len(b) > 1}
    home = str(Path.home()).rstrip("/")
    if len(home) > 1:  # jamais « / » : il remplacerait toutes les barres
        bases.add((home, "~"))
    return tuple(sorted(bases, key=lambda b: -len(b[0])))


def _shorten_paths(text: str, root: str) -> str:
    for base, short in _path_bases(root):
        # Bornes des deux côtés : « /mnt<projet>/x » et « <projet>-2/y » ne sont pas le projet.
        text = re.sub(r"(?<![\w/.-])" + re.escape(base) + r"(?![\w.-])", short, text)
    return text


def _summary(state: dict, root: str) -> str:
    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
    raw = next((v for v in (inp.get(k) for k in SUMMARY_KEYS) if isinstance(v, str) and v.strip()), "")
    # Coupé AVANT le masquage : seul ce début peut finir dans le résumé (SUMMARY_MAX ≪ SCAN_MAX).
    line = _shorten_paths(" ".join(redact(raw[:SCAN_MAX]).split()), root)
    return line if len(line) <= SUMMARY_MAX else line[:SUMMARY_MAX].rstrip() + "…"


def _duration_ms(state: dict) -> Optional[int]:
    span = state.get("time") if isinstance(state.get("time"), dict) else {}
    start, end = span.get("start"), span.get("end")
    ints = all(isinstance(v, int) and not isinstance(v, bool) for v in (start, end))
    return max(0, end - start) if ints else None


def step_of(event: dict, root: str = "") -> Step:
    """Événement `tool_use` d'opencode → étape. Tolère toute forme inattendue."""
    part = event.get("part") if isinstance(event.get("part"), dict) else {}
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    status = state.get("status")
    return {"tool": str(part.get("tool") or DEFAULT_TOOL)[:60],
            "summary": _summary(state, root),
            "status": status[:STATUS_MAX] if isinstance(status, str) and status else UNKNOWN,
            "ms": _duration_ms(state)}
