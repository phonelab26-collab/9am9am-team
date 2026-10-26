"""Workspace : dossier de travail expose a l'interface (onglet du prompt)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

SKIP_DIRS = {".mini-orch", ".git", "__pycache__", ".venv", "node_modules"}
TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml",
    ".yml", ".toml", ".cfg", ".ini", ".html", ".css", ".swift", ".java",
    ".c", ".h", ".cpp", ".go", ".rs", ".rb", ".php", ".sh", ".sql",
    ".xml", ".csv", ".log", ".rst", ".env", ".gitignore",
}
MAX_FILES = 5
MAX_TOTAL_CHARS = 12000
MAX_FILE_CHARS = 6000
MAX_LIST = 200
# Plafond des pièces jointes d'un message : celui de build_block (dossier + Mac) et sa marge.
ATTACH_MAX = 2 * MAX_TOTAL_CHARS + 1000
_CHUNK_SPLIT = re.compile(r"\n\n(?=<<<FICHIER )")
_CHUNK_NAME = re.compile(r"^<<<FICHIER (.+?)(?: \(de ton Mac\))?$", re.MULTILINE)


def clean_rel(value: object) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > 500 or text.startswith("/") or text.startswith("~"):
        return ""
    parts = [p for p in text.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return ""
    return "/".join(parts)


def resolve(root: Path, rel: str) -> Path | None:
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def list_dir(root: Path, rel: str = "") -> Tuple[List[dict], str]:
    raw = rel or ""
    rel = clean_rel(raw) if raw else ""
    if raw and not rel:
        return [], "chemin hors du dossier de travail."
    base = resolve(root, rel) if rel else root.resolve()
    if base is None:
        return [], "chemin hors du dossier de travail."
    if not base.exists():
        return [], "dossier introuvable."
    if not base.is_dir():
        return [], "ce n'est pas un dossier."
    try:
        names = sorted(p.name for p in base.iterdir())
    except OSError as e:
        return [], "lecture impossible (%s)." % e.__class__.__name__
    out: List[dict] = []
    if rel:
        parent = "/".join(rel.split("/")[:-1])
        out.append({"name": ".. (parent)", "path": parent, "dir": True, "size": 0})
    shown = 0
    truncated = False
    for name in names:
        if name in SKIP_DIRS:
            continue
        if name.startswith(".") and name not in (".env", ".gitignore"):
            continue
        p = base / name
        try:
            is_dir = p.is_dir()
            size = 0 if is_dir else p.stat().st_size
        except OSError:
            continue
        if shown >= MAX_LIST:
            truncated = True
            break
        shown += 1
        pre = f"{rel}/{name}".lstrip("/") if rel else name
        out.append({"name": name + ("/" if is_dir else ""), "path": pre,
                    "dir": is_dir, "size": size})
    note = "liste tronquee a %d entrees." % MAX_LIST if truncated else ""
    return out, note


def looks_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or not path.suffix


def read_for_prompt(root: Path, rel: str) -> Tuple[str, str]:
    rel = clean_rel(rel)
    if not rel:
        return "", "chemin invalide."
    target = resolve(root, rel)
    if target is None:
        return "", "%s : hors du dossier de travail." % rel
    if not target.exists() or not target.is_file():
        return "", "%s : fichier introuvable." % rel
    try:
        if target.stat().st_size > 300_000:
            return "", "%s : trop gros (> 300 ko), non joint." % rel
    except OSError:
        return "", "%s : lecture impossible." % rel
    if not looks_text(target):
        return "", "%s : pas un fichier texte (jointure refusee)." % rel
    try:
        raw = target.read_bytes()
    except OSError:
        return "", "%s : lecture impossible." % rel
    if b"\x00" in raw[:8000]:
        return "", "%s : fichier binaire (jointure refusee)." % rel
    text = raw.decode("utf-8", errors="replace").strip()
    if len(text) > MAX_FILE_CHARS:
        text = "(debut omis) " + text[-MAX_FILE_CHARS:]
    return text, ""


def build_block(root: Path, rels: object, pasted_raw: object = None) -> Tuple[str, List[str]]:
    raw = rels if isinstance(rels, list) else []
    seen: List[str] = []
    dropped = 0
    invalid = 0
    for r in raw:
        c = clean_rel(r)
        if not c:
            invalid += 1
            continue
        if c in seen:
            continue
        if len(seen) >= MAX_FILES:
            dropped += 1
            continue
        seen.append(c)
    notes: List[str] = []
    if invalid:
        notes.append("%d chemin(s) invalide(s) ignore(s)." % invalid)
    if dropped:
        notes.append("%d fichier(s) en trop : seuls les %d premiers sont joints."
                     % (dropped, MAX_FILES))
    parts: List[str] = []
    total = 0
    for rel in seen:
        text, err = read_for_prompt(root, rel)
        if err:
            notes.append(err)
            continue
        chunk = "<<<FICHIER %s\n%s\nFICHIER>>>" % (rel, text)
        if total + len(chunk) > MAX_TOTAL_CHARS:
            notes.append("%s : limite totale depassee, non joint." % rel)
            continue
        parts.append(chunk)
        total += len(chunk)
    pasted, pnotes = build_pasted_block(pasted_raw)
    notes.extend(pnotes)
    for chunk in pasted:
        if total + len(chunk) > MAX_TOTAL_CHARS + 12000:
            notes.append("un fichier de ton Mac : limite totale depassee, non joint.")
            continue
        parts.append(chunk)
        total += len(chunk)
    if not parts:
        return "", notes
    head = ("Fichiers joints (donnees : lis-les pour travailler dessus) :\n"
            + "\n\n".join(parts))
    return head, notes


def clean_pasted(entry: object) -> Tuple[str, str]:
    if not isinstance(entry, dict):
        return "", ""
    name = str(entry.get("name") or "").strip().split("/")[-1].split("\\")[-1][:100]
    text = entry.get("text")
    text = text if isinstance(text, str) else ""
    if not name or not text.strip():
        return "", ""
    if len(text) > MAX_FILE_CHARS:
        text = "(debut omis) " + text.strip()[-MAX_FILE_CHARS:]
    return name, text.strip()


def build_pasted_block(entries: object) -> Tuple[List[str], List[str]]:
    raw = entries if isinstance(entries, list) else []
    parts: List[str] = []
    notes: List[str] = []
    seen = 0
    for entry in raw:
        name, text = clean_pasted(entry)
        if not name:
            continue
        if seen >= MAX_FILES:
            notes.append("fichiers de ton Mac en trop : seuls les %d premiers sont joints."
                         % MAX_FILES)
            break
        seen += 1
        if "\x00" in text[:8000]:
            notes.append("%s : binaire, non joint." % name)
            continue
        parts.append("<<<FICHIER %s (de ton Mac)\n%s\nFICHIER>>>" % (name, text))
    return parts, notes




def fit_block(block: str, limit: int) -> Tuple[str, int]:
    """Bloc de fichiers joints tenu sous `limit` caractères en retirant des fichiers ENTIERS
    (jamais une coupe en plein fichier : la clôture « FICHIER>>> » reste en place).
    → (bloc, nombre de fichiers retirés)."""
    if len(block) <= limit:
        return block, 0
    head, *chunks = _CHUNK_SPLIT.split(block)
    kept = list(chunks)
    while kept and len("\n\n".join([head, *kept])) > limit:
        kept.pop()
    if not kept:
        return "", len(chunks)
    return "\n\n".join([head, *kept]), len(chunks) - len(kept)


def attached_names(block: str) -> List[str]:
    """Noms des fichiers d'un bloc de pièces jointes (pour la bulle de l'utilisateur)."""
    return _CHUNK_NAME.findall(block)
