# 9AM9AM-TEAM

Mini-organisation d'agents IA : un **CEO** (Claude Code, `claude -p`) découpe un objectif
en tâches et vérifie chaque résultat ; des **workers** (opencode, autoclaw, cline) les
exécutent. Inspiré du heartbeat de Paperclip (prise de tâche → contexte → travail →
statut), sans serveur : l'état vit dans `.mini-orch/`. Python stdlib uniquement.

## Démarrage rapide

```bash
git clone https://github.com/phonelab26-collab/9am9am-team.git
cd 9am9am-team
./9AM9AM-TEAM.command selftest   # vérifie l'installation, aucun vrai CLI appelé
```

- **Double-clic sur `9AM9AM-TEAM.command`** → interface web locale (recommandé).
- Menu Terminal : `./9AM9AM-TEAM.command menu`.
- Vérifier l'installation : `./9AM9AM-TEAM.command selftest` (aucun vrai CLI appelé).

## L'interface (navigateur, ou logiciel `9AM9AM-TEAM.app`)

1. Écris un **Objectif** (ex. « préparer la démo de vendredi »).
2. **3. Tick complet** : planifie l'objectif s'il est nouveau, exécute sa tâche suivante,
   puis le CEO vérifie le résultat. Reclique pour avancer. (**1. Plan CEO** planifie seul ;
   **2. Run tâche suivante** exécute sans supervision.)
3. **Arrêter** interrompt la tâche en cours (le worker et tous ses sous-process sont
   arrêtés ; la tâche repasse en file sans perdre de tentative). Pendant que le CEO vérifie
   un résultat, c'est pareil : **rien n'est validé**, la tâche reprendra.
4. **Réessayer bloquées** remet les tâches `blocked`/`failed` en file (compteur à 0).
5. Les réglages **Agents** sont sauvegardés 0,5 s après la dernière frappe.
   **Model qui travaille (opencode)** : à chaque ouverture, le catalogue `opencode models`
   est scanné en arrière-plan et ses models gratuits remplissent la liste (gratuits d'après
   leur nom : `-free`, `:free`, `/free/`, fournisseur `…free`, ou connus comme `big-pickle` —
   opencode ne donne pas les prix). « ↻ Rescanner » relit le catalogue ; « Autre… » accepte
   n'importe quel `provider/model`. En mode démo, aucun scan.
6. La liste des tâches se met à jour seule, y compris si tu lances des commandes dans le
   Terminal en même temps. Fermer l'onglet (ou le logiciel) pendant une tâche l'arrête proprement.

### Dossier de travail (« 📂 Parcourir… »)

Au-dessus du message, **📁 nom** indique le dossier sur lequel travaillent le CEO et les
agents ; **📂 Parcourir…** ouvre le sélecteur de dossiers du Mac pour en choisir un autre
(retenu au prochain lancement). **+** joint des fichiers au message.
- Tâches, conversation et journal sont **propres à chaque dossier** (`<dossier>/.mini-orch/`) ;
  les **réglages** (models, efforts, agents) restent **globaux**, rangés avec le logiciel.
- Refusé pendant un travail en cours, et pour `/`, ton dossier personnel entier ou un
  dossier système. Un dossier retenu qui a disparu → retour au dossier du logiciel (signalé).
- La ligne de commande travaille par défaut dans le dossier du logiciel ; `--dir DOSSIER`
  (ou `--workspace`) choisit un autre dossier, avec les mêmes garde-fous que « 📂 Parcourir… »
  (voir « Ligne de commande »).

**Ouvrir un dossier qui ne vient pas de toi** (projet téléchargé, cloné, reçu) :
- Un `.mini-orch/` qui contient un **lien symbolique, un lien physique ou un fichier spécial**
  (FIFO…) — lui-même ou un fichier dedans — est **refusé** à l'ouverture ; et tout fichier
  d'état est ouvert **sans jamais suivre de lien**, en refusant tout ce qui n'est pas un
  fichier normal à un seul nom (`journal.log` → `~/.zshrc` n'écrit rien dans ton shell ; un
  FIFO ne gèle pas l'application). Les titres de tâches sont ramenés sur une ligne.
- Un `.mini-orch/` que **ce programme n'a pas créé** (venu avec le projet, ou laissé par une
  version antérieure) est **signalé** dans le journal avec le nombre de tâches en attente, à
  chaque ouverture tant qu'elles ne sont pas terminées : elles ne partent qu'à ton
  « Tick »/« Run », relis-les d'abord.
