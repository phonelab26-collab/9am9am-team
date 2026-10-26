"""Dépôts GitHub pour le mode cloud : via `gh` (jamais de token dans le programme).

Pourquoi `gh` et pas l'API Claude : il n'existe pas de commande `claude` qui liste
les dépôts connectés au compte ; `gh` utilise le login déjà présent sur la machine
(keyring). Si `gh` est absent ou déconnecté, on renvoie une erreur explicite, jamais
de secret, jamais d'écriture (lecture seule).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .procs import CmdResult, run_cmd

GH_TIMEOUT_S = 30
GH_CACHE_TTL_S = 300  # la liste ne change pas toutes les minutes : évite de spammer l'API
GH_MAX_REPOS = 100
# owner/repo GitHub : lettres, chiffres, . _ - (1 à 100 chacun).
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


NOT_CONNECTED = ("« gh » n'est pas connecté : lance « gh auth login » dans ton Terminal, "
                 "puis réessaie.")


def clean_repo(value: object) -> str:
    """Un « owner/repo » valide, ou « »."""
    text = value.strip() if isinstance(value, str) else ""
    return text if _REPO_RE.match(text) else ""


@dataclass
class Repo:
    full_name: str  # owner/repo
    private: bool = False
    updated: str = ""


@dataclass
class ReposResult:
    ok: bool
    repos: List[Repo] = field(default_factory=list)
    error: str = ""
    warnings: List[str] = field(default_factory=list)  # comptes en échec quand d'autres ont répondu


class GithubRepos:
    """Liste les dépôts via `gh repo list --json`, avec cache mémoire court."""

    def __init__(self, log: Callable[[str], None],
                 runner: Callable[..., CmdResult] = run_cmd,
                 gh_bin: Optional[str] = None) -> None:
        self._log, self._run = log, runner
        self._gh = gh_bin if gh_bin is not None else (shutil.which("gh") or "")
        self._lock = threading.Lock()
        self._cached: Optional[ReposResult] = None
        self._cached_at = 0.0

    def available(self) -> bool:
        """`gh` est-il présent sur la machine ?"""
        return bool(self._gh)

    def clear_cache(self) -> None:
        with self._lock:
            self._cached, self._cached_at = None, 0.0

    def _accounts(self) -> Tuple[List[Tuple[str, bool]], str]:
        """([(login, actif)], erreur) : comptes github.com connectés. Une panne de `gh` (code,
        délai, JSON illisible) est dite telle quelle ; « pas connecté » = réponse valide sans compte."""
        res = self._run([self._gh, "auth", "status", "--json", "hosts"], timeout=GH_TIMEOUT_S,
                        cwd=str(Path.home()))
        if res.timed_out:
            return [], f"« gh auth status » : délai dépassé ({GH_TIMEOUT_S} s)."
        if res.cancelled:
            return [], "« gh auth status » annulé."
        data = None
        try:
            data = json.loads(res.out) if res.out.strip() else None
        except ValueError:
            pass
        if not res.ok and not isinstance(data, dict):
            lines = (res.err or res.out).strip().splitlines()
            said = lines[-1][:200] if lines else f"code {res.code}"
            if "auth login" in (res.err + res.out):
                return [], NOT_CONNECTED
            return [], f"« gh auth status » a échoué : {said}"
        hosts = data.get("hosts") if isinstance(data, dict) else None
        if not isinstance(hosts, dict):
            return [], "« gh auth status » : réponse illisible (JSON inattendu)."
        logins: List[Tuple[str, bool]] = []
        # github.com uniquement : un compte GHES n'a pas de dépôts listables ici.
        for host, entries in hosts.items():
            if host != "github.com" or not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict) and str(entry.get("state")) == "success":
                    login = str(entry.get("login") or "").strip()
                    if login and all(login != known for known, _ in logins):
                        logins.append((login, bool(entry.get("active"))))
        return (logins, "") if logins else ([], NOT_CONNECTED)

    def _list_for(self, login: str, active: bool = False) -> Tuple[List[Repo], str]:
        """Dépôts d'UN compte. `gh auth token` est le seul mécanisme officiel de `gh`
        pour lister les dépôts d'un compte non actif : le token ne transite QUE par
        l'environnement du sous-process — jamais journalisé, jamais renvoyé au navigateur.
        (On ne matérialise volontairement pas d'alternative : `gh api` ne parle qu'au
        compte actif.)"""
        env = None  # le compte ACTIF n'a besoin d'aucun jeton : `gh` utilise déjà le sien
        if not active:
            token = self._run([self._gh, "auth", "token", "--user", login], timeout=GH_TIMEOUT_S,
                              cwd=str(Path.home()))
            if not token.ok or not token.out.strip():
                return [], f"token de {login} introuvable (« gh auth login » ?)"
            env = dict(os.environ, GH_TOKEN=token.out.strip())
        raw = self._run([self._gh, "repo", "list", "--limit", str(GH_MAX_REPOS), "--json",
                         "nameWithOwner,isPrivate,updatedAt"], timeout=GH_TIMEOUT_S,
                        cwd=str(Path.home()), env=env)
        if not raw.ok:
            lines = (raw.err or raw.out).strip().splitlines()
            return [], f"dépôts de {login} : {lines[-1][:200] if lines else 'code ' + str(raw.code)}"
        try:
            data = json.loads(raw.out or "[]")
        except ValueError:
            return [], f"dépôts de {login} : réponse illisible de « gh »."
        out: List[Repo] = []
        if isinstance(data, list):
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                name = clean_repo(entry.get("nameWithOwner"))
                if not name:
                    continue
                out.append(Repo(name, bool(entry.get("isPrivate")), str(entry.get("updatedAt") or "")[:10]))
                if len(out) >= GH_MAX_REPOS:
                    break
        return out, ""

    def list(self, force: bool = False) -> ReposResult:
        with self._lock:
            if (not force and self._cached is not None
                    and time.monotonic() - self._cached_at < GH_CACHE_TTL_S):
                return self._cached
            # Fetch SOUS le verrou (verrou dédié au cache, pas le verrou d'état) :
            # deux requêtes simultanées font un seul parcours `gh` au lieu de deux
            # — sinon on gaspille le rate-limit GitHub en double.
            res = self._fetch()
            if res.ok:  # une panne n'est jamais gardée en cache : le prochain clic retente aussitôt
                self._cached, self._cached_at = res, time.monotonic()
            return res

    def _fetch(self) -> ReposResult:
        if not self._gh:
            return ReposResult(False, error="« gh » introuvable : installe GitHub CLI "
                                            "(https://cli.github.com) puis « gh auth login ».")
        logins, why = self._accounts()
        if why:
            return ReposResult(False, error=why)
        seen: set = set()
        repos, errors = [], []
        for login, active in logins:
            found, err = self._list_for(login, active)
            if err:
                errors.append(err)
            for r in found:
                if r.full_name in seen:
                    continue
                seen.add(r.full_name)
                repos.append(r)
            if len(repos) >= GH_MAX_REPOS:
                break
        if not repos and errors:
            return ReposResult(False, error=" ; ".join(errors[:3]))
        if errors:
            self._log("dépôts : " + " ; ".join(errors[:3]))
        return ReposResult(True, repos[:GH_MAX_REPOS], warnings=errors[:3])
