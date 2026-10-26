"""Journal horodaté sur disque (.mini-orch/journal.log) + écho (console ou interface).

Jamais ouvert à travers un lien symbolique (secure_open) ; caractères de contrôle neutralisés :
un retour chariot ou une séquence d'échappement venue d'un titre ne réécrit pas l'affichage."""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Callable, Optional

from .config import Paths
from .fileio import UnsafePath, secure_open
from .lockfile import LockTimeout, lock_for

MAX_BYTES = 1_000_000
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # tout sauf \t et \n


class Journal:
    """Appelable : `journal("message")`. Sûr entre threads ; append entre process."""

    def __init__(self, paths: Paths, echo: Optional[Callable[[str], None]] = None) -> None:
        self._path, self._echo, self._root = paths.journal, echo, paths.root
        self._guard = threading.Lock()
        self._broken = False
        self._rotate(paths)

    def _rotate(self, paths: Paths) -> None:
        # Rotation « au mieux » : verrou occupé (LockTimeout) ou disque qui refuse ne
        # doivent JAMAIS empêcher le programme de démarrer.
        try:
            with lock_for(paths.lock).held():
                if self._path.exists() and self._path.stat().st_size > MAX_BYTES:
                    self._path.replace(self._path.with_name(self._path.name + ".1"))
        except (OSError, LockTimeout) as e:
            self._warn(e)

    def _warn(self, e: Exception) -> None:
        if not self._broken:
            self._broken = True
            if self._echo:
                why = f" — {e.strerror}" if isinstance(e, UnsafePath) else (
                    " — dossier de travail supprimé ou déplacé" if isinstance(e, FileNotFoundError) else "")
                self._echo(f"(journal.log inaccessible : {e.__class__.__name__}{why} ; "
                           "le journal n'est plus enregistré sur disque)")

    def __call__(self, msg: str) -> None:
        if self._echo:
            self._echo(msg)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        clean = _CONTROL.sub("?", msg.rstrip())
        line = f"{stamp} {clean}".replace("\n", "\n    ") + "\n"
        with self._guard:
            if not self._root.is_dir():  # dossier de travail supprimé : on ne le ressuscite pas
                self._warn(FileNotFoundError("dossier de travail introuvable"))
                return
            try:
                fd = secure_open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                try:
                    os.write(fd, line.encode("utf-8"))
                finally:
                    os.close(fd)
            except OSError as e:
                self._warn(e)
