"""Fantasy basketball engine: player values, team outlooks, trades, waivers, sell-high/buy-low.

Values are per game, rest of season (ROS): last season (or your projections) shrunk toward this season
as games pile up. 9-cat uses z-scores vs the rostered pool; points uses the league's scoring weights.

9-cat decisions (trades, waivers, standings) use a matchup model on top of that: each team's expected weekly
totals -> odds of winning each category vs every other team -> odds of winning the matchup (5 of 9).
A move is judged by how much it changes your weekly win %, so a category you already dominate or can't win
stops mattering on its own (punting emerges), and the other teams' rosters are part of every answer.

CLI (prints JSON, used by Claude Code and for quick checks):
  python model.py team                      # win odds, projected standings, category odds, punt suggestions
  python model.py trade --give "A" --get "B, C"
  python model.py ideas --mode finesse      # fair | finesse
  python model.py search --get "Wembanyama" --tier easy --lock "Jokic"   # offers for a target, see --help
  python model.py waivers
  python model.py movers                    # sell-high / buy-low
  python model.py week --vs "Team"          # this week's matchup (--vs defaults to the snapshot's opponent)
  add --punt "FT%,TOV" to any of them
"""
import argparse
import difflib
import functools
import itertools
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta
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
SHRINK = 20  # games of current season that count as much as the baseline
OLDER_W, REGRESS = 0.5, 25  # baseline: a game two seasons ago counts half; 25 games of a fringe line pull small samples in
HYPE = 10    # same, for how other managers see players (hot starts count more: 3 games = 23%, 10 games = half)
OUT = {"O", "INJ", "IL", "IL+", "NA", "SUSP"}  # Yahoo statuses that mean "not playing now"

# Matchup model. DISPERSION, SCHED_CV and PCT_NOISE are fitted to real weeks of 2025-26 box scores (backtest.py: 20
# simulated leagues, predictions from what was known each Monday); re-run it after changing how lines are projected.
GAMES_WEEK = 3.4                                   # NBA games per team per week
DISPERSION = {"FG3M": 1.75, "PTS": 3.0, "REB": 2.5, "AST": 3.0, "STL": 1.0, "BLK": 1.5, "TOV": 1.25}  # variance / mean
SCHED_CV = 0.15                                    # week-to-week swing beyond box-score noise (schedule, rest, minutes,
                                                   # and projection error)
PCT_NOISE = 1.75                                   # FG%/FT% variance vs coin-flip shooting at each player's rate
WEAK = 0.35                                        # a "weak spot" / punt hint: winning a category under 35% of
                                                   # weeks (about the bottom 10% of team-categories once odds are calibrated)
FILL = 0.5                                         # share of a sat-out player's games your bench/streamers cover
STATUS_AVAIL = {"GTD": 0.9, "DTD": 0.9, "O": 0.7, "SUSP": 0.8, "INJ": 0.6, "IL": 0.5, "IL+": 0.4, "NA": 0.0}

# Trade search difficulty: (min gain for you, min gain for them, both win %; min "looks to them" in value units).
# A gradual ladder: they clearly gain / both gain / they lose a little but it looks good / you gain big, needs a pitch.
# ponytail: starting guesses like the knobs above; tune once real offers get accepted or turned down.
TIERS = {"easy": (0.5, 1, 0.3), "fair": (1, 0, -0.5), "finesse": (1.5, -2.5, 0.3), "hard": (3, -6, -1)}


def accept(their_net, their_view, u=1.0):
    """How likely the other manager says yes: they mostly judge on how it looks (Yahoo ranks), a little on real fit."""
    if their_view > 0.3 * u and their_net > -1:
        return "Likely yes"
    return "Long shot" if their_view < -0.5 * u else "Maybe"


