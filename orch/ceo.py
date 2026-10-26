"""Le CEO (`claude -p`) : appel, lecture du plan et du verdict."""
from __future__ import annotations

import json
import unicodedata
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .claude_cli import JSON_OUTPUT, LEAN_ARGS, NO_TOOLS, READ_TOOLS, read_result  # noqa: F401 (réexport)
from .effort import CLI_EFFORT, clean_effort
from .procs import CmdResult, run_cmd
from .prompts import PLAN_MAX_TASKS, PLAN_MIN_TASKS

CEO_TIMEOUT_S = 600
REPLY_MAX = 4000
CEO_MODEL_SUGGESTIONS = ["sonnet", "opus", "haiku", "fable"]  # alias `claude --model`
# Alias (« sonnet ») ou nom complet ; jamais d'espace, de « - » initial ni de caractère spécial :
# la valeur est passée telle quelle à `claude --model`, elle ne doit pas pouvoir devenir une option.
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,99}$")

# Mode d'exécution du CEO : local (ton Mac) ou cloud (session distante claude.ai).
# Une session cloud existante : id `session_...`/`cse_...` ou URL `claude.ai/code/...`.
CEO_MODES = ("local", "cloud")
_CLOUD_SESSION_RE = re.compile(r"^(session_[A-Za-z0-9_.-]{3,200}|cse_[A-Za-z0-9_.-]{3,200}|"
                               r"https://claude\.ai/code/[A-Za-z0-9_.-]{3,300})$")


def clean_model(value: object) -> str:
    """Model demandé pour le CEO, ou « » s'il est vide ou invalide."""
    text = value.strip() if isinstance(value, str) else ""
    return text if _MODEL_RE.match(text) else ""


def clean_ceo_mode(value: object) -> str:
    """Mode du CEO (« local » ou « cloud »), ou « » si invalide."""
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if text in CEO_MODES else ""


def clean_cloud_session(value: object) -> str:
    """Id ou URL d'une session cloud existante, ou « » si vide ou invalide."""
    text = value.strip() if isinstance(value, str) else ""
    return text if _CLOUD_SESSION_RE.match(text) else ""

# Ancré en début de ligne (une citation en milieu de phrase ne compte pas) ;
# tolère gras/code Markdown, numérotation, espaces insécables ; refuse l'écho du
# gabarit « ok|ko » et les alternatives « ok / ko », « ko ou ok ».
_VERDICT_RE = re.compile(
    r"^[ \t>#*_`-]*(?:\d+[.)][ \t]+)?VERDICT[ \t  *_`]*:"
    r"[ \t  *_`\"']*(ok|ko)\b(?![ \t]*(?:\||/|ou\b|or\b))",
    re.IGNORECASE | re.MULTILINE)


class PlanError(ValueError):
    """Réponse du CEO inutilisable comme plan : rien n'est enregistré."""


def parse_verdict(answer: str) -> Tuple[Optional[str], str]:
    """(verdict, note) ; (None, texte) si absent, contradictoire ou CEO en erreur."""
    if answer.lstrip().startswith("CEO-ERROR("):
        return None, answer.strip()
    found = list(_VERDICT_RE.finditer(answer))
    verdicts = {m.group(1).lower() for m in found}
    if len(verdicts) != 1:
        return None, answer.strip()
    m = found[-1]  # le verdict final, pas une citation
    note = (answer[:m.start()] + " " + answer[m.end():]).strip(" \n\t:-—–.*_`")
    return verdicts.pop(), note


def _first_json_list(raw: str) -> Optional[list]:
    dec = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch != "[":
            continue
        try:
            value, _ = dec.raw_decode(raw, i)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and any(isinstance(v, dict) for v in value):
            return value
    return None


def _validate_items(data: list, workers: List[str]) -> Tuple[List[dict], List[str]]:
    items, notes = [], []
    for pos, it in enumerate(data, 1):
        title = str(it.get("title") or "").strip() if isinstance(it, dict) else ""
        if not title:
            notes.append(f"élément n°{pos} ignoré : pas de titre.")
            continue
        worker = str(it.get("worker") or "")
        if worker not in workers:
            notes.append(f"« {title[:60]} » : worker {worker or '(vide)'!r} indisponible → {workers[0]}.")
            worker = workers[0]
        items.append({"title": title[:200], "worker": worker,
                      "detail": str(it.get("detail") or "")[:2000]})
    return items, notes


