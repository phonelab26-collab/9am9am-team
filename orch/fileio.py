"""Lecture/écriture JSON sûres : atomiques, durables, jamais d'écrasement silencieux.

Le dossier d'état `.mini-orch/` vit dans un projet qui peut venir d'ailleurs : tout fichier
qu'on y ouvre l'est SANS suivre de lien symbolique (ni le fichier, ni `.mini-orch/` et ses
sous-dossiers). Sinon `.mini-orch/journal.log` → `~/.zshrc` ferait écrire dans ton shell.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import secrets
import stat
import time
from pathlib import Path

MISSING = object()
STATE_DIR = ".mini-orch"
_LINK_ERRNOS = (errno.ELOOP, errno.ENOTDIR, errno.EMLINK)  # O_NOFOLLOW sur un lien (selon le système)


class UnsafePath(OSError):
    """Un composant du dossier d'état est un lien symbolique (ou pas un dossier) : refusé."""


def _shown(parts: tuple) -> str:
    """Chemin montré à l'utilisateur : à partir de `.mini-orch`, jamais absolu."""
    return "/".join(parts) or "?"


def _refuse(parts: tuple) -> UnsafePath:
    return UnsafePath(errno.ELOOP, f"« {_shown(parts)} » est un lien symbolique, un lien physique ou un "
                                   "fichier spécial : refusé, dossier d'état .mini-orch non fiable")


def _cut(parts: tuple) -> int:
    """Index du dernier `.mini-orch` : ce qui est avant (le projet, choisi par toi) est fiable."""
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == STATE_DIR:
            return i
    return len(parts)


def _dir_fd(directory: Path, create: bool) -> int:
    """Descripteur du dossier, en refusant tout lien à partir de `.mini-orch`."""
    parts = Path(directory).parts
    cut = _cut(parts)
    trusted = Path(*parts[:cut])
    if create:
        trusted.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(trusted), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for i, name in enumerate(parts[cut:], cut):
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            try:
                nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as e:
                if e.errno in _LINK_ERRNOS:
                    raise _refuse(parts[cut:i + 1]) from None
                raise
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_in(dfd: int, name: str, flags: int, mode: int, shown: tuple) -> int:
    """O_NONBLOCK : un FIFO planté ne gèle pas l'ouverture ; puis on n'accepte qu'un fichier
    normal à UN seul nom (un lien physique vers `~/.zshrc` passerait O_NOFOLLOW)."""
    try:
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode, dir_fd=dfd)
    except OSError as e:
        if e.errno in _LINK_ERRNOS + (errno.ENXIO,):  # ENXIO : FIFO sans lecteur en écriture
            raise _refuse(shown) from None
        raise
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        os.close(fd)
        raise _refuse(shown)
    os.set_blocking(fd, True)
    return fd


def secure_open(path: Path, flags: int, mode: int = 0o600, create_dirs: bool = True) -> int:
    """`os.open` sans jamais suivre de lien (fichier, `.mini-orch/` et dessous). Lève UnsafePath."""
    path = Path(path)
    dfd = _dir_fd(path.parent, create_dirs)
    try:
        return _open_in(dfd, path.name, flags, mode, path.parts[_cut(path.parts):])
    finally:
        os.close(dfd)


def _full_fsync(fd: int) -> None:
    # Sur macOS, fsync ne vide pas le cache du disque : F_FULLFSYNC le fait
    # (une coupure machine en pleine session a déjà eu lieu).
    try:
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    except (AttributeError, OSError):
        os.fsync(fd)


def atomic_write_json(path: Path, data: object, mode: int = 0o600) -> None:
    path = Path(path)
    dfd = _dir_fd(path.parent, create=True)  # dossier vérifié : chmod et écriture n'en sortent pas
    try:
        try:
            os.fchmod(dfd, 0o700)  # l'umask peut laisser un dossier lisible par tous
        except OSError:
            pass  # dossier d'un autre propriétaire : on écrit quand même, le fichier reste en 0600
        tmp = f".{path.name}.{secrets.token_hex(6)}.tmp"
        fd = _open_in(dfd, tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode,
                      path.parts[_cut(path.parts):])
        try:
            os.fchmod(fd, mode)  # mode exact, quel que soit l'umask
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                _full_fsync(f.fileno())
            os.rename(tmp, path.name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except FileNotFoundError:
                pass
            raise
        os.fsync(dfd)
    finally:
        os.close(dfd)


def read_json(path: Path) -> object:
    """Contenu JSON, ou MISSING si le fichier n'existe pas. Lève ValueError si illisible,
    UnsafePath si c'est un lien symbolique."""
    try:
        fd = secure_open(path, os.O_RDONLY, create_dirs=False)
    except FileNotFoundError:
        return MISSING
    with os.fdopen(fd, "r", encoding="utf-8") as f:
        raw = f.read()
    return json.loads(raw)  # json.JSONDecodeError est une ValueError


def quarantine(path: Path) -> Path:
    """Met un fichier illisible de côté, sans jamais écraser une quarantaine précédente."""
    stamp = time.strftime("%Y%m%dT%H%M%S")
    dest = path.with_name(f"{path.name}.corrupt-{stamp}")
    n = 1
    while dest.exists():
        dest = path.with_name(f"{path.name}.corrupt-{stamp}-{n}")
        n += 1
    path.replace(dest)
    return dest


def secure_listdir(directory: Path) -> list:
    """Noms des entrées d'un dossier d'état, sans suivre de lien (FileNotFoundError s'il manque)."""
    dfd = _dir_fd(Path(directory), create=False)
    try:
        return sorted(os.listdir(dfd))
    finally:
        os.close(dfd)


def secure_unlink(path: Path) -> None:
    """Supprime l'entrée elle-même (jamais à travers un `.mini-orch` ou `runs/` devenu lien)."""
    path = Path(path)
    try:
        dfd = _dir_fd(path.parent, create=False)
    except FileNotFoundError:
        return
    try:
        os.unlink(path.name, dir_fd=dfd)
    except FileNotFoundError:
        pass
    finally:
        os.close(dfd)


def cleanup_tmp(directory: Path) -> None:
    """Fichiers temporaires laissés par un crash (appelé sous le verrou d'état)."""
    dfd = _dir_fd(Path(directory), create=False)
    try:
        for name in os.listdir(dfd):
            if name.startswith(".") and name.endswith(".tmp"):
                try:
                    os.unlink(name, dir_fd=dfd)
                except FileNotFoundError:
                    pass
    finally:
        os.close(dfd)
