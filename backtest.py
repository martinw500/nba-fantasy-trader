"""Backtest the 9-cat matchup model on a real NBA season, no Yahoo needed:  python backtest.py [2025-26]
python backtest.py replay [2025-26]: rest-of-season error by expert-projection weight instead (fits PROJ_W).

Drafts 20 simulated 12-team leagues from preseason projections. Each week the model predicts every matchup from what
it would have known that Monday (last season blended with games so far, availability from games played), then the
prediction is scored against the real box scores that week. Lines blend in ESPN's preseason projection (prior()). All games count (no daily lineup cap), rosters never change,
so predictions leave out League.week's FILL term (missed games covered at waiver level): nobody fills in here.
Prints calibration (predicted vs actual win rates), log loss and Brier score for categories and for whole matchups.
"""
import itertools
import random
import sys

import numpy as np
import pandas as pd

import model

ARGS = [a for a in sys.argv[1:] if a not in ("replay", "teammates")]
SEASON = ARGS[0] if ARGS else "2025-26"
PREV, PREV2 = (f"{int(SEASON[:4]) - n}-{int(SEASON[2:4]) - n + 1:02d}" for n in (1, 2))
LEAGUES, TEAMS, ROSTER = 20, 12, 13


def logs(season):
    """Every player's box score for the regular season (model.gamelogs), with its week number."""
    g = model.gamelogs(season)
    start = g.GAME_DATE.min() - pd.Timedelta(days=g.GAME_DATE.min().weekday())  # Monday of week 1
    g["week"] = (g.GAME_DATE - start).dt.days // 7
    return g


def per_game(games):
    """Each player's per-game line over these box scores, GP = games."""
    return games.groupby("key").agg(PLAYER_NAME=("PLAYER_NAME", "first"), GP=("GAME_ID", "count"),
                                    **{c: (c, "mean") for c in ["MIN"] + model.RAW})


def experts(g, base):
    """ESPN's preseason projection for the season, as model.experts() lines."""
    return model.experts({}, model.espn(SEASON), base, base.index.union(g.key.unique()))


def season_setup():
    g, base = logs(SEASON), model.baseline(model.fetch(PREV), model.fetch(PREV2))
    pre = model.prior(base, experts(g, base))
    weeks = range(int(g.week.max()) + 1)
    keys = sorted(set(base.index) | set(g.key))
    actual = {w: g[g.week == w].groupby("key")[model.RAW].sum().reindex(keys).fillna(0).to_numpy() for w in weeks}
    # team games per week, and each player's latest team before each week
    team_games = g.groupby(["TEAM_ABBREVIATION", "week"]).GAME_ID.nunique()
    first_team = g.sort_values("GAME_DATE").groupby("key").TEAM_ABBREVIATION.first()
    return g, base, pre, keys, weeks, actual, team_games, first_team


def projections(g, base, pre, keys, w):
    """What the model knew on the Monday of week w: per-game ROS line (from the prior) and availability (from the
    baseline's games, as League does)."""
    cur = per_game(g[g.week < w])
    line = model.ros(pre, cur).reindex(keys)
    line[model.RAW] = line[model.RAW].fillna(0)
    played = base.GP.reindex(keys).fillna(0) + cur.GP.reindex(keys).fillna(0)
    possible = base.GP.max() + (cur.GP.max() if len(cur) else 0)
    avail = (played + 0.85 * possible / 2) / (possible * 1.5)
    avail[~pd.Index(keys).isin(base.index)] = 0.85
    return line, avail.to_numpy()


def draft(line, seed):
    """Snake draft by preseason value with some randomness, like yahoo.demo()."""
    val = model.zscores(line.dropna(subset=["MIN"]), TEAMS * ROSTER).sum(axis=1)
    rng = random.Random(seed)
    order = sorted(val.index, key=lambda k: -(val[k] + rng.gauss(0, 1.2)))[:TEAMS * ROSTER]
    teams = [[] for _ in range(TEAMS)]
    for i, k in enumerate(order):
        rnd, pick = divmod(i, TEAMS)
        teams[pick if rnd % 2 == 0 else TEAMS - 1 - pick].append(k)
    return teams


def outcome(A, B):
    """Real result per category: 1 win, 0.5 tie, 0 loss. A, B: (..., len(RAW)) weekly totals."""
    out = []
    for c in model.CATS:
        if c in ("FG%", "FT%"):
            m, a = model.RAW.index(c[:2] + "M"), model.RAW.index(c[:2] + "A")
            x, y = A[..., m] / np.maximum(A[..., a], 1e-9), B[..., m] / np.maximum(B[..., a], 1e-9)
        else:
            x, y = A[..., model.RAW.index(c)], B[..., model.RAW.index(c)]
            if c == "TOV":
                x, y = y, x
        out.append(np.where(x > y, 1.0, np.where(x < y, 0.0, 0.5)))
    return np.stack(out, -1)