- **Aucun processus n'est arrêté sur la foi du projet** : la reprise après plantage n'arrête
  un worker orphelin que s'il est inscrit dans `trust.json`, le registre tenu **à côté des
  réglages** (hors du projet) au moment où ce programme l'a lancé.
- La commande de vérification **détectée** ne tourne qu'avec **ton accord** (voir
  « Vérification automatique »).
- `claude auth status`, `claude auth logout`, `claude auth login` et `claude -p /usage` tournent depuis ton
  dossier personnel, jamais dans le dossier ouvert (son `.claude/` — hooks… — n'est pas lu).
  Le CEO et les agents, eux, travaillent **dans** le dossier : c'est leur rôle.

## Ligne de commande

| Commande | Effet |
|---|---|
| `python3 mini_orchestrateur.py` | menu interactif |
| `… plan "objectif"` | le CEO découpe l'objectif en 1 à 6 tâches |
| `… tick "objectif"` | reprend les `blocked` de l'objectif, le planifie s'il est nouveau, exécute sa tâche suivante, supervision CEO |
| `… tick ""` | exécute la prochaine tâche prête, tous objectifs confondus |
| `… run [--worker opencode\|autoclaw\|cline] [--model provider/model]` | exécute 1 tâche, sans supervision |
| `… retry` | remet en file les tâches `blocked`/`failed` |
| `… status [--json]` | état des tâches (**lecture seule**) ; `--json` : JSON seul sur la sortie standard |
| `… selftest` | suite de tests (≈ 870 tests, ~2 min, aucun vrai CLI appelé) |
| `… --dry-run tick "objectif"` | boucle complète sans appeler les CLIs |
| `… --version` | version = commit git du dossier du logiciel (« inconnue » si ce n'est pas un dépôt, jamais un numéro inventé) |
| `… --dir ~/projets/site tick "objectif"` | travaille dans CE dossier ; les réglages restent globaux |

L'aide est en français : `… --help`, et `… COMMANDE --help` (avec des exemples). Les options
globales `--dry-run` et `--dir` se mettent **avant ou après** la commande.

- **`--dir`** : relatif au dossier courant, refusé (code `2`) s'il est introuvable, trop large
  (`/`, ton dossier personnel), système, ou si son `.mini-orch` contient un lien. Sans effet
  sur `gui` et `selftest` (refusé, code `2`) : l'interface choisit son dossier elle-même.
- **`status --json`** : un objet `{team, workers, model, problem, counts, tasks[]}` sur la
  sortie standard, **rien d'autre** ; le journal et les avertissements (ex. `.mini-orch` venu
  d'ailleurs) vont sur la sortie d'erreur. Une erreur est aussi du JSON : `{"ok": false, "error": …}`.
  Aucun chemin absolu.

Codes retour : `0` = réussi (tâche faite et validée par le CEO, ou rien à faire) ;
`1` = échec, refus du CEO, CEO indisponible, rien d'exécutable, ou état illisible (y compris
`status` quand `tasks.json` est inutilisable ; le message dit pourquoi) ;
`2` = usage incorrect (commande ou option inconnue, argument manquant, `--dir` refusé) ;
`130` = Ctrl-C.

## Comment une tâche est exécutée

1. **Prise** — la tâche passe `in_progress` sous verrou, avec un propriétaire (pid, run)
   et un *bail* : un fichier verrouillé tant que le process vit. Deux interfaces/Terminaux
   ne peuvent jamais prendre la même tâche.
2. **Contexte** — le worker reçoit l'objectif, les résultats déjà terminés **de son plan**,
   le retour du CEO s'il a refusé l'essai précédent, et la liste des tâches suivantes à
   **ne pas** faire. Tout contenu recopié est encadré : c'est de la donnée, pas un ordre.
3. **Travail** — le worker s'exécute dans le dossier du projet, stdin fermé, avec délai.
4. **Supervision** (tick) — la tâche reste `in_progress` pendant la vérification ; le CEO
   répond `VERDICT: ok` ou `VERDICT: ko`. `ok` → `done` validé ; `ko` → `blocked` avec son
   retour (ou `failed` après 3 exécutions) ; réponse absente/ambiguë ou CEO indisponible →
   `done` **non supervisé** (affiché comme tel) et code retour 1 — en conversation aussi, et
   le CEO rédige alors quand même son rapport final pour le dire. « Arrêter » ou Ctrl-C pendant
   cette vérification → la tâche repasse en file (`blocked`, interrompue), jamais validée.
5. **Dépendances** — une tâche n'est lancée que si les précédentes de son plan sont `done`.
6. **Renfort Claude Code** — **seulement quand le CEO refuse la qualité du travail**, la tâche
   monte d'un palier à chaque reprise : worker prévu → **Claude Code · Sonnet · élevé**
   → **Claude Code · Opus · max**. Un échec d'exécution (délai dépassé, model gratuit en panne,
   erreur) n'est **jamais** escaladé : nouvel essai sur le même worker, qui change de model
   gratuit. Plafond : 2 tâches reprises par Claude par demande ; réglage « Reprise par Claude
   Code » dans les Paramètres pour la couper : **désactivée**, Claude Code n'est plus proposé
   au CEO et une tâche qui lui était confiée n'est pas lancée (c'est dit, sans dépense). Le message dit la vraie cause de chaque reprise.
   Le palier ne redescend jamais ; chaque palier consomme une des 3 exécutions, donc au pire 3 essais puis `failed` et rapport honnête du CEO — aucune
   boucle. Une interruption (« Arrêter », process tué) n'escalade pas ; « Réessayer » garde le
   palier atteint. Claude Code travaille en `acceptEdits` (jamais de contournement des
   permissions). Le CEO peut aussi confier d'emblée une tâche dure au worker `claude` (si la
   reprise par Claude Code est autorisée).
