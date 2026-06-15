"""
bot_sphinx_generic.py — Moteur générique de remplissage de questionnaires Sphinx Online.

Ce module ne contient AUCUN champ codé en dur. Il lit un fichier de config JSON
(voir examples/config_freshpoke.json) qui décrit l'URL, les pages, et chaque question
(type, sélecteur/ID HTML, poids, règles conditionnelles), puis :

  1. génère un "profil" de réponses pondéré et cohérent (generate_profile),
  2. remplit le formulaire page par page via Playwright (fill_survey).

Usage normal via run_bot.py :
    python run_bot.py examples/config_freshpoke.json 150

Cadre d'usage : destiné à tester / charger VOS PROPRES questionnaires Sphinx
(QA, test de capacité, données de démonstration). N'agissez que sur des
formulaires que vous possédez ou que vous êtes autorisé à remplir.
"""

import io
import json
import os
import random
import sys
import time
from datetime import datetime

# Sortie UTF-8 robuste (Windows console) — repris de bot_sphinx.py
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
]


# ---------------------------------------------------------------------------
# Chargement / validation de la config
# ---------------------------------------------------------------------------

def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Validations minimales pour donner des erreurs lisibles plutôt que des
    # KeyError obscurs en plein run.
    for key in ("url", "pages", "questions"):
        if key not in cfg:
            raise ValueError(f"Config invalide : clé manquante '{key}' dans {path}")

    questions = cfg["questions"]
    for page_idx, page in enumerate(cfg["pages"]):
        for qname in page.get("questions", []):
            if qname not in questions:
                raise ValueError(
                    f"Config invalide : la page {page_idx} référence la question "
                    f"'{qname}' qui n'est pas définie dans 'questions'."
                )

    # post_url_match sert à attendre la requête AJAX de navigation. Par défaut,
    # on le déduit de l'URL (chemin en minuscules) — voir click_next.
    cfg.setdefault("post_url_match", _default_post_match(cfg["url"]))
    return cfg


def _default_post_match(url):
    """Déduit un fragment d'URL à matcher pour la requête POST de navigation.

    Sphinx poste sur la même URL que le formulaire. On prend le chemin en
    minuscules (les comparaisons d'URL Playwright sont sensibles à la casse).
    """
    try:
        from urllib.parse import urlparse
        return urlparse(url).path.lower()
    except Exception:
        return url.lower()


# ---------------------------------------------------------------------------
# Génération de profils pondérés + conditionnels
# ---------------------------------------------------------------------------
#
# Chaque question de la config peut porter :
#   "type"        : "radio" | "checkbox" | "text" | "scale"
#   "field"       : ID/name HTML du champ (ou "selector" pour un CSS custom)
#   "weights"     : { "1": 45, "2": 45, ... }  (radio/scale : poids par option)
#   "weights_if"  : [ { "when": {"autreQ": [valeurs]}, "weights": {...} }, ... ]
#                     -> remplace "weights" si TOUTES les conditions "when" matchent
#   "select_count": { "1": 50, "2": 35, "3": 15 }  (checkbox : combien d'options cocher)
#   "override_if" : [ { "when": {...}, "value": [6] } ]
#                     -> force une valeur fixe si la condition matche (ex: "n'achète pas")
#   "constraint"  : { "gte_field": "prix_min" }  (radio/scale : valeur >= autre champ)
#   "visible_if"  : { "field": "situation", "in": [6] }
#                     -> question conditionnelle : tirée/remplie seulement si vrai
#   "value"       : [ "texte..." ]  (text : valeur(s) possibles à écrire)
#
# Les questions sont résolues dans l'ordre de définition du dict "questions",
# donc weights_if / override_if / constraint peuvent référencer des réponses
# déjà tirées (comme generate_profile() de l'ancien bot le faisait à la main).


def _matches(conditions, profile):
    """True si, pour chaque {champ: [valeurs]}, la réponse déjà tirée matche.

    Pour un champ checkbox (réponse = liste), match si l'intersection est non vide.
    Pour un champ scalaire, match si la valeur est dans la liste attendue.
    """
    for field, expected in conditions.items():
        got = profile.get(field)
        if got is None:
            return False
        if isinstance(got, list):
            if not any(g in expected for g in got):
                return False
        else:
            if got not in expected:
                return False
    return True


def _weighted_choice(weights, rng):
    """Tire une clé (convertie en int) selon un dict {valeur: poids}."""
    options = [int(k) for k in weights.keys()]
    w = list(weights.values())
    return rng.choices(options, weights=w)[0]


def _resolve_weights(qdef, profile):
    """Renvoie le dict de poids effectif (en tenant compte de weights_if)."""
    for rule in qdef.get("weights_if", []):
        if _matches(rule["when"], profile):
            return rule["weights"]
    return qdef.get("weights", {})


