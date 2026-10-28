"""Pull your Yahoo league into data/league.json (settings, every roster, top free agents). Read-only.

  python yahoo.py            # uses YAHOO_LEAGUE_ID from .env
  python yahoo.py demo       # fake 12-team league drafted from real stats, no Yahoo needed
  python yahoo.py import snapshot.txt ["My team"]   # league from a text snapshot, no Yahoo API (format: parse_text)

One-time setup: see README.md -> "Connect Yahoo".
"""
import json
import os
import random
import re
import sys
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "league.json"
YAHOO_STAT = {"3PTM": "FG3M", "ST": "STL", "TO": "TOV", "FG%": "FG%", "FT%": "FT%", "PTS": "PTS", "REB": "REB",
              "AST": "AST", "BLK": "BLK", "FGM": "FGM", "FGA": "FGA", "FTM": "FTM", "FTA": "FTA"}
BENCH_ONLY = {"IL", "IL+", "NA"}
STATUS = {"out": "O", "o": "O", "questionable": "GTD", "q": "GTD", "gtd": "GTD", "dtd": "DTD", "d": "DTD", "day-to-day": "DTD",
          "probable": "", "p": "", "inj": "INJ", "il": "IL", "il+": "IL+", "susp": "SUSP", "na": "NA"}


def save(snap):
    OUT.parent.mkdir(exist_ok=True)
    snap["fetched"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    OUT.write_text(json.dumps(snap, indent=1, ensure_ascii=False), encoding="utf-8")
    return snap


def text(v):
    return v.decode() if isinstance(v, bytes) else str(v)


def pl(p):
    p = p.get("player", p) if isinstance(p, dict) else p
    return {"name": p.full_name, "pos": p.display_position, "status": p.status or "",
            "nba": p.editorial_team_abbr, "slot": getattr(p, "selected_position_value", "")}


def snapshot(league_id=None):
    from dotenv import load_dotenv
    from yfpy.query import YahooFantasySportsQuery

    load_dotenv(ROOT / ".env")
    league_id = league_id or os.environ["YAHOO_LEAGUE_ID"]
    q = YahooFantasySportsQuery(league_id, game_code="nba", env_file_location=ROOT, save_token_data_to_env_file=True)
    s = q.get_league_settings()
    cats_raw = [st for st in s.stat_categories.stats if not st.is_only_display_stat]
    by_id = {st.stat_id: YAHOO_STAT.get(st.display_name, st.display_name) for st in cats_raw}
    points = None
    if "point" in (s.scoring_type or ""):
        points = {by_id[m.stat_id]: m.value for m in s.stat_modifiers.stats if m.stat_id in by_id}
    roster = sum(rp.count for rp in s.roster_positions if rp.position not in BENCH_ONLY)

    teams, me = {}, None
    for t in q.get_league_teams():
        name = text(t.name)
        teams[name] = [pl(p) for p in q.get_team_roster_player_info_by_week(t.team_id)]
        if t.is_owned_by_current_login:
            me = name

    fa = []
    for start in range(0, 100, 25):  # top 100 available players by Yahoo's actual rank
        got = q.query(f"https://fantasysports.yahooapis.com/fantasy/v2/league/{q.get_league_key()}/players;"
                      f"status=A;sort=AR;start={start};count=25", ["league", "players"])
        fa += [pl(p) for p in (got if isinstance(got, list) else [got])]

    snap = {"name": text(q.get_league_info().name), "me": me, "teams": teams, "free_agents": fa,
            "settings": {"teams": len(teams), "roster": roster, "mode": "points" if points else "9cat",
                         "cats": list(by_id.values()), "points": points,
                         "scoring": "one_win" if s.scoring_type == "headone" else "categories"}}  # "head" = categories
    try:  # this week's opponent and both teams' totals so far. ponytail: unverified yfpy shapes, like the rest
        names = {st.stat_id: st.display_name for st in s.stat_categories.stats}  # incl. display-only "FGM/FGA"
        unwrap = lambda x, k: x.get(k, x) if isinstance(x, dict) else x
        for m in q.get_league_scoreboard_by_week(q.get_league_metadata().current_week).matchups:
            m = unwrap(m, "matchup")
            sides = {text(t.name): t for t in (unwrap(t, "team") for t in m.teams)}
            if me in sides:
                totals = {}
                for n, t in sides.items():
                    for st in (unwrap(x, "stat") for x in t.team_stats.stats):
                        for k, v in zip(names.get(st.stat_id, "").split("/"), str(st.value or "").split("/")):
                            if YAHOO_STAT.get(k) and k not in ("FG%", "FT%"):
                                totals.setdefault(n, {})[YAHOO_STAT[k]] = float(v) if re.fullmatch(r"[\d.]+", v) else 0.0
                snap["matchup"] = {"opponent": next(n for n in sides if n != me), "week_start": m.week_start,
                                   "week_end": m.week_end, "totals": totals}
    except Exception as e:
        print(f"No matchup this week ({e})", file=sys.stderr)
    return save(snap)


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
    matchups, transactions, projections, adds, waiver_order, schedule, rules (settings lines), settings, and when
    the header has them: me, pulled, week, from (first day whose games aren't in the matchup totals yet)."""
    out = {"name": "", "teams": {}, "records": {}, "matchups": [], "transactions": [], "projections": {}, "adds": {},
           "waiver_order": [], "schedule": [], "rules": {}}
    teams, fa = out["teams"], {}
    y0 = date.today().year - (date.today().month < 8)
    lines = ["".join(c for c in raw if unicodedata.category(c) != "Co").strip() for raw in text.splitlines()]
    bar = lambda i: 0 <= i < len(lines) and lines[i].startswith("====")
    mode, cur, cols, pair = None, None, [], None
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
                # "No Oct 27 games had started ... earlier games were final": today still counts.
                # ponytail: 7 PM ET is the usual first tip-off; weekend matinees start earlier
                started = not re.search(r"\bno\b[^.;]*\bstarted", line, re.I) and (
                    "final" in line or at >= datetime.combine(at.date(), time(19), ET))
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
            if (m := re.fullmatch(r"(.+?) vs (.+)", line)) and m[1] in teams and m[2] in teams:
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
    out["settings"] = {**({"roster": int(slots[1]) + int(slots[2]), "starters": int(slots[1]),
                           **({"il": int(slots[3])} if slots[3] else {})} if slots else {}),
                       **({"scoring": scoring} if scoring else {}), **({"max_adds": int(adds[0])} if adds else {}),
                       **({"playoff_teams": int(seats[1])} if seats else {}),
                       **({"playoff_weeks": [int(x) for x in re.findall(r"\d+", po[1])]} if po else {})}
    out["free_agents"] = list(fa.values())
    return out


def from_text(path, me=None):
    """league.json from a league text file (parse_text). me defaults to the file's "My team:" line. Free agents are
    the ones the file lists (with roster % and claim dates) plus the best players nobody rosters, since anyone not
    on a roster is available."""
    import model
    got = parse_text(Path(path).read_text(encoding="utf-8"))
    teams = got["teams"]
    me = next((t for t in teams if t.lower() == (me or got.get("me", "")).lower()), None)
    if not me:
        raise SystemExit(f"No team called that. Teams: {', '.join(teams)}")
    line, val = _pool()
    keep = ("pulled", "records", "matchups", "transactions", "projections", "adds", "waiver_order", "schedule", "rules")
    snap = {"name": got["name"] or Path(path).stem, "me": me, "teams": teams, "text": str(path), "free_agents": [],
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
    if mine and got.get("week"):
        snap["matchup"] = {"opponent": next(t for t in mine["teams"] if t != me), "week_start": got["week"]["start"],
                           "week_end": got["week"]["end"], "totals": mine["totals"],
                           **({"games": mine["games"]} if mine.get("games") else {}),
                           **({"from": got["from"]} if got.get("from") else {})}
    elif old.get("matchup") and old.get("me") == me:  # the opponent picked in the app survives a re-import
        snap["matchup"] = old["matchup"]
    return save(snap)


def _pool():
    """ROS line and z-value of everyone who's played this season."""
    import model
    base, cur = model.baseline(model.fetch(model.LAST), model.fetch(model.OLDER)), model.fetch(model.CUR, max_age_hours=12)
    line = model.ros(base, cur)
    line = line[line.index.isin(cur.index)]
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
    snap = demo() if a == ["demo"] else from_text(*a[1:3]) if a[:1] == ["import"] else snapshot(*a)
    print(f"Saved {OUT}: {len(snap['teams'])} teams, {len(snap['free_agents'])} free agents, you = {snap['me']}")
