"""Local web app: python app.py -> http://localhost:8765"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
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
# every league here is on the saved what-ifs (Q27) and plan picks (Q30)
load = lambda refetch=(): model.load(refetch=refetch, what=model.whatif(), made=model.moves())
lg = load()
gen = 0  # bumped whenever data/league.json changes under the app (remember(), Refresh): live() drops a rebuild it
         # started before (Nov 4 audit #23: a team switch made while it loaded was overwritten with the old team)


def remember(**choices):
    """Save choices into the snapshot file, so they survive restarts and refreshes."""
    global gen
    with refreshing:  # a pull writing league.json meanwhile would lose this choice, or this its rosters
        snap = {**json.loads(yahoo.OUT.read_text(encoding="utf-8")), **choices}
        model._write(yahoo.OUT, json.dumps(snap, indent=1, ensure_ascii=False))
        gen += 1  # after the write: a rebuild that read the old file is dropped, one reading the new file is fine


def state(l, punt, aim="season"):
    """The league's state, plus the watchlist. aim (M30): "title" adds the title odds to Waivers' suggested moves."""
    st = l.state(punt, aim)
    st["watch"] = model.watching(l, punt)  # Q32: the watchlist (data/watch.json), at the top of Waivers
    return st


refreshing = threading.Lock()  # Refresh and watch() one at a time


def pull():
    """Yahoo's API into data/league.json; False when it fails (no access yet, rate limited, offline)."""
    try:
        yahoo.snapshot(me=lg.me, fresh=True)  # the button: the rank pages too (Y2)
        return True
    except Exception as e:
        print(f"Yahoo API pull failed ({type(e).__name__}: {e})", file=sys.stderr)
        return False


def refresh():
    """Refresh: Yahoo's API pulled again when .env has YAHOO_LEAGUE_ID (a failed pull keeps the last one), then the
    league rebuilt from data/league.json with injuries, this season's stats and live scores refetched. Without the API
    the league is the last `python yahoo.py import <snapshot>.txt` (or the demo): nothing reads the folder (Y3)."""
    global lg, gen
    with refreshing:
        if os.environ.get("YAHOO_LEAGUE_ID"):
            pull()
        gen += 1  # league.json is rewritten: drop a rebuild live() started on the old one
        lg = load(("injuries", "stats", "live"))  # offline, the old copies stay (the sidebar says so)
        sync_offers(lg)
    record()


def record():
    """Q33: data/history.jsonl gets each new snapshot's row and each finished week's final (model.history(), ~1 s a new
    snapshot; the first run backfills every one, ~25 s). A failure never stops a Refresh."""
    try:
        model.history()
    except Exception:
        traceback.print_exc()


def watch():
    """A what-if or a plan pick set from the AI GM chat (model.py what) rebuilds the league, checked every 15 seconds.
    The page asks /api/stamp every 20 seconds and pulls the new numbers. (Y3: it no longer looks for snapshot files.)"""
    global lg, gen
    what = model.mtime(model.WHATIF)
    while True:
        time.sleep(15)
        try:
            if (at := model.mtime(model.WHATIF)) != what:
                what = at
                if (model.whatif() != {w["name"]: {k: v for k, v in w.items() if k not in ("name", "text")} for w in lg.whats()}
                        or [m["id"] for m in model.moves()] != [m["id"] for m in lg.moves]):
                    with refreshing:
                        gen += 1
                        lg = load()
        except Exception:
            traceback.print_exc()