def _draw_question(qname, qdef, profile, rng):
    """Tire la réponse d'une question et la range dans profile[qname].

    Renvoie True si la question a été tirée, False si elle est masquée (visible_if).
    """
    # Question conditionnelle : si la condition d'affichage est fausse, on saute.
    vis = qdef.get("visible_if")
    if vis is not None and not _matches({vis["field"]: vis["in"]}, profile):
        return False

    qtype = qdef.get("type", "radio")

    if qtype in ("radio", "scale"):
        # override_if : valeur forcée (ex: situation->autre)
        forced = None
        for rule in qdef.get("override_if", []):
            if _matches(rule["when"], profile):
                forced = rule["value"]
                break
        if forced is not None:
            value = forced[0] if isinstance(forced, list) else forced
        else:
            weights = _resolve_weights(qdef, profile)
            value = _weighted_choice(weights, rng)
        # Contrainte d'ordre (ex: prix_max >= prix_min)
        c = qdef.get("constraint")
        if c and "gte_field" in c:
            other = profile.get(c["gte_field"])
            if isinstance(other, int) and value < other:
                value = other
        profile[qname] = value

    elif qtype == "checkbox":
        forced = None
        for rule in qdef.get("override_if", []):
            if _matches(rule["when"], profile):
                forced = rule["value"]
                break
        if forced is not None:
            profile[qname] = list(forced)
        else:
            weights = qdef.get("weights", {})
            opts = [int(k) for k in weights.keys()]
            w = list(weights.values())
            sc = qdef.get("select_count", {"1": 100})
            nb = _weighted_choice(sc, rng)
            nb = max(1, min(nb, len(opts)))
            # Tirage sans remise pondéré : on sur-échantillonne puis on déduplique
            # (même technique que l'ancien bot pour éviter les doublons).
            picked = list(dict.fromkeys(rng.choices(opts, weights=w, k=nb * 4)))[:nb]
            if not picked:
                picked = [opts[0]]
            profile[qname] = picked

    elif qtype == "text":
        values = qdef.get("value", [""])
        profile[qname] = rng.choice(values)

    else:
        raise ValueError(f"Type de question inconnu pour '{qname}': {qtype}")

    return True


def generate_profile(cfg, rng=random):
    """Construit un profil complet {nom_question: réponse} selon la config."""
    profile = {}
    for qname, qdef in cfg["questions"].items():
        _draw_question(qname, qdef, profile, rng)
    return profile


# ---------------------------------------------------------------------------
# Helpers d'interaction Playwright (repris de bot_sphinx.py, rendus génériques)
# ---------------------------------------------------------------------------

def human_delay(mini=0.3, maxi=0.9):
    time.sleep(random.uniform(mini, maxi))


def _selector_for(qdef, value=None):
    """Construit le sélecteur CSS d'un champ.

    Priorité à "selector" (CSS custom) ; sinon on utilise input[name='<field>'].
    Pour radio/checkbox on cible la valeur précise ; pour text, le champ lui-même.
    """
    if "selector" in qdef:
        base = qdef["selector"]
        if value is not None and "{value}" in base:
            return base.replace("{value}", str(value))
        return base
    field = qdef["field"]
    if value is not None:
        return f"input[name='{field}'][value='{value}']"
    return f"[name='{field}']"


def click_choice(page, qdef, value, timeout=12000):
    """Coche un radio/checkbox via le label (contournement Sphinx)."""
    sel = _selector_for(qdef, value)
    page.wait_for_selector(sel, timeout=timeout)
    human_delay(0.15, 0.5)
    label_id = page.get_attribute(sel, "id")
    if label_id:
        page.click(f"label[for='{label_id}']")
    else:
        page.click(sel, force=True)


def fill_text(page, qdef, value, timeout=12000):
    sel = _selector_for(qdef)
    page.wait_for_selector(sel, timeout=timeout)
    human_delay(0.15, 0.4)
    page.fill(sel, str(value))


def _field_present(page, qdef, value=None):
    """True si le champ existe dans le DOM (pour les questions conditionnelles)."""
    sel = _selector_for(qdef, value)
    try:
        return page.locator(sel).count() > 0
    except Exception:
        return False


def click_next(page, cfg, button_name="ssvnext"):
    """Clique le bouton de navigation et attend la fin de la requête AJAX."""
    human_delay(0.5, 1.0)
    match = cfg["post_url_match"]
    with page.expect_response(
        lambda r: match in r.url.lower() and r.request.method == "POST",
        timeout=20000,
    ):
        page.click(f"button[name='{button_name}']")
    page.wait_for_load_state("networkidle", timeout=15000)
    human_delay(0.3, 0.7)


def click_save(page, button_name="ssvsave"):
    """Clique le bouton final (pas de requête à matcher, juste networkidle)."""
    human_delay(0.5, 1.0)
    page.click(f"button[name='{button_name}']")
    page.wait_for_load_state("networkidle", timeout=20000)
    human_delay(0.5, 1.0)


