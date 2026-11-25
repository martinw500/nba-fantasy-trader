"""Pull your Yahoo league into data/league.json (settings, every roster, top free agents). Read-only.

  python yahoo.py            # Yahoo's API (YAHOO_LEAGUE_ID and the keys in .env): data/league.json + snapshots/yahoo-<day>.json
  python yahoo.py check      # an API pull next to the newest snapshot, nothing written but the raw XML (data/yahoo_raw/)
  python yahoo.py demo       # fake 12-team league drafted from real stats, no Yahoo needed
  python yahoo.py import snapshot.txt ["My team"]   # league from a text snapshot, no Yahoo API (format: parse_text)

One-time setup: see README.md -> "Connect Yahoo".
"""
import json
import os
import random
import re
import shutil
import sys
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "league.json"
YAHOO_STAT = {"3PTM": "FG3M", "ST": "STL", "TO": "TOV", "FG%": "FG%", "FT%": "FT%", "PTS": "PTS", "REB": "REB",
              "AST": "AST", "BLK": "BLK", "FGM": "FGM", "FGA": "FGA", "FTM": "FTM", "FTA": "FTA"}
BENCH_ONLY = {"IL", "IL+", "NA"}
STATUS = {"out": "O", "o": "O", "questionable": "GTD", "q": "GTD", "gtd": "GTD", "dtd": "DTD", "d": "DTD", "day-to-day": "DTD",
          "probable": "", "p": "", "inj": "INJ", "il": "IL", "il+": "IL+", "susp": "SUSP", "na": "NA"}


