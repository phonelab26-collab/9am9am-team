# 9AM9AM Bots — adéquation avec les projets d'Achraf

> Note stratégique — 2026-11-09.
> Sources : `CONTEXT.md` / `README.md` des projets dans `~/Documents`, et capacités réelles du bot.
> Statut : analyse, pas engagement. Les coûts, la légalité et les risques sont à vérifier avant tout pilote.

---

## 1. Ce que le bot sait faire (rappel)

9AM9AM Bots est un **coéquipier IA auto-hébergé**, pas une bibliothèque à importer :

- chat avec rôle, instructions, mémoire durable ;
- **ordinateur cloud** (navigateur, shell, bureau Linux, viewer noVNC) ;
- **apps connectées** via Composio (Gmail, Slack, Google, etc.) ;
- **routines** cron et événements ;
- **délégation** entre bots, sous-agents, groupes ;
- **voix** (Grok realtime) ;
- bibliothèque de livrables ;
- approbations humaines pour les actions sensibles.

Il est donc adapté quand il y a : **tâches répétitives + apps externes + navigateur/ordinateur + validation humaine**.

---

## 2. Grille de lecture

Chaque projet est noté sur cinq axes :

| Axe | Question |
|---|---|
| **Fit** | Les capacités du bot correspondent-elles au besoin ? |
| **Valeur** | Combien de temps ou d'argent le bot peut-il faire gagner ? |
| **Risque** | Données sensibles, légal, réputation, sécurité. |
| **Effort** | Combien de travail d'intégration avant un pilote ? |
| **Vitesse** | Peut-on tester en une semaine avec un résultat mesurable ? |

---

## 3. Classement synthétique

| Projet | Rôle principal du bot | Fit | Valeur | Risque | Effort |
|---|---|---|---|---|---|
| **Testeur d'app** | Prospection + exécution du bench + rapport | 5/5 | 5/5 | 2/5 | 3/5 |
| **troti.ma** | Conseiller commercial et SAV WhatsApp/Instagram | 5/5 | 4/5 | 3/5 | 4/5 |
| **COMPTEX** | Relance des pièces et suivi des échéances | 4/5 | 5/5 | 4/5 | 4/5 |
| **Kino** | Rappels de RDV et admin (pas de clinique) | 4/5 | 4/5 | 5/5 | 3/5 |
| **OBDZ / AutoDiag PC** | Support technique N1 | 4/5 | 3/5 | 2/5 | 2/5 |
| **9AM9AM-TEAM** | Front office/plateforme d'agents | 5/5 | 4/5 | 3/5 | 5/5 |
| **footbook / PitchUp** | Prospection clubs + support réservation | 4/5 | 4/5 | 2/5 | 3/5 |
| **marketplace-maroc** | Modération, fraude, support | 4/5 | 4/5 | 4/5 | 4/5 |
| **ST-induction** | Veille, contenus, support apprenant | 4/5 | 3/5 | 3/5 | 2/5 |
| **site archi zineb** | Concierge et qualification de leads | 3/5 | 3/5 | 1/5 | 2/5 |
| **jihane kine** | FAQ et demandes de RDV | 3/5 | 2/5 | 4/5 | 2/5 |
| **tifo** | Support organisateur | 3/5 | 2/5 | 1/5 | 3/5 |
| **Animalo** | Modération et support | 3/5 | 3/5 | 3/5 | 3/5 |
| **twitter desktop** | Veille et brouillons de posts | 3/5 | 3/5 | 3/5 | 3/5 |
| **motion-studio** | Montage vidéo assisté | 3/5 | 3/5 | 1/5 | 3/5 |
| **plan2dxf** | Support et QA des conversions | 3/5 | 2/5 | 1/5 | 2/5 |
| **ÉTALON** | Agent de mesure des services IA | 3/5 | 3/5 | 1/5 | 3/5 |
| **distributeur de tampon** | Étude de marché | 2/5 | 2/5 | 1/5 | 2/5 |
| **indiana** | Concierge du café | 2/5 | 1/5 | 1/5 | 2/5 |
| **ARTEFACT-AUDIT** | Audit sécurité assisté | 2/5 | 2/5 | 1/5 | 3/5 |

> Les notes sont relatives aux capacités **actuelles** du bot (prototype). Elles ne préjugent pas de la valeur des projets eux-mêmes.

---

## 4. Les cinq meilleurs matchs

### 4.1 Testeur d'app — le plus monétisable

**Projet :** `~/Documents/testeur dapp` — « Contrôle technique d'app » : on pousse une app jusqu'à la casse, on rend un verdict go/no-go et un plan de capacité. Offres déjà chiffrées : 149 € one-shot, 499 € startup, 1 500 €/mois agence.

