"""Local web app: python app.py -> http://localhost:8765"""
import json
import os
import sys
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

import model
import yahoo

ROOT = Path(__file__).parent
PORT = 8765
load_dotenv(ROOT / ".env")
lg = model.load()
gm = None  # created on first chat so the app works without an API key


def api(path, q, body):
    global lg, gm
    punt = body.get("punt", q.get("punt", [""])[0].split(",") if q.get("punt") else [])
    punt = [c for c in punt if c]
    if path == "/api/state":
        return lg.state(punt)
    if path == "/api/team":
        return {"team": body["team"], "roster": lg.team_roster(body["team"], punt), "build": lg.build(body["team"])}
    if path == "/api/trade":
        return lg.trade(body["give"], body["get"], punt)
    if path == "/api/ideas":
        return lg.ideas(body.get("mode", "fair"), punt, body.get("team") or None)
    if path == "/api/search":
        return lg.search(body.get("give", []), body.get("get", []), body.get("lock", []), body.get("team") or None,
                         int(body.get("size", 3)), body.get("tier", "fair"), body.get("goal", "win"),
                         body.get("cats", []), body.get("vs"), bool(body.get("healthy")), body.get("shape"), punt,
                         limit=30)
    if path == "/api/players":
        return sorted(({"name": n, "team": t} for t, ks in lg.rosters.items() for n in lg.names(ks)), key=lambda p: p["name"])
    if path == "/api/refresh":
        yahoo.snapshot() if os.environ.get("YAHOO_LEAGUE_ID") else yahoo.demo()
        lg, gm = model.load(), None
        return lg.state(punt)
    if path == "/api/chat":
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return {"reply": "Add ANTHROPIC_API_KEY=... to .env and restart to chat with the AI GM."}
        if gm is None:
            import ai
            gm = ai.GM(lg)
        if body.get("reset"):
            gm.reset()
            return {"reply": ""}
        return {"reply": gm.ask(body["message"], punt)}
    raise KeyError(path)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, data, ctype="application/json"):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self, body):
        u = urlparse(self.path)
        if u.path == "/":
            return self._send(200, (ROOT / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
        try:
            self._send(200, api(u.path, parse_qs(u.query), body))
        except KeyError as e:
            self._send(404 if u.path not in API else 400, {"error": str(e).strip("'\"")})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": str(e)})

    def do_GET(self):
        self._handle({})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._handle(json.loads(self.rfile.read(n) or b"{}"))

    def log_message(self, *a):
        pass


API = {"/api/state", "/api/team", "/api/trade", "/api/ideas", "/api/search","/api/players", "/api/refresh", "/api/chat"}

if __name__ == "__main__":
    # ponytail: localhost-only, single user; no auth because it never listens on the network
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Running on http://localhost:{PORT}  ({lg.snap.get('name')}, you = {lg.me})")
    if "--no-browser" not in sys.argv:
        webbrowser.open(f"http://localhost:{PORT}")
    srv.serve_forever()
