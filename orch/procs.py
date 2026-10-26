"""Lancement des CLI enfants (claude, opencode, commandes autoclaw/cline).

Règles apprises à la dure :
- stdin FERMÉ : `opencode run` attend l'EOF d'un stdin non-terminal (gel à vie).
- stdout et stderr séparés : un avertissement sur stderr ne doit pas casser le JSON.
- l'enfant a son propre groupe de processus, tué en entier au délai, à
  l'annulation, à Ctrl-C et en fin de run : aucun petit-enfant orphelin ;
- un descendant qui a quitté le groupe (`setsid`, démon auto-relancé…) peut
  retenir les tuyaux : la vidange après kill est BORNÉE (FINAL_DRAIN_S), jamais
  infinie — sans quoi délai et « Arrêter » ne rendraient plus jamais la main ;
- la sortie est lue au fil de l'eau et seule la FIN est gardée (TAIL_BYTES par flux) :
  `communicate()` gardait tout en mémoire jusqu'au bout (3,5 Go en 4 s avec `yes`).
"""
from __future__ import annotations

import os
import selectors
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Mapping, Optional, Tuple

POLL_S = 0.25
GRACE_S = 2.0  # l'enfant est sorti mais un petit-enfant garde les pipes ouverts
KILL_WAIT_S = 0.5
FINAL_DRAIN_S = 3.0  # après kill : attente bornée de la fermeture des tuyaux
OUT_MAX = 2_000_000  # caractères gardés (la fin) par flux
TAIL_BYTES = OUT_MAX * 4  # octets gardés par flux : de quoi décoder OUT_MAX caractères UTF-8
READ_CHUNK = 65536
CWD_MISSING = 125  # le dossier de travail n'existe plus (supprimé ou déplacé pendant le run)


def _cwd_missing(cwd: str) -> str:
    return (f"dossier de travail introuvable : « {Path(cwd).name} » a été supprimé ou déplacé. "
            "Rouvre un dossier existant.")


@dataclass(frozen=True)
class CmdResult:
    code: int
    out: str
    err: str
    timed_out: bool = False
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out and not self.cancelled


def _pgid_of(p: "subprocess.Popen") -> int:
    """PGID réel de l'enfant (pas de supposition `pgid == pid`).

    `start_new_session=True` le rend leader de session dans le cas normal,
    mais un binaire setuid/wrapper peut changer la donne : on lit le vrai
    pgid, avec repli sur le pid si la lecture échoue (process déjà sorti)."""
    try:
        return os.getpgid(p.pid)
    except (OSError, ProcessLookupError):
        return p.pid


