"""Conversation avec le CEO : messages persistés dans .mini-orch/chat.json.

Trois sortes de lignes : `user` (toi), `ceo` (Claude Code te répond) et `event`
(activité de l'équipe : plan, tâche lancée, résultat, vérification). Les `id`
croissent toujours, ce qui permet à la page de ne demander que les nouveautés.
"""
from __future__ import annotations

import time
from typing import Callable, List, Optional

from .config import Paths
from .fileio import MISSING, UnsafePath, atomic_write_json, quarantine, read_json
from .lockfile import lock_for

MAX_MESSAGES = 500
TEXT_MAX = 6000
HISTORY_ROLES = ("user", "ceo")


class ChatStore:
    def __init__(self, paths: Paths, log: Callable[[str], None]) -> None:
        self._paths, self._log = paths, log
        self._lock = lock_for(paths.lock)
        self._unsafe_said = False  # lien planté dans .mini-orch : dit une fois, pas à chaque lecture

    def _read(self, for_write: bool) -> List[dict]:
        try:
            data = read_json(self._paths.chat)
        except UnsafePath as e:
            if for_write:
                raise
            if not self._unsafe_said:
                self._unsafe_said = True
                self._log(f"Conversation illisible : {e.strerror}")
            return []
        except ValueError:
            if for_write:  # on ne réécrit jamais par-dessus un fichier illisible sans le garder
                kept = quarantine(self._paths.chat)
                self._log(f"chat.json illisible : conservé en {kept.name}, nouvelle conversation.")
            return []
        if data is MISSING or not isinstance(data, list):
            return []
        return [m for m in data if isinstance(m, dict)]

    def add(self, role: str, text: object, kind: str = "", data: Optional[dict] = None) -> dict:
        if not self._paths.root.is_dir():  # dossier supprimé : pas de résurrection par une écriture
            raise FileNotFoundError("dossier de travail introuvable (supprimé ou déplacé)")
        with self._lock.held():
            msgs = self._read(True)
            last = max((int(m.get("id") or 0) for m in msgs), default=0)
            msg = {"id": last + 1, "role": role, "text": str(text or "")[:TEXT_MAX],
                   "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            if kind:
                msg["kind"] = kind
            if data:
                msg["data"] = data
            atomic_write_json(self._paths.chat, (msgs + [msg])[-MAX_MESSAGES:])
            return msg

    def since(self, after_id: int = 0) -> List[dict]:
        return [m for m in self._read(False) if int(m.get("id") or 0) > after_id]

    def recent(self, n: int) -> List[dict]:
        """Les n derniers échanges (toi + le CEO), sans le bruit des événements."""
        return [m for m in self._read(False) if m.get("role") in HISTORY_ROLES][-n:]

    def last_id(self) -> int:
        """Dernier id présent (0 = conversation vide) : la page détecte un retour en arrière."""
        return max((int(m.get("id") or 0) for m in self._read(False)), default=0)
