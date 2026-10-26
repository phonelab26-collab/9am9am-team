"""« Changer de compte » : déconnexion puis `claude auth login --email …`, lancés par l'app.

L'app ne voit JAMAIS de mot de passe ni de jeton : le navigateur s'ouvre (c'est `claude` qui le fait)
et la validation s'y passe. Ici, seulement l'enchaînement, son suivi et son annulation.

Garde-fous :
- l'adresse est validée strictement (jamais une option : `--sso`, `-x@…`) et passée comme argument
  séparé, sans shell ;
- les commandes tournent dans le dossier personnel, jamais dans le projet ouvert (son `.claude/`
  — hooks… — n'est pas lu) ;
- une déconnexion qui échoue n'est ignorée que si le compte est DÉJÀ déconnecté (sinon on s'arrête :
  pas de connexion lancée par-dessus un état inconnu) ;
- rien de ce que dit la commande n'est affiché sans masquage des secrets, et aucune adresse e-mail
  n'est écrite dans le journal.
"""
from __future__ import annotations

import re
import threading
from typing import Callable, Dict, Optional, Tuple

from .procs import CmdResult, run_cmd
from .redact import redact

LOGOUT_TIMEOUT_S = 15    # `claude auth logout` : instantané ; au-delà, quelque chose bloque
LOGIN_TIMEOUT_S = 300    # le temps de valider dans le navigateur ; au-delà, on abandonne
DETAIL_MAX = 300         # caractères d'explication gardés d'une commande en échec
EMAIL_MAX = 254          # longueur maximale d'une adresse (RFC 5321)

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_EMAIL = re.compile(rf"[A-Za-z0-9][A-Za-z0-9._%+-]{{0,63}}@{_LABEL}(?:\.{_LABEL})*\.[A-Za-z]{{2,}}")
_ANY_EMAIL = re.compile(r"[\w.%+-]+@[\w.-]+")

IDLE, RUNNING, DONE, FAILED, CANCELLED = "idle", "running", "done", "failed", "cancelled"
MANUAL_HINT = "Si aucun navigateur ne s'est ouvert, utilise « Copier la commande » et colle-la dans ton Terminal."


def clean_email(value: object) -> Optional[str]:
    """L'adresse nettoyée, ou None. Jamais une option (« --sso », « -x@… ») ni plusieurs mots."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if len(text) <= EMAIL_MAX and _EMAIL.fullmatch(text) else None


def _masked(text: str) -> str:
    """Pour le journal : aucune adresse e-mail n'y est écrite."""
    return _ANY_EMAIL.sub("***@***", text)


def _detail(res: CmdResult) -> str:
    why = res.err.strip() or res.out.strip() or f"code {res.code}, aucune sortie"
    return redact(why)[:DETAIL_MAX]


