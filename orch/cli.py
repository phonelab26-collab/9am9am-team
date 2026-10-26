"""Ligne de commande et menu Terminal."""
from __future__ import annotations

import json
import os
import signal
import sys
import unittest
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import tasks as T
from .ceo import Ceo
from .chat import ChatStore
from .config import Bins, ConfigStore, Paths
from .cli_parser import EXIT_FAILED, EXIT_INTERRUPTED, EXIT_OK, EXIT_USAGE, build_parser
from .engine import PROBLEM_PREFIX, Engine
from .journal import Journal
from .lockfile import LockTimeout
from .store import Store, StoreError
from .trust import TrustStore, follow_state
from .workers import Workers

from .workspace_root import validate_workspace


def build(root: Path, echo: Optional[Callable[[str], None]],
          settings_dir: Optional[Path] = None,
          scanned: Optional[Callable[[], List[str]]] = None) -> Tuple[Engine, Journal]:
    paths = Paths.for_root(root, settings_dir)
    origin = TrustStore(paths, echo or print).state_origin()  # avant que le journal ne crée .mini-orch
    journal = Journal(paths, echo)
    cfg, bins = ConfigStore(paths, journal), Bins.resolve()
    store = Store(paths, journal)
    follow_state(TrustStore(paths, journal), origin, _pending(store), journal)
    engine = Engine(paths, store, cfg, Workers(paths, bins, cfg, journal, scanned=scanned),
                    Ceo(bins.claude, str(paths.root), journal, model=cfg.ceo_model,
                        mode=cfg.ceo_mode, cloud_session=cfg.ceo_cloud_session,
                        cloud_repo=cfg.ceo_cloud_repo,
                        effort=cfg.ceo_effort_raw),
                    journal, chat=ChatStore(paths, journal))
    return engine, journal


def _pending(store: Store) -> Optional[int]:
    """Tâches qui restent à faire (None = liste illisible)."""
    try:
        return sum(1 for t in store.snapshot() if t.get("status") not in (T.DONE, T.FAILED))
    except StoreError:
        return None


def run_selftest(root: Path) -> int:
    """Suite de tests hermétique (faux CLI, dossiers temporaires, aucune interface ouverte)."""
    tests = Path(root) / "tests"
    if not (tests / "__init__.py").exists():
        print("selftest : dossier tests/ introuvable à côté de mini_orchestrateur.py (copie incomplète ?).")
        return 1
    suite = unittest.TestLoader().discover(str(tests), top_level_dir=str(root))
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=1).run(suite)
    bad = len(result.failures) + len(result.errors)
    print(f"Selftest : {'tout est OK' if not bad else f'{bad} échec(s)'} ({result.testsRun} tests).")
    return 0 if result.wasSuccessful() else 1


def _ask(prompt: str) -> Optional[str]:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None


MENU = ("\n[1] plan  [2] run (1 tâche)  [3] tick (plan+run+supervision)  [4] status  "
        "[5] quitter  [6] selftest  [7] réessayer bloquées/échouées")


def menu(engine: Engine, root: Path, model: Optional[str], dry_run: bool) -> int:
    while True:
        print(f"\n===== {engine.status_lines()[0].split(' :')[0]} =====")
        print("\n".join(engine.status_lines()))
        print(MENU)
        choice = _ask("Choix : ")
        if choice is None or choice == "5":
            return 0
        try:
            if choice in ("1", "3"):
                goal = _ask("Objectif : " if choice == "1" else "Objectif (vide = reprendre les tâches) : ")
                if goal is None:
                    return 0
                engine.plan(goal, dry_run) if choice == "1" else engine.tick(goal, None, model, dry_run)
            elif choice == "2":
                engine.run_next(None, model, dry_run)
            elif choice == "4":
                continue
            elif choice == "6":
                run_selftest(root)
            elif choice == "7":
                engine.retry()
            else:
                print("Choix inconnu.")
        except (StoreError, LockTimeout) as e:
            print(f"ERREUR : {e}")


def _emit(payload: object, as_json: bool, code: int) -> int:
    """Erreur de la CLI : « ERREUR : … » en texte, {"ok": false, "error": …} en --json."""
    print(json.dumps({"ok": False, "error": payload}, ensure_ascii=False) if as_json else f"ERREUR : {payload}")
    return code


def _workdir(raw: Optional[str], root: Path) -> Tuple[Optional[Path], str]:
    """Dossier de travail demandé (--dir, relatif au dossier courant) ou celui du logiciel."""
    if raw is None:
        return root, ""
    return validate_workspace(str(Path(os.path.expanduser(raw)).resolve()))


def install_signal_handlers() -> None:
    """Terminal fermé (SIGHUP) ou arrêt (SIGTERM) : même chemin que Ctrl-C, donc les agents
    lancés sont arrêtés et la tâche remise en file au lieu de tourner sans interface."""
    def _interrupt(signum, _frame):
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _interrupt)


def main(argv: List[str], root: Path) -> int:
    try:
        a = build_parser(root).parse_args(argv)
    except SystemExit as e:  # --help, --version, usage incorrect (2) : un code de retour, pas une exception
        return e.code if isinstance(e.code, int) else EXIT_OK
    cmd, as_json = a.cmd or "menu", bool(getattr(a, "json", False))
    if a.workdir is not None and cmd in ("gui", "selftest"):
        return _emit(f"--dir n'a pas d'effet sur « {cmd} »"
                     + (" : choisis le dossier avec « 📂 Parcourir… »." if cmd == "gui" else "."),
                     as_json, EXIT_USAGE)
    if cmd == "selftest":
        return run_selftest(root)
    if cmd == "gui":
        from .webui import serve  # interface dans le navigateur (le Tk d'Apple affiche blanc)
        return serve(root, a.dry_run, open_browser=not a.no_browser, parent_pid=a.parent)
    workdir, err = _workdir(a.workdir, root)
    if workdir is None:
        return _emit(err, as_json, EXIT_USAGE)
    install_signal_handlers()
    # --json : stdout ne porte que le JSON ; journal et avertissements vont sur la sortie d'erreur.
    echo = (lambda m: print(m, file=sys.stderr)) if as_json else print
    try:
        engine, _ = build(workdir, echo, Paths.for_root(root).state)  # réglages globaux, comme l'interface
        if cmd == "status":
            if as_json:
                data = engine.status_data()
                print(json.dumps(data, ensure_ascii=False, indent=2))
                return EXIT_FAILED if data["problem"] else EXIT_OK
            lines = engine.status_lines()
            print("\n".join(lines))
            return EXIT_FAILED if any(ln.startswith(PROBLEM_PREFIX) for ln in lines) else EXIT_OK
        if cmd == "plan":
            return engine.plan(a.goal, a.dry_run)
        if cmd == "run":
            return engine.run_next(a.worker, a.model, a.dry_run).rc
        if cmd == "tick":
            return engine.tick(a.goal, a.worker, a.model, a.dry_run)
        if cmd == "chat":
            return engine.converse(a.message, a.dry_run)
        if cmd == "retry":
            return engine.retry()
        if cmd == "report":
            return engine.report(a.goal, a.dry_run)
        return menu(engine, root, getattr(a, "model", None), a.dry_run)
    except (StoreError, LockTimeout, OSError) as e:  # OSError : dont un lien planté dans .mini-orch
        return _emit(str(e), as_json, EXIT_FAILED)
    except KeyboardInterrupt:
        if as_json:
            return EXIT_INTERRUPTED
        print("\nInterrompu : la tâche en cours est remise en file (blocked).")
        return EXIT_INTERRUPTED
