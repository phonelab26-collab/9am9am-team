"""Models gratuits d'opencode, scannés à chaque ouverture de l'interface.

`opencode models` ne donne ni prix ni drapeau « gratuit » : un model est dit gratuit
quand son nom le dit (segment « free » : « -free », « :free », « /free/ »), quand son
fournisseur le dit (« browserfree/… »), ou s'il fait partie des gratuits connus sans ce
mot (FREE_MODELS, ex. opencode/big-pickle). Aucun prix n'est deviné.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from .config import FREE_MODELS, clean_custom_model
from .procs import CmdResult, run_cmd

SCAN_TIMEOUT_S = 120  # `opencode models` : le 1er lancement peut réindexer
MAX_FREE_MODELS = 200
ERROR_MAX = 300
_SEGMENT_RE = re.compile(r"[/:._-]")


def is_free_model(name: object) -> bool:
    model = clean_custom_model(name)
    if not model:
        return False
    if model in FREE_MODELS:
        return True
    lowered = model.lower()
    return "free" in _SEGMENT_RE.split(lowered) or lowered.split("/", 1)[0].endswith("free")


def free_models(catalog_out: str) -> List[str]:
    """Sortie de `opencode models` → models gratuits, ceux d'opencode (Zen) d'abord."""
    found = {line.strip() for line in catalog_out.splitlines()}
    free = sorted((m for m in found if is_free_model(m)),
                  key=lambda m: (not m.startswith("opencode/"), m))
    return free[:MAX_FREE_MODELS]


class FreeModelScanner:
    """Un scan à la fois, en arrière-plan ; `view()` donne l'état pour la page."""

    def __init__(self, opencode_bin: Callable[[], str], cwd: str, log: Callable[[str], None],
                 runner: Callable[..., CmdResult] = run_cmd,
                 clock: Callable[[], float] = time.time) -> None:
        self._bin, self._cwd, self._log = opencode_bin, cwd, log
        self._run, self._clock = runner, clock
        self._lock = threading.Lock()
        self._state: Dict[str, object] = {"status": "idle", "models": [], "error": "", "at": ""}
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()  # fermeture du logiciel : pas d'`opencode` orphelin

    def view(self) -> dict:
        with self._lock:
            return {**self._state, "models": list(self._state["models"])}  # type: ignore[call-overload]

    def skip(self, reason: str) -> None:
        with self._lock:
            self._state = {"status": "off", "models": [], "error": reason, "at": ""}

    def start(self) -> bool:
        """Lance un scan en arrière-plan ; refuse si un scan tourne déjà."""
        if not self._begin():
            return False
        self._thread = threading.Thread(target=self._finish, name="free-models-scan", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._cancel.set()

    def join(self, timeout: float) -> bool:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return thread is None or not thread.is_alive()

    def scan(self) -> dict:
        """Scan synchrone (tests, ligne de commande)."""
        if self._begin():
            self._finish()
        return self.view()

    def _begin(self) -> bool:
        with self._lock:
            if self._state["status"] == "scanning":
                return False
            self._state = {**self._state, "status": "scanning", "error": ""}
            return True

    def _finish(self) -> None:
        try:
            models, error = self._read_catalog()
        except Exception as e:  # le thread ne doit jamais mourir en laissant « scanning »
            models, error = None, f"{e.__class__.__name__} : {e}"
        at = time.strftime("%H:%M", time.localtime(self._clock()))
        with self._lock:
            if models is None:
                self._state = {**self._state, "status": "error", "error": error[:ERROR_MAX], "at": at}
            else:
                self._state = {"status": "ok", "models": models, "error": "", "at": at}
        if models is None:
            self._log(f"Scan des models gratuits opencode impossible : {error[:ERROR_MAX]}")
        else:
            self._log(f"Scan opencode : {len(models)} models gratuits trouvés.")

    def _read_catalog(self) -> Tuple[Optional[List[str]], str]:
        binary = self._bin()  # relu à chaque scan : opencode a pu être installé entre-temps
        if not binary:
            return None, "opencode introuvable : installe OpenCode (ou règle MINI_ORCH_OPENCODE)."
        res = self._run([binary, "models"], timeout=SCAN_TIMEOUT_S, cwd=self._cwd, cancel=self._cancel)
        if res.ok and not any(clean_custom_model(ln) for ln in res.out.splitlines()):
            return None, "catalogue opencode vide ou illisible (aucun « provider/model »)."
        if res.ok:
            return free_models(res.out), ""
        if res.cancelled:
            return None, "scan annulé (fermeture)."
        if res.timed_out:
            return None, f"délai dépassé ({SCAN_TIMEOUT_S} s)."
        return None, res.err.strip() or res.out.strip() or f"code {res.code}"
