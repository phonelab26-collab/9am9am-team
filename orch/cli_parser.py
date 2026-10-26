"""Ligne de commande : aide, exemples et erreurs en français, codes de sortie documentés.

argparse parle anglais (titres, « usage: », erreurs) : ce module le remet en français sans
dépendre de la langue du système, et accepte les options globales avant OU après la commande.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .registry import NAMES
from .version import version_line

PROG = "mini_orchestrateur.py"
EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 130
EXIT_CODES = "codes de sortie : 0 réussi · 1 échec ou erreur · 2 usage incorrect · 130 interrompu (Ctrl-C)"

_ERRORS: List[Tuple["re.Pattern[str]", Callable[["re.Match[str]"], str]]] = [
    (re.compile(r"the following arguments are required: (.+)"),
     lambda m: f"argument manquant : {m.group(1)}"),
    (re.compile(r"argument (\S+): invalid choice: '?(.+?)'? \(choose from (.+)\)"),
     lambda m: f"« {m.group(2)} » n'est pas une commande valide (commandes : {m.group(3).replace(chr(39), '')})"),
    (re.compile(r"unrecognized arguments: (.+)"), lambda m: f"argument inconnu : {m.group(1)}"),
    (re.compile(r"argument (\S+): expected one argument"), lambda m: f"{m.group(1)} attend une valeur"),
    (re.compile(r"argument (\S+): invalid int value: '?([^']*)'?"),
     lambda m: f"{m.group(1)} attend un nombre entier (reçu : {m.group(2)})"),
    (re.compile(r"ambiguous option: (.+)"), lambda m: f"option ambiguë : {m.group(1)}"),
]


def french_error(message: str) -> str:
    """Message d'erreur argparse traduit ; un message inconnu est rendu tel quel, jamais caché."""
    for pattern, render in _ERRORS:
        found = pattern.search(message)
        if found:
            return render(found)
    return message


class _Formatter(argparse.RawDescriptionHelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):  # type: ignore[no-untyped-def]
        return super().add_usage(usage, actions, groups, "utilisation : " if prefix is None else prefix)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog} : erreur : {french_error(message)}\n")


class _Version(argparse.Action):
    """--version calculé À L'APPEL (lance git) : construire le parseur ne coûte rien."""

    def __init__(self, option_strings, dest, version_text, help=None):  # type: ignore[no-untyped-def]
        super().__init__(option_strings, dest=dest, default=argparse.SUPPRESS, nargs=0, help=help)
        self._text = version_text

    def __call__(self, parser, namespace, values, option_string=None):  # type: ignore[no-untyped-def]
        print(self._text())
        parser.exit(EXIT_OK)


def _example(*lines: str) -> str:
    return "exemples :\n" + "\n".join(f"  {PROG} {line}" for line in lines)


def _common(suppress: bool) -> argparse.ArgumentParser:
    """Options valables partout. Sur une sous-commande, SUPPRESS évite d'écraser la valeur
    donnée avant la commande (`--dir X status` comme `status --dir X`)."""
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("options communes")
    g.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS if suppress else False,
                   help="boucle de démonstration : aucun CLI réel n'est appelé")
    g.add_argument("--dir", "--workspace", dest="workdir", metavar="DOSSIER",
                   default=argparse.SUPPRESS if suppress else None,
                   help="dossier de travail du CEO et des agents (tâches, conversation, journal y "
                        "sont rangés) ; défaut : le dossier du logiciel. Réglages toujours globaux.")
    return p


_COMMANDS = (  # (nom, résumé, exemples)
    ("plan", "le CEO découpe un objectif en tâches",
     ('plan "créer la page d\'accueil"', '--dir ~/projets/site plan "ajouter un formulaire"')),
    ("run", "exécute UNE tâche prête (par un agent)", ("run", "run --worker opencode")),
    ("tick", "reprise + plan si l'objectif est nouveau + 1 tâche + supervision du CEO",
     ('tick "créer la page d\'accueil"', '--dry-run tick "essai sans appeler les CLI"')),
    ("status", "état des tâches (lecture seule)",
     ("status", "status --json | jq '.tasks[] | select(.status == \"blocked\")'")),
    ("chat", "écris au CEO : il répond, délègue si besoin, puis fait son rapport",
     ('chat "où en est-on ?"',)),
    ("retry", "remet en file les tâches bloquées ou échouées (tentatives remises à 0)", ("retry",)),
    ("report", "le CEO rédige la réponse finale à un objectif", ("report", 'report "créer la page d\'accueil"')),
    ("menu", "menu interactif (c'est aussi ce qui s'ouvre sans commande)", ("menu",)),
    ("gui", "interface dans le navigateur (ou le logiciel 9AM9AM-TEAM.app)", ("gui", "gui --no-browser")),
    ("selftest", "suite de tests (aucun CLI réel appelé)", ("selftest",)),
)


