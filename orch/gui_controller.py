"""Logique de l'interface : jobs, état, et FAÇADE vers le moteur.

`WebApp` (webui.py) ne parle qu'à cette classe — jamais à `engine.*`.
Tout ce que la couche web veut savoir du domaine passe par ici, sous forme
de vues (DTO) : si le moteur change, seul ce fichier bouge.
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .account import AccountSwitch
from .ceo import CEO_MODEL_SUGGESTIONS
from .cli import build
from .config import ConfigStore, Paths
from .engine import Engine, task_line
from .free_models import FreeModelScanner
from .lockfile import LockTimeout
from .procs import CmdResult, run_cmd
from .redact import redact
from .escalation import CLAUDE_WORKER, LADDER
from .registry import spec_for
from .store import StoreError
from .usage import parse_usage
from .workspace_root import pick_folder, validate_workspace
from .textutil import tail

Job = Callable[[threading.Event], int]
_new_cancel = threading.Event  # point d'injection pour les tests
AUTH_TIMEOUT_S = 15  # `claude auth status` : pas d'attente indéfinie
USAGE_TIMEOUT_S = 45  # `claude -p /usage` : ≈ 7 s mesurées, large marge
SCAN_JOIN_S = 3.0  # attente bornée de la fin du scan à la fermeture
SELFTEST_TIMEOUT_S = 900  # la suite en sous-process (≈1 min mesurées : large marge)
CEO_SETTING_KEYS = ("ceo_mode", "ceo_cloud_session", "ceo_cloud_repo", "ceo_model", "ceo_effort")


def _account_cwd() -> str:
    """Où lancer `claude auth …` et `claude -p /usage` : ils lisent le `.claude/` (hooks…) du
    dossier courant — jamais celui du projet ouvert, qui peut venir d'ailleurs."""
    return str(Path.home())


