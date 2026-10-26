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
  python model.py waivers
  python model.py movers                    # sell-high / buy-low
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
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "data"
LAST, CUR = "2025-26", "2026-27"
CATS = ["FG%", "FT%", "FG3M", "PTS", "REB", "AST", "STL", "BLK", "TOV"]
COUNTING = CATS[2:]
RAW = ["FGM", "FGA", "FTM", "FTA"] + COUNTING
POINTS = {"PTS": 1, "REB": 1.2, "AST": 1.5, "STL": 3, "BLK": 3, "TOV": -1}  # Yahoo default
SHRINK = 20  # games of current season that count as much as the baseline
HYPE = 5     # same, for how other managers see players (hot starts count a lot more)
OUT = {"O", "INJ", "IL", "IL+", "NA", "SUSP"}  # Yahoo statuses that mean "not playing now"

# Matchup model. ponytail: calibration knobs are informed guesses; fit them to real weekly results if odds look off.
GAMES_WEEK = 3.4                                   # NBA games per team per week
DISPERSION = {"PTS": 2.2, "REB": 1.2, "AST": 1.1}  # per-game variance / mean; ~Poisson (1.0) for 3PM/STL/BLK/TO
SCHED_CV = 0.10                                    # week-to-week swing beyond box-score noise (schedule, rest, minutes)
PCT_NOISE = 1.0                                    # FG%/FT% variance vs coin-flip shooting at each player's rate
FILL = 0.5                                         # share of a sat-out player's games your bench/streamers cover
STATUS_AVAIL = {"GTD": 0.9, "DTD": 0.9, "O": 0.7, "SUSP": 0.8, "INJ": 0.6, "IL": 0.5, "IL+": 0.4, "NA": 0.0}


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


def keyed(df):
    return df.assign(key=df.PLAYER_NAME.map(norm)).drop_duplicates("key").set_index("key")


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


