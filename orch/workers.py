"""Workers : opencode (n'importe quel model, repli automatique), autoclaw, cline + customs."""
from __future__ import annotations

import json
import os
import shlex
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from .config import FREE_MODELS, Bins, ConfigStore, Paths
from .claude_cli import JSON_OUTPUT, WORKER_LEAN_ARGS, read_result
from .effort import cli_effort
from .verify import VERIFY_OUTPUT_CHARS, VERIFY_TIMEOUT_S
from .escalation import CLAUDE_WORKER, LADDER
from .free_models import free_models
from .procs import CmdResult, run_cmd
from .registry import SPECS, spec_for
from .shellcheck import shell_syntax_error
from .steps import MAX_STEPS, Step, step_of
from .textutil import tail

MODEL_TIMEOUT_S = 600   # un appel opencode
CLAUDE_WORKER_TIMEOUT_S = 1800  # renfort Claude Code : un effort « max » sur une tâche dure est long
CLAUDE_MODELS = frozenset(r.model for r in LADDER.values())
CLAUDE_EFFORTS = frozenset(r.effort for r in LADDER.values())
TASK_BUDGET_S = 1200    # toutes tentatives de models confondues, pour une tâche
CATALOG_TTL_S = 600
CATALOG_TIMEOUT_S = 120  # `opencode models` : le 1er lancement peut réindexer
CLINE_HUB_TIMEOUT_S = 3  # hub local : au-delà, il est de toute façon hors ligne
CLINE_HUB = os.environ.get("MINI_ORCH_CLINE_HUB", "http://127.0.0.1:25463")
# Panne de model/routeur, cherchée UNIQUEMENT dans stderr et les événements
# `type: error` (jamais dans les sorties d'outils : un `ls` peut dire « not found »).
MODEL_ERROR_MARKERS = ("Cannot connect to API", "does not exist", "Daily check-in", "Insufficient",
                       "connection", "Unable to connect", "Unexpected server error", "APIError",
                       "UnknownError", "not found", "not available", "rate limit", "quota")

OnSpawn = Callable[[int, str], None]


@dataclass(frozen=True)
class WorkOutcome:
    code: int
    text: str
    cancelled: bool = False
    tokens: Optional[int] = None  # tokens Claude consommés (renfort) ; None = non mesuré / gratuit
    steps: Tuple[Step, ...] = ()  # outils utilisés (opencode seulement), sans leur sortie
    steps_omitted: int = 0        # étapes au-delà de MAX_STEPS : comptées, pas gardées
    refused: bool = False         # lancement refusé par un réglage : rien n'a tourné, ne pas retenter
    model: str = ""               # model réellement utilisé (le repli peut différer du choix) ; « » = inconnu


@dataclass(frozen=True)
class _Events:
    texts: Tuple[str, ...]
    errors: Tuple[str, ...]
    used_tools: bool
    session: str
    steps: Tuple[Step, ...] = ()
    steps_omitted: int = 0


def parse_events(raw: str, root: str = "") -> _Events:
    texts, errors, session, tools = [], [], "", False
    steps: List[Step] = []
    omitted = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        session = str(ev.get("sessionID") or session)
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
        kind = ev.get("type")
        tools = tools or kind == "tool_use"
        if kind == "tool_use":
            if len(steps) < MAX_STEPS:
                steps.append(step_of(ev, root))
            else:
                omitted += 1
        if kind == "text" and isinstance(part.get("text"), str) and part["text"].strip():
            texts.append(part["text"])
        err = ev.get("error")
        if kind == "error" and isinstance(err, dict):
            data = err.get("data") if isinstance(err.get("data"), dict) else {}
            errors.append(str(data.get("message") or err.get("name") or "erreur"))
    return _Events(tuple(texts), tuple(errors), tools, session, tuple(steps), omitted)


def summarize(ev: _Events, res: CmdResult) -> str:
    """Flux JSON d'opencode → texte lisible (conclusion en fin de texte)."""
    parts = [f"[session {ev.session}]"] if ev.session else []
    if ev.texts:
        parts.append(tail("\n".join(ev.texts), 3000))
    if ev.errors:
        parts.append("ERREURS : " + " | ".join(dict.fromkeys(ev.errors)))
    if res.timed_out:
        parts.append("(délai dépassé)")
    if not ev.texts and not ev.errors:
        parts.append(tail(res.err or res.out, 1500) or "(aucune sortie)")
    return "\n".join(parts).strip()


