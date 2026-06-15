# Bot Sphinx — remplissage automatique de questionnaires (piloté par Claude Code)

Outil pour **tester et charger vos propres questionnaires [Sphinx Online](https://www.lesphinx.eu/)** :
il explore votre formulaire, génère des réponses réalistes et cohérentes, et les
soumet automatiquement. Pratique pour vérifier le comportement d'une enquête,
tester sa capacité, ou produire un jeu de données de démonstration.

La particularité : **vous ne touchez à aucun code**. Vous ouvrez le projet dans
[Claude Code](https://claude.com/claude-code) et vous lancez un seul prompt. Claude
fait le reste — il explore votre questionnaire, vous pose quelques questions sur le
public visé, construit la configuration, et lance le remplissage.

Pendant le remplissage :
- une **fenêtre Chrome** s'ouvre et remplit le formulaire sous vos yeux (mode démo) ;
- un **onglet tableau de bord** affiche l'avancement en direct (barre de
  progression, nombre de réponses, erreurs, temps restant), rafraîchi tout seul ;
- Claude lance le bot **en arrière-plan** et arrête de parler — il ne « regarde »
  pas le déroulé, donc cette phase **ne consomme pas de tokens**.

> ⚠️ **Cadre d'usage.** Cet outil est destiné aux questionnaires que **vous
> possédez ou que vous êtes autorisé à remplir** (vos propres enquêtes, instances
> de test, démonstrations). Ne l'utilisez pas pour fausser l'enquête d'un tiers :
> injecter de fausses réponses dans une vraie collecte invalide les données et
> contrevient généralement aux conditions d'utilisation des plateformes.

## Utilisation (la voie normale)

1. Installez [Claude Code](https://claude.com/claude-code).
2. Clonez ce dépôt et ouvrez-le :
   ```bash
   git clone <url-du-repo>
   cd sphinx
   claude
   ```
3. Lancez un prompt comme :
   > « Remplis mon questionnaire Sphinx de test. »

   Claude va alors :
   - vous demander le **lien** de votre questionnaire ;
   - l'**explorer automatiquement** (détection des pages, questions, types, options) ;
   - vous poser quelques questions sur votre **public cible** (âge, profil, ton des
     réponses…) pour rendre les réponses crédibles ;
   - construire la configuration et vous la **résumer pour validation** ;
   - lancer le remplissage du **nombre de réponses** que vous indiquez.

Tout le déroulé que Claude suit est décrit dans [`CLAUDE.md`](CLAUDE.md).

## Combien ça consomme (tokens / coût) ?

Seule la phase d'**orchestration** par Claude consomme des tokens : installation,
récapitulatif de l'exploration, questions sur votre cible, résumé de la config, et
lancement. Le **remplissage lui-même tourne en arrière-plan et ne coûte rien** (le
suivi se fait dans l'onglet navigateur, pas via Claude).

Ordre de grandeur pour une mise en route complète (exploration + config + lancement) :

| Phase | Échanges Claude | Tokens approx. |
|-------|-----------------|----------------|
| Installation + exploration + récap | 2–4 messages | ~5–15 k |
| Questions cible + rédaction config | 3–5 messages | ~10–25 k |
| Essai démo + lancement série | 1–2 messages | ~3–8 k |
| **Total mise en route** | **~6–11 messages** | **~20–50 k tokens** |
| Remplissage de N réponses (arrière-plan) | 0 | **0** |

Soit, à titre indicatif, bien moins d'un dollar avec un modèle Claude récent, que
vous lanciez 10 ou 1000 réponses. Les chiffres exacts dépendent du nombre de
questions de votre formulaire et des allers-retours de validation.

## Prérequis

```bash
pip install -r requirements.txt
python -m playwright install chromium
```
(Claude le fera pour vous si besoin.)

## Utilisation manuelle (sans Claude)

Si vous préférez piloter les scripts vous-même :

```bash
# 1. Explorer un questionnaire -> génère config_squelette.json
python explore_sphinx.py "https://.../SurveyServer/s/XXXX" config_squelette.json
#    (ajoutez --visible pour voir le navigateur)

# 2. Éditer config_squelette.json : ajuster les poids et règles
#    (voir examples/config_freshpoke.json pour un exemple complet)

# 3. Lancer le bot
python run_bot.py config_squelette.json 150
#    Conseil : testez d'abord avec "1 --visible".
```

## Format de configuration (en bref)

Une config décrit l'URL, l'ordre des pages, et chaque question. Une question fermée
porte des **poids** par option, et peut porter des **règles** :

| Clé | Effet |
|-----|-------|
| `weights` | Probabilité de chaque option (`{"1": 45, "2": 45, ...}`) |
| `select_count` | (cases à cocher) combien d'options cocher en moyenne |
| `weights_if` | Poids différents selon une autre réponse déjà donnée |
| `override_if` | Force une valeur si une condition est vraie |
| `constraint` | Impose un ordre entre deux champs (`gte_field`) |
| `visible_if` | Question conditionnelle (n'apparaît que si une option est choisie) |

L'exemple [`examples/config_freshpoke.json`](examples/config_freshpoke.json)
illustre toutes ces règles sur un cas réel.

## Fichiers

| Fichier | Rôle |
|---------|------|
| [`CLAUDE.md`](CLAUDE.md) | Instructions que Claude Code suit pour tout orchestrer |
| `explore_sphinx.py` | Détecteur de structure → squelette de config |
| `bot_sphinx_generic.py` | Moteur générique (génération + remplissage) |
| `run_bot.py` | Point d'entrée en ligne de commande |
| `examples/config_freshpoke.json` | Configuration d'exemple complète |
