"""Local web app: python app.py -> http://localhost:8765"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
import webbrowser
from datetime import datetime
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


def remember(**choices):
    """Save choices into the snapshot file, so they survive restarts and refreshes."""
    snap = {**json.loads(yahoo.OUT.read_text(encoding="utf-8")), **choices}
    yahoo.OUT.write_text(json.dumps(snap, indent=1, ensure_ascii=False), encoding="utf-8")


GM_PROMPT = """You are the AI GM in the user's fantasy basketball app. Follow the playbook below, with these changes:
the app keeps data/league.json current, so skip step 1; the only command you can run is `.venv/bin/python model.py ...`;
there's no humanizer skill here, so write pitches short and casual yourself; and the chat shows replies as plain text,
so no markdown (no tables, headers, bold or backticks). Keep answers short.
"""
WEB = """You can also search the web (WebSearch) for news model.py can't see: injuries, minutes and role changes, trades.
Search when the question needs fresh news, name the site you got it from, and still take every number from model.py.
"""
# the chat settings the AI GM tab can pick (the first of each is the default). Fable needs usage credits on a Pro plan.
MODELS, EFFORTS = ("claude-opus-5-5", "claude-fable-5-1"), ("medium", "low", "high", "xhigh", "max")


def claude_chat(message, punt, session=None, model=None, effort=None, web=True):
    """Answer through Claude Code headless (`claude -p`) on the user's own login, so no API key is needed.
    --safe-mode keeps Martin's CLAUDE.md, memory, plugins and hooks out; the GM playbook is SKILL.md.
    model / effort fall back to the defaults unless they're on the lists; web adds the WebSearch tool."""
    skill = (ROOT / ".claude" / "skills" / "gm" / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2]
    flag = f'add --punt "{",".join(punt)}" to every model.py command' if punt else "no --punt"
    web = ["WebSearch"] if web else []
    cmd = [shutil.which("claude"), "-p", "--output-format", "json", "--safe-mode",
           "--model", model if model in MODELS else MODELS[0], "--effort", effort if effort in EFFORTS else EFFORTS[0],
           "--system-prompt", GM_PROMPT + (WEB if web else "") + skill, "--tools", "Bash", *web,
           "--allowedTools", "Bash(.venv/bin/python model.py:*)", *web, "--permission-mode", "dontAsk"]
    if session:
        cmd += ["--resume", session]
    # the message goes in on stdin, so one starting with "-" can't be read as a flag
    r = subprocess.run(cmd, input=f"(User's team: {lg.me}. Punting: {', '.join(punt) or 'nothing'}, so {flag}.)\n\n{message}",
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=300)
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude -p failed: {(r.stderr or r.stdout).strip()[-300:]}") from None
    return {"reply": out.get("result") or "No answer came back.", "session": out.get("session_id")}


CHATS, chats_lock = model.DATA / "chats.json", threading.Lock()