7. **Commandes d'agents** (cline, autoclaw, agents ajoutés) — vérifiées à l'enregistrement
   (`sh -n` : analysée, jamais exécutée) ; une commande qui n'est pas du shell valide est
   refusée, et un agent déjà réglé avec une commande cassée n'est plus proposé au CEO (dit
   dans le journal, avec l'erreur).

### Changer de compte Claude Code (Paramètres → « Connexion Claude Code »)

Écris l'email du compte voulu, puis **Changer de compte** : l'app déconnecte le compte actuel
(`claude auth logout`) puis lance `claude auth login --email …` ; **le navigateur s'ouvre** et c'est là
que tu valides (5 min au plus). L'app ne voit jamais de mot de passe ni de jeton.
- Refusé pendant un travail (le CEO changerait de compte à mi-vol), en mode démo, et si l'adresse n'est
  pas une adresse (jamais une option comme `--sso`). Pendant ce temps, **Se déconnecter** est refusé aussi.
- **Annuler** arrête la connexion ; le compte précédent reste alors déconnecté (dit dans l'interface).
- À la fin, le compte réellement connecté est relu et affiché ; si le navigateur a validé un autre
  compte que celui demandé, c'est signalé. L'adresse n'est jamais écrite dans le journal.
- Une déconnexion qui échoue arrête tout, sauf si le compte était déjà déconnecté. Si la connexion échoue
  ensuite, l'interface dit que le compte précédent est déconnecté (plus aucun compte actif) et la raison
  est au journal, secrets masqués et sans l'adresse.
- Pas de navigateur qui s'ouvre ? **Copier la commande** et colle-la dans le Terminal.
- Une clé `ANTHROPIC_API_KEY` ou `CLAUDE_CODE_OAUTH_TOKEN` définie dans l'environnement garde la
  priorité sur le compte connecté : dans ce cas, changer de compte n'a pas d'effet sur le CEO.

Dans `status` : `— validé par le CEO`, `— non supervisé`, `— refusé par le CEO`,
`— en cours (work|review, pid …)`.

## Mode Local / Cloud du CEO (bouton + dépôts GitHub)

Dans **Paramètres**, deux boutons : **💻 Local** (défaut) et **☁️ Cloud**.

- **Local** : `claude -p …` tourne sur ton Mac (inchangé).
- **Cloud** : `claude --cloud <session> -p …` envoie le prompt à une session distante
  `claude.ai` **existante** (avec `-p`, le CLI refuse de créer une session : colle son
  id `session_...`/`cse_...` ou son URL `claude.ai/code/...`). Sans session valide →
  repli automatique sur Local, **dit dans la conversation** ; la page affiche le mode
  *effectif* (« 💻 local »), pas le réglage.
- ⚠️ En mode Cloud, la restriction « lecture seule » du CEO n'est **pas** appliquée (la
  session distante agit avec ses propres droits) ; en local, le CEO ne peut que lire.