def parse_plan(raw: str, workers: List[str]) -> Tuple[List[dict], List[str]]:
    """Plan du CEO → tâches validées + remarques (éléments écartés, workers remplacés)."""
    data = _first_json_list(raw)
    if data is None:
        raise PlanError("le CEO n'a pas renvoyé de liste JSON de tâches.")
    items, notes = _validate_items(data, workers)
    if not PLAN_MIN_TASKS <= len(items) <= PLAN_MAX_TASKS:
        raise PlanError(f"plan de {len(items)} tâche(s) valide(s) : attendu entre "
                        f"{PLAN_MIN_TASKS} et {PLAN_MAX_TASKS}. Reformule l'objectif.")
    return items, notes


def _first_json_object(raw: str) -> Optional[dict]:
    dec = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            value, _ = dec.raw_decode(raw, i)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and ("reply" in value or "tasks" in value):
            return value
    return None


def parse_chat(raw: str, workers: List[str]) -> Tuple[str, List[dict], List[str]]:
    """Réponse du CEO à un message → (réponse, tâches à déléguer, remarques).

    Une réponse en prose (sans JSON) reste une réponse valable : jamais de perte."""
    obj = _first_json_object(raw)
    if obj is None:
        return raw.strip()[:REPLY_MAX], [], ["réponse du CEO sans JSON : gardée comme simple réponse."]
    reply = str(obj.get("reply") or "").strip()[:REPLY_MAX]
    tasks, notes = obj.get("tasks") or [], []
    if not isinstance(tasks, list):
        return reply, [], ["« tasks » n'est pas une liste : aucune tâche créée."]
    items, notes = _validate_items(tasks, workers)
    if len(items) > PLAN_MAX_TASKS:
        notes.append(f"plan tronqué à {PLAN_MAX_TASKS} tâches ({len(items)} proposées).")
        items = items[:PLAN_MAX_TASKS]
    return reply, items, notes


EFFORTS = ("faible", "élevé", "max", "ultracode")
ADVICE_UNKNOWN = "Modèle conseillé : non précisé par le CEO"
ADVICE_MODEL_MAX, ADVICE_REASON_MAX = 30, 120


def _one_line(value: object, limit: int) -> str:
    return " ".join(unicodedata.normalize("NFC", str(value or "")).split())[:limit]


def effort_advice(raw: str) -> str:
    """Première ligne de chaque réponse : « Modèle conseillé : <modèle> · <effort> — <raison> ».

    Construite ici, pas par le modèle : format et niveaux sont garantis. Si le CEO ne
    donne pas de conseil valide, on le dit — jamais de valeur plausible inventée."""
    obj = _first_json_object(raw)
    advice = obj.get("conseil") if obj else None
    if not isinstance(advice, dict):
        return ADVICE_UNKNOWN
    model = _one_line(advice.get("modele"), ADVICE_MODEL_MAX)
    effort = _one_line(advice.get("effort"), 20).lower()
    reason = _one_line(advice.get("raison"), ADVICE_REASON_MAX)
    if not model or effort not in EFFORTS:
        return ADVICE_UNKNOWN
    return f"Modèle conseillé : {model} · {effort}" + (f" — {reason}" if reason else "")


@dataclass(frozen=True)
class CeoAnswer:
    text: Optional[str]  # stdout du CEO ; None si l'appel a échoué
    error: str = ""
    cancelled: bool = False
    tokens: Optional[int] = None  # tokens traités par cet appel ; None = non mesuré


