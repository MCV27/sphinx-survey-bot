"""
run_bot.py — Lance le bot Sphinx avec suivi visuel.

Au lancement, ce script :
  1. démarre un petit serveur web local qui sert ce dossier,
  2. ouvre dashboard.html dans le navigateur (suivi d'avancement EN DIRECT),
  3. le dashboard permet de choisir le nombre de réponses et le mode (visible/headless)
     puis de lancer le bot en un clic.

Usage :
    python run_bot.py <config.json> [nombre] [options]

Options :
    --headless     Cache le navigateur du bot (par défaut : VISIBLE / mode démo).
    --no-dashboard Ne pas ouvrir le dashboard ni le serveur web.
    --port N       Port du serveur de suivi (défaut : 8765).

Exemples :
    python run_bot.py examples/config_freshpoke.json
    python run_bot.py config_freshpoke.json 150 --headless

Cadre d'usage : pour tester / charger VOS PROPRES questionnaires Sphinx.
N'agissez que sur des formulaires que vous possédez ou êtes autorisé à remplir.
"""

import functools
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import webbrowser

import bot_sphinx_generic as engine

HERE = os.path.dirname(os.path.abspath(__file__))

_bot_lock = threading.Lock()
_bot_running = False


def _run_bot_thread(config_path, total, headless):
    global _bot_running
    try:
        engine.run(config_path, total, headless=headless)
    finally:
        _bot_running = False


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, config_path=None, default_total=150, default_headless=True, **kwargs):
        self._config_path = config_path
        self._default_total = default_total
        self._default_headless = default_headless
        super().__init__(*args, directory=HERE, **kwargs)

    def do_GET(self):
        # Expose config par défaut au dashboard
        if self.path.startswith("/bot-config"):
            body = json.dumps({
                "config": os.path.basename(self._config_path) if self._config_path else "",
                "config_path": self._config_path or "",
                "default_total": self._default_total,
                "default_headless": self._default_headless,
                "running": _bot_running,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def do_POST(self):
        global _bot_running
        if self.path == "/launch":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")

            with _bot_lock:
                if _bot_running:
                    body = json.dumps({"ok": False, "error": "Bot déjà en cours"}).encode()
                    self.send_response(409)
                else:
                    total = int(data.get("total", self._default_total))
                    headless = bool(data.get("headless", self._default_headless))
                    config_path = data.get("config_path", self._config_path)
                    _bot_running = True
                    t = threading.Thread(
                        target=_run_bot_thread,
                        args=(config_path, total, headless),
                        daemon=True,
                    )
                    t.start()
                    body = json.dumps({"ok": True, "total": total, "headless": headless}).encode()
                    self.send_response(200)

            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # silence les logs HTTP


def start_dashboard_server(port, config_path, default_total, default_headless):
    handler = functools.partial(
        DashboardHandler,
        config_path=config_path,
        default_total=default_total,
        default_headless=default_headless,
    )
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

    config_path = os.path.abspath(args[0])
    headless = "--headless" in args
    no_dashboard = "--no-dashboard" in args

    port = 8765
    if "--port" in args:
        try:
            port = int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            pass

    rest = [a for a in args[1:] if not a.startswith("--") and a != str(port)]
    total = int(rest[0]) if rest else 150

    if no_dashboard:
        engine.run(config_path, total, headless=headless)
        return

    try:
        start_dashboard_server(port, config_path, total, headless)
        url = f"http://127.0.0.1:{port}/dashboard.html"
        print(f"Dashboard : {url}", flush=True)
        print("Lance le bot depuis le dashboard (bouton 'Lancer').", flush=True)
        webbrowser.open(url)
    except Exception as e:
        print(f"(Dashboard non démarré : {e} — lance le bot manuellement)", flush=True)
        engine.run(config_path, total, headless=headless)
        return

    # Garde le process vivant pendant que le serveur tourne
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nArrêt.", flush=True)


if __name__ == "__main__":
    main()