- En mode Cloud, la section **« Dépôts GitHub associés à ton Claude »** liste tes
  dépôts (tous les comptes `gh` connectés : ton compte personnel, tes organisations…),
  avec filtre, actualisation et clic pour choisir le dépôt visé. Le CEO reçoit alors
  ce dépôt en tête de ses prompts (plan, conversation) ; sans dépôt, il te le demande.
- Source : GitHub CLI (`gh auth status` → `gh auth token` → `gh repo list`), en lecture
  seule, **jamais de token journalisé ni renvoyé à la page** (le token n'existe que dans
  l'environnement du sous-process). Résultat caché 5 min ; bouton « ↻ Actualiser ».
- Sans `gh` ou sans connexion : message explicite (`gh auth login`), aucune erreur
  silencieuse. Les vraies causes sont dites (panne de `gh`, délai, réponse illisible ≠
  « pas connecté »). Une panne n'est pas mise en cache (le clic suivant retente) ; si un
  compte échoue et les autres répondent, la liste s'affiche avec un ⚠️. Le compte actif
  n'a besoin d'aucun jeton ; seuls les autres comptes en chargent un.

Réglages persistés dans `.mini-orch/config.json` : `ceo_mode`, `ceo_cloud_session`,
`ceo_cloud_repo`.

## Robustesse

- **Plantage, `kill -9`, coupure** : au prochain lancement, une tâche dont le process est
  mort est reprise (`blocked`, ou `done` non supervisé si seule la vérification manquait)
  et son worker orphelin est arrêté — seulement s'il a été lancé par ce programme (registre
  `trust.json` hors du projet) et après vérification que c'est bien lui.
- **Pièces jointes** : le texte (4000 caractères) et les fichiers (~25 000) ont chacun leur
  plafond ; ce qui ne tient pas est écarté fichier entier et dit dans la conversation (jamais
  une clôture coupée). La bulle montre « 📎 noms », pas le bloc brut.
- **Écritures** atomiques et vidées sur disque ; un fichier illisible est mis de côté en
  `*.json.corrupt-<date>` (jamais écrasé) ; un `tasks.json` incohérent (id en double…) est
  signalé et **rien n'est écrit** par-dessus.
- **Journal** : tout ce qui s'affiche est aussi dans `.mini-orch/journal.log`.
- **Un `kill -9` rend l'essai** de la tâche (comme l'annulation) : trois plantages ne
  l'épuisent pas. Un `tasks.json` corrompu est dit dans la conversation (« Rien à faire »
  ne s'affiche plus à tort, code de sortie 1).
- **`config.json` illisible** : mis de côté, réglages par défaut, mais la reprise payante
  par Claude Code est **désactivée par prudence** (réactive-la dans Paramètres) ; c'est dit
  dans la conversation. Un réglage invalide (effort du CEO…) est signalé, pas ignoré en douce.
- **Fermeture** : le scan des models est attendu (3 s max) ; dans le Terminal, SIGTERM/SIGHUP
  se comportent comme Ctrl-C (agents arrêtés, tâche remise en file). Un dossier de travail
  supprimé pendant un run est dit (« dossier de travail introuvable ») et n'est pas recréé.