def cat_probs(A, B, cats):
    """P(team A beats team B) in each category. A, B: (..., len(RAW)) weekly totals -> (..., len(cats))."""
    out = []
    for c in cats:
        if c in ("FG%", "FT%"):
            m, a = RAW.index(c[:2] + "M"), RAW.index(c[:2] + "A")
            pa, pb = A[..., m] / A[..., a], B[..., m] / B[..., a]
            var = PCT_NOISE * (pa * (1 - pa) / A[..., a] + pb * (1 - pb) / B[..., a])
            diff = pa - pb
        else:
            i, d = RAW.index(c), DISPERSION.get(c, 1.0)
            ta, tb = A[..., i], B[..., i]
            var = d * (ta + tb) + (SCHED_CV * ta) ** 2 + (SCHED_CV * tb) ** 2
            diff = tb - ta if c == "TOV" else ta - tb
        out.append(phi(diff / np.sqrt(var)))
    return np.stack(out, -1)


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

    def __init__(self, snap, base=None, cur=None):
        s = snap["settings"]
        self.snap, self.teams_n, self.roster_n = snap, s.get("teams", 12), s.get("roster", 13)
        self.mode = s.get("mode", "9cat")
        self.u = 3.0 if self.mode == "points" else 1.0  # FP/game spreads ~3x wider than 9-cat z-sums; scales thresholds
        self.cats = [c for c in s.get("cats", CATS) if c in CATS] or CATS
        self.points = s.get("points") or POINTS
        self.me = snap["me"]
        base = fetch(LAST) if base is None else base
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
        self.avail = self._availability(base, cur)
        # expected weekly contribution: games played at his line, missed games partly covered at waiver level
        fill = self.p.loc[self._waiver_level(self.value()), RAW].mean()
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

    def _availability(self, base, cur):
        """Share of games a player suits up for: GP rate over last season + this one, shrunk halfway toward 85%
        (one bad injury year shouldn't count fully), then cut by Yahoo injury status."""
        played = base.GP.reindex(self.p.index).fillna(0) + cur.GP.reindex(self.p.index).fillna(0)
        possible = base.GP.max() + cur.GP.max()
        a = (played + 0.85 * possible / 2) / (possible * 1.5)
        a[~self.p.index.isin(base.index)] = 0.85  # rookies: no history
        status = pd.Series({k: STATUS_AVAIL.get(m.get("status", ""), 1.0) for k, m in self.meta.items()}, dtype=float)
        return a * status.reindex(a.index).fillna(1.0)

    def find(self, name, strict=True):
        k = norm(name)
        if k in self.p.index:
            return k
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
                "z": {c: round(float(self.z.at[k, c]), 2) for c in self.cats}}

    # --- matchup model (9-cat) -----------------------------------------------
    def vec(self, keys):
        return self.week.reindex(list(keys)).fillna(0).sum().to_numpy()

    @functools.cached_property
    def vecs(self):
        return {t: self.vec(ks) for t, ks in self.rosters.items()}

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

    def _after(self, team, out, inn):
        """Roster after a trade at its current size: an extra player means dropping the weakest one (incoming
        included), an open spot gets the best free agent."""
        keys = [k for k in self.rosters[team] if k not in out] + list(inn)
        n = len(keys) - len(self.rosters[team])
        dropped, added = [], []
        if n > 0:
            dropped = self.value(keys).nsmallest(n).index.tolist()
            keys = [k for k in keys if k not in dropped]
        elif n < 0 and self.fa:
            added = self.value([k for k in self.fa if k not in keys]).nlargest(-n).index.tolist()
            keys += added
        return keys, dropped, added

    # --- trades ------------------------------------------------------------
    def trade(self, give, get, punt=()):
        """Evaluate a trade for you. 9-cat: change in your weekly win odds vs the league, both rosters updated
        (extra players dropped, open spots filled from waivers). Points: value net of replacement level."""
        give = [self.find(n) for n in give]
        get = [self.find(n) for n in get]
        name = lambda ks: [self.p.at[k, "PLAYER_NAME"] for k in ks]
        if not_mine := [k for k in give if k not in self.rosters[self.me]]:
            raise KeyError(f"Not on your roster: {', '.join(name(not_mine))}")
        if len({self.owner(k) for k in get}) > 1 or any(self.owner(k) in (None, "FA", self.me) for k in get):
            raise KeyError("Players you get must all come from one other team: "
                           + ", ".join(f"{p} ({self.owner(k) or 'no team'})" for k, p in zip(get, name(get))))
        n = len(get) - len(give)
        rv, rz = self.replacement(punt)
        cats = (self.z.loc[get].sum() - self.z.loc[give].sum() - n * rz)
        mine = float(self.value(get, punt).sum() - self.value(give, punt).sum() - n * rv)
        other = self.owner(get[0]) if get else None
        tp = self.build(other) if other in self.rosters else []
        theirs = float(self.value(give, tp).sum() - self.value(get, tp).sum() + n * self.replacement(tp)[0])
        cv = self.replacement(frame="cur")[0]
        seen = float(self.value(give, frame="cur").sum() - self.value(get, frame="cur").sum() + n * cv)
        out = {"give": [self.player(k, punt) for k in give], "get": [self.player(k, punt) for k in get],
               "cats": {c: round(float(cats[c]), 2) for c in self.cats}, "net": round(mine, 2), "unit": "value",
               "other_team": other, "their_build": tp, "their_net": round(theirs, 2), "their_view": round(seen, 2),
               "verdict": "accept" if mine > 0.25 * self.u else "decline" if mine < -0.25 * self.u else "toss-up"}
        if self.mode == "points" or other not in self.rosters:
            return out
        mk, md, ma = self._after(self.me, give, get)
        tk, td, ta = self._after(other, get, give)
        ov = {self.me: self.vec(mk), other: self.vec(tk)}
        b, a = self.outlook(self.me, punt=punt), self.outlook(self.me, ov, punt)
        tb, tw = self.outlooks[other]["win"], self.outlook(other, ov)["win"]
        sb, sa = self.standings(), self.standings(ov)
        net = 100 * (a["win"] - b["win"])
        out.update({
            "unit": "win%", "value_net": out["net"], "net": round(net, 1), "their_net": round(100 * (tw - tb), 1),
            "win": round(100 * b["win"], 1), "win_after": round(100 * a["win"], 1),
            "their_win": round(100 * tb, 1), "their_win_after": round(100 * tw, 1),
            "rank": sb[self.me]["rank"], "rank_after": sa[self.me]["rank"],
            "odds": {c: round(100 * (a["odds"][c] - b["odds"][c]), 1) for c in self.cats},
            "you_drop": name(md), "you_add": name(ma), "they_drop": name(td), "they_add": name(ta),
            "verdict": "accept" if net > 0.5 else "decline" if net < -0.5 else "toss-up"})
        return out

    def ideas(self, mode="fair", punt=(), team=None, limit=20, depth=10):
        """Search 1-for-1, 2-for-1, 1-for-2 and 2-for-2 deals with every other team.

        Points leagues (no categories to trade around, so every deal is zero-sum):
        fair:    you gain a little, it's near even for them and doesn't look bad to them
        finesse: you gain more, they *look* like winners on hype (hot starts) but lose a little in reality
        """
        if self.mode != "points":
            return self._ideas_matchup(mode, punt, team, limit, depth)
        vm, vc = self.value(punt=punt).to_dict(), self.value(frame="cur").to_dict()
        mine = self.value(self.rosters[self.me], punt).nlargest(depth).index.tolist()
        rv, cv = self.replacement(punt)[0], self.replacement(frame="cur")[0]
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

    def _ideas_matchup(self, mode, punt, team, limit, depth):
        """9-cat trade search scored by the matchup model, vectorized over every give/get combo per team.

        fair:    both teams' weekly win odds go up (category fit), and it doesn't look like a loss to them
        finesse: your odds go up more; they *look* like winners on hot starts while losing a little in reality
        """
        u, cats = self.u, self.cats
        vg, vc = self.value().to_dict(), self.value(frame="cur").to_dict()
        cv = self.replacement(frame="cur")[0]
        best_fa = self.value(self.fa).idxmax() if self.fa else None
        fa_vec = self.vec([best_fa]) if best_fa else np.zeros(len(RAW))

        def side(cands, roster):
            combos = [c for r in (1, 2) for c in itertools.combinations(cands, r)]
            weak = [min(c, key=vg.get) for c in combos]
            rest = [k for k in roster if k not in cands]
            worst = min(rest, key=vg.get) if rest else None
            return (combos, np.stack([self.vec(c) for c in combos]), np.array([len(c) for c in combos]),
                    np.stack([self.vec([k]) for k in weak]), np.array([vg[k] for k in weak]),
                    np.array([sum(vc[k] for k in c) for c in combos]),
                    self.vec([worst]) if worst else np.zeros(len(RAW)), vg[worst] if worst else np.inf)

        mine = self.value(self.rosters[self.me], punt).nlargest(depth).index.tolist()
        gc, GV, gn, GW, GWv, gseen, mw, mwv = side(mine, self.rosters[self.me])
        my_before = self.outlook(self.me, punt=punt)["win"]
        out = []
        for t, keys in self.rosters.items():
            if t == self.me or (team and t != team):
                continue
            theirs = self.value(keys).nlargest(depth).index.tolist()
            rc, RV, rn, RW, RWv, rseen, tw, twv = side(theirs, keys)
            n = (rn[None, :] - gn[:, None])[..., None]  # extra players you receive
            # receiving an extra player: drop the weaker of your worst bench guy and the weakest incoming player
            drop_me = np.where((RWv < mwv)[None, :, None], RW[None, :, :], mw)
            drop_t = np.where((GWv < twv)[:, None, None], GW[:, None, :], tw)
            me_after = self.vecs[self.me] - GV[:, None] + RV[None, :] - (n == 1) * drop_me + (n == -1) * fa_vec
            them_after = self.vecs[t] - RV[None, :] + GV[:, None] - (n == -1) * drop_t + (n == 1) * fa_vec
            _, O = self._others(self.me, t)
            k = len(O) + 1
            p_mt = cat_probs(me_after, them_after, cats)
            m_o = match_prob(self._concede(cat_probs(me_after[:, :, None], O[None, None], cats), punt)).sum(-1)
            my_after = (m_o + match_prob(self._concede(p_mt, punt))) / k
            t_o = match_prob(cat_probs(them_after[:, :, None], O[None, None], cats)).sum(-1)
            their_after = (t_o + match_prob(1 - p_mt)) / k
            dme, dthem = 100 * (my_after - my_before), 100 * (their_after - self.outlooks[t]["win"])
            seen = gseen[:, None] - rseen[None, :] + n[..., 0] * cv
            if mode == "finesse":
                ok = (dme > 1.5) & (seen > 0.3 * u) & (dthem > -5)
            else:
                ok = (dme > 1) & (dthem > 0.5) & (seen > -u)
            for gi, ri in np.argwhere(ok):
                out.append((float(dme[gi, ri]), t, gc[gi], rc[ri], float(dthem[gi, ri]), float(seen[gi, ri])))
        return self._top_ideas(out, mode, limit, "win%", 1)

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
        """Categories a team is effectively punting: up to 2 it wins under 30% of the time across the league."""
        if self.mode == "points":
            return []
        odds = self.outlooks[team]["odds"]
        return [c for c in sorted(odds, key=odds.get)[:2] if odds[c] < 0.3]

    def profile(self):
        """Your weekly win odds, projected standings, per-category odds, H2H vs each team, punt suggestions."""
        tot = self.totals
        ranks = tot.rank(ascending=False, method="min").astype(int)
        o, n = self.outlooks[self.me], len(self.cats)
        vs = {t: {"pct": round(o["vs"][t], 3), "win": round(o["cats_won"][t], 1), "loss": round(n - o["cats_won"][t], 1)}
              for t in o["vs"]}
        st = self.standings()
        return {"totals": tot.round(2).to_dict("index"), "ranks": ranks.to_dict("index"), "vs": vs,
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
    return League(json.loads(Path(path).read_text(encoding="utf-8")))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["team", "trade", "ideas", "waivers", "movers", "roster"])
    ap.add_argument("--give", default="")
    ap.add_argument("--get", default="")
    ap.add_argument("--mode", default="fair", choices=["fair", "finesse"])
    ap.add_argument("--team", help="limit ideas / show roster for one team")
    ap.add_argument("--punt", default="", help='e.g. "FT%%,TOV"')
    a = ap.parse_args()
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]
    lg, punt = load(), split(a.punt)
    out = {"team": lambda: {**lg.profile(), "roster": lg.team_roster(lg.me, punt)},
           "trade": lambda: lg.trade(split(a.give), split(a.get), punt),
           "ideas": lambda: lg.ideas(a.mode, punt, a.team),
           "waivers": lambda: lg.waivers(punt),
           "movers": lambda: lg.movers(punt),
           "roster": lambda: lg.team_roster(a.team or lg.me, punt)}[a.cmd]()
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