def save(snap):
    import model
    snap["fetched"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    model._write(OUT, json.dumps(snap, indent=1, ensure_ascii=False))  # the running app may be reading it
    return snap


API = "https://fantasysports.yahooapis.com/fantasy/v2/"
RAW = None  # a folder: every API response is saved there as XML (`python yahoo.py check`), to fix field paths from


def _api(league_id=None):
    """get(path) -> the XML root of a Yahoo Fantasy API call (namespaces stripped), signed with the token yfpy saved
    to .env (refreshed by itself), and the league ID. XML, not JSON: Yahoo's JSON wraps every field in lists."""
    from dotenv import load_dotenv
    from yfpy.query import YahooFantasySportsQuery
    load_dotenv(ROOT / ".env")
    league_id = league_id or os.environ["YAHOO_LEAGUE_ID"]
    if not os.environ.get("YAHOO_REFRESH_TOKEN") and not sys.stdin.isatty():  # the app can't answer the login prompt
        raise RuntimeError("Not logged in to Yahoo: run `.venv/bin/python yahoo.py check` once in Terminal")
    q = YahooFantasySportsQuery(league_id, game_code="nba", env_file_location=ROOT, save_token_data_to_env_file=True)
    local, renew = threading.local(), threading.Lock()

    def session():  # Y2: pull() calls from a few threads at once, and a requests session isn't promised thread-safe
        if getattr(local, "token", None) != q.oauth.access_token:  # one per thread, made again after a token refresh
            local.token, local.s = q.oauth.access_token, q.oauth.oauth.get_session(token=q.oauth.access_token)
        return local.s

    def get(path):
        token = q.oauth.access_token
        r = session().get(API + path)
        if r.status_code == 401:  # token expired mid-pull: refreshed once, whichever thread sees it first
            with renew:
                if q.oauth.access_token == token:
                    q._authenticate()
            r = session().get(API + path)
        if RAW:  # named by path: the calls run in any order
            Path(RAW).mkdir(parents=True, exist_ok=True)
            (Path(RAW) / f"{re.sub(r'[^\w;=,.-]+', '_', path)[:120]}.xml").write_bytes(r.content)
        if r.status_code == 999:
            raise RuntimeError("Yahoo is rate limiting this app; try again in a while")
        root = ElementTree.fromstring(r.content)
        for e in root.iter():
            e.tag = e.tag.rpartition("}")[2]
        if r.status_code != 200:
            raise RuntimeError(f"Yahoo said {r.status_code} to {path}: {root.findtext('.//description') or r.text[:300]}")
        return root
    return get, league_id


def _pair(name):
    """A display stat's parts: Yahoo shows made / attempted as "FGM/A" (Nov 23's real pull, not the docs' "FGM/FGA"),
    which read as FGM and "A" left FGA out, so FT% this week came to 21 made of 0 tried (100.4% expected)."""
    a, *b = name.split("/")
    return [a, *(a[:-1] + x if x == "A" else x for x in b)]


def _x(e, path, default=""):
    return (e.findtext(path) or default).strip() if e is not None else default


def _n(e, path):
    v = _x(e, path)
    return int(float(v)) if re.fullmatch(r"-?[\d.]+", v) else 0


def _when(ts):
    """A Unix time as the snapshot's transaction time, local: "Nov 22 4:50pm"."""
    d = datetime.fromtimestamp(int(ts))
    return f"{d:%b} {d.day} {d.hour % 12 or 12}:{d:%M}{'am' if d.hour < 12 else 'pm'}"


def _tipped(now):
    """The NBA teams whose game today (the machine's date, the model's `today`; an evening game's ET date is the same)
    had tipped off at the pull: Yahoo's totals carry their stats live, so the model locks them and never adds their
    game again (League.held). None when nba.com's scoreboard can't be read.
    Nov 23: the snapshots' rule (the week from tomorrow once any game tipped) dropped every later tip that night from
    This week, and its Today panel said none of your players had a game while Start / sit listed five."""
    import model
    try:
        games = [g for g in model.scores()["games"].values() if g["date"] == now.date().isoformat()]
    except Exception:
        return None
    return sorted({t for g in games if g["status"] != 1 or datetime.fromisoformat(g["tip"]) <= now for t in g["teams"]})


WORKERS = 6  # Yahoo calls at once (Y2): it rate-limits with HTTP 999
TOOK = None  # the last pull's (seconds, calls), for the CLI


def pull(league_id=None, get=None, fresh=False):
    """The league from Yahoo's API in parse_text's shape, so build() treats both alike. Checked against the real
    league Nov 23 (`python yahoo.py check` compares a pull with the newest snapshot and saves the raw XML). Yahoo's API
    has no projections (its "projected_season" stats are the season's actual ones), so those come from the newest
    snapshot file, as does a preseason rank Yahoo doesn't give; players it ranks below MAX_RANK get no rank.
    Y2 (Nov 24): the calls run WORKERS at a time once the settings are in, each path once a pull; the rank pages come
    from data/yahoo_ranks.json when under RANK_MIN minutes old (fresh: from Yahoo regardless, the Refresh button)."""
    global TOOK
    if get is None:
        get, league_id = _api(league_id)
    t0, asked, pool = datetime.now(), {}, ThreadPoolExecutor(WORKERS)

    def ask(path):  # the call started on the pool now; the same path asked again shares it
        if path not in asked:
            asked[path] = pool.submit(get, path)
        return asked[path]
    try:
        out = _pull(league_id, ask, lambda path: ask(path).result(), fresh)
    finally:
        pool.shutdown(cancel_futures=True)
    TOOK = ((datetime.now() - t0).total_seconds(), len(asked))
    return out


def _pull(league_id, ask, get, fresh):
    gk = _x(get("game/nba"), "game/game_key")
    L = f"league/{gk}.l.{league_id}"
    lg = get(f"{L}/settings").find("league")
    st = lg.find("settings")
    cats = st.find("stat_categories/stats")
    names = {_x(s, "stat_id"): _x(s, "display_name") for s in cats.findall("stat")}  # incl. display-only "FGM/FGA"
    scored = [s for s in cats.findall("stat") if _x(s, "is_only_display_stat") != "1" and _x(s, "enabled", "1") == "1"]
    kind = _x(st, "scoring_type")  # head = categories, headone = one win, headpoint = points, roto
    points = {YAHOO_STAT.get(names[_x(m, "stat_id")], names[_x(m, "stat_id")]): float(_x(m, "value"))
              for m in st.findall("stat_modifiers/stats/stat") if _x(m, "stat_id") in names} if "point" in kind else None
    pos = [(_x(r, "position"), _n(r, "count")) for r in st.findall("roster_positions/roster_position")]
    slots = [p for p, c in pos if p not in BENCH_ONLY | {"BN"} for _ in range(c)]
    end = _n(lg, "end_week")
    first_po = _n(st, "playoff_start_week") if _x(st, "uses_playoff") == "1" else end + 1
    settings = {"roster": len(slots) + sum(c for p, c in pos if p == "BN"), "starters": len(slots), "slots": slots,
                "il": sum(c for p, c in pos if p in ("IL", "IL+")), "mode": "points" if points else "9cat",
                "cats": [YAHOO_STAT.get(_x(s, "display_name"), _x(s, "display_name")) for s in scored], "points": points,
                "scoring": "one_win" if kind == "headone" else "categories",
                **({"max_adds": _n(st, "max_weekly_adds")} if _x(st, "max_weekly_adds") else {}),
                **({"trade_end": _x(st, "trade_end_date")} if _x(st, "trade_end_date") else {}),  # M30's key dates
                **({"playoff_teams": _n(st, "num_playoff_teams"),
                    "playoff_weeks": list(range(first_po, end + 1))} if first_po <= end else {})}
    out = {"name": _x(lg, "name"), "settings": settings, "teams": {}, "records": {}, "adds": {}, "matchups": [],
           "transactions": [], "free_agents": [], "schedule": [], "results": []}  # results: last week's final scores (Q33)
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    first = _n(lg, "start_week") or 1
    prev = _n(lg, "current_week") - 1  # last week: its final scores are fetched once, then kept from the last pull
    kept = [r for r in old.get("results") or () if r["week"] == prev and "totals" in r]
    had = {w["week"] for w in old.get("schedule") or () if w["matchups"]}
    ranks = _ranks(L, ask, fresh)  # the rank pages first: the most calls
    for path in ("teams/roster", "standings", "scoreboard", "transactions;count=50", "transactions;types=trade",
                 "draftresults/players", *[f"scoreboard;week={w}" for w in range(first, min(end + 1, first_po))
                                           if w not in had or w == prev and not kept]):
        ask(f"{L}/{path}")
    ask(f"game/{gk}/game_weeks")

    ranked, pre, last = {}, {}, _last_snapshot()  # Yahoo's current rank: the place in its per-game sort (average_season; plain AR ranks totals, Edwards 242 vs 7)
    for p in last["players"]:
        pre[p["name"]] = p["rank"]["pre"]
    for page in (r.findall(".//player") for r in ranks()):
        for p in page:
            ranked[_x(p, "player_key")] = (len(ranked) + 1, p)
        if len(page) < 25:
            break

    def player(p):
        k, name, tag = _x(p, "player_key"), _x(p, "name/full"), _x(p, "status")
        cur, full = ranked.get(k, (None, p))
        d = {"name": name, "nba": _x(p, "editorial_team_abbr").upper(), "pos": _x(p, "display_position"),
             "status": STATUS.get(tag.lower(), tag)}
        if slot := _x(p, "selected_position/position"):
            d["slot"] = slot
            # IL-eligible on Yahoo: "IL" / "IL+" among his eligible positions (mchc, Nov 24: only O players have it)
            d["il"] = bool({"IL", "IL+"} & {e.text for e in p.findall("eligible_positions/position")})
        # preseason: Yahoo's OR rank (the snapshots' Pre: Edwards 79, LaMelo 24 in both), else the last snapshot's
        orank = next((_n(r, "rank_value") for r in full.iter("player_rank") if _x(r, "rank_type") == "OR"), 0)
        if cur and (orank or name in pre):
            d["rank"] = {"pre": orank or pre[name], "cur": cur}
        # Yahoo-wide roster % (its delta: the change this week) and start % (delta: since yesterday), Y4
        for f, tag in (("owned", "percent_owned"), ("started", "percent_started")):
            for key, sub in ((f, "value"), (f + "_delta", "delta")):
                if re.fullmatch(r"-?[\d.]+", v := _x(full, f"{tag}/{sub}")):
                    d[key] = round(float(v))
        return d

    order, mine = [], ""
    for t in get(f"{L}/teams/roster").findall("league/teams/team"):
        name = _x(t, "name")
        out["teams"][name] = [player(p) for p in t.findall("roster/players/player")]
        out["adds"][name] = _n(t, "roster_adds/value")
        order.append((_n(t, "waiver_priority") or 99, name))
        if _x(t, "is_owned_by_current_login") == "1":
            out["me"], mine = name, _x(t, "team_key")
            ask(f"{L}/transactions;types=pending_trade;team_key={mine}")
    out["waiver_order"] = [n for _, n in sorted(order)]
    for t in get(f"{L}/standings").findall("league/standings/teams/team"):
        s = t.find("team_standings")
        out["records"][_x(t, "name")] = {"w": _n(s, "outcome_totals/wins"), "l": _n(s, "outcome_totals/losses"),
                                         "t": _n(s, "outcome_totals/ties"), "rank": _n(s, "rank")}
    rostered = {_x(p, "player_key") for p in get(f"{L}/teams/roster").iter("player")}
    # free agents and players on waivers, by Yahoo's rank: everyone on the pages nobody rosters now (an hour-old page:
    # not one added since, and one dropped since too, with his positions: Y3, so build() pads nobody)
    for k, (_, p) in ranked.items():
        if k not in rostered:
            d = player(p)
            if w := _x(p, "ownership/waiver_date"):
                d["waiver"] = f"{date.fromisoformat(w):%b} {date.fromisoformat(w).day}"  # "Nov 25", as parse_text's
            out["free_agents"].append(d)

    def board(week=None):
        """One week's matchups: [{teams, totals, games}], and the week's (number, start, end)."""
        ms, wk = [], None
        for m in get(f"{L}/scoreboard" + (f";week={week}" if week else "")).findall("league/scoreboard/matchups/matchup"):
            wk = (_n(m, "week"), _x(m, "week_start"), _x(m, "week_end"))
            pair = {"teams": [], "totals": {}, "games": {}}
            for t in m.findall("teams/team"):
                n = _x(t, "name")
                pair["teams"].append(n)
                pair["totals"][n] = {YAHOO_STAT[k]: float(v) if re.fullmatch(r"[\d.]+", v) else 0.0
                                     for s in t.findall("team_stats/stats/stat")
                                     for k, v in zip(_pair(names.get(_x(s, "stat_id"), "")), _x(s, "value").split("/"))
                                     if k in YAHOO_STAT and "%" not in k}
                g = t.find("team_remaining_games/total")
                if g is not None:
                    pair["games"][n] = {"played": _n(g, "completed_games") + _n(g, "live_games"),
                                        "left": _n(g, "remaining_games")}
            pair["status"], pair["won"] = _x(m, "status"), {}  # Yahoo's own final score: categories each team won
            key = {_x(t, "team_key"): _x(t, "name") for t in m.findall("teams/team")}
            for s in m.findall("stat_winners/stat_winner"):
                if _x(s, "is_tied") != "1" and (n := key.get(_x(s, "winner_team_key"))):
                    pair["won"][n] = pair["won"].get(n, 0) + 1
            ms.append(pair)
        return ms, wk

    out["matchups"], wk = board()
    if wk:
        out["week"] = {"week": wk[0], "start": wk[1], "end": wk[2]}
    if kept:
        out["results"] = kept
    elif prev >= first:  # Y2: Yahoo's final score of last week's matchups, parse_text's results (+ the category totals)
        ms, _ = board(prev)
        out["results"] = ([{"week": prev, "teams": m["teams"], "score": [m["won"].get(t, 0) for t in m["teams"]],
                                    "totals": m["totals"]} for m in ms] if all(m["status"] == "postevent" for m in ms) else [])
    weeks = {_n(w, "week"): (_x(w, "start"), _x(w, "end")) for w in get(f"game/{gk}/game_weeks").iter("game_week")}
    keep = {w["week"]: w for w in old.get("schedule") or () if w["matchups"]} if schedule_ok(old.get("schedule"), out["teams"]) else {}
    for w in range(first, end + 1):  # the season's pairings: fetched once, then kept from the last pull
        if w in keep or w >= first_po:
            out["schedule"].append(keep.get(w) or {"week": w, "start": weeks.get(w, ("", ""))[0],
                                                   "end": weeks.get(w, ("", ""))[1], "matchups": []})
            continue
        ms, wk2 = board(w)
        out["schedule"].append({"week": w, "start": wk2[1] if wk2 else weeks[w][0], "end": wk2[2] if wk2 else weeks[w][1],
                                "matchups": [m["teams"] for m in ms]})

    for tx in get(f"{L}/transactions;count=50").findall("league/transactions/transaction"):
        if _x(tx, "status") != "successful":  # vetoed and pending trades moved nobody
            continue
        when, ps = _when(_n(tx, "timestamp")), tx.findall("players/player")
        moves = [(_x(p, "name/full"), p.find("transaction_data")) for p in ps]
        if _x(tx, "type") == "trade":
            for side in dict.fromkeys(_x(d, "source_team_name") for _, d in moves):
                sent = [n for n, d in moves if _x(d, "source_team_name") == side]
                to = next(_x(d, "destination_team_name") for n, d in moves if _x(d, "source_team_name") == side)
                got = [n for n, d in moves if _x(d, "destination_team_name") == side]
                out["transactions"].append({"when": when, "team": side, "type": "trade",
                                            "player": f"sent {' + '.join(sent)} to {to} (received {' + '.join(got)})"})
            continue
        drops = [n for n, d in moves if _x(d, "type") == "drop"]
        for n, d in moves:
            if _x(d, "type") == "add":
                out["transactions"].append({"when": when, "team": _x(d, "destination_team_name"), "player": n,
                                            "type": "waiver claim" if _x(d, "source_type") == "waivers" else "add",
                                            **({"dropped": drops[0]} if drops else {})})
        if drops and not any(_x(d, "type") == "add" for _, d in moves):
            out["transactions"].append({"when": when, "team": _x(moves[0][1], "source_team_name"), "type": "drop",
                                        "player": drops[0]})

    def trade(tx):
        """A trade as {key, status, ts, from (who proposed), to, give (from's players), get (to's)}."""
        a, b = _x(tx, "trader_team_name"), _x(tx, "tradee_team_name")
        side = lambda team: [_x(p, "name/full") for p in tx.findall("players/player")
                             if _x(p, "transaction_data/source_team_name") == team]
        return {"key": _x(tx, "transaction_key"), "status": _x(tx, "status"), "from": a, "to": b, "give": side(a),
                "get": side(b), "ts": _n(tx, "timestamp") or _n(tx, "trade_proposed_time")}
    # every trade this season (successful or vetoed: both managers said yes) and the offers to or from you still
    # waiting (status "proposed"): app.sync_offers() logs and marks yours under Offers sent, the data M6 fits on
    out["trades"] = [trade(tx) for q in ("types=trade", f"types=pending_trade;team_key={mine}") if mine or q == "types=trade"
                     for tx in get(f"{L}/transactions;{q}").findall("league/transactions/transaction")]
    # where this league drafted each player: the market's starting price (League._market), not Yahoo's preseason rank
    out["draft"] = {n: _n(d, "pick") for d in get(f"{L}/draftresults/players").iter("draft_result")
                    if (n := _x(d, "players/player/name/full"))}

    now = datetime.now().astimezone().replace(second=0, microsecond=0)
    out["pulled"] = now.isoformat()
    if (tipped := _tipped(now)) is None:  # no scoreboard: the snapshots' rule, from tomorrow once 7 PM ET has passed
        et = now.astimezone(ET)
        out["from"] = (et.date() + timedelta(days=1 if et.time() >= time(19) else 0)).isoformat()
    else:  # today still counts: its games that tipped are in Yahoo's totals (held), the later ones still to play
        out["from"], out["held"] = now.date().isoformat(), {"day": now.date().isoformat(), "teams": tipped}
    out["projections"] = last["projections"]
    return out


MAX_RANK = 500  # pages of Yahoo's rank pulled: 20 calls; Nov 23's rosters reach ~300, the free agents listed ~250
RANKS = ROOT / "data" / "yahoo_ranks.json"  # the last rank pages' XML and when (Y2)
RANK_MIN = 60  # rank pages reused this long: ranks and roster % barely move within a day
RANK_OUT = "percent_owned,percent_started,ownership,ranks"  # one call carries all four (Nov 24); a cache saved with
# another list (before Y4: no percent_started) is asked again


def _ranks(L, ask, fresh):
    """The MAX_RANK rank pages (XML roots) as a function that waits for them: from RANKS when it's this league's, asked with RANK_OUT and
    under RANK_MIN minutes old, else asked of Yahoo now (all at once) and saved there. Ownership on an hour-old page
    can be stale: pull() leaves out a free agent who is on a roster now."""
    try:
        c = json.loads(RANKS.read_text(encoding="utf-8"))
        if not fresh and c["league"] == L and c.get("out") == RANK_OUT and datetime.now().timestamp() - c["at"] < RANK_MIN * 60:
            return lambda: [ElementTree.fromstring(x) for x in c["pages"]]
    except (OSError, ValueError, KeyError):
        pass
    got = [ask(f"{L}/players;sort=AR;sort_type=average_season;start={s};count=25;out={RANK_OUT}")
           for s in range(0, MAX_RANK, 25)]

    def wait():
        import model
        pages = [f.result() for f in got]
        model._write(RANKS, json.dumps({"league": L, "out": RANK_OUT, "at": datetime.now().timestamp(),
                                        "pages": [ElementTree.tostring(r, encoding="unicode") for r in pages]}))
        return pages
    return wait


LINEUPS = ROOT / "data" / "yahoo_lineups.json"  # every team's lineup each past day and the season's add / drop log (Y5)


def lineups(league_id=None, get=None, until=None):
    """Y5 (Nov 24): every team's lineup on each past day of the season, and the season's add / drop log, kept in
    LINEUPS. `teams/roster;date=D` gives all the teams' rosters that day with each player's
    selected_position in one call (checked Nov 24: the same as `team/<key>/roster;date=D`), and a past day never
    changes, so only days not in the file yet are asked: 35 calls once (Oct 20 - Nov 23, 3.5 s), then one a day. The
    log (`transactions;types=add,drop`, 100 a call) is asked again with them. Not part of pull(): app.live() calls it
    after its pulls (no call while the file has yesterday), and `python yahoo.py lineups`; model.habits() reads it.
    until: the last day (yesterday). A failed call (a 999: Yahoo's rate limit) keeps the days already in and raises.
    -> {"league", "days": {date: {team: [[name, NBA team, positions, slot], ...]}}, "moves": [{ts, team, add, drop,
    waiver}], "at"}"""
    import model
    d1 = until or date.today() - timedelta(1)
    if get is None:
        try:  # app.live() asks after every pull: nothing new until a day has passed, so no call
            if max((c := json.loads(LINEUPS.read_text(encoding="utf-8")))["days"]) >= d1.isoformat():
                return c
        except (OSError, ValueError, KeyError):
            pass
        get, league_id = _api(league_id)
    lg = get(f"league/{_x(get('game/nba'), 'game/game_key')}.l.{league_id}").find("league")
    L = f"league/{_x(lg, 'league_key')}"
    try:
        c = json.loads(LINEUPS.read_text(encoding="utf-8"))
        c = c if c.get("league") == L else None
    except (OSError, ValueError):
        c = None
    c = c or {"league": L, "days": {}, "moves": []}
    d0 = date.fromisoformat(_x(lg, "start_date"))
    want = [d for d in ((d0 + timedelta(i)).isoformat() for i in range((d1 - d0).days + 1)) if d not in c["days"]]
    with ThreadPoolExecutor(WORKERS) as pool:
        asked = {d: pool.submit(get, f"{L}/teams/roster;date={d}") for d in want}
        try:
            for d, f in asked.items():
                c["days"][d] = {_x(t, "name"): [[_x(p, "name/full"), _x(p, "editorial_team_abbr").upper(), _x(p, "display_position"),
                                                 _x(p, "selected_position/position")] for p in t.findall("roster/players/player")]
                                for t in f.result().iter("team")}
            moves, start = [], 0
            while True:
                txs = get(f"{L}/transactions;types=add,drop;start={start};count=100").findall("league/transactions/transaction")
                for tx in txs:
                    ps = {_x(p, "transaction_data/type"): p for p in tx.findall("players/player")}
                    if _x(tx, "status") == "successful" and ps:
                        a, b = ps.get("add"), ps.get("drop")
                        moves.append({"ts": _n(tx, "timestamp"), "add": _x(a, "name/full") or None, "drop": _x(b, "name/full") or None,
                                      "team": _x(a, "transaction_data/destination_team_name") or _x(b, "transaction_data/source_team_name"),
                                      "waiver": _x(a, "transaction_data/source_type") == "waivers"})
                if len(txs) < 100:
                    break
                start += 100
            c["moves"] = moves
        finally:
            c["days"] = dict(sorted(c["days"].items()))
            c["at"] = datetime.now().isoformat(timespec="minutes")
            model._write(LINEUPS, json.dumps(c, ensure_ascii=False))
    return c


def _last_snapshot():
    """The newest snapshot file's preseason ranks and Yahoo projections, which the API doesn't give."""
    import model
    files = model.snapshots()
    got = parse_text(files[-1].read_text(encoding="utf-8")) if files else {"teams": {}, "free_agents": [], "projections": {}}
    return {"players": [p for p in [*sum(got["teams"].values(), []), *got["free_agents"]] if p.get("rank")],
            "projections": got["projections"]}


# what a pull can change that matters: who's where (slot, injury tag; not rank or roster %, which move all day), moves,
# trades and offers, records, adds, waiver order and this week's totals
NEWS = lambda s: ({t: [(p["name"], p.get("slot"), p.get("status")) for p in ps] for t, ps in s.get("teams", {}).items()},
                  *(s.get(k) for k in ("transactions", "trades", "records", "adds", "waiver_order")),
                  (s.get("matchup") or {}).get("totals"))


def snapshot(league_id=None, write=True, changed=False, me=None, fresh=False):
    """The league from Yahoo's API into data/league.json, and a copy under snapshots/ (one a day: the season's history,
    as the text snapshots were). changed: None, with nothing written, when rosters, moves, trades, records, adds and
    this week's totals are as league.json has them (app.live()'s pulls, so the page only reloads on news); then the
    file's time is touched, so the sidebar sees Yahoo checked. me: the team the app is on (else the login's).
    fresh: the rank pages from Yahoo too, not the hour's saved ones (pull()).
    A failure is noted in model.FAILED for the sidebar, then raised."""
    import model
    try:
        got = pull(league_id, fresh=fresh)
        snap = build(got, me=me if me in got["teams"] else None, write=False)  # a demo team: the login's
    except Exception as e:
        model.FAILED[OUT.name] = f"{type(e).__name__}: {e}"[:200]
        raise
    model.FAILED.pop(OUT.name, None)
    if changed and OUT.exists():
        old = json.loads(OUT.read_text(encoding="utf-8"))
        if NEWS(snap) == NEWS(old):
            os.utime(OUT)
            return None
    if write:
        save(snap)
        model._write(model.SNAPS / f"yahoo-{date.today()}.json", json.dumps(snap, ensure_ascii=False))
    return snap


def check(league_id=None):
    """A pull next to the newest snapshot, section by section, without touching data/league.json; the raw XML goes
    to data/yahoo_raw/. For the first real pull: what doesn't match is a field path to fix in pull()."""
    global RAW
    import model
    RAW = model.DATA / "yahoo_raw"
    shutil.rmtree(RAW, ignore_errors=True)
    api = pull(league_id, fresh=True)
    f = model.snapshots()[-1]
    snap = parse_text(f.read_text(encoding="utf-8"))
    print(f"Yahoo API vs {f.name} (pulled {snap.get('pulled')}); raw XML in {RAW}\n")
    say = lambda what, ok, more="": print(f"{'ok  ' if ok else 'DIFF'} {what}{': ' + more if more and not ok else ''}")
    say("league name", api["name"] == snap["name"], f"{api['name']!r} vs {snap['name']!r}")
    say("my team", api.get("me") == snap.get("me"), f"{api.get('me')!r} vs {snap.get('me')!r}")
    say("teams", set(api["teams"]) == set(snap["teams"]), f"{sorted(api['teams'])} vs {sorted(snap['teams'])}")
    for k in sorted(set(api["settings"]) | set(snap["settings"])):
        if k not in ("mode", "cats", "points") or k in snap["settings"]:
            say(f"settings.{k}", api["settings"].get(k) == snap["settings"].get(k),
                f"{api['settings'].get(k)} vs {snap['settings'].get(k)}")
    print(f"     settings.cats: {api['settings']['cats']}")
    for t in sorted(set(api["teams"]) & set(snap["teams"])):
        a, s = ({p["name"]: p for p in got["teams"][t]} for got in (api, snap))
        miss = sorted(set(a) ^ set(s))
        diff = [f"{n} {f} {a[n].get(f)!r} vs {s[n].get(f)!r}" for n in set(a) & set(s)
                for f in ("slot", "status", "pos", "nba") if a[n].get(f) != s[n].get(f)]
        say(f"{t}: roster", not miss and not diff, "; ".join([f"only on one side: {', '.join(miss)}"] * bool(miss) + diff[:8]))
    stuck = [f"{p['name']} ({t}, {p['status'] or 'no tag'})" for t, ps in api["teams"].items() for p in ps
             if p.get("slot") in ("IL", "IL+") and not p.get("il")]
    print(f"     in an IL slot but not IL-eligible on Yahoo (that team can't add): {', '.join(stuck) or 'nobody'}")
    for k in ("records", "adds"):
        bad = {t: (api[k].get(t), v) for t, v in snap[k].items() if api[k].get(t) != v}
        say(k, not bad, "; ".join(f"{t} {a} vs {s}" for t, (a, s) in bad.items()))
    say("waiver order", api["waiver_order"] == snap["waiver_order"], f"{api['waiver_order']} vs {snap['waiver_order']}")
    say("this week", api.get("week") == snap.get("week"), f"{api.get('week')} vs {snap.get('week')}")
    mine = lambda got: next((m for m in got["matchups"] if got.get("me") in m["teams"]), {})
    say("my matchup totals", mine(api).get("totals") == mine(snap).get("totals"), f"\n  {mine(api)}\n  {mine(snap)}")
    sched = lambda got: [(w["week"], w["start"], w["end"], sorted(map(sorted, w["matchups"]))) for w in got["schedule"]]
    bad = [f"{a} vs {s}" for a, s in zip(sched(api), sched(snap)) if a != s]
    say("season schedule", not bad and len(api["schedule"]) == len(snap["schedule"]), "; ".join(bad[:3]))
    rk = lambda got: {p["name"]: p for p in [*sum(got["teams"].values(), []), *got["free_agents"]]}
    a, s = rk(api), rk(snap)
    both = [n for n in s if n in a and s[n].get("rank") and a[n].get("rank")]
    off = sorted(both, key=lambda n: -abs(a[n]["rank"]["cur"] - s[n]["rank"]["cur"]))
    say(f"current ranks ({len(both)} players in both)", all(abs(a[n]["rank"]["cur"] - s[n]["rank"]["cur"]) <= 5 for n in both),
        ", ".join(f"{n} {a[n]['rank']['cur']} vs {s[n]['rank']['cur']}" for n in off[:6]))
    owned = [n for n in s if n in a and "owned" in s[n]]
    say(f"roster % ({len(owned)} players)", all(abs(a[n].get("owned", -99) - s[n]["owned"]) <= 3 for n in owned),
        ", ".join([f"{n} {a[n].get('owned')} vs {s[n]['owned']}" for n in owned if abs(a[n].get("owned", -99) - s[n]["owned"]) > 3][:6]))
    wv = [n for n in s if s[n].get("waiver")]
    say(f"waiver dates ({len(wv)})", all(a.get(n, {}).get("waiver") == s[n]["waiver"] for n in wv),
        ", ".join(f"{n} {a.get(n, {}).get('waiver')} vs {s[n]['waiver']}" for n in wv if a.get(n, {}).get("waiver") != s[n]["waiver"]))
    res = lambda got: sorted((r["week"], *sorted(zip(r["teams"], r["score"]))) for r in got["results"])
    if snap["results"] and api["results"] and snap["results"][0]["week"] == api["results"][0]["week"]:
        say(f"week {api['results'][0]['week']}'s final scores", res(api) == res(snap), f"{res(api)} vs {res(snap)}")
    print("\nNewest transactions, API then snapshot:")
    for x in api["transactions"][:5] + [None] + snap["transactions"][:5]:
        print("  " + (json.dumps(x, ensure_ascii=False) if x else "--"))
    print(f"\nfree agents {len(api['free_agents'])} (snapshot lists {len(snap['free_agents'])}), from {api['from']}, "
          f"pulled {api['pulled']}; {TOOK[1]} calls in {TOOK[0]:.1f} s")


PLAYER = re.compile(r"(\S+(?: \S+)*?)\s{2,}([A-Z]{2,4}) - ([A-Z,]+)(.*)")  # "Cade Cunningham    DET - PG,SG  [O: Out]"
SLOTS = {"PG", "SG", "G", "SF", "PF", "F", "C", "Util", "BN", "IL", "IL+", "NA"}
WEEK = re.compile(r"Week (\d+): \w{3} (\w{3} \d+) - \w{3} (\w{3} \d+)")  # "Week 2: Mon Oct 26 - Sun Nov 1"
ET = ZoneInfo("America/New_York")
# section heading words -> what its rows are, first match wins; a section nothing matches is skipped
MODES = [("injur", "skip"), ("adds used", "adds"), ("priority", "adds"), ("team roster", "teams"), ("free agent", "fa"),
         ("15%+", "fa"), ("waiver", "fa"), ("matchup", "matchups"), ("transaction", "transactions"),
         ("projection", "projections"), ("settings", "settings"), ("schedule", "schedule")]


def _day(s, y0):
    """"Oct 26" -> "2026-10-26" in the season that starts in October of y0 (January-July dates are in y0 + 1)."""
    y = y0 + (datetime.strptime(s[:3], "%b").month < 8)
    return datetime.strptime(f"{s} {y}", "%b %d %Y").date().isoformat()


def parse_text(text):
    """A league snapshot as text (Instinct's "MCHC FANTASY BASKETBALL - FULL LEAGUE SNAPSHOT"), or a hand-typed list:

        Pulled Monday, October 26, 2026, 11:37 PM MST (America/Creston, UTC-7) ...   Week 2: Mon Oct 26 - Sun Nov 1
        LEAGUE SETTINGS (between ==== lines)         Roster slots: ... (10 starters) + 3 BN + 3 IL = 16
        PART 1 - ALL 12 TEAM ROSTERS                 or   ## Toronto blue jays
        1. Toronto blue jays  (4-5-0, 7th place)          Cade Cunningham (Q)
           C     Ivica Zubac   IND - C  [O: Out]   Pre 53   Cur 189  Ros 94%
        PART 3 - TOP 100 FREE AGENTS / 15%+ / WAIVER WIRE: rows with Pre/Cur ranks, roster %, "W Oct 28" claim dates
        PART 6 matchups (pair line, then each team's FGM/FGA 36/91 ... TO 16 and Games played 7 Games remaining 32),
        PART 7 transactions,
        PART 8 Yahoo projections per game, ADDS USED ... AND WAIVER PRIORITY, SEASON SCHEDULE

    Injury tags O / Q / P / D / NA (or [O: Out]). "Empty slot" rows, the injured list and any section it doesn't
    know (last season, notes) are skipped. Returns a dict: name, teams {team: [player]}, free_agents, records,
    matchups, transactions, projections, adds, waiver_order, schedule, rules (settings lines), settings, results (last
    week's final scores, Part 6's "WEEK 4 FINAL RESULTS" block: [{week, teams, score}]), and when
    the header has them: me, pulled, week, from (first day whose games aren't in the matchup totals yet)."""
    out = {"name": "", "teams": {}, "records": {}, "matchups": [], "transactions": [], "projections": {}, "adds": {},
           "waiver_order": [], "schedule": [], "rules": {}, "results": []}
    teams, fa = out["teams"], {}
    y0 = date.today().year - (date.today().month < 8)
    lines = ["".join(c for c in raw if unicodedata.category(c) != "Co").strip() for raw in text.splitlines()]
    bar = lambda i: 0 <= i < len(lines) and lines[i].startswith("====")
    mode, cur, cols, pair, fin = None, None, [], None, None
    for i, line in enumerate(lines):
        if m := re.search(r'league "([^"]+)"', line) or re.fullmatch(r"# (\S.*)", line):
            out["name"] = out["name"] or m[1]
        head = re.fullmatch(r"(?:PART \d+ - |## )(.+)", line) or (bar(i - 1) and bar(i + 1) and not bar(i)
                                                                    and re.fullmatch(r"(.+)", line))
        if head:
            h = head[1].lower()
            mode = next((md for w, md in MODES if w in h), "list" if line.startswith("## ") else "skip")
            if mode == "list":
                cur = teams.setdefault(head[1].split(" · ")[0].strip(), [])
            cols = []
            continue
        if mode is None:  # header lines before the first section
            if m := re.match(r"Pulled \w+, (\w+ \d+, \d{4}), ~?(\d+:\d+ [AP]M)(?:.*?UTC([+-]\d+))?", line):
                at = datetime.strptime(f"{m[1]} {m[2]}", "%B %d, %Y %I:%M %p").replace(
                    tzinfo=timezone(timedelta(hours=int(m[3]))) if m[3] else ET)
                out["pulled"], y0 = at.isoformat(), at.year - (at.month < 8)
                # games under way at pull time are in the totals, so the rest of the week starts the next day.
                # "No Oct 27 games had started ... earlier games were final" / "No games were in progress ... All
                # Oct 31 games were final": today still counts (a "final" only counts for today's games, Nov 1).
                # "A game was in progress ... totals are frozen at the pre-game baseline" (Nov 2): today isn't in the
                # totals, so it still counts.
                # ponytail: 7 PM ET is the usual first tip-off; weekend matinees start earlier
                started = not re.search(r"\bpre-?game\b", line, re.I) and not re.search(
                    r"\bno\b[^.;]*\b(started|in progress|under ?way)", line, re.I) and (
                    bool(re.search(r"\b(started|in progress|under ?way)\b", line, re.I))
                    or bool(re.search(rf"\b{at:%b} {at.day}\b[^.;]*\bfinal", line))
                    or at >= datetime.combine(at.date(), time(19), ET))
                out["from"] = (at.date() + timedelta(days=1 if started else 0)).isoformat()
            elif m := re.match(r"My team: (.+)", line):
                out["me"] = m[1]
            elif m := WEEK.match(line):
                out["week"] = {"week": int(m[1]), "start": _day(m[2], y0), "end": _day(m[3], y0)}
            continue
        if mode == "skip" or "Empty slot" in line or not re.search(r"[A-Za-z]", line):
            continue
        if mode == "settings":
            if m := re.match(r"([^:]+):\s+(.+)", line):
                out["rules"][m[1]] = m[2]
        elif mode == "teams" and (m := re.fullmatch(r"\d+\.\s+(.+?)\s+\((.*)\)", line)):
            cur = teams.setdefault(m[1], [])
            out["records"][m[1]] = dict(zip(("w", "l", "t", "rank"), map(int, re.findall(r"\d+", m[2]))))
        elif mode == "matchups":
            if m := re.match(r"WEEK (\d+) FINAL RESULTS", line):  # last week's scores (Q33): "fatty zhou 7 - Wesburn incident 2"
                fin = int(m[1])
            elif not pair and fin and (m := re.fullmatch(r"(.+?) (\d+) - (.+?) (\d+)", line)) and {
                    m[1].lower(), m[3].lower()} <= {t.lower() for t in teams}:
                out["results"].append({"week": fin, "teams": [m[1], m[3]], "score": [int(m[2]), int(m[4])]})
            elif (m := re.fullmatch(r"(.+?) vs (.+)", line)) and m[1] in teams and m[2] in teams:
                pair = {"teams": [m[1], m[2]], "totals": {}}
                out["matchups"].append(pair)
            elif pair and (t := next((t for t in pair["teams"] if line.startswith(t + " ")), None)):
                pair["totals"][t] = {YAHOO_STAT[k]: float(v) for ks, vs in re.findall(r"(\S+) ([\d./]+)", line[len(t):])
                                     for k, v in zip(ks.split("/"), vs.split("/")) if k in YAHOO_STAT and "%" not in k}
            elif pair and pair["totals"] and (m := re.match(r"Games played (\d+)\s+Games remaining (\d+)", line)):
                pair.setdefault("games", {})[list(pair["totals"])[-1]] = {"played": int(m[1]), "left": int(m[2])}
        elif mode == "transactions":  # a long team name can fill its column, so match the names, not the spacing
            m = re.match(r"(\w{3} \d+ \d+:\d+[ap]m)\s+(.+)", line)
            if m and (t := max((t for t in teams if m[2].startswith(t + " ")), key=len, default=None)):
                kind, what = (re.split(r"\s{2,}", m[2][len(t):].strip(), maxsplit=1) + [""])[:2]
                d = re.fullmatch(r"(.*?)(?: \(dropped (.+)\))?", what)
                out["transactions"].append({"when": m[1], "team": t, "type": kind, "player": d[1],
                                            **({"dropped": d[2]} if d[2] else {})})
        elif mode == "adds":
            if (m := re.fullmatch(r"(.+?)\s+(\d+)/\d+", line)) and m[1] in teams:
                out["adds"][m[1]] = int(m[2])
            elif (m := re.fullmatch(r"\d+\s+(.+)", line)) and m[1] in teams:
                out["waiver_order"].append(m[1])
        elif mode == "schedule":
            if m := WEEK.match(line):
                out["schedule"].append({"week": int(m[1]), "start": _day(m[2], y0), "end": _day(m[3], y0), "matchups": []})
            elif out["schedule"] and (m := re.fullmatch(r"(.+?) vs (.+)", line)) and m[1] in teams and m[2] in teams:
                out["schedule"][-1]["matchups"].append([m[1], m[2]])
        elif "Team - Pos" in line:  # column headers: "#  Blend  Pre  Cur  Player  Team - Pos  Inj  %Ros"
            w = line.split()
            cols = w[w.index("Pos") + 1:] if mode == "projections" else w[:w.index("Player")]
        elif mode == "projections":
            if (m := PLAYER.search(line)) and len(vals := m[4].split()) == len(cols):  # rookies: "no projection shown"
                out["projections"][m[1]] = {YAHOO_STAT[c]: float(v) for c, v in zip(cols, vals) if c in YAHOO_STAT and v != "-"}
        elif m := PLAYER.search(line):  # a player on a roster or a free-agent list
            tag = re.match(r"\s+(?:\[)?(O|Q|P|D|NA|GTD|DTD|INJ|SUSP)\b", m[4])
            p = {"name": m[1], "nba": m[2], "pos": m[3], "status": STATUS.get(tag[1].lower(), tag[1]) if tag else ""}
            left = dict(zip(cols[::-1], line[:m.start(1)].split()[::-1]))  # FA columns before the name, right-aligned
            r = re.search(r"Pre (\d+)\s+Cur (\d+)", m[4])  # roster rows: "Pre 5  Cur 41  Ros 100%"
            pre, now = (r[1], r[2]) if r else (left.get("Pre", ""), left.get("Cur", ""))
            extra = {"rank": {"pre": int(pre), "cur": int(now)}} if pre.isdigit() and now.isdigit() else {}
            if pct := re.search(r"(\d+)%", line):
                extra["owned"] = int(pct[1])
            if mode in ("teams", "list") and cur is not None:
                slot = line.split()[0]
                cur.append({**p, **({"slot": slot} if slot in SLOTS else {}), **extra})
            elif mode == "fa":
                w = re.search(r"(?:^|W )([A-Z][a-z]{2} \d{1,2})\b", line)
                fa.setdefault(p["name"], p).update(extra, **({"waiver": w[1]} if w else {}))
        elif mode == "list" and cur is not None and len(line.split()) <= 4 and not re.search(r"\d", line):
            m = re.fullmatch(r"(.+?)\s*\((\S+)\)", line)  # hand-typed "Name" or "Name (O)"
            cur.append({"name": m[1] if m else line, "pos": "", "nba": "",
                        "status": STATUS.get(m[2].lower(), m[2].upper()) if m else ""})
    rules = out["rules"]
    slots = re.search(r"\((\d+) starters\) \+ (\d+) BN(?: \+ (\d+) IL)?", rules.get("Roster slots", ""))  # roster: active
    adds = re.match(r"\d+", rules.get("Max adds/week", ""))
    seats = re.match(r"(\d+) teams", rules.get("Playoffs", ""))
    scoring = next((k for k, w in (("categories", "categor"), ("one_win", "one win")) if w in rules.get("Scoring type", "").lower()), None)
    po = re.match(r"[^(]*?Weeks? ([\d, and]+)", rules.get("Playoffs", ""))  # "8 teams, Weeks 21, 22 and 23 (Mon Mar 22 ..."
    deadline = re.match(r"([A-Z][a-z]+ \d{1,2}, \d{4})", rules.get("Trade deadline", ""))  # "March 4, 2027 (Yahoo ..."
    pos = [x.strip() for x in rules.get("Roster slots", "").split("(")[0].split(",") if x.strip()]  # starting slots
    out["settings"] = {**({"roster": int(slots[1]) + int(slots[2]), "starters": int(slots[1]),
                           **({"slots": pos} if len(pos) == int(slots[1]) else {}),
                           **({"il": int(slots[3])} if slots[3] else {})} if slots else {}),
                       **({"scoring": scoring} if scoring else {}), **({"max_adds": int(adds[0])} if adds else {}),
                       **({"playoff_teams": int(seats[1])} if seats else {}),
                       **({"playoff_weeks": [int(x) for x in re.findall(r"\d+", po[1])]} if po else {}),
                       **({"trade_end": datetime.strptime(deadline[1], "%B %d, %Y").date().isoformat()} if deadline else {})}
    out["free_agents"] = list(fa.values())
    return out


def from_text(path, me=None, write=True):
    """league.json from a league text file (parse_text). me defaults to the file's "My team:" line.
    write=False: the league only, data/league.json untouched (backtest.py lineups)."""
    return build(parse_text(Path(path).read_text(encoding="utf-8")), me, text=str(path), write=write)


def build(got, me=None, text=None, write=True):
    """league.json from parse_text's dict or pull()'s (text: the snapshot file it came from, None for the API). Free
    agents are the ones listed (with roster % and claim dates) plus the best players nobody rosters, since anyone
    not on a roster is available. Padded ones have no positions (model.positions() fills them for a text snapshot);
    an API pull lists everyone who's played and isn't rostered, so it pads nobody (Nov 24: 0 of 328)."""
    import model
    path = text or "the Yahoo pull"
    teams = got["teams"]
    me = next((t for t in teams if t.lower() == (me or got.get("me", "")).lower()), None)
    if not me:
        raise SystemExit(f"No team called that. Teams: {', '.join(teams)}")
    line, val = _pool()
    keep = ("pulled", "records", "matchups", "transactions", "projections", "adds", "waiver_order", "schedule", "rules",
            "trades", "draft", "results")
    snap = {"name": got["name"] or (Path(text).stem if text else "Yahoo league"), "me": me, "teams": teams,
            **({"text": text} if text else {"source": "yahoo"}), "free_agents": [],
            "settings": {"teams": len(teams), "roster": 13, "mode": "9cat", "cats": model.CATS, **got["settings"]},
            **{k: got[k] for k in keep if got.get(k)}}
    lg = model.League(snap)  # matches names the way the app will ("N. Alexander-Walker", accents, Jr.)
    if missed := [p["name"] for ps in teams.values() for p in ps if not lg.find(p["name"], strict=False)]:
        print(f"Left out (no NBA stats yet, or a name to fix in {path}): {', '.join(missed)}")
    taken = {k for ks in lg.rosters.values() for k in ks}
    listed = {lg.find(p["name"], strict=False) for p in got["free_agents"]}
    best = [k for k in val.sort_values(ascending=False).index if k not in taken and k not in listed][:100]
    snap["free_agents"] = got["free_agents"] + [{"name": line.at[k, "PLAYER_NAME"], "pos": "", "status": "",
                                                 "nba": line.at[k, "TEAM_ABBREVIATION"]} for k in best]
    mine = next((x for x in got["matchups"] if me in x["teams"]), None)
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    if "draft" not in snap and old.get("draft"):  # a snapshot file has no draft: the last API pull's stays
        snap["draft"] = old["draft"]
    if not schedule_ok(snap.get("schedule"), teams):  # Nov 1's file had your pairing 6 times a week: playoff odds of
        snap["schedule"] = old.get("schedule") if schedule_ok(old.get("schedule"), teams) else []  # 12% for the 3rd best
        print(f"The season schedule in {path} has a team twice in a week (or a team not in the league), so "
              + ("the last import's is kept." if snap["schedule"] else "it's left out (no playoff odds)."))
    if mine and got.get("week"):
        snap["matchup"] = week_of(got, snap.get("schedule", ()), me, old)
    elif old.get("matchup") and old.get("me") == me:  # the opponent picked in the app survives a re-import
        snap["matchup"] = old["matchup"]
    return save(snap) if write else snap


def week_of(got, schedule, me, old):
    """This week's matchup for team `me` from a pull or snapshot's matchups. The season schedule's dates for the week
    number win: weeks 7 and 17 run 14 days as one matchup (4 adds in all), so a header showing only its second 7 days
    would reset the adds count mid-matchup. An opponent picked in the app (old league.json: same team, same week)
    stays, with his own score so far from his own matchup (Nov 23: API pulls every 15 minutes reset it)."""
    mine = next(x for x in got["matchups"] if me in x["teams"])
    wk = next((w for w in schedule if w["week"] == got["week"]["week"]), got["week"])
    opp, o = next(t for t in mine["teams"] if t != me), old.get("matchup") or {}
    if old.get("me") == me and o.get("week_end") == wk["end"] and o.get("opponent") in got["teams"] and o["opponent"] != me:
        opp = o["opponent"]
    theirs = next((x for x in got["matchups"] if opp in x["teams"]), mine)
    games = {me: mine.get("games", {}).get(me), opp: theirs.get("games", {}).get(opp)}
    return {"opponent": opp, "week_start": wk["start"], "week_end": wk["end"],
            "totals": {me: mine["totals"].get(me, {}), opp: theirs["totals"].get(opp, {})},
            **({"games": games} if None not in games.values() else {}), **({"from": got["from"]} if got.get("from") else {}),
            **({"held": got["held"]} if got.get("held") else {})}


def schedule_ok(sched, teams):
    """A season schedule (parse_text's) can be real: each week every team plays at most once, and only the league's."""
    return all(len(ts := [t for pr in w["matchups"] for t in pr]) == len(set(ts)) and set(ts) <= set(teams)
               for w in sched or ())


def _pool():
    """ROS line and z-value of everyone who's played this season."""
    import model
    base, cur = model.baseline(model.fetch(model.LAST), model.fetch(model.OLDER)), model.fetch(model.CUR, max_age_hours=12)
    line = model.ros(base, cur)
    # this season's team: ros() keeps the baseline's, which put 22 free agents on last season's team (Oct 30)
    line = line[line.index.isin(cur.index)].assign(TEAM_ABBREVIATION=cur.TEAM_ABBREVIATION)
    return line, model.zscores(line, 156).sum(axis=1)


def demo(seed=7):
    """12 teams snake-drafted (with some randomness) from players who've played this season."""
    import model
    line, val = _pool()
    rng = random.Random(seed)
    order = sorted(line.index, key=lambda k: -(val[k] + rng.gauss(0, 1.2)))
    names = ["You", "Ball Don't Lie", "Brunson Burner", "Wemby's World", "Tatum Tots", "Dame Time",
             "Trust the Process", "Splash Bros", "Cade Runners", "Point God", "Glass Cleaners", "Kawhi Not"]
    teams = {n: [] for n in names}
    for i, k in enumerate(order[:12 * 13]):
        rnd, pick = divmod(i, 12)
        teams[names[pick if rnd % 2 == 0 else 11 - pick]].append(k)
    as_player = lambda k: {"name": line.at[k, "PLAYER_NAME"], "pos": "", "status": "",
                           "nba": line.at[k, "TEAM_ABBREVIATION"]}
    fa = sorted(order[12 * 13:], key=lambda k: -val[k])
    # a few weeks of waiver moves: every team swaps its worst player for the best free agent while it's an upgrade,
    # so free agents end up near 13th-man level like a real league (otherwise trades that free a roster spot look great)
    for t in names * 3:
        worst = min(teams[t], key=val.get)
        if val[fa[0]] > val[worst]:
            teams[t] = [k for k in teams[t] if k != worst] + [fa.pop(0)]
            fa = sorted(fa + [worst], key=lambda k: -val[k])
    fa = fa[:100]
    return save({"name": "Demo League", "demo": True, "me": "You",
                 "teams": {t: [as_player(k) for k in ks] for t, ks in teams.items()},
                 "free_agents": [as_player(k) for k in fa],
                 "settings": {"teams": 12, "roster": 13, "mode": "9cat", "cats": model.CATS}})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if a[:1] == ["check"]:
        raise SystemExit(check(*a[1:2]))
    if a[:1] == ["lineups"]:
        c = lineups()
        raise SystemExit(print(f"Saved {LINEUPS}: {len(c['days'])} days ({min(c['days'])} to {max(c['days'])}), {len(c['moves'])} adds / drops"))
    snap = demo() if a == ["demo"] else from_text(*a[1:3]) if a[:1] == ["import"] else snapshot(*a)
    print(f"Saved {OUT}: {len(snap['teams'])} teams, {len(snap['free_agents'])} free agents, you = {snap['me']}"
          + (f"; {TOOK[1]} Yahoo calls in {TOOK[0]:.1f} s" if TOOK else ""))
