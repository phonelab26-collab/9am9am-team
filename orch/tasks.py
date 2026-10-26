"""Domaine pur des tâches : validation, groupes, dépendances, transitions.

Les tâches restent des `dict` (champs inconnus préservés) ; aucune fonction ne
modifie ses arguments : chacune renvoie de nouveaux objets.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .escalation import CEO_REFUSED, MAX_ESCALATED_PER_PLAN, escalated, was_escalated
from .textutil import norm_goal
from .verify import CHECK_ERROR, CHECK_FAILED, CHECK_KO, error_note, failure_note

TODO, IN_PROGRESS, DONE, BLOCKED, FAILED = "todo", "in_progress", "done", "blocked", "failed"
STATUSES = (TODO, IN_PROGRESS, DONE, BLOCKED, FAILED)
MAX_ATTEMPTS = 3  # exécutions par tâche avant `failed`
REVIEW_MAX = 800  # caractères du retour CEO gardés pour l'essai suivant
UNSUPERVISED = "non_supervise"  # `done` sans verdict du CEO (run seul, CEO indisponible)
_STR_FIELDS = ("title", "worker", "detail", "goal", "result", "review", "plan_id")

Task = Dict[str, object]
_TITLE_BREAKS = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]+")  # sauts de ligne, échappements…

# tasks.json = {"schema": 2, "tasks": [...]} — le schema dit au lecteur quelle
# forme de fichier il va trouver. Un fichier plus récent que SCHEMA est refusé
# (on ne réécrit jamais par-dessus un format qu'on ne comprend pas).
# Le format d'origine (une simple liste) vaut schema 1 : lu tel quel, jamais
# réécriture forcée.
SCHEMA = 2


class TaskFileError(ValueError):
    """tasks.json lisible mais incohérent : on refuse d'écrire par-dessus."""


def pack(tasks: List[Task]) -> dict:
    """tasks -> enveloppe versionnée à écrire sur disque."""
    return {"schema": SCHEMA, "tasks": tasks}


def unpack(data: object) -> List[Task]:
    """Enveloppe lue du disque -> liste de tâches validée.

    - liste (ancien format, schema 1) : validée telle quelle ;
    - objet {"schema": n, "tasks": [...]} : n > SCHEMA = refus (version plus
      récente) ; n < 2 ou tasks absent = enveloppe corrompue.
    La lecture n'écrit jamais : la migration au format courant a lieu à la
    première écriture.
    """
    if isinstance(data, list):
        return _one_line_titles(validate(data))
    if not isinstance(data, dict):
        raise TaskFileError("tasks.json : ni liste ni objet {schema, tasks}.")
    schema = data.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        raise TaskFileError(f"tasks.json : schema invalide {schema!r}.")
    if schema > SCHEMA:
        raise TaskFileError(f"tasks.json : schema {schema} > {SCHEMA} — fichier écrit "
                            f"par une version plus récente, ne pas écraser (mets à jour l'app).")
    if schema < 2:
        raise TaskFileError(f"tasks.json : schema {schema} sans liste de tâches.")
    if "tasks" not in data:
        raise TaskFileError("tasks.json : enveloppe sans clé « tasks ».")
    return _one_line_titles(validate(data["tasks"]))


def one_line(text: str) -> str:
    """Titre sur UNE ligne : un « \\n » venu d'un tasks.json planté ne fabrique pas une ligne
    de journal (ni de commande) à part."""
    return " ".join(_TITLE_BREAKS.sub(" ", text).split())


def _one_line_titles(tasks: List[Task]) -> List[Task]:
    return [{**t, "title": one_line(t["title"])} if isinstance(t.get("title"), str)
            and _TITLE_BREAKS.search(t["title"]) else t for t in tasks]


def _plain_int(value: object) -> bool:
    """Entier JSON « vrai » : ni booléen (sous-type d'int), ni flottant."""
    return isinstance(value, int) and not isinstance(value, bool)