**Ce que le bot ferait :**

- **Prospection** : trouver des indie hackers qui lancent une app (Product Hunt, X, LinkedIn, Indie Hackers), enrichir les contacts, rédiger l'approche.
- **Qualification** : vérifier l'app, le stack, les endpoints, estimer le trafic, proposer le bon test.
- **Exécution** : lancer le bench Go sur une machine cloud, capturer les métriques, screenshots et logs.
- **Rapport** : rédiger le verdict, le plan de capacité et le coût d'infra.
- **Suivi** : relance à 7 jours, témoignage, contenu.

**Pourquoi ça colle :** le bot a un ordinateur, un shell, un navigateur, des routines et des connecteurs email. Le bench est un CLI : le bot peut l'appeler, lire les résultats et rédiger le livrable. C'est un workflow long et multi-étapes, exactement ce qu'il sait faire.

**Intégration :** moyenne. Commencer en interne : le bot appelle `bench/run.sh`, lit les fichiers de résultats, écrit le rapport. Le self-serve viendra après.

**Risque :** faible, à condition de ne tester que des apps **avec autorisation** et de ne pas lancer de charge agressive sans accord.

**Premier pilote :** 20 prospects contactés, 5 réponses, 1 test payant, rapport produit en moins d'une heure.

---

### 4.2 troti.ma — le meilleur pour le commerce

**Projet :** `~/Documents/troti.ma` — vente de trottinettes et engins électriques, pièces détachées, mise en relation avec des réparateurs. Monorepo NestJS/Next.js/Expo, paiements réels reportés au post-MVP.

**Ce que le bot ferait :**

- Répondre aux questions produit : compatibilité, autonomie, pièces, prix, disponibilité.
- **Trier les demandes de réparation** : modèle, symptômes, photos, localisation, réparateur le plus proche.
- Suivre les devis et les rendez-vous.
- Relancer les paniers abandonnés, demander un avis, rappeler l'entretien.
- Être présent sur **WhatsApp Business, Instagram DM, email et chat web** — au Maroc, une grande partie du commerce se fait là.

**Pourquoi ça colle :** volume élevé de conversations répétitives, catalogue et demandes de réparation structurés, besoin de réponse 24/7. Le bot escalade vers un humain pour les engagements (prix, remise, paiement).

**Intégration :** moyenne à élevée. Exposer le catalogue, les commandes et les demandes de réparation via API, puis connecter WhatsApp/Instagram (Composio ou Twilio/WhatsApp Cloud API).

**Risque :** moyen. Données clients et paiements : le bot ne doit jamais promettre un prix ou un stock sans source, ni rembourser seul.

**Premier pilote :** 50 conversations, 20 qualifiées, 5 demandes de réparation, zéro promesse non sourcée.

---

### 4.3 COMPTEX — le meilleur B2B à forte valeur

**Projet :** `~/Documents/COMPTEX` — logiciel de gestion pour cabinet d'expertise comptable au Maroc : dossiers clients, échéances, pièces, relances, honoraires, loi 69-21. Serveur tranche 1, desktop à venir.

**Ce que le bot ferait :**

- Relancer les clients qui n'ont pas envoyé leurs pièces (email/WhatsApp), avec la liste exacte des documents manquants.
- Suivre les échéances et les délais de paiement (loi 69-21).
- Extraire les données des factures (OCR + computer use), classer, résumer.
- Préparer les rendez-vous : retrouver une pièce, résumer un dossier, répondre aux questions internes.
- Rédiger les relances et courriers répétitifs.

**Pourquoi ça colle :** courir après les pièces est un coût caché énorme dans un cabinet. Le bot peut le faire en continu, avec une trace. La valeur par client est élevée, donc le prix peut l'être aussi.

**Intégration :** moyenne à élevée. Accès contrôlé aux dossiers, journal d'audit, approbations, chiffrement, déploiement privé.

**Risque :** élevé. Données financières confidentielles : usage **interne**, jamais d'envoi automatique sans validation, conformité à vérifier.

**Premier pilote :** 10 clients, 30 pièces manquantes, 80 % collectées en 7 jours, zéro email envoyé sans approbation.

---

### 4.4 Kino — le besoin est déjà écrit

