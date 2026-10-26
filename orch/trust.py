"""Ce que CE programme a créé ou lancé, mémorisé HORS du dossier de projet (trust.json, à
côté des réglages).

Un projet venu d'ailleurs peut apporter son `.mini-orch/` : son tasks.json n'est pas une
preuve. Seul ce registre dit (1) quels `.mini-orch/` ce programme a créés (jeton recopié dans
`.mini-orch/origine.json`) et (2) quels groupes de processus il a lancés : `recover_stale` ne
tue qu'un groupe inscrit ici, jamais un `owner.child_pgid` lu dans le projet.
"""
from __future__ import annotations

import secrets
from typing import Callable, Dict, Optional

from .config import Paths
from .fileio import MISSING, atomic_write_json, quarantine, read_json
from .lockfile import LockTimeout, lock_for

MARKER = "origine.json"  # dans .mini-orch/ : {"token": …} ; le même jeton est dans trust.json
ABSENT, OURS, FOREIGN = "absent", "ours", "foreign"


class TrustStore:
    def __init__(self, paths: Paths, log: Callable[[str], None]) -> None:
        self._paths, self._log = paths, log
        self._root = str(paths.root)
        self.root_name = paths.root.name or "?"  # pour les messages : jamais le chemin absolu
        self._lock = lock_for(paths.config_lock)  # même verrou que config.json (même dossier)

    # ---- fichier ------------------------------------------------------------
    def _load(self) -> Dict[str, dict]:
        """Registre ; illisible = mis de côté et vide : on ne fait alors confiance à RIEN
        (aucun groupe ne sera arrêté), jamais l'inverse — et on le dit."""
        try:
            data = read_json(self._paths.trust)
        except ValueError:
            kept = quarantine(self._paths.trust)
            self._log(f"trust.json illisible : conservé en {kept.name} ; aucun processus orphelin "
                      "ne sera arrêté sur la foi de l'ancien registre.")
            return {}
        if isinstance(data, dict):
            return data
        if data is not MISSING:
            self._log("trust.json n'est pas un objet JSON : ignoré (aucun processus ne sera arrêté).")
        return {}

    def _section(self, data: Dict[str, dict], name: str) -> Dict[str, object]:
        table = data.get(name)
        mine = table.get(self._root) if isinstance(table, dict) else None
        return mine if isinstance(mine, dict) else {}

    def _save(self, data: Dict[str, dict], name: str, mine: Dict[str, object]) -> None:
        table = data.get(name) if isinstance(data.get(name), dict) else {}
        others = {k: v for k, v in table.items() if k != self._root}
        atomic_write_json(self._paths.trust, {**data, name: {**others, self._root: mine} if mine else others})

    # ---- origine du dossier d'état ----------------------------------------
    def state_origin(self) -> str:
        """« absent » (pas encore de .mini-orch), « ours » (créé ici) ou « foreign »."""
        if not self._paths.state.exists():
            return ABSENT
        try:
            marker = read_json(self._paths.state / MARKER)
        except (ValueError, OSError):
            return FOREIGN
        token = marker.get("token") if isinstance(marker, dict) else None
        with self._lock.held():
            known = self._section(self._load(), "states").get("token")
        return OURS if isinstance(token, str) and token and token == known else FOREIGN

    def adopt(self) -> None:
        """Ce dossier d'état est désormais suivi par ce programme (nouveau jeton)."""
        token = secrets.token_hex(16)
        with self._lock.held():
            atomic_write_json(self._paths.state / MARKER, {"token": token})
            self._save(self._load(), "states", {"token": token})

    # ---- groupes de processus lancés --------------------------------------
    def remember_child(self, run_id: str, pgid: int, cmd: str) -> None:
        with self._lock.held():
            data = self._load()
            mine = {**self._section(data, "children"), run_id: {"pgid": pgid, "cmd": cmd}}
            self._save(data, "children", mine)

    def launched(self, run_id: str, pgid: int, cmd: str) -> bool:
        with self._lock.held():
            entry = self._section(self._load(), "children").get(run_id)
        return isinstance(entry, dict) and entry.get("pgid") == pgid and entry.get("cmd") == cmd

    def forget_dead_runs(self, is_live: Callable[[str], bool]) -> None:
        """Oublie les runs terminés (bail libre) : le registre ne grossit pas."""
        with self._lock.held():
            data = self._load()
            mine = self._section(data, "children")
            keep = {rid: v for rid, v in mine.items() if is_live(rid)}
            if keep != mine:
                self._save(data, "children", keep)


def follow_state(trust: TrustStore, origin: str, pending: Optional[int], log: Callable[[str], None]) -> None:
    """À l'ouverture d'un dossier : signale un `.mini-orch/` que ce programme n'a pas créé.
    Il n'est inscrit (avertissement tu) qu'une fois ses tâches en attente terminées : tant
    qu'elles sont là, on le redit à chaque ouverture. `origin` est lu AVANT que le journal ou
    les verrous ne créent `.mini-orch/`."""
    if origin == FOREIGN:
        count = "un nombre inconnu de" if pending is None else str(pending)
        log(f"Attention : le dossier d'état .mini-orch de « {trust.root_name} » n'a pas été créé par ce "
            "programme sur ce Mac (copié avec le projet, téléchargé, ou laissé par une version antérieure). "
            f"Ses tâches en attente ({count} tâche(s)) ne partent qu'à ton « Tick » ou « Run » : relis-les "
            "d'abord. Aucun processus ne sera arrêté sur la foi de ses fichiers. (Rappelé à chaque "
            "ouverture tant que ces tâches ne sont pas terminées.)")
    if origin == OURS or (origin == FOREIGN and pending != 0):
        return
    try:
        trust.adopt()
    except (OSError, LockTimeout) as e:
        log(f"Dossier d'état non inscrit ({e.__class__.__name__}) : cet avertissement reviendra "
            "à la prochaine ouverture.")
