"""Verrous : état partagé (tasks.json + config.json) et bail d'exécution d'une tâche.

`flock` est libéré par le noyau quand le process meurt, même par `kill -9` ou un
plantage machine : aucun verrou périmé à nettoyer à la main.
"""
from __future__ import annotations

import errno
import fcntl
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

from .fileio import secure_open, secure_unlink

LOCK_TIMEOUT_S = 10.0
LOCK_POLL_S = 0.05  # attente entre deux essais de flock


class LockTimeout(RuntimeError):
    pass


def _try_flock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as e:
        if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            return False
        raise


class StateLock:
    """Exclusif entre process (flock) et entre threads ; réentrant dans un thread."""

    def __init__(self, path: Path, timeout: float = LOCK_TIMEOUT_S) -> None:
        self._path, self._timeout = path, timeout
        self._rlock = threading.RLock()
        self._depth = 0
        self._fd: Optional[int] = None

    @contextmanager
    def held(self) -> Iterator[None]:
        if not self._rlock.acquire(timeout=self._timeout):
            raise LockTimeout("état occupé par une autre opération de ce programme depuis "
                              f"{self._timeout:.0f} s.")
        try:
            if self._depth == 0:
                self._fd = self._acquire_file()
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
                if self._depth == 0 and self._fd is not None:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                    os.close(self._fd)
                    self._fd = None
        finally:
            self._rlock.release()

    def _acquire_file(self) -> int:
        fd = secure_open(self._path, os.O_RDWR | os.O_CREAT)  # jamais à travers un lien
        deadline = time.monotonic() + self._timeout
        while not _try_flock(fd):
            if time.monotonic() >= deadline:
                os.close(fd)
                raise LockTimeout("état .mini-orch verrouillé par un autre process depuis "
                                  f"{self._timeout:.0f} s (interface ou Terminal en pleine écriture).")
            time.sleep(LOCK_POLL_S)
        return fd


_LOCKS: Dict[str, StateLock] = {}
_LOCKS_GUARD = threading.Lock()


def lock_for(path: Path) -> StateLock:
    """Un seul StateLock par fichier et par process (deux flock du même process
    sur deux descripteurs se bloqueraient mutuellement). Chemin absolu SANS résoudre les
    liens : `.mini-orch` en lien doit être refusé par secure_open, pas suivi ici."""
    key = os.path.abspath(str(path))
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            _LOCKS[key] = StateLock(Path(key))
        return _LOCKS[key]


class Lease:
    """Bail d'exécution d'une tâche : tenu pendant le worker et la supervision.

    Libre = le process propriétaire est mort (le noyau a relâché le flock)."""

    def __init__(self, path: Path, fd: int) -> None:
        self.path, self._fd = path, fd

    @classmethod
    def acquire(cls, path: Path) -> "Lease":
        fd = secure_open(path, os.O_RDWR | os.O_CREAT)
        if not _try_flock(fd):
            os.close(fd)
            raise LockTimeout(f"bail {path.name} déjà tenu.")
        os.write(fd, str(os.getpid()).encode())
        return cls(path, fd)

    @staticmethod
    def is_held(path: Path) -> bool:
        try:
            fd = secure_open(path, os.O_RDWR, create_dirs=False)
        except FileNotFoundError:
            return False
        try:
            if _try_flock(fd):
                fcntl.flock(fd, fcntl.LOCK_UN)
                return False
            return True
        finally:
            os.close(fd)

    def release(self, delete: bool = True) -> None:
        if self._fd is None:
            return
        if delete:
            secure_unlink(self.path)
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None