def kill_group(pgid: int) -> None:
    """SIGTERM au groupe, puis SIGKILL aux survivants. Groupe absent : rien à faire."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        except PermissionError:  # pgid réattribué à un autre utilisateur : pas à nous
            return
        if sig == signal.SIGTERM:
            time.sleep(KILL_WAIT_S)


def _decode(data: Optional[bytes]) -> str:
    text = (data or b"").decode("utf-8", errors="replace")
    return text[-OUT_MAX:]


def _stop_reason(p: "subprocess.Popen", deadline: float, cancel: Optional[threading.Event],
                 exited_at: Optional[float]) -> Tuple[Optional[str], Optional[float]]:
    """Faut-il arrêter le groupe ? ("cancel" | "timeout" | "grace" | None, exited_at)."""
    now = time.monotonic()
    if cancel is not None and cancel.is_set():
        return "cancel", exited_at
    if now >= deadline:
        return "timeout", exited_at
    if p.poll() is None:
        return None, exited_at
    # L'enfant est sorti mais un petit-enfant garde les pipes : délai de grâce.
    exited_at = exited_at or now
    return ("grace" if now - exited_at >= GRACE_S else None), exited_at


class _Tail:
    """Garde au plus `limit` octets : la FIN du flux (là où sont conclusion et erreurs)."""

    def __init__(self, limit: int) -> None:
        self._chunks: "deque[bytes]" = deque()
        self._size, self._limit = 0, limit

    def add(self, data: bytes) -> None:
        self._chunks.append(data)
        self._size += len(data)
        while self._chunks and self._size - len(self._chunks[0]) >= self._limit:
            self._size -= len(self._chunks.popleft())

    def value(self) -> bytes:
        return b"".join(self._chunks)[-self._limit:]


class _Reader:
    """Lit stdout et stderr dans le même fil (pas de thread) sans jamais bloquer."""

    def __init__(self, p: "subprocess.Popen") -> None:
        self._sel = selectors.DefaultSelector()
        self.tails = (_Tail(TAIL_BYTES), _Tail(TAIL_BYTES))
        self._streams = [s for s in (p.stdout, p.stderr) if s is not None]
        for stream, tail in zip((p.stdout, p.stderr), self.tails):
            if stream is not None:
                self._sel.register(stream, selectors.EVENT_READ, tail)

    @property
    def open(self) -> bool:
        return bool(self._sel.get_map())

    def pump(self, wait: float) -> None:
        """Lit ce qui est disponible pendant au plus `wait` secondes ; EOF → flux fermé."""
        if not self.open:
            time.sleep(wait)
            return
        for key, _ in self._sel.select(timeout=wait):
            data = os.read(key.fd, READ_CHUNK)
            if data:
                key.data.add(data)
            else:
                self._sel.unregister(key.fileobj)

    def close(self) -> Tuple[bytes, bytes]:
        """Ferme NOS extrémités (un descendant détaché peut garder les siennes) et rend la fin lue."""
        self._sel.close()
        for stream in self._streams:
            stream.close()
        return self.tails[0].value(), self.tails[1].value()


def _wait(p: "subprocess.Popen", timeout: float,
          cancel: Optional[threading.Event]) -> Tuple[bytes, bytes, Optional[str]]:
    deadline, exited_at = time.monotonic() + timeout, None
    reader = _Reader(p)
    try:
        while True:
            reader.pump(POLL_S)
            if not reader.open and p.poll() is not None:
                return (*reader.close(), None)
            reason, exited_at = _stop_reason(p, deadline, cancel, exited_at)
            if reason is not None:
                kill_group(_pgid_of(p))
                _final_drain(reader)
                return (*reader.close(), reason)
    except BaseException:
        reader.close()
        raise


def _final_drain(reader: _Reader) -> None:
    """Le groupe vient d'être tué : lire ce qui reste, mais un temps BORNÉ.

    Un descendant détaché du groupe (`setsid`) resté vivant peut garder les tuyaux sans fin :
    sans borne, délai dépassé et « Arrêter » ne rendraient plus la main. Au-delà, on garde
    ce qui a déjà été lu (l'enfant, lui, a bien été tué)."""
    deadline = time.monotonic() + FINAL_DRAIN_S
    while reader.open and time.monotonic() < deadline:
        reader.pump(POLL_S)


def run_cmd(cmd: List[str], timeout: float, cwd: Optional[str] = None,
            cancel: Optional[threading.Event] = None,
            on_spawn: Optional[Callable[[int], None]] = None,
            env: Optional[Mapping[str, str]] = None) -> CmdResult:
    name = Path(cmd[0]).name
    if cancel is not None and cancel.is_set():  # arrêt déjà demandé : rien à lancer
        return CmdResult(130, "", "", cancelled=True)
    if cwd is not None and not os.path.isdir(cwd):
        return CmdResult(CWD_MISSING, "", _cwd_missing(cwd))
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, cwd=cwd, env=env, start_new_session=True)
    except FileNotFoundError:
        if cwd is not None and not os.path.isdir(cwd):  # disparu entre le test et le lancement
            return CmdResult(CWD_MISSING, "", _cwd_missing(cwd))
        return CmdResult(127, "", f"binaire introuvable : {name}")
    except PermissionError:
        return CmdResult(126, "", f"binaire non exécutable : {name}")
    with p:
        try:
            if on_spawn is not None:
                on_spawn(p.pid)  # nouvelle session : pgid == pid
            out, err, reason = _wait(p, timeout, cancel)
        except BaseException:
            kill_group(p.pid)
            try:
                p.wait(timeout=KILL_WAIT_S * 10)  # borné : un process bloqué sur le
            except subprocess.TimeoutExpired:      # disque ne doit pas figer le thread
                pass
            raise
        kill_group(p.pid)  # petits-enfants restés dans le groupe après une sortie normale
    timed_out, cancelled = reason == "timeout", reason == "cancel"
    code = 124 if timed_out else 130 if cancelled else p.returncode
    return CmdResult(code, _decode(out), _decode(err), timed_out, cancelled)