**Projet :** `~/Documents/kino` — EMR et gestion de cabinet de kinésithérapie, local-first, Next.js + SQLite, « produit rendu revendable » au 2026-10-10. Point ouvert nº2 : **rappels WhatsApp automatiques** (aujourd'hui lien manuel).

**Ce que le bot ferait :**

- Confirmer, rappeler et reprogrammer les rendez-vous par WhatsApp.
- Envoyer le formulaire d'accueil avant la séance, collecter les documents et le consentement.
- Relancer les séances non planifiées, les reçus, les mutuelles.
- Répondre aux questions **administratives** du patient (horaires, accès, documents).

**Pourquoi ça colle :** besoin réel déjà documenté, dans un produit mature. Le bot apporte une brique que Kino n'a pas.

**Intégration :** moyenne. Kino est **local-first** : le bot doit tourner sur la machine du cabinet ou dans un cloud privé, avec un accès limité aux rendez-vous.

**Risque : très élevé.** Données de santé, secret médical, RGPD. Le bot ne doit **jamais** lire les notes cliniques ni donner un avis médical. Uniquement l'admin et les rappels, avec validation humaine.

**Premier pilote :** 20 rendez-vous, rappels J-1, 0 donnée clinique lue, 0 message médical.

---

### 4.5 OBDZ / AutoDiag PC — le meilleur support technique

**Projet :** `~/Documents/OBDZ` — diagnostic OBD-II et UDS, PySide6 + python-OBD, 721 tests, simulateur honnête.

**Ce que le bot ferait :**

- Interpréter les codes défaut (DTC), expliquer les causes probables et guider les tests.
- Trier les tickets : version, véhicule, logs, captures ; reproduire avec le simulateur.
- Rédiger la FAQ, les articles et les réponses aux forums/Facebook.
- Répondre aux questions pré-vente.

**Pourquoi ça colle :** domaine technique, documentation riche, questions répétitives. Le bot peut utiliser le simulateur et le shell pour reproduire un bug, puis escalader.

**Intégration :** faible à moyenne. Pas besoin de toucher au Python : le bot est un **sidecar** (chat web, email, Facebook).

**Risque :** moyen. Ne pas donner de conseils de sécurité dangereux ; disclaimer et escalade humaine.

**Premier pilote :** 30 questions, 80 % résolues sans escalade, 0 conseil dangereux.

---

## 5. Les autres matchs

### 9AM9AM-TEAM / PROD — le match stratégique

`~/Documents/9am9am-team` est un orchestrateur Python local : un CEO (Claude Code) découpe un objectif, des workers (opencode, autoclaw, cline) exécutent, l'état vit dans `.mini-orch/`.

Le bot peut être le **front office** : agents avec ordinateur, apps connectées, mémoire, voix, orientés client. L'orchestrateur reste le **back office** : planification, tâches, vérification. Deux stacks, un pont API à construire. C'est le match le plus ambitieux, mais le plus long.

### footbook / PitchUp — prospection et support

`~/Documents/footbook` — réservation de terrains multi-sports au Maroc, Supabase/Flutter. Le bot peut prospecter les clubs (annuaires, Instagram, email), rédiger les approches, onboarder les centres, répondre aux réservations et relancer les no-shows. Fit élevé, risque faible.

### marketplace-maroc / VINTAGED — confiance et sécurité

`~/Documents/marketplace-maroc` — marketplace de seconde main, Expo + Express/Prisma/Postgres, OTP/Twilio. Le bot peut modérer les annonces, détecter les schémas de fraude, aider les vendeurs à rédiger, trier les litiges. Fit élevé, mais risque moyen-élevé (fausses accusations, données personnelles).

### ST-induction — recherche et contenu

`~/Documents/ST-induction` — benchmark puis PWA d'induction HSE pour organismes de formation. Le bot peut faire la veille concurrentielle, générer des contenus et quiz, répondre aux apprenants, relancer les certifications qui expirent. Le contenu réglementaire doit être validé par un expert.

### site archi zineb / ze-architecture-premium — concierge de leads

Site vitrine d'architecture. Le bot peut qualifier les demandes (type de projet, surface, budget, délai), répondre aux questions, proposer un rendez-vous. Faible volume mais forte valeur par lead. Intégration simple en widget.

### jihane kine — concierge de cabinet

Site du cabinet de kinésithérapie. Le bot peut répondre aux questions pratiques, prendre les demandes de rendez-vous, envoyer les documents d'accueil. **Pas de conseil médical**, pas de données de santé dans le chat. Intégration simple, risque élevé.

### tifo — support organisateur

`~/Documents/tifo` — tifo numérique de stade, Expo + Supabase. Le bot peut accompagner les organisateurs, surveiller une session, générer les communications avant/après. Événementiel, faible fréquence : le bot est un bonus, pas le cœur.

### Animalo — modération et support

`~/Documents/Animalo` — rencontres et services pour animaux, Flutter + Supabase. Le bot peut modérer les profils, répondre au support, aider à retrouver un animal perdu. Un bot dans le chat social peut être intrusif : préférer l'usage interne.

### twitter desktop — veille et brouillons

Client macOS pour X avec un lien agent **lecture seule**. Le bot peut résumer le fil, suivre des comptes, préparer des brouillons. Attention aux CGU de X et à la qualité des sources.

### motion-studio / motion design — montage assisté

Le bot peut faire ce que la vidéo montre : télécharger les rushes, les organiser, lancer un montage, générer des sous-titres et des déclinaisons. Utile si l'activité contenu devient sérieuse.

### plan2dxf — support et QA

Convertisseur PDF/JPG → DXF pour architectes. Le bot peut répondre aux questions, guider l'installation, tester des conversions et documenter les erreurs. Faible volume.

### ÉTALON — agent de mesure

Concept de mesure indépendante des services IA (prix, latence, fiabilité). Le bot peut être l'**agent client** qui teste les services et collecte les preuves. Intéressant, mais le projet est encore un concept.

### Les petits

- **distributeur de tampon** : étude de marché, le bot peut faire de la recherche.
- **indiana** : concierge du café, réservation, FAQ.
- **ARTEFACT-AUDIT** : assistant d'audit sécurité avec navigateur.

---

## 6. À ne pas faire (maintenant)

| Projet | Pourquoi |
|---|---|
| **trading bot / bot 7trade** | Le bot peut surveiller et alerter, mais **jamais exécuter** des ordres. Risque financier trop élevé. |
| **marocourses** | Paris hippiques : cadre légal et éthique à clarifier avant d'automatiser. |
| **RELAIS** | Soutien parental, public vulnérable : un agent IA user-facing est prématuré. |
| **Kino (clinique)** | Notes de soin, secret médical : uniquement l'admin, jamais le clinique. |
| **pokemongospoof** | Contournement de règles/ToS : à éviter. |
| **DEFAULT / socle-projet** | Templates, pas des produits. |
| **centre-ambre-images-backup / archives** | Pas de besoin métier. |

---

## 7. Comment l'intégrer

Le bot est une app **Node/React autonome**. Les projets sont surtout **Python, Flutter, Swift, Next.js**. Ne pas fusionner les codebases : utiliser un **sidecar**.

### Trois modes

1. **Widget client** — un chat sur le site (iframe/web component) qui appelle l'API du bot. Idéal : troti.ma, jihane kine, site archi.
2. **Opérateur interne** — le bot tourne sur une machine cloud, utilise le navigateur/shell, fait le travail. Idéal : Testeur d'app, COMPTEX, OBDZ.
3. **Routines** — le bot se réveille à intervalles réguliers (relances, veille, monitoring). Idéal : COMPTEX, marketplace, Kino.

### Ce qui n'est pas prêt

- **Multi-tenant** : l'app est pensée pour un utilisateur et ses bots. Vendre à plusieurs clients exige une isolation stricte (DB, VM, secrets, logs).
- **Facturation** : pas de Stripe, pas de plans, pas de quotas.
- **Audit sécurité** : l'upstream n'a pas été audité. Les bots avec accès email/Slack/shell sont une surface d'attaque.
- **Voix** : incomplète dans la démo upstream.
- **Support** : pas de monitoring, pas de backups, pas de SLA.

---

## 8. Recommandation

| Objectif | Projet | Pourquoi |
|---|---|---|
| **Gagner de l'argent vite** | **Testeur d'app** | Offre déjà chiffrée, bot automatise prospection **et** livraison. |
| **Lancer le commerce** | **troti.ma** | WhatsApp/Instagram sont le canal naturel au Maroc. |
| **B2B à forte valeur** | **COMPTEX** | Relance des pièces = douleur réelle, prix élevé. |
| **Produit 9AM9AM** | **9AM9AM-TEAM** | Front office + back office : la vision complète. |
| **Support produit** | **OBDZ** | Le plus simple, le moins risqué. |
| **Santé** | **Kino phase 2** | Admin seulement, pas de clinique. |

**Mon choix pour un premier pilote : Testeur d'app.** C'est le seul projet où le bot peut à la fois **trouver le client**, **exécuter le service** et **produire le livrable**, avec un risque réglementaire faible et un prix déjà défini.

---

## 9. Prochaine étape

1. Choisir **un** projet et **un** rôle du bot.
2. Définir la métrique de succès (temps gagné, euros générés, taux de résolution).
3. Lister les données et outils autorisés (et interdits).
4. Construire le pont API/webhook minimal.
5. Faire un pilote d'une semaine, avec approbations humaines.
6. Décider : arrêter, ajuster, ou industrialiser.

> Rappel : le bot est un prototype. Avant un usage client réel, durcir l'isolation, les approbations, les logs, les sauvegardes et la facturation.
