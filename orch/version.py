"""Version du logiciel : le commit git du dossier du logiciel, ou « inconnue » — jamais un
numéro inventé (le projet n'en tient aucun)."""
from __future__ import annotations

import platform
import subprocess
from pathlib import Path

GIT_TIMEOUT_S = 5  # `git describe` local : instantané ; au-delà, la machine est en souffrance


def software_version(root: Path) -> str:
    """Commit court du dépôt git qui EST le dossier du logiciel (« -dirty » si modifié), « » sinon.

    Un dossier simplement posé dans un autre dépôt n'a pas la version de celui-ci : on exige
    un `.git` à la racine du logiciel."""
    root = Path(root)
    if not (root / ".git").exists():
        return ""
    try:
        res = subprocess.run(["git", "-C", str(root), "describe", "--always", "--dirty", "--tags"],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL,
                             timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def version_line(root: Path) -> str:
    version = software_version(root)
    shown = f"version {version}" if version else "version inconnue (pas un dépôt git)"
    return f"9AM9AM-TEAM — {shown} · Python {platform.python_version()}"
