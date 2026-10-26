"""Registry des workers intégrés : la SEULE déclaration des providers supportés.

Pour ajouter un worker intégré : une entrée ici — `config`, `cli`, `workers`,
`engine` et l'interface web dérivent tous leur liste de cette table (avant,
cinq listes en dur devaient être synchronisées à la main).

Deux kinds d'exécution :
- « structured » : logique dédiée (catalogue de models, repli automatique,
  parse d'événements) — c'est le cas d'opencode ;
- « shell » : `sh -c <commande> $ORCH_PROMPT`, commande posée dans les
  réglages ou portée par MINI_ORCH_<NOM>_CMD.

Les agents « customs » (ajoutés dans l'interface) sont des shells non intégrés :
ils vivent dans la config, pas dans ce registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    kind: str                # "structured" | "shell"
    default: bool = False    # worker de repli (dry-run, aucun worker détecté)
    extra_settings: Tuple[str, ...] = ()  # clés de réglages propres à ce worker

    @property
    def is_shell(self) -> bool:
        return self.kind == "shell"

    @property
    def cmd_env(self) -> str:
        """Variable d'environnement qui peut porter la commande (shell)."""
        return f"MINI_ORCH_{self.name.upper()}_CMD"

    @property
    def config_cmd_key(self) -> str:
        """Clé de config où vit la commande réglée dans l'interface (shell)."""
        return f"{self.name}_cmd"

    @property
    def setting_keys(self) -> Tuple[str, ...]:
        """Clés de réglages exposées par l'interface pour ce worker."""
        keys = [f"{self.name}_model"]
        if self.is_shell:
            keys.append(self.config_cmd_key)
            keys.append(f"{self.name}_effort")
        keys.extend(self.extra_settings)
        return tuple(keys)


SPECS: Tuple[WorkerSpec, ...] = (
    WorkerSpec("opencode", "structured", default=True,
               extra_settings=("opencode_agent", "opencode_variant")),
    WorkerSpec("autoclaw", "shell"),
    WorkerSpec("cline", "shell", extra_settings=("cline_models",)),
)

WORKERS: Dict[str, WorkerSpec] = {s.name: s for s in SPECS}
NAMES: Tuple[str, ...] = tuple(WORKERS)          # ordre = ordre d'affichage
DEFAULT: str = next(s.name for s in SPECS if s.default)


def spec_for(name: str) -> Optional[WorkerSpec]:
    """Spec d'un worker intégré, ou None (custom ou inconnu)."""
    return WORKERS.get(str(name or ""))


def shell_names() -> Tuple[str, ...]:
    """Workers intégrés exécutés via `sh -c` (autoclaw, cline…)."""
    return tuple(s.name for s in SPECS if s.is_shell)


def setting_keys() -> Tuple[str, ...]:
    """Toutes les clés de réglages workers (whitelist de l'API /api/setting)."""
    return tuple(k for s in SPECS for k in s.setting_keys)
