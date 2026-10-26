"""Niveaux d'effort choisis par l'utilisateur : faible / normal / élevé / max / ultracode.

Vocabulaire de l'interface (français). Vers l'extérieur, un seul vocabulaire : celui du CLI
Claude Code (`--effort low|medium|high|xhigh|max`), aussi donné aux agents shell via
$ORCH_EFFORT. « ultracode » = le palier le plus haut (`xhigh`).
"""
from __future__ import annotations

import unicodedata
from typing import Dict, Optional, Tuple

LEVELS: Tuple[str, ...] = ("faible", "normal", "élevé", "max", "ultracode")
CLI_EFFORT: Dict[str, str] = {"faible": "low", "normal": "medium", "élevé": "high",
                              "max": "max", "ultracode": "xhigh"}


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


_BY_FOLDED = {_fold(level): level for level in LEVELS}


def clean_effort(value: object) -> Optional[str]:
    """Niveau normalisé ; « » = défaut de l'outil ; None = valeur invalide (jamais devinée)."""
    if not isinstance(value, str):
        return None
    text = _fold(value.strip().lower())
    if not text:
        return ""
    return _BY_FOLDED.get(text)


def cli_effort(value: object) -> str:
    """Valeur du CLI pour un niveau enregistré, « » si défaut ou invalide."""
    level = clean_effort(value)
    return CLI_EFFORT.get(level or "", "")
