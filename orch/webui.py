"""Interface dans le navigateur (page web locale servie par la stdlib).

Pourquoi pas de GUI native : le seul Tk de ce Mac est le Tk 8.5.9 d'Apple (2011).
Sous macOS 26 il n'affiche plus rien (page blanche) et boucle sur ses redessins
(100 % CPU). Le navigateur, lui, affiche toujours ; le serveur est en stdlib pure.

Sécurité (un serveur local reste joignable par les pages web ouvertes) :
- écoute uniquement sur 127.0.0.1, port aléatoire ;
- jeton secret : `?t=` pour la page et ses fichiers, en-tête `X-Token` pour l'API
  (une autre page ne peut pas poser cet en-tête sans pré-vol CORS, jamais accordé) ;
- en-tête Host vérifié (DNS rebinding) ; corps JSON uniquement, taille bornée ;
- CSP stricte, pas de Referer (le jeton ne fuit pas) ; le front n'injecte jamais de HTML.
"""
from __future__ import annotations

import hmac
import json
import os
import queue
import re
import secrets
import signal
import threading
import time
import webbrowser
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .ceo import clean_cloud_session, clean_ceo_mode, clean_model
from .effort import clean_effort
from .verify import clean_verify_cmd, refusal_reason
from .config import (BUILTIN_WORKERS, CLINE_MODELS_MAX, RESERVED_WORKERS, clean_agent_name,
                     clean_custom_model, parse_custom_models, parse_model_list)
from .engine import MESSAGE_MAX, TEAM_NAME
from .github import GithubRepos, clean_repo
from .account import clean_email
from .gui_controller import CEO_SETTING_KEYS, GuiController
from .redact import redact
from .shellcheck import shell_syntax_error

WEB_DIR = Path(__file__).parent / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/markdown.js": ("markdown.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
       "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
from .registry import setting_keys as worker_setting_keys, shell_names

# Whitelist des réglages : clés CEO/produit explicites + toutes les clés workers
# générées depuis le registry (une clé orpheline = un réglage invisible à l'API).
SETTINGS = ("ceo_model", "ceo_effort", "ceo_mode", "ceo_cloud_session", "ceo_cloud_repo") \
    + worker_setting_keys() + ("custom_models", "ui_color", "escalation", "verify_cmd")
# Commandes des workers shell intégrés (autoclaw, cline…) : déclarées par le registry.
SHELL_CMD_KEYS = tuple(f"{name}_cmd" for name in shell_names())
UI_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}\Z")
MAX_BODY = 256 * 1024
JOURNAL_MAX = 3000
HANDSHAKE_TTL_S = 60  # durée de vie du nonce one-shot posé dans l'URL d'ouverture
USAGE_CACHE_TTL_S = 120  # `claude -p /usage` : ≈ 7 s et un sous-process, pas plus d'un appel / 2 min
AUTH_CACHE_TTL_S = 5  # /api/auth lance un sous-process : pas plus d'un appel / 5 s
FORCE_MIN_GAP_S = 60  # ?force=1 ignoré si un fetch forcé a eu lieu il y a moins d'une minute
CLOSE_WAIT_S = 20
SERVE_POLL_S = 0.5  # boucle d'arrêt propre du serveur HTTP
PARENT_WATCH_S = 2.0  # intervalle de veille sur le pid du logiciel hôte
LOGIN_CMD_HINT = "claude auth login --email"


