"""Fantasy basketball engine: player values, team profiles, trades, waivers, sell-high/buy-low.

Values are per game, rest of season (ROS): last season (or your projections) shrunk toward this season
as games pile up. 9-cat uses z-scores vs the rostered pool; points uses the league's scoring weights.

CLI (prints JSON, used by Claude Code and for quick checks):
  python model.py team                      # your category ranks, matchups, punt suggestions
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

    def _keys(self, players):
        keys = []
        for pl in players:
            k = self.find(pl["name"], strict=False)
            if k:
                self.meta[k] = pl
                keys.append(k)
        return keys

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
        if self.fa:
            pick = v.reindex(self.fa).nlargest(5).index
        else:
            n = self.teams_n * self.roster_n
            pick = v.nlargest(n + 10).index[n:]
        z = self.zcur if frame == "cur" else self.z
        return float(v[pick].mean()), z.loc[pick].mean()

    def player(self, k, punt=()):
        m = self.meta.get(k, {})
        return {"name": self.p.at[k, "PLAYER_NAME"], "team": self.owner(k), "pos": m.get("pos", ""),
                "status": m.get("status", ""), "value": round(float(self.value([k], punt).iloc[0]), 2),
                "value_now": round(float(self.value([k], punt, "cur").iloc[0]), 2),
                "fp": round(float(self.fp[k]), 1), "gp": int(self.p.at[k, "GP_CUR"]),
                "z": {c: round(float(self.z.at[k, c]), 2) for c in self.cats}}

    # --- trades ------------------------------------------------------------
    def trade(self, give, get, punt=()):
        """Net change for you. Uneven trades are adjusted by replacement level: receiving an extra player costs a
        drop, sending one frees a roster spot you refill from waivers."""
        give = [self.find(n) for n in give]
        get = [self.find(n) for n in get]
        n = len(get) - len(give)
        rv, rz = self.replacement(punt)
        cats = (self.z.loc[get].sum() - self.z.loc[give].sum() - n * rz)
        mine = float(self.value(get, punt).sum() - self.value(give, punt).sum() - n * rv)
        other = self.owner(get[0]) if get else None
        tp = self.build(other) if other in self.rosters else []
        theirs = float(self.value(give, tp).sum() - self.value(get, tp).sum() + n * self.replacement(tp)[0])
        cv = self.replacement(frame="cur")[0]
        seen = float(self.value(give, frame="cur").sum() - self.value(get, frame="cur").sum() + n * cv)
        return {"give": [self.player(k, punt) for k in give], "get": [self.player(k, punt) for k in get],
                "cats": {c: round(float(cats[c]), 2) for c in self.cats}, "net": round(mine, 2),
                "other_team": other, "their_build": tp, "their_net": round(theirs, 2), "their_view": round(seen, 2),
                "verdict": "accept" if mine > 0.25 * self.u else "decline" if mine < -0.25 * self.u else "toss-up"}

    def ideas(self, mode="fair", punt=(), team=None, limit=20, depth=10):
        """Search 1-for-1, 2-for-1, 1-for-2 and 2-for-2 deals with every other team.

        Each team is valued on its own build (its punts, see build()), so a deal can help both sides.
        fair:    both teams gain on their build and it's within 1 z of even on plain overall value; best-balanced first
                 (points leagues: you gain a little, it's near even for them and doesn't look bad to them)
        finesse: you gain more, they *look* like winners on hype (hot starts) but lose a little in reality
        """
        vm, vc, vo = self.value(punt=punt).to_dict(), self.value(frame="cur").to_dict(), self.value().to_dict()
        mine = self.value(self.rosters[self.me], punt).nlargest(depth).index.tolist()
        rv, cv, ov = self.replacement(punt)[0], self.replacement(frame="cur")[0], self.replacement()[0]
        u = self.u
        combos = lambda ks: [c for r in (1, 2) for c in itertools.combinations(ks, r)]
        total = lambda v, ks: sum(v[k] for k in ks)
        out = []
        for t, keys in self.rosters.items():
            if t == self.me or (team and t != team):
                continue
            tp = self.build(t)
            vg, gv = self.value(punt=tp).to_dict(), self.replacement(tp)[0]
            theirs = self.value(keys, tp).nlargest(depth).index.tolist()
            for g in combos(mine):
                for r in combos(theirs):
                    n = len(r) - len(g)
                    me = total(vm, r) - total(vm, g) - n * rv
                    them = total(vg, g) - total(vg, r) + n * gv
                    seen = total(vc, g) - total(vc, r) + n * cv
                    even = abs(total(vo, g) - total(vo, r) + n * ov) < u
                    if mode == "finesse":
                        ok = me > 0.6 * u and seen > 0.3 * u and them > -3 * u
                    elif self.mode == "points":  # no categories -> no fit; every deal is zero-sum, so "fair" = near even
                        ok = me > 0.3 * u and them > -u and seen > -0.5 * u
                    else:
                        ok = me > 0.3 * u and them > 0.3 * u and even and seen > -u
                    if ok:
                        out.append((me, t, g, r, them, seen))
        out.sort(key=lambda x: -min(x[0], x[4]) if mode == "fair" else -x[0])
        per_team = {}
        out = [o for o in out if per_team.setdefault(o[1], []).append(o) or len(per_team[o[1]]) <= 3]
        names = lambda ks: [self.p.at[k, "PLAYER_NAME"] for k in ks]
        return [{"team": t, "give": names(g), "get": names(r), "net": round(me, 2), "their_net": round(th, 2),
                 "their_view": round(se, 2)} for me, t, g, r, th, se in out[:limit]]

    # --- team / waivers / movers -------------------------------------------
    @functools.cached_property
    def totals(self):
        """Per-team category totals: sum of player z per game."""
        return pd.DataFrame({t: self.z.loc[ks].sum() for t, ks in self.rosters.items()}).T

    def build(self, team):
        """Categories a team is effectively punting: its 2 worst, if they're in the league's bottom 40%."""
        if self.mode == "points":
            return []
        r = self.totals.rank(ascending=False, method="min").loc[team].sort_values(ascending=False)
        return [c for c in r.index[:2] if r[c] > self.teams_n * 0.6]

    def profile(self):
        """League ranks per category, your H2H result vs each team, punt suggestions."""
        tot = self.totals
        ranks = tot.rank(ascending=False, method="min").astype(int)
        mine = tot.loc[self.me]
        vs = {t: {"win": int((mine > row).sum()), "loss": int((mine < row).sum())}
              for t, row in tot.iterrows() if t != self.me}
        return {"totals": tot.round(2).to_dict("index"), "ranks": ranks.to_dict("index"), "vs": vs,
                "punt_suggestions": self.build(self.me), "builds": {t: self.build(t) for t in self.rosters},
                "strengths": list(ranks.loc[self.me].sort_values().index[:3])}

    def waivers(self, punt=(), limit=15):
        """Best free agents for your build + add/drop pairs that beat your weakest healthy player."""
        fa = self.value(self.fa, punt).nlargest(limit)
        roster = [k for k in self.rosters[self.me] if self.meta.get(k, {}).get("status", "") not in OUT]
        drops = self.value(roster, punt).nsmallest(3)
        moves = [{"add": self.p.at[a, "PLAYER_NAME"], "drop": self.p.at[d, "PLAYER_NAME"], "gain": round(float(fa[a] - dv), 2)}
                 for a in fa.index for d, dv in drops.items() if fa[a] - dv > 0.3 * self.u]
        moves.sort(key=lambda m: -m["gain"])
        return {"free_agents": [self.player(k, punt) for k in fa.index],
                "drop_candidates": [self.player(k, punt) for k in drops.index], "moves": moves[:8]}

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