def _sub(subs, name: str, summary: str, examples: Tuple[str, ...]) -> argparse.ArgumentParser:
    return subs.add_parser(name, help=summary, description=summary, add_help=False,
                           formatter_class=_Formatter, parents=[_common(True)],
                           epilog=_example(*examples) + "\n\n" + EXIT_CODES)


def _help(p: argparse.ArgumentParser) -> None:
    p.add_argument_group("autres options").add_argument(
        "-h", "--help", action="help", default=argparse.SUPPRESS, help="affiche cette aide et quitte")


def build_parser(root: Optional[Path] = None) -> argparse.ArgumentParser:
    ap = _Parser(prog=PROG, add_help=False, formatter_class=_Formatter, parents=[_common(False)],
                 description="9AM9AM-TEAM : le CEO (Claude Code) planifie et vérifie, des agents gratuits exécutent.",
                 epilog=_example("status", '--dir ~/projets/site tick "créer la page d\'accueil"',
                                 "status --json | jq .counts", '--dry-run tick "essai"')
                 + "\n\n« " + PROG + " COMMANDE --help » détaille une commande.\n" + EXIT_CODES)
    meta = ap.add_argument_group("autres options")
    meta.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help="affiche cette aide et quitte")
    meta.add_argument("--version", action=_Version, dest=argparse.SUPPRESS, version_text=lambda: version_line(root or Path(".")),
                      help="affiche la version (commit git du dossier du logiciel) et quitte")
    subs = ap.add_subparsers(dest="cmd", title="commandes", metavar="COMMANDE")
    made = {name: _sub(subs, name, summary, ex) for name, summary, ex in _COMMANDS}
    for p in made.values():
        _help(p)
    goal = made["plan"].add_argument_group("arguments")
    goal.add_argument("goal", metavar="OBJECTIF", help="ce que le CEO doit découper en tâches (entre guillemets)")
    made["tick"].add_argument_group("arguments").add_argument(
        "goal", metavar="OBJECTIF", help="l'objectif à planifier s'il est nouveau, sinon reprise des tâches")
    for name in ("run", "tick"):
        g = made[name].add_argument_group("options de la commande")
        g.add_argument("--worker", default=None, metavar="AGENT",
                       help=f"agent visé ({', '.join(NAMES)} ou un agent ajouté dans l'interface)")
        g.add_argument("--model", default=None, metavar="PROVIDER/MODEL",
                       help="model opencode (défaut : réglage de l'interface)")
    made["menu"].add_argument_group("options de la commande").add_argument(
        "--model", default=None, metavar="PROVIDER/MODEL", help="model opencode (défaut : réglage de l'interface)")
    made["status"].add_argument_group("options de la commande").add_argument(
        "--json", action="store_true",
        help="état en JSON sur la sortie standard (journal et avertissements sur la sortie d'erreur)")
    made["chat"].add_argument_group("arguments").add_argument(
        "message", metavar="MESSAGE", help="ton message au CEO (entre guillemets)")
    made["report"].add_argument_group("arguments").add_argument(
        "goal", metavar="OBJECTIF", nargs="?", default="", help="objectif visé (défaut : le dernier plan)")
    g = made["gui"].add_argument_group("options de la commande")
    g.add_argument("--no-browser", action="store_true", help="n'ouvre pas le navigateur (logiciel hôte)")
    g.add_argument("--parent", type=int, default=None, metavar="PID",
                   help="pid du logiciel hôte : le serveur s'arrête quand il disparaît")
    return ap
