"""Fonctions de texte pures, partagées par le moteur, la CLI et l'interface."""
from __future__ import annotations

import textwrap

JOURNAL_WRAP = 100  # caractères par ligne du journal (≈ largeur de l'interface)
GOAL_MAX = 2000


def tail(text: object, limit: int) -> str:
    """Garde la FIN du texte : les workers y mettent leur conclusion et leurs erreurs."""
    s = str(text or "").strip()
    return s if len(s) <= limit else "…(début omis) " + s[-limit:]


def norm_goal(goal: object) -> str:
    """Même normalisation partout : l'objectif sert de clé de regroupement."""
    return str(goal or "").strip()[:GOAL_MAX]


def journal_lines(text: object, width: int = JOURNAL_WRAP) -> list[str]:
    """Texte → lignes de Listbox (la Listbox ne replie pas le texte elle-même)."""
    out: list[str] = []
    for raw in str(text or "").expandtabs(4).splitlines() or [""]:
        out.extend(textwrap.wrap(raw, width) or [""])
    return out
