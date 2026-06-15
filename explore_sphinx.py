"""
explore_sphinx.py — Détecteur automatique de structure d'un questionnaire Sphinx.

Ouvre l'URL avec un vrai navigateur (Playwright), parcourt les pages une à une en
cliquant le bouton "Suivant", et détecte sur chaque page :
  - les champs radio / checkbox / texte / scale,
  - leur ID/name HTML,
  - leurs options (valeur + libellé via le <label for=...>),
  - le bouton de navigation de la page (ssvnext / ssvsave / autre).

Il écrit un SQUELETTE de config JSON (config_squelette.json par défaut) où il ne
reste plus qu'à ajuster les poids et les règles conditionnelles. Il NE SOUMET RIEN :
sur la dernière page, il s'arrête avant le bouton d'enregistrement.

Pourquoi un vrai navigateur et pas du HTML statique : les formulaires Sphinx
chargent les pages 2+ en AJAX et régénèrent un token CSRF à chaque page. Le HTML
statique de la 1re page ne révèle donc pas les questions suivantes.

Usage :
    python explore_sphinx.py <URL_SPHINX> [fichier_sortie.json] [--visible] [--max-pages N]

Exemple :
    python explore_sphinx.py https://.../SurveyServer/s/taomeg config_squelette.json --visible
"""

import io
import json
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from urllib.parse import urlparse
from playwright.sync_api import sync_playwright


# Boutons de navigation Sphinx connus, par ordre de priorité de détection.
NEXT_BUTTONS = ["ssvnext", "ssvsave", "ssvfin", "ssvterm"]

# Script JS injecté pour extraire la structure de la page courante. On le fait
# côté navigateur en une passe (plus fiable que multiplier les appels Playwright).
EXTRACT_JS = r"""
() => {
  const out = { title: "", questions: [] };
  const h = document.querySelector("h1, h2, .title, legend");
  if (h) out.title = (h.innerText || "").trim().slice(0, 200);

  // Regroupe les inputs par name. Un radio/checkbox a plusieurs inputs même name.
  const byName = {};
  const order = [];
  document.querySelectorAll("input, textarea, select").forEach(el => {
    const name = el.getAttribute("name");
    if (!name) return;
    // Ignore les champs techniques Sphinx (token CSRF, navigation, etc.)
    if (/^(CS|ssv|__)/.test(name)) return;
    const type = (el.tagName === "TEXTAREA") ? "textarea"
               : (el.tagName === "SELECT") ? "select"
               : (el.getAttribute("type") || "text").toLowerCase();
    if (!byName[name]) { byName[name] = { name, type, options: [] }; order.push(name); }
    const value = el.getAttribute("value");
    if (value !== null && (type === "radio" || type === "checkbox")) {
      // Libellé associé via <label for="id">
      let label = "";
      const id = el.getAttribute("id");
      if (id) {
        const lab = document.querySelector(`label[for="${CSS.escape(id)}"]`);
        if (lab) label = (lab.innerText || "").trim();
      }
      byName[name].options.push({ value, label });
    }
  });

  out.questions = order.map(n => byName[n]);
  return out;
}
"""


def detect_next_button(page):
    """Renvoie le name du bouton de navigation présent et visible, sinon None."""
    for name in NEXT_BUTTONS:
        loc = page.locator(f"button[name='{name}']")
        try:
            if loc.count() > 0 and loc.first.is_visible():
                return name
        except Exception:
            continue
    return None


def normalize_type(raw):
    """Mappe le type HTML détecté vers le vocabulaire de la config."""
    if raw in ("radio",):
        return "radio"
    if raw in ("checkbox",):
        return "checkbox"
    if raw in ("textarea", "text", "email", "tel", "number"):
        return "text"
    if raw in ("range",):
        return "scale"
    if raw in ("select",):
        return "radio"  # liste déroulante -> traitée comme choix unique
    return "text"


def even_weights(options):
    """Poids égaux par défaut (1 à N) — l'utilisateur ajustera ensuite."""
    return {opt["value"]: round(100 / max(len(options), 1)) for opt in options}


