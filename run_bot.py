"""
run_bot.py — Lance le bot Sphinx avec suivi visuel.

Au lancement, ce script :
  1. démarre un petit serveur web local qui sert ce dossier,
  2. ouvre dashboard.html dans le navigateur (suivi d'avancement EN DIRECT),
  3. lance le bot, qui remplit le questionnaire et met à jour le dashboard.

Le suivi se fait donc dans un onglet navigateur — pas dans Claude Code. Inutile de
surveiller la sortie texte : le dashboard montre la progression, les compteurs et le
temps restant, et se rafraîchit tout seul.

Usage :
    python run_bot.py <config.json> [nombre] [options]

Options :
    --headless     Cache le navigateur du bot (par défaut : VISIBLE / mode démo).
    --no-dashboard Ne pas ouvrir le dashboard ni le serveur web.
    --port N       Port du serveur de suivi (défaut : 8765).

Exemples :
    python run_bot.py examples/config_freshpoke.json 150
    python run_bot.py config_squelette.json 3            (essai démo, navigateur visible)
    python run_bot.py config_squelette.json 200 --headless

Cadre d'usage : pour tester / charger VOS PROPRES questionnaires Sphinx.
N'agissez que sur des formulaires que vous possédez ou êtes autorisé à remplir.
"""

import functools
import http.server
import os
import socketserver
import sys
import threading
import time
import webbrowser

import bot_sphinx_generic as engine

HERE = os.path.dirname(os.path.abspath(__file__))


def start_dashboard_server(port):
    """Sert ce dossier en HTTP (nécessaire pour que dashboard.html lise progress.json)."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=HERE)
    # allow_reuse_address évite "address already in use" sur relance rapide.
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    config_path = args[0]
    headless = "--headless" in args
    no_dashboard = "--no-dashboard" in args

    port = 8765
    if "--port" in args:
        try:
            port = int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            pass

    rest = [a for a in args[1:] if not a.startswith("--")
            and a != str(port)]
    total = int(rest[0]) if rest else 150

    if not no_dashboard:
        try:
            start_dashboard_server(port)
            url = f"http://127.0.0.1:{port}/dashboard.html"
            print(f"Dashboard de suivi : {url}", flush=True)
            webbrowser.open(url)
            time.sleep(1.0)  # laisse l'onglet s'ouvrir avant de démarrer
        except Exception as e:
            print(f"(Dashboard non démarré : {e} — le bot continue quand même)", flush=True)

    engine.run(config_path, total, headless=headless)

    if not no_dashboard:
        print("\nLe dashboard reste consultable. Ferme cette fenêtre quand tu as fini.",
              flush=True)


if __name__ == "__main__":
    main()
