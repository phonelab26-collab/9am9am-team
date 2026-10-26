"""tasks.json : lectures, et écritures toutes faites sous verrou, patch par id.

Cycle d'une tâche (inspiré du checkout Paperclip) :
  todo --checkout--> in_progress (owner.phase=work, bail tenu)
       --record_result--> in_progress (phase=review) | done | blocked | failed
       --finalize (verdict CEO)--> done (validé) | blocked | failed
Un in_progress dont le bail est libre appartient à un process mort : recover_stale.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import tasks as T
from .escalation import WORKER_ERROR, SETTING_REFUSED
from .config import Paths
from .fileio import MISSING, atomic_write_json, cleanup_tmp, quarantine, read_json
from .lockfile import Lease, lock_for
from .procs import kill_group
from .textutil import norm_goal, tail
from .trust import TrustStore
from .fileio import UnsafePath, secure_listdir, secure_unlink  # lot 2 : dossier non fiable
from typing import Iterator

RESULT_MAX = 4000
REPORTS_KEEP = 20
PS_TIMEOUT_S = 10  # `ps` local : au-delà, la machine est de toute façon en souffrance


class StoreError(RuntimeError):
    """tasks.json inutilisable : message destiné à l'utilisateur."""


@dataclass(frozen=True)
class Claim:
    task: Optional[dict]
    run_id: str = ""
    lease: Optional[Lease] = None
    waiting: Optional[Tuple[dict, dict]] = None  # (tâche en attente, tâche bloquante)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def kill_orphan_group(pgid: int, cmd_name: str) -> List[str]:
    """Arrête le groupe d'un worker dont l'orchestrateur est mort, après avoir
    vérifié qu'il s'agit bien de ce worker (un pgid peut être réattribué)."""
    try:
        ps = subprocess.run(["ps", "-A", "-o", "pgid=,command="], capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=PS_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [f"groupe {pgid} non vérifié ({e.__class__.__name__}) : laissé tel quel."]
    members = [line.split(None, 1)[1] for line in ps.stdout.splitlines()
               if len(line.split(None, 1)) == 2 and line.split(None, 1)[0] == str(pgid)]
    if not members:
        return []
    # Le programme lancé figure parmi les premiers mots de sa ligne de commande,
    # qu'il soit natif (`opencode run`) ou interprété (`node …/opencode run`).
    ours = any(cmd_name and Path(word).name == cmd_name
               for cmd in members for word in cmd.split()[:3])
    if not ours:
        return [f"groupe {pgid} ne correspond pas à {cmd_name!r} (pgid réattribué ?) : laissé tel quel."]
    kill_group(pgid)
    return [f"worker orphelin {cmd_name} (groupe {pgid}) arrêté."]


class Store:
    def __init__(self, paths: Paths, log: Callable[[str], None],
                 orphan_killer: Callable[[int, str], List[str]] = kill_orphan_group) -> None:
        self._paths, self._log, self._kill_orphan = paths, log, orphan_killer
        self._lock = lock_for(paths.lock)
        self._trust = TrustStore(paths, log)  # groupes lancés par CE programme (hors du projet)
        self._said: set = set()
        self._set_aside = ""  # tasks.json corrompu mis de côté, pas encore signalé (take_damage)

    def _log_once(self, msg: str) -> None:
        if msg not in self._said:  # lu à chaque rafraîchissement de la page : dit une fois
            self._said.add(msg)
            self._log(msg)

    @contextmanager
    def _held(self) -> Iterator[None]:
        """Verrou d'état ; un lien planté dans .mini-orch devient une StoreError (message
        pour l'utilisateur) au lieu d'une OSError que l'interface n'attend pas."""
        if not self._paths.root.is_dir():  # supprimé pendant le run : jamais recréé par une écriture
            raise StoreError("dossier de travail introuvable (supprimé ou déplacé) : rouvre un dossier existant.")
        try:
            with self._lock.held():
                yield
        except UnsafePath as e:
            raise StoreError(e.strerror) from None

    # ---- lecture ---------------------------------------------------------
    def snapshot(self) -> List[dict]:
        """Lecture seule (status, interface) : ne déplace ni ne modifie rien."""
        try:
            data = read_json(self._paths.tasks)
        except UnsafePath as e:
            raise StoreError(e.strerror) from None
        except ValueError:
            raise StoreError("tasks.json illisible (JSON invalide) : il sera mis de côté "
                             "à la prochaine écriture.") from None
        if data is MISSING:
            return []
        try:
            return T.unpack(data)
        except T.TaskFileError as e:
            raise StoreError(f"tasks.json incohérent : {e}") from None

    def _load_for_write(self) -> List[dict]:
        try:
            data = read_json(self._paths.tasks)
        except UnsafePath as e:
            raise StoreError(e.strerror) from None
        except ValueError:
            kept = quarantine(self._paths.tasks)
            self._set_aside = kept.name
            self._log(f"tasks.json illisible : conservé en {kept.name} ; nouvelle liste vide.")
            return []
        if data is MISSING:
            return []
        try:
            return T.unpack(data)
        except T.TaskFileError as e:
            raise StoreError(f"tasks.json incohérent, rien n'est écrit : {e}") from None

    def take_damage(self) -> str:
        """Nom du tasks.json corrompu mis de côté depuis le dernier appel (« » = rien) : une
        lecture d'écriture (reprise, checkout) le met de côté sans rien dire à l'appelant."""
        with self._held():
            name, self._set_aside = self._set_aside, ""
            return name

    def _mutate(self, fn: Callable[[List[dict]], Tuple[List[dict], object]]) -> object:
        with self._held():
            if self._paths.state.exists():
                cleanup_tmp(self._paths.state)
            tasks = self._load_for_write()
            new, result = fn(tasks)
            if new is not tasks:
                atomic_write_json(self._paths.tasks, T.pack(new))
            return result

    def _owned(self, tasks: List[dict], tid: int, run_id: str, phases) -> Optional[dict]:
        phases = (phases,) if isinstance(phases, str) else phases
        t = T.find(tasks, tid)
        owner = t.get("owner") if t else None
        if (t is None or t.get("status") != T.IN_PROGRESS or not isinstance(owner, dict)
                or owner.get("run_id") != run_id or owner.get("phase") not in phases):
            self._log(f"[{tid}] écriture refusée : la tâche n'appartient plus à ce run "
                      "(récupérée ou relancée ailleurs).")
            return None
        return t

    # ---- rapports du CEO -------------------------------------------------
    def reports(self) -> List[dict]:
        """Rapports finaux du CEO, du plus récent au plus ancien (fichier illisible : liste vide)."""
        try:
            data = read_json(self._paths.reports)
        except UnsafePath as e:
            self._log_once(f"Rapports illisibles : {e.strerror}")
            return []
        except ValueError:
            self._log_once("reports.json illisible : rapports non affichés (il sera mis de côté "
                           "à la prochaine écriture).")
            return []
        if data is MISSING or not isinstance(data, list):
            return []
        return [r for r in reversed(data) if isinstance(r, dict)]

    def add_report(self, goal: str, text: str) -> None:
        goal = norm_goal(goal)
        with self._held():
            try:
                data = read_json(self._paths.reports)
            except ValueError:
                kept = quarantine(self._paths.reports)
                self._log(f"reports.json illisible : conservé en {kept.name}.")
                data = []
            keep = [r for r in ([] if data is MISSING or not isinstance(data, list) else data)
                    if isinstance(r, dict) and r.get("goal") != goal]
            keep.append({"goal": goal, "text": text, "at": _now()})
            atomic_write_json(self._paths.reports, keep[-REPORTS_KEEP:])

    # ---- écritures -------------------------------------------------------
    def add_plan(self, goal: str, items: List[dict], if_absent: bool = True) -> Tuple[Optional[str], List[int]]:
        goal = norm_goal(goal)

        def fn(tasks):
            if if_absent and any(norm_goal(t.get("goal")) == goal for t in tasks):
                return tasks, (None, [])
            plan_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
            start, created = T.next_id(tasks), _now()
            new = [{"id": start + i, "title": T.one_line(str(it["title"])), "worker": it["worker"],
                    "detail": it.get("detail", ""), "goal": goal, "plan_id": plan_id,
                    "status": T.TODO, "result": "", "attempts": 0, "created": created}
                   for i, it in enumerate(items)]
            return tasks + new, (plan_id, [t["id"] for t in new])
        return self._mutate(fn)

    def checkout(self, worker: Optional[str] = None, goal: str = "") -> Claim:
        with self._held():
            tasks = self._load_for_write()
            pick, waiting = T.next_ready(tasks, worker, goal)
            if pick is None:
                return Claim(None, waiting=waiting)
            run_id = time.strftime("%H%M%S-") + secrets.token_hex(4)
            lease = Lease.acquire(self._paths.runs / f"{run_id}.lock")
            try:
                owner = {"pid": os.getpid(), "run_id": run_id, "since": _now(), "phase": "work"}
                claimed = {**pick, "status": T.IN_PROGRESS, "owner": owner,
                           "attempts": int(pick.get("attempts") or 0) + 1}
                atomic_write_json(self._paths.tasks, T.pack(T.replace_task(tasks, claimed)))
            except BaseException:
                lease.release()
                raise
            return Claim(claimed, run_id, lease)

    def set_child(self, tid: int, run_id: str, pgid: int, cmd_name: str) -> None:
        def fn(tasks):
            t = self._owned(tasks, tid, run_id, ("work", "verify", "review"))
            if t is None:
                return tasks, None
            # Inscrit AVANT tasks.json : seul ce registre autorise un arrêt après un plantage.
            self._trust.remember_child(run_id, pgid, cmd_name)
            owner = {**t["owner"], "child_pgid": pgid, "child_cmd": cmd_name}
            return T.replace_task(tasks, {**t, "owner": owner}), None
        self._mutate(fn)

    def record_result(self, tid: int, run_id: str, code: int, text: str, supervise: bool,
                      cancelled: bool = False, refused: bool = False) -> Optional[dict]:
        def fn(tasks):
            t = self._owned(tasks, tid, run_id, "work")
            if t is None:
                return tasks, None
            # « interrupted » (annulation, arrêt du process) n'est pas un échec du worker : jamais
            # d'escalade. Il ne survit pas à l'essai suivant : un vrai échec reste escaladable.
            base = {k: v for k, v in t.items() if k not in ("owner", "interrupted")}
            base["result"] = tail(text, RESULT_MAX)
            if cancelled:
                new = {**base, "status": T.BLOCKED, "attempts": max(0, int(t.get("attempts") or 1) - 1),
                       "interrupted": True,
                       "result": ("[annulé par l'utilisateur]\n" + base["result"]).strip()}
            elif code == 0 and supervise:
                owner = {k: v for k, v in t["owner"].items() if not k.startswith("child_")}
                new = {**base, "owner": {**owner, "phase": "review"}}
            elif code == 0:
                new = T.apply_verdict(base, None, "")
            elif refused:  # un réglage l'interdit : le retenter ne changerait rien (ni essai, ni escalade)
                new = {**base, "status": T.FAILED, "last_failure": SETTING_REFUSED}
            else:
                exhausted = int(t.get("attempts") or 0) >= T.MAX_ATTEMPTS
                new = {**base, "status": T.FAILED if exhausted else T.BLOCKED, "last_failure": WORKER_ERROR}
            return T.replace_task(tasks, new), new
        return self._mutate(fn)

    def finalize(self, tid: int, run_id: str, verdict: Optional[str], note: str) -> Optional[dict]:
        def fn(tasks):
            t = self._owned(tasks, tid, run_id, "review")
            if t is None:
                return tasks, None
            new = T.apply_verdict(t, verdict, note)
            return T.replace_task(tasks, new), new
        return self._mutate(fn)

    def _set_phase(self, tid: int, run_id: str, frm: str, to: str) -> Optional[dict]:
        def fn(tasks):
            t = self._owned(tasks, tid, run_id, frm)
            if t is None:
                return tasks, None
            owner = {k: v for k, v in t["owner"].items() if not k.startswith("child_")}
            new = {**t, "owner": {**owner, "phase": to}}
            return T.replace_task(tasks, new), new
        return self._mutate(fn)

    def begin_verify(self, tid: int, run_id: str) -> Optional[dict]:
        """Phase dédiée : un crash pendant les tests ne doit jamais valider la tâche en douce."""
        return self._set_phase(tid, run_id, "review", "verify")

    def end_verify(self, tid: int, run_id: str) -> Optional[dict]:
        """Tests verts : la tâche passe à l'audit du CEO."""
        return self._set_phase(tid, run_id, "verify", "review")

    def fail_check(self, tid: int, run_id: str, cmd: str, output: str,
                   broken: str = "") -> Optional[dict]:
        """Vérification rouge → blocked/failed sans le CEO ; `broken` = la commande elle-même
        ne marche pas (introuvable, délai) → échec net, sans essai gaspillé."""
        def fn(tasks):
            t = self._owned(tasks, tid, run_id, "verify")
            if t is None:
                return tasks, None
            new = (T.apply_check_error(t, cmd, broken, output) if broken
                   else T.apply_check_failure(t, cmd, output))
            return T.replace_task(tasks, new), new
        return self._mutate(fn)

    def interrupt_review(self, tid: int, run_id: str, phase: str = "verify") -> Optional[dict]:
        """Arrêt pendant les tests (« verify ») ou l'audit du CEO (« review ») : comme une
        annulation (essai rendu, jamais d'escalade) — jamais une validation en douce."""
        def fn(tasks):
            t = self._owned(tasks, tid, run_id, phase)
            if t is None:
                return tasks, None
            new = {k: v for k, v in t.items() if k != "owner"}
            new.update(status=T.BLOCKED, interrupted=True,
                       attempts=max(0, int(t.get("attempts") or 1) - 1))
            return T.replace_task(tasks, new), new
        return self._mutate(fn)

    def requeue_blocked(self, goal: str, escalate: bool = False) -> int:
        def fn(tasks):
            new, n = T.requeue_blocked(tasks, goal, escalate)
            return (new if n else tasks), n
        return self._mutate(fn)

    def reset_stuck(self) -> int:
        def fn(tasks):
            new, n = T.reset_stuck(tasks)
            return (new if n else tasks), n
        return self._mutate(fn)

    def recover_stale(self) -> List[str]:
        """Reprend les in_progress dont le process propriétaire est mort."""
        def fn(tasks):
            out, msgs, live = tasks, [], set()
            for t in tasks:
                if t.get("status") != T.IN_PROGRESS:
                    continue
                owner = t.get("owner") if isinstance(t.get("owner"), dict) else {}
                run_id = str(owner.get("run_id") or "")
                lease = self._paths.runs / f"{run_id}.lock"
                if run_id and Lease.is_held(lease):
                    live.add(lease.name)
                    continue
                if owner.get("child_pgid"):
                    msgs += self._stop_orphan(t, run_id, int(owner["child_pgid"]), str(owner.get("child_cmd") or ""))
                new, msg = self._recovered(t, owner.get("phase"))
                out = T.replace_task(out, new)
                msgs.append(msg)
            self._drop_free_leases(live)
            self._trust.forget_dead_runs(lambda rid: Lease.is_held(self._paths.runs / f"{rid}.lock"))
            return out, msgs
        return self._mutate(fn)

    def _stop_orphan(self, t: dict, run_id: str, pgid: int, cmd: str) -> List[str]:
        """N'arrête QUE un groupe inscrit par ce programme : un tasks.json venu avec le projet
        (ou écrit par une version antérieure) ne peut faire tuer aucun processus."""
        if run_id and self._trust.launched(run_id, pgid, cmd):
            return self._kill_orphan(pgid, cmd)
        return [f"[{T.int_id(t)}] groupe {pgid} ({cmd or '?'}) absent du registre de ce programme "
                "(tasks.json venu d'ailleurs ou d'une version antérieure) : laissé tel quel."]

    @staticmethod
    def _recovered(t: dict, phase: object) -> Tuple[dict, str]:
        tid = T.int_id(t)
        if phase == "verify":  # tests interrompus : jamais une validation silencieuse
            new = {k: v for k, v in t.items() if k != "owner"}
            new.update(status=T.BLOCKED, interrupted=True, attempts=max(0, int(t.get("attempts") or 1) - 1),
                       result=(str(t.get("result") or "") + "\n[récupération] vérification automatique "
                               "interrompue : à refaire.").strip())
            return new, f"[{tid}] vérification interrompue (process arrêté) → blocked, reprise possible."
        if phase == "review":
            new = T.apply_verdict(t, None, "")
            new["result"] = (str(t.get("result") or "") + "\n[récupération] supervision "
                             "interrompue : résultat non validé par le CEO.").strip()
            return new, f"[{tid}] supervision interrompue (process arrêté) → done non supervisé."
        # L'essai est rendu (comme pour verify et cancel) : ce n'est pas un échec de l'agent, et trois
        # kill -9 ne doivent pas épuiser la tâche ni couper la reprise automatique.
        new = {k: v for k, v in t.items() if k != "owner"}
        new.update(status=T.BLOCKED, interrupted=True, attempts=max(0, int(t.get("attempts") or 1) - 1),
                   result=(str(t.get("result") or "") +
                           "\n[récupération] process interrompu en cours d'exécution.").strip())
        return new, f"[{tid}] exécution interrompue (process arrêté) → blocked, reprise possible."

    def _drop_free_leases(self, live: set) -> None:
        try:
            names = secure_listdir(self._paths.runs)
        except FileNotFoundError:
            return
        for name in names:
            lease = self._paths.runs / name
            if name.endswith(".lock") and name not in live and not Lease.is_held(lease):
                secure_unlink(lease)