GM_PROMPT = """You are the AI GM in the user's fantasy basketball app. Follow the playbook below, with these changes:
the app keeps data/league.json current, so skip step 1; the only command you can run is `.venv/bin/python model.py ...`;
there's no humanizer skill here, so write pitches short and casual yourself; and the chat shows replies as plain text,
so no markdown (no tables, headers, bold or backticks). Keep answers short.
"""
WEB = """You can also search the web (WebSearch) and read a page (WebFetch) for news model.py can't see: injuries,
minutes and role changes, trades. On game days, who's expected to play is on RotoWire's lineups page
(https://www.rotowire.com/basketball/nba-lineups.php) and the NBA's official injury report (official.nba.com, "NBA Injury
Report"). Look when the question needs fresh news or tonight's lineups, name the site, and still take every number from
model.py; when a page contradicts the model (a player ruled out it still counts), say so and write a note.
"""
# ESPN's injury feed is refetched this often while the app runs (live()): game-day statuses change by the hour. On game
# nights live() also wakes a minute after each tip-off and every model.LIVE_POLL minutes while a game is on (M18)
LIVE_MIN = 30
YAHOO_MIN = 15  # Yahoo's API pulled this often by live() (10 calls a pull, the rank pages hourly: Y2) when .env has YAHOO_LEAGUE_ID
# the Python the AI GM runs model.py with: SKILL.md says .venv/bin/python (Mac), Windows venvs keep it in Scripts
PY = next((p for p in (".venv/bin/python", ".venv/Scripts/python.exe") if (ROOT / p).exists()), "python")
# the chat settings the AI GM tab can pick (the first of each is the default). Fable needs usage credits on a Pro plan.
MODELS, EFFORTS = ("claude-opus-5-5", "claude-fable-5-1"), ("medium", "low", "high", "xhigh", "max")