# ---------------------------------------------------------------------------
# Remplissage d'une page puis du formulaire complet
# ---------------------------------------------------------------------------

def fill_page(page, cfg, page_def, profile):
    kind = page_def.get("kind", "questions")
    next_button = page_def.get("next", "ssvnext")

    if kind != "intro":
        for qname in page_def.get("questions", []):
            qdef = cfg["questions"][qname]

            # Question masquée dans le profil (visible_if faux) -> on saute.
            if qname not in profile:
                continue

            answer = profile[qname]
            qtype = qdef.get("type", "radio")

            # Pour une question conditionnelle, le champ peut ne pas être dans le
            # DOM même si on a tiré une valeur : on saute proprement plutôt que
            # de timeout (l'ancien bot ne gérait pas ce cas du tout).
            if qdef.get("visible_if") is not None and not _field_present(page, qdef):
                continue

            if qtype in ("radio", "scale"):
                click_choice(page, qdef, answer)
            elif qtype == "checkbox":
                for v in answer:
                    click_choice(page, qdef, v)
            elif qtype == "text":
                fill_text(page, qdef, answer)

    # Navigation : ssvsave (dernière page) ne déclenche pas le même flux AJAX.
    if next_button == "ssvsave":
        click_save(page, next_button)
    else:
        page.wait_for_selector(f"button[name='{next_button}']", timeout=15000)
        click_next(page, cfg, next_button)


def fill_survey(cfg, profile, headless=True):
    """Remplit une fois le questionnaire complet. Lève en cas d'échec."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": random.randint(1280, 1920),
                      "height": random.randint(768, 1080)},
            locale=cfg.get("locale", "fr-FR"),
        )
        page = context.new_page()
        try:
            page.goto(cfg["url"], wait_until="networkidle", timeout=30000)
            time.sleep(random.uniform(1.5, 3.0))
            for page_def in cfg["pages"]:
                fill_page(page, cfg, page_def, profile)
        finally:
            context.close()
            browser.close()


# ---------------------------------------------------------------------------
# Suivi d'avancement (fichier lu par dashboard.html — pas par Claude)
# ---------------------------------------------------------------------------
#
# Le bot écrit son état dans progress.json après chaque réponse. dashboard.html
# rafraîchit ce fichier toute seule dans un onglet navigateur. Ainsi l'utilisateur
# voit l'avancement EN TEMPS RÉEL sans que Claude ait à lire la sortie du script
# (donc sans consommer de tokens à surveiller le run).

PROGRESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "progress.json")


def _write_progress(state):
    """Écrit l'état courant dans progress.json (écriture atomique)."""
    try:
        tmp = PROGRESS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PROGRESS_FILE)
    except Exception:
        # Le suivi ne doit jamais faire planter le bot.
        pass


# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------

def run(config_path, total, headless=True):
    cfg = load_config(config_path)
    success = 0
    errors = 0
    started = time.time()
    recent = []  # dernières lignes pour le dashboard

    def push_progress(status, last_line):
        recent.append(last_line)
        del recent[:-12]  # ne garder que les 12 dernières
        done = success + errors
        elapsed = time.time() - started
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 else None
        _write_progress({
            "status": status,                 # "running" | "done"
            "total": total,
            "done": done,
            "success": success,
            "errors": errors,
            "percent": round(100 * done / total, 1) if total else 0,
            "elapsed_sec": round(elapsed),
            "eta_sec": round(eta) if eta is not None else None,
            "url": cfg["url"],
            "config": os.path.basename(config_path),
            "updated_at": datetime.now().strftime("%H:%M:%S"),
            "recent": list(recent),
        })

    print(f"Config : {config_path}")
    print(f"URL    : {cfg['url']}")
    print(f"Cible  : {total} réponses, headless={'oui' if headless else 'non'}")
    print(f"Suivi  : ouvre dashboard.html dans un navigateur.")
    print("-" * 60, flush=True)
    push_progress("running", "Démarrage…")

    for i in range(1, total + 1):
        profile = generate_profile(cfg)
        summary = " ".join(
            f"{k}={v}" for k, v in list(profile.items())[:6]
        )
        print(f"[{i}/{total}] {summary} ...", flush=True)

        try:
            fill_survey(cfg, profile, headless=headless)
            success += 1
            line = f"[{i}/{total}] OK"
            print(f"{line}  (total: {success} OK / {errors} err)", flush=True)
        except PlaywrightTimeout as e:
            errors += 1
            line = f"[{i}/{total}] TIMEOUT"
            print(f"{line} : {e}", flush=True)
        except Exception as e:
            errors += 1
            line = f"[{i}/{total}] ERREUR"
            print(f"{line} : {e}", flush=True)
        finally:
            push_progress("running", line)
            time.sleep(random.uniform(2.0, 5.0))

    push_progress("done", f"Terminé : {success} OK / {errors} erreurs")
    print(f"\nTerminé — {success} réponses envoyées, {errors} erreurs.", flush=True)
    return success, errors