class Workers:
    def __init__(self, paths: Paths, bins: Bins, cfg: ConfigStore, log: Callable[[str], None],
                 runner: Callable[..., CmdResult] = run_cmd,
                 clock: Callable[[], float] = time.monotonic,
                 scanned: Optional[Callable[[], List[str]]] = None) -> None:
        # `scanned` : gratuits du dernier scan (interface) ; sans lui, ils sont déduits du catalogue.
        self.scanned = scanned
        self._paths, self._bins, self._cfg, self._log = paths, bins, cfg, log
        self._run, self._clock = runner, clock
        self._catalog: Optional[Tuple[float, Set[str]]] = None
        self._syntax: Dict[str, str] = {}  # commande → erreur de syntaxe (« » = valide), vérifiée une fois

    def available(self) -> List[str]:
        out = []
        for spec in SPECS:
            if spec.is_shell:
                if self._usable_cmd(spec.name, self._cfg.worker_cmd(spec.name)):
                    out.append(spec.name)
            elif os.path.isfile(self._bins.opencode) and os.access(self._bins.opencode, os.X_OK):
                out.append(spec.name)
        if self.can_escalate():
            out.append(CLAUDE_WORKER)
        out += [a["name"] for a in self._cfg.custom_agents()
                if a["name"] not in out and self._usable_cmd(a["name"], a["cmd"])]
        return out

    def _usable_cmd(self, name: str, cmd: str) -> bool:
        """Commande présente ET du shell valide. Une commande cassée n'est pas proposée au CEO
        (chaque essai planterait) et c'est dit une fois dans le journal, avec l'erreur."""
        if not cmd:
            return False
        if cmd not in self._syntax:
            self._syntax[cmd] = shell_syntax_error(cmd)
            if self._syntax[cmd]:
                self._log(f"agent « {name} » écarté : sa commande n'est pas du shell valide (erreur de "
                          f"syntaxe : {self._syntax[cmd]}). Corrige-la dans les Paramètres.")
        return not self._syntax[cmd]

    def can_escalate(self) -> bool:
        """Claude Code est-il installé pour prendre le relais des tâches trop dures ?"""
        return os.path.isfile(self._bins.claude) and os.access(self._bins.claude, os.X_OK)

    def custom_cmd(self, worker: str) -> str:
        for a in self._cfg.custom_agents():
            if a["name"] == worker:
                return a["cmd"]
        return ""

    def run(self, worker: str, prompt: str, model: str, dry_run: bool,
            cancel: Optional[threading.Event] = None, on_spawn: Optional[OnSpawn] = None,
            effort: Optional[str] = None) -> WorkOutcome:
        spawn = on_spawn or (lambda pid, name: None)
        if dry_run:
            return WorkOutcome(0, f"[dry-run] {worker} : OK")
        if worker == CLAUDE_WORKER:
            return self._claude(prompt, model, effort or "", cancel, spawn)
        spec = spec_for(worker)
        if spec is not None and not spec.is_shell:  # structured (opencode)
            return self._opencode(prompt, model, cancel, spawn)
        if spec is not None:  # shell intégré (autoclaw, cline)
            return self._shell(worker, self._cfg.worker_cmd(spec.name), prompt, cancel, spawn,
                               self._cfg.worker_effort(spec.name))
        custom = self.custom_cmd(worker)  # agent ajouté dans l'interface
        if custom:
            return self._shell(worker, custom, prompt, cancel, spawn, self._cfg.worker_effort(worker))
        return WorkOutcome(2, f"worker inconnu {worker!r} : tâche non exécutée.")

    # ---- opencode --------------------------------------------------------
    def catalog(self, cancel: Optional[threading.Event] = None) -> Set[str]:
        now = self._clock()
        if self._catalog and now - self._catalog[0] < CATALOG_TTL_S:
            return self._catalog[1]
        res = self._run([self._bins.opencode, "models"], timeout=CATALOG_TIMEOUT_S,
                        cwd=str(self._paths.root), cancel=cancel)
        models = {ln.strip() for ln in res.out.splitlines() if "/" in ln} if res.ok else set()
        if not models:
            self._log("catalogue opencode indisponible : aucun filtrage des models.")
        self._catalog = (now, models)
        return models

    def _free_now(self, known: Set[str]) -> List[str]:
        """Gratuits à tenter : ceux du dernier scan, sinon déduits du catalogue lui-même."""
        if self.scanned is not None:
            found = list(self.scanned())
            if found:
                return found
        return free_models("\n".join(sorted(known))) if known else []

    def _candidates(self, model: str, cancel: Optional[threading.Event] = None) -> List[str]:
        known = self.catalog(cancel)
        wanted = list(dict.fromkeys([model, *self._cfg.user_models(), *self._free_now(known)]))
        if known:
            absent = [m for m in wanted if m not in known]
            if absent:
                self._log("model(s) absent(s) du catalogue opencode : " + ", ".join(absent))
            cands = [m for m in wanted if m in known]
            if not cands:  # dernier recours : la graine, filtrée par le catalogue
                cands = [m for m in FREE_MODELS if m in known]
        else:
            cands = list(dict.fromkeys([*wanted, *FREE_MODELS]))
        if not cands:
            self._log("aucun model gratuit connu au catalogue : le model demandé est essayé tel quel.")
            cands = [model]
        dead = self._cfg.dead_models()
        alive = [m for m in cands if m not in dead]
        if not alive:
            self._log("tous les models sont marqués en panne : on les réessaie quand même.")
            return cands
        if len(alive) < len(cands):
            self._log("écartés 6 h (panne récente) : " + ", ".join(m for m in cands if m in dead))
        return alive

    def _opencode_args(self, model: str) -> List[str]:
        """Options type opencode : -m + --agent/--variant si réglés (sans secret)."""
        opts = self._cfg.opencode_opts()
        variant = opts.get("opencode_variant")
        # Le CLI opencode n'a pas d'option --variant : la variante (effort de raisonnement
        # propre au model) se glisse dans le nom, « provider/model#variant ».
        model_arg = f"{model}#{variant}" if variant and "#" not in model else model
        args = [self._bins.opencode, "run", "-m", model_arg, "--format", "json"]
        if opts.get("opencode_agent"):
            args += ["--agent", opts["opencode_agent"]]
        return args

    def _opencode(self, prompt: str, model: str, cancel: Optional[threading.Event],
                  spawn: OnSpawn) -> WorkOutcome:
        start, last = self._clock(), "aucun model disponible"
        last_ev: Optional[_Events] = None
        for m in self._candidates(model, cancel):
            remaining = TASK_BUDGET_S - (self._clock() - start)
            if remaining < 5:
                return WorkOutcome(124, f"budget de {TASK_BUDGET_S} s épuisé pour cette tâche. {last}")
            res = self._run([*self._opencode_args(m), prompt],
                            timeout=min(MODEL_TIMEOUT_S, remaining), cwd=str(self._paths.root),
                            cancel=cancel, on_spawn=lambda pid: spawn(pid, Path(self._bins.opencode).name))
            ev = last_ev = parse_events(res.out, root=str(self._paths.root))
            text = summarize(ev, res)
            seen = {"steps": ev.steps, "steps_omitted": ev.steps_omitted, "model": m}
            if res.cancelled:
                return WorkOutcome(130, text, cancelled=True, **seen)
            if res.ok and not ev.texts and not ev.errors and not ev.used_tools:
                # exit 0 sans texte ni outil (sortie vide ou pas du JSON) : jamais compté comme un succès
                return WorkOutcome(1, "OPENCODE-SORTIE-VIDE : opencode a rendu la main sans rien produire "
                                      f"(aucune sortie exploitable, model {m}). "
                                      + tail(res.err or res.out, 300), **seen)
            if res.ok and (ev.texts or not ev.errors):
                if m != model:  # repli du moment : jamais enregistré, le choix de l'utilisateur reste
                    self._log(f"model {model!r} indisponible → repli sur {m!r} (pour cette tâche seulement).")
                return WorkOutcome(0, text, **seen)
            model_down = (res.timed_out or bool(ev.errors)
                          or any(k in res.err for k in MODEL_ERROR_MARKERS))
            if not model_down:
                return WorkOutcome(res.code or 1, text, **seen)  # échec du travail, pas du model
            self._cfg.remember_dead(m)
            if ev.used_tools:
                return WorkOutcome(res.code or 1, text + f"\nModel {m} en panne après avoir commencé à "
                                   "modifier des fichiers : pas de relance automatique sur un autre model "
                                   "(elle referait le travail en double). Relance la tâche.", **seen)
            self._log(f"model {m} en panne ({'délai dépassé' if res.timed_out else 'erreur'}) : "
                      "essai du suivant.")
            last = text
        return WorkOutcome(1, f"OPENCODE-ERREUR (tous models essayés) : {last}",
                           steps=last_ev.steps if last_ev else (),
                           steps_omitted=last_ev.steps_omitted if last_ev else 0)

    # ---- vérification automatique (commande du projet) -----------------------------
    def verify(self, cmd: str, cancel: Optional[threading.Event] = None,
               on_spawn: Optional[OnSpawn] = None) -> WorkOutcome:
        """Lance la commande de vérification réglée par l'utilisateur dans le dossier de travail."""
        spawn = on_spawn or (lambda pid, name: None)
        res = self._run(["sh", "-c", cmd], timeout=VERIFY_TIMEOUT_S, cwd=str(self._paths.root),
                        cancel=cancel, on_spawn=lambda pid: spawn(pid, "sh"))
        text = tail((res.out + ("\n" + res.err if res.err.strip() else "")).strip(), VERIFY_OUTPUT_CHARS)
        if res.cancelled:
            return WorkOutcome(130, text, cancelled=True)
        if res.timed_out:
            return WorkOutcome(124, f"délai dépassé ({VERIFY_TIMEOUT_S} s). {text}".strip())
        return WorkOutcome(res.code, text or "(aucune sortie)")

    # ---- renfort Claude Code ----------------------------------------------------
    def _claude(self, prompt: str, model: str, effort: str, cancel: Optional[threading.Event],
                spawn: OnSpawn) -> WorkOutcome:
        """`claude -p` sur une tâche escaladée. acceptEdits : il peut écrire dans le dossier de
        travail ; jamais de --dangerously-skip-permissions (le prompt contient des sorties
        d'autres workers : donnée non fiable)."""
        if model not in CLAUDE_MODELS or effort not in CLAUDE_EFFORTS:
            return WorkOutcome(2, f"CLAUDE-REGLAGE-INVALIDE : model {model[:30]!r}, effort {effort[:20]!r}.")
        name = Path(self._bins.claude).name
        res = self._run([self._bins.claude, "--model", model, "--effort", effort,
                         "--permission-mode", "acceptEdits", *WORKER_LEAN_ARGS, *JSON_OUTPUT, "-p", prompt],
                        timeout=CLAUDE_WORKER_TIMEOUT_S, cwd=str(self._paths.root), cancel=cancel,
                        on_spawn=lambda pid: spawn(pid, name))
        got = read_result(res.out)
        body = got.text if got.text is not None else got.error
        text = tail((body + ("\n" + res.err if res.err.strip() else "")).strip(), 4000)
        if res.cancelled:
            return WorkOutcome(130, text, cancelled=True, tokens=got.tokens, model=model)
        if res.ok and got.text is not None:
            return WorkOutcome(0, text or "(aucune sortie)", tokens=got.tokens, model=model)
        why = "délai dépassé" if res.timed_out else f"code={res.code}"
        return WorkOutcome(res.code or 1, f"CLAUDE-ERREUR({why}) : {text}", tokens=got.tokens, model=model)

    # ---- autoclaw / cline / customs ------------------------------------------
    def _shell(self, name: str, cmd: str, prompt: str, cancel: Optional[threading.Event],
               spawn: OnSpawn, effort: str = "") -> WorkOutcome:
        if not cmd:
            hint = self._cline_hub_hint() if name == "cline" else ""
            return WorkOutcome(2, f"{name.upper()}-NON-CONFIGURE : renseigne sa commande dans l'interface "
                                  f"(ou MINI_ORCH_{name.upper()}_CMD).{hint}")
        # Le prompt passe par shlex.quote (argument) ET par $ORCH_PROMPT (env) :
        # les nouvelles commandes doivent référencer "$ORCH_PROMPT" pour qu'aucun
        # caractère du prompt ne puisse interagir avec le shell, même si la commande
        # est mal échappée.
        res = self._run(["sh", "-c", cmd + " " + shlex.quote(prompt)], timeout=MODEL_TIMEOUT_S,
                        cwd=str(self._paths.root), cancel=cancel,
                        env=self._shell_env(prompt, effort),
                        on_spawn=lambda pid: spawn(pid, "sh"))
        text = tail((res.out + ("\n" + res.err if res.err.strip() else "")).strip(), 4000)
        if res.cancelled:
            return WorkOutcome(130, text, cancelled=True)
        if res.ok:
            return WorkOutcome(0, text)
        return WorkOutcome(res.code or 1, f"{name.upper()}-ERREUR(code={res.code}) : {text}")

    @staticmethod
    def _shell_env(prompt: str, effort: str) -> Dict[str, str]:
        """$ORCH_PROMPT toujours ; $ORCH_EFFORT (low|medium|high|xhigh|max) seulement si réglé."""
        env = dict(os.environ, ORCH_PROMPT=prompt)
        level = cli_effort(effort)
        if level:
            env["ORCH_EFFORT"] = level
        else:
            env.pop("ORCH_EFFORT", None)  # jamais hérité d'un shell parent : le réglage fait foi
        return env

    @staticmethod
    def _cline_hub_hint() -> str:
        try:
            with urllib.request.urlopen(urllib.request.Request(CLINE_HUB, method="GET"),
                                        timeout=CLINE_HUB_TIMEOUT_S):
                return " (le hub Cline répond, mais n'a pas d'API de tâche documentée.)"
        except (urllib.error.URLError, OSError):
            return " (hub Cline injoignable.)"