def claude_chat(message, punt, session=None, model=None, effort=None, web=True, opts=(), aim="season"):
    """Answer through Claude Code headless (`claude -p`) on the user's own login, so no API key is needed.
    --safe-mode keeps Martin's CLAUDE.md, memory, plugins and hooks out; the GM playbook is SKILL.md.
    model / effort fall back to the defaults unless they're on the lists; web adds the WebSearch tool. opts: model.py
    flags for the app's Waivers / Trades settings (--healthy-fa, --no-free, --their-free), so its numbers match.
    aim (M30): the app's Aim for; on the title the chat adds --aim title to strategy, trade, move, waivers and search."""
    skill = (ROOT / ".claude" / "skills" / "gm" / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2]
    flag = f'add --punt "{",".join(punt)}" to every model.py command' if punt else "no --punt"
    if opts:
        flag += f", and the app's Waivers / Trades settings: add {' '.join(opts)} to waivers, trade, search and strategy"
    if aim == "title":
        flag += ", and he's aiming for the title: add --aim title to strategy, trade, move, waivers and search"
    web = ["WebSearch", "WebFetch"] if web else []
    cmd = [shutil.which("claude"), "-p", "--output-format", "json", "--safe-mode",
           "--model", model if model in MODELS else MODELS[0], "--effort", effort if effort in EFFORTS else EFFORTS[0],
           "--system-prompt", (GM_PROMPT + (WEB if web else "") + skill).replace(".venv/bin/python", PY),
           "--tools", "Bash", *web, "--allowedTools", f"Bash({PY} model.py:*)", *web, "--permission-mode", "dontAsk"]
    if session:
        cmd += ["--resume", session]
    # the message goes in on stdin, so one starting with "-" can't be read as a flag
    r = subprocess.run(cmd, input=f"(User's team: {lg.me}. Punting: {', '.join(punt) or 'nothing'}. Aim for: {aim}. So {flag}.)\n\n{message}",
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
        if c or body.get("delete"):
            write(CHATS, saved)
    return sorted(({"id": k, "title": v["title"], "updated": v["updated"], "n": len(v["messages"])}
                   for k, v in saved.items()), key=lambda x: x["updated"], reverse=True)


def write(path, data):
    """Write a copy, then swap it in, so a crash never leaves half a file."""
    model._write(path, json.dumps(data, indent=1, ensure_ascii=False))


OFFERS = model.DATA / "offers.json"
OFFER_KEYS = ("me", "team", "give", "get", "text", "unit", "net", "their_net", "their_view", "win", "win_after",
              "market", "accept", "verdict")


def offers(body, punt):
    """Trade offers you copied for Yahoo (Q8), newest first in data/offers.json: the deal, what the model said then
    and the answer you mark. It's the data for tuning HYPE, TIERS and accept() (N6). {log: offer} adds one (the same
    deal still waiting for an answer is replaced), {set: id, outcome: "yes" | "no" | None} marks it, {delete: id}
    removes it; every call returns the list."""
    with chats_lock:
        saved = json.loads(OFFERS.read_text(encoding="utf-8")) if OFFERS.exists() else []
        if o := body.get("log"):
            o = {k: o.get(k) for k in OFFER_KEYS}
            deal = lambda x: (x["me"], x["team"], sorted(x["give"]), sorted(x["get"]))
            saved = [{**o, "punt": punt, "id": uuid.uuid4().hex[:8], "sent": datetime.now().isoformat(timespec="seconds"),
                      "outcome": None}] + [x for x in saved if x["outcome"] or deal(x) != deal(o)]
        for x in saved:
            if x["id"] == body.get("set"):
                x["outcome"] = body.get("outcome") if body.get("outcome") in ("yes", "no") else None
                x.pop("auto", None)  # your answer, not Yahoo's
        if body.get("delete"):
            saved = [x for x in saved if x["id"] != body["delete"]]
        if o or body.get("set") or body.get("delete"):
            write(OFFERS, saved)
    return saved


def sync_offers(lg):
    """Yahoo's trades into Offers sent after an API pull (M6's data without marking by hand). An offer to or from you
    still pending on Yahoo is logged with what the model says now (no punt), or tied to the one you copied; once it's
    gone from Yahoo it's Accepted if the players moved (or the trade was vetoed: they still said yes), else Declined,
    marked `auto` (a withdrawn offer reads as declined: re-mark it). An offer they sent you is a yes from them
    (`by: "them"`). ponytail: a trade accepted between two pulls is never seen pending, so it's only caught if you
    copied it; auto-refresh closes that."""
    trades, me = lg.snap.get("trades"), lg.me
    if trades is None:  # a snapshot file, not the API
        return
    mine = lambda t: ({"team": t["to"], "give": t["give"], "get": t["get"]} if t["from"] == me
                      else {"team": t["from"], "give": t["get"], "get": t["give"]})
    deal = lambda x: (x["team"], tuple(sorted(x["give"])), tuple(sorted(x["get"])))
    ours = [t for t in trades if me in (t["from"], t["to"])]
    pending = {t["key"]: t for t in ours if t["status"] == "proposed"}
    done = {deal(mine(t)) for t in ours if t["status"] in ("successful", "vetoed")}
    have = lambda n: lg.find(n, strict=False) in lg.rosters[me]
    with chats_lock:
        saved = json.loads(OFFERS.read_text(encoding="utf-8")) if OFFERS.exists() else []
        before = json.dumps(saved)
        for key, t in pending.items():
            o = mine(t)
            x = next((x for x in saved if x.get("yahoo") == key), None) or next(
                (x for x in saved if not x.get("yahoo") and x["outcome"] is None and x["me"] == me and deal(x) == deal(o)), None)
            if x is None:
                try:
                    r = lg.trade(o["give"], o["get"])
                except Exception as e:  # a player the model can't find: logged without a prediction
                    print(f"Offer {o} not scored ({e})", file=sys.stderr)
                    r = {}
                x = {**{k: r.get(k) for k in OFFER_KEYS}, **o, "me": me, "text": "", "punt": [], "id": uuid.uuid4().hex[:8],
                     "sent": datetime.fromtimestamp(t["ts"]).isoformat(timespec="seconds"), "outcome": None}
                if t["from"] != me:
                    x.update(by="them", outcome="yes")
                saved.insert(0, x)
            x["yahoo"] = key
        for x in saved:
            if x["outcome"] is None and x["me"] == me and x.get("yahoo") not in pending:
                yes = deal(x) in done or (all(map(have, x["get"])) and not any(map(have, x["give"])))
                if yes or x.get("yahoo"):  # one never seen on Yahoo may not have been sent: left for you
                    x.update(outcome="yes" if yes else "no", auto=True)
            if x["me"] == me and x.get("yahoo"):  # Nov 25: still live on Yahoo; the page folds the rest under Past offers
                x["open"] = x["yahoo"] in pending
        if json.dumps(saved) != before:
            write(OFFERS, saved)


def api(path, q, body):
    global lg, gen
    punt = model.punts(body.get("punt", q.get("punt", [""])[0]))
    aim = model.League._aim(body.get("aim", q.get("aim", [""])[0]) or None)  # M30: Aim for the season or the title
    # Waivers' "Healthy only": injured free agents off waivers for its suggestions and every trade's free moves
    lg_in = lg
    fl = lg.healthy_fa() if body.get("healthy_fa", q.get("healthy_fa", [""])[0]) else lg
    if path == "/api/state":
        return state(fl, punt, aim)
    if path == "/api/team":
        t = body["team"]  # wants: what they'd want from you (Trades), category leagues only
        return {"team": t, "roster": lg.team_roster(t, punt), "build": lg.build(t),
                **({"wants": lg.wants(t)} if t != lg.me and lg.mode != "points" else {})}
    if path == "/api/trade":
        return fl.trade(body["give"], body["get"], punt, free=bool(body.get("free", True)),
                        their_free=bool(body.get("their_free")), drop=body.get("drop") or (), aim=aim)
    if path == "/api/ideas":
        return fl.ideas(body.get("mode", "fair"), punt, body.get("team") or None)
    if path == "/api/search":
        return fl.search(body.get("give", []), body.get("get", []), body.get("lock", []), body.get("team") or None,
                         int(body.get("size", 3)), body.get("tier", "fair"), body.get("goal") or ("title" if aim == "title" else "win"),
                         body.get("cats", []), body.get("vs"), bool(body.get("healthy")), body.get("shape"), punt,
                         limit=30, free=bool(body.get("free", True)), their_free=bool(body.get("their_free")),
                         max_overpay=None if body.get("max_overpay") is None else float(body["max_overpay"]),
                         outside=int(body.get("outside") or 0))
    if path == "/api/strategy":  # punt planner, ~45s on the real league; aim "title" picks on title odds (M30)
        return fl.strategy(aim=aim)
    if path == "/api/player":  # the player card
        return lg.card(body["name"], punt)
    if path == "/api/compare":  # 2 to 4 players side by side
        return lg.compare(body.get("players", []), punt)
    if path == "/api/move":  # Waivers' add/drop evaluator: any add and any drop, this week and the season
        return lg.add_drop(body.get("add") or None, body.get("drop") or None, punt, day=body.get("day") or None, aim=aim)
    if path == "/api/waivers":  # Q32: Waivers' Play for, free agents ranked on some categories (none: as in state)
        return {**fl.waivers(punt, 40, body.get("cats") or (), aim), "dropped": fl.dropped(punt)}
    if path == "/api/watch":  # Q32: POST {add} or {remove} a player on the watchlist; the rows either way
        return model.watching(fl, punt, body.get("add"), body.get("remove"))
    if path == "/api/check":  # Q29: what's wrong with your roster now (also in /api/state as "check")
        return lg.roster_check(punt)
    if path == "/api/drops":  # Q29: Who to drop, each cut this week, next week and the season (~1 s)
        return lg.drops(punt)
    if path == "/api/playoffs":  # M30: League's Playoffs period (each round vs the likely opponents) and the key dates
        return {**(lg.playoff_view() or {}), "dates": lg.dates()}
    if path == "/api/ahead":  # League's period picker: the league over schedule weeks from `week` (0 = this one)
        return fl.weeks_ahead(int(q.get("week", ["1"])[0]), int(q.get("n", ["1"])[0]))
    if path == "/api/what":  # POST {player, line, pct, min, health, until} sets his what-if (none of them: clears it);
        with refreshing:     # {clear: true} alone clears all. Every number is rebuilt on what's saved (Q27)
            name = body.get("player") and lg.p.at[lg.find(body["player"]), "PLAYER_NAME"]
            model.whatif(name, bool(body.get("clear")), **{k: body.get(k) for k in ("line", "pct", "min", "health", "until")})
            gen += 1
            lg = load()
        return state(lg.healthy_fa() if fl is not lg_in else lg, punt, aim)
    if path == "/api/moved":  # Q30: POST a plan pick (Who to drop's Use this: {add, drop, activate, park}) or
        with refreshing:      # {undo: id | "all"}; every number rebuilt
            if body.get("undo"):
                model.moves(undo=body["undo"])
            else:
                if old := [m["id"] for m in lg.moves]:
                    model.moves(undo=old)  # one pick at a time: a new Use this replaces the last
                    lg = load()
                model.moves(new=lg.made(**{k: body.get(k) or () for k in ("add", "drop", "activate", "park")}))
            gen += 1
            lg = load()
        return state(lg.healthy_fa() if fl is not lg_in else lg, punt, aim)
    if path == "/api/schedule":  # schedule planner: your games per day, NBA teams' games ahead
        return lg.planner(punt)
    if path == "/api/news":  # My team's News panel
        return lg.roster_news()
    if path == "/api/players":  # rostered players, plus free agents (team "FA") with {"fa": true}
        return lg.pool(bool(body.get("fa")))
    if path == "/api/me":  # which team is yours
        if body.get("team") not in lg.rosters:
            raise KeyError(f"Unknown team: {body.get('team')}")
        remember(me=body["team"], matchup=model.switched(lg.snap, body["team"]))  # the week stays, not the opponent (B1)
        lg = load()
        return state(lg, punt, aim)
    if path == "/api/week":  # POST {opponent} sets this week's opponent until the week ends
        if opp := body.get("opponent"):
            if opp not in lg.rosters or opp == lg.me:
                raise KeyError(f"Unknown opponent: {opp}")
            lg.snap["matchup"] = lg.matchup = {**lg.matchup, "opponent": opp, "week_end": lg.week_end}
            remember(matchup=lg.matchup)
            fl = lg.healthy_fa() if fl is not lg else lg  # the copy was made before the new opponent
        keep, wts, fl = body.get("keep") or (), body.get("weights") or None, fl.without(body.get("skip") or ())  # skip: taken off waivers
        w = fl.this_week(punt=punt, keep=keep, weights=wts, cats=body.get("cats") or (), choose=body.get("choose") or None,  # Healthy only: no injured free agent as a stream (audit #15); keep: never dropped
                         deep=bool(body.get("deep")), adds=None if body.get("adds") in (None, "") else int(body["adds"]),  # B20: the deep search; how many adds to use
                         pool=body.get("pool"))  # B21: who streams may drop, picked on the page (null: the model's 3)
        keep = w.get("keep", keep) if w.get("picked") else keep  # your pick keeps everyone else, Today's moves too
        return {**w, "night": fl.game_night(punt=punt, keep=keep, weights=wts, week=w)} if w.get("opponent") else w  # Q31: the Today panel
    if path == "/api/recap":  # Q33: last week (or ?week=N) and the season so far, from data/history.jsonl
        return lg.recap(int(q.get("week", ["-1"])[0]))
    if path == "/api/today":  # Q31: game day, tonight's games, lineups, live lines and the week day by day
        return fl.without(body.get("skip") or ()).game_night(punt=punt, keep=body.get("keep") or (), weights=body.get("weights") or None)
    if path == "/api/refresh":
        refresh()
        return state(lg, punt, aim)
    if path == "/api/stamp":  # when the league on screen was pulled, its what-ifs and plan picks: the page's check for
        return {"fetched": lg.snap.get("fetched", ""), "what": lg.whats(), "moves": [m["id"] for m in lg.moves]}  # a new one
    if path == "/api/chats":
        return chats(body)
    if path == "/api/offers":  # Trades' Offers sent: what you copied for Yahoo and the answers
        return offers(body, punt)
    if path == "/api/chat":
        if not shutil.which("claude"):
            return {"reply": "Install Claude Code and log in to chat with the AI GM."}
        return {"reply": ""} if body.get("reset") else claude_chat(body["message"], punt, body.get("session"),
                                                                   body.get("model"), body.get("effort"),
                                                                   body.get("web", True) is not False,
                                                                   [f for f, on in (("--healthy-fa", body.get("healthy_fa")),
                                                                                    ("--no-free", body.get("free") is False),
                                                                                    ("--their-free", body.get("their_free"))) if on], aim)
    raise KeyError(path)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, data, ctype="application/json"):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-cache")  # a restarted app's new page, not the browser's old copy
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