def build(games="fixed"):
    """Every matchup of every week: expected totals for both sides (the model's view) and the real result."""
    g, base, pre, keys, weeks, actual, team_games, first_team = season_setup()
    idx = {k: i for i, k in enumerate(keys)}
    line0, _ = projections(g, base, pre, keys, 0)
    rosters = [[idx[k] for k in t] for s in range(LEAGUES) for t in draft(line0, s)]
    R = np.zeros((len(rosters), len(keys)))
    for i, t in enumerate(rosters):
        R[i, t] = 1
    pairs = [(l * TEAMS + a, l * TEAMS + b) for l in range(LEAGUES)
             for a, b in itertools.combinations(range(TEAMS), 2)]
    I, J = np.array(pairs).T
    E, Y = [], []
    for w in weeks:
        line, avail = projections(g, base, pre, keys, w)
        if games == "fixed":
            n = model.GAMES_WEEK
        else:  # the real schedule: games the player's team played that week
            last = g[g.week < w].sort_values("GAME_DATE").groupby("key").TEAM_ABBREVIATION.last()
            team = pd.Series(keys, index=keys).map(last).fillna(pd.Series(keys, index=keys).map(first_team))
            n = np.array([team_games.get((t, w), 0) for t in team])[:, None]
        exp = R @ (n * avail[:, None] * line[model.RAW].to_numpy())
        act = R @ actual[w]
        if act.sum() == 0:
            continue
        E.append((exp[I], exp[J]))
        Y.append(outcome(act[I], act[J]))
    A = np.concatenate([e[0] for e in E]); B = np.concatenate([e[1] for e in E])
    return A, B, np.concatenate(Y)


def score(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {"logloss": float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()), "brier": float(((p - y) ** 2).mean())}


def calibration(p, y, bins=10):
    b = np.minimum((p * bins).astype(int), bins - 1)
    return [(f"{i * 100 // bins}-{(i + 1) * 100 // bins}%", int((b == i).sum()), round(float(y[b == i].mean()) * 100, 1))
            for i in range(bins) if (b == i).any()]


def report(A, B, Y, label):
    P = model.cat_probs(A, B, model.CATS)
    match = model.match_prob(P)
    won = (Y.sum(-1) > len(model.CATS) / 2) + 0.5 * (Y.sum(-1) == len(model.CATS) / 2)
    print(f"\n== {label}: {len(A)} matchups ==")
    print("categories:", {k: round(v, 4) for k, v in score(P.ravel(), Y.ravel()).items()}, "(coin flip: logloss 0.6931, brier 0.25)")
    for c, i in zip(model.CATS, range(len(model.CATS))):
        print(f"  {c:5}", {k: round(v, 4) for k, v in score(P[:, i], Y[:, i]).items()})
    print("category calibration (predicted bin, n, actual win %):", calibration(P.ravel(), Y.ravel()))
    print("matchups:", {k: round(v, 4) for k, v in score(match, won).items()})
    print("matchup calibration:", calibration(match, won))


def replay(weights=(0, 0.25, 0.5, 0.6, 0.75, 1)):
    """Each Monday's rest-of-season line (ros() on prior() at each expert weight; 0 = the baseline alone) vs what
    players did after: mean absolute miss per stat over that stat's average, so stats count equally, for players with
    10+ games left. Fitted PROJ_W on 2025-26."""
    g, base = logs(SEASON), model.baseline(model.fetch(PREV), model.fetch(PREV2))
    proj = experts(g, base)
    print(f"\n== {SEASON}: rest-of-season error by expert weight ==")
    for x in weights:
        pre, err = model.prior(base, proj, x), []
        for w in range(int(g.week.max()) - 3):
            left = per_game(g[g.week >= w]).query("GP >= 10")
            line = model.ros(pre, per_game(g[g.week < w])).reindex(left.index).fillna(0)
            err.append(float(((line[model.RAW] - left[model.RAW]).abs() / left[model.RAW].mean()).mean(axis=1).mean()))
        print(f"  weight {x:<5} week 0 {err[0]:.4f}   weeks 1-6 {np.mean(err[1:7]):.4f}   season {np.mean(err):.4f}")


