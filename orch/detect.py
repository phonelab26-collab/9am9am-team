"""Détection de la commande de vérification d'un projet (affichée en clair, remplaçable ou
désactivable dans les Paramètres).

Prudente : on ne propose qu'une commande dont l'existence se lit dans un fichier du projet
(cible `make` réelle, script `npm test` réel, Package.swift…). Un projet Xcode seul n'est pas
deviné : son schéma est inconnu. Ordre : Makefile > Swift > npm > Python > Rust > Go.

Une commande détectée exécute le code DU PROJET (Makefile, conftest.py, build.rs…) : elle ne
tourne qu'avec ton accord, lié à l'empreinte des fichiers qui la définissent (`definition_sig`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Optional, Tuple

MAKE_TARGETS = ("check-fast", "check", "test")  # du plus rapide au plus complet
NPM_PLACEHOLDER = "no test specified"            # le script « test » par défaut de `npm init`
READ_LIMIT = 200_000                             # octets lus au plus dans un fichier de config
SIG_LIMIT = 5_000_000                            # au-delà, l'empreinte dit « trop gros » (jamais lu en entier)
PYTEST_FILES = ("pytest.ini", "conftest.py", "pyproject.toml")
MAKE_FILES = ("GNUmakefile", "makefile", "Makefile")  # ordre de lecture de GNU make
# Fichiers du projet que la commande lit AVANT de lancer les tests (config, scripts, hooks) :
# l'accord est lié à leur empreinte, absents compris (un fichier ajouté après l'accord compte).
SIG_FILES = {
    "make": MAKE_FILES,
    "swift": ("Package.swift",),
    "npm": ("package.json", ".npmrc"),
    "python3 -m pytest": PYTEST_FILES + ("tox.ini", "setup.cfg", "tests/conftest.py"),
    "python3 -m unittest": (),
    "cargo": ("Cargo.toml", "build.rs", ".cargo/config.toml", ".cargo/config",
              "rust-toolchain", "rust-toolchain.toml"),
    "go": ("go.mod",),
}

Detected = Tuple[str, Tuple[str, ...]]  # (commande, fichiers du projet qui la définissent)


def _open_regular(path: Path) -> Optional[int]:
    """Descripteur d'un fichier NORMAL, ou None : un FIFO nommé « Makefile » ne gèle jamais la
    détection (O_NONBLOCK), un dossier ou un périphérique est ignoré."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None
    os.set_blocking(fd, True)
    return fd


def _read(path: Path) -> str:
    fd = _open_regular(path)
    if fd is None:
        return ""
    with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as f:
        return f.read(READ_LIMIT)


def _names(root: Path) -> set:
    """Noms exacts du dossier (APFS ignore la casse : `exists("makefile")` serait vrai pour Makefile)."""
    try:
        return set(os.listdir(str(root)))
    except OSError:
        return set()


def _make(root: Path) -> str:
    # GNU make lit GNUmakefile, puis makefile, puis Makefile : on lit celui qu'il lira.
    used = next((n for n in MAKE_FILES if n in _names(root)), "")
    text = _read(root / used) if used else ""
    for target in MAKE_TARGETS:  # une cible = une ligne « nom: » en début de ligne (pas une recette)
        if re.search(rf"^{re.escape(target)}\s*:(?!=)", text, re.MULTILINE):
            return f"make {target}"
    return ""


def _npm(root: Path) -> str:
    try:
        scripts = json.loads(_read(root / "package.json")).get("scripts")
    except (ValueError, AttributeError):
        return ""
    test = scripts.get("test") if isinstance(scripts, dict) else None
    return "npm test" if isinstance(test, str) and test.strip() and NPM_PLACEHOLDER not in test else ""


def _pytest_files(root: Path) -> Tuple[str, ...]:
    found = [n for n in PYTEST_FILES[:2] if (root / n).exists()]
    if "[tool.pytest" in _read(root / "pyproject.toml"):
        found.append("pyproject.toml")
    return tuple(found)


def _unittest(root: Path) -> bool:
    tests = root / "tests"
    return tests.is_dir() and any("unittest" in _read(f) for f in sorted(tests.glob("test_*.py"))[:20])


def _command(root: Path) -> str:
    make = _make(root)
    if make:
        return make
    if (root / "Package.swift").exists():
        return "swift test"
    if _npm(root):
        return "npm test"
    if _pytest_files(root):
        return "python3 -m pytest -q"
    if _unittest(root):  # le code lancé = tests/ lui-même, que les agents modifient : accord sur la commande
        return "python3 -m unittest discover -s tests"
    if (root / "Cargo.toml").exists():
        return "cargo test"
    if (root / "go.mod").exists():
        return "go test ./..."
    return ""


def sig_files(cmd: str) -> Tuple[str, ...]:
    """Fichiers couverts par l'accord pour cette commande."""
    return next((files for prefix, files in SIG_FILES.items() if cmd.startswith(prefix)), ())


def detect_verify(root: Path) -> Detected:
    """(commande de tests du projet, fichiers présents qui la définissent), ou (« », ())."""
    root = Path(root)
    cmd = _command(root)
    if not cmd:
        return "", ()
    names = _names(root)
    present = tuple(n for n in sig_files(cmd) if (n in names if "/" not in n else (root / n).exists()))
    return cmd, present


def detect_verify_cmd(root: Path) -> str:
    """Commande de tests du projet, ou « » si rien de sûr n'est détecté."""
    return detect_verify(root)[0]


def _file_sig(path: Path) -> str:
    """Empreinte d'un fichier ; un FIFO, /dev/zero ou un fichier géant n'est jamais lu."""
    if not os.path.lexists(str(path)):
        return "absent"
    fd = _open_regular(path)
    if fd is None:
        return "pas-un-fichier-normal"
    with os.fdopen(fd, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        if size > SIG_LIMIT:
            return f"trop-gros:{size}:{os.fstat(f.fileno()).st_mtime_ns}"
        return hashlib.sha256(f.read(SIG_LIMIT + 1)).hexdigest()


def definition_sig(root: Path, cmd: str) -> str:
    """Empreinte de la commande ET de tous les fichiers qu'elle lit (absents compris)."""
    h = hashlib.sha256(cmd.encode("utf-8"))
    for name in sig_files(cmd):
        h.update(b"\0" + name.encode("utf-8") + b"\0" + _file_sig(Path(root) / name).encode("ascii"))
    return h.hexdigest()
