"""Appels `claude -p` sobres, et lecture de la consommation réelle qu'ils renvoient.

Mesuré (haiku, « Réponds uniquement : OK ») : un appel par défaut embarque ~40 000 tokens
de contexte (MCP, skills, hooks et CLAUDE.md perso, définitions d'outils). Sans tout ça et
sans outils : ~7 000 (−83 %) ; avec lecture seule : ~9 500. `--bare` n'est pas utilisable :
il coupe la connexion par abonnement (clé API obligatoire).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

# Ni MCP, ni skills (slash commands), ni réglages perso (hooks, CLAUDE.md global) : le CEO
# n'en a pas besoin. Les réglages du PROJET (CLAUDE.md du dossier de travail) restent lus.
LEAN_ARGS = ("--strict-mcp-config", "--disable-slash-commands", "--setting-sources", "project,local")
# Le renfort Claude modifie des fichiers : il garde tes autorisations perso, seuls MCP et skills tombent.
WORKER_LEAN_ARGS = ("--strict-mcp-config", "--disable-slash-commands")
JSON_OUTPUT = ("--output-format", "json")
NO_TOOLS = ""                   # planifier, discuter, rédiger : aucun outil
READ_TOOLS = "Read,Glob,Grep"   # vérifier un résultat : relire les fichiers, jamais écrire


@dataclass(frozen=True)
class CliResult:
    text: Optional[str]      # texte de la réponse (None si erreur)
    tokens: Optional[int]    # tokens traités (contexte + sortie) ; None = non mesuré
    error: str = ""


def read_result(out: str) -> CliResult:
    """Enveloppe JSON de `--output-format json` → texte + tokens. Toute autre sortie (texte
    brut, faux CLI des tests, JSON métier sans enveloppe) est gardée telle quelle, tokens
    non mesurés : jamais de chiffre inventé. Une enveloppe d'erreur est lue AVANT le reste,
    même sans clé `result` : son message et ses tokens comptent."""
    start = out.find("{")
    try:
        data = json.loads(out[start:]) if start >= 0 else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or ("usage" not in data and data.get("type") != "result"):
        return CliResult(out, None)
    if "result" not in data and not data.get("is_error"):
        return CliResult(out, None)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    keys = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    counts = [usage.get(k) for k in keys]
    tokens = sum(int(c) for c in counts if isinstance(c, int)) if any(isinstance(c, int) for c in counts) else None
    if data.get("is_error"):
        why = str(data.get("result") or data.get("subtype") or "erreur du CLI Claude")
        return CliResult(None, tokens, why[:300])
    return CliResult(str(data.get("result") or ""), tokens)
