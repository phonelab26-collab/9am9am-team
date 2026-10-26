#!/usr/bin/env python3
"""9AM9AM-TEAM — point d'entrée. Le code est dans le paquet `orch/` à côté.

Aide complète : `python3 mini_orchestrateur.py --help` (et `COMMANDE --help`).
  python3 mini_orchestrateur.py                    # menu (double-clic)
  python3 mini_orchestrateur.py gui                 # interface web locale
  python3 mini_orchestrateur.py plan "objectif"     # le CEO découpe en tâches
  python3 mini_orchestrateur.py tick "objectif"     # reprise + plan + 1 tâche + supervision
  python3 mini_orchestrateur.py status [--json]     # état (lecture seule)
  python3 mini_orchestrateur.py --dir ~/projet tick "…"   # autre dossier de travail
  python3 mini_orchestrateur.py --dry-run tick "…"  # boucle sans appeler les CLI
  python3 mini_orchestrateur.py --version
Codes de sortie : 0 réussi · 1 échec ou erreur · 2 usage incorrect · 130 interrompu.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from orch.cli import main  # noqa: E402  (après l'ajout de ROOT au chemin)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], ROOT))