class AccountSwitch:
    """Un changement de compte à la fois, dans un fil d'arrière-plan, annulable."""

    def __init__(self, claude_bin: Callable[[], str], status: Callable[[], dict],
                 log: Callable[[str], None], cwd: Callable[[], str],
                 runner: Optional[Callable[..., CmdResult]] = None,
                 on_finish: Callable[[], None] = lambda: None) -> None:
        self._claude_bin, self._status, self._log, self._cwd = claude_bin, status, log, cwd
        self._runner, self._on_finish = runner, on_finish
        self._lock = threading.Lock()
        self._view: Dict[str, str] = {"state": IDLE, "step": "", "message": ""}
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self._closed = False  # l'app se ferme : plus aucun changement ne démarre

    # ---- état -------------------------------------------------------------
    @property
    def running(self) -> bool:
        with self._lock:
            return self._view["state"] == RUNNING

    def view(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._view)

    def _set(self, state: str, step: str = "", message: str = "") -> None:
        with self._lock:
            self._view = {"state": state, "step": step, "message": message}

    # ---- commandes ----------------------------------------------------------
    def start(self, email: object) -> Tuple[bool, str]:
        """Lance le changement. (False, raison) si l'adresse est invalide ou si un changement tourne."""
        address = clean_email(email)
        if address is None:
            return False, "adresse e-mail invalide : écris-la sous la forme prenom@exemple.com."
        with self._lock:
            if self._closed:
                return False, "l'application se ferme : changement de compte non lancé."
            if self._view["state"] == RUNNING:
                return False, "un changement de compte est déjà en cours : attends sa fin ou annule-le."
            self._view = {"state": RUNNING, "step": "logout", "message": "Déconnexion du compte actuel…"}
            self._cancel = threading.Event()
            cancel = self._cancel
            self._thread = threading.Thread(target=self._work, args=(address, cancel),
                                            name="account-switch", daemon=True)
            self._thread.start()
        return True, ""

    def cancel(self) -> bool:
        """Demande l'arrêt ; False s'il n'y avait rien à arrêter."""
        with self._lock:
            if self._view["state"] != RUNNING:
                return False
            self._cancel.set()
        return True

    def close(self, timeout: float) -> bool:
        """Fermeture de l'app : annule et attend (aucun `claude auth login` orphelin)."""
        with self._lock:
            self._closed = True
        self.cancel()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return not self.running

    # ---- déroulé ------------------------------------------------------------
    def _run(self, argv, timeout: float, cancel: threading.Event) -> CmdResult:
        runner = self._runner or run_cmd  # cherché à l'appel : substituable par les tests
        return runner(argv, timeout=timeout, cwd=self._cwd(), cancel=cancel)

    def _work(self, email: str, cancel: threading.Event) -> None:
        try:
            self._log("Changement de compte Claude : déconnexion…")
            if not self._logout(cancel):
                return
            self._login(email, cancel)
        except Exception as e:  # noqa: BLE001 — jamais un fil mort sans le dire
            self._fail(f"erreur interne : {e.__class__.__name__}")
        finally:
            self._on_finish()

    def _logout(self, cancel: threading.Event) -> bool:
        res = self._run([self._claude_bin(), "auth", "logout"], LOGOUT_TIMEOUT_S, cancel)
        if res.cancelled:
            self._finish(CANCELLED, "Annulé pendant la déconnexion.", "annulé (déconnexion)")
            return False
        if res.ok:
            return True
        status = self._status()
        if status.get("loggedIn") is False:  # déjà déconnecté : rien à défaire, on continue
            return True
        self._fail(f"déconnexion impossible : {_detail(res)}")
        return False

    def _login(self, email: str, cancel: threading.Event) -> None:
        self._set(RUNNING, "login", "Navigateur ouvert : valide la connexion (5 min au plus). "
                                    "Rien n'est demandé ici.")
        self._log("Changement de compte Claude : connexion lancée, validation dans le navigateur.")
        res = self._run([self._claude_bin(), "auth", "login", "--email", email], LOGIN_TIMEOUT_S, cancel)
        if res.cancelled:
            self._finish(CANCELLED, "Connexion annulée : le compte précédent est déconnecté, aucun compte "
                                    "n'est actif. Relance « Changer de compte ».", "annulé (connexion)")
        elif res.timed_out:
            self._fail(f"délai dépassé ({LOGIN_TIMEOUT_S // 60} min) : le navigateur n'a pas validé la "
                       f"connexion. {MANUAL_HINT}", signed_out=True)
        elif not res.ok:
            self._fail(f"connexion refusée : {_detail(res)}. {MANUAL_HINT}", signed_out=True)
        else:
            self._verify(email)

    def _verify(self, asked: str) -> None:
        """La commande a réussi : on relit le compte réel, on ne le suppose pas."""
        status = self._status()
        if not status.get("ok"):
            self._fail("la connexion s'est terminée mais aucun compte actif n'est visible : "
                       f"{redact(str(status.get('error') or 'état inconnu'))[:DETAIL_MAX]}", signed_out=True)
            return
        got = str(status.get("email") or "")
        plan = f" · {status['plan']}" if status.get("plan") else ""
        if got.lower() == asked.lower():
            self._finish(DONE, f"Connecté : {got}{plan}.", "terminé")
        else:
            self._finish(DONE, f"Connecté en tant que {got or 'un compte sans adresse'}{plan} — différent "
                               f"de l'adresse demandée ({asked}).", "terminé (autre compte choisi)")

    def _fail(self, why: str, signed_out: bool = False) -> None:
        """`signed_out` : la déconnexion a réussi avant l'échec, donc plus aucun compte n'est actif."""
        tail = " Le compte précédent est déconnecté : aucun compte n'est actif." if signed_out else ""
        # La raison va aussi au journal (secrets masqués, adresse retirée par _finish) : la vue
        # en mémoire est perdue au redémarrage, le journal reste.
        self._finish(FAILED, f"Changement de compte impossible : {why}{tail}", f"échec ({redact(why)})")

    def _finish(self, state: str, message: str, journal: str) -> None:
        self._set(state, "", message)
        self._log(_masked(f"Changement de compte Claude : {journal}."))