class WebApp:
    """État exposé au navigateur : tâches, journal (par index), job en cours."""

    def __init__(self, ctl: GuiController) -> None:
        self.ctl = ctl
        self.on_quit: Callable[[], None] = lambda: None
        self._lines: List[str] = []
        self._first = 0  # index du premier élément encore gardé
        self._lock = threading.Lock()
        self.last_rc: Optional[int] = None
        self.repos = GithubRepos(lambda msg: ctl.log(msg))  # journal du dossier COURANT (il peut changer)
        self._auth_cache: Optional[Tuple[float, dict]] = None
        self._usage_cache: Optional[Tuple[float, dict]] = None
        self._usage_lock = threading.Lock()
        self._cache_gen = 0  # +1 à chaque changement de compte : invalide les lectures en cours
        ctl.on_account_change = self._drop_account_caches  # compte changé : plus de cache périmé
        self._last_force = float("-inf")  # fenêtre de throttle : jamais de fetch forcé au départ
        self.handshake: dict = {"nonce": "", "at": 0.0, "used": False}
        self.handshake_lock = threading.Lock()

    def _pump(self) -> None:
        while True:
            try:
                kind, payload = self.ctl.messages.get_nowait()
            except queue.Empty:
                return
            with self._lock:
                if kind == "done":
                    self.last_rc = int(payload)
                    continue
                self._lines.extend(str(payload).splitlines() or [""])
                excess = len(self._lines) - JOURNAL_MAX
                if excess > 0:
                    del self._lines[:excess]
                    self._first += excess

    def auth_status(self) -> dict:
        """État de connexion : sous-process délégué à la façade, cache 5 s ici
        (un appel = un `claude auth status` : un onglet qui boucle ne doit pas
        saturer la machine)."""
        with self._lock:
            if self._auth_cache and time.monotonic() - self._auth_cache[0] < AUTH_CACHE_TTL_S:
                return self._auth_cache[1]
            gen = self._cache_gen
        out = self.ctl.auth_status()
        with self._lock:
            if gen == self._cache_gen:  # le compte a changé pendant la lecture : résultat périmé, non gardé
                self._auth_cache = (time.monotonic(), out)
        return out

    def usage_status(self) -> dict:
        """Usage Claude pour la ligne du haut : un seul appel à la fois, cache 2 min."""
        with self._usage_lock:  # sérialise : deux onglets ne lancent pas deux `claude`
            if self._usage_cache and time.monotonic() - self._usage_cache[0] < USAGE_CACHE_TTL_S:
                return self._usage_cache[1]
            gen = self._cache_gen
            out = self.ctl.usage_status()
            if gen == self._cache_gen:  # idem : jamais 2 min d'usage de l'ancien compte
                self._usage_cache = (time.monotonic(), out)
            return out

    def auth_logout(self) -> Tuple[int, dict]:
        reason = self.ctl.account_busy_reason()
        if reason:
            return 409, {"error": reason}
        ok, err = self.ctl.auth_logout()
        if ok:
            with self._lock:
                self._auth_cache = None  # l'état vient de changer : pas de cache périmé
            return 200, {"ok": True}
        return 500, {"error": err}

    def _drop_account_caches(self) -> None:
        """Le compte vient de changer : ni l'identité ni l'usage en cache ne sont encore vrais."""
        with self._lock:
            self._cache_gen += 1
            self._auth_cache = None
        self._usage_cache = None  # sans verrou : un appel d'usage en cours peut durer 45 s

    def auth_switch(self, body: dict) -> Tuple[int, dict]:
        """Changer de compte Claude : 202 si lancé, 400 si l'adresse est invalide, 409 sinon."""
        email = clean_email(body.get("email"))
        if email is None:
            return 400, {"error": "adresse e-mail invalide : écris-la sous la forme prenom@exemple.com."}
        ok, err = self.ctl.switch_account(email)
        return (202, {"ok": True}) if ok else (409, {"error": err})

    def auth_switch_cancel(self) -> Tuple[int, dict]:
        return 200, {"ok": True, "cancelled": self.ctl.cancel_account_switch()}

    def github_repos(self, force: bool = False) -> Tuple[int, dict]:
        """Dépôts GitHub du compte `gh` : lecture seule, jamais de secret exposé.
        `force=1` contourne le cache : borné à 1 fetch forcé par minute pour ne pas
        marteler l'API GitHub avec le token de l'utilisateur."""
        with self._lock:
            if force:
                if time.monotonic() - self._last_force < FORCE_MIN_GAP_S:
                    force = False  # déjà un fetch forcé récent : on sert le cache
                else:
                    self._last_force = time.monotonic()
        res = self.repos.list(force=force)
        if not res.ok:
            return 503, {"ok": False, "error": res.error, "gh": self.repos.available()}
        return 200, {"ok": True, "gh": True, "warnings": list(res.warnings),
                     "repos": [{"repo": r.full_name, "private": r.private, "updated": r.updated}
                               for r in res.repos]}

    def chat(self, body: dict) -> Tuple[int, dict]:
        message = body.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > MESSAGE_MAX:
            return 400, {"error": f"message invalide (1 à {MESSAGE_MAX} caractères)"}
        block, notes = self.ctl.chat_block(body.get("files"), body.get("pasted"))
        if not self.ctl.start("Conversation", self.ctl.chat_job(message.strip(), block)):
            return 409, {"error": "Le CEO est déjà au travail : attends sa réponse ou clique sur « Arrêter »."}
        out: dict = {"ok": True}
        if notes:
            out["notes"] = notes
        return 202, out

    def workspace(self, rel: str) -> Tuple[int, dict]:
        return 200, self.ctl.list_workspace(rel)

    def workspace_read(self, body: dict) -> Tuple[int, dict]:
        data, err = self.ctl.read_workspace(body.get("path"))
        if err:
            return 400, {"error": err}
        return 200, data

    def state(self, since: int, chat_since: int = 0) -> dict:
        self._pump()
        with self._lock:
            start = max(since, self._first) - self._first
            journal, nxt = self._lines[start:], self._first + len(self._lines)
        out = self.ctl.state_view(chat_since)
        out.update({"last_rc": self.last_rc, "journal": journal, "next": nxt,
                    "login_hint": LOGIN_CMD_HINT})
        return out

    def action(self, body: dict) -> Tuple[int, dict]:
        name = body.get("action")
        goal, model = str(body.get("goal") or ""), str(body.get("model") or "")
        if name == "stop":
            self.ctl.stop()
            return 202, {"ok": True}
        if name == "quit":
            self.on_quit()
            return 202, {"ok": True}
        jobs: Dict[str, Tuple[str, Callable]] = {
            "plan": ("Plan CEO", lambda: self.ctl.plan_job(goal)),
            "run": ("Run", lambda: self.ctl.run_job(model)),
            "tick": ("Tick", lambda: self.ctl.tick_job(goal, model)),
            "retry": ("Réessayer", self.ctl.retry_job),
            "report": ("Rapport CEO", lambda: self.ctl.report_job(goal)),
            "selftest": ("Selftest", lambda: self.ctl.selftest_job),
        }
        if name not in jobs:
            return 400, {"error": f"action inconnue : {str(name)[:40]!r}"}
        label, make = jobs[name]
        if not self.ctl.start(label, make()):
            return 409, {"error": "Une tâche ou un changement de compte est déjà en cours : attends sa fin "
                                  "ou clique sur « Arrêter »."}
        return 202, {"ok": True}

    def setting(self, body: dict) -> Tuple[int, dict]:
        key, value = body.get("key"), body.get("value")
        if key not in SETTINGS or not isinstance(value, str) or len(value) > 5000:
            return 400, {"error": "réglage refusé"}
        text = value.strip()
        if key in CEO_SETTING_KEYS and self.ctl.busy:
            # Le CEO peut être en plein ask() : changer ses réglages à chaud le ferait
            # basculer de credentials/mode à mi-job (cf. gui_controller.save_setting).
            return 409, {"error": "CEO en cours : attends la fin du travail ou clique sur « Arrêter »."}
        if key == "ceo_model" and text and clean_model(value) != text:
            return 400, {"error": "model invalide : un nom comme « sonnet », « opus » ou « claude-sonnet-5 » "
                                  "(sans espace, sans « - » au début)"}
        if key == "verify_cmd":  # propre au dossier de travail courant
            cmd = clean_verify_cmd(value)
            if cmd is None:
                return 400, {"error": "commande de vérification refusée : " + refusal_reason(value)}
            self.ctl.save_verify_cmd(cmd)
            return 200, {"ok": True}
        if key == "escalation":
            if text.lower() not in ("on", "off"):
                return 400, {"error": "escalade : « on » (sur refus du CEO) ou « off »."}
            self.ctl.save_setting(key, text.lower())
            return 200, {"ok": True}
        if key.endswith("_effort"):  # ceo_effort, autoclaw_effort, cline_effort : 5 niveaux ou vide
            level = clean_effort(value)
            if level is None:
                return 400, {"error": "effort invalide : faible, normal, élevé, max, ultracode (ou vide = défaut)."}
            self.ctl.save_setting(key, level)
            return 200, {"ok": True}
        if key == "ceo_mode":
            if clean_ceo_mode(value) != text.lower():
                return 400, {"error": "mode invalide : « local » (ton Mac) ou « cloud » (session distante)."}
            self.ctl.save_setting(key, clean_ceo_mode(value))
            return 200, {"ok": True}
        if key == "ceo_cloud_session" and text and not clean_cloud_session(text):
            return 400, {"error": "session cloud invalide : colle l'id session_... (ou cse_...) ou "
                                  "l'URL claude.ai/code/... d'une session existante."}
        if key == "ceo_cloud_repo" and text and not clean_repo(text):
            return 400, {"error": "dépôt invalide : un « owner/repo » (ex. phonelab26-collab/9am9am-team)."}
        if key == "ui_color":
            # La couleur part directement dans le CSS dérivé côté client : la moindre
            # chose qui ressemblerait à du CSS serait une porte ouverte → hex strict.
            if text and not UI_COLOR_RE.fullmatch(text):
                return 400, {"error": "couleur invalide : un hexadécimal comme « #ffb000 » (ou vide pour le défaut)."}
            self.ctl.save_setting(key, text.lower())
            return 200, {"ok": True}
        if key in ("opencode_model", "autoclaw_model", "cline_model") and text:
            if not clean_custom_model(text) and not clean_model(text):
                return 400, {"error": "model invalide : « provider/model » (ex. opencode/big-pickle)."}
        if key in SHELL_CMD_KEYS and text:
            err = shell_syntax_error(text)
            if err:
                return 400, {"error": f"commande invalide (erreur de syntaxe shell) : {err}"}
        if key in ("opencode_agent", "opencode_variant") and text:
            if len(text) > 80 or " " in text or text.startswith("-"):
                return 400, {"error": "option invalide : lettres/chiffres/.-_ sans espace."}
        if key == "cline_models" and text:
            found = parse_model_list(text, limit=None)
            if not found:
                return 400, {"error": "aucun model valide : un nom par ligne (ex. stealth/space-bunny-alpha)."}
            if len(found) > CLINE_MODELS_MAX:
                return 400, {"error": f"trop de models : {CLINE_MODELS_MAX} au plus "
                                      "(note seulement les gratuits du moment)."}
        if key == "custom_models" and text:
            if not parse_custom_models(text):
                return 400, {"error": "aucun model valide : un « provider/model » par ligne."}
        self.ctl.save_setting(key, value)
        return 200, {"ok": True}

    def verify_approve(self, body: dict) -> Tuple[int, dict]:
        """Accord explicite pour la commande de vérification DÉTECTÉE (celle affichée)."""
        cmd = body.get("cmd")
        if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > 500:
            return 400, {"error": "commande à autoriser illisible."}
        ok, err = self.ctl.approve_verify(cmd.strip())
        return (200, {"ok": True}) if ok else (409, {"error": err})

    def custom_agent(self, body: dict) -> Tuple[int, dict]:
        """Ajoute ou supprime un agent custom (commande + model). Jamais de clé ici."""
        op = str(body.get("op") or "")
        agents = self.ctl.custom_agents()
        if op == "delete":
            name = clean_agent_name(body.get("name"))
            if not name:
                return 400, {"error": "nom d'agent invalide"}
            return 200, {"ok": True,
                         "agents": self.ctl.set_custom_agents([a for a in agents
                                                                if a["name"] != name])}
        name = clean_agent_name(body.get("name"))
        cmd = str(body.get("cmd") or "").strip()
        model = clean_custom_model(body.get("model") or "")
        effort = clean_effort(body.get("effort") or "")
        if effort is None:
            return 400, {"error": "effort invalide : faible, normal, élevé, max, ultracode (ou vide = défaut)."}
        if not name:
            return 400, {"error": "nom invalide : minuscules, chiffres, tirets (ex. mon-api)."}
        if name in RESERVED_WORKERS:
            return 400, {"error": f"« {name} » est un agent intégré : choisis un autre nom."}
        if not cmd or len(cmd) > 2000:
            return 400, {"error": "commande invalide (1 à 2000 caractères)."}
        err = shell_syntax_error(cmd)
        if err:
            return 400, {"error": f"commande invalide (erreur de syntaxe shell) : {err}"}
        for banned in ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "MOT_DE_PASSE", "_CLE="):
            if banned in cmd.upper():
                return 400, {"error": "ne colle jamais de secret dans la commande "
                                      f"(« {banned.lower()} » détecté) : utilise une variable "
                                      "d'environnement ou « opencode providers login »."}
        agents = [a for a in agents if a["name"] != name] + [{"name": name, "cmd": cmd, "model": model, "effort": effort}]
        return 200, {"ok": True, "agents": self.ctl.set_custom_agents(agents)}


