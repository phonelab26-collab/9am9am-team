"""Chemins, binaires et réglages persistants (config.json)."""
from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .fileio import MISSING, atomic_write_json, quarantine, read_json
from .lockfile import lock_for

# Graine de DERNIER RECOURS (catalogue illisible, opencode absent) : la liste vivante vient du
# scan des gratuits (free_models.py). Seuls des models encore présents au catalogue y figurent.
FREE_MODELS = [
    "opencode/mimo-v2.6-flash-free",
    "opencode/big-pickle",
    "opencode/nemotron-3-ultra-free",
    "9router/code-free",
]
DEAD_MODEL_TTL_S = 6 * 3600  # un model en échec est mis de côté 6 h
# Les workers intégrés viennent du registry (la SEULE déclaration) : ajouter un
# provider ici ajouterait une liste en dur de plus à synchroniser.
from .detect import definition_sig, detect_verify
from .effort import clean_effort
from .escalation import CLAUDE_WORKER
from .registry import NAMES as BUILTIN_WORKERS
# provider/model, 3 à 200 caractères (jamais d'option shell : pas d'espace ni de « - » initial).
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{1,198}[A-Za-z0-9]$")
# nom d'agent custom : lettres, chiffres, tirets (1 à 40).
_AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
RESERVED_WORKERS = (*BUILTIN_WORKERS, CLAUDE_WORKER)  # noms interdits aux agents custom


def clean_custom_model(value: object) -> str:
    """Un model « provider/model » valide, ou « »."""
    text = value.strip() if isinstance(value, str) else ""
    if len(text) > 200 or "/" not in text:
        return ""
    return text if _MODEL_RE.match(text) else ""


def parse_custom_models(value: object) -> List[str]:
    """Texte multiligne « un provider/model par ligne » → liste dédupliquée valide."""
    raw = value if isinstance(value, str) else ""
    out: List[str] = []
    for line in raw.splitlines():
        m = clean_custom_model(line)
        if m and m not in out:
            out.append(m)
    return out[:50]


CLINE_MODELS_MAX = 10  # Cline n'en propose que 3 ou 4 gratuits à la fois : la liste reste courte


def clean_model_name(value: object) -> str:
    """Un nom de model avec ou sans fournisseur (« stealth/x », « anthropic--claude… »), ou « »."""
    text = value.strip() if isinstance(value, str) else ""
    return text if len(text) <= 200 and _MODEL_RE.match(text) else ""


def parse_model_list(value: object, limit: Optional[int] = CLINE_MODELS_MAX) -> List[str]:
    """Texte multiligne « un model par ligne » → liste dédupliquée valide (bornée à `limit`)."""
    raw = value if isinstance(value, str) else ""
    out: List[str] = []
    for line in raw.splitlines():
        name = clean_model_name(line)
        if name and name not in out:
            out.append(name)
    return out if limit is None else out[:limit]


def clean_agent_name(value: object) -> str:
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if _AGENT_RE.match(text) else ""


@dataclass(frozen=True)
class Paths:
    root: Path   # dossier de travail des workers (racine du projet)
    state: Path
    tasks: Path
    config: Path
    lock: Path
    runs: Path
    journal: Path
    reports: Path
    chat: Path
    config_lock: Path
    trust: Path  # registre de ce qui a été créé/lancé ici : à côté des réglages, hors du projet

    @classmethod
    def for_root(cls, root: Path, settings_dir: Optional[Path] = None) -> "Paths":
        """État du travail (tâches, conversation, journal) dans <root>/.mini-orch ; les
        réglages dans `settings_dir` s'il est donné (globaux, partagés entre projets)."""
        root = Path(root).resolve()
        state = root / ".mini-orch"
        conf = Path(settings_dir).resolve() if settings_dir else state
        return cls(root, state, state / "tasks.json", conf / "config.json",
                   state / ".lock", state / "runs", state / "journal.log", state / "reports.json",
                   state / "chat.json", conf / ".lock", conf / "trust.json")