- **Sortie d'opencode vide** (code 0 sans texte ni outil) = échec explicite, pas un succès.

## Consommation Claude : seulement pour orchestrer

- **Les agents gratuits ne coûtent aucun token Claude** : pendant qu'ils travaillent, le CEO
  n'est pas appelé.
- **Le CEO est appelé en mode sobre** : sans MCP, sans skills, sans tes réglages perso (hooks,
  CLAUDE.md global), aucun outil pour planifier/discuter/rédiger, lecture seule pour vérifier.
  Mesuré : ~4 k tokens par tour de conversation et ~11 k par vérification, contre ~40–60 k
  avant. Le CLAUDE.md du *projet* reste lu. (Mode cloud : appel inchangé.)
- **Bilan à la fin de chaque demande** dans la conversation : nombre d'appels (CEO, reprises
  par Claude Code) et tokens réellement traités, lus dans la réponse du CLI — « non mesuré »
  si le CLI ne le dit pas, jamais un chiffre inventé.
- Pour économiser encore : CEO en **Sonnet, effort faible/normal** (Paramètres).

## Vérification automatique (tests avant l'audit de Claude)

Précisions (lot 3) : « aucun fichier modifié » ne regarde que les dossiers ignorés **à la
racine** (`build/`, `node_modules/`…) : `pkg/build/gen.py` compte. `pytest` absent (code 1) ou
aucun test collecté (code 5) = **commande cassée** (« vérification impossible »), l'agent n'est
pas relancé 3 fois pour rien. Quand les tests n'ont pas tourné, le CEO le lit dans son audit.