def int_id(task: Task) -> Optional[int]:
    v = task.get("id")
    return v if _plain_int(v) else None


def validate(data: object) -> List[Task]:
    """Vérifie la forme de tasks.json ; lève TaskFileError avec un message clair."""
    if not isinstance(data, list):
        raise TaskFileError("tasks.json doit contenir une liste de tâches.")
    seen = set()
    for pos, t in enumerate(data, 1):
        if not isinstance(t, dict):
            raise TaskFileError(f"entrée n°{pos} : ce n'est pas un objet JSON.")
        tid = int_id(t)
        if tid is None:
            raise TaskFileError(f"entrée n°{pos} : id {t.get('id')!r} n'est pas un entier.")
        if tid in seen:
            raise TaskFileError(f"id {tid} en double : corrige tasks.json à la main.")
        seen.add(tid)
        if t.get("status") not in STATUSES:
            raise TaskFileError(f"tâche {tid} : statut inconnu {t.get('status')!r}.")
        for field in _STR_FIELDS:
            if field in t and t[field] is not None and not isinstance(t[field], str):
                raise TaskFileError(f"tâche {tid} : le champ {field!r} doit être du texte.")
        # Champs qui pilotent des `int()` (checkout, reprises) ou un `kill` (recover_stale) :
        # une valeur douteuse donnerait un plantage au lieu du message « rien n'est écrit ».
        if "attempts" in t and t["attempts"] is not None and not _plain_int(t["attempts"]):
            raise TaskFileError(f"tâche {tid} : le champ 'attempts' doit être un entier.")
        owner = t.get("owner")
        if owner is not None and not isinstance(owner, dict):
            raise TaskFileError(f"tâche {tid} : le champ 'owner' doit être un objet JSON.")
        pgid = owner.get("child_pgid") if isinstance(owner, dict) else None
        if pgid is not None and not _plain_int(pgid):
            raise TaskFileError(f"tâche {tid} : 'owner.child_pgid' doit être un entier.")
    return data


def group_key(task: Task) -> Optional[str]:
    """Plan d'appartenance : plan_id, sinon objectif (fichiers v1), sinon aucun."""
    if task.get("plan_id"):
        return f"plan:{task['plan_id']}"
    goal = norm_goal(task.get("goal"))
    return f"goal:{goal}" if goal else None


def blocking_task(task: Task, tasks: List[Task]) -> Optional[Task]:
    """Première tâche antérieure du même plan pas encore `done` (plan ordonné).

    Une tâche en revue CEO est encore `in_progress` : elle bloque, car son
    résultat n'est pas validé. Tâches sans plan : jamais bloquées."""
    key, tid = group_key(task), int_id(task)
    if key is None or tid is None:
        return None
    return next((t for t in tasks if group_key(t) == key and t.get("status") != DONE
                 and (int_id(t) or 0) < tid), None)


def next_ready(tasks: List[Task], worker: Optional[str] = None,
               goal: str = "") -> Tuple[Optional[Task], Optional[Tuple[Task, Task]]]:
    """(tâche prête, None) ou (None, (tâche en attente, sa bloquante)) ou (None, None).

    Avec un objectif, seules ses tâches comptent : taper « G » ne doit pas lancer
    une vieille tâche d'un autre objectif."""
    goal = norm_goal(goal)
    todo = [t for t in tasks if t.get("status") == TODO and (not worker or t.get("worker") == worker)
            and (not goal or norm_goal(t.get("goal")) == goal)]
    waiting = None
    for t in todo:
        blocker = blocking_task(t, tasks)
        if blocker is None:
            return t, None
        waiting = waiting or (t, blocker)
    return None, waiting