def build_question_entry(q):
    """Transforme une question détectée en entrée de config (squelette)."""
    qtype = normalize_type(q["type"])
    entry = {"type": qtype, "field": q["name"]}

    if qtype in ("radio", "scale"):
        if q["options"]:
            entry["weights"] = even_weights(q["options"])
            entry["_options_detectees"] = {o["value"]: o["label"] for o in q["options"]}
    elif qtype == "checkbox":
        if q["options"]:
            entry["weights"] = even_weights(q["options"])
            entry["select_count"] = {"1": 50, "2": 35, "3": 15}
            entry["_options_detectees"] = {o["value"]: o["label"] for o in q["options"]}
    elif qtype == "text":
        entry["value"] = ["À COMPLÉTER"]

    return entry


def explore(url, out_path, visible, max_pages):
    cfg = {
        "url": url,
        "post_url_match": urlparse(url).path.lower(),
        "_note": ("Squelette généré par explore_sphinx.py. Ajustez les poids "
                  "(weights), les select_count (checkbox), et ajoutez les règles "
                  "weights_if / override_if / constraint / visible_if au besoin. "
                  "Supprimez les clés _options_detectees après relecture."),
        "pages": [],
        "questions": {},
    }
    seen_fields = set()
    page_count = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not visible)
        context = browser.new_context(locale="fr-FR")
        page = context.new_page()

        print(f"Ouverture : {url}", flush=True)
        page.goto(url, wait_until="networkidle", timeout=30000)
        time.sleep(2.5)

        while page_count < max_pages:
            page_count += 1
            data = page.evaluate(EXTRACT_JS)
            next_btn = detect_next_button(page)

            title = data.get("title") or "(sans titre)"
            qs = data.get("questions", [])
            print(f"\nPage {page_count} : {title!r} — {len(qs)} champ(s), "
                  f"bouton={next_btn}", flush=True)

            page_questions = []
            for q in qs:
                # Nom auto-généré stable basé sur le name HTML.
                qname = f"q_{q['name']}"
                if q["name"] in seen_fields:
                    continue
                seen_fields.add(q["name"])
                cfg["questions"][qname] = build_question_entry(q)
                page_questions.append(qname)
                opts = len(q["options"])
                print(f"  - {qname}  ({normalize_type(q['type'])}, "
                      f"{opts} option(s))", flush=True)

            kind = "intro" if not page_questions else "questions"
            cfg["pages"].append({
                "kind": kind,
                "next": next_btn or "ssvnext",
                "questions": page_questions,
            })

            # Conditions d'arrêt : plus de bouton, ou bouton final (on NE clique PAS
            # save pour ne rien soumettre pendant l'exploration).
            if next_btn is None:
                print("\nFin : aucun bouton de navigation détecté.", flush=True)
                break
            if next_btn in ("ssvsave", "ssvfin", "ssvterm"):
                print(f"\nFin : bouton final '{next_btn}' atteint — "
                      f"arrêt SANS soumettre.", flush=True)
                break

            # Avance à la page suivante via AJAX (même logique que le bot).
            try:
                match = cfg["post_url_match"]
                with page.expect_response(
                    lambda r: match in r.url.lower() and r.request.method == "POST",
                    timeout=20000,
                ):
                    page.click(f"button[name='{next_btn}']")
                page.wait_for_load_state("networkidle", timeout=15000)
                time.sleep(1.5)
            except Exception as e:
                print(f"\nArrêt : échec navigation page suivante ({type(e).__name__}: {e})",
                      flush=True)
                break

        context.close()
        browser.close()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    print(f"\n✓ Squelette écrit dans : {out_path}", flush=True)
    print(f"  {len(cfg['questions'])} question(s) sur {len(cfg['pages'])} page(s).", flush=True)
    print("  Étape suivante : ajuster les poids et règles, puis lancer le bot.", flush=True)
    return cfg


def main():
    args = [a for a in sys.argv[1:]]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    url = args[0]
    out_path = "config_squelette.json"
    visible = "--visible" in args
    max_pages = 30
    rest = [a for a in args[1:] if not a.startswith("--")]
    if rest:
        out_path = rest[0]
    if "--max-pages" in args:
        try:
            max_pages = int(args[args.index("--max-pages") + 1])
        except (IndexError, ValueError):
            pass

    explore(url, out_path, visible, max_pages)


if __name__ == "__main__":
    main()