def norm(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"[^a-z0-9 ]", "", name)
    return " ".join(re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name).split())


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
    """{player name: {status, note, return}} from ESPN's public injury feed, cached for 3 hours.
    ponytail: unofficial endpoint (personal use); if ESPN changes it, this goes quiet and Yahoo's status tags still work."""
    def get():
        url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
        with urllib.request.urlopen(url, timeout=30) as r:
            feed = json.load(r)
        status = {"Out": "O", "Day-To-Day": "DTD", "Suspension": "SUSP"}
        return {i["athlete"]["displayName"]: {"status": status.get(i.get("status"), ""), "note": i.get("shortComment", ""),
                                              "return": i.get("details", {}).get("returnDate", "")}
                for team in feed.get("injuries", []) for i in team.get("injuries", [])}
    return _cached(DATA / "injuries.json", 3, get)


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


def ros(base, cur, k=SHRINK):
    """Rest-of-season per-game line. weight on this season = GP / (GP + k).
    Rookies (no baseline) start from a fringe-roster line, so 3 hot games don't make them stars."""
    cols = ["MIN"] + RAW
    rookies = cur.index.difference(base.index)
    out = pd.concat([base, cur.loc[rookies]])
    out.loc[rookies, cols] = base.nlargest(200, "MIN").tail(60)[cols].mean().to_numpy()
    both = out.index.intersection(cur.index)
    w = (cur.loc[both, "GP"] / (cur.loc[both, "GP"] + k)).to_numpy()[:, None]
    out.loc[both, cols] = w * cur.loc[both, cols].to_numpy() + (1 - w) * out.loc[both, cols].to_numpy()
    out["GP_CUR"] = cur.GP.reindex(out.index).fillna(0)
    return out


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

    def __init__(self, snap, base=None, cur=None, sched=None, inj=None, today=None):
        s = snap["settings"]
        self.snap, self.teams_n, self.roster_n = snap, s.get("teams", 12), s.get("roster", 13)
        self.mode = s.get("mode", "9cat")
        self.u = 3.0 if self.mode == "points" else 1.0  # FP/game spreads ~3x wider than 9-cat z-sums; scales thresholds
        self.cats = [c for c in s.get("cats", CATS) if c in CATS] or CATS
        self.points = s.get("points") or POINTS
        self.me = snap["me"]
        base = baseline(fetch(LAST), fetch(OLDER)) if base is None else base
        cur = fetch(CUR, max_age_hours=12) if cur is None else cur
        pool = self.teams_n * self.roster_n
        line = ros(base, cur)
        self.p = line[["PLAYER_NAME", "MIN", "GP_CUR"] + RAW].copy()
        self.z = zscores(line, pool)[self.cats]                      # ROS value
        # "cur" = perceived value: reputation plus a heavily weighted hot/cold start (roughly what Yahoo ranks show)
        seen = ros(base, cur, HYPE)
        self.zcur = zscores(seen, pool)[self.cats]
        self.fp = sum(line[c] * w for c, w in self.points.items() if c in line)
        self.fp_cur = sum(seen[c] * w for c, w in self.points.items() if c in seen)
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
        self.rate, self.avail = self._availability(base, cur)
        # this week's opponent and totals so far (Yahoo pull, or picked in the app); last week's one doesn't count.
        # ponytail: without a Yahoo pull, weeks are assumed to run Monday-Sunday
        m = snap.get("matchup") or {}
        self.matchup = m if (m.get("week_end") or "") >= self.today else {}
        self.week_end = self.matchup.get("week_end") or sunday
        self.games_week = pd.Series({k: self.games_left(k, self.week_end) for k in self.p.index}, dtype=float)
        # expected weekly contribution: games played at his line, missed games partly covered at waiver level
        fill = self.p.loc[self._waiver_level(self.value()), RAW].mean()
        # what a missed game is worth in value units: the half-covered waiver line (zero point for effective())
        f = pd.DataFrame([FILL * fill], index=["_fill"]).assign(MIN=0.0)
        self.zfill = {"ros": zscores(pd.concat([line, f]), pool).loc["_fill", self.cats],
                      "cur": zscores(pd.concat([seen, f]), pool).loc["_fill", self.cats]}
        a = self.avail.to_numpy()[:, None]
        self.week = GAMES_WEEK * (a * self.p[RAW] + (1 - a) * FILL * fill.to_numpy())

    def _keys(self, players):
        keys = []
        for pl in players:
            k = self.find(pl["name"], strict=False)
            if k:
                self.meta[k] = pl
                keys.append(k)
        return keys

    def games_left(self, k, end="9999", start=None):
        """Games k's NBA team plays from today (or `start`, or his return date, if later) through `end`."""
        start = max(start or self.today, self.today, self.inj.get(k, {}).get("return") or "")
        return sum(start <= d <= end for d in self.sched.get(self.nba.get(k), ()))

    def _availability(self, base, cur):
        """Share of games a player suits up for: GP rate over last season + this one, shrunk halfway toward 85%
        (one bad injury year shouldn't count fully), then cut by injury status. Returns (rate, rest-of-season).
        A known return date replaces the status guess: rest of season he plays the share of remaining games after it."""
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
        return rate, rate * share

    def find(self, name, strict=True):
        k = norm(name)
        if k in self.p.index:
            return k
        if len(last := [x for x in self.p.index if x.endswith(" " + k)]) == 1:  # "Jokic", "Gilgeous-Alexander"
            return last[0]
        close = difflib.get_close_matches(k, self.p.index, n=1, cutoff=0.8)
        if close:
            return close[0]
        if strict:
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

    def weakest(self, team, m, pv):
        """Perceived cost of the m players `team` would drop to make room: its weakest by perceived value."""
        return float(pv.reindex(self.rosters[team]).nsmallest(m).sum()) if m > 0 else 0.0

    def best_fa(self, n, skip=()):
        """The n free agents a team would pick up for open spots: per-game value times share of games played, so an
        injured name on waivers isn't who fills the spot."""
        if not self.fa:
            return []
        v = self.effective().reindex(self.fa)
        return v[~v.index.isin(list(skip))].nlargest(n).index.tolist()

    def _waiver_level(self, v):
        """The 5 best free agents by v, or the 10 players just outside the rostered pool without FA data."""
        if self.fa:
            return v.reindex(self.fa).nlargest(5).index
        n = self.teams_n * self.roster_n
        return v.nlargest(n + 10).index[n:]

    def player(self, k, punt=()):
        m = self.meta.get(k, {})
        return {"name": self.p.at[k, "PLAYER_NAME"], "team": self.owner(k), "pos": m.get("pos", ""),
                "status": m.get("status", ""), "value": round(float(self.value([k], punt).iloc[0]), 2),
                "value_now": round(float(self.value([k], punt, "cur").iloc[0]), 2),
                "fp": round(float(self.fp[k]), 1), "gp": int(self.p.at[k, "GP_CUR"]),
                "avail": round(float(self.avail[k]), 2),
                **({"games": int(self.games_week[k])} if self.sched else {}),
                **({"injury": self.inj[k]["note"], "return": self.inj[k]["return"]} if k in self.inj else {}),
                **{f: m[f] for f in ("owned", "waiver") if f in m},  # Yahoo roster % / on waivers until
                "z": {c: round(float(self.z.at[k, c]), 2) for c in self.cats}}

    # --- matchup model (9-cat) -----------------------------------------------
    def vec(self, keys):
        return self.week.reindex(list(keys)).fillna(0).sum().to_numpy()

    def week_vec(self, keys):
        """This week's expected totals: per-game line x games left this week (none before a return date) x
        availability. Without a schedule it's the season-average week, vec()."""
        if not self.sched:
            return self.vec(keys)
        keys = [k for k in keys if k in self.p.index]
        g = (self.games_week.reindex(keys) * self.rate.reindex(keys)).to_numpy()[:, None]
        return (g * self.p.loc[keys, RAW].to_numpy()).sum(0)

    @functools.cached_property
    def vecs(self):
        return {t: self.vec(ks) for t, ks in self.rosters.items()}

    @functools.cached_property
    def rows(self):
        """Weekly vector per player, as a dict of arrays for fast summing in loops."""
        return dict(zip(self.week.index, self.week.to_numpy()))

    def _contrib(self, team, keys, punt=()):
        """Each player's worth to `team` in win %: what it loses without him (its own players) or gains with him (anyone
        else). ponytail: measured on the current roster, not the post-trade one; exact would mean a pass per combo."""
        keys, base, own = list(keys), self.vecs[team], set(self.rosters[team])
        if not keys:
            return {}
        V = np.stack([base - self.rows[k] if k in own else base + self.rows[k] for k in keys])
        _, O = self._others(team)
        w = match_prob(self._concede(cat_probs(V[:, None], O[None], self.cats), punt)).mean(-1)
        w0 = self.outlook(team, punt=punt)["win"]
        return {k: float(w0 - x if k in own else x - w0) for k, x in zip(keys, w)}

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
        """A team's odds to win a weekly matchup vs every other team, and per-category odds (averaged)."""
        override = override or {}
        v = override.get(team, self.vecs[team])
        opp = [t for t in self.rosters if t != team]
        O = np.stack([override.get(t, self.vecs[t]) for t in opp])
        p = self._concede(cat_probs(v[None], O, self.cats), punt)
        m = match_prob(p)
        return {"win": float(m.mean()), "odds": dict(zip(self.cats, p.mean(0).tolist())),
                "vs": dict(zip(opp, m.tolist())), "cats_won": dict(zip(opp, p.sum(-1).tolist()))}

    @functools.cached_property
    def outlooks(self):
        return {t: self.outlook(t) for t in self.rosters}

    def standings(self, override=None):
        """Projected finish: every team's average weekly win odds vs the rest of the league."""
        wins = ({t: o["win"] for t, o in self.outlooks.items()} if not override
                else {t: self.outlook(t, override)["win"] for t in self.rosters})
        order = sorted(wins, key=lambda t: -wins[t])
        return {t: {"win": round(wins[t], 3), "rank": i + 1} for i, t in enumerate(order)}

    def _after(self, team, out, inn, punt=()):
        """Roster after a trade at its current size: extra players mean dropping whoever costs the least win %
        (incoming included), open spots get the best free agents."""
        keys = [k for k in self.rosters[team] if k not in out] + list(inn)
        n = len(keys) - len(self.rosters[team])
        dropped, added = [], []
        if n > 0:
            c = self._contrib(team, keys, punt)
            dropped = sorted(keys, key=c.get)[:n]
            keys = [k for k in keys if k not in dropped]
        elif n < 0 and self.fa:
            added = self.best_fa(-n, skip=keys)
            keys += added
        return keys, dropped, added

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

    def trade(self, give, get, punt=()):
        """Evaluate a trade for you. 9-cat: change in your weekly win odds vs the league, both rosters updated
        (extra players dropped, open spots filled from waivers). Points: value net of replacement level."""
        give, get = self._check(give, get)
        name = self.names
        n = len(get) - len(give)
        rv, rz = self.replacement(punt)
        cats = (self.z.loc[get].sum() - self.z.loc[give].sum() - n * rz)
        mine = float(self.value(get, punt).sum() - self.value(give, punt).sum() - n * rv)
        other = self.owner(get[0]) if get else None
        tp = self.build(other) if other in self.rosters else []
        theirs = float(self.value(give, tp).sum() - self.value(get, tp).sum() + n * self.replacement(tp)[0])
        cv, pv = self.perceived()
        seen = float(pv.reindex(give).sum() - pv.reindex(get).sum()) + (
            -self.weakest(other, -n, pv) if n < 0 and other in self.rosters else n * cv)
        out = {"give": [self.player(k, punt) for k in give], "get": [self.player(k, punt) for k in get],
               "cats": {c: round(float(cats[c]), 2) for c in self.cats}, "net": round(mine, 2), "unit": "value",
               "other_team": other, "their_build": tp, "their_net": round(theirs, 2), "their_view": round(seen, 2),
               "verdict": "accept" if mine > 0.25 * self.u else "decline" if mine < -0.25 * self.u else "toss-up"}
        if self.mode == "points" or other not in self.rosters:
            return out
        mk, md, ma = self._after(self.me, give, get, punt)
        tk, td, ta = self._after(other, get, give)
        ov = {self.me: self.vec(mk), other: self.vec(tk)}
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
            "you_drop": name(md), "you_add": name(ma), "they_drop": name(td), "they_add": name(ta),
            "accept": accept(100 * (tw - tb), seen, self.u),
            "verdict": "accept" if net > 0.5 else "decline" if net < -0.5 else "toss-up"})
        return out

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
               goal_team=None, healthy_only=False, shape=None, punt=(), limit=20, depth=10):
        """9-cat trade search scored by the matchup model, vectorized over every give/get combo per team.

        give / get: players every deal must include. Pinning a get fixes the team and searches both whole rosters,
               otherwise it's your top `depth` vs theirs.
        lock:  players never offered or asked for.
        tier:  how likely they say yes (TIERS). easy: both gain and it looks good to them. fair: both gain.
               finesse: they *look* like winners on hot starts but lose a little. hard: you gain big, needs a pitch.
        goal:  "win" (weekly win odds), "cats" (your odds in goal_cats) or "team" (head-to-head vs goal_team).
               The last two skip deals that cost you more than 1% win odds.
        shape: "consolidate" (you send more players) or "depth" (you get more).
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
        if max(len(give), len(get)) > size:
            raise KeyError(f"More than {size} pinned players on one side")
        if get:
            if team and team != self.owner(get[0]):
                raise KeyError(f"{self.names(get)[0]} plays for {self.owner(get[0])}, not {team}")
            team = self.owner(get[0])
        lock = {self.find(n) for n in lock} - set(give) - set(get)
        u, cats, me, wide = self.u, self.cats, self.me, bool(get)
        cv, pv = self.perceived()
        vg, vc, vm = self.value().to_dict(), pv.to_dict(), self.value(punt=punt).to_dict()
        t0, t1, t2 = TIERS[tier]

        def combos(keys, pins, v, skip=()):
            free = sorted((k for k in keys if k not in lock and k not in pins and k not in skip), key=lambda k: -v[k])
            free = free if wide else free[:depth]
            return [tuple(pins) + c for s in range(max(1, len(pins)), size + 1)
                    for c in itertools.combinations(free, s - len(pins))]

        def stack(cs):  # summed weekly vectors, player counts, summed perceived value
            return (np.stack([sum(self.rows[k] for k in c) for c in cs]), np.array([len(c) for c in cs]),
                    np.array([sum(vc[k] for k in c) for c in cs]))

        def low2(groups, c):  # per group: the 2 players the team would miss least (padded with inf) and their vectors
            vals, vecs = np.full((len(groups), 2), np.inf), np.zeros((len(groups), 2, len(RAW)))
            for i, g in enumerate(groups):
                for j, k in enumerate(sorted(g, key=c.get)[:2]):
                    vals[i, j], vecs[i, j] = c[k], self.rows[k]
            return vals, vecs

        def drops(A, B, n):  # weekly vector of the n (0-2) cheapest of A's pair (per give combo) and B's (per get combo)
            a, b = len(A[0]), len(B[0])
            vals = np.concatenate([np.broadcast_to(A[0][:, None], (a, b, 2)), np.broadcast_to(B[0][None], (a, b, 2))], -1)
            vecs = np.concatenate([np.broadcast_to(A[1][:, None], (a, b, 2, len(RAW))),
                                   np.broadcast_to(B[1][None], (a, b, 2, len(RAW)))], 2)
            cut = np.take_along_axis(vecs, np.argsort(vals, -1)[..., :2, None], 2)
            return (n >= 1)[..., None] * cut[:, :, 0] + (n >= 2)[..., None] * cut[:, :, 1]

        fa = np.zeros((3, len(RAW)))  # the best 0, 1 or 2 free agents, for whoever ends up with open spots
        for i, k in enumerate(self.best_fa(2)):
            fa[i + 1:] += self.rows[k]

        roster = self.rosters[me]
        mine = combos(roster, give, vm)
        GV, gn, gseen = stack(mine)
        my_rest = low2([[k for k in roster if k not in c] for c in mine], self._contrib(me, roster, punt))
        before = self.outlook(me, punt=punt)
        out = []
        for t, keys in self.rosters.items():
            if t == me or (team and t != team):
                continue
            hurt = [k for k in keys if healthy_only and self.meta.get(k, {}).get("status", "") in OUT]
            theirs = combos(keys, get, vg, hurt)
            if not theirs:
                continue
            RV, rn, rseen = stack(theirs)
            c_in = self._contrib(me, {k for c in theirs for k in c}, punt)  # what their players would add for you
            c_t = self._contrib(t, set(keys) | {k for c in mine for k in c})
            n = rn[None] - gn[:, None]  # extra players you receive
            me_after = (self.vecs[me] - GV[:, None] + RV[None] - drops(my_rest, low2(theirs, c_in), n)
                        + fa[np.clip(-n, 0, 2)])
            them_after = (self.vecs[t] - RV[None] + GV[:, None] + fa[np.clip(n, 0, 2)]
                          - drops(low2(mine, c_t), low2([[k for k in keys if k not in c] for c in theirs], c_t), -n))
            names, O = self._others(me, t)
            k = len(O) + 1
            p_mt = cat_probs(me_after, them_after, cats)
            p_o = cat_probs(me_after[:, :, None], O[None, None], cats)  # (give combo, get combo, other team, cat)
            m_o, m_t = match_prob(self._concede(p_o, punt)), match_prob(self._concede(p_mt, punt))
            dme = 100 * ((m_o.sum(-1) + m_t) / k - before["win"])
            t_o = match_prob(cat_probs(them_after[:, :, None], O[None, None], cats)).sum(-1)
            dthem = 100 * ((t_o + match_prob(1 - p_mt)) / k - self.outlooks[t]["win"])
            low = np.concatenate([[0], np.cumsum(np.sort(pv.reindex(keys).to_numpy()))])  # their weakest, for drops
            seen = gseen[:, None] - rseen[None] + np.where(n > 0, n * cv, -low[np.clip(-n, 0, len(low) - 1)])
            if goal == "team":
                h2h = m_t if goal_team == t else m_o[..., names.index(goal_team)]
                score = 100 * (h2h - before["vs"][goal_team])
            elif goal == "cats":
                idx = [cats.index(c) for c in goal_cats]
                odds = ((p_o.sum(-2) + p_mt) / k)[..., idx].mean(-1)
                score = 100 * (odds - np.mean([before["odds"][c] for c in goal_cats]))
            else:
                score = dme
            ok = (score > t0) & (dthem > t1) & (seen > t2 * u)
            if goal != "win":
                ok &= dme > -1
            if shape == "consolidate":
                ok &= n < 0
            elif shape == "depth":
                ok &= n > 0
            G, R = np.nonzero(ok)
            rank = np.minimum(score, dthem)[G, R] if tier in ("easy", "fair") else score[G, R]
            top = np.argsort(-rank)[:limit]
            for gi, ri in zip(G[top], R[top]):
                out.append({"team": t, "give": self.names(mine[gi]), "get": self.names(theirs[ri]),
                            "net": round(float(dme[gi, ri]), 1), "their_net": round(float(dthem[gi, ri]), 1),
                            "their_view": round(float(seen[gi, ri]), 2), "unit": "win%",
                            "accept": accept(dthem[gi, ri], seen[gi, ri], u),
                            **({"score": round(float(score[gi, ri]), 1)} if goal != "win" else {})})
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
        """Your weekly win odds, projected standings, per-category odds and category standings (every team's odds
        and rank in each category), H2H vs each team, punt suggestions."""
        tot = self.totals
        odds = pd.DataFrame({t: o["odds"] for t, o in self.outlooks.items()}).T[self.cats]  # team x category
        ranks = odds.rank(ascending=False, method="min").astype(int)  # projected category standings
        o, n = self.outlooks[self.me], len(self.cats)
        vs = {t: {"pct": round(o["vs"][t], 3), "win": round(o["cats_won"][t], 1), "loss": round(n - o["cats_won"][t], 1)}
              for t in o["vs"]}
        st = self.standings()
        return {"totals": tot.round(2).to_dict("index"), "ranks": ranks.to_dict("index"), "vs": vs,
                "cat_odds": odds.round(3).to_dict("index"),
                "odds": {c: round(p, 3) for c, p in o["odds"].items()}, "win": round(o["win"], 3),
                "rank": st[self.me]["rank"], "standings": st,
                "punt_suggestions": self.build(self.me), "builds": {t: self.build(t) for t in self.rosters},
                "strengths": sorted(o["odds"], key=lambda c: -o["odds"][c])[:3]}

    def waivers(self, punt=(), limit=15):
        """Best free agents for your team + add/drop pairs. 9-cat: scored by the change in your weekly win odds."""
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
        """Every (add, drop) pair scored by the change in your weekly win odds vs the league."""
        adds = self.value(self.fa, punt).nlargest(40).index.tolist()
        drops = [k for k in self.rosters[self.me] if self.meta.get(k, {}).get("status", "") not in OUT]
        A = np.stack([self.vec([k]) for k in adds])
        D = np.stack([self.vec([k]) for k in drops])
        new = self.vecs[self.me] - D[None, :, :] + A[:, None, :]
        _, O = self._others(self.me)
        win = match_prob(self._concede(cat_probs(new[:, :, None], O[None, None], self.cats), punt)).mean(-1)
        gain = 100 * (win - self.outlook(self.me, punt=punt)["win"])  # (adds, drops)
        fit, best_drop = gain.max(1), gain.argmax(1)
        order = np.argsort(-fit)[:limit]
        moves = [{"add": self.p.at[adds[i], "PLAYER_NAME"], "drop": self.p.at[drops[best_drop[i]], "PLAYER_NAME"],
                  "gain": round(float(fit[i]), 1), "unit": "win%"} for i in order if fit[i] > 0.5][:8]
        cheap = np.argsort(-gain.max(0))[:3]  # drops that cost least when swapped for the best add
        return {"free_agents": [{**self.player(adds[i], punt), "fit": round(float(fit[i]), 1)} for i in order],
                "drop_candidates": [self.player(drops[j], punt) for j in cheap], "moves": moves}

    def this_week(self, opp=None, punt=(), limit=5):
        """This week's matchup: totals so far + the rest of the week (week_vec, players in IL slots sit) -> odds per
        category and for the week, a safe / chase / lost call per category, and streaming adds scored on this week.
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
        play = lambda t: [k for k in self.rosters[t] if self.meta.get(k, {}).get("slot") not in ("IL", "IL+", "NA")]
        left = {t: self.week_vec(play(t)) for t in (me, opp)}
        odds = lambda v: self._concede(cat_probs(v, left[opp], self.cats, so_far[me], so_far[opp]), punt)
        p = odds(left[me])
        win = float(match_prob(p))

        def games(k):  # a waiver claim only plays from the day it clears
            w = self.meta.get(k, {}).get("waiver")
            clears = w and datetime.strptime(f"{w} {self.today[:4]}", "%b %d %Y").date().isoformat()
            return self.games_left(k, self.week_end, clears) if self.sched else GAMES_WEEK

        # streams: each healthy free agent for whichever of your 3 cheapest healthy players (season win-% cost) helps most
        healthy = lambda ks: [k for k in ks if self.meta.get(k, {}).get("status", "") not in OUT]
        cost = self._contrib(me, healthy(play(me)), punt)
        drops, adds = sorted(cost, key=cost.get)[:3], healthy(self.fa)
        streams = []
        if drops and adds:
            g = {k: games(k) for k in adds}
            full = lambda k: self.games_week[k] if self.sched else GAMES_WEEK
            A = np.stack([self.week_vec([k]) * (g[k] / full(k) if full(k) else 0) for k in adds])
            D = np.stack([self.week_vec([k]) for k in drops])
            gain = 100 * (match_prob(odds(left[me] - D[None] + A[:, None])) - win)
            season0 = self.outlook(me, punt=punt)["win"]
            for i in np.argsort(-gain.max(1))[:limit]:
                a, d = adds[i], drops[gain[i].argmax()]
                if gain[i].max() < 0.1:
                    break
                season = self.outlook(me, {me: self.vecs[me] - self.rows[d] + self.rows[a]}, punt)["win"]
                streams.append({**self.player(a, punt), **({"games": int(g[a])} if self.sched else {}),
                                "gain": round(float(gain[i].max()), 1), "drop": self.p.at[d, "PLAYER_NAME"],
                                "season": round(100 * (season - season0), 1)})
        call = lambda c, x: "punt" if c in punt else "safe" if x >= 1 - WEAK else "lost" if x <= WEAK else "chase"
        return {"opponent": opp, "teams": others, "week_end": self.week_end,
                "live": m.get("opponent") == opp and all(t in m.get("totals", {}) for t in (me, opp)),
                "win": round(100 * win, 1), "score": [round(float(p.sum()), 1), round(len(self.cats) - float(p.sum()), 1)],
                **({"games": {"you": int(self.games_week.reindex(play(me)).sum()),
                              "them": int(self.games_week.reindex(play(opp)).sum())}} if self.sched else {}),
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

    def state(self, punt=()):
        return {"league": self.snap.get("name", "League"), "mode": self.mode, "cats": self.cats, "me": self.me,
                "teams": list(self.rosters), "fetched": self.snap.get("fetched", ""), "demo": self.snap.get("demo", False),
                "roster": sorted((self.player(k, punt) for k in self.rosters[self.me]), key=lambda p: -p["value"]),
                "profile": self.profile() if self.mode != "points" else None,
                "waivers": self.waivers(punt), "movers": self.movers(punt)}

    def team_roster(self, team, punt=()):
        return sorted((self.player(k, punt) for k in self.rosters[team]), key=lambda p: -p["value"])


def load(path=DATA / "league.json"):
    if not Path(path).exists():
        import yahoo
        yahoo.demo()
    return League(json.loads(Path(path).read_text(encoding="utf-8")), sched=schedule(), inj=injuries())


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["team", "trade", "ideas", "search", "waivers", "movers", "roster", "week"])
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
    a = ap.parse_args()
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]
    lg, punt = load(), split(a.punt)
    out = {"team": lambda: {**lg.profile(), "roster": lg.team_roster(lg.me, punt)},
           "trade": lambda: lg.trade(split(a.give), split(a.get), punt),
           "ideas": lambda: lg.ideas(a.mode, punt, a.team),
           "search": lambda: lg.search(split(a.give), split(a.get), split(a.lock), a.team, a.size, a.tier, a.goal,
                                       split(a.cats), a.vs, a.healthy, a.shape, punt),
           "waivers": lambda: lg.waivers(punt),
           "movers": lambda: lg.movers(punt),
           "week": lambda: lg.this_week(a.vs, punt),
           "roster": lambda: lg.team_roster(a.team or lg.me, punt)}[a.cmd]()
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