def chats(body):
    """The AI GM's past conversations, kept in data/chats.json as {id: {title, updated, session, messages}}.
    {save: {id, title, session, messages}} stores one, {open: id} returns it, {delete: id} removes it; every other
    call (and save / delete) returns the list, newest first, without the messages."""
    with chats_lock:  # ponytail: one lock for the whole file; fine for one user
        saved = json.loads(CHATS.read_text(encoding="utf-8")) if CHATS.exists() else {}
        if body.get("open"):
            return {"id": body["open"], **saved[body["open"]]}
        if c := body.get("save"):
            saved[str(c["id"])[:64]] = {"title": str(c.get("title") or "Untitled")[:80], "session": c.get("session"),
                                        "updated": datetime.now().isoformat(timespec="seconds"),
                                        "messages": list(c["messages"])}
        if body.get("delete"):
            saved.pop(body["delete"], None)
        if c or body.get("delete"):  # write a copy, then swap it in, so a crash never leaves half a file
            tmp = CHATS.with_suffix(".tmp")
            tmp.write_text(json.dumps(saved, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(CHATS)
    return sorted(({"id": k, "title": v["title"], "updated": v["updated"], "n": len(v["messages"])}
                   for k, v in saved.items()), key=lambda x: x["updated"], reverse=True)


def api(path, q, body):
    global lg, gm
    punt = body.get("punt", q.get("punt", [""])[0].split(",") if q.get("punt") else [])
    punt = [c for c in punt if c]
    if path == "/api/state":
        return lg.state(punt)
    if path == "/api/team":
        return {"team": body["team"], "roster": lg.team_roster(body["team"], punt), "build": lg.build(body["team"])}
    if path == "/api/trade":
        return lg.trade(body["give"], body["get"], punt, their_free=bool(body.get("their_free")))
    if path == "/api/ideas":
        return lg.ideas(body.get("mode", "fair"), punt, body.get("team") or None)
    if path == "/api/search":
        return lg.search(body.get("give", []), body.get("get", []), body.get("lock", []), body.get("team") or None,
                         int(body.get("size", 3)), body.get("tier", "fair"), body.get("goal", "win"),
                         body.get("cats", []), body.get("vs"), bool(body.get("healthy")), body.get("shape"), punt,
                         limit=30, their_free=bool(body.get("their_free")),
                         max_overpay=None if body.get("max_overpay") is None else float(body["max_overpay"]))
    if path == "/api/strategy":  # punt planner, ~15s on the real league
        return lg.strategy()
    if path == "/api/player":  # the player card
        return lg.card(body["name"], punt)
    if path == "/api/compare":  # 2 to 4 players side by side
        return lg.compare(body.get("players", []), punt)
    if path == "/api/schedule":  # schedule planner: your games per day, NBA teams' games ahead
        return lg.planner(punt)
    if path == "/api/news":  # My team's News panel
        return lg.roster_news()
    if path == "/api/players":  # rostered players, plus free agents (team "FA") with {"fa": true}
        return lg.pool(bool(body.get("fa")))
    if path == "/api/me":  # which team is yours
        if body.get("team") not in lg.rosters:
            raise KeyError(f"Unknown team: {body.get('team')}")
        remember(me=body["team"], matchup=None)  # the opponent was the old team's
        lg, gm = model.load(), None
        return lg.state(punt)
    if path == "/api/week":  # POST {opponent} sets this week's opponent until the week ends
        if opp := body.get("opponent"):
            if opp not in lg.rosters or opp == lg.me:
                raise KeyError(f"Unknown opponent: {opp}")
            lg.snap["matchup"] = lg.matchup = {**lg.matchup, "opponent": opp, "week_end": lg.week_end}
            remember(matchup=lg.matchup)
        return lg.this_week(punt=punt)
    if path == "/api/refresh":
        if os.environ.get("YAHOO_LEAGUE_ID"):
            yahoo.snapshot()
        elif lg.snap.get("text"):  # imported from a snapshot file: read the newest one (and refetch injuries)
            # newest = latest date in the name ("...-2026-10-27.txt"), file time only between same-day pulls,
            # so an old snapshot copied back into the folder doesn't win
            day = lambda p: m[0] if (m := re.search(r"\d{4}-\d{2}-\d{2}", p.name)) else ""
            newest = max(ROOT.glob("*snapshot*.txt"), key=lambda p: (day(p), p.stat().st_mtime),
                         default=lg.snap["text"])
            yahoo.from_text(newest, lg.me)
            (model.DATA / "injuries.json").unlink(missing_ok=True)
        else:
            yahoo.demo()
        lg, gm = model.load(), None
        return lg.state(punt)
    if path == "/api/chats":
        return chats(body)
    if path == "/api/chat":
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            if not shutil.which("claude"):
                return {"reply": "Install Claude Code and log in (or add ANTHROPIC_API_KEY=... to .env) to chat with the AI GM."}
            return {"reply": ""} if body.get("reset") else claude_chat(body["message"], punt, body.get("session"),
                                                                       body.get("model"), body.get("effort"),
                                                                       body.get("web", True) is not False)
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


API = {"/api/state", "/api/team", "/api/trade", "/api/ideas", "/api/search", "/api/strategy", "/api/player", "/api/compare", "/api/schedule", "/api/news",
       "/api/players", "/api/me", "/api/week", "/api/refresh", "/api/chat", "/api/chats"}

if __name__ == "__main__":
    # ponytail: localhost-only, single user; no auth because it never listens on the network
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Running on http://localhost:{PORT}  ({lg.snap.get('name')}, you = {lg.me})")
    if "--no-browser" not in sys.argv:
        webbrowser.open(f"http://localhost:{PORT}")
    srv.serve_forever()