def apply_verdict(task: Task, verdict: Optional[str], note: str) -> Task:
    """Clôture d'une tâche en revue : ok → done validé ; ko → blocked (failed si
    tentatives épuisées) avec le retour du CEO ; None → done non supervisé."""
    out = {k: v for k, v in task.items() if k != "owner"}
    if verdict == "ko":
        exhausted = int(task.get("attempts") or 0) >= MAX_ATTEMPTS
        out.update(status=FAILED if exhausted else BLOCKED, verdict="ko", last_failure=CEO_REFUSED,
                   review=" ".join(str(note).split())[:REVIEW_MAX]
                   or "résultat jugé insuffisant (aucun détail du CEO)")
        return out
    out.pop("last_failure", None)
    out.update(status=DONE, verdict="ok" if verdict == "ok" else UNSUPERVISED)
    if out.get("review"):
        out["prev_review"] = out.pop("review")
    return out


def apply_check_failure(task: Task, cmd: str, output: str) -> Task:
    """Vérification automatique rouge : comme un refus, mais SANS le CEO — la sortie de
    l'erreur part à l'agent gratuit au prochain essai, et ce n'est jamais un motif d'escalade."""
    out = {k: v for k, v in task.items() if k != "owner"}
    exhausted = int(task.get("attempts") or 0) >= MAX_ATTEMPTS
    out.update(status=FAILED if exhausted else BLOCKED, verdict=CHECK_KO, last_failure=CHECK_FAILED,
               review=failure_note(cmd, output)[:REVIEW_MAX * 4])
    return out


def apply_check_error(task: Task, cmd: str, why: str, output: str) -> Task:
    """La commande de vérification elle-même ne marche pas (introuvable, délai) : échec net,
    sans faire retravailler l'agent sur un réglage qu'il ne peut pas réparer."""
    out = {k: v for k, v in task.items() if k != "owner"}
    out.update(status=FAILED, verdict=CHECK_ERROR, last_failure=CHECK_ERROR,
               review=error_note(cmd, why, output)[:REVIEW_MAX * 4])
    return out


def requeue_blocked(tasks: List[Task], goal: str, escalate: bool = False) -> Tuple[List[Task], int]:
    """Reprise auto : blocked → todo s'il reste des tentatives (objectif donné, ou tous).

    escalate=True : la reprise passe la tâche au palier supérieur (Claude Code), sauf si elle
    a été interrompue (annulation, arrêt du process) ou si le sommet est atteint (elle repart alors telle quelle)."""
    goal = norm_goal(goal)
    taken: Dict[Optional[str], int] = {}  # tâches déjà confiées à Claude, par demande (plafond)
    for t in tasks:
        if was_escalated(t):
            taken[group_key(t)] = taken.get(group_key(t), 0) + 1
    out, n = [], 0
    for t in tasks:
        ok = (t.get("status") == BLOCKED and int(t.get("attempts") or 0) < MAX_ATTEMPTS
              and (not goal or norm_goal(t.get("goal")) == goal))
        if ok:
            room = was_escalated(t) or taken.get(group_key(t), 0) < MAX_ESCALATED_PER_PLAN
            up = escalated(t) if escalate and room else None
            if up is not None and not was_escalated(t):
                taken[group_key(t)] = taken.get(group_key(t), 0) + 1
            t = {k: v for k, v in (up or t).items() if k != "interrupted"}
            t["status"] = TODO
        out.append(t)
        n += ok
    return out, n


def reset_stuck(tasks: List[Task]) -> Tuple[List[Task], int]:
    """Action manuelle « Réessayer » : blocked ET failed → todo, compteur à 0."""
    out, n = [], 0
    for t in tasks:
        stuck = t.get("status") in (BLOCKED, FAILED)
        out.append({**t, "status": TODO, "attempts": 0} if stuck else t)
        n += stuck
    return out, n


def next_id(tasks: List[Task]) -> int:
    return max((int_id(t) or 0 for t in tasks), default=0) + 1


def replace_task(tasks: List[Task], new: Task) -> List[Task]:
    return [new if int_id(t) == int_id(new) else t for t in tasks]


def find(tasks: List[Task], tid: int) -> Optional[Task]:
    return next((t for t in tasks if int_id(t) == tid), None)
