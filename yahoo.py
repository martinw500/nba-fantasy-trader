"""Pull your Yahoo league into data/league.json (settings, every roster, top free agents). Read-only.

  python yahoo.py            # uses YAHOO_LEAGUE_ID from .env
  python yahoo.py demo       # fake 12-team league drafted from real stats, no Yahoo needed

One-time setup: see README.md -> "Connect Yahoo".
"""
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "league.json"
YAHOO_STAT = {"3PTM": "FG3M", "ST": "STL", "TO": "TOV", "FG%": "FG%", "FT%": "FT%", "PTS": "PTS", "REB": "REB",
              "AST": "AST", "BLK": "BLK", "FGM": "FGM", "FGA": "FGA", "FTM": "FTM", "FTA": "FTA"}
BENCH_ONLY = {"IL", "IL+", "NA"}


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


def demo(seed=7):
    """12 teams snake-drafted (with some randomness) from players who've played this season."""
    import model
    base, cur = model.fetch(model.LAST), model.fetch(model.CUR, max_age_hours=12)
    line = model.ros(base, cur)
    line = line[line.index.isin(cur.index)]
    val = model.zscores(line, 156).sum(axis=1)
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
    fa = sorted(order[12 * 13:], key=lambda k: -val[k])[:100]
    return save({"name": "Demo League", "demo": True, "me": "You",
                 "teams": {t: [as_player(k) for k in ks] for t, ks in teams.items()},
                 "free_agents": [as_player(k) for k in fa],
                 "settings": {"teams": 12, "roster": 13, "mode": "9cat", "cats": model.CATS}})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    snap = demo() if sys.argv[1:] == ["demo"] else snapshot(*sys.argv[1:])
    print(f"Saved {OUT}: {len(snap['teams'])} teams, {len(snap['free_agents'])} free agents, you = {snap['me']}")