def _executable(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def _bin(env: str, default: str, name: str, candidates: Tuple[str, ...] = ()) -> str:
    # Portabilité (clé USB / autre Mac) : variable d'env > chemin connu > PATH >
    # emplacements usuels, dont le CLI LIVRÉ DANS UNE APPLICATION (hors PATH).
    value = os.environ.get(env)
    if value:
        return value
    for path in (default, shutil.which(name) or "", *candidates):
        if _executable(path):
            return path
    return default


@dataclass(frozen=True)
class Bins:
    claude: str
    opencode: str

    @classmethod
    def resolve(cls) -> "Bins":
        home = Path.home()
        return cls(
            _bin("MINI_ORCH_CLAUDE", str(home / ".local/bin/claude"), "claude",
                 # installateurs usuels de Claude Code (npm global, Homebrew, ~/.claude/local)
                 (str(home / ".claude/local/claude"),
                  "/opt/homebrew/bin/claude", "/usr/local/bin/claude")),
            _bin("MINI_ORCH_OPENCODE", "/opt/homebrew/bin/opencode", "opencode",
                 # Le CLI est aussi livré DANS l'application OpenCode : lancé par
                 # double-clic, le PATH minimal du Finder ne le voyait pas (worker invisible).
                 (str(home / ".opencode/bin/opencode"),
                  "/Applications/OpenCode.app/Contents/Resources/opencode-cli",
                  str(home / ".bun/bin/opencode"), str(home / ".local/bin/opencode"),
                  "/usr/local/bin/opencode")))


@dataclass(frozen=True)
class VerifyStatus:
    cmd: str                     # commande affichée (tapée ou détectée), « » si aucune
    source: str                  # custom | detected | pending | changed | off | none
    files: Tuple[str, ...] = ()  # fichiers du projet qui définissent la commande détectée

    @property
    def runnable(self) -> str:
        """Commande à lancer : jamais une commande détectée sans ton accord."""
        return self.cmd if self.source in ("custom", "detected") else ""


class ConfigStore:
    """config.json : fusion sous verrou (jamais d'écrasement d'une clé voisine)."""

    def __init__(self, paths: Paths, log: Callable[[str], None]) -> None:
        self._paths, self._log = paths, log
        self._lock = lock_for(paths.config_lock)
        self._reserved_said: set = set()  # noms réservés déjà signalés (une fois, pas à chaque lecture)
        self._notices: List[str] = []     # à dire dans la conversation (pop_notices)
        self._safe_defaults: Dict[str, object] = {}  # si l'écriture de la config de secours a échoué

    def load(self) -> Dict[str, object]:
        with self._lock.held():
            try:
                data = read_json(self._paths.config)
            except ValueError:
                return self._damaged("illisible")
            if data is MISSING:
                return dict(self._safe_defaults)
            if not isinstance(data, dict):
                return self._damaged("n'est pas un objet JSON")
            return data

    def _damaged(self, why: str) -> Dict[str, object]:
        """config.json abîmé : mis de côté, réglages par défaut SAUF la reprise payante par
        Claude Code, coupée par prudence (« on » par défaut la réactiverait sans ton accord)."""
        kept = quarantine(self._paths.config)
        safe: Dict[str, object] = {"escalation": "off"}
        try:
            atomic_write_json(self._paths.config, safe)
        except OSError as e:
            self._safe_defaults = safe
            self._log(f"config.json de secours non enregistré ({e.__class__.__name__}).")
        text = (f"Ton fichier de réglages (config.json) {why} : il est conservé en {kept.name} et les "
                "réglages par défaut s'appliquent. Par prudence, la reprise payante par Claude Code est "
                "désactivée (réactive-la dans Paramètres) ; ressaisis aussi tes autres réglages "
                "(model du CEO, commandes…).")
        self._notices.append(text)
        self._log(text)
        return dict(safe)

    def pop_notices(self) -> List[str]:
        """Avis à montrer dans la conversation (une seule fois chacun)."""
        with self._lock.held():
            out, self._notices = self._notices, []
            return out

    def setting_problems(self) -> List[str]:
        """Réglages présents mais invalides (donc ignorés) : à dire, jamais à neutraliser en douce."""
        data = self.load()
        out: List[str] = []
        effort = data.get("ceo_effort")
        if effort not in (None, "") and clean_effort(effort) is None:
            out.append(f"Réglage « effort du CEO » invalide ({str(effort)[:20]!r}) : ignoré, effort par "
                       "défaut de Claude Code. Choisis un niveau dans Paramètres.")
        mode = data.get("ceo_mode")
        if mode not in (None, "") and not (isinstance(mode, str) and mode.strip().lower() in ("local", "cloud")):
            out.append(f"Réglage « mode du CEO » invalide ({str(mode)[:20]!r}) : le CEO tourne en local.")
        return out

    def update(self, **values: object) -> None:
        with self._lock.held():
            atomic_write_json(self._paths.config, {**self.load(), **values})

    def model(self, cli_model: Optional[str], default: str) -> str:
        return cli_model or str(self.load().get("opencode_model") or "") or default

    def agent_model(self, agent: str, cli_model: Optional[str], default: str) -> str:
        """Model d'un agent : --model (opencode) > réglage <agent>_model > opencode_model > défaut."""
        data = self.load()
        if agent == "opencode" and cli_model:
            return cli_model
        specific = str(data.get(f"{agent}_model") or "").strip()
        if specific:
            return specific
        if agent == "opencode":
            return str(data.get("opencode_model") or "") or default
        return default

    def user_models(self) -> List[str]:
        """Models ajoutés à la main dans l'interface (valides, dédupliqués)."""
        return parse_custom_models(self.load().get("custom_models"))

    def all_models(self) -> List[str]:
        """FREE_MODELS + models ajoutés dans l'interface (dédupliqués, valides)."""
        customs = parse_custom_models(self.load().get("custom_models"))
        return FREE_MODELS + [m for m in customs if m not in FREE_MODELS]

    def cline_models(self) -> List[str]:
        """Models de Cline tenus à la main dans l'interface (Cline ne publie aucun catalogue)."""
        return parse_model_list(self.load().get("cline_models"))

    def custom_agents(self) -> List[Dict[str, str]]:
        """Agents ajoutés dans l'interface : [{"name","cmd","model"}]. Jamais de secret ici."""
        raw = self.load().get("custom_agents")
        if not isinstance(raw, list):
            return []
        out = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name = clean_agent_name(entry.get("name"))
            cmd = str(entry.get("cmd") or "").strip()[:2000]
            model = clean_custom_model(entry.get("model") or "")
            if name in RESERVED_WORKERS:
                self._say_reserved(name)
            elif name and cmd:
                out.append({"name": name, "cmd": cmd, "model": model,
                            "effort": clean_effort(entry.get("effort")) or ""})
        return out

    def _say_reserved(self, name: str) -> None:
        if name not in self._reserved_said:
            self._reserved_said.add(name)
            self._log(f"agent custom « {name} » ignoré : ce nom est réservé à un agent intégré "
                      "(renomme-le dans l'interface).")

    def set_custom_agents(self, agents: List[Dict[str, str]]) -> None:
        cleaned = []
        seen = set()
        for entry in agents:
            if not isinstance(entry, dict):
                continue
            name = clean_agent_name(entry.get("name"))
            cmd = str(entry.get("cmd") or "").strip()[:2000]
            model = clean_custom_model(entry.get("model") or "")
            if name and cmd and name not in RESERVED_WORKERS and name not in seen:
                seen.add(name)
                cleaned.append({"name": name, "cmd": cmd, "model": model,
                                "effort": clean_effort(entry.get("effort")) or ""})
        self.update(custom_agents=cleaned[:20])

    def opencode_opts(self) -> Dict[str, str]:
        """Options type opencode : agent + variant (chaînes courtes, sans espace)."""
        data = self.load()
        out: Dict[str, str] = {}
        for key in ("opencode_agent", "opencode_variant"):
            val = str(data.get(key) or "").strip()[:80]
            if val and re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$", val):
                out[key] = val
        return out

    def worker_cmd(self, name: str) -> str:
        # Réglage de l'interface > variable d'environnement.
        return (str(self.load().get(f"{name}_cmd") or "")
                or os.environ.get(f"MINI_ORCH_{name.upper()}_CMD", ""))

    def ceo_model(self) -> str:
        """Model demandé pour Claude Code (le CEO) ; « » = celui par défaut de Claude Code."""
        value = self.load().get("ceo_model")
        return value.strip() if isinstance(value, str) else ""

    def verify_cmd(self, root: Path) -> str:
        """Commande de vérification du dossier de travail `root` (« » = aucune)."""
        table = self.load().get("verify_cmds")
        value = table.get(str(Path(root).resolve())) if isinstance(table, dict) else ""
        return value.strip() if isinstance(value, str) else ""

    def verify_status(self, root: Path) -> VerifyStatus:
        """Commande de ce dossier et son état : « custom » (tapée par toi), « detected » (trouvée
        dans le projet ET autorisée), « pending » (trouvée, en attente de ton accord), « changed »
        (autorisée, mais le fichier qui la définit a changé depuis), « off » ou « none »."""
        stored = self.verify_cmd(root)
        if stored == "off":
            return VerifyStatus("", "off")
        if stored:
            return VerifyStatus(stored, "custom")
        cmd, files = detect_verify(Path(root))
        if not cmd:
            return VerifyStatus("", "none")
        table = self.load().get("verify_approvals")
        ok = table.get(str(Path(root).resolve())) if isinstance(table, dict) else None
        if not isinstance(ok, dict) or ok.get("cmd") != cmd:
            return VerifyStatus(cmd, "pending", files)
        same = ok.get("sig") == definition_sig(Path(root), cmd)
        return VerifyStatus(cmd, "detected" if same else "changed", files)

    def effective_verify(self, root: Path) -> Tuple[str, str]:
        """(commande à LANCER, origine) : « » tant qu'une commande détectée n'est pas autorisée."""
        status = self.verify_status(root)
        return status.runnable, status.source

    def approve_verify(self, root: Path, cmd: str) -> Tuple[bool, str]:
        """Accord pour la commande détectée de ce dossier, lié à l'empreinte de ses fichiers.
        Refusé si ce n'est plus la commande affichée (le projet a changé entre-temps)."""
        with self._lock.held():
            found, _ = detect_verify(Path(root))
            if not found or found != cmd:
                now = f"« {found} »" if found else "aucune"
                return False, f"la commande détectée a changé (maintenant : {now}) : relis-la avant d'autoriser."
            data = self.load()
            table = dict(data.get("verify_approvals") or {}) if isinstance(data.get("verify_approvals"), dict) else {}
            table[str(Path(root).resolve())] = {"cmd": cmd, "sig": definition_sig(Path(root), cmd),
                                                "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            atomic_write_json(self._paths.config, {**data, "verify_approvals": table})
        return True, ""

    def set_verify_cmd(self, root: Path, cmd: str) -> None:
        """Une commande PAR dossier de travail : `swift test` pour l'un, `npm test` pour l'autre."""
        with self._lock.held():
            data = self.load()
            table = dict(data.get("verify_cmds") or {}) if isinstance(data.get("verify_cmds"), dict) else {}
            key = str(Path(root).resolve())
            if cmd:
                table[key] = cmd
            else:
                table.pop(key, None)
            atomic_write_json(self._paths.config, {**data, "verify_cmds": table})

    def escalation_enabled(self) -> bool:
        """Reprise par Claude Code sur refus du CEO : « on » par défaut, « off » la coupe."""
        return str(self.load().get("escalation") or "on").strip().lower() != "off"

    def workspace(self) -> str:
        """Dernier dossier de travail choisi (« » = le dossier du logiciel)."""
        value = self.load().get("workspace")
        return value.strip() if isinstance(value, str) else ""

    def ceo_effort(self) -> str:
        """Effort demandé au CEO (faible…ultracode), « » = défaut de Claude Code."""
        return clean_effort(self.load().get("ceo_effort")) or ""

    def ceo_effort_raw(self) -> str:
        """Valeur enregistrée telle quelle : le CEO signale lui-même une valeur invalide."""
        value = self.load().get("ceo_effort")
        return value.strip() if isinstance(value, str) else ""

    def worker_effort(self, name: str) -> str:
        """Effort réglé pour un agent (intégré shell ou custom), « » = défaut de l'outil."""
        for a in self.custom_agents():
            if a["name"] == name:
                return a.get("effort", "")
        return clean_effort(self.load().get(f"{name}_effort")) or ""

    def ceo_mode(self) -> str:
        """Où le CEO travaille : « local » (ton Mac, défaut) ou « cloud » (session distante)."""
        value = self.load().get("ceo_mode")
        text = value.strip().lower() if isinstance(value, str) else ""
        return text if text in ("local", "cloud") else "local"

    def ceo_cloud_session(self) -> str:
        """Id (session_.../cse_...) ou URL (claude.ai/code/...) d'une session cloud ; « » = aucune."""
        value = self.load().get("ceo_cloud_session")
        return value.strip() if isinstance(value, str) else ""

    def ceo_cloud_repo(self) -> str:
        """Dépôt GitHub visé en mode cloud (« owner/repo ») ; « » = aucun (le CEO le demandera)."""
        value = self.load().get("ceo_cloud_repo")
        return value.strip() if isinstance(value, str) else ""

    def dead_models(self, now: Optional[float] = None) -> Set[str]:
        now = time.time() if now is None else now
        raw = self.load().get("dead_models") or {}
        if not isinstance(raw, dict):
            self._log("config.json : dead_models invalide, ignoré.")
            return set()
        out = set()
        for model, ts in raw.items():
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                if now - ts < DEAD_MODEL_TTL_S:
                    out.add(model)
            else:
                self._log(f"config.json : date invalide pour le model {model!r}, ignorée.")
        return out

    def remember_dead(self, model: str, now: Optional[float] = None) -> None:
        with self._lock.held():
            raw = self.load().get("dead_models")
            dead = dict(raw) if isinstance(raw, dict) else {}
            dead[model] = time.time() if now is None else now
            self.update(dead_models=dead)
