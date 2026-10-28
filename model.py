"""Fantasy basketball engine: player values, team outlooks, trades, waivers, sell-high/buy-low.

Values are per game, rest of season (ROS): expert projections (Yahoo's, ESPN's) blended with the last two seasons,
shrunk toward this season as games pile up. 9-cat uses z-scores vs the rostered pool; points uses the league's
scoring weights.

9-cat decisions (trades, waivers, standings) use a matchup model on top of that: each team's expected weekly
totals -> odds of winning each category vs every other team -> what the week is worth in the standings: the share
of categories won (Head-to-Head Categories, each one counts) or the odds of winning the matchup, 5 of 9 (One Win).
A move is judged by how much it changes that win %, so a category you already dominate or can't win
stops mattering on its own (punting emerges), and the other teams' rosters are part of every answer.

CLI (prints JSON, used by Claude Code and for quick checks):
  python model.py team                      # win %, projected standings, category odds, punt suggestions
  python model.py trade --give "A" --get "B, C"
  python model.py ideas --mode finesse      # fair | finesse
  python model.py search --get "Wembanyama" --tier easy --lock "Jokic"   # offers for a target, see --help
                                            # every trade and search result is priced on the market too
  python model.py waivers                   # trades and search are scored on top of the free moves it lists
  python model.py movers                    # sell-high / buy-low
  python model.py week --vs "Team"          # this week's matchup (--vs defaults to the snapshot's opponent)
  python model.py strategy                  # punt planner: every build's waiver adds + up to 3 trades, true odds (~15s)
  python model.py news                      # ESPN news on your players and their teammates since the snapshot
  add --punt "FT%,TOV" to any of them
"""
import argparse
import collections
import copy
import difflib
import functools
import itertools
import json
import re
import sys
import threading
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OLDER, LAST, CUR = "2024-25", "2025-26", "2026-27"
CATS = ["FG%", "FT%", "FG3M", "PTS", "REB", "AST", "STL", "BLK", "TOV"]
COUNTING = CATS[2:]
RAW = ["FGM", "FGA", "FTM", "FTA"] + COUNTING
POINTS = {"PTS": 1, "REB": 1.2, "AST": 1.5, "STL": 3, "BLK": 3, "TOV": -1}  # Yahoo default
SHRINK = 20  # production per minute: games of baseline minutes that count as much as this season's minutes played
MIN_SHRINK = 6  # minutes per game: games of baseline that count as much as this season's (a new role shows at once)
OLDER_W, REGRESS = 0.5, 25  # baseline: a game two seasons ago counts half; 25 games of a fringe line pull small samples in
PROJ_W = 0.6  # expert projections' weight in the pre-season line (prior()); fitted by python backtest.py replay
HYPE = 10    # same, for how other managers see players (hot starts count more: 3 games = 23%, 10 games = half)
OUT = {"O", "INJ", "IL", "IL+", "NA", "SUSP"}  # Yahoo statuses that mean "not playing now"

# Matchup model. DISPERSION, SCHED_CV and PCT_NOISE are fitted to real weeks of 2025-26 box scores (backtest.py: 20
# simulated leagues, predictions from what was known each Monday, injured regulars sitting until a rough return date as
# with ESPN's); re-run it after changing how lines are projected.
GAMES_WEEK = 3.4                                   # NBA games per team per week
DISPERSION = {"FG3M": 1.75, "PTS": 3.0, "REB": 2.5, "AST": 3.0, "STL": 1.0, "BLK": 1.5, "TOV": 1.25}  # variance / mean
SCHED_CV = 0.12                                    # week-to-week swing beyond box-score noise (schedule, rest, minutes,
                                                   # and projection error); 0.17 while the backtest didn't know who's out
PCT_NOISE = 1.5                                    # FG%/FT% variance vs coin-flip shooting at each player's rate (was 1.75)
WEAK = 0.35                                        # a "weak spot" / punt hint: winning a category under 35% of
                                                   # weeks (about the bottom 10% of team-categories once odds are calibrated)
FILL = 0.5                                         # share of a sat-out player's games your bench/streamers cover
                                                   # (not in backtest.py: its rosters never change, so nobody fills in)
STATUS_AVAIL = {"GTD": 0.9, "DTD": 0.9, "O": 0.7, "SUSP": 0.8, "INJ": 0.6, "IL": 0.5, "IL+": 0.4, "NA": 0.0}
# Teammates' injuries (lifts()), fitted on 2025-26 box scores (python backtest.py teammates): while a regular (REGULAR+
# minutes) sits, SHARE of his minutes go to teammates playing 10+, split by their room under ROOM minutes x
# e^(ROLE x role similarity) and played at their own rates; USAGE of the USAGE_STATS those minutes don't replace go to
# the same players by their output. The rest goes to the end of the bench.
REGULAR, SHARE, ROOM, ROLE, USAGE = 25, 0.37, 36, 0.5, 0.25
USAGE_STATS = ["FGM", "FGA", "FTM", "FTA", "PTS", "AST", "TOV"]  # rebounds, steals, blocks and threes barely move

# Trade market: what managers pay for a player, from his market rank (League.market): 100 x MARKET^(rank - 1).
# Steep, so a star is worth more than the sum of spare parts: rank 1 = 100, 25 = 48, 50 = 22, 100 = 5, 150 = 1.
# ponytail: starting guess; tune with the trade log (N6).
MARKET = 0.97
# Games until Yahoo's Cur rank counts half in the market (League.market). Market only: HYPE's 10, averaging ranks, let 4
# cold games sink Jalen Brunson (Pre 27, Cur 217) to #81 behind a hot Paul George (Pre 75, Cur 23) at #50 (Martin, Oct 28:
# managers don't trade like that). ponytail: a guess like MARKET; tune with the trade log (N6)
CHASE = 20
EVEN = 5  # market gap (%) that still reads as "even"
# Trade search difficulty: (min gain for you, min gain for them, both win %; market gap range, % you gain on market).
# easy: they win on market and you still gain. fair: market within 15% (or FAIR_FIT, below). finesse: market even or
# in their favour, but the fit is strongly yours. hard: you win on market, needs a pitch. No tier overpays over 30%.
# ponytail: starting guesses like the knobs above; tune once real offers get accepted or turned down.
TIERS = {"easy": (0.5, -1, -30, -EVEN), "fair": (1, -np.inf, -15, 15), "finesse": (1.5, -2.5, -30, EVEN),
         "hard": (3, -6, EVEN, np.inf)}
FAIR_FIT = 30  # fair's second route: both teams gain in real odds and the market is within this (Martin, Oct 26)
VERDICT, MOVE, GUARD = 0.5, 0.5, 1  # trade accept/decline beyond +-; waiver move worth listing; most a cats/team goal
                                    # search may cost you
# The win-% cutoffs above (and accept()'s) are in One Win terms. A categories league's win % moves about 2.5x less
# per move (measured Oct 26 on the real league), so League scales them by this.
CAT_SCALE = 0.4
# Punt planner (strategy()): staying balanced, every single-category punt, and these pairs
PAIRS = [("FT%", "TOV"), ("FG%", "TOV"), ("FG%", "FT%"), ("AST", "TOV"), ("FG3M", "FT%"), ("FG%", "FG3M")]


def accept(mkt, their_net, s=1.0):
    """How likely the other manager says yes: mostly on market value (priced()'s diff_pct), a little on real fit.
    Anything fair-priced (TIERS, FAIR_FIT) is at least a Maybe."""
    if mkt <= -EVEN and their_net > -s:
        return "Likely yes"
    return "Maybe" if mkt <= TIERS["fair"][3] or (their_net > 0 and mkt <= FAIR_FIT) else "Long shot"


def priced(give, get):
    """A trade on the market, from the market value each side ends up with (roster spots included). diff_pct: what
    you gain as a share of the bigger side, so a deal is the same size from both ends (+20% for you = -20% for them)."""
    d = round(100 * (get - give) / max(give, get, 1), 1)
    return {"give": round(give, 1), "get": round(get, 1), "diff_pct": d,
            "verdict": "even" if abs(d) < EVEN else f"you overpay {-d:.0f}%" if d < 0 else f"they overpay {d:.0f}%"}


def norm(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"[^a-z0-9 ]", "", name)
    return " ".join(re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name).split())


def match(name, keys):
    """The stats key a name means (League.find): the same name once accents and Jr/III are dropped, a unique last name
    ("Jokic", "Gilgeous-Alexander"), or a close spelling. None when nothing fits."""
    k = norm(name)
    if k in keys:
        return k
    if len(last := [x for x in keys if x.endswith(" " + k)]) == 1:
        return last[0]
    return next(iter(difflib.get_close_matches(k, keys, n=1, cutoff=0.8)), None)


def fetch(season, max_age_hours=None):
    """Per-game stats from stats.nba.com, cached in data/. The current season refreshes when the cache is stale."""
    cache = DATA / f"stats_{season}.csv"
    stale = max_age_hours and cache.exists() and time.time() - cache.stat().st_mtime > max_age_hours * 3600
    if not cache.exists() or stale:
        from nba_api.stats.endpoints import leaguedashplayerstats
        df = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season, per_mode_detailed="PerGame", timeout=60).get_data_frames()[0]
        DATA.mkdir(exist_ok=True)
        df[["PLAYER_NAME", "TEAM_ABBREVIATION", "GP", "MIN"] + RAW].to_csv(cache, index=False)
    return keyed(pd.read_csv(cache))


TRICODE = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "PHO": "PHX", "WSH": "WAS", "UTAH": "UTA", "BRK": "BKN",
           "CHO": "CHA"}  # Yahoo/ESPN short forms -> NBA tricodes


def _cached(cache, hours, get):
    """Read a JSON cache, refreshing it with get() when older than `hours`. Offline: the stale cache, or {}."""
    if not cache.exists() or time.time() - cache.stat().st_mtime > hours * 3600:
        try:
            DATA.mkdir(exist_ok=True)
            cache.write_text(json.dumps(get(), ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"Couldn't refresh {cache.name} ({e}); using {'the old copy' if cache.exists() else 'nothing'}.",
                  file=sys.stderr)
    return json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}


def schedule(season=CUR):
    """{NBA tricode: sorted game dates "YYYY-MM-DD"} from stats.nba.com, cached in data/ for a day."""
    def get():
        from nba_api.stats.endpoints import scheduleleaguev2
        d = scheduleleaguev2.ScheduleLeagueV2(season=season, timeout=60).get_data_frames()[0]
        day = pd.to_datetime(d.gameDate).dt.strftime("%Y-%m-%d")
        out = {}
        for side in ("homeTeam_teamTricode", "awayTeam_teamTricode"):
            for t, dt in zip(d[side], day):
                out.setdefault(t, []).append(dt)
        return {t: sorted(ds) for t, ds in out.items()}
    return _cached(DATA / f"schedule_{season}.json", 24, get)


