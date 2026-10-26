"""Escalade d'une tâche trop dure pour le worker courant : Claude Code prend le relais.

Échelle fixe et monotone (jamais de retour en arrière, jamais au-delà du sommet) :
  palier 0  le worker prévu au plan (opencode…)
  palier 1  Claude Code · Sonnet · effort élevé
  palier 2  Claude Code · Opus · effort max
Chaque passage de palier consomme une tentative (tasks.MAX_ATTEMPTS) : l'échelle ne
peut donc pas boucler — au sommet, la tâche échoue honnêtement et le CEO le dit.

Déclencheur UNIQUE : le CEO a refusé la qualité du travail (« ceo_ko »). Un échec
d'exécution (délai dépassé, model gratuit en panne, erreur) n'est pas de la difficulté :
nouvel essai sur le même worker (opencode change alors de model gratuit), jamais Claude.
Plafond : MAX_ESCALATED_PER_PLAN tâches reprises par Claude par demande.
Fonctions pures : une tâche entre, une nouvelle tâche sort.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

CLAUDE_WORKER = "claude"  # réservé : ni agent custom, ni réglage d'interface
MAX_TIER = 2
MAX_ESCALATED_PER_PLAN = 2      # tâches confiées à Claude Code par demande, au plus
CEO_REFUSED = "ceo_ko"          # last_failure : le CEO a jugé le résultat insuffisant
WORKER_ERROR = "worker_error"   # last_failure : le worker a planté / dépassé le délai
SETTING_REFUSED = "setting_refused"  # last_failure : un réglage interdit de lancer ce worker (rien n'a tourné)


@dataclass(frozen=True)
class Rung:
    model: str    # alias `claude --model`
    effort: str   # niveau `claude --effort`
    label: str    # affichage utilisateur


LADDER: Dict[int, Rung] = {
    1: Rung("sonnet", "high", "Sonnet · élevé"),
    2: Rung("opus", "max", "Opus · max"),
}


def current_tier(task: dict) -> int:
    """Palier courant ; une valeur illisible vaut « pas escaladée », jamais une valeur devinée."""
    raw = task.get("tier")
    tier = raw if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw <= MAX_TIER else 0
    if task.get("worker") == CLAUDE_WORKER:
        return max(tier, 1)  # planifiée directement sur Claude Code : départ au palier 1
    return tier


def rung_for(task: dict) -> Optional[Rung]:
    """Réglage Claude Code de cette tâche ; None si elle ne tourne pas sur Claude Code."""
    return LADDER[current_tier(task)] if task.get("worker") == CLAUDE_WORKER else None


def was_escalated(task: dict) -> bool:
    """Tâche reprise par Claude Code après un autre worker (compte dans le plafond)."""
    return bool(task.get("origin_worker")) and current_tier(task) >= 1


def escalated(task: dict) -> Optional[dict]:
    """La tâche relancée un palier plus haut, ou None : pas de refus du CEO (échec
    d'exécution, interruption), ou sommet atteint."""
    if task.get("interrupted") or task.get("last_failure") != CEO_REFUSED:
        return None
    nxt = current_tier(task) + 1
    if nxt > MAX_TIER:
        return None
    origin = task.get("origin_worker") or (task.get("worker") if task.get("worker") != CLAUDE_WORKER else "")
    out = {**task, "worker": CLAUDE_WORKER, "tier": nxt}
    if origin:
        out["origin_worker"] = origin
    return out
