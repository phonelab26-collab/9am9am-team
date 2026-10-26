"""Moteur : plan (CEO) → exécution (worker) → supervision (CEO), façon heartbeat Paperclip.

Aucune globale, aucun `print` : tout passe par `log`, pour que la CLI et
l'interface affichent la même chose et que tout finisse dans journal.log.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional

from . import tasks as T
from .ceo import READ_TOOLS, PlanError, effort_advice, parse_chat, parse_plan, parse_verdict
from .chat import ChatStore
from .config import FREE_MODELS, ConfigStore, Paths
from .verify import (CHECK_ERROR, CHECK_FAILED, CHECK_KO, INFRA_CODES, broken_reason, check_block,
                     fingerprint, not_run_block)
from .escalation import (CEO_REFUSED, CLAUDE_WORKER, MAX_ESCALATED_PER_PLAN, MAX_TIER, WORKER_ERROR,
                         current_tier, rung_for)
from .registry import DEFAULT as DEFAULT_WORKER
from .prompts import (CEO_PLAN_PROMPT, CEO_SUPERVISE_PROMPT, CHAT_HISTORY_MESSAGES, PLAN_MAX_TASKS,
                      PLAN_MIN_TASKS, build_chat_prompt, build_report_prompt, build_task_prompt,
                      claude_hint, supervision_view, FOLLOWUP_ADVICE)
from .redact import redact
from .store import Store, StoreError
from .workspace import ATTACH_MAX, attached_names, fit_block
from .textutil import norm_goal, tail
from .workers import WorkOutcome

TEAM_NAME = "9AM9AM-TEAM"
PROBLEM_PREFIX = "PROBLÈME : "  # début de la ligne de status_lines quand tasks.json est inutilisable
MESSAGE_MAX = 4000
CLAUDE_DISABLED = ("CLAUDE-DESACTIVE : Claude Code (payant) est désactivé dans les Paramètres "
                   "(« Reprise par Claude Code »). Réactive-le, ou relance la demande pour qu'elle "
                   "soit confiée à un agent gratuit.")
TASK_RESULT_CHARS = 3000  # réponse d'un agent gardée dans la conversation (elle s'y lit mise en forme)
_MARKS = {"ok": " — validé par le CEO", "ko": " — refusé par le CEO", T.UNSUPERVISED: " — non supervisé",
          CHECK_KO: " — vérification automatique échouée", CHECK_ERROR: " — vérification impossible (réglage)"}


@dataclass(frozen=True)
class RunReport:
    rc: int
    task: Optional[dict] = None
    run_id: str = ""


def task_line(t: dict) -> str:
    mark = _MARKS.get(str(t.get("verdict") or ""), "")
    owner = t.get("owner") if isinstance(t.get("owner"), dict) else {}
    if t.get("status") == T.IN_PROGRESS and owner:
        mark = f" — en cours ({owner.get('phase', '?')}, pid {owner.get('pid', '?')})"
    return f"[{t.get('id')}] {str(t.get('status')):11} ({str(t.get('worker')):8}) {t.get('title', '?')}{mark}"


class Engine:
    def __init__(self, paths: Paths, store: Store, cfg: ConfigStore, workers, ceo,
                 log: Callable[[str], None], default_model: str = FREE_MODELS[0],
                 chat: Optional[ChatStore] = None) -> None:
        self.paths, self.store, self.cfg = paths, store, cfg
        self.workers, self.ceo, self.log, self.default_model = workers, ceo, log, default_model
        self.chat = chat
        self._chat_broken = False
        self._announced_check = ""
        self._usage = self._new_usage()
        self._report_tried = False  # rapport final déjà demandé pour la demande en cours (_drive)
        self._stop_said = False     # arrêt déjà annoncé dans la conversation (_drive)
        self._settings_said: set = set()  # avis de réglages déjà dits dans la conversation

    # ---- consommation Claude (rien de caché) ---------------------------------
    @staticmethod
    def _new_usage() -> dict:
        return {"ceo_calls": 0, "worker_calls": 0, "tokens": 0, "unmeasured": 0, "avoided": 0}

    def _count(self, kind: str, tokens: object) -> None:
        self._usage[kind] += 1
        if isinstance(tokens, int) and not isinstance(tokens, bool):
            self._usage["tokens"] += tokens
        else:
            self._usage["unmeasured"] += 1

    def _ask(self, prompt: str, cancel: Optional[threading.Event] = None, **kw):
        """Appel du CEO, compté (le seul endroit où le CEO est appelé)."""
        ans = self.ceo.ask(prompt, cancel, **kw)
        self._count("ceo_calls", getattr(ans, "tokens", None))
        return ans

    def _say_usage(self) -> None:
        u = self._usage
        calls = u["ceo_calls"] + u["worker_calls"]
        if not calls and not u["avoided"]:
            return
        detail = f"{u['ceo_calls']} du CEO" + (f", {u['worker_calls']} de reprise par Claude Code"
                                               if u["worker_calls"] else "")
        if not calls:
            amount, tokens = "0 token", 0
        elif u["unmeasured"] == calls:
            amount, tokens = "tokens non mesurés", None
        else:
            amount = f"≈ {round(u['tokens'] / 1000)} k tokens traités"
            amount += f" ({u['unmeasured']} appel(s) non mesuré(s))" if u["unmeasured"] else ""
            tokens = u["tokens"]
        n = u["avoided"]
        avoided = (f" · {n} vérification{'s' if n > 1 else ''} Claude évitée{'s' if n > 1 else ''} "
                   "grâce aux tests") if n else ""
        head = (f"{calls} appel{'s' if calls > 1 else ''} ({detail})" if calls else "aucun appel")
        text = (f"Consommation Claude pour cette demande : {head} · {amount}{avoided}. "
                "Le travail des agents gratuits : 0 token Claude.")
        self.log(text)
        self._say("event", text, "claude_usage", {"ceo_calls": u["ceo_calls"], "worker_calls": u["worker_calls"],
                                                  "tokens": tokens, "avoided": n})

    def say_notice(self, text: str, kind: str, data: Optional[dict] = None) -> None:
        """Information à montrer dans la conversation (ligne d'activité)."""
        self._say("event", text, kind, data)

    def _announce_settings(self) -> None:
        """Réglages abîmés, ignorés ou remplacés (config illisible, effort invalide, cloud sans
        session) : dits dans la conversation, une fois — le journal est replié par défaut."""
        self.cfg.load()  # lit (et met de côté) un config.json abîmé AVANT de décider quoi proposer
        note = getattr(self.ceo, "fallback_note", None)
        for text in (*self.cfg.pop_notices(), *self.cfg.setting_problems(), note() if callable(note) else ""):
            if text and text not in self._settings_said:
                self._settings_said.add(text)
                self.say_error(text)

    def say_error(self, text: str) -> None:
        """Erreur à montrer dans la conversation (pas seulement dans le journal technique)."""
        self._say("ceo", text, "error")

    def _offered_workers(self) -> List[str]:
        """Agents proposés au CEO. « Reprise par Claude Code : désactivée — les agents gratuits
        seulement » retire aussi Claude Code (payant) de la liste : il ne peut pas être choisi d'emblée."""
        avail = self.workers.available()
        return avail if self.cfg.escalation_enabled() else [w for w in avail if w != CLAUDE_WORKER]

    def _say(self, role: str, text: str, kind: str = "", data: Optional[dict] = None) -> None:
        """Ajoute une ligne à la conversation (sans ChatStore : rien). Un échec d'écriture
        est dit une fois dans le journal, jamais silencieux, et n'arrête pas le travail."""
        if self.chat is None:
            return
        try:
            self.chat.add(role, text, kind, data)
        except Exception as e:  # noqa: BLE001
            if not self._chat_broken:
                self._chat_broken = True
                self.log(f"conversation non enregistrée ({e.__class__.__name__}) : {e}")

    # ---- plan -------------------------------------------------------------
    def plan(self, goal: str, dry_run: bool = False, cancel: Optional[threading.Event] = None) -> int:
        goal = norm_goal(goal)
        self._announce_settings()
        if not goal:
            self.log("Objectif vide : écris un objectif (ex. « créer le site vitrine ») avant de lancer le CEO.")
            return 1
        workers = self._offered_workers() or ([DEFAULT_WORKER] if dry_run else [])
        if not workers:
            self.log("Aucun worker installé : installe opencode, ou renseigne la commande autoclaw/cline "
                     "dans l'interface (MINI_ORCH_OPENCODE / MINI_ORCH_CLAUDE pour d'autres chemins).")
            return 1
        existing = [t for t in self.store.snapshot() if norm_goal(t.get("goal")) == goal]
        if existing:
            self.log(f"Objectif déjà planifié ({len(existing)} tâche(s)) : change son texte pour en démarrer un nouveau.")
            return 1
        raw = self._plan_text(goal, workers, dry_run, cancel)
        if raw is None:
            return 1
        try:
            items, notes = parse_plan(raw, workers)
        except PlanError as e:
            self.log(f"Plan refusé : {e}\nRéponse du CEO (début) :\n{raw[:800]}")
            return 1
        for note in notes:
            self.log("Plan : " + note)
        plan_id, ids = self.store.add_plan(goal, items, if_absent=True)
        if plan_id is None:
            self.log("Objectif déjà planifié entre-temps (autre interface ou Terminal) : ce plan est ignoré.")
            return 1
        self.log(f"Plan CEO : {len(ids)} tâche(s) ajoutée(s) {ids} (workers : {', '.join(workers)}).")
        return 0

    def _cloud_prefix(self) -> str:
        """En mode cloud effectif : le dépôt GitHub visé (donnée encadrée pour le CEO)."""
        ctx = getattr(self.ceo, "cloud_context", None)
        return ctx() if callable(ctx) else ""

    def _plan_text(self, goal: str, workers: List[str], dry_run: bool,
                   cancel: Optional[threading.Event]) -> Optional[str]:
        if dry_run:
            return json.dumps([{"title": "Tâche démo (dry-run)", "worker": workers[0],
                                "detail": "ne rien faire, répondre OK"}])
        self.log(f"CEO : découpage de l'objectif « {goal[:80]} »…")
        ans = self._ask(self._cloud_prefix() + CEO_PLAN_PROMPT.format(
            goal=goal, workers=", ".join(workers), claude_hint=claude_hint(workers),
            min_tasks=PLAN_MIN_TASKS, max_tasks=PLAN_MAX_TASKS), cancel)
        if ans.text is None:
            self.log(f"Plan impossible : {ans.error}")
        return ans.text

    # ---- exécution --------------------------------------------------------
    def recover(self) -> None:
        for msg in self.store.recover_stale():
            self.log(msg)

    def run_next(self, worker: Optional[str] = None, model: Optional[str] = None, dry_run: bool = False,
                 supervise: bool = False, goal: str = "",
                 cancel: Optional[threading.Event] = None) -> RunReport:
        self.recover()
        claim = self.store.checkout(worker, goal)
        damaged = self.store.take_damage()  # mis de côté par la reprise ou le checkout ci-dessus
        if claim.task is None:
            if damaged:  # jamais « Rien à faire » : la liste de tâches était corrompue
                msg = (f"La liste des tâches (tasks.json) était corrompue : elle est conservée en "
                       f"{damaged} et la liste repart vide. Aucune tâche n'a été exécutée.")
                self.log(msg)
                self.say_error(msg)
                return RunReport(1)
            return RunReport(self._explain_idle(claim.waiting, worker, goal))
        try:
            check_cmd = self._check_command() if supervise and not dry_run else ""
            before = fingerprint(self.paths.root) if check_cmd else None
            rec, outcome = self._work(claim.task, claim.run_id, model, dry_run, supervise, cancel)
            if claim.task.get("worker") == CLAUDE_WORKER and not dry_run and not outcome.refused:
                self._count("worker_calls", outcome.tokens)  # un refus de réglage n'a rien coûté
            if rec is None:
                return RunReport(1, None, claim.run_id)
            self.log(f"[{rec['id']}] {rec.get('title')} -> {rec['status']}\n{tail(outcome.text, 1500)}")
            self._say("event", redact(tail(outcome.text, TASK_RESULT_CHARS)) or "(aucune sortie)", "task_result",
                      {"id": rec["id"], "status": rec["status"], "steps": list(outcome.steps),
                       "steps_omitted": outcome.steps_omitted, "cancelled": outcome.cancelled,
                       "model": outcome.model})
            if rec["status"] == T.IN_PROGRESS:
                stopped, check = self._verify(rec, claim.run_id, check_cmd, before, cancel)
                if stopped is not None:  # tests rouges ou arrêt : ni CEO, ni Claude
                    return RunReport(1, stopped, claim.run_id)
                try:
                    fin = self._supervise(rec, claim.run_id, cancel, check)
                except KeyboardInterrupt:
                    self.store.interrupt_review(rec["id"], claim.run_id, phase="review")
                    raise
                ok = fin is not None and fin.get("verdict") == "ok"
                if ok and not dry_run:
                    self._maybe_report(fin, cancel)
                return RunReport(0 if ok else 1, fin or rec, claim.run_id)
            if supervise and dry_run and rec["status"] == T.DONE:
                self.log("(dry-run : supervision CEO ignorée)")
            return RunReport(0 if rec["status"] == T.DONE else 1, rec, claim.run_id)
        finally:
            claim.lease.release()

    def _work(self, task: dict, run_id: str, model: Optional[str], dry_run: bool, supervise: bool,
              cancel: Optional[threading.Event]):
        tid = task["id"]
        self.log(f"[{tid}] {task.get('title')} → {task.get('worker')} "
                 f"(essai {task.get('attempts')}/{T.MAX_ATTEMPTS})…")
        self._say("event", f"{task.get('worker')} exécute la tâche [{tid}] « {task.get('title')} »", "task_start",
                  {"id": tid, "title": task.get("title"), "worker": task.get("worker"),
                   "attempt": task.get("attempts")})
        try:
            prompt = build_task_prompt(task, self.store.snapshot())
            worker_name = str(task.get("worker"))
            spawn = lambda pid, name: self.store.set_child(tid, run_id, pid, name)  # noqa: E731
            rung = rung_for(task)
            if worker_name == CLAUDE_WORKER and not self.cfg.escalation_enabled():
                outcome = WorkOutcome(2, CLAUDE_DISABLED, refused=True)  # confiée avant que le réglage change
            elif rung is not None:  # renfort Claude Code : model et effort fixés par le palier
                outcome = self.workers.run(worker_name, prompt, rung.model, dry_run, cancel,
                                           on_spawn=spawn, effort=rung.effort)
            else:
                chosen = self.cfg.agent_model(worker_name, model, self.default_model)
                outcome = self.workers.run(worker_name, prompt, chosen, dry_run, cancel, on_spawn=spawn)
        except KeyboardInterrupt:
            self.store.record_result(tid, run_id, 130, "interrompu (Ctrl-C)", supervise, cancelled=True)
            raise
        except Exception as e:  # noqa: BLE001 — jamais de tâche figée en in_progress
            outcome = WorkOutcome(1, f"EXCEPTION : {e.__class__.__name__}: {e}")
        rec = self.store.record_result(tid, run_id, outcome.code, outcome.text,
                                       supervise=supervise and not dry_run, cancelled=outcome.cancelled,
                                       refused=outcome.refused)
        return rec, outcome

    def _requeue(self, goal: str, worker: Optional[str] = None) -> int:
        """Reprise des tâches bloquées. Seul un refus du CEO peut faire reprendre une tâche
        par Claude Code (réglage « escalade », plafond par demande) ; un échec d'exécution
        repart sur le même worker gratuit.

        Bornes : le palier ne fait que monter (sommet : Opus · max) et chaque reprise consomme
        une tentative (MAX_ATTEMPTS) — aucune boucle possible. Une interruption ne monte jamais.
        Un run limité à un autre worker (« --worker opencode ») n'escalade pas : il ne pourrait
        pas exécuter la tâche passée à Claude Code."""
        installed = bool(self.workers.can_escalate())
        allowed = self.cfg.escalation_enabled()
        can = installed and allowed and worker in (None, CLAUDE_WORKER)
        before = {T.int_id(t): t for t in self.store.snapshot() if t.get("status") == T.BLOCKED}
        n = self.store.requeue_blocked(goal, escalate=can)
        if not n:
            return 0
        for t in self.store.snapshot():
            old = before.get(T.int_id(t))
            if old is None or t.get("status") != T.TODO:
                continue
            self._explain_requeue(old, t, installed, allowed)
        return n

    def _explain_requeue(self, old: dict, new: dict, installed: bool, allowed: bool) -> None:
        """Dit la VRAIE cause de la reprise — jamais « trop dure » sans refus du CEO."""
        if old.get("interrupted"):
            return
        tid, who = new["id"], old.get("worker")
        rung = rung_for(new)
        if rung is not None and current_tier(new) > current_tier(old):
            step = (f"Claude Code monte à {rung.label}" if who == CLAUDE_WORKER
                    else f"Claude Code reprend ({rung.label})")
            text = (f"Tâche [{tid}] : le CEO a refusé le résultat de {who} → {step}, "
                    f"essai {int(new.get('attempts') or 0) + 1}/{T.MAX_ATTEMPTS}.")
            self.log(text)
            self._say("event", text, "escalation", {"id": tid, "worker": new.get("worker"),
                                                   "tier": current_tier(new), "model": rung.model,
                                                   "effort": rung.effort})
            return
        self.log(f"Tâche [{tid}] : {self._why_no_escalation(old, installed, allowed)}, nouvel essai sur {who}.")

    def _why_no_escalation(self, old: dict, installed: bool, allowed: bool) -> str:
        if old.get("last_failure") == WORKER_ERROR:
            return (f"{old.get('worker')} a échoué à l'exécution (délai dépassé, model en panne ou erreur) "
                    "— ce n'est pas de la difficulté, pas de Claude")
        if old.get("last_failure") != CEO_REFUSED:
            return "reprise"
        if current_tier(old) >= MAX_TIER:
            return "le CEO a refusé le résultat ; palier maximal déjà atteint"
        if not allowed:
            return "le CEO a refusé le résultat ; escalade désactivée dans les Paramètres"
        if not installed:
            return "le CEO a refusé le résultat ; renfort Claude Code indisponible (CLI introuvable)"
        return (f"le CEO a refusé le résultat ; plafond de {MAX_ESCALATED_PER_PLAN} tâches reprises "
                "par Claude pour cette demande atteint")

    def _explain_idle(self, waiting, worker: Optional[str], goal: str) -> int:
        if waiting:
            w, b = waiting
            if b.get("status") in (T.BLOCKED, T.FAILED):
                hint = "Relance-la avec « retry » (bouton « Réessayer bloquées »)."
            elif b.get("status") == T.IN_PROGRESS:
                hint = "Elle est en cours ailleurs : attends sa fin."
            else:
                hint = f"Elle doit être faite d'abord (worker {b.get('worker')})."
            self.log(f"Rien d'exécutable : [{w.get('id')}] attend [{b.get('id')}] {b.get('title', '?')} "
                     f"({b.get('status')}). {hint}")
            return 1
        scope = (f" pour {worker}" if worker else "") + (f" pour l'objectif « {goal[:60]} »" if goal else "")
        self.log(f"Rien à faire (aucune tâche todo{scope}).")
        return 0

    def _check_command(self) -> str:
        """Commande de vérification de ce dossier : réglée, sinon détectée ET autorisée.
        Une commande détectée exécute le code du projet : sans ton accord, rien n'est lancé
        et on le dit (une fois par commande)."""
        status = self.cfg.verify_status(self.paths.root)
        said = (status.source, status.cmd)
        if status.source in ("pending", "changed") and self._announced_check != said:
            self._announced_check = said
            self.log(self._consent_needed(status))
        elif status.source == "detected" and self._announced_check != said:
            self._announced_check = said
            self.log(f"Vérification automatique : « {status.cmd} » détectée dans le projet et autorisée "
                     "pour ce dossier (change-la ou écris « off » dans Paramètres pour la couper).")
        return status.runnable

    @staticmethod
    def _consent_needed(status) -> str:
        files = ", ".join(status.files) or "le projet"
        if status.source == "changed":
            return (f"Vérification automatique NON lancée : {files} a changé depuis ton accord pour "
                    f"« {status.cmd} ». Relis-le, puis autorise-la de nouveau dans Paramètres → "
                    "« Vérification de CE dossier ».")
        return (f"Vérification automatique NON lancée : « {status.cmd} » a été détectée ({files}) mais "
                "elle exécute le code de ce dossier. Autorise-la dans Paramètres → « Vérification de "
                "CE dossier » (une fois par dossier), ou écris ta propre commande.")

    def _verify(self, rec: dict, run_id: str, cmd: str, before: Optional[str],
                cancel: Optional[threading.Event]):
        """Commande de vérification du projet, entre l'agent et le CEO. → (tâche close si
        rouge/impossible/arrêt, sinon None ; bloc « tests verts » pour l'audit du CEO)."""
        if not cmd:
            return None, ""
        was_red = rec.get("last_failure") == CHECK_FAILED  # tests connus rouges : jamais sautés
        if not was_red and before is not None and fingerprint(self.paths.root) == before:
            self.log(f"[{rec['id']}] aucun fichier modifié : vérification « {cmd} » inutile.")
            return None, not_run_block(f"aucun fichier modifié, « {cmd} » n'a pas été lancée")
        now = self.cfg.verify_status(self.paths.root)
        if now.source in ("pending", "changed"):
            # Le fichier qui définit la commande (Makefile, package.json…) a changé pendant la
            # tâche : ton accord portait sur l'ancien contenu → rien n'est lancé, on le dit.
            self._announced_check = (now.source, now.cmd)
            self.log(f"[{rec['id']}] " + self._consent_needed(now))
            return None, not_run_block(f"« {cmd} » attend ton autorisation dans les Paramètres")
        if self.store.begin_verify(rec["id"], run_id) is None:
            return rec, ""  # la tâche n'est plus à nous (reprise ailleurs) : on n'y touche pas
        self.log(f"[{rec['id']}] Vérification automatique : {cmd}…")
        out = self.workers.verify(cmd, cancel,
                                  on_spawn=lambda pid, name: self.store.set_child(rec["id"], run_id, pid, name))
        if out.cancelled:
            return self.store.interrupt_review(rec["id"], run_id) or rec, ""
        if out.code == 0:
            self.store.end_verify(rec["id"], run_id)
            self._say("event", f"Vérification « {cmd} » réussie pour la tâche [{rec['id']}] : le CEO audite.",
                      "check", {"id": rec["id"], "ok": True, "cmd": cmd})
            return None, check_block(cmd, out.text)
        return self._check_failed(rec, run_id, cmd, out), ""

    def _check_failed(self, rec: dict, run_id: str, cmd: str, out: WorkOutcome) -> dict:
        broken = INFRA_CODES.get(out.code, "") or broken_reason(cmd, out.code, out.text)
        fin = self.store.fail_check(rec["id"], run_id, cmd, out.text, broken)
        self._usage["avoided"] += 1
        if broken:
            text = (f"Vérification « {cmd} » impossible ({broken}) pour la tâche [{rec['id']}] : "
                    "corrige la commande dans les Paramètres, puis « Réessayer ». Aucun appel à Claude.")
        else:
            text = (f"Vérification « {cmd} » échouée pour la tâche [{rec['id']}] : elle repart sur "
                    f"{rec.get('worker')} avec l'erreur, sans appel à Claude.")
        self.log(text + "\n" + tail(out.text, 800))
        self._say("event", text, "check", {"id": rec["id"], "ok": False, "cmd": cmd, "broken": bool(broken)})
        return fin or rec

    def _supervise(self, rec: dict, run_id: str, cancel: Optional[threading.Event],
                   check: str = "") -> Optional[dict]:
        self.log("--- Supervision CEO ---")
        ans = self._ask(CEO_SUPERVISE_PROMPT.format(
            title=rec.get("title"), worker=rec.get("worker"), detail=rec.get("detail", ""),
            result=supervision_view(rec.get("result")), check=check), cancel, tools=READ_TOOLS,
            # suivi comme le worker : tué par recover_stale si l'orchestrateur meurt
            on_spawn=lambda pid, name: self.store.set_child(rec["id"], run_id, pid, name))
        if ans.cancelled:
            stopped = self.store.interrupt_review(rec["id"], run_id, phase="review")
            self.log(f"[{rec['id']}] arrêt pendant l'audit du CEO : rien n'est validé, la tâche repart en file.")
            self._say("event", "Arrêté pendant la vérification du CEO : rien n'est validé, la tâche "
                               "pourra reprendre.", "stopped", {"id": rec["id"]})
            self._stop_said = True
            return stopped
        verdict, note = None, ""
        if ans.text is None:
            self.log(f"{ans.error} → tâche NON validée par le CEO (done non supervisé).")
        else:
            self.log(ans.text.strip()[:1500])
            verdict, note = parse_verdict(ans.text)
            if verdict is None:
                self.log("(pas de VERDICT exploitable : tâche NON validée par le CEO, done non supervisé)")
        fin = self.store.finalize(rec["id"], run_id, verdict, note)
        if fin is not None:
            self._say("event", note or ans.error or "", "verdict",
                      {"id": fin["id"], "verdict": fin.get("verdict")})
        if fin is not None and fin["status"] in (T.BLOCKED, T.FAILED):
            why = ("tentatives épuisées : « retry » pour recommencer" if fin["status"] == T.FAILED
                   else "son retour sera transmis au worker au prochain essai")
            self.log(f"Le CEO refuse le résultat de [{fin['id']}] → {fin['status']} ({why}).")
        return fin

    # ---- rapport du CEO ---------------------------------------------------
    def _maybe_report(self, fin: dict, cancel: Optional[threading.Event]) -> None:
        """Dernière tâche du plan validée : le CEO répond à l'utilisateur. Un échec ici
        (CEO indisponible) est dit dans le journal mais ne change pas le résultat du tick."""
        key = T.group_key(fin)
        if key is None:
            return
        plan = [t for t in self.store.snapshot() if T.group_key(t) == key]
        if plan and all(t.get("status") == T.DONE for t in plan):
            self.report(norm_goal(fin.get("goal")), cancel=cancel)

    def report(self, goal: str = "", dry_run: bool = False,
               cancel: Optional[threading.Event] = None) -> int:
        """Le CEO rédige la réponse finale à la demande de l'utilisateur (sans goal : la dernière)."""
        tasks = self.store.snapshot()
        goal = norm_goal(goal) or next((norm_goal(t.get("goal")) for t in reversed(tasks)
                                        if norm_goal(t.get("goal"))), "")
        mine = [t for t in tasks if goal and norm_goal(t.get("goal")) == goal]
        if not mine:
            self.log("Rapport impossible : aucune tâche pour cet objectif.")
            return 1
        if dry_run:
            self.log("(dry-run : rapport du CEO ignoré)")
            return 0
        self.log("CEO : rédaction du rapport final…")
        self._report_tried = True  # réussi ou non : _drive ne le redemande pas (ni coût, ni message en double)
        ans = self._ask(build_report_prompt(goal, mine), cancel)
        if ans.text is None or not ans.text.strip():
            why = ans.error or "réponse vide du CEO"
            self.log(f"Rapport impossible : {why}")
            if not ans.cancelled:  # un arrêt demandé n'est pas une panne à signaler
                self.say_error(f"Je n'ai pas pu rédiger le rapport final : {why}")
            return 1
        text = tail(ans.text.strip(), 4000)
        # Travail dur FINI : tout le plan réussi ET une tâche reprise par Claude. Jamais tant qu'une
        # tâche a échoué ou attend : le CEO donne alors lui-même son conseil (« Opus élevé pour la 19 »).
        if mine and all(t.get("status") == T.DONE for t in mine) and any(current_tier(t) >= 1 for t in mine):
            text += "\n\n" + FOLLOWUP_ADVICE
        self.store.add_report(goal, text)
        self.log("--- Rapport du CEO ---\n" + text)
        self._say("ceo", text, "report", {"goal": goal})
        return 0

    # ---- conversation ------------------------------------------------------
    def converse(self, message: str, dry_run: bool = False,
                 cancel: Optional[threading.Event] = None, attachments: str = "") -> int:
        """Un tour de discussion : le CEO répond ; s'il faut agir, il délègue à ses workers,
        vérifie chaque résultat, puis te fait son rapport. 0 = tout est fait, 1 sinon.
        Se termine toujours par le bilan de consommation Claude de la demande."""
        self._usage = self._new_usage()
        try:
            return self._converse_turn(message, dry_run, cancel, attachments)
        finally:
            self._say_usage()

    def _converse_turn(self, message: str, dry_run: bool,
                       cancel: Optional[threading.Event], attachments: str = "") -> int:
        message = str(message or "").strip()
        if not message:
            self.log("Message vide : écris quelque chose au CEO.")
            return 1
        cut = len(message) > MESSAGE_MAX
        message = message[:MESSAGE_MAX]
        block, dropped = fit_block(str(attachments or "").strip(), ATTACH_MAX)
        self._announce_settings()
        history = self.chat.recent(CHAT_HISTORY_MESSAGES) if self.chat else []
        files = attached_names(block)
        self._say("user", message, "", {"files": files[:20]} if files else None)
        if cut:
            self._say("event", f"Ton message dépassait {MESSAGE_MAX} caractères : la fin n'a pas été "
                               "transmise au CEO. Raccourcis-le ou envoie-le en plusieurs fois.", "message_cut")
        if dropped:
            self._say("event", f"{dropped} fichier(s) joint(s) écarté(s) : trop volumineux pour un seul "
                               "message. Joins-les dans un message suivant.", "attachments_dropped")
        avail = self._offered_workers()
        turn = self._ask_chat(message + ("\n\n" + block if block else ""), history, avail, dry_run, cancel)
        if turn is None:
            return 1
        reply, items = turn
        if reply:
            self._say("ceo", reply, "reply")
        if not items:
            return 0
        if not avail and not dry_run:
            self._say("ceo", "Je n'ai aucun worker disponible : je ne peux pas déléguer ce travail.", "error")
            return 1
        _, ids = self.store.add_plan(message, items, if_absent=False)
        self.log(f"Plan CEO : {len(ids)} tâche(s) ajoutée(s) {ids}.")
        self._say("event", f"Plan : {len(ids)} tâche(s)", "plan", {"tasks": [
            {"id": i, "title": it["title"], "worker": it["worker"]} for i, it in zip(ids, items)]})
        return self._drive(message, ids, dry_run, cancel)

    def _ask_chat(self, message: str, history: List[dict], avail: List[str], dry_run: bool,
                  cancel: Optional[threading.Event]):
        """(réponse, tâches) ou None si le CEO n'a pas pu répondre (dit dans la conversation)."""
        if dry_run:
            return "Modèle conseillé : Sonnet 5.5 · faible — simulation (dry-run)\n[dry-run] Je délègue à mon équipe.", [
                {"title": "Tâche démo (dry-run)", "worker": DEFAULT_WORKER, "detail": "ne rien faire, répondre OK"}]
        workers = avail or [DEFAULT_WORKER]
        ans = self._ask(self._cloud_prefix() + build_chat_prompt(message, history, workers), cancel)
        if ans.cancelled:
            self._say("event", "Arrêté à ta demande.", "stopped")
            return None
        if ans.text is None:
            self._say("ceo", f"Je ne peux pas te répondre pour l'instant : {ans.error}", "error")
            return None
        reply, items, notes = parse_chat(ans.text, workers)
        for note in notes:
            self.log("Conversation : " + note)
        if not reply and not items:
            self._say("ceo", "Ma réponse est vide : peux-tu reformuler ta demande ?", "error")
            return None
        advice = effort_advice(ans.text)
        return (f"{advice}\n{reply}" if reply else advice), items

    def _drive(self, goal: str, ids: List[int], dry_run: bool, cancel: Optional[threading.Event]) -> int:
        """Exécute le plan jusqu'au bout : reprise automatique après un refus du CEO."""
        stopped = False
        self._report_tried = self._stop_said = False
        for _ in range(len(ids) * T.MAX_ATTEMPTS + 2):
            if cancel is not None and cancel.is_set():
                stopped = True
                break
            self._requeue(goal)
            report = self.run_next(None, None, dry_run, supervise=True, goal=goal, cancel=cancel)
            if report.task is None:
                break
        stopped = stopped or (cancel is not None and cancel.is_set())
        mine = [t for t in self.store.snapshot() if t.get("id") in ids]
        done = all(t.get("status") == T.DONE for t in mine)
        # « done non supervisé » n'est pas un succès : le code retour et le rapport le disent.
        validated = done and (dry_run or all(t.get("verdict") == "ok" for t in mine))
        if stopped and not done:
            if not self._stop_said:
                self._say("event", "Arrêté à ta demande : le travail en cours est interrompu et pourra reprendre.",
                          "stopped")
            return 1
        if not validated and not dry_run and not self._report_tried:
            self.report(goal, cancel=cancel)  # le CEO explique honnêtement ce qui a échoué ou manque
        return 0 if validated else 1

    # ---- tick -------------------------------------------------------------
    def tick(self, goal: str, worker: Optional[str] = None, model: Optional[str] = None,
             dry_run: bool = False, cancel: Optional[threading.Event] = None) -> int:
        """Reprise des blocked → plan si l'objectif est nouveau → 1 tâche → supervision."""
        goal = norm_goal(goal)
        self.recover()
        n = self._requeue(goal, worker)
        if n:
            self.log(f"{n} tâche(s) blocked reprise(s) (tentatives restantes).")
        tasks = self.store.snapshot()
        if goal:
            same = [t for t in tasks if norm_goal(t.get("goal")) == goal]
            if not same:
                rc = self.plan(goal, dry_run, cancel)
                if rc:
                    return rc
            elif not any(t.get("status") == T.TODO for t in same):
                return self._goal_idle(same)
        elif not any(t.get("status") == T.TODO for t in tasks):
            stuck = any(t.get("status") in (T.BLOCKED, T.FAILED) for t in tasks)
            self.log("Rien à exécuter : " + ("des tâches sont blocked/failed ; « retry » (bouton « Réessayer "
                     "bloquées ») pour les relancer, ou donne un nouvel objectif." if stuck
                     else "donne un objectif."))
            return 1
        return self.run_next(worker, model, dry_run, supervise=True, goal=goal, cancel=cancel).rc

    def _goal_idle(self, same: List[dict]) -> int:
        running = [t["id"] for t in same if t.get("status") == T.IN_PROGRESS]
        if running:
            self.log(f"Objectif en cours ailleurs : tâche(s) {running} en exécution.")
            return 0
        stuck = [t["id"] for t in same if t.get("status") in (T.BLOCKED, T.FAILED)]
        self.log(f"Objectif déjà planifié ({len(same)} tâche(s))"
                 + (f", à relancer via « retry » : {stuck}" if stuck else ", terminé")
                 + ". Change le texte de l'objectif pour en démarrer un nouveau.")
        return 1 if stuck else 0

    # ---- divers -----------------------------------------------------------
    def retry(self) -> int:
        n = self.store.reset_stuck()
        self.log(f"{n} tâche(s) remise(s) en file d'attente.")
        return 0

    def status_data(self) -> dict:
        """État pour `status --json` : mêmes informations que status_lines, sans chemin absolu.
        `problem` non vide = tasks.json inutilisable (et alors aucune tâche n'est inventée)."""
        data = {"team": TEAM_NAME, "workers": list(self.workers.available()),
                "model": self.cfg.model(None, self.default_model), "problem": "",
                "counts": {s: 0 for s in T.STATUSES}, "tasks": []}
        try:
            tasks = self.store.snapshot()
        except StoreError as e:
            return {**data, "problem": str(e)}
        for t in tasks:
            owner = t.get("owner") if isinstance(t.get("owner"), dict) else {}
            data["counts"][t.get("status")] += 1
            data["tasks"].append({"id": t.get("id"), "status": t.get("status"), "worker": t.get("worker"),
                                  "title": t.get("title", "?"), "verdict": t.get("verdict"),
                                  "attempts": t.get("attempts", 0), "phase": owner.get("phase")})
        return data

    def status_lines(self) -> List[str]:
        lines = [f"{TEAM_NAME} : CEO=claude-code | workers dispo : {', '.join(self.workers.available()) or 'aucun'}",
                 f"Model opencode actif : {self.cfg.model(None, self.default_model)} "
                 "(modifiable via --model ou l'interface)"]
        try:
            tasks = self.store.snapshot()
        except StoreError as e:
            return lines + [f"{PROBLEM_PREFIX}{e}"]
        counts = {s: [t["id"] for t in tasks if t.get("status") == s] for s in T.STATUSES}
        lines.append(f"Tâches : {len(tasks)} | " + " ".join(f"{s}={ids}" for s, ids in counts.items() if ids))
        return lines + ["  " + task_line(t) for t in tasks]