class GuiController:
    def __init__(self, root: Path, dry_run: bool,
                 runner: Callable[..., CmdResult] = run_cmd) -> None:
        # root = dossier du logiciel (code, selftest, réglages globaux) ; le dossier de
        # travail (CEO, agents, tâches, conversation) se choisit et peut en différer.
        self.root, self.dry_run, self._run = Path(root), dry_run, runner
        self.messages: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self._settings_dir = Paths.for_root(self.root).state
        from .config import Bins
        self.on_account_change: Callable[[], None] = lambda: None  # la couche web y vide ses caches
        self.account = AccountSwitch(
            claude_bin=lambda: Bins.resolve().claude, status=lambda: self.auth_status(),
            log=lambda msg: self.log(msg), cwd=_account_cwd,
            on_finish=lambda: self.on_account_change())
        self.free_models = FreeModelScanner(lambda: Bins.resolve().opencode, str(self.root), self._echo, runner)
        self.engine, self.log = build(self._initial_workspace(), self._echo, self._settings_dir,
                                      self._scanned_models)
        self._recover_at_startup()
        self.cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._label, self._started = "", 0.0
        self._start_guard = threading.Lock()  # deux clics simultanés = un seul job
        self._pick_guard = threading.Lock()   # un seul sélecteur de dossier ouvert à la fois
        self._ws_gen = 0  # change à chaque bascule : les pages ouvertes savent qu'il faut recharger

    def _scanned_models(self) -> List[str]:
        return list(self.free_models.view()["models"])

    def scan_free_models(self) -> bool:
        """Relit le catalogue opencode en arrière-plan (à l'ouverture, ou « Rescanner »).
        En démo, aucun CLI n'est lancé : on le dit au lieu d'inventer une liste."""
        if self.dry_run:
            self.free_models.skip("mode démo : scan non lancé.")
            return False
        return self.free_models.start()

    def _echo(self, msg: str) -> None:
        self.messages.put(("log", msg))

    def _initial_workspace(self) -> Path:
        """Dossier de travail mémorisé s'il est encore utilisable, sinon celui du logiciel (dit)."""
        saved = ConfigStore(Paths.for_root(self.root), self._echo).workspace()
        if not saved:
            return self.root
        path, err = validate_workspace(saved)
        if path is None:
            self._echo(f"Dossier de travail mémorisé inutilisable ({err}) : retour au dossier du logiciel.")
            return self.root
        return path

    def _recover_at_startup(self) -> None:
        """Reprise au lancement : une tâche dont le process est mort (kill -9, coupure,
        plantage) repasse en file et son worker orphelin est arrêté, sans attendre un
        « Tick ». Un état inutilisable est dit, mais ne bloque jamais l'interface."""
        try:
            self.engine.recover()
        except (StoreError, LockTimeout, OSError) as e:  # OSError : dont UnsafePath (lien planté)
            self._echo(f"Reprise des tâches interrompues impossible pour l'instant : {e}")

    def switch_workspace(self, raw: object) -> Tuple[bool, str]:
        """Bascule CEO, agents, tâches et conversation sur un autre dossier ; réglages conservés."""
        with self._start_guard:  # aucun travail ne peut démarrer pendant la bascule
            if self.busy:
                return False, "un travail est en cours : attends sa fin ou clique sur « Arrêter »."
            path, err = validate_workspace(raw)
            if path is None:
                return False, err
            try:  # mémorisé AVANT de basculer : un échec laisse l'ancien dossier intact
                self.engine.cfg.update(workspace=str(path))
            except (OSError, RuntimeError) as e:
                return False, f"dossier non mémorisé ({e.__class__.__name__} : {e})."
            self.engine, self.log = build(path, self._echo, self._settings_dir, self._scanned_models)
            self._ws_gen += 1
        self.log(f"Dossier de travail : {path}")
        return True, ""

    def pick_workspace(self) -> dict:
        """Sélecteur de dossiers du Mac puis bascule. Annuler n'est pas une erreur."""
        if self.busy:
            return {"ok": False, "error": "un travail est en cours : attends sa fin ou clique sur « Arrêter »."}
        if not self._pick_guard.acquire(blocking=False):
            return {"ok": False, "error": "le sélecteur de dossier est déjà ouvert."}
        try:
            chosen, err = pick_folder(self._run)
        finally:
            self._pick_guard.release()
        if chosen is None:
            return {"ok": False, "error": err} if err else {"ok": False, "cancelled": True}
        ok, err = self.switch_workspace(chosen)
        return {"ok": True, "root": self.workspace_root.name} if ok else {"ok": False, "error": err}

    @property
    def job_label(self) -> str:
        return self._label if self.busy else ""

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, label: str, job: Job) -> bool:
        """Lance `job(cancel)` en arrière-plan ; refuse s'il y en a déjà un."""
        with self._start_guard:
            if self.busy or self.account.running:  # un changement de compte ne se fait pas pendant un travail
                return False
            self._launch(label, job)
            return True

    def _launch(self, label: str, job: Job) -> None:
        self.cancel = _new_cancel()
        self._label, self._started = label, time.monotonic()
        cancel = self.cancel

        def wrapper() -> None:
            rc = 1
            try:
                rc = job(cancel)
            except Exception as e:  # noqa: BLE001 — affiché dans l'interface, jamais avalé
                self.log(f"ERREUR ({label}) : {e.__class__.__name__}: {e}")
                # Le journal est replié : la conversation doit le dire aussi, sinon on croit
                # que le CEO travaille encore alors que rien ne se passe.
                self.engine.say_error(f"Erreur interne pendant « {label} » : {e.__class__.__name__} : "
                                      f"{redact(str(e))[:300]}. Le travail s'est arrêté là ; le détail est "
                                      "dans le journal technique.")
            finally:
                self.messages.put(("done", rc))
        # daemon : la fermeture annule et attend déjà le job (close) ; si, par
        # extraordinaire, il restait coincé, le process ne doit pas survivre sans
        # interface. L'écriture atomique + la reprise au lancement suivant couvrent ce cas.
        self._thread = threading.Thread(target=wrapper, name=f"job-{label}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self.busy and not self.cancel.is_set():
            self.cancel.set()
            self.log("Arrêt demandé : le process en cours est interrompu, la tâche repasse en file.")

    def close(self, timeout: float) -> bool:
        """Fermeture : annule le job et l'attend (pas d'orphelin, pas d'in_progress)."""
        self.stop()
        account_stopped = self.account.close(timeout)  # aucun `claude auth login` orphelin
        self.free_models.stop()
        self.free_models.join(SCAN_JOIN_S)  # pas d'`opencode models` vivant après la fermeture
        if self._thread is not None:
            self._thread.join(timeout)
        return account_stopped and not self.busy

    def progress_text(self) -> str:
        if not self.busy:
            return "Prêt."
        state = "arrêt en cours" if self.cancel.is_set() else "en cours"
        return f"{self._label} : {state} depuis {int(time.monotonic() - self._started)} s…"

    # ---- jobs --------------------------------------------------------------
    def plan_job(self, goal: str) -> Job:
        return lambda cancel: self.engine.plan(goal, self.dry_run, cancel)

    def run_job(self, model: str) -> Job:
        return lambda cancel: self.engine.run_next(None, model or None, self.dry_run, cancel=cancel).rc

    def tick_job(self, goal: str, model: str) -> Job:
        return lambda cancel: self.engine.tick(goal, None, model or None, self.dry_run, cancel)

    def chat_job(self, message: str, attachments: str = "") -> Job:
        return lambda cancel: self.engine.converse(message, self.dry_run, cancel, attachments)

    def report_job(self, goal: str) -> Job:
        return lambda cancel: self.engine.report(goal, self.dry_run, cancel)

    def retry_job(self) -> Job:
        return lambda cancel: self.engine.retry()

    def selftest_job(self, cancel: threading.Event) -> int:
        # Sous-process : la suite ne partage aucun état avec l'interface.
        self.log("Selftest lancé (≈ 1 min)…")
        res = self._run([sys.executable, str(self.root / "mini_orchestrateur.py"), "selftest"],
                        timeout=SELFTEST_TIMEOUT_S, cwd=str(self.root), cancel=cancel)
        self.log(tail(res.out + ("\n" + res.err if res.err.strip() else ""), 4000))
        lines = [ln for ln in (res.out + "\n" + res.err).strip().splitlines() if ln.strip()]
        last = lines[-1].strip()[:200] if lines else "aucune sortie"
        if res.ok:
            text = f"Selftest réussi. {last}"
        elif res.cancelled:
            text = "Selftest arrêté à ta demande."
        elif res.timed_out:
            text = f"Selftest échoué : délai dépassé ({SELFTEST_TIMEOUT_S} s). Détail dans le journal."
        else:
            text = f"Selftest échoué (code {res.code}). {last} — détail dans le journal."
        self.engine.say_notice(text, "selftest", {"ok": res.ok})
        return 0 if res.ok else 1

    # ---- état ---------------------------------------------------------------
    def tasks_signature(self) -> Optional[Tuple[int, int, int]]:
        try:
            st = self.engine.paths.tasks.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def task_lines(self) -> List[str]:
        try:
            tasks = self.engine.store.snapshot()
        except StoreError as e:
            return [f"PROBLÈME : {e}"]
        return [task_line(t) for t in tasks] or ["(aucune tâche — écris un objectif puis Plan CEO ou Tick)"]

    def save_setting(self, key: str, value: str) -> None:
        if key not in CEO_SETTING_KEYS:
            self.engine.cfg.update(**{key: value.strip()})
            return
        with self._start_guard:  # ni job ni bascule de dossier pendant la reconstruction du CEO
            if self.busy:
                # Le CEO peut être en plein appel ask() dans un autre thread : remplacer
                # engine.ceo à chaud ferait basculer le job en cours de réglages à mi-vol.
                raise RuntimeError("CEO en cours : change ce réglage après l'arrêt du travail.")
            self.engine.cfg.update(**{key: value.strip()})
            self._rebuild_ceo()  # le CEO lit ses réglages à la création : on le resynchronise

    def save_verify_cmd(self, cmd: str) -> None:
        """Commande de vérification du dossier de travail COURANT."""
        engine = self.engine
        engine.cfg.set_verify_cmd(engine.paths.root, cmd)

    def approve_verify(self, cmd: str) -> Tuple[bool, str]:
        """Ton accord pour lancer la commande détectée (elle exécute le code du dossier COURANT)."""
        engine = self.engine
        ok, err = engine.cfg.approve_verify(engine.paths.root, cmd)
        if ok:
            self.log(f"Vérification « {cmd} » autorisée pour ce dossier (redemandée si le fichier "
                     "qui la définit change).")
        return ok, err

    def _rebuild_ceo(self) -> None:
        """Recrée le CEO avec les réglages courants (mode local/cloud, session, dépôt, model)."""
        try:
            engine = self.engine
            if not isinstance(engine, Engine):
                return
            from .ceo import Ceo
            from .config import Bins
            cfg = engine.cfg
            engine.ceo = Ceo(Bins.resolve().claude, str(engine.paths.root), self.log,
                             model=cfg.ceo_model, mode=cfg.ceo_mode,
                             cloud_session=cfg.ceo_cloud_session, cloud_repo=cfg.ceo_cloud_repo,
                             effort=cfg.ceo_effort_raw)
        except Exception as e:  # noqa: BLE001 — un réglage ne doit jamais casser l'interface
            self.log(f"CEO non resynchronisé ({e.__class__.__name__}) : pris en compte au message suivant.")

    def current_model(self, agent: str = "opencode") -> str:
        if agent == "opencode":
            return self.engine.cfg.model(None, self.engine.default_model)
        return self.engine.cfg.agent_model(agent, None, self.engine.default_model)

    def model_label(self, worker: str, custom: Optional[dict]) -> str:
        """Model affiché pour ce worker dans l'équipe (label UI, jamais une donnée)."""
        if worker == CLAUDE_WORKER:  # renfort : le palier décide du model, pas un réglage
            return "renfort : " + " → ".join(r.label for _, r in sorted(LADDER.items()))
        spec = spec_for(worker)
        if spec is None:  # agent custom : model de sa ligne de config
            if custom is None:
                return "custom"
            return custom.get("model") or "réglé dans la commande"
        if spec.name == "opencode":
            return self.current_model("opencode")
        model = self.current_model(spec.name)
        if not model or model == self.engine.default_model:
            return "réglé dans l'outil"
        return model

    def effort_label(self, agent: str) -> str:
        """Effort affiché dans l'équipe ; « » pour le renfort (son palier est dans le libellé)."""
        cfg = self.engine.cfg
        if agent == CLAUDE_WORKER:
            return ""
        if agent == "ceo":
            level = cfg.ceo_effort()
        elif agent == "opencode":
            level = cfg.opencode_opts().get("opencode_variant", "")
        else:
            level = cfg.worker_effort(agent)
        return level or "par défaut"

    def workers_label(self) -> str:
        return ", ".join(self.engine.workers.available()) or "aucun"

    # ---- façade pour la couche web (WebApp ne touche jamais engine.*) --------
    @property
    def workspace_root(self) -> Path:
        return self.engine.paths.root

    def tasks_view(self) -> Tuple[List[dict], str]:
        """Lignes de tâches pour le navigateur, ou ([], message) si illisible."""
        try:
            snap = self.engine.store.snapshot()
        except StoreError as e:
            return [], str(e)
        rows = []
        for t in snap:
            owner = t.get("owner") if isinstance(t.get("owner"), dict) else {}
            rows.append({"id": t.get("id"), "status": t.get("status"), "worker": t.get("worker"),
                         "title": t.get("title", "?"), "verdict": t.get("verdict"),
                         "phase": owner.get("phase"), "attempts": t.get("attempts", 0)})
        return rows, ""

    def team_view(self, rows: List[dict]) -> dict:
        """Qui travaille en ce moment : le CEO (Claude Code) planifie/vérifie, les workers exécutent."""
        busy, label = self.busy, self.job_label
        running = [t for t in rows if t.get("status") == "in_progress"] if busy else []
        reviewing = [t for t in running if t.get("phase") == "review"]
        working = [t for t in running if t.get("phase") != "review"]
        if reviewing:
            ceo = f"vérifie la tâche [{reviewing[0]['id']}] « {reviewing[0]['title'][:60]} »"
        elif busy and not running:
            ceo = {"Rapport CEO": "rédige son rapport final",
                   "Conversation": "réfléchit à ta demande"}.get(label, "planifie l'objectif en tâches")
        else:
            ceo = "au repos"
        workers = []
        for name in self.engine.workers.available():
            mine = [t for t in working if t.get("worker") == name]
            state = f"exécute la tâche [{mine[0]['id']}] « {mine[0]['title'][:60]} »" if mine else "au repos"
            custom = next((a for a in self.engine.cfg.custom_agents() if a["name"] == name), None)
            workers.append({"name": name, "state": state, "active": bool(mine),
                            "model": self.model_label(name, custom), "effort": self.effort_label(name)})
        setting = self.engine.cfg.ceo_mode()
        repo = self.engine.cfg.ceo_cloud_repo()
        # Le mode EFFECTIF (celui du CEO), pas le réglage : « cloud » sans session tourne en local.
        ceo_obj = self.engine.ceo
        mode = ceo_obj.effective_mode() if hasattr(ceo_obj, "effective_mode") else setting
        fallback = ceo_obj.fallback_note() if hasattr(ceo_obj, "fallback_note") else ""
        where = f"☁️ {repo}" if (mode == "cloud" and repo) else ("☁️ cloud" if mode == "cloud" else "💻 local")
        return {"ceo": {"name": "Claude Code", "role": "CEO", "state": ceo, "active": ceo != "au repos",
                        "model": self.engine.cfg.ceo_model() or "par défaut",
                        "effort": self.effort_label("ceo"),
                        "mode": mode, "mode_setting": setting, "fallback": fallback,
                        "where": where, "repo": repo},
                "workers": workers}

    def state_view(self, chat_since: int) -> dict:
        """Tout l'état du domaine pour /api/state, sans le journal (reste côté web)."""
        engine = self.engine  # une seule lecture : pas de mélange de deux dossiers pendant une bascule
        tasks, problem = self.tasks_view()
        # Ids qui repartent à 1 (conversation mise de côté) : le curseur de la page est « devant ».
        reset = bool(engine.chat) and chat_since > engine.chat.last_id()
        chat = engine.chat.since(0 if reset else chat_since) if engine.chat else []
        cfg = engine.cfg
        raw = cfg.load()
        opts = cfg.opencode_opts()
        verify = cfg.verify_status(engine.paths.root)
        scan = self.free_models.view()
        return {"busy": self.busy, "progress": self.progress_text(), "workspace": self._ws_gen,
                "account": self.account.view(),
                "chat": chat, "chat_last": chat[-1]["id"] if chat else chat_since,
                "chat_reset": reset,
                "tasks": tasks, "problem": problem, "team": self.team_view(tasks),
                "reports": self.engine.store.reports()[:5],
                "workers": self.workers_label(), "models": self._model_suggestions(cfg, scan),
                "free_models": scan, "cline_models": cfg.cline_models(),
                "ceo_models": CEO_MODEL_SUGGESTIONS,
                "custom_agents": cfg.custom_agents(),
                "dry_run": self.dry_run,
                "settings": {"ceo_model": cfg.ceo_model(),
                             "ceo_effort": cfg.ceo_effort(),
                             "escalation": "on" if cfg.escalation_enabled() else "off",
                             "verify_cmd": cfg.verify_cmd(engine.paths.root),
                             "verify_effective": verify.cmd,
                             "verify_source": verify.source,
                             "verify_files": list(verify.files),
                             "autoclaw_effort": cfg.worker_effort("autoclaw"),
                             "cline_effort": cfg.worker_effort("cline"),
                             "ceo_mode": cfg.ceo_mode(),
                             "ceo_cloud_session": cfg.ceo_cloud_session(),
                             "ceo_cloud_repo": cfg.ceo_cloud_repo(),
                             "opencode_model": self.current_model("opencode"),
                             "autoclaw_cmd": str(raw.get("autoclaw_cmd") or ""),
                             "cline_cmd": str(raw.get("cline_cmd") or ""),
                             "autoclaw_model": str(raw.get("autoclaw_model") or ""),
                             "cline_model": str(raw.get("cline_model") or ""),
                             "opencode_agent": str(opts.get("opencode_agent") or ""),
                             "opencode_variant": str(opts.get("opencode_variant") or ""),
                             "custom_models": str(raw.get("custom_models") or ""),
                             "cline_models": str(raw.get("cline_models") or ""),
                             "ui_color": str(raw.get("ui_color") or "")}}

    @staticmethod
    def _model_suggestions(cfg: ConfigStore, scan: dict) -> List[str]:
        """Suggestions des champs « model » : models connus + gratuits scannés."""
        known = cfg.all_models()
        extra = cfg.cline_models() + list(scan["models"])
        return known + [m for i, m in enumerate(extra) if m not in known and m not in extra[:i]]

    def auth_status(self) -> dict:
        """État de connexion [CC] : jamais de secret, juste email/org/abonnement."""
        from .config import Bins
        claude_bin = Bins.resolve().claude
        res = run_cmd([claude_bin, "auth", "status", "--json"], timeout=AUTH_TIMEOUT_S,
                      cwd=_account_cwd())
        if res.cancelled:
            return {"ok": False, "error": "annulé"}
        try:
            data = json.loads(res.out) if res.out.strip() else None
        except ValueError:
            data = None
        if not isinstance(data, dict) or "loggedIn" not in data:
            # Ni « connecté » ni « déconnecté » : une panne, dite comme telle (jamais « non connecté »).
            if res.timed_out:
                why = f"délai dépassé ({AUTH_TIMEOUT_S} s)"
            else:
                why = res.err.strip() or res.out.strip() or f"code {res.code}, aucune sortie"
            return {"ok": False, "error": f"état du compte inconnu : {why}"[:300]}
        if not data.get("loggedIn"):
            return {"ok": False, "loggedIn": False, "error": "non connecté — utilise ton email ci-dessous."}
        return {"ok": True, "loggedIn": True,
                "email": str(data.get("email") or ""),
                "org": str(data.get("orgName") or ""),
                "plan": str(data.get("subscriptionType") or "")}

    def usage_status(self) -> dict:
        """Usage de l'abonnement (session / semaine) via `claude -p /usage` — commande locale,
        sans appel au modèle. En démo, rien n'est lancé : on le dit au lieu d'inventer."""
        if self.dry_run:
            return {"ok": False, "error": "mode démo : usage non lu."}
        from .config import Bins
        res = run_cmd([Bins.resolve().claude, "-p", "/usage"], timeout=USAGE_TIMEOUT_S,
                      cwd=_account_cwd())
        if res.cancelled:
            return {"ok": False, "error": "annulé"}
        if not res.ok:
            why = "délai dépassé" if res.timed_out else (res.err.strip() or res.out.strip() or f"code {res.code}")
            return {"ok": False, "error": why[:300]}
        return parse_usage(res.out)

    def switch_account(self, email: str) -> Tuple[bool, str]:
        """Change de compte Claude Code : déconnexion puis connexion (navigateur). En démo, rien
        n'est lancé ; pendant un travail, rien non plus (le CEO changerait de compte à mi-vol)."""
        if self.dry_run:
            return False, "mode démo : changement de compte non lancé."
        with self._start_guard:  # aucun travail ne démarre entre la vérification et le lancement
            if self.busy:
                return False, "un travail est en cours : attends sa fin ou clique sur « Arrêter »."
            return self.account.start(email)

    def cancel_account_switch(self) -> bool:
        return self.account.cancel()

    def account_busy_reason(self) -> str:
        """Pourquoi on ne peut pas toucher au compte maintenant (« » = on peut)."""
        if self.busy:
            return "un travail est en cours : attends sa fin ou clique sur « Arrêter »."
        if self.account.running:
            return "un changement de compte est en cours : attends sa fin ou annule-le."
        return ""

    def auth_logout(self) -> Tuple[bool, str]:
        """Déconnexion Claude Code : (succès, message d'erreur si échec). Refusée pendant un travail
        ou un changement de compte (le CEO ou la connexion en cours se retrouveraient sans compte)."""
        from .config import Bins
        claude_bin = Bins.resolve().claude
        with self._start_guard:  # aucun travail ne démarre pendant la déconnexion
            reason = self.account_busy_reason()
            if reason:
                return False, reason
            res = run_cmd([claude_bin, "auth", "logout"], timeout=AUTH_TIMEOUT_S,
                          cwd=_account_cwd())
        if res.ok:
            return True, ""
        return False, (res.err.strip() or res.out.strip() or "déconnexion impossible")[:300]

    def custom_agents(self) -> List[Dict[str, str]]:
        return self.engine.cfg.custom_agents()

    def set_custom_agents(self, agents: List[Dict[str, str]]) -> List[Dict[str, str]]:
        self.engine.cfg.set_custom_agents(agents)
        return self.engine.cfg.custom_agents()

    def chat_block(self, files: object, pasted: object) -> Tuple[str, List[str]]:
        """Pièces jointes du message (workspace) → (bloc texte, notes)."""
        from .workspace import build_block
        return build_block(self.workspace_root, files, pasted)

    def list_workspace(self, rel: str) -> dict:
        """Contenu d'un dossier du workspace, prêt pour la réponse."""
        from .workspace import clean_rel, list_dir
        entries, note = list_dir(self.workspace_root, rel or "")
        shown = clean_rel(rel) if rel else ""
        root = self.workspace_root
        return {"root": root.name or str(root), "dir": shown, "entries": entries, "note": note}

    def read_workspace(self, rel: object) -> Tuple[Optional[dict], str]:
        """Fichier du workspace : (payload, "") ou (None, erreur utilisateur)."""
        from .workspace import clean_rel, read_for_prompt
        path = clean_rel(rel)
        if not path:
            return None, "chemin invalide."
        text, err = read_for_prompt(self.workspace_root, path)
        if err:
            return None, err
        return {"ok": True, "path": path, "size": len(text)}, ""