def mondays():
    """What the model knew each Monday from week 1 on: this season so far (past, cur), the rest-of-season line, each
    player's team, the regulars, who was out (sat his team's last game, until his next one: exact return dates, which
    ESPN's feed only estimates) and next week's box scores."""
    g, base = logs(SEASON), model.baseline(model.fetch(PREV), model.fetch(PREV2))
    pre, T = model.prior(base, experts(g, base)), "TEAM_ABBREVIATION"
    first = g.sort_values("GAME_DATE").groupby("key")[T].first()
    back = lambda w: g[g.week >= w].groupby("key").GAME_DATE.min()
    for w in range(1, int(g.week.max()) + 1):
        past, nxt = g[g.week < w], g[g.week == w]
        cur = per_game(past)
        line = model.ros(pre, cur)
        team = past.sort_values("GAME_DATE").groupby("key")[T].last().combine_first(first)
        regs = line.index[line.MIN >= model.REGULAR]
        last, played, ret = past.groupby(T).GAME_DATE.max(), set(zip(past.key, past.GAME_DATE)), back(w)
        out = {k: ret.get(k, pd.Timestamp.max) for k in regs if team.get(k) in last and (k, last[team[k]]) not in played}
        yield {"pre": pre, "past": past, "cur": cur, "line": line, "team": team, "regs": regs, "out": out, "nxt": nxt}


def lifted(m, deboost=True):
    """Week m's per-game lines with teammates' injuries (League does the same): this season net of the lifts players
    had (model.had()), plus each out regular's lift over a player's games before his return."""
    lift = model.lifts(m["line"][m["line"].index.isin(m["cur"].index) | m["line"].index.isin(m["regs"])], m["team"],
                       m["regs"])
    line = m["line"]
    if deboost:
        h = model.had(m["past"], m["team"], lift).reindex(m["cur"].index).dropna()
        full = m["cur"].copy()
        full.loc[h.index, h.columns] = (full.loc[h.index, h.columns] - h).clip(lower=0)
        line = model.ros(m["pre"], full)
    lift = lift[lift.index.get_level_values("by").isin(list(m["out"]))]
    games = m["nxt"][["key", "GAME_DATE"]].merge(lift.index.to_frame(index=False), on="key")
    games["on"] = games.GAME_DATE < games.by.map(m["out"])
    share = games.groupby(["key", "by"]).on.mean()
    boost = lift.loc[share.index].mul(share, axis=0).groupby(level="key").sum()
    return line, boost[boost.MIN > 0]


def teammates(grid=((0.37, 0.25),)):
    """Next week's per-game lines with and without teammates' injuries (model.lifts), vs what players did that week,
    as in replay(), for players with an injured teammate and for everyone. Fitted SHARE and USAGE on 2025-26."""
    weeks, cols = list(mondays()), ["MIN"] + model.RAW
    print(f"\n== {SEASON}: next week's lines with teammates' injuries ==")
    for share, usage in grid:
        model.SHARE, model.USAGE = share, usage
        rows = {k: [] for k in ("act", "off", "on", "fwd", "hit")}
        for m in weeks:
            act = per_game(m["nxt"])
            act = act[act.index.isin(m["line"].index)]
            on, boost = lifted(m)
            fwd, _ = lifted(m, deboost=False)
            add = boost.reindex(act.index).fillna(0)
            rows["act"].append(act[cols]); rows["off"].append(m["line"].loc[act.index, cols])
            rows["on"].append(on.loc[act.index, cols] + add); rows["fwd"].append(fwd.loc[act.index, cols] + add)
            rows["hit"].append(pd.Series(act.index.isin(boost.index), index=act.index))
        act, hit = pd.concat(rows["act"]), pd.concat(rows["hit"]).to_numpy()
        for name, sub in (("injured teammate", hit), ("everyone", np.ones(len(hit), bool))):
            a = act[sub]
            err = lambda p: ((p[sub] - a).abs() / a.mean()).mean()
            out = {v: err(pd.concat(rows[v])) for v in ("off", "on", "fwd")}
            print(f"  SHARE {share} USAGE {usage}  {name:16} n {sub.sum():5}  " + "   ".join(
                f"{v} MIN {e.MIN:.4f} stats {e[model.RAW].mean():.4f}" for v, e in out.items()))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "replay" in sys.argv:
        replay()
    elif "teammates" in sys.argv:
        teammates([(s, u) for s in (0.25, 0.37, 0.5) for u in (0, 0.25, 0.5)])
    else:
        for games in ("fixed", "schedule"):
            report(*build(games), f"{SEASON}, games per week: {games}")