def injuries():
    """{player name: {status, note, return}} from ESPN's public injury feed, cached for 3 hours, each day's last copy
    kept as data/injuries_<date>.json: the return dates it gave, to measure backtest.py's RETURN_NOISE against later.
    ponytail: unofficial endpoint (personal use); if ESPN changes it, this goes quiet and Yahoo's status tags still work."""
    def get():
        url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
        with urllib.request.urlopen(url, timeout=30) as r:
            feed = json.load(r)
        status = {"Out": "O", "Day-To-Day": "DTD", "Suspension": "SUSP"}
        out = {i["athlete"]["displayName"]: {"status": status.get(i.get("status"), ""), "note": i.get("shortComment", ""),
                                             "return": i.get("details", {}).get("returnDate", "")}
               for team in feed.get("injuries", []) for i in team.get("injuries", [])}
        (DATA / f"injuries_{date.today()}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out
    return _cached(DATA / "injuries.json", 3, get)


ESPN_STAT = {"MIN": "40", "FGM": "13", "FGA": "14", "FTM": "15", "FTA": "16", "FG3M": "17", "PTS": "0", "REB": "6",
             "AST": "3", "STL": "2", "BLK": "1", "TOV": "11"}  # ESPN's stat ids (checked Oct 27: PTS = 2 FGM + 3PM + FTM)


def espn(season=CUR):
    """{player name: per-game projection} from ESPN's public fantasy API (its season projection), cached a day, each
    day's copy kept as data/projections_<date>.json for a later fit. A past season gives its preseason projection,
    fetched once: 2025-26's misses that season as much as baseline() does, so it wasn't updated with hindsight.
    ponytail: unofficial endpoint like injuries(); if ESPN changes it, this goes quiet and Yahoo's projections still work."""
    yr = int(season[:4]) + 1
    def get():
        only = {"players": {"limit": 2000, "sortPercOwned": {"sortPriority": 1, "sortAsc": False},  # a sort is required
                            "filterStatsForSourceIds": {"value": [1]},  # 1 = projections
                            "filterStatsForSplitTypeIds": {"value": [0]}, "filterStatsForExternalIds": {"value": [yr]}}}
        req = urllib.request.Request(f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{yr}/segments/0"
                                     "/leaguedefaults/3?view=kona_player_info", headers={"X-Fantasy-Filter": json.dumps(only)})
        with urllib.request.urlopen(req, timeout=60) as r:
            players = json.load(r)["players"]
        out = {p["player"]["fullName"]: {c: round(a[i], 2) for c, i in ESPN_STAT.items() if i in a}
               for p in players for s in p["player"].get("stats", ())
               if s.get("statSourceId") == 1 and s.get("seasonId") == yr and (a := s.get("averageStats"))}
        if season == CUR:
            (DATA / f"projections_{date.today()}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out
    return _cached(DATA / f"projections_{season}.json", 24 if season == CUR else np.inf, get)


def news_index():
    """{player name: [ESPN id, time of his latest news (UTC ISO) or None]} for ESPN's 1000 most-owned players, cached
    3 hours. ponytail: unofficial endpoint like espn()."""
    yr = int(CUR[:4]) + 1
    def get():
        only = {"players": {"limit": 1000, "sortPercOwned": {"sortPriority": 1, "sortAsc": False},  # a sort is required
                            "filterStatsForSourceIds": {"value": [1]},  # any stats filter: none at all sends 3 MB
                            "filterStatsForSplitTypeIds": {"value": [0]}, "filterStatsForExternalIds": {"value": [yr]}}}
        req = urllib.request.Request(f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{yr}/segments/0"
                                     "/leaguedefaults/3?view=kona_player_info", headers={"X-Fantasy-Filter": json.dumps(only)})
        with urllib.request.urlopen(req, timeout=60) as r:
            players = json.load(r)["players"]
        utc = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {p["player"]["fullName"]: [p["player"]["id"], (t := p["player"].get("lastNewsDate")) and utc(t)]
                for p in players}
    return _cached(DATA / "news_index.json", 3, get)


NEWS, news_lock = DATA / "news.json", threading.Lock()


def news(keys, since=""):
    """{stats key: ESPN's fantasy news on him (Rotowire's blurbs), newest first: [{date (UTC ISO), headline, story}]}
    for the keys ESPN lists (news_index()) with news since `since`. A player's news is fetched again only once the index
    shows something newer; kept in data/news.json. Offline: what's saved."""
    ix = news_index()
    names = {norm(n): n for n in ix}
    found = {k: ix[names[m]] for k in keys if (m := match(k, names)) and (ix[names[m]][1] or "") >= since}
    read = lambda: json.loads(NEWS.read_text(encoding="utf-8")) if NEWS.exists() else {}
    with news_lock:
        saved = read()
    def get(pid):
        url = f"https://site.api.espn.com/apis/fantasy/v2/games/fba/news/players?playerId={pid}"
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                feed = json.load(r).get("feed", [])
        except Exception as e:
            print(f"Couldn't fetch news for ESPN player {pid} ({e}).", file=sys.stderr)
            return None
        return [{"date": x["published"], "headline": x.get("headline", ""), "story": x.get("story", "")}
                for x in feed if x.get("type") == "Rotowire"]
    stale = {str(pid): last for pid, last in found.values() if saved.get(str(pid), {}).get("last") != last}
    if stale:
        with ThreadPoolExecutor(8) as ex:
            got = {pid: {"last": stale[pid], "items": items} for pid, items in zip(stale, ex.map(get, stale)) if items is not None}
        with news_lock:  # re-read: the card and the panel can fetch at once
            saved = {**read(), **got}
            DATA.mkdir(exist_ok=True)
            NEWS.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    return {k: [x for x in saved[str(pid)]["items"] if x["date"] >= since]
            for k, (pid, _) in found.items() if str(pid) in saved}


def gamelogs(season=CUR, max_age_hours=None):
    """Every player's regular-season box scores from stats.nba.com, keyed like the stats, cached in data/ and refetched
    once older than max_age_hours. Offline: the old copy, or None."""
    cache = DATA / f"gamelogs_{season}.csv"
    if not cache.exists() or max_age_hours and time.time() - cache.stat().st_mtime > max_age_hours * 3600:
        try:
            from nba_api.stats.endpoints import leaguegamelog
            g = leaguegamelog.LeagueGameLog(season=season, player_or_team_abbreviation="P", timeout=90,
                                            season_type_all_star="Regular Season").get_data_frames()[0]
            DATA.mkdir(exist_ok=True)
            g.to_csv(cache, index=False)
        except Exception as e:
            print(f"Couldn't refresh {cache.name} ({e}); using {'the old copy' if cache.exists() else 'nothing'}.",
                  file=sys.stderr)
    if not cache.exists():
        return None
    g = pd.read_csv(cache, parse_dates=["GAME_DATE"])
    return g.assign(key=g.PLAYER_NAME.map(norm))


def keyed(df):
    return df.assign(key=df.PLAYER_NAME.map(norm)).drop_duplicates("key").set_index("key")


def baseline(last, older):
    """Pre-season per-game line from the last two seasons: GP-weighted, a game two seasons ago counting OLDER_W of one
    last season, pulled toward a fringe-rotation line by REGRESS games (so a 5-game season doesn't count as gospel, and
    a player who missed all of last season isn't treated as a rookie). GP = the weighted games, for availability.
    Backtested on 2025-26 (backtest.py): slightly better weekly odds than last season alone."""
    cols = ["MIN"] + RAW
    keys = last.index.union(older.index)
    fringe = last.nlargest(200, "MIN").tail(60)[cols].mean().to_numpy()
    w1, w2 = last.GP.reindex(keys).fillna(0), OLDER_W * older.GP.reindex(keys).fillna(0)
    x1, x2 = last[cols].reindex(keys).fillna(0), older[cols].reindex(keys).fillna(0)
    line = (x1.mul(w1, axis=0) + x2.mul(w2, axis=0) + REGRESS * fringe).div(w1 + w2 + REGRESS, axis=0)
    return line.assign(PLAYER_NAME=last.PLAYER_NAME.combine_first(older.PLAYER_NAME).reindex(keys),
                       TEAM_ABBREVIATION=last.TEAM_ABBREVIATION.combine_first(older.TEAM_ABBREVIATION).reindex(keys),
                       GP=w1 + w2)


def experts(yahoo, espn, base, keys=()):
    """One expert per-game line per player, with its source: Yahoo's (the snapshot's, updated in-season) where it has
    him, else ESPN's. Names are matched to `keys` (the stats' players) like League.find, so a spelling variant lands on
    the player it means; the rest are rookies yet to play. Yahoo shows no minutes, so his are ESPN's (else the
    baseline's) scaled by how much more or less Yahoo has him scoring, rebounding and assisting: a role change moves
    minutes, not rates. A stat a source leaves out is 0: ESPN drops zeros (Duren: no 3PM, PTS = 2 FGM + FTM)."""
    def frame(d):
        df = pd.DataFrame([{"PLAYER_NAME": n, **x} for n, x in d.items()], columns=["PLAYER_NAME", "MIN"] + RAW)
        df[RAW] = df[RAW].fillna(0)
        df["key"] = [match(n, keys) or norm(n) for n in df.PLAYER_NAME]
        return df.drop_duplicates("key").set_index("key").astype({c: float for c in ["MIN"] + RAW})
    y, e = frame(yahoo), frame(espn)
    vol = lambda d: d[["PTS", "REB", "AST"]].sum(axis=1)
    ref = e.reindex(y.index).combine_first(base.reindex(y.index))
    y["MIN"] = ref.MIN * vol(y) / vol(ref).clip(lower=1)
    return pd.concat([y.dropna(subset=["MIN"]).assign(src="Yahoo"),  # a rookie only Yahoo projects: no minutes to go on
                      e[~e.index.isin(y.index)].assign(src="ESPN")])


def prior(base, proj, w=PROJ_W):
    """Pre-season line: baseline() blended with the experts' line (experts(), weighing w). A rookie's baseline is the
    fringe-roster line ros() starts unprojected rookies from. ros() then leans on this season as games pile up."""
    cols = ["MIN"] + RAW
    fringe = base.nlargest(200, "MIN").tail(60)[cols].mean()
    new = proj.loc[proj.index.difference(base.index), ["PLAYER_NAME"]].assign(GP=0.0, **fringe.to_dict())
    out = pd.concat([base, new]) if len(new) else base.copy()
    out.loc[proj.index, cols] = (1 - w) * out.loc[proj.index, cols] + w * proj[cols]
    return out


def ros(base, cur, k=SHRINK, k_min=MIN_SHRINK):
    """Rest-of-season per-game line = projected minutes x production per minute. Minutes lean on this season fast,
    GP / (GP + k_min), since a new role or team shows up at once and sticks. Production per minute leans on it slowly:
    this season's minutes played / (those + k games of baseline minutes), so a player stuck at 8 minutes a night barely
    moves his rates. Rookies no expert projects (prior()) start from a fringe-roster line, so 3 hot games don't make
    them stars.
    Replayed on 2025-26 (each Monday's rest-of-season line vs what players did after, stats scaled equally): 8% less
    error than one GP / (GP + 20) blend in weeks 1-6, 9% less for players whose minutes moved 6+, even late in the
    season. Faster minutes for players on a new team gained nothing."""
    rookies = cur.index.difference(base.index)
    out = pd.concat([base, cur.loc[rookies]])
    out.loc[rookies, ["MIN"] + RAW] = base.nlargest(200, "MIN").tail(60)[["MIN"] + RAW].mean().to_numpy()
    both = out.index.intersection(cur.index)
    gp, cm, bm = cur.loc[both, "GP"], cur.loc[both, "MIN"], out.loc[both, "MIN"]
    mins = (gp * cm + k_min * bm) / (gp + k_min)
    w = (gp * cm / (gp * cm + k * bm)).to_numpy()[:, None]
    rate = (w * cur.loc[both, RAW].div(cm.clip(lower=1), axis=0).to_numpy()
            + (1 - w) * out.loc[both, RAW].div(bm, axis=0).to_numpy())
    out.loc[both, RAW] = rate * mins.to_numpy()[:, None]
    out.loc[both, "MIN"] = mins
    out["GP_CUR"] = cur.GP.reindex(out.index).fillna(0)
    return out


def lifts(line, team, keys):
    """Per-game change to teammates' lines (MIN + RAW) while each regular in `keys` sits, as if he sat alone (the
    knobs above). line: per-game lines of everyone who can take his minutes (this season's players) and of `keys`;
    team: {player: NBA team}. Role similarity: cosine of per-minute AST, REB, BLK and 3PM, standardized over the
    rotation. Rows (player, by)."""
    cols = ["MIN"] + RAW
    rot = line.loc[line.MIN >= 10, cols].astype(float)
    pm = rot[["AST", "REB", "BLK", "FG3M"]].div(rot.MIN, axis=0)
    prof = ((pm - pm.mean()) / pm.std()).to_numpy()
    at, tm = {k: i for i, k in enumerate(rot.index)}, pd.Series(team).reindex(rot.index).to_numpy()
    out = []
    for j in keys:
        if j not in at or rot.at[j, "MIN"] < REGULAR:
            continue
        mates = (tm == team.get(j)) & (rot.index != j)
        if not mates.any():
            continue
        b, p, q = rot[mates], prof[mates], prof[at[j]]
        sim = p @ q / np.sqrt((p * p).sum(1) * (q @ q) + 1e-9)
        w = np.clip(ROOM - b.MIN.to_numpy(), 0, None) * np.exp(ROLE * sim)
        if w.sum() <= 0:
            continue
        mins = SHARE * rot.at[j, "MIN"] * w / w.sum()
        rate = b[RAW].div(b.MIN, axis=0)
        d = rate.mul(mins, axis=0)  # his minutes at their own rates
        used = rate[USAGE_STATS].mul(b.MIN + mins, axis=0)  # and what those don't replace, by output
        d[USAGE_STATS] += USAGE * used / used.sum().clip(lower=1e-9) * (rot.loc[j, USAGE_STATS] - d[USAGE_STATS].sum())
        out.append(d.assign(MIN=mins, by=j))
    if not out:
        return pd.DataFrame(columns=cols, index=pd.MultiIndex.from_tuples([], names=["key", "by"]), dtype=float)
    return pd.concat(out).set_index("by", append=True).rename_axis(["key", "by"])[cols]


def had(logs, team, lift):
    """The lift (lifts()) each player had per game in these box scores: every game a teammate in `lift` sat out (one of
    his team's games from his first for it, or all of them if he's yet to play) adds that teammate's lift, averaged
    over the player's games. ros() reads this season net of it, as if everyone played."""
    T, cols = "TEAM_ABBREVIATION", ["MIN"] + RAW
    games, sat = logs.drop_duplicates([T, "GAME_ID"]), []
    for j in lift.index.get_level_values("by").unique():
        mine = logs[(logs.key == j) & (logs[T] == team.get(j))]
        tg = games[(games[T] == team.get(j)) & (games.GAME_DATE >= mine.GAME_DATE.min() if len(mine) else True)]
        sat.append(tg.loc[~tg.GAME_ID.isin(mine.GAME_ID), [T, "GAME_ID"]].assign(by=j))
    if not sat:
        return pd.DataFrame(columns=cols, dtype=float)
    n = logs[[T, "GAME_ID", "key"]].merge(pd.concat(sat), on=[T, "GAME_ID"]).groupby(["key", "by"]).size()
    n = n[n.index.isin(lift.index)]  # games each player played while `by` sat
    return lift.loc[n.index].mul(n, axis=0).groupby(level=0).sum().div(logs.key.value_counts(), axis=0).dropna()


def zscores(df, pool_size):
    """z per category vs the top `pool_size` players by minutes. FG%/FT% are volume-weighted, TOV inverted."""
    pool = df.nlargest(pool_size, "MIN")
    z = pd.DataFrame(index=df.index)
    for pct, m, a in [("FG%", "FGM", "FGA"), ("FT%", "FTM", "FTA")]:
        rate = pool[m].sum() / pool[a].sum()
        val, pv = df[m] - rate * df[a], pool[m] - rate * pool[a]
        z[pct] = (val - pv.mean()) / pv.std()
    for c in COUNTING:
        z[c] = (df[c] - pool[c].mean()) / pool[c].std()
    z["TOV"] *= -1
    return z[CATS]


def phi(x):
    """Standard normal CDF (Abramowitz-Stegun erf, error < 2e-7), vectorized without scipy."""
    z = np.abs(x) / np.sqrt(2)
    t = 1 / (1 + 0.3275911 * z)
    y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-z * z)
    return 0.5 * (1 + np.sign(x) * y)


def cat_probs(A, B, cats, A0=None, B0=None):
    """P(team A beats team B) in each category. A, B: (..., len(RAW)) weekly totals -> (..., len(cats)).
    A0, B0: totals already banked this week. They move the final totals but add no variance."""
    TA, TB = (A, B) if A0 is None else (A + A0, B + B0)
    out = []
    for c in cats:
        if c in ("FG%", "FT%"):
            m, a = RAW.index(c[:2] + "M"), RAW.index(c[:2] + "A")
            na, nb = np.maximum(TA[..., a], 1e-9), np.maximum(TB[..., a], 1e-9)  # no shots at all: 0%, not NaN
            pa, pb = TA[..., m] / na, TB[..., m] / nb
            var = PCT_NOISE * (pa * (1 - pa) * A[..., a] / na ** 2 + pb * (1 - pb) * B[..., a] / nb ** 2)
            diff = pa - pb
        else:
            i, d = RAW.index(c), DISPERSION.get(c, 1.0)
            ta, tb = A[..., i], B[..., i]
            var = d * (ta + tb) + (SCHED_CV * ta) ** 2 + (SCHED_CV * tb) ** 2
            diff = TB[..., i] - TA[..., i] if c == "TOV" else TA[..., i] - TB[..., i]
        out.append(phi(diff / np.sqrt(var + 1e-12)))  # no games left: a banked lead is a sure win
    return np.stack(out, -1)


def total(v, c):
    """A weekly total as shown: FG%/FT% in percent (None before any attempts), counting stats to one decimal."""
    if c in ("FG%", "FT%"):
        a = v[RAW.index(c[:2] + "A")]
        return round(100 * float(v[RAW.index(c[:2] + "M")] / a), 1) if a else None
    return round(float(v[RAW.index(c)]), 1)


def match_prob(p):
    """P(win the matchup) = P(more category wins than losses), categories independent; a tie counts half."""
    n = p.shape[-1]
    dist = np.zeros(p.shape[:-1] + (n + 1,))
    dist[..., 0] = 1
    for k in range(n):
        pk = p[..., k:k + 1]
        dist = dist * (1 - pk) + np.concatenate([np.zeros_like(pk), dist[..., :-1]], -1) * pk
    wins = np.arange(n + 1)
    return (dist * (wins > n / 2)).sum(-1) + 0.5 * (dist * (wins == n / 2)).sum(-1)


class League:
    """A league snapshot (see yahoo.py) joined with NBA stats."""

    def __init__(self, snap, base=None, cur=None, sched=None, inj=None, today=None, logs=None, proj=None, news=None):
        s = snap["settings"]
        self.snap, self.teams_n, self.roster_n = snap, s.get("teams", 12), s.get("roster", 13)
        self.lineup = s.get("starters", 10)  # players whose games count each day
        self.mode = s.get("mode", "9cat")
        self.scoring = s.get("scoring", "categories")  # Yahoo's H2H default; "one_win" = only the matchup counts
        self.s = 1.0 if self.scoring == "one_win" else CAT_SCALE  # scales the One Win cutoffs
        self.u = 3.0 if self.mode == "points" else 1.0  # FP/game spreads ~3x wider than 9-cat z-sums; scales thresholds
        self.cats = [c for c in s.get("cats", CATS) if c in CATS] or CATS
        self.points = s.get("points") or POINTS
        self.me = snap["me"]
        self.free = {}  # {team: free moves already made on this copy} (after_free)
        self.trades = []  # trades already made on this copy (after_trade)
        base = baseline(fetch(LAST), fetch(OLDER)) if base is None else base
        cur = fetch(CUR, max_age_hours=12) if cur is None else cur
        self.cur, self.logs = cur, logs  # this season's per-game stats and box scores (gamelogs()), for card()
        self.news = news  # news() from load(), for card() and roster_news(); never moves a number
        pool = self.teams_n * self.roster_n
        # expert projections: the snapshot's (Yahoo), and ESPN's (proj, from load()) for players Yahoo doesn't list
        self.proj = experts(snap.get("projections", {}), proj or {}, base, base.index.union(cur.index))
        pre = prior(base, self.proj)
        line = ros(pre, cur)
        cols = ["PLAYER_NAME", "MIN", "GP_CUR"] + RAW
        self.p = line[cols].copy()  # find() works from here; teammates' injuries adjust the numbers below
        self.meta, self.rosters = {}, {}
        for team, players in snap["teams"].items():
            self.rosters[team] = self._keys(players)
        self.fa = self._keys(snap.get("free_agents", []))
        # NBA schedule + injury news: games left this week, and return dates for availability
        self.sched, self.inj = sched or {}, {}
        self.today = (today or date.today()).isoformat()
        sunday = (date.fromisoformat(self.today) + timedelta(6 - date.fromisoformat(self.today).weekday())).isoformat()
        nba = cur.TEAM_ABBREVIATION.combine_first(base.TEAM_ABBREVIATION).to_dict()
        nba.update({k: m["nba"] for k, m in self.meta.items() if m.get("nba")})  # the league's view is more current
        self.nba = {k: TRICODE.get(str(t).upper(), str(t).upper()) for k, t in nba.items()}
        for name, i in (inj or {}).items():
            if k := self.find(name, strict=False):
                self.inj[k] = i
                m = self.meta.setdefault(k, {})
                m["status"] = m.get("status") or i["status"]  # the league's own tag wins
        # this week's opponent and totals so far (Yahoo pull, snapshot, or picked in the app). Once that week is over,
        # the season schedule names the next opponent and its dates (weeks 7 and 17 run 14 days), with no score yet.
        # ponytail: without either, weeks are assumed to run Monday-Sunday
        m = snap.get("matchup") or {}
        self.rolled = bool(m) and (m.get("week_end") or "") < self.today
        if not m or self.rolled:
            wk = next((w for w in snap.get("schedule", ()) if w["start"] <= self.today <= w["end"]), None)
            opp = next((t for pr in wk["matchups"] if self.me in pr for t in pr if t != self.me), None) if wk else None
            m = {"week_start": wk["start"], "week_end": wk["end"], **({"opponent": opp} if opp else {})} if wk else {}
        self.matchup = m
        self.week_end = m.get("week_end") or sunday
        # teammates' injuries: every regular's lift (lifts()) read off this season's box scores (had()), so a teammate's
        # minutes while he sat aren't counted as the new normal; the injured ones' lifts then go back on for the share
        # of each teammate's games before the return (_boost): rest of season here, this week in p_week
        regs = line.index[line.MIN >= REGULAR]
        lift = lifts(line[line.index.isin(cur.index) | line.index.isin(regs)], self.nba, regs)
        if logs is not None and len(lift):
            h = had(logs, self.nba, lift).reindex(cur.index).dropna()
            full = cur.copy()
            full.loc[h.index, h.columns] = (full.loc[h.index, h.columns] - h).clip(lower=0)
            line = ros(pre, full)
        back = {k for k, i in self.inj.items() if (i.get("return") or "") > self.today}
        self.lift = lift[lift.index.get_level_values("by").isin(back)]
        ahead = self._boost("9999")
        line.loc[ahead.index, ahead.columns] += ahead
        self.p = line[cols].copy()
        self.z = zscores(line, pool)[self.cats]                      # ROS value
        # "cur" = perceived value: reputation plus a heavily weighted hot/cold start (roughly what Yahoo ranks show)
        seen = ros(pre, cur, HYPE)
        self.zcur = zscores(seen, pool)[self.cats]
        self.fp = sum(line[c] * w for c, w in self.points.items() if c in line)
        self.fp_cur = sum(seen[c] * w for c, w in self.points.items() if c in seen)
        self.p_week = self.p.copy()  # this week's per-game lines: the lifts for this week's games instead
        wk = self._boost(self.week_end).sub(ahead, fill_value=0)
        self.p_week.loc[wk.index, wk.columns] += wk
        self.rate, self.avail, self.back = self._availability(base, cur)
        self.games_week = pd.Series({k: len(self.game_days(k, self.week_end)) for k in self.p.index}, dtype=float)
        # expected weekly contribution: games played at his line, missed games partly covered at waiver level
        fill = self.p.loc[self._waiver_level(self.value()), RAW].mean()
        # what a missed game is worth in value units: the half-covered waiver line (zero point for effective())
        f = pd.DataFrame([FILL * fill], index=["_fill"]).assign(MIN=0.0)
        self.zfill = {"ros": zscores(pd.concat([line, f]), pool).loc["_fill", self.cats],
                      "cur": zscores(pd.concat([seen, f]), pool).loc["_fill", self.cats]}
        a = self.avail.to_numpy()[:, None]
        self.week = GAMES_WEEK * (a * self.p[RAW] + (1 - a) * FILL * fill.to_numpy())
        # parked in an IL slot (IL, IL+, NA): he holds no active spot, so nobody covers his missed games, and from his
        # return (the share `back` of games left) he takes the spot of a waiver-level player. Nothing if even then he
        # wouldn't beat one (effective value): he'd be dropped. ponytail: he bumps the waiver level, not the team's worst
        self.il = {t: {k for k in ks if self.meta[k].get("slot") in ("IL", "IL+", "NA")} for t, ks in self.rosters.items()}
        self.il_n = s.get("il", max(map(len, self.il.values()), default=0))  # IL slots per team (yahoo.py reads them)
        w, r = self._waiver_level(self.value()), self.rate.to_numpy()[:, None]
        healthy = GAMES_WEEK * (r * self.p[RAW] + (1 - r) * FILL * fill.to_numpy())  # his week once he's back
        il = (healthy - self.week.loc[w].mean()).mul(self.back, axis=0)
        floor = float(self.zfill["ros"].sum())
        self.week_il = il.where(floor + self.rate * (self.value() - floor) > self.effective()[w].mean(), 0.0, axis=0)
        # an open roster spot on the trade market: the waiver level's market value, fixed now so a player dropped on a
        # copy (free moves) never prices a pickup at his own rank
        self.spot = float(self.market["value"].reindex(w).mean())

    def _keys(self, players):
        keys = []
        for pl in players:
            k = self.find(pl["name"], strict=False)
            if k:
                self.meta[k] = pl
                keys.append(k)
        return keys

    def game_days(self, k, end="9999", start=None):
        """Dates k's NBA team plays from today (or `start`, his return date, or the first day the matchup totals don't
        cover yet, if later) through `end`."""
        start = max(start or self.today, self.today, self.matchup.get("from", ""),
                    self.inj.get(k, {}).get("return") or "")
        return [d for d in self.sched.get(self.nba.get(k), ()) if start <= d <= end]

    def starts(self, keys, start=None):
        """{player: games he starts the rest of this week}. Yahoo only scores starters, so each day the best `lineup`
        players with a game count and bench games fill in on light days. start: {player: first day}, for claims.
        ponytail: best = per-game value, position eligibility ignored; a lineup set by hand can count fewer."""
        day = {}
        for k in sorted(keys, key=self._value.get, reverse=True):
            for d in self.game_days(k, self.week_end, (start or {}).get(k)):
                day.setdefault(d, []).append(k)
        return collections.Counter(k for ks in day.values() for k in ks[:self.lineup])

    def _boost(self, end):
        """Per-game lift each player gets from injured teammates (self.lift) over his games from today through `end`:
        each one's lift times the share of those games before his return."""
        share = []
        for k, j in self.lift.index:
            days = self.game_days(k, end)
            share.append(sum(d < self.inj[j]["return"] for d in days) / max(len(days), 1))
        return self.lift.mul(share, axis=0).groupby(level="key").sum()

    def _availability(self, base, cur):
        """Share of games a player suits up for: GP rate over last season + this one, shrunk halfway toward 85%
        (one bad injury year shouldn't count fully), then cut by injury status. Returns (rate, rest-of-season, share of
        games left after his return). A known return date replaces the status guess: rest of season he plays the share
        of remaining games after it."""
        played = base.GP.reindex(self.p.index).fillna(0) + cur.GP.reindex(self.p.index).fillna(0)
        possible = base.GP.max() + cur.GP.max()
        a = (played + 0.85 * possible / 2) / (possible * 1.5)
        a[~self.p.index.isin(base.index)] = 0.85  # rookies: no history
        status = pd.Series({k: STATUS_AVAIL.get(m.get("status", ""), 1.0) for k, m in self.meta.items()}, dtype=float)
        share = pd.Series(1.0, index=a.index)
        for k, i in self.inj.items():
            rest = [d for d in self.sched.get(self.nba.get(k), ()) if d >= self.today]
            if (i.get("return") or "") > self.today and rest and k in share.index:
                status[k], share[k] = 1.0, sum(d >= i["return"] for d in rest) / len(rest)
        rate = a * status.reindex(a.index).fillna(1.0)
        return rate, rate * share, share

    def find(self, name, strict=True):
        if (k := match(name, self.p.index)) or not strict:
            return k
        raise KeyError(f"Player not found: {name}")

    def owner(self, k):
        return next((t for t, ks in self.rosters.items() if k in ks), "FA" if k in self.fa else None)

    # --- value -------------------------------------------------------------
    def active(self, punt=()):
        return [c for c in self.cats if c not in punt]

    def value(self, keys=None, punt=(), frame="ros"):
        """One number per player: z-sum over non-punted cats (9cat) or fantasy points per game."""
        if self.mode == "points":
            v = {"ros": self.fp, "cur": self.fp_cur}.get(frame, self.fp)
        else:
            v = (self.zcur if frame == "cur" else self.z)[self.active(punt)].sum(axis=1)
        return v if keys is None else v.reindex(keys).fillna(0)

    def replacement(self, punt=(), frame="ros"):
        """What you can grab off waivers: average of the 5 best free agents (or the pool edge without FA data)."""
        v = self.value(punt=punt, frame=frame)
        pick = self._waiver_level(v)
        z = self.zcur if frame == "cur" else self.z
        return float(v[pick].mean()), z.loc[pick].mean()

    def effective(self, punt=(), frame="ros"):
        """Value over the games a player will actually play: his per-game value where he plays (avail), the
        half-covered waiver line the matchup model uses where he sits. A player who never plays sits below any
        healthy free agent (a plain value x avail would rank him above them, since most values are negative)."""
        v, floor = self.value(punt=punt, frame=frame), float(self.zfill[frame][self.active(punt)].sum())
        return floor + self.avail.reindex(v.index).fillna(0.85) * (v - floor)

    def perceived(self):
        """(waiver level, every player's perceived value): how a player looks on this season's hype over the games he
        plays (Yahoo ranks run on totals, and managers remember who missed a season), floored at what the best free
        agents look like, since anyone worse is just a droppable roster spot."""
        raw = self.effective(frame="cur")
        cv = float(raw[self._waiver_level(raw)].mean())
        return cv, raw.clip(lower=cv)

    @functools.cached_property
    def market(self):
        """Every player's trade-market rank and value (MARKET curve). Rank: Yahoo's preseason rank blended with its
        season-to-date one, weighted GP / (GP + CHASE) (Cur is noise early, and injured players sink to 600+), as a
        geometric mean so a cold start moves a player as much as an equally hot one (Cur runs to 700 but can't beat 1),
        then re-ranked. Players Yahoo didn't rank take the model's perceived rank (yahoo False)."""
        rank = self.effective(frame="cur").rank(ascending=False)
        yahoo = [k for k, m in self.meta.items() if m.get("rank")]
        for k in yahoo:
            w = self.p.at[k, "GP_CUR"] / (self.p.at[k, "GP_CUR"] + CHASE)
            rank[k] = self.meta[k]["rank"]["pre"] ** (1 - w) * max(self.meta[k]["rank"]["cur"], 1) ** w
        rank = rank.rank(method="first")
        return pd.DataFrame({"rank": rank.astype(int), "value": 100 * MARKET ** (rank - 1),
                             "yahoo": rank.index.isin(yahoo)})

    def _tier(self, tier, you, them, mkt, max_overpay=None):
        """Which deals clear a difficulty tier (TIERS): win-% gains for you and them, market gap in % (priced()).
        max_overpay (%) can only tighten a tier's floor. Works on scalars and arrays."""
        y, t, lo, hi = TIERS[tier]
        floor = -np.inf if max_overpay is None else -max_overpay
        gain = you > y * self.s
        ok = gain & (them > t * self.s) & (mkt >= max(lo, floor)) & (mkt <= hi)
        if tier == "fair":  # or both teams gain and the gap is moderate: you may send slightly lesser players
            ok = ok | (gain & (them > 0) & (mkt >= max(-FAIR_FIT, floor)) & (mkt <= FAIR_FIT))
        return ok

    def weakest(self, team, m, pv):
        """Perceived cost of the m players `team` would drop to make room: its weakest active ones by perceived value."""
        return float(pv.reindex(self._playing(self.rosters[team])).nsmallest(m).sum()) if m > 0 else 0.0

    def best_fa(self, n, skip=()):
        """The n free agents a team would pick up for open spots: per-game value times share of games played, so an
        injured name on waivers isn't who fills the spot."""
        if not self.fa:
            return []
        v = self.effective().reindex(self.fa)
        return v[~v.index.isin(list(skip))].nlargest(n).index.tolist()

    def _waiver_level(self, v):
        """The 5 best free agents by v, or the 10 players just outside the rostered pool without FA data."""
        if self.fa:  # healthy ones only: an NA or IL free agent isn't who you'd grab
            fa = [k for k in self.fa if self.meta.get(k, {}).get("status", "") not in OUT]
            return v.reindex(fa or self.fa).nlargest(5).index
        n = self.teams_n * self.roster_n
        return v.nlargest(n + 10).index[n:]

    def player(self, k, punt=()):
        m = self.meta.get(k, {})
        return {"name": self.p.at[k, "PLAYER_NAME"], "team": self.owner(k), "pos": m.get("pos", ""),
                "status": m.get("status", ""), "value": round(float(self.value([k], punt).iloc[0]), 2),
                "value_now": round(float(self.value([k], punt, "cur").iloc[0]), 2),
                "fp": round(float(self.fp[k]), 1), "gp": int(self.p.at[k, "GP_CUR"]),
                "avail": round(float(self.avail[k]), 2),
                "mkt": round(float(self.market.at[k, "value"]), 1), "mkt_rank": int(self.market.at[k, "rank"]),
                **({} if self.market.at[k, "yahoo"] else {"mkt_model": True}),  # no Yahoo rank: the model's own
                **({"games": int(self.games_week[k])} if self.sched else {}),
                **({"injury": self.inj[k]["note"], "return": self.inj[k]["return"]} if k in self.inj else {}),
                **({"lift": self.lifted[k]} if k in self.lifted else {}),  # injured teammates whose minutes he takes
                **({"proj": self.proj.at[k, "src"]} if k in self.proj.index else {}),  # whose projection his line blends in
                **{f: m[f] for f in ("owned", "waiver") if f in m},  # Yahoo roster % / on waivers until
                "z": {c: round(float(self.z.at[k, c]), 2) for c in self.cats}}

    @functools.cached_property
    def lifted(self):
        """{player: [each injured teammate whose minutes he takes (self.lift), back when, how many of the player's
        games come before that (game_days(), so after his own return), and his per-game lift in them]}, for lifts of
        half a minute or more in at least one game."""
        out = {}
        for (k, j), d in self.lift.iterrows():
            n = sum(x < self.inj[j]["return"] for x in self.game_days(k))
            if d.MIN >= 0.5 and n:
                out.setdefault(k, []).append({"by": self.p.at[j, "PLAYER_NAME"], "return": self.inj[j]["return"],
                                              "games": n, **{c: round(float(d[c]), 1) for c in ["MIN"] + COUNTING}})
        return out

    def weeks(self, n=3):
        """The n matchup weeks after this one, (start, end): the season schedule's (some run 14 days), else
        Monday to Sunday."""
        out = [(w["start"], w["end"]) for w in self.snap.get("schedule", ()) if w["start"] > self.week_end][:n]
        while len(out) < n:
            s = date.fromisoformat(out[-1][1] if out else self.week_end) + timedelta(1)
            out.append((s.isoformat(), (s + timedelta(6)).isoformat()))
        return out

    def card(self, name, punt=()):
        """One player in full (the app's player card): player() plus per-game lines (rest of season; the Yahoo or ESPN
        projection blended into it; this season, the last 14 and last 7 days from the box scores, or this season's stats
        without them; a window is left out when it holds the same games as the one before), his NBA games left this
        matchup week and in the next 3, and his rank on your build (effective(), valued on the MARKET curve like
        strategy()'s buy list) to set against his market rank."""
        k, cols = self.find(name), ["MIN"] + RAW
        lines = {"ros": (self.p.loc[k, cols], None)}
        if k in self.proj.index:  # the expert line blended into his
            x, src = self.proj.loc[k, cols].astype(float), self.proj.at[k, "src"]
            if src == "Yahoo":
                x["MIN"] = np.nan  # Yahoo shows none; experts() estimates them
            lines[src.lower()] = (x, None)
        if self.logs is None:
            if k in self.cur.index:
                lines["season"] = (self.cur.loc[k, cols], self.cur.at[k, "GP"])
        else:
            g, n = self.logs[self.logs.key == k], 0
            for w, days in (("season", None), ("last14", 14), ("last7", 7)):
                x = g if days is None else g[g.GAME_DATE >= pd.Timestamp(self.today) - pd.Timedelta(days=days)]
                if len(x) and len(x) != n:
                    lines[w], n = (x[cols].mean(), len(x)), len(x)
        pct = lambda s, m, a: round(100 * float(s[m] / s[a]), 1) if s[a] else None
        fit = self.effective(punt).rank(ascending=False, method="first")[k]
        weeks = [(self.today, self.week_end)] + self.weeks(3) if self.sched else []
        return {**self.player(k, punt), "nba": self.nba.get(k, ""),
                "lines": {w: {"gp": gp and int(gp), "MIN": None if pd.isna(s.MIN) else round(float(s.MIN), 1),
                              "FG%": pct(s, "FGM", "FGA"), "FT%": pct(s, "FTM", "FTA"),
                              **{c: round(float(s[c]), 1) for c in COUNTING}}
                          for w, (s, gp) in lines.items()},
                "weeks": [{"start": s, "end": e, "games": len(self.game_days(k, e, s))} for s, e in weeks],
                "fit_rank": int(fit), "fit": round(100 * MARKET ** (fit - 1), 1),
                **({"news": self.news([k]).get(k, [])[:3]} if self.news else {})}

    def roster_news(self, days=2):
        """News on your players and their NBA teammates in the rotation (10+ min), newest first: since the snapshot or
        the last `days` days, whichever is longer. new = after the snapshot."""
        mine = set(self.rosters[self.me])
        teams = {self.nba.get(k) for k in mine} - {None, ""}
        keys = mine | {k for k in self.p.index if self.nba.get(k) in teams and self.p.at[k, "MIN"] >= 10}
        utc = lambda t: t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        pulled = utc(datetime.fromisoformat(self.snap["pulled"])) if self.snap.get("pulled") else ""
        since = min(pulled or "9", utc(datetime.now(timezone.utc) - timedelta(days)))
        got = self.news(keys, since) if self.news else {}
        return sorted(({"name": self.p.at[k, "PLAYER_NAME"], "nba": self.nba.get(k, ""), "team": self.owner(k),
                        "mine": k in mine, "new": x["date"] > pulled, **x} for k, xs in got.items() for x in xs),
                      key=lambda x: x["date"], reverse=True)

    # --- matchup model (9-cat) -----------------------------------------------
    def vec(self, keys, il=None):
        """Weekly totals: each player's week, or his IL-slot week (week_il) if he's in `il` (default: parked here)."""
        il = self._parked if il is None else set(il)
        return (self.week.reindex([k for k in keys if k not in il]).fillna(0).sum()
                + self.week_il.reindex([k for k in keys if k in il]).fillna(0).sum()).to_numpy()

    @property
    def _parked(self):
        return set().union(*self.il.values())

    def week_vec(self, keys, start=None):
        """This week's expected totals: per-game line x games he starts the rest of this week (starts()) x
        availability. Without a schedule it's the season-average week, vec()."""
        if not self.sched:
            return self.vec(keys)
        g = pd.Series(self.starts([k for k in keys if k in self.p.index], start), dtype=float)
        return (g * self.rate.reindex(g.index)).to_numpy() @ self.p_week.loc[g.index, RAW].to_numpy()

    @functools.cached_property
    def _value(self):
        return self.value()

    @functools.cached_property
    def vecs(self):
        return {t: self.vec(ks) for t, ks in self.rosters.items()}

    @functools.cached_property
    def rows(self):
        """Weekly vector per player, as a dict of arrays for fast summing in loops."""
        return dict(zip(self.week.index, self.week.to_numpy()))

    @functools.cached_property
    def rows_il(self):
        """Same, parked in an IL slot (week_il)."""
        return dict(zip(self.week_il.index, self.week_il.to_numpy()))

    def _contrib(self, team, keys, punt=()):
        """Each player's worth to `team` in win %, for ranking drops: what its current roster loses without his line,
        for its own players and anyone else alike. (Gains with an outsider, losses without an own player, sat on opposite
        sides of the odds curve: a team behind kept a worse incoming scrub over its own better player.)
        ponytail: measured on the current roster, not the post-trade one; exact would mean a pass per combo."""
        keys, base = list(keys), self.vecs[team]
        if not keys:
            return {}
        V = np.stack([base - self.rows[k] for k in keys])
        _, O = self._others(team)
        w = self._score(self._concede(cat_probs(V[:, None], O[None], self.cats), punt)).mean(-1)
        w0 = self.outlook(team, punt=punt)["win"]
        return {k: float(w0 - x) for k, x in zip(keys, w)}

    def _score(self, p):
        """What a week is worth in the standings, from category odds p (..., cats): the expected share of categories
        won (categories: each one counts as a win), or the chance to win the matchup (one win)."""
        return match_prob(p) if self.scoring == "one_win" else p.mean(-1)

    def _concede(self, p, punt):
        """Punted categories count as losses for you: that's what committing to a punt means."""
        idx = [self.cats.index(c) for c in punt if c in self.cats]
        if idx:
            p = p.copy()
            p[..., idx] = 0.0
        return p

    def _others(self, *skip):
        names = [t for t in self.rosters if t not in skip]
        return names, (np.stack([self.vecs[t] for t in names]) if names else np.zeros((0, len(RAW))))

    def outlook(self, team, override=None, punt=()):
        """A team's win % (_score) vs every other team and on average, and per-category odds (averaged)."""
        override = override or {}
        v = override.get(team, self.vecs[team])
        opp = [t for t in self.rosters if t != team]
        O = np.stack([override.get(t, self.vecs[t]) for t in opp])
        p = self._concede(cat_probs(v[None], O, self.cats), punt)
        m = self._score(p)
        return {"win": float(m.mean()), "odds": dict(zip(self.cats, p.mean(0).tolist())),
                "vs": dict(zip(opp, m.tolist())), "cats_won": dict(zip(opp, p.sum(-1).tolist()))}

    @functools.cached_property
    def outlooks(self):
        return {t: self.outlook(t) for t in self.rosters}

    def standings(self, override=None):
        """Projected finish: every team's average weekly win % vs the rest of the league."""
        wins = ({t: o["win"] for t, o in self.outlooks.items()} if not override
                else {t: self.outlook(t, override)["win"] for t in self.rosters})
        order = sorted(wins, key=lambda t: -wins[t])
        return {t: {"win": round(wins[t], 3), "rank": i + 1} for i, t in enumerate(order)}

    def _after(self, team, out, inn, punt=()):
        """Roster after a trade, keeping its number of active players: injured players coming in take its free IL slots
        (longest out first), extra active players mean dropping whoever costs the least win % (an injured one coming in
        isn't dropped), open spots get the best free agents. Players parked in IL slots hold no active spot.
        -> (keys, dropped, added, parked)"""
        hurt = sorted((k for k in inn if self.meta.get(k, {}).get("status", "") in OUT), key=lambda k: (self.back[k], k))
        il = [k for k in self.il[team] if k not in out]
        il += hurt[:max(self.il_n - len(il), 0)]
        keys = [k for k in self.rosters[team] if k not in out] + list(inn)
        n = len(keys) - len(il) - len(self._playing(self.rosters[team]))
        dropped, added = [], []
        if n > 0:
            c = self._contrib(team, [k for k in keys if k not in il and k not in hurt], punt)
            dropped = sorted(c, key=c.get)[:n]
            keys = [k for k in keys if k not in dropped]
        elif n < 0 and self.fa:
            added = self.best_fa(-n, skip=keys)
            keys += added
        return keys, dropped, added, il

    # --- trades ------------------------------------------------------------
    def names(self, keys):
        return [self.p.at[k, "PLAYER_NAME"] for k in keys]

    def _check(self, give, get):
        """Keys for a trade's players. Every give must be yours and every get from one other team (KeyError)."""
        give, get = [self.find(n) for n in give], [self.find(n) for n in get]
        if not_mine := [k for k in give if k not in self.rosters[self.me]]:
            raise KeyError(f"Not on your roster: {', '.join(self.names(not_mine))}")
        if len({self.owner(k) for k in get}) > 1 or any(self.owner(k) in (None, "FA", self.me) for k in get):
            raise KeyError("Players you get must all come from one other team: "
                           + ", ".join(f"{p} ({self.owner(k) or 'no team'})" for k, p in zip(get, self.names(get))))
        return give, get

    def trade(self, give, get, punt=(), free=True, their_free=False):
        """Evaluate a trade for you. 9-cat: change in your weekly win % vs the league, both rosters updated
        (extra players dropped, open spots filled from waivers), on top of your free moves (after_free; free=False
        scores it on today's rosters). their_free: the other team's too; off by default, since managers judge the
        deal itself, not their own waiver options (Martin, Oct 27). Points: value net of replacement level."""
        give, get = self._check(give, get)
        if free:
            lg = self.after_free(punt, keep=give)
            if their_free and get:
                lg = lg.after_free(keep=get, team=lg.owner(get[0]))
            return lg.trade(give, get, punt, free=False)
        name = self.names
        n = len(get) - len(give)
        rv, rz = self.replacement(punt)
        cats = (self.z.loc[get].sum() - self.z.loc[give].sum() - n * rz)
        mine = float(self.value(get, punt).sum() - self.value(give, punt).sum() - n * rv)
        other = self.owner(get[0]) if get else None
        tp = self.build(other) if other in self.rosters else []
        theirs = float(self.value(give, tp).sum() - self.value(get, tp).sum() + n * self.replacement(tp)[0])
        cv, pv = self.perceived()
        swap = float(pv.reindex(give).sum() - pv.reindex(get).sum())
        seen = swap + (-self.weakest(other, -n, pv) if n < 0 and other in self.rosters else n * cv)
        out = {"give": [self.player(k, punt) for k in give], "get": [self.player(k, punt) for k in get],
               "cats": {c: round(float(cats[c]), 2) for c in self.cats}, "net": round(mine, 2), "unit": "value",
               "other_team": other, "their_build": tp, "their_net": round(theirs, 2), "their_view": round(seen, 2),
               "free": self._moves_out(self.free.get(self.me, ())),
               "their_free": self._moves_out(self.free.get(other, ())),
               "verdict": "accept" if mine > 0.25 * self.u else "decline" if mine < -0.25 * self.u else "toss-up"}
        if self.mode == "points" or other not in self.rosters:
            return out
        mk, md, ma, mil = self._after(self.me, give, get, punt)
        tk, td, ta, til = self._after(other, get, give)
        ov = {self.me: self.vec(mk, mil), other: self.vec(tk, til)}
        out["their_view"] = round(swap - self.weakest(other, len(td), pv) + len(ta) * cv, 2)  # IL slots counted
        b, a = self.outlook(self.me, punt=punt), self.outlook(self.me, ov, punt)
        to = self.outlook(other, ov)
        tb, tw = self.outlooks[other]["win"], to["win"]
        sb, sa = self.standings(), self.standings(ov)
        net = 100 * (a["win"] - b["win"])
        pct1 = lambda x: round(100 * x, 1)
        side = lambda v0, v1, o0, o1, c: {"total": [total(v0, c), total(v1, c)], "odds": [pct1(o0[c]), pct1(o1[c])]}
        cats_detail = {c: {"you": side(self.vecs[self.me], ov[self.me], b["odds"], a["odds"], c),
                           "them": side(self.vecs[other], ov[other], self.outlooks[other]["odds"], to["odds"], c)}
                       for c in self.cats}
        roster = lambda keys, inn, dropped, added: (
            sorted(({**self.player(k, punt), "move": "in" if k in inn else "add" if k in added else ""} for k in keys),
                   key=lambda p: -p["value"]) + [{**self.player(k, punt), "move": "drop"} for k in dropped])
        mine_d = sa[self.me]["win"] - sb[self.me]["win"]
        rival = {t for t in sa if t != self.me and (t == other or sb[t]["rank"] <= 4)
                 and sa[t]["win"] - sb[t]["win"] > mine_d}
        flags = []
        for k in get:
            p, why = self.player(k), []
            if p["status"] in OUT:
                why.append(f"listed {p['status']}")
            if p["avail"] < 0.7:
                why.append(f"expected to play {round(100 * p['avail'])}% of games")
            if p["gp"] < 10:
                why.append(f"only {p['gp']} games this season")
            if why:
                flags.append(f"{p['name']}: {', '.join(why)}.")
            if p["value_now"] - p["value"] > 0.5:
                flags.append(f"{p['name']} is playing {p['value_now'] - p['value']:.1f} above his real value: "
                             "you'd be buying at peak hype.")
        posset = lambda keys: {x for k in keys for x in self.meta.get(k, {}).get("pos", "").split(",") if x}
        if lost := sorted(posset(self.rosters[self.me]) - posset(mk)):
            flags.append(f"Leaves you with no {', '.join(lost)}.")
        flags += [f"{t} gains more than you ({100 * (sa[t]['win'] - sb[t]['win']):+.1f}% vs {100 * mine_d:+.1f}%)"
                  + (", and they're your trade partner." if t == other else f", and they're projected {sb[t]['rank']}.")
                  for t in sorted(rival, key=lambda t: sb[t]["rank"])]
        mkt = self._priced(give, get, md, ma, td, ta)
        d, fair = mkt["diff_pct"], TIERS["fair"][3]
        if d < -fair:
            flags.append(f"You overpay on the market: you send {mkt['give']:.0f} in market value for {mkt['get']:.0f}.")
        elif d > fair:
            flags.append(f"Lowball on the market: you get {mkt['get']:.0f} in market value for {mkt['give']:.0f}. "
                         "Expect a no unless the fit sells it.")
        out.update({
            "cats_detail": cats_detail,
            "vs": {t: [pct1(b["vs"][t]), pct1(a["vs"][t])] for t in b["vs"]},
            "standings": {t: {"rank": [sb[t]["rank"], sa[t]["rank"]], "win": [pct1(sb[t]["win"]), pct1(sa[t]["win"])],
                              "rival": t in rival} for t in sb},
            "roster_after": {"you": roster(mk, get, md, ma), "them": roster(tk, give, td, ta)},
            "flags": flags, "build_after": self._weak(self.outlook(self.me, ov)["odds"])})
        out.update({
            "unit": "win%", "value_net": out["net"], "net": round(net, 1), "their_net": round(100 * (tw - tb), 1),
            "win": round(100 * b["win"], 1), "win_after": round(100 * a["win"], 1),
            "their_win": round(100 * tb, 1), "their_win_after": round(100 * tw, 1),
            "rank": sb[self.me]["rank"], "rank_after": sa[self.me]["rank"],
            "odds": {c: round(100 * (a["odds"][c] - b["odds"][c]), 1) for c in self.cats},
            "you_drop": name(md), "you_add": name(ma), "they_drop": name(td), "they_add": name(ta), "market": mkt,
            "tiers": [t for t in TIERS if self._tier(t, net, 100 * (tw - tb), d)],
            "accept": accept(d, 100 * (tw - tb), self.s),
            "verdict": "accept" if net > VERDICT * self.s else "decline" if net < -VERDICT * self.s else "toss-up"})
        return out

    def _priced(self, give, get, md, ma, td, ta):
        """priced() for a trade: the market value each side ends up with, net of the players it drops (md, td) and the
        open spots it fills (ma, ta: a free agent is free, so each is worth `spot`, whoever it is)."""
        mv = lambda ks: float(self.market["value"].reindex(list(ks)).sum())
        return priced(mv(give) - mv(td) + len(ta) * self.spot, mv(get) - mv(md) + len(ma) * self.spot)

    def after_trade(self, give, get, punt=()):
        """The league after a trade (checked as in trade()): both rosters as trade() scores them (extra players dropped,
        yours by `punt`; open spots filled from waivers), the drops back on waivers. Recorded in .trades, with your
        drops and pickups and its market price."""
        give, get = self._check(give, get)
        other = self.owner(get[0])
        mk, md, ma, mil = self._after(self.me, give, get, punt)
        tk, td, ta, til = self._after(other, get, give)
        lg = self._copy({self.me: mk, other: tk}, [k for k in self.fa if k not in ma + ta] + md + td,
                        {self.me: set(mil), other: set(til)})
        lg.trades = [*self.trades, {"team": other, "give": give, "get": get, "drop": md, "add": ma,
                                    "market": self._priced(give, get, md, ma, td, ta)}]
        return lg

    def _step(self, x, punt=()):
        """One trade of a strategy() plan on this league (x: give and get names), then its follow-up free moves (the
        players the plan brought in are kept). None when it offers a player who's gone, or fails the fair tier's terms
        for the other side (their odds and the market, any gain for you). win: your true odds after, obj: on `punt`.
        add / drop: your waiver moves around it, net (an open spot's pickup that a free move swaps out isn't one)."""
        try:
            lg = self.after_trade(x["give"], x["get"], punt)
        except KeyError:
            return None
        t = lg.trades[-1]
        them, mkt = 100 * (lg.outlook(t["team"])["win"] - self.outlooks[t["team"]]["win"]), t["market"]["diff_pct"]
        if not self._tier("fair", np.inf, them, mkt):
            return None
        n = len(lg.free.get(self.me, ()))
        lg = lg.after_free(punt, keep=[k for tr in lg.trades for k in tr["get"]])
        new = lg.free.get(self.me, [])[n:]
        add, drop = t["add"] + [a for a, _, _ in new], t["drop"] + [d for _, d, _ in new if d]
        return {"lg": lg, "free": lg._moves_out(new), "add": self.names(k for k in add if k not in drop),
                "drop": self.names(k for k in drop if k not in add),
                "win": lg.outlook(self.me)["win"], "obj": lg.outlook(self.me, punt=punt)["win"],
                "trade": {"team": t["team"], "give": self.names(t["give"]), "get": self.names(t["get"]),
                          "their_net": round(them, 1), "market": t["market"], "accept": accept(mkt, them, self.s)}}

    def ideas(self, mode="fair", punt=(), team=None, limit=20, depth=10):
        """Trade finder. 9-cat goes through search() with mode as the tier.

        Points leagues search 1-for-1 up to 2-for-2 (no categories to trade around, so every deal is zero-sum):
        fair:    you gain a little, it's near even for them and doesn't look bad to them
        finesse: you gain more, they *look* like winners on hype (hot starts) but lose a little in reality
        """
        if self.mode != "points":
            return self.search(team=team, tier=mode, punt=punt, limit=limit, depth=depth)
        cv, pv = self.perceived()
        vm, vc = self.value(punt=punt).to_dict(), pv.to_dict()
        mine = self.value(self.rosters[self.me], punt).nlargest(depth).index.tolist()
        rv = self.replacement(punt)[0]
        u = self.u
        combos = lambda ks: [c for r in (1, 2) for c in itertools.combinations(ks, r)]
        total = lambda v, ks: sum(v[k] for k in ks)
        out = []
        for t, keys in self.rosters.items():
            if t == self.me or (team and t != team):
                continue
            vg, gv = self.value().to_dict(), self.replacement()[0]
            theirs = self.value(keys).nlargest(depth).index.tolist()
            for g in combos(mine):
                for r in combos(theirs):
                    n = len(r) - len(g)
                    me = total(vm, r) - total(vm, g) - n * rv
                    them = total(vg, g) - total(vg, r) + n * gv
                    seen = total(vc, g) - total(vc, r) + n * cv
                    if mode == "finesse":
                        ok = me > 0.6 * u and seen > 0.3 * u and them > -3 * u
                    else:
                        ok = me > 0.3 * u and them > -u and seen > -0.5 * u
                    if ok:
                        out.append((me, t, g, r, them, seen))
        return self._top_ideas(out, mode, limit, "value", 2)

    def search(self, give=(), get=(), lock=(), team=None, size=3, tier="fair", goal="win", goal_cats=(),
               goal_team=None, healthy_only=False, shape=None, punt=(), limit=20, depth=10, free=True,
               their_free=False, max_overpay=None):
        """9-cat trade search scored by the matchup model, vectorized over every give/get combo per team.

        give / get: players every deal must include. Pinning a get fixes the team and searches both whole rosters,
               otherwise it's your top `depth` vs theirs.
        lock:  players never offered or asked for.
        tier:  how likely they say yes (TIERS: your gain, theirs, and the market gap). easy: they win on market, you
               still gain. fair: market within 15%, or 30% when both gain. finesse: market even or in their favour,
               the fit strongly yours. hard: you win on market. max_overpay (%): tightens the tier's market floor.
        goal:  "win" (weekly win %), "cats" (your odds in goal_cats) or "team" (head-to-head vs goal_team).
               The last two skip deals that cost you more than GUARD win %.
        shape: "consolidate" (you send more players) or "depth" (you get more).
        free:  score deals on top of your free moves (after_free), never offering the players they pick up.
               their_free: each partner's too (as in trade()).
        """
        if self.mode == "points":
            raise KeyError("Trade search needs a category league")
        if tier not in TIERS:
            raise KeyError(f"Unknown tier {tier}: pick one of {', '.join(TIERS)}")
        if goal == "cats" and not (goal_cats and set(goal_cats) <= set(self.cats)):
            raise KeyError(f"Goal 'cats' needs categories from {', '.join(self.cats)}")
        if goal == "team" and (goal_team not in self.rosters or goal_team == self.me):
            raise KeyError(f"Goal 'team' needs another team: {', '.join(t for t in self.rosters if t != self.me)}")
        give, get = self._check(give, get)
        team = team or (self.owner(get[0]) if get else None)  # a pinned get fixes the team (and lifts _rank's cap)
        if free:
            lg, out = self.after_free(punt, keep=give), []
            each = their_free and not team  # one search per partner, each on top of its own free moves
            for t in [x for x in self.rosters if x != self.me] if each else [team]:
                lt = lg.after_free(keep=get, team=t) if their_free and t else lg
                out += lt.search(give, get, [*lock, *(a for ms in lt.free.values() for a, _, _ in ms)], t, size, tier,
                                 goal, goal_cats, goal_team, healthy_only, shape, punt, limit, depth, free=False,
                                 max_overpay=max_overpay)
            return self._rank(out, tier, team, limit)
        if max(len(give), len(get)) > size:
            raise KeyError(f"More than {size} pinned players on one side")
        if get:
            if team and team != self.owner(get[0]):
                raise KeyError(f"{self.names(get)[0]} plays for {self.owner(get[0])}, not {team}")
            team = self.owner(get[0])
        lock = {self.find(n) for n in lock} - set(give) - set(get)
        cats, me, wide = self.cats, self.me, bool(get)
        cv, pv = self.perceived()
        vg, vc, vm = self.value().to_dict(), pv.to_dict(), self.value(punt=punt).to_dict()
        mv = self.market["value"].to_dict()

        def combos(keys, pins, v, skip=()):
            free = sorted((k for k in keys if k not in lock and k not in pins and k not in skip), key=lambda k: -v[k])
            free = free if wide else free[:depth]
            return [tuple(pins) + c for s in range(max(1, len(pins)), size + 1)
                    for c in itertools.combinations(free, s - len(pins))]

        injured = lambda k: self.meta.get(k, {}).get("status", "") in OUT

        def stack(cs, il):  # summed weekly vectors (active, and as held: parked ones at week_il), players, active
            # players, parked ones, summed perceived and market value
            return (np.stack([sum(self.rows[k] for k in c) for c in cs]),
                    np.stack([sum(self.rows_il[k] if k in il else self.rows[k] for k in c) for c in cs]),
                    np.array([len(c) for c in cs]), np.array([sum(k not in il for k in c) for c in cs]),
                    np.array([sum(k in il for k in c) for c in cs]),
                    np.array([sum(vc[k] for k in c) for c in cs]), np.array([sum(mv[k] for k in c) for c in cs]))

        def parked(cs):  # per combo: injured players (they take the receiver's free IL slots, as in _after), and what
            cut = np.zeros((len(cs), size + 1, len(RAW)))  # parking the first 0..size of them takes off its week
            for i, c in enumerate(cs):
                for j, k in enumerate(sorted(filter(injured, c), key=lambda k: (self.back[k], k))):
                    cut[i, j + 1:] += self.rows[k] - self.rows_il[k]
            return np.array([sum(map(injured, c)) for c in cs]), cut

        def low(groups, c):  # per group: the `size` players the team would miss least (padded with inf), vectors, market
            vals, vecs, mk = (np.full((len(groups), size), np.inf), np.zeros((len(groups), size, len(RAW))),
                              np.zeros((len(groups), size)))
            for i, g in enumerate(groups):
                for j, k in enumerate(sorted(g, key=c.get)[:size]):
                    vals[i, j], vecs[i, j], mk[i, j] = c[k], self.rows[k], mv[k]
            return vals, vecs, mk

        def drops(A, B, n):  # vector and market value of the n cheapest of A's (per give combo) and B's (per get combo)
            a, b = len(A[0]), len(B[0])
            pair = lambda x, y: np.concatenate([np.broadcast_to(x[:, None], (a, b) + x.shape[1:]),
                                                np.broadcast_to(y[None], (a, b) + y.shape[1:])], 2)
            pick = np.argsort(pair(A[0], B[0]), -1)[..., :size]
            cut, mk = np.take_along_axis(pair(A[1], B[1]), pick[..., None], 2), np.take_along_axis(pair(A[2], B[2]), pick, 2)
            on = np.arange(size) < n[..., None]
            return (on[..., None] * cut).sum(2), (on * mk).sum(2)

        fa, fam = np.zeros((size + 1, len(RAW))), np.arange(size + 1) * self.spot  # the best 0..size free agents
        for i, k in enumerate(self.best_fa(size)):  # (vectors; market: open spots), for whoever ends up with open spots
            fa[i + 1:] += self.rows[k]

        roster, il_me = self.rosters[me], self.il[me]
        mine = combos(roster, give, vm)
        GV, GH, gn, ga, gp, gseen, gmk = stack(mine, il_me)
        g_hurt, g_cut = parked(mine)
        my_rest = low([[k for k in roster if k not in c and k not in il_me] for c in mine], self._contrib(me, roster, punt))
        I = np.arange(len(mine))[:, None]
        before = self.outlook(me, punt=punt)
        out = []
        for t, keys in self.rosters.items():
            if t == me or (team and t != team):
                continue
            hurt = [k for k in keys if healthy_only and self.meta.get(k, {}).get("status", "") in OUT]
            theirs = combos(keys, get, vg, hurt)
            if not theirs:
                continue
            il_t, J = self.il[t], np.arange(len(theirs))[None]
            RV, RH, rn, ra, rp, rseen, rmk = stack(theirs, il_t)
            r_hurt, r_cut = parked(theirs)
            c_in = self._contrib(me, {k for c in theirs for k in c}, punt)  # what their players would add for you
            c_t = self._contrib(t, set(keys) | {k for c in mine for k in c})
            # injured players coming in park in free IL slots (the ones a side sends free theirs up); the rest need
            # active spots: n_me / n_t extra active players you / they end up with
            to_me = np.minimum(r_hurt[None], np.maximum(self.il_n - len(il_me) + gp[:, None], 0))
            to_t = np.minimum(g_hurt[:, None], np.maximum(self.il_n - len(il_t) + rp[None], 0))
            n_me, n_t = rn[None] - to_me - ga[:, None], gn[:, None] - to_t - ra[None]
            healthy = lambda cs: [[k for k in c if not injured(k)] for c in cs]  # an injured player coming in stays
            my_drop, my_drop_m = drops(my_rest, low(healthy(theirs), c_in), n_me)
            their_drop, their_drop_m = drops(low(healthy(mine), c_t),
                                             low([[k for k in keys if k not in c and k not in il_t] for c in theirs], c_t), n_t)
            me_after = self.vecs[me] - GH[:, None] + RV[None] - r_cut[J, to_me] - my_drop + fa[np.clip(-n_me, 0, size)]
            them_after = self.vecs[t] - RH[None] + GV[:, None] - g_cut[I, to_t] - their_drop + fa[np.clip(-n_t, 0, size)]
            give_m = gmk[:, None] - their_drop_m + fam[np.clip(-n_t, 0, size)]  # market value each side ends up with
            get_m = rmk[None] - my_drop_m + fam[np.clip(-n_me, 0, size)]
            mkt = 100 * (get_m - give_m) / np.maximum(np.maximum(give_m, get_m), 1)  # as in priced()
            names, O = self._others(me, t)
            k = len(O) + 1
            p_mt = cat_probs(me_after, them_after, cats)
            p_o = cat_probs(me_after[:, :, None], O[None, None], cats)  # (give combo, get combo, other team, cat)
            m_o, m_t = self._score(self._concede(p_o, punt)), self._score(self._concede(p_mt, punt))
            dme = 100 * ((m_o.sum(-1) + m_t) / k - before["win"])
            t_o = self._score(cat_probs(them_after[:, :, None], O[None, None], cats)).sum(-1)
            dthem = 100 * ((t_o + self._score(1 - p_mt)) / k - self.outlooks[t]["win"])
            weak = np.concatenate([[0], np.cumsum(np.sort(pv.reindex(self._playing(keys)).to_numpy()))])  # for drops
            seen = gseen[:, None] - rseen[None] + np.where(n_t < 0, -n_t * cv, -weak[np.clip(n_t, 0, len(weak) - 1)])
            if goal == "team":
                h2h = m_t if goal_team == t else m_o[..., names.index(goal_team)]
                score = 100 * (h2h - before["vs"][goal_team])
            elif goal == "cats":
                idx = [cats.index(c) for c in goal_cats]
                odds = ((p_o.sum(-2) + p_mt) / k)[..., idx].mean(-1)
                score = 100 * (odds - np.mean([before["odds"][c] for c in goal_cats]))
            else:
                score = dme
            ok = self._tier(tier, score, dthem, mkt, max_overpay)
            if goal != "win":
                ok &= dme > -GUARD * self.s
            if shape == "consolidate":
                ok &= gn[:, None] > rn[None]
            elif shape == "depth":
                ok &= gn[:, None] < rn[None]
            G, R = np.nonzero(ok)
            rank = np.minimum(score, dthem)[G, R] if tier in ("easy", "fair") else score[G, R]
            top = np.argsort(-rank)[:limit]
            for gi, ri in zip(G[top], R[top]):
                m = priced(float(give_m[gi, ri]), float(get_m[gi, ri]))
                out.append({"team": t, "give": self.names(mine[gi]), "get": self.names(theirs[ri]),
                            "net": round(float(dme[gi, ri]), 1), "their_net": round(float(dthem[gi, ri]), 1),
                            "their_view": round(float(seen[gi, ri]), 2), "unit": "win%", "market": m,
                            "accept": accept(m["diff_pct"], dthem[gi, ri], self.s),
                            **({"score": round(float(score[gi, ri]), 1)} if goal != "win" else {})})
        return self._rank(out, tier, team, limit)

    @staticmethod
    def _rank(out, tier, team, limit):
        s = lambda x: x.get("score", x["net"])
        out.sort(key=lambda x: -min(s(x), x["their_net"]) if tier in ("easy", "fair") else -s(x))
        if not team:  # spread suggestions across teams
            per_team = {}
            out = [o for o in out if per_team.setdefault(o["team"], []).append(o) or len(per_team[o["team"]]) <= 3]
        return out[:limit]

    def _top_ideas(self, out, mode, limit, unit, digits):
        out.sort(key=lambda x: -min(x[0], x[4]) if mode == "fair" else -x[0])
        per_team = {}
        out = [o for o in out if per_team.setdefault(o[1], []).append(o) or len(per_team[o[1]]) <= 3]
        names = lambda ks: [self.p.at[k, "PLAYER_NAME"] for k in ks]
        return [{"team": t, "give": names(g), "get": names(r), "net": round(me, digits), "their_net": round(th, digits),
                 "their_view": round(se, 2), "unit": unit} for me, t, g, r, th, se in out[:limit]]

    # --- team / waivers / movers -------------------------------------------
    @functools.cached_property
    def totals(self):
        """Per-team category totals: sum of player z per game."""
        return pd.DataFrame({t: self.z.loc[ks].sum() for t, ks in self.rosters.items()}).T

    def build(self, team):
        """Categories a team is effectively punting: up to 2 it wins under WEAK of the time across the league."""
        return [] if self.mode == "points" else self._weak(self.outlooks[team]["odds"])

    @staticmethod
    def _weak(odds):
        return [c for c in sorted(odds, key=odds.get)[:2] if odds[c] < WEAK]

    def profile(self):
        """Your weekly win %, projected standings, per-category odds and category standings (every team's odds
        and rank in each category), H2H vs each team, punt suggestions."""
        tot = self.totals
        odds = pd.DataFrame({t: o["odds"] for t, o in self.outlooks.items()}).T[self.cats]  # team x category
        ranks = odds.rank(ascending=False, method="min").astype(int)  # projected category standings
        o, n = self.outlooks[self.me], len(self.cats)
        vs = {t: {"pct": round(o["vs"][t], 3), "win": round(o["cats_won"][t], 1), "loss": round(n - o["cats_won"][t], 1)}
              for t in o["vs"]}
        st = self.standings()
        return {"totals": tot.round(2).to_dict("index"), "ranks": ranks.to_dict("index"), "vs": vs,
                "cat_odds": odds.round(3).to_dict("index"), "scoring": self.scoring,
                "odds": {c: round(p, 3) for c, p in o["odds"].items()}, "win": round(o["win"], 3),
                "rank": st[self.me]["rank"], "standings": st,
                "punt_suggestions": self.build(self.me), "builds": {t: self.build(t) for t in self.rosters},
                "strengths": sorted(o["odds"], key=lambda c: -o["odds"][c])[:3], "cheap": self.cheapest()}

    def cheapest(self):
        """Categories where one more player-z (a week of games) raises your odds most, averaged over the league: where a
        trade or stream pays off. Odds near 50% move most, but a noisy category (STL) moves less than its odds suggest."""
        pool = self.p.nlargest(self.teams_n * self.roster_n, "MIN")  # zscores()' pool: one z = one pool std
        unit = np.zeros((len(self.cats), len(RAW)))
        for i, c in enumerate(self.cats):
            if c in ("FG%", "FT%"):  # makes over the pool's rate at the same attempts
                m, a = c[:2] + "M", c[:2] + "A"
                unit[i, RAW.index(m)] = (pool[m] - pool[m].sum() / pool[a].sum() * pool[a]).std()
            else:
                unit[i, RAW.index(c)] = pool[c].std() * (-1 if c == "TOV" else 1)
        _, O = self._others(self.me)
        up = cat_probs((self.vecs[self.me] + GAMES_WEEK * unit)[:, None], O[None], self.cats).mean(1).diagonal()
        odds = self.outlooks[self.me]["odds"]
        return sorted(({"cat": c, "odds": round(odds[c], 3), "per_z": round(100 * float(u - odds[c]), 1)}
                       for c, u in zip(self.cats, up)), key=lambda x: -x["per_z"])

    def strategy(self, size=3, top=5, steps=3):
        """Punt planner. Conceding a category only lowers today's odds: a punt pays off through the moves it unlocks,
        often over several trades, the first of which costs the most. So each build (staying balanced, each category,
        PAIRS) makes its own free moves, then up to `steps` trades in a row: each the one that lifts its own (punted)
        odds most of its top `top` fair deals (search() with that punt on the rosters by then, size 2 after the first
        for speed), followed by its free moves again (_step). Every step is scored on true odds, nothing conceded, and
        a build keeps its best prefix, or the single trade that lifts true odds most. Balanced also tries every build's
        first deals and replays every build's chain while each trade lifts true odds, since it could make them too: a
        punt only wins through what it alone unlocks (its waiver moves, or trades that only pay after a costly first
        one). pick: the build to play, [] (stay balanced) unless a punt beats balanced by more than VERDICT.
        stars: your top 5 players' weak spots (z <= -1) the build concedes. buy: the 5 players on other teams the
        market prices furthest below their worth to the build (its effective() rank on the MARKET curve)."""
        if self.mode == "points":
            raise KeyError("The punt planner needs a category league")
        me, now = self.me, round(100 * self.outlook(self.me)["win"], 1)
        builds = [[]] + [[c] for c in self.cats] + [list(p) for p in PAIRS if set(p) <= set(self.cats)]
        found = [self.search(tier="fair", punt=b, size=size, limit=top) for b in builds]
        every = list({(frozenset(x["give"]), frozenset(x["get"])): x for xs in found for x in xs}.values())
        lifts = lambda st, xs, b: [s for x in xs if (s := st["lg"]._step(x, b)) and s["obj"] > st["obj"]]
        plans = []
        for b, xs in zip(builds, found):
            lg = self.after_free(b)
            free = lg._moves_out(lg.free.get(me, ()))
            path = [{"lg": lg, "free": free, "trade": None, "add": [m["add"] for m in free],
                     "drop": [m["drop"] for m in free if m["drop"]],
                     "win": lg.outlook(me)["win"], "obj": lg.outlook(me, punt=b)["win"]}]
            nxt = lifts(path[0], xs if b else every, b)
            opts = [path[:1]] + ([[path[0], max(nxt, key=lambda s: s["win"])]] if nxt else [])
            while nxt:
                path.append(max(nxt, key=lambda s: s["obj"]))
                if len(path) > steps:
                    break
                cur = path[-1]["lg"]  # players the plan brought in or sent away are never traded again
                nxt = lifts(path[-1], cur.search(tier="fair", punt=b, size=min(size, 2), limit=top, free=False,
                                                 lock=set(cur.rosters[me]) ^ set(self.rosters[me])), b)
            plans.append((b, path, opts + [path[:i] for i in range(2, len(path) + 1)]))
        (_, (start, *_), flat_opts) = plans[0]
        for _, path, _ in plans[1:]:  # balanced replays each chain while every trade lifts true odds
            r = [start]
            while len(r) < len(path) and (s := lifts(r[-1], [path[len(r)]["trade"]], [])):
                r += s
            flat_opts.extend(r[:i] for i in range(2, len(r) + 1))
        top5 = self.value(self.rosters[me]).nlargest(5).index
        theirs = [k for t, ks in self.rosters.items() if t != me for k in ks]
        later = lambda t: not (set(t["give"]) <= set(self.rosters[me])  # needs an earlier step first
                               and {self.owner(k) for k in t["get"]} == {t["team"]})
        out = []
        for b, _, opts in plans:
            best = max(opts, key=lambda p: p[-1]["win"])  # the first best, so the fewest moves on a tie
            win = round(100 * best[-1]["win"], 1)
            fit = self.effective(b).rank(ascending=False)
            gap = 100 * MARKET ** (fit - 1) - self.market["value"]
            out.append({"punt": b, "win": win, "gain": round(win - now, 1),
                        "steps": [{"trade": s["trade"] and {**s["trade"], "later": later(s["lg"].trades[-1])},
                                   **{k: s[k] for k in ("free", "add", "drop")}, "win": round(100 * s["win"], 1)}
                                  for s in best],
                        "stars": [{"name": self.p.at[k, "PLAYER_NAME"], "cat": c, "z": round(float(self.z.at[k, c]), 1)}
                                  for k in top5 for c in b if self.z.at[k, c] <= -1],
                        "buy": [{**self.player(k, b), "fit_rank": int(fit[k]), "gap": round(float(g), 1)}
                                for k, g in gap.reindex(theirs).nlargest(5).items() if g > 0]})
        out.sort(key=lambda x: -x["win"])  # stable: balanced wins ties
        edge = round(out[0]["win"] - next(x["win"] for x in out if not x["punt"]), 1)  # 62.1 - 61.9 isn't 0.2 in floats
        return {"win": now, "builds": out, "pick": out[0]["punt"] if edge > VERDICT * self.s else [], "edge": edge}

    def waivers(self, punt=(), limit=15):
        """Best free agents for your team + add/drop pairs. 9-cat: scored by the change in your weekly win %."""
        if self.mode != "points" and self.fa:
            return self._waivers_matchup(punt, limit)
        fa = self.value(self.fa, punt).nlargest(limit)
        roster = [k for k in self.rosters[self.me] if self.meta.get(k, {}).get("status", "") not in OUT]
        drops = self.value(roster, punt).nsmallest(3)
        moves = [{"add": self.p.at[a, "PLAYER_NAME"], "drop": self.p.at[d, "PLAYER_NAME"],
                  "gain": round(float(fa[a] - dv), 2), "unit": "value"}
                 for a in fa.index for d, dv in drops.items() if fa[a] - dv > 0.3 * self.u]
        moves.sort(key=lambda m: -m["gain"])
        return {"free_agents": [self.player(k, punt) for k in fa.index],
                "drop_candidates": [self.player(k, punt) for k in drops.index], "moves": moves[:8]}

    def _waivers_matchup(self, punt, limit):
        """Every (add, drop) pair scored by the change in your weekly win % vs the league (drop None: an open spot),
        and the free moves trades are scored on top of."""
        roster = self.rosters[self.me]
        adds, drops = self._adds(punt), self._drops(roster)
        gain = self._gains(self.me, roster, adds, drops, punt)
        fit, best_drop = gain.max(1), gain.argmax(1)
        order = np.argsort(-fit)[:limit]
        moves = [{**self._moves_out([(adds[i], drops[best_drop[i]], fit[i])])[0], "unit": "win%"}
                 for i in order if fit[i] > MOVE * self.s][:8]
        cheap = [j for j in np.argsort(-gain.max(0)) if drops[j]][:3]  # drops that cost least when swapped for the best add
        return {"free_agents": [{**self.player(adds[i], punt), "fit": round(float(fit[i]), 1)} for i in order],
                "drop_candidates": [self.player(drops[j], punt) for j in cheap], "moves": moves,
                "free": self._moves_out(self.free_moves(punt))}

    def _adds(self, punt):
        return self.value(self.fa, punt).nlargest(40).index.tolist()

    def _drops(self, roster, keep=()):
        """Who an add can replace: healthy active players outside `keep`, plus nobody (None) while the active roster has
        an open spot."""
        drops = [k for k in self._playing(roster) if k not in keep and self.meta.get(k, {}).get("status", "") not in OUT]
        return drops + [None] * (len(self._playing(roster)) < self.roster_n)

    def _gains(self, team, roster, adds, drops, punt):
        """Change in team's win % vs the league from swapping each add in for each drop on `roster`: (adds, drops)."""
        V, D = self.vec(roster), np.stack([self.rows[k] if k else np.zeros(len(RAW)) for k in drops])
        A = np.stack([self.rows[k] for k in adds])
        _, O = self._others(team)
        win = lambda X: self._score(self._concede(cat_probs(X[..., None, :], O, self.cats), punt)).mean(-1)
        return 100 * (win(V - D[None] + A[:, None]) - win(V))

    def free_moves(self, punt=(), keep=(), team=None):
        """Waiver moves worth making without a trade: the best add/drop (or plain add into an open spot) by win %,
        repeated while one still clears MOVE. keep: players never dropped (a trade's). team: yours by default (punt is
        only yours). [(add, drop or None, gain)]
        ponytail: greedy and ignores the weekly adds cap; a cap only delays moves, rest of season."""
        if self.mode == "points" or not self.fa:
            return []
        team = team or self.me
        roster, adds, moves = list(self.rosters[team]), self._adds(punt), []
        while adds:
            drops = self._drops(roster, keep)
            g = self._gains(team, roster, adds, drops, punt)
            i, j = np.unravel_index(g.argmax(), g.shape)
            if g[i, j] <= MOVE * self.s:
                break
            moves.append((adds.pop(i), drops[j], float(g[i, j])))
            roster = [k for k in roster if k != drops[j]] + [moves[-1][0]]
        return moves

    def after_free(self, punt=(), keep=(), team=None):
        """The league after a team's free moves (yours by default): trades and search are scored on top of it, so a
        deal gets no credit for a pickup that team could make anyway, and an open spot it leaves gets the next-best
        free agent. .free[team] holds every free move made on the copy so far."""
        team = team or self.me
        moves = self.free_moves(punt, keep, team)
        if not moves:
            return self
        roster, fa = list(self.rosters[team]), list(self.fa)
        for a, d, _ in moves:
            roster, fa = [k for k in roster if k != d] + [a], [k for k in fa if k != a] + [d] * bool(d)
        lg = self._copy({team: roster}, fa)
        lg.free = {**self.free, team: [*self.free.get(team, ()), *moves]}
        return lg

    def _copy(self, rosters, fa, il=None):
        """A copy of the league with these rosters, free agents and IL slots (after_free, after_trade)."""
        lg = copy.copy(self)
        for c in ("vecs", "outlooks", "totals"):  # cached on today's rosters
            lg.__dict__.pop(c, None)
        lg.rosters, lg.fa, lg.il = {**self.rosters, **rosters}, fa, {**self.il, **(il or {})}
        return lg

    def _moves_out(self, moves):
        name = lambda k: self.p.at[k, "PLAYER_NAME"] if k else None
        return [{"add": name(a), "drop": name(d), "gain": round(float(g), 1)} for a, d, g in moves]

    def _playing(self, keys):
        """Players not parked in an IL slot: the ones on the active roster."""
        parked = self._parked
        return [k for k in keys if k not in parked]

    def this_week(self, opp=None, punt=(), limit=5):
        """This week's matchup: totals so far + the rest of the week (week_vec, players in IL slots sit) -> odds per
        category, the expected score, the chance to win more categories (win), a safe / chase / lost call per
        category, and streaming adds scored on this week's win % (_score; the 0.1 floor is display resolution).
        opp defaults to the snapshot's opponent; with none, returns {"opponent": None, "teams": [...]} to pick from."""
        if self.mode == "points":
            raise KeyError("This week needs a category league")
        me, m = self.me, self.matchup
        others = [t for t in self.rosters if t != me]
        opp = opp or m.get("opponent")
        if not opp:
            return {"opponent": None, "teams": others, "week_end": self.week_end}
        if opp not in others:
            raise KeyError(f"Unknown opponent {opp}: pick one of {', '.join(others)}")
        so_far = {t: np.array([float(m.get("totals", {}).get(t, {}).get(s, 0)) for s in RAW]) for t in (me, opp)}
        play = lambda t: self._playing(self.rosters[t])
        left = {t: self.week_vec(play(t)) for t in (me, opp)}
        odds = lambda v: self._concede(cat_probs(v, left[opp], self.cats, so_far[me], so_far[opp]), punt)
        p = odds(left[me])
        win = float(match_prob(p))

        def clears(k):  # a waiver claim only plays from the day it clears ("Jan 2" read on Dec 30 is next year)
            w = self.meta.get(k, {}).get("waiver")
            if not w:
                return {}
            d = datetime.strptime(f"{w} {self.today[:4]}", "%b %d %Y").date()
            return {k: (d.replace(year=d.year + 1) if d < date.fromisoformat(self.today) - timedelta(60) else d).isoformat()}

        # streams: each healthy free agent for whichever of your 3 cheapest healthy players (season win-% cost) helps most,
        # scored on the whole lineup since an add only counts on days he cracks your top `lineup`
        healthy = lambda ks: [k for k in ks if self.meta.get(k, {}).get("status", "") not in OUT]
        cost = self._contrib(me, healthy(play(me)), punt)
        drops, adds = sorted(cost, key=cost.get)[:3], healthy(self.fa)
        swap = lambda a, d: [k for k in play(me) if k != d] + [a]
        streams = []
        if drops and adds:
            V = np.array([[self.week_vec(swap(a, d), clears(a)) for d in drops] for a in adds])
            gain = 100 * (self._score(odds(V)) - self._score(p))
            season0 = self.outlook(me, punt=punt)["win"]
            for i in np.argsort(-gain.max(1))[:limit]:
                a, d = adds[i], drops[gain[i].argmax()]
                if gain[i].max() < 0.1:
                    break
                season = self.outlook(me, {me: self.vecs[me] - self.rows[d] + self.rows[a]}, punt)["win"]
                g = self.starts(swap(a, d), clears(a))[a]
                streams.append({**self.player(a, punt), **({"games": g} if self.sched else {}),
                                "gain": round(float(gain[i].max()), 1), "drop": self.p.at[d, "PLAYER_NAME"],
                                "season": round(100 * (season - season0), 1)})
        call = lambda c, x: "punt" if c in punt else "safe" if x >= 1 - WEAK else "lost" if x <= WEAK else "chase"
        return {"opponent": opp, "teams": others, "week_end": self.week_end,
                "live": m.get("opponent") == opp and all(t in m.get("totals", {}) for t in (me, opp)),
                **({"yahoo_games": {"you": m["games"][me], "them": m["games"][opp]}}  # Yahoo's played / left, as set
                   if m.get("opponent") == opp and all(t in m.get("games", {}) for t in (me, opp)) else {}),
                "win": round(100 * win, 1), "score": [round(float(p.sum()), 1), round(len(self.cats) - float(p.sum()), 1)],
                **({"games": {"you": sum(self.starts(play(me)).values()),
                              "them": sum(self.starts(play(opp)).values())}} if self.sched else {}),
                "cats": {c: {"you": [total(so_far[me], c), total(so_far[me] + left[me], c)],
                             "them": [total(so_far[opp], c), total(so_far[opp] + left[opp], c)],
                             "odds": round(100 * float(x), 1), "call": call(c, x)} for c, x in zip(self.cats, p)},
                "streams": streams}

    def movers(self, punt=(), limit=10):
        """Sell high: your players beating their ROS value right now. Buy low: good ROS players slumping on other teams."""
        gap = self.value(punt=punt, frame="cur") - self.value(punt=punt)
        played = self.p.GP_CUR >= 3
        mine = [k for k in self.rosters[self.me] if played.get(k, False)]
        others = [k for t, ks in self.rosters.items() if t != self.me for k in ks if played.get(k, False)]
        good = self.value(others, punt) > (1.5 if self.mode != "points" else 30)
        sell = gap.reindex(mine).nlargest(limit)
        buy = gap.reindex([k for k in others if good[k]]).nsmallest(limit)
        fmt = lambda s: [{**self.player(k, punt), "gap": round(float(v), 2)} for k, v in s.items()]
        return {"sell_high": fmt(sell[sell > 0.5 * self.u]), "buy_low": fmt(buy[buy < -0.5 * self.u]),
                "note": f"Based on {int(self.p.GP_CUR.max())} games so far — small samples swing hard early."}

    def dropped(self, punt=(), limit=10):
        """Players dropped in the snapshot's transactions (newest first) that nobody has picked up since."""
        out, seen = [], set()
        for t in self.snap.get("transactions", ()):
            name = t.get("dropped") or (t["player"] if t["type"] == "drop" else None)
            k = self.find(name, strict=False) if name else None
            if k and k not in seen and not any(k in ks for ks in self.rosters.values()):
                seen.add(k)
                out.append({**self.player(k, punt), "by": t["team"], "when": t["when"]})
        return out[:limit]

    def state(self, punt=()):
        return {"league": self.snap.get("name", "League"), "mode": self.mode, "scoring": self.scoring, "cats": self.cats,
                "me": self.me, "teams": list(self.rosters), "fetched": self.snap.get("fetched", ""), "demo": self.snap.get("demo", False),
                "pulled": self.snap.get("pulled", ""), "rolled": self.rolled,  # snapshot time; its week is over
                "roster": sorted((self.player(k, punt) for k in self.rosters[self.me]), key=lambda p: -p["value"]),
                "profile": self.profile() if self.mode != "points" else None,
                "waivers": {**self.waivers(punt), "dropped": self.dropped(punt)}, "movers": self.movers(punt)}

    def team_roster(self, team, punt=()):
        return sorted((self.player(k, punt) for k in self.rosters[team]), key=lambda p: -p["value"])


def load(path=DATA / "league.json"):
    if not Path(path).exists():
        import yahoo
        yahoo.demo()
    return League(json.loads(Path(path).read_text(encoding="utf-8")), sched=schedule(), inj=injuries(),
                  logs=gamelogs(CUR, 12), proj=espn(), news=news)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["team", "trade", "ideas", "search", "waivers", "movers", "roster", "week",
                                    "strategy", "news"])
    ap.add_argument("--give", default="")
    ap.add_argument("--get", default="")
    ap.add_argument("--mode", default="fair", choices=["fair", "finesse"])
    ap.add_argument("--team", help="limit ideas / search / show roster for one team")
    ap.add_argument("--punt", default="", help='e.g. "FT%%,TOV"')
    ap.add_argument("--lock", default="", help="search: players never traded")
    ap.add_argument("--tier", default="fair", choices=list(TIERS), help="search: how likely they say yes")
    ap.add_argument("--goal", default="win", choices=["win", "cats", "team"])
    ap.add_argument("--cats", default="", help='search --goal cats: e.g. "STL,AST"')
    ap.add_argument("--vs", help="search --goal team: the team to beat. week: this week's opponent")
    ap.add_argument("--size", type=int, default=3, help="search: max players per side")
    ap.add_argument("--healthy", action="store_true", help="search: skip injured players you'd get")
    ap.add_argument("--shape", choices=["consolidate", "depth"])
    ap.add_argument("--max-overpay", type=float, help="search: most you'd overpay on the market, in %%")
    a = ap.parse_args()
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]
    lg, punt = load(), split(a.punt)
    out = {"team": lambda: {**lg.profile(), "roster": lg.team_roster(lg.me, punt)},
           "trade": lambda: lg.trade(split(a.give), split(a.get), punt),
           "ideas": lambda: lg.ideas(a.mode, punt, a.team),
           "search": lambda: lg.search(split(a.give), split(a.get), split(a.lock), a.team, a.size, a.tier, a.goal,
                                       split(a.cats), a.vs, a.healthy, a.shape, punt, max_overpay=a.max_overpay),
           "waivers": lambda: lg.waivers(punt),
           "movers": lambda: lg.movers(punt),
           "week": lambda: lg.this_week(a.vs, punt),
           "strategy": lambda: lg.strategy(a.size),
           "news": lg.roster_news,
           "roster": lambda: lg.team_roster(a.team or lg.me, punt)}[a.cmd]()
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
