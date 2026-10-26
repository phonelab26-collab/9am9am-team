# Plan de test

## Objectif

Vérifier que l'orchestrateur 9AM9AM-TEAM découpe correctement un objectif en
tâches, exécute ces tâches via les workers disponibles et tient l'état à jour
dans `.mini-orch/`.

## Périmètre

- Menu interactif et interface web locale (`python3 mini_orchestrateur.py gui`).
- Commandes de ligne de commande : `plan`, `run`, `tick`, `status`, `retry`, `selftest`.
- Cycle de vie des tâches : `todo`, `in_progress`, `done`, `blocked`,
  `failed`, avec 3 tentatives maximum et bouton « Réessayer bloquées ».
- Réglages agents sauvegardés dans `.mini-orch/config.json` (model opencode,
  commandes autoclaw/cline).
- Repli automatique de model lorsque le model courant échoue.

Hors périmètre : installation des CLI tiers, performances sous charge.

## Prérequis

- Python 3.9+ ; l'interface est une page web locale servie par la bibliothèque
  standard (aucun `tkinter` requis ; utiliser le lanceur `9AM9AM-TEAM.command`).
- CLI `claude` pour le CEO, CLI `opencode` pour le worker opencode
  (commandes surchargeables par variables d'environnement si absentes).
- Les workers autoclaw et cline sont optionnels.
- Sauvegarder l'état existant de `.mini-orch/` avant tout test destructif.

## Étapes

1. Lancer `selftest` : la suite de tests s'exécute avec de faux CLI, sans appel réel.
2. Lancer `status` et noter l'état initial des tâches.
3. Lancer `plan "<objectif>"` et contrôler que le CEO produit 1 à 6 tâches
   (sans tâche de vérification séparée : le CEO vérifie chaque tâche).
4. Lancer `status` : les tâches créées sont en attente.
5. Lancer `tick "<objectif>"` : une tâche passe à `in_progress` puis change
   d'état selon le résultat du worker.
6. Lancer `run` une fois et vérifier que seule la tâche visée est traitée.
7. Forcer un échec (worker indisponible) puis vérifier le passage en `blocked`
   ou `failed` et le compteur de tentatives.
8. Utiliser « Réessayer bloquées » (ou la commande `retry`) pour remettre
   les tâches en file.
9. Ouvrir l'interface (double-clic, ou `python3 mini_orchestrateur.py gui`), saisir
   un objectif, exécuter « Plan CEO » puis « Tick complet », et vérifier l'affichage.
10. Modifier le model opencode dans l'interface, relancer une tâche, puis
    vérifier que `.mini-orch/config.json` contient la nouvelle valeur.

## Critères de succès

- `selftest` se termine sans erreur et sans appel de CLI.
- `plan` génère entre 1 et 6 tâches pour un objectif donné.
- `status` reflète à chaque étape l'état réel des tâches.
- Une tâche échouée passe en `blocked`/`failed` après 3 tentatives, puis peut
  être remise en file.
- Le model mort est automatiquement remplacé par le model suivant de la liste.
- Les réglages modifiés dans l'interface sont persistés dans `.mini-orch/`.
- Aucune donnée perdue : `tasks.json` reste lisible après chaque étape.

## Test réel (sans `--dry-run`)

Mission test : « Créer le fichier `TEST-RESULT.md` à la racine du projet, avec un
titre de niveau 1 et les sections `Objectif`, `Exécution`, `Verdict`. »

1. Sauvegarder `.mini-orch/tasks.json` et noter `ls` de la racine (fichiers
   présents avant test, pour prouver qu'aucun n'est écrasé).
2. Lancer `plan "<mission>"` : le CEO (claude) reçoit la mission et renvoie une
   liste JSON de tâches ; l'écran affiche `Plan CEO : N tâches ajoutées`.
3. Lancer `status` : les tâches créées sont en `todo`, dont une tâche de création
   de `TEST-RESULT.md` assignée au worker `opencode`.
4. Lancer `run --worker opencode` : la tâche passe `in_progress` puis, si le
   worker sort avec le code 0, `done` ; son `result` est renseigné.
5. Relancer `run --worker opencode` jusqu'à exécution de la tâche qui crée le
   fichier (ou utiliser `tick "<mission>"` pour enchaîner plan + 1 tâche).
6. Contrôler la création du fichier avec les critères ci-dessous.
7. Lancer `status` et relire `.mini-orch/tasks.json` : la tâche de création est
   `done`, `attempts ≥ 1`, `result` non vide.
8. Restaurer la sauvegarde de `.mini-orch/tasks.json`.

## Critères de succès du test réel

- **Fichier présent** : `test -f TEST-RESULT.md` renvoie vrai.
- **Non vide** : `TEST-RESULT.md` pèse plus de 0 octet.
- **Sections attendues** : présence d'un titre `# ` et des trois intertitres
  `Objectif`, `Exécution`, `Verdict`.
- **Encodage UTF-8** : `iconv -f utf-8 -t utf-8 TEST-RESULT.md` se termine sans
  erreur ; aucun BOM en tête.
- **État cohérent** : tâche `done`, `attempts ≥ 1`, `result` non vide dans
  `tasks.json`, et `tasks.json` reste un JSON lisible.
- **Rien d'écrasé** : les fichiers listés à l'étape 1 sont toujours identiques.
- **Supervision** : avec `tick`, la tâche est `done` avec `verdict: ok` seulement si
  le CEO a vérifié le résultat ; `run` marque `verdict: non_supervise` (contenu à
  vérifier soi-même).
