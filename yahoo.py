"""Pull your Yahoo league into data/league.json (settings, every roster, top free agents). Read-only.

  python yahoo.py            # uses YAHOO_LEAGUE_ID from .env
  python yahoo.py demo       # fake 12-team league drafted from real stats, no Yahoo needed
  python yahoo.py import snapshot.txt "My team"   # league from a text snapshot, no Yahoo API (format: parse_text)

One-time setup: see README.md -> "Connect Yahoo".
"""
import json
import os
import random
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

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

    return save({"name": text(q.get_league_info().name), "me": me, "teams": teams, "free_agents": fa,
                 "settings": {"teams": len(teams), "roster": roster, "mode": "points" if points else "9cat",
                              "cats": list(by_id.values()), "points": points}})


PLAYER = re.compile(r"(\S+(?: \S+)*?)\s{2,}([A-Z]{2,4}) - ([A-Z,]+)(.*)")  # "Cade Cunningham    DET - PG,SG  [O: Out]"
SLOTS = {"PG", "SG", "G", "SF", "PF", "F", "C", "Util", "BN", "IL", "IL+", "NA"}


def parse_text(text):
    """A league snapshot as text (e.g. Instinct's "MCHC FANTASY BASKETBALL - FULL LEAGUE SNAPSHOT"), or a hand-typed list:

        1. Toronto blue jays  (4-5-0, 7th place)        or   ## Toronto blue jays
           PG    Cade Cunningham    DET - PG,SG                Cade Cunningham (Q)
           C     Ivica Zubac        IND - C  [O: Out]
        PART 3 - TOP 75 FREE AGENTS ...                 or   ## Free agents
        1   117   135   98    Daniel Gafford    DAL - C           34%

    Injury tags O / Q / P / D / NA (or [O: Out]); % = Yahoo roster %; "W Oct 28" = on waivers until then.
    Sections about injured players are skipped (those players are already on the rosters).
    Returns (league name, {team: [player]}, [free agent])."""
    name, teams, fa, cur, mode = "", {}, {}, None, "teams"
    for raw in text.splitlines():
        line = "".join(c for c in raw if unicodedata.category(c) != "Co").strip()  # some exports add invisible glyphs
        if m := re.search(r'league "([^"]+)"', line) or re.fullmatch(r"# (.+)", line):
            name = name or m[1]
        head = re.fullmatch(r"(?:PART \d+ - |## )(.+)", line)
        if head:  # section heading: teams, injured (skip), or free agents / waivers
            h = head[1].lower()
            mode = "skip" if "injur" in h else "teams" if "roster" in h and "%" not in h else "fa" if (
                "free" in h or "waiver" in h or "%" in h) else "teams"
            if line.startswith("## ") and mode == "teams":
                cur = teams.setdefault(head[1].split(" · ")[0].strip(), [])
            continue
        if mode == "teams" and (m := re.fullmatch(r"\d+\.\s+(.+?)\s+\(\d+-\d+.*", line)):
            cur = teams.setdefault(m[1], [])
            continue
        if mode == "skip" or not re.search(r"[A-Za-z]", line):
            continue
        if m := PLAYER.search(line):
            tag = re.match(r"\s+(?:\[)?(O|Q|P|D|NA|GTD|DTD|INJ|SUSP)\b", m[4])
            p = {"name": m[1], "nba": m[2], "pos": m[3], "status": STATUS.get(tag[1].lower(), tag[1]) if tag else ""}
            if mode == "teams" and cur is not None:
                slot = line.split()[0]
                cur.append({**p, **({"slot": slot} if slot in SLOTS else {})})
            elif mode == "fa":
                pct, w = re.search(r"(\d+)%", line), re.search(r"(?:^|W )([A-Z][a-z]{2} \d{1,2})\b", line)
                fa.setdefault(p["name"], p).update({"owned": int(pct[1])} if pct else {}, **({"waiver": w[1]} if w else {}))
        elif mode == "teams" and cur is not None and len(line.split()) <= 4 and not re.search(r"\d", line):
            m = re.fullmatch(r"(.+?)\s*\((\S+)\)", line)  # hand-typed "Name" or "Name (O)"
            cur.append({"name": m[1] if m else line, "pos": "", "nba": "",
                        "status": STATUS.get(m[2].lower(), m[2].upper()) if m else ""})
    return name, teams, list(fa.values())


def from_text(path, me):
    """league.json from a league text file (parse_text). Free agents come from the file when it lists them,
    otherwise they're the best players nobody rosters (then players on waivers count as free)."""
    import model
    name, teams, listed = parse_text(Path(path).read_text(encoding="utf-8"))
    me = next((t for t in teams if t.lower() == me.lower()), None)
    if not me:
        raise SystemExit(f"No team called that. Teams: {', '.join(teams)}")
    line, val = _pool()
    roster = max(sum(p.get("slot") not in BENCH_ONLY for p in ps) for ps in teams.values())
    snap = {"name": name or Path(path).stem, "me": me, "teams": teams, "text": str(path), "free_agents": [],
            "settings": {"teams": len(teams), "roster": roster, "mode": "9cat", "cats": model.CATS}}
    lg = model.League(snap)  # matches names the way the app will ("N. Alexander-Walker", accents, Jr.)
    if missed := [p["name"] for ps in teams.values() for p in ps if not lg.find(p["name"], strict=False)]:
        print(f"Left out (no NBA stats yet, or a name to fix in {path}): {', '.join(missed)}")
    taken = {k for ks in lg.rosters.values() for k in ks}
    fa = [k for k in val.sort_values(ascending=False).index if k not in taken][:100]
    snap["free_agents"] = listed or [{"name": line.at[k, "PLAYER_NAME"], "pos": "", "status": "",
                                      "nba": line.at[k, "TEAM_ABBREVIATION"]} for k in fa]
    return save(snap)


def _pool():
    """ROS line and z-value of everyone who's played this season."""
    import model
    base, cur = model.fetch(model.LAST), model.fetch(model.CUR, max_age_hours=12)
    line = model.ros(base, cur)
    line = line[line.index.isin(cur.index)]
    return line, model.zscores(line, 156).sum(axis=1)


def demo(seed=7):
    """12 teams snake-drafted (with some randomness) from players who've played this season."""
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