class Ceo:
    """Appel de `claude -p` : seul stdout est interprété, stderr va au journal.

    Mode local : `claude -p <prompt>` (ton Mac).
    Mode cloud : `claude -p --cloud <session> <prompt>` (session distante
    claude.ai existante — avec `-p`, le CLI exige un id ou une URL, jamais
    une description de création).
    """

    def __init__(self, claude_bin: str, cwd: str, log: Callable[[str], None],
                 runner: Callable[..., CmdResult] = run_cmd,
                 model: Optional[Callable[[], str]] = None,
                 mode: Optional[Callable[[], str]] = None,
                 cloud_session: Optional[Callable[[], str]] = None,
                 cloud_repo: Optional[Callable[[], str]] = None,
                 effort: Optional[Callable[[], str]] = None) -> None:
        self._bin, self._cwd, self._log, self._run = claude_bin, cwd, log, runner
        self._effort = effort
        self._model = model
        self._mode = mode
        self._cloud_session = cloud_session
        self._cloud_repo = cloud_repo

    def _model_args(self) -> List[str]:
        raw = self._model() if self._model else ""
        if not raw:
            return []
        model = clean_model(raw)
        if not model:
            self._log(f"réglage « model du CEO » invalide ({str(raw)[:40]!r}) : ignoré, model par défaut.")
        return ["--model", model] if model else []

    def _effort_args(self) -> List[str]:
        raw = self._effort() if self._effort else ""
        level = clean_effort(raw)
        if level is None:
            self._log(f"réglage « effort du CEO » invalide ({str(raw)[:20]!r}) : ignoré, effort par défaut.")
        return ["--effort", CLI_EFFORT[level]] if level else []

    FALLBACK_NOTE = ("Le mode « cloud » est demandé mais aucune session valide n'est réglée : le CEO "
                     "tourne en local sur ton Mac. Colle l'id session_... ou l'URL claude.ai/code/... "
                     "dans Paramètres pour passer vraiment en cloud.")

    def _resolve_mode(self) -> Tuple[List[str], str, str]:
        """(args `--cloud`, mode EFFECTIF, raison du repli ou « »). Sans effet de bord."""
        if clean_ceo_mode(self._mode() if self._mode else "") != "cloud":
            return [], "local", ""
        session = clean_cloud_session(self._cloud_session() if self._cloud_session else "")
        if not session:
            return [], "local", self.FALLBACK_NOTE
        return ["--cloud", session], "cloud", ""

    def effective_mode(self) -> str:
        """Le mode qui tourne VRAIMENT (le réglage « cloud » sans session donne « local »)."""
        return self._resolve_mode()[1]

    def fallback_note(self) -> str:
        """Pourquoi le mode demandé n'est pas celui qui tourne, ou « »."""
        return self._resolve_mode()[2]

    def _cloud_args(self) -> Tuple[List[str], str]:
        """Args `--cloud` + mode effectif (« local » par défaut)."""
        args, mode, why = self._resolve_mode()
        if why:
            self._log("mode CEO « cloud » sans session valide : repli sur le mode local "
                      "(colle l'id session_... ou l'URL claude.ai/code/... dans les réglages).")
        return args, mode

    def cloud_context(self) -> str:
        """Bloc à mettre en tête des prompts en mode cloud (dépôt GitHub), ou « » en local."""
        _, mode = self._cloud_args()
        if mode != "cloud":
            return ""
        from .prompts import cloud_repo_block
        return cloud_repo_block(self._cloud_repo() if self._cloud_repo else "") + "\n\n"

    def _command(self, prompt: str, tools: str) -> Tuple[List[str], bool]:
        """(commande, enveloppe JSON attendue). Local : appel sobre (mesuré ~7–10 k tokens au
        lieu de ~40 k). Cloud : inchangé, la session distante a son propre contexte."""
        cloud_args, mode = self._cloud_args()
        head = [self._bin, *self._model_args(), *self._effort_args()]
        if mode == "cloud":
            return [*head, *cloud_args, "-p", prompt], False
        return [*head, *LEAN_ARGS, "--tools", tools, *JSON_OUTPUT, "-p", prompt], True

    def ask(self, prompt: str, cancel: Optional[threading.Event] = None,
            on_spawn: Optional[Callable[[int, str], None]] = None, tools: str = NO_TOOLS) -> CeoAnswer:
        # Pas de --dangerously-skip-permissions : au plus de la lecture (supervision), jamais
        # un shell alimenté par les sorties des workers.
        name = Path(self._bin).name
        spawn = (lambda pid: on_spawn(pid, name)) if on_spawn else None
        cmd, enveloped = self._command(prompt, tools)
        res = self._run(cmd, timeout=CEO_TIMEOUT_S, cwd=self._cwd, cancel=cancel, on_spawn=spawn)
        if res.err.strip():
            self._log("CEO (stderr) : " + res.err.strip()[-500:])
        if res.cancelled:
            return CeoAnswer(None, "annulé", cancelled=True)
        if not res.ok:
            why = "délai dépassé" if res.timed_out else f"code {res.code}"
            env = read_result(res.out) if enveloped else None
            if env is not None and env.text is None and env.error:  # le message de Claude, pas la fin du JSON
                return CeoAnswer(None, f"CEO indisponible ({why}) : {env.error}", tokens=env.tokens)
            return CeoAnswer(None, f"CEO indisponible ({why}) : {res.err.strip()[-300:] or res.out[-300:]}")
        if not enveloped:
            return CeoAnswer(res.out)
        got = read_result(res.out)
        if got.text is None:
            return CeoAnswer(None, f"CEO indisponible : {got.error}", tokens=got.tokens)
        return CeoAnswer(got.text, tokens=got.tokens)
