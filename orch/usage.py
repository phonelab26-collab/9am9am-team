"""Usage de l'abonnement Claude, lu dans la sortie de `claude -p /usage`.

Lecture stricte : on ne garde que les lignes « Current session|week … : N% used [· resets …] ».
Si rien n'est lisible, on le dit (`ok: False`) — jamais un pourcentage plausible inventé.
"""
from __future__ import annotations

import re
from typing import Dict, List

WARN_PCT, LIMIT_PCT = 80, 100
_LINE = re.compile(r"^\s*Current (session|week)([^:\n]*):\s*(\d{1,4})%\s+used(?:\s*·\s*resets\s+(.+?))?\s*$",
                   re.MULTILINE)
_LABELS = {"session": "session", "week": "semaine"}


def _level(pct: int) -> str:
    return "limit" if pct >= LIMIT_PCT else "warn" if pct >= WARN_PCT else "ok"


def _meter(kind: str, qualifier: str, pct: str, reset: str) -> Dict[str, object]:
    value = min(int(pct), LIMIT_PCT)
    qual = qualifier.strip().strip("()").strip()
    label = _LABELS[kind] + (f" ({qual})" if qual and qual != "all models" else "")
    return {"label": label, "pct": value, "reset": (reset or "").strip(), "level": _level(value)}


def parse_usage(raw: object) -> Dict[str, object]:
    text = raw if isinstance(raw, str) else ""
    meters: List[Dict[str, object]] = [_meter(*m.groups("")) for m in _LINE.finditer(text)]
    if not meters:
        return {"ok": False, "error": "usage illisible : la sortie de « claude -p /usage » est inattendue."}
    full = next((m for m in meters if m["level"] == "limit"), None)
    return {"ok": True, "meters": meters, "limit_reached": full is not None,
            "limit_reset": str(full["reset"]) if full else ""}
