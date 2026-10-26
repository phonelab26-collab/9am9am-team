"""Masquage des secrets avant qu'un texte d'agent soit affiché ou enregistré (chat.json).

Un agent peut lancer `curl -H "Authorization: Bearer sk-…"` ou `API_KEY=… npm test` : sa commande
ne doit ni s'afficher ni rester en clair sur le disque. On masque la VALEUR (« *** »), jamais le nom,
pour que le texte reste lisible. Un simple mot (« TokenizerTests », « keyboard ») n'est pas un secret.
"""
from __future__ import annotations

import re

MASK = "***"
# Un secret collé dans une commande : affectation NOM=valeur au nom sensible, ou jeton reconnaissable.
# (Partagés avec verify.py, qui REFUSE une commande qui en contient.)
# Quantificateurs BORNÉS : un mot géant sans « = » coûtait un temps quadratique. La borne (256)
# reste bien au-delà des vrais noms de variables (un nom long ne doit pas échapper au masquage).
SECRET_ASSIGN = re.compile(r"\b([A-Za-z0-9_]{0,256}(?:API_?KEY|SECRET|TOKEN|PASSWORD|PASSWD|MOT_DE_PASSE|_CLE)"
                           r"[A-Za-z0-9_]{0,256})=", re.IGNORECASE)
SECRET_LITERAL = re.compile(r"\b(ghp_|github_pat_|sk-[A-Za-z0-9]|xox[bp]-|Bearer\s)", re.IGNORECASE)

_VALUE = r"""(?:"[^"]*"|'[^']*'|[^\s"']*)"""
_ASSIGNMENT = re.compile(SECRET_ASSIGN.pattern + _VALUE, re.IGNORECASE)
_URL_CREDENTIALS = re.compile(r"(://[^/\s:@]+:)[^@\s/]+@")
_BEARER = re.compile(r"(\bBearer\s+)[^\s\"']+", re.IGNORECASE)
_AUTH_HEADER = re.compile(r"(\bAuthorization:\s*\w+\s+)[^\s\"']+", re.IGNORECASE)
_TOKEN_SHAPES = re.compile(r"\b(?:ghp_|github_pat_|xox[bp]-)[A-Za-z0-9_-]+|\bsk-[A-Za-z0-9][A-Za-z0-9_-]*",
                           re.IGNORECASE)
# Une option commence un mot (« tool --token x ») : jamais au milieu (« mot-de-passe », « a-a-a-… »),
# sinon chaque tiret relançait une analyse jusqu'au bout du texte (temps quadratique).
_FLAG = re.compile(r"""(?<![\w-])(--?)([A-Za-z][A-Za-z-]*)(=|\s+)("[^"]*"|'[^']*'|[^\s"']+)""")
# Une option est sensible si l'un de ses mots l'est (--api-key, --token, --db-password) ;
# « --keep-going » ou « --monkey-patch » ne le sont pas.
_SENSITIVE_WORDS = frozenset({"key", "apikey", "token", "secret", "password", "passwd", "pwd", "auth"})


def _mask_flag(m: "re.Match[str]") -> str:
    if _SENSITIVE_WORDS.isdisjoint(m.group(2).lower().split("-")):
        return m.group(0)
    return m.group(1) + m.group(2) + m.group(3) + MASK


def redact(text: object) -> str:
    """Le texte sans les valeurs de secrets reconnues (idempotent ; non-texte → « »)."""
    if not isinstance(text, str):
        return ""
    out = _URL_CREDENTIALS.sub(lambda m: m.group(1) + MASK + "@", text)
    out = _ASSIGNMENT.sub(lambda m: m.group(1) + "=" + MASK, out)
    out = _BEARER.sub(lambda m: m.group(1) + MASK, out)
    out = _AUTH_HEADER.sub(lambda m: m.group(1) + MASK, out)
    out = _TOKEN_SHAPES.sub(MASK, out)
    return _FLAG.sub(_mask_flag, out)
