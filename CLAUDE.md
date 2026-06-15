# Assistant de remplissage de questionnaires Sphinx

Ce dépôt est un outil pour **tester et charger ses propres questionnaires Sphinx
Online** : il génère des réponses réalistes et les soumet automatiquement (vérifier
le comportement d'un formulaire, sa capacité, ou produire un jeu de démonstration).

**Tu (Claude) es l'interface.** L'utilisateur n'écrit aucun code, n'édite aucun
JSON, ne lance aucune commande lui-même. Tu fais tout : tu explores son
questionnaire, tu lui poses quelques questions, tu génères la config, puis tu lances
le bot avec un suivi visuel.

## ⚠️ Règle d'or : économiser les tokens

Le suivi de progression se fait dans **un onglet navigateur** (dashboard.html), PAS
dans la conversation. Une fois le bot lancé :
- lance-le **en arrière-plan** (`run_in_background: true`),
- **n'attends pas** et **ne relis pas** sa sortie en boucle,
- dis simplement à l'utilisateur de regarder l'onglet « Bot Sphinx » dans son
  navigateur, puis **arrête de parler**.

Lire la sortie du bot ligne par ligne ne sert à rien (le dashboard le fait) et
gaspille des tokens. Ne reviens vers l'utilisateur que s'il te le demande, ou en fin
de run pour un résumé court.

## Cadre d'usage (rappel bref au démarrage)

Outil destiné aux questionnaires que l'utilisateur **possède ou est autorisé à
remplir**. Si l'usage décrit vise clairement à fausser l'enquête d'un tiers, ne
poursuis pas. Dans le doute, pose la question une fois, puis fais confiance.

---

## Déroulé à suivre (en une fois, sans faire patienter inutilement)

### Étape 1 — Installer les dépendances (silencieux si déjà fait)
```
pip install -r requirements.txt
python -m playwright install chromium
```
Fais-le une fois au début. Si tout est déjà installé, c'est quasi instantané.

### Étape 2 — Demander le lien Sphinx
Demande l'URL du questionnaire (`https://.../SurveyServer/s/XXXX`). C'est la seule
chose requise de l'utilisateur pour démarrer l'exploration.

### Étape 3 — Explorer automatiquement le questionnaire
```
python explore_sphinx.py "<URL>" config_squelette.json
```
- Le script parcourt les pages (AJAX) et écrit `config_squelette.json`. Il **ne
  soumet rien**.
- Lis `config_squelette.json` et **récapitule en clair** à l'utilisateur : nombre de
  pages, et pour chaque question son libellé détecté (`_options_detectees`) + son
  type. C'est le moment où il corrige un type mal deviné.
- Si l'exploration échoue, relance en ajoutant `--visible` pour voir le navigateur
  et diagnostiquer.

### Étape 4 — Poser les questions sur la CIBLE de répondants
C'est ce qui rend les réponses crédibles. Demande, de façon groupée et
conversationnelle (idéalement via le sélecteur de questions) :
- **tranche d'âge dominante** (étudiants 18-25 ? actifs 30-50 ? mélange ?) ;
- **profil** (étudiants, actifs, grand public, clientèle d'un secteur ?) ;
- **répartition H/F** approximative si pertinent ;
- **tonalité attendue** des réponses d'opinion (enthousiastes / mitigées / variées) ;
- toute **contrainte logique** connue (ex : « si fréquence = jamais, pas de lieu
  d'achat », « prix max ≥ prix min »).

### Étape 5 — Traduire la cible en config pondérée
Édite `config_squelette.json` :
- mets des **poids réalistes** (`weights`) sur chaque question fermée (au lieu des
  poids égaux par défaut) selon la cible décrite ;
- pour les **cases à cocher**, ajuste `select_count` et les poids par option ;
- ajoute les **règles** : `weights_if` (poids selon une autre réponse),
  `override_if` (forcer une valeur sous condition), `constraint.gte_field`
  (imposer un ordre), `visible_if` (question conditionnelle « Précisez… ») ;
- **supprime** les clés `_options_detectees`, `_note`, `_description` une fois propre.

`examples/config_freshpoke.json` est l'exemple de référence complet (toutes les
règles illustrées) — appuie-toi dessus pour le format exact.

Puis **résume la config en français** (pas le JSON brut) et demande validation :
« voici comment je répartis les réponses, je lance ? ».

### Étape 6 — Essai de démonstration (1 réponse, navigateur visible)
Avant la série complète, lance UN essai visible pour montrer que ça marche :
```
python run_bot.py config_squelette.json 1
```
Ça ouvre Chrome (le mec voit le formulaire se remplir) + le dashboard. Si l'essai
passe, enchaîne sur la série.

### Étape 7 — Lancer la série complète EN ARRIÈRE-PLAN
Demande le **nombre de réponses**, puis lance en arrière-plan :
```
python run_bot.py config_squelette.json <nombre>
```
- Lance cette commande avec `run_in_background: true`.
- Le script ouvre tout seul l'onglet **dashboard** (barre de progression,
  compteurs, temps restant, qui se rafraîchit toutes les 2 s) et la fenêtre Chrome
  du bot.
- Dis à l'utilisateur : « C'est parti — regarde l'onglet *Bot Sphinx* dans ton
  navigateur pour suivre l'avancement. » Puis **arrête de parler** (cf. règle d'or).
- Si les toutes premières réponses échouent, ré-explore et corrige la config (souvent
  un type de champ ou un ID). Sinon, ne surveille pas.

---

## Rappels techniques (Sphinx)
- Pages chargées en **AJAX** ; le bot attend `networkidle` après chaque clic.
- Bouton « Suivant » = `ssvnext` ; **dernière page** = `ssvsave`. L'explorateur le
  détecte et le met dans `pages[].next`.
- Cases/radios cochés via leur `<label>` (le moteur gère ça).
- Token CSRF qui change à chaque page → on passe par un vrai navigateur.

## Fichiers
| Fichier | Rôle |
|---------|------|
| `explore_sphinx.py` | Détecte la structure → `config_squelette.json` |
| `bot_sphinx_generic.py` | Moteur : génère les profils, remplit, écrit `progress.json` |
| `run_bot.py` | Lance le serveur de suivi + le dashboard + le bot |
| `dashboard.html` | Suivi d'avancement en direct (onglet navigateur) |
| `examples/config_freshpoke.json` | Exemple de référence (toutes les règles) |
