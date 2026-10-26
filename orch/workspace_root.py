"""Choix du dossier de travail (là où travaillent le CEO et les agents).

Le sélecteur est celui du Mac (`osascript … choose folder`) : il marche dans le logiciel
comme dans le navigateur, car le serveur tourne sur ce Mac. Le dossier choisi est validé
strictement — jamais « / », le dossier personnel entier ni un dossier système, où un agent
qui écrit des fichiers ferait des dégâts.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable, Optional, Tuple

from .fileio import STATE_DIR
from .procs import CmdResult, run_cmd

PICK_TIMEOUT_S = 900  # l'utilisateur prend le temps de choisir ; au-delà, on abandonne
PICK_PROMPT = "Choisis le dossier de travail de 9AM9AM-TEAM"
SYSTEM_DIRS = ("/System", "/Library", "/Applications", "/usr", "/bin", "/sbin", "/etc", "/dev",
               "/cores", "/opt", "/private/etc", "/private/var/db")
CANCEL_MARK = "-128"  # code AppleScript « User canceled »
STATE_SCAN_MAX = 2000  # entrées de .mini-orch examinées au plus (runs/ reste petit : baux purgés)


def _key(path: object) -> str:
    """Comparaison insensible à la casse : APFS l'est par défaut (« /USERS » = « /Users »)."""
    return str(path).casefold()


def _too_broad(path: Path) -> bool:
    return _key(path) in {_key(p) for p in ("/", "/Users", Path.home().resolve(), "/Volumes")}


def _system(path: Path) -> bool:
    text = _key(path)
    return any(text == _key(d) or text.startswith(_key(d) + "/") for d in SYSTEM_DIRS)


def validate_workspace(raw: object) -> Tuple[Optional[Path], str]:
    """(dossier résolu, "") ou (None, raison lisible)."""
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        return None, "chemin vide."
    if not text.startswith("/"):
        return None, f"chemin non absolu : {text[:200]}"
    path = Path(text).resolve()
    if not path.exists():
        return None, f"dossier introuvable : {path}"
    if not path.is_dir():
        return None, f"ce n'est pas un dossier : {path}"
    if _too_broad(path):
        return None, f"dossier trop large ({path}) : choisis le dossier d'un projet."
    if _system(path):
        return None, f"dossier système ({path}) : les agents ne doivent pas y écrire."
    if not os.access(str(path), os.W_OK | os.X_OK):
        return None, f"pas de droit d'écriture sur {path}."
    planted = _planted_link(path / STATE_DIR)
    if planted:
        # Ce programme ne crée jamais de lien dans `.mini-orch` : un lien y ferait écrire tâches
        # et journal ailleurs (ex. dans ton shell). Le dossier est refusé, rien n'y est touché.
        return None, (f"« {planted} » de ce dossier est un lien symbolique, un lien physique ou un "
                      "fichier spécial : refusé. Supprime-le (ou renomme-le) si tu ne l'as pas créé toi-même.")
    return path, ""


def _odd(entry: Path) -> bool:
    """Ce que ce programme ne crée jamais dans `.mini-orch` : lien symbolique, lien physique
    (plusieurs noms), FIFO ou autre fichier spécial (un FIFO gèlerait l'ouverture)."""
    st = entry.lstat()
    if stat.S_ISDIR(st.st_mode):
        return False
    return not stat.S_ISREG(st.st_mode) or st.st_nlink != 1


def _planted_link(state: Path) -> str:
    """Première entrée piégée de `.mini-orch` (chemin relatif), « » si aucune."""
    if not os.path.lexists(str(state)):
        return ""
    if state.is_symlink() or not state.is_dir():
        return STATE_DIR
    for entry in sorted(state.iterdir())[:STATE_SCAN_MAX]:
        rel = f"{STATE_DIR}/{entry.name}"
        if _odd(entry):
            return rel
        if entry.is_dir():
            inner = next((e for e in sorted(entry.iterdir())[:STATE_SCAN_MAX] if _odd(e)), None)
            if inner is not None:
                return f"{rel}/{inner.name}"
    return ""


def pick_folder(runner: Callable[..., CmdResult] = run_cmd) -> Tuple[Optional[str], str]:
    """Sélecteur natif : (chemin, "") ; (None, "") si annulé ; (None, erreur) sinon."""
    res = runner(["osascript", "-e", "tell me to activate",
                  "-e", f'POSIX path of (choose folder with prompt "{PICK_PROMPT}")'],
                 timeout=PICK_TIMEOUT_S)
    if not res.ok:
        if CANCEL_MARK in res.err or res.cancelled:
            return None, ""
        why = "délai dépassé" if res.timed_out else (res.err.strip() or f"code {res.code}")
        return None, f"sélecteur de dossier indisponible : {why[:300]}"
    chosen = res.out.strip()
    if not chosen:
        return None, "le sélecteur n'a renvoyé aucun dossier."
    return (chosen.rstrip("/") or "/"), ""