La commande est **détectée automatiquement** : cible `make` `check-fast` > `check` > `test`,
sinon `Package.swift` (`swift test`), script `npm test`, pytest/unittest, `cargo test`,
`go test ./...`. Un projet Xcode seul n'est pas deviné (schéma inconnu).
**Elle exécute le code du projet** (Makefile, `conftest.py`, `build.rs`…) : elle **ne tourne
qu'après ton accord**, une fois par dossier — Paramètres → « Vérification de CE dossier » →
**Autoriser « … » pour ce dossier**. L'accord est lié à l'empreinte des fichiers que la
commande lit avant les tests, absents compris : `GNUmakefile`/`makefile`/`Makefile`,
`package.json` + `.npmrc`, `Package.swift`, `pytest.ini`/`conftest.py`/`pyproject.toml`/
`tox.ini`/`setup.cfg`/`tests/conftest.py`, `Cargo.toml` + `build.rs` + `.cargo/config*` +
`rust-toolchain*`, `go.mod`. Si l'un change (ou apparaît), même pendant une tâche, la
vérification n'est plus lancée et l'interface te redemande. L'accord ne couvre pas le code
des tests lui-même, que les agents modifient : il vaut pour ce dossier. Sans accord, rien n'est
lancé et le journal le dit. Tu peux aussi la remplacer par ta propre commande (tapée = déjà
autorisée), ou écrire `off` pour couper (un champ vide = retour à la détection).
Une par dossier de travail. Après chaque travail d'un agent qui a **modifié des fichiers**,
elle tourne dans le dossier :
- **rouge** → la tâche repart sur l'agent gratuit avec la fin de la sortie d'erreur,
  **sans appel à Claude** et **sans escalade** (un test rouge n'est pas un refus du CEO) ;
- **vert** → le CEO audite en voyant ce résultat.
Aucun fichier modifié (simple audit, question) → pas de vérification. Délai : 15 min ;
« Arrêter » la coupe. La commande ne vient que des Paramètres (une ligne, jamais de secret).
Le bilan de fin de demande compte les vérifications Claude évitées.
- Après un test rouge, l'essai suivant est **toujours** revérifié, même sans fichier modifié.
- Commande introuvable / non exécutable / délai dépassé → la tâche échoue tout de suite avec
  « vérification impossible : corrige la commande dans les Paramètres » (pas d'essais gaspillés).
- Appli tuée pendant les tests → tâche reprise, jamais validée en douce.
- ⚠️ Les réglages (dont cette commande) sont dans `.mini-orch/config.json` du **dossier du
  logiciel**. Si tu fais travailler les agents *dans* ce dossier, l'un d'eux pourrait modifier
  ce fichier : choisis plutôt le dossier de ton projet avec « 📂 Parcourir… ».

## Effort de chaque agent (Paramètres)

Liste `défaut / faible / normal / élevé / max / ultracode` pour le CEO, autoclaw, cline et
chaque agent ajouté ; l'effort choisi s'affiche dans la carte « Équipe ».
- **CEO** : `claude --effort low|medium|high|max|xhigh` (ultracode = `xhigh`).
- **autoclaw, cline, agents ajoutés** : la valeur arrive dans `$ORCH_EFFORT`
  (`low|medium|high|xhigh|max`) ; ta commande doit la lire, par ex. `mon-cli --effort "$ORCH_EFFORT"`.
- **opencode** : l'effort est la *variante* du model (`provider/model#variant`), dont les noms
  dépendent du model (`low`, `high`, `max`…) — champ libre avec suggestions.
- **Renfort Claude Code** : paliers fixes (Sonnet · élevé puis Opus · max), voir plus haut.

## Models opencode

Le repli utilise les gratuits **réellement scannés** (`opencode models`, rescannés à chaque
ouverture) puis tes models ajoutés, toujours filtrés par le catalogue ; `FREE_MODELS` (dans
`orch/config.py`) n'est qu'une graine de dernier recours. Le repli **n'écrase jamais** ton
choix : le model réellement utilisé est affiché sur la carte de la tâche. Un model en panne
(délai dépassé, événement d'erreur, connexion) est mis de côté 6 h et le suivant prend le
relais — **sauf** si le worker avait déjà commencé à modifier des fichiers (pas de travail
en double : la tâche passe `blocked`, relance-la). Budget total : 20 min par tâche.

## Prérequis

- **CEO** : `claude` — sinon `MINI_ORCH_CLAUDE=/chemin/claude`.
- **Worker opencode** : `opencode` — sinon `MINI_ORCH_OPENCODE=/chemin/opencode`.
- **autoclaw / cline** (optionnels) : leur commande dans l'interface, ou
  `MINI_ORCH_AUTOCLAW_CMD` / `MINI_ORCH_CLINE_CMD`.
- **Python 3.9+** — le lanceur choisit `/usr/bin/python3` (python système du Mac, comme le
  logiciel `9AM9AM-TEAM.app`) puis se replie sur le `python3` du PATH. Aucun Tk requis :
  l'interface est une page web locale (127.0.0.1, port aléatoire, jeton d'accès).

- **Mac Apple Silicon ou Intel** — `./app/build.sh` fabrique un binaire universel
  (arm64 + x86_64) : le même dossier marche sur les deux. Après avoir copié le projet d'un
  Mac à l'autre, relance `./app/build.sh` (le `.app` est un artefact, il n'est pas versionné).

## Compatibilité et limites

- **macOS uniquement.** Conçu et testé sur Mac (Apple Silicon et Intel) : le lanceur
  `.command`, le logiciel `.app` (WebKit), le sélecteur de dossiers (`osascript`) et
  `F_FULLFSYNC` sont propres au Mac. Linux et Windows ne sont **ni testés ni supportés**
  (Windows n'a ni `fcntl` ni `os.killpg`, dont le programme dépend).
- **Outils externes non fournis** : Claude Code (`claude`, **abonnement payant**) pour le CEO,
  opencode pour les agents gratuits. Les models « gratuits » dépendent du catalogue d'opencode et
  peuvent changer ou disparaître sans préavis ; la liste de secours intégrée peut être périmée
  (le scan à chaque ouverture la remplace).
- **Projet jeune** : un seul auteur à ce jour, surtout éprouvé sur une machine. Les tests
  (≈ 870, sans aucun vrai CLI) ne prouvent pas que `claude` ou `opencode` se comportent comme
  prévu après une mise à jour de leur côté.
- **Le logiciel `.app` n'est pas fourni ni notarisé** : `./app/build.sh` (Xcode requis) le
  fabrique chez toi et ne le signe que localement (« ad hoc ») ; macOS peut demander une
  confirmation à la première ouverture.
- **Langue** : interface, messages et documentation en français uniquement.

## Sécurité : à lire avant d'ouvrir un dossier

- Le CEO et les agents travaillent **dans** le dossier choisi et **y écrivent des fichiers** :
  prends un dossier de projet dédié (`/` et ton dossier personnel entier sont refusés).
- Un dossier qui ne vient pas de toi peut être piégé. Les garde-fous (liens refusés, vérification
  soumise à ton accord, aucun processus arrêté sur la foi du projet) sont décrits dans « Ouvrir un
  dossier qui ne vient pas de toi ». **Ils ne couvrent pas** ce que le CEO et les agents lisent dans
  le dossier (`CLAUDE.md`, `.claude/`, configuration d'opencode…) : relis-les avant de lancer un travail.
- Le programme lui-même n'ouvre **aucune connexion sortante** (Python seul : uniquement
  `127.0.0.1`, pour l'interface, protégée par un jeton d'accès, et le hub local de Cline). Le réseau
  est utilisé par les CLI qu'il **lance** — `claude`, `opencode`, et `gh` (liste de dépôts, optionnel) —
  avec leurs propres comptes et règles.

## Licence

MIT — voir [`LICENSE`](LICENSE).

## Dépannage

| Symptôme | Piste |
|---|---|
| Le logiciel ne s'ouvre pas (« Mauvais type de CPU ») | `.app` compilé pour l'autre architecture : relance `./app/build.sh` |
| L'interface ne s'ouvre pas | port occupé ou navigateur bloqué → relance, ou `./9AM9AM-TEAM.command menu` |
| `Aucun worker installé` | installe opencode ou renseigne une commande dans l'interface |
| `Rien d'exécutable : [n] attend [m]` | une tâche précédente du plan n'est pas terminée ; `retry` si elle est `blocked`/`failed` |
| `Objectif déjà planifié` | change le texte de l'objectif pour en démarrer un nouveau |
| `tasks.json incohérent` | corrige le fichier à la main (message précis) ; une sauvegarde est conseillée |
| `état .mini-orch verrouillé` | une autre interface/Terminal écrit ; réessaie |
| `Vérification impossible` (pytest absent, aucun test) | la commande est cassée, pas ton code : corrige-la dans Paramètres. Les tests non lancés sont signalés au CEO |
| Tâche `done — non supervisé` | le CEO n'a pas pu vérifier : relis le résultat toi-même |

## Fichiers

- `mini_orchestrateur.py` — point d'entrée ; le code est dans `orch/` :
  `tasks.py` (règles), `store.py` (état, verrous, reprise), `procs.py` (sous-process),
  `workers.py`, `ceo.py`, `prompts.py`, `engine.py`, `cli.py`, `chat.py` (conversation),
  `webui.py` (page web locale + API JSON), `gui_controller.py` (travail en arrière-plan),
  `cli_parser.py` (aide et erreurs en français), `version.py` (`--version`).
- `orch/web/` — la page (HTML/CSS/JS, aucune dépendance externe).
- `app/` — logiciel Mac (`main.swift` + `build.sh` → `9AM9AM-TEAM.app`, WebKit) : il lance
  `gui --no-browser --parent <pid>` et se ferme avec le logiciel.
- `tests/` — la suite de tests (faux `claude`/`opencode` dans `tests/bin/`).
- `.mini-orch/` — `tasks.json`, `config.json`, `chat.json`, `reports.json`,
  `journal.log`, `runs/` (baux), `origine.json` (jeton : créé par ce programme).
  Dans celui du logiciel seulement : `trust.json` (dossiers d'état créés et processus lancés
  par ce programme) et les accords de vérification (`verify_approvals` de `config.json`).
- `9AM9AM-TEAM.command` — lanceur double-clic macOS.
- Tk a été retiré : ne jamais le réintroduire (`orch/gui.py` a été supprimé, un test
  l'interdit) ; côté page, `innerHTML` et consorts sont interdits par un autre test.
