"""Une commande d'agent réglée dans l'interface doit au moins être du shell valide.

Trouvé à l'audit : une ligne de tâche collée (« [5] done (opencode) … ») enregistrée comme
commande de cline. Elle était proposée au CEO et chaque essai plantait sur la parenthèse.
`sh -n` ANALYSE la commande sans jamais l'exécuter : c'est le même `sh` que celui qui la lancera.
"""
from __future__ import annotations

from .procs import run_cmd

SYNTAX_TIMEOUT_S = 5
ERROR_MAX = 300


def shell_syntax_error(cmd: str) -> str:
    """« » si `sh -n` accepte la commande, sinon l'erreur de syntaxe (ou pourquoi on ne sait pas)."""
    if "\x00" in cmd:  # refusé par le système avant même sh : ce serait une exception, pas un message
        return "caractère nul interdit dans une commande"
    res = run_cmd(["sh", "-n", "-c", cmd], timeout=SYNTAX_TIMEOUT_S)
    if res.ok:
        return ""
    if res.timed_out:
        return f"vérification de syntaxe trop longue (> {SYNTAX_TIMEOUT_S} s)"
    return (res.err.strip() or res.out.strip() or f"sh -n : code {res.code}")[:ERROR_MAX]