def make_handler(app: WebApp, token: str, handshake: dict, handshake_lock: threading.Lock) -> type:
    class Handler(BaseHTTPRequestHandler):
        server_version, sys_version = TEAM_NAME, ""
        timeout = 30  # une connexion qui traîne ne bloque pas un thread indéfiniment

        def log_message(self, fmt: str, *args: object) -> None:  # accès : trop bavard
            pass

        def log_error(self, fmt: str, *args: object) -> None:
            msg = fmt % args
            if token and token in msg:  # le jeton ne doit jamais finir dans le journal
                msg = msg.replace(token, "[jeton]")
            app.ctl.log("interface web : " + str(msg)[:300])

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            for k, v in (("Content-Type", ctype), ("Content-Length", str(len(body))),
                         ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                         ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY"),
                         ("Content-Security-Policy", CSP)):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: dict) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _allowed(self, api: bool) -> bool:
            port = self.server.server_address[1]
            if self.headers.get("Host", "") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                return False
            if api:
                given = self.headers.get("X-Token", "")
            else:  # page/fichiers : vieux lien ?t= ou cookie posé par le handshake
                given = (parse_qs(urlparse(self.path).query).get("t", [""])[0]
                         or self._cookie_token())
            return hmac.compare_digest(given.encode(), token.encode())

        def _cookie_token(self) -> str:
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "orch":
                    return v
            return ""

        def _consume_handshake(self, url) -> bool:
            """Vrai si `?h=<nonce>` est valide : usage unique, valable 60 s."""
            given = parse_qs(url.query).get("h", [""])[0]
            with handshake_lock:
                if (handshake["nonce"] and not handshake["used"] and given == handshake["nonce"]
                        and time.monotonic() - handshake["at"] < HANDSHAKE_TTL_S):
                    handshake["used"] = True
                    return True
            return False

        def do_GET(self) -> None:  # noqa: N802 (nom imposé par http.server)
            self._guarded(self._get)

        def do_POST(self) -> None:  # noqa: N802
            self._guarded(self._post)

        def end_headers(self) -> None:
            self._headers_sent = True
            super().end_headers()

        def _guarded(self, handle: Callable[[], None]) -> None:
            """Filet : une erreur imprévue devient un 500 JSON lisible + une ligne de journal,
            jamais une connexion coupée que la page prendrait pour « Serveur injoignable »."""
            self._headers_sent = False
            try:
                handle()
            except ConnectionError:  # le navigateur a fermé la connexion : plus personne à qui répondre
                raise
            except Exception as e:  # noqa: BLE001 — dit au journal ET à la page, jamais avalé
                where = f"{self.command} {self.path.split('?', 1)[0][:80]}"  # sans la requête (jeton)
                app.ctl.log(f"interface web : erreur interne sur {where} — {e.__class__.__name__}: {redact(str(e))}")
                if not self._headers_sent:  # la page n'a que la classe : le détail (chemins…) reste au journal
                    self._json(500, {"error": f"erreur interne du serveur ({e.__class__.__name__}). "
                                              "Le détail est dans le journal technique."})

        def _get(self) -> None:
            url = urlparse(self.path)
            if url.path == "/" and self._consume_handshake(url):
                # Le jeton ne vit jamais dans l'URL (historique navigateur, extensions,
                # copier/coller) : le nonce one-shot pose un cookie HttpOnly puis
                # redirige vers une URL propre.
                self.send_response(302)
                for k, v in (("Location", "/"),
                             ("Set-Cookie", f"orch={token}; HttpOnly; SameSite=Strict; "
                                            f"Path=/; Max-Age=86400"),
                             ("Cache-Control", "no-store"),
                             ("Referrer-Policy", "no-referrer")):
                    self.send_header(k, v)
                self.end_headers()
                return
            if not self._allowed(url.path.startswith("/api/")):
                return self._json(403, {"error": "accès refusé"})
            if url.path == "/api/state":
                query = parse_qs(url.query)
                try:
                    since = int(query.get("since", ["0"])[0])
                    chat_since = int(query.get("chat_since", ["0"])[0])
                except ValueError:
                    since = chat_since = 0
                # borné : un since absurde ne doit pas produire d'index géant
                since = max(0, min(since, 10 ** 9))
                chat_since = max(0, min(chat_since, 10 ** 9))
                return self._json(200, app.state(since, chat_since))
            if url.path == "/api/auth":
                return self._json(200, app.auth_status())
            if url.path == "/api/usage":
                return self._json(200, app.usage_status())
            if url.path == "/api/github_repos":
                query = parse_qs(url.query)
                return self._json(*app.github_repos(force=query.get("force", [""])[0] == "1"))
            if url.path == "/api/workspace":
                query = parse_qs(url.query)
                return self._json(*app.workspace(query.get("dir", [""])[0]))
            if url.path in STATIC:
                name, ctype = STATIC[url.path]
                body = (WEB_DIR / name).read_text(encoding="utf-8").replace("{{TOKEN}}", token)
                return self._send(200, body.encode("utf-8"), ctype)
            return self._json(404, {"error": "introuvable"})

        def _post(self) -> None:
            path = urlparse(self.path).path
            if not self._allowed(True):
                return self._json(403, {"error": "accès refusé"})
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json(415, {"error": "JSON attendu"})
            raw = self.headers.get("Content-Length", "0").strip()
            if not raw.isdigit():  # exclut aussi les valeurs négatives (lecture jusqu'à EOF)
                return self._json(400, {"error": "Content-Length invalide"})
            length = int(raw)
            if length > MAX_BODY:
                # On vide ce qui a déjà été envoyé avant de répondre : sinon le
                # serveur ferme pendant que le client écrit encore → RST côté client.
                try:
                    self.rfile.read(min(length, MAX_BODY + 1))
                except OSError:
                    pass
                return self._json(413, {"error": "requête trop grosse"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json(400, {"error": "JSON invalide"})
            if not isinstance(body, dict):
                return self._json(400, {"error": "objet JSON attendu"})
            if len(json.dumps(body)) > MAX_BODY:  # garde anti-bomb (clés dupliquées, imbrication)
                return self._json(413, {"error": "requête trop grosse"})
            if path == "/api/action":
                return self._json(*app.action(body))
            if path == "/api/setting":
                return self._json(*app.setting(body))
            if path == "/api/verify_approve":
                return self._json(*app.verify_approve(body))
            if path == "/api/custom_agent":
                return self._json(*app.custom_agent(body))
            if path == "/api/auth_switch":
                return self._json(*app.auth_switch(body))
            if path == "/api/auth_switch_cancel":
                return self._json(*app.auth_switch_cancel())
            if path == "/api/auth_logout":
                return self._json(*app.auth_logout())
            if path == "/api/chat":
                return self._json(*app.chat(body))
            if path == "/api/workspace_pick":
                return self._json(200, app.ctl.pick_workspace())
            if path == "/api/workspace_read":
                return self._json(*app.workspace_read(body))
            if path == "/api/free_models_scan":
                return self._json(200, {"ok": app.ctl.scan_free_models(),
                                        "free_models": app.ctl.free_models.view()})
            return self._json(404, {"error": "introuvable"})
    return Handler


def watch_parent(pid: int, on_gone: Callable[[], None],
                 interval: float = PARENT_WATCH_S) -> threading.Event:
    """Appelle `on_gone` quand le process `pid` n'existe plus (le logiciel hôte a planté :
    sans ça, le serveur et le worker en cours resteraient en orphelins). Renvoie un Event
    d'arrêt de la surveillance."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                on_gone()
                return
            except PermissionError:
                continue  # existe mais appartient à un autre utilisateur : vivant

    threading.Thread(target=loop, name="parent-watch", daemon=True).start()
    return stop


class LocalServer(ThreadingHTTPServer):
    """Serveur de l'interface, lié à 127.0.0.1 seulement.

    `HTTPServer.server_bind` appelle `socket.getfqdn()`, une résolution DNS INVERSE : 35 s mesurées
    sur un poste à DNS lent (runner CI macOS), pendant lesquelles l'interface n'apparaît pas. Le
    nom du serveur n'est jamais lu ici : l'adresse littérale suffit."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), port


def serve(root: Path, dry_run: bool, open_browser: bool = True, port: int = 0,
          on_ready: Optional[Callable[[str, WebApp], None]] = None,
          parent_pid: Optional[int] = None) -> int:
    ctl = GuiController(root, dry_run)
    app = WebApp(ctl)
    token = secrets.token_urlsafe(24)
    handshake = app.handshake
    handshake["nonce"] = secrets.token_urlsafe(16)
    handshake["at"] = time.monotonic()
    handshake["used"] = False
    handshake_lock = app.handshake_lock
    httpd = LocalServer(("127.0.0.1", port), make_handler(app, token,
                                                                  handshake, handshake_lock))
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?h={handshake['nonce']}"
    stop = lambda *_a: threading.Thread(target=httpd.shutdown, daemon=True).start()  # noqa: E731
    app.on_quit = stop
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGHUP):  # Terminal fermé
            signal.signal(sig, stop)
    ctl.log(f"{TEAM_NAME} prêt. {'MODE DÉMO (dry-run)' if dry_run else 'Mode réel'}.")
    ctl.scan_free_models()  # à chaque ouverture : la liste des models gratuits est fraîche
    print(f"Interface prête :\n  {url}\n"
          + ("Garde ce Terminal ouvert. Arrêt : bouton « Quitter » ou Ctrl-C."
             if open_browser else "Logiciel 9AM9AM-TEAM : quitte le logiciel ou clique sur « Quitter »."), flush=True)
    if parent_pid is not None:
        watch_parent(parent_pid, stop)
    if open_browser:
        webbrowser.open(url)
    if on_ready is not None:
        on_ready(url, app)
    try:
        httpd.serve_forever(poll_interval=SERVE_POLL_S)
    except KeyboardInterrupt:
        pass
    finally:
        if not ctl.close(timeout=CLOSE_WAIT_S):
            print("La tâche en cours ne s'est pas arrêtée à temps : elle sera reprise au prochain lancement.")
        httpd.server_close()
    print("Interface arrêtée.")
    return 0