API = {"/api/state", "/api/team", "/api/trade", "/api/ideas", "/api/search", "/api/strategy", "/api/player", "/api/compare", "/api/move", "/api/schedule", "/api/news",
       "/api/players", "/api/me", "/api/week", "/api/refresh", "/api/stamp", "/api/ahead", "/api/what", "/api/moved", "/api/check", "/api/drops", "/api/waivers", "/api/watch", "/api/today", "/api/recap", "/api/chat", "/api/chats", "/api/offers", "/api/playoffs"}

def live():
    """Every LIVE_MIN minutes, ESPN's injury feed again (and any other cache past its window: news, box scores...),
    the league rebuilt on the same snapshot (Martin, Nov 3: "it should be constantly updating"). On game nights (M18)
    also a minute after each tip-off, so its players lock on time, and every model.LIVE_POLL minutes while a game is on
    (model.next_poll()): load() refetches the live scores then (their own window), and this season's stats and box
    scores once a game goes final; the injury feed stays on its LIVE_MIN clock. Requests already running finish on the
    old copy; offline, the old copies stay and the sidebar says so. With Yahoo's API on, every YAHOO_MIN minutes a
    pull, which rebuilds the league (and Offers) only when something changed (yahoo.snapshot(changed=True)); the page
    sees the new `fetched` within 20 s. Before it, yesterday's lineups once a day (yahoo.lineups(), Y5)."""
    global lg, gen
    feed = asked = time.time()  # the injury feed's last forced refetch, Yahoo's last pull
    api_on = bool(os.environ.get("YAHOO_LEAGUE_ID"))
    while True:
        time.sleep(model.next_poll(lg.live, (YAHOO_MIN if api_on else LIVE_MIN) * 60))
        if api_on and time.time() - asked >= YAHOO_MIN * 60 - 60:  # Nov 23: moves made on Yahoo come in by themselves
            asked, new = time.time(), None
            try:  # Y5: yesterday's lineups, once a day (each team's lineup habit, model.habits(), read by load())
                yahoo.lineups()
            except Exception as e:
                print(f"Yahoo lineups failed ({type(e).__name__}: {e})", file=sys.stderr)
            try:
                with refreshing:
                    if new := yahoo.snapshot(changed=True, me=lg.me):
                        gen += 1
                        lg = load()
                        sync_offers(lg)
                if new:
                    record()  # Q33: a new day's pull gets its history row, a finished week its final
                    continue
            except Exception as e:
                print(f"Yahoo API pull failed ({type(e).__name__}: {e})", file=sys.stderr)
        due = time.time() - feed >= LIVE_MIN * 60 - 60
        g0 = gen
        try:
            new = load(("injuries",) if due else ())
        except Exception:
            traceback.print_exc()
            continue
        feed = time.time() if due else feed
        if gen != g0:  # your team, the opponent or the snapshot changed while it loaded: that request's league stands
            continue
        lg = new


if __name__ == "__main__":
    threading.Thread(target=live, daemon=True).start()
    threading.Thread(target=watch, daemon=True).start()
    threading.Thread(target=record, daemon=True).start()  # Q33: the history, backfilled on the first run
    # ponytail: localhost-only, single user; no auth because it never listens on the network
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Running on http://localhost:{PORT}  ({lg.snap.get('name')}, you = {lg.me})")
    if "--no-browser" not in sys.argv:
        webbrowser.open(f"http://localhost:{PORT}")
    srv.serve_forever()
