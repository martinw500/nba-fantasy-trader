"""Backtest the 9-cat matchup model on a real NBA season, no Yahoo needed:  python backtest.py [2025-26]

Drafts 20 simulated 12-team leagues from preseason projections. Each week the model predicts every matchup from what
it would have known that Monday (last season blended with games so far, availability from games played), then the
prediction is scored against the real box scores that week. All games count (no daily lineup cap), rosters never change,
so predictions leave out League.week's FILL term (missed games covered at waiver level): nobody fills in here.
Prints calibration (predicted vs actual win rates), log loss and Brier score for categories and for whole matchups.
"""
import itertools
import random
import sys

import numpy as np
import pandas as pd

import model

SEASON = sys.argv[1] if len(sys.argv) > 1 else "2025-26"
PREV, PREV2 = (f"{int(SEASON[:4]) - n}-{int(SEASON[2:4]) - n + 1:02d}" for n in (1, 2))
LEAGUES, TEAMS, ROSTER = 20, 12, 13


def logs(season):
    """Every player's box score for the regular season (model.gamelogs), with its week number."""
    g = model.gamelogs(season)
    start = g.GAME_DATE.min() - pd.Timedelta(days=g.GAME_DATE.min().weekday())  # Monday of week 1
    g["week"] = (g.GAME_DATE - start).dt.days // 7
    return g


def season_setup():
    g, base = logs(SEASON), model.baseline(model.fetch(PREV), model.fetch(PREV2))
    weeks = range(int(g.week.max()) + 1)
    keys = sorted(set(base.index) | set(g.key))
    actual = {w: g[g.week == w].groupby("key")[model.RAW].sum().reindex(keys).fillna(0).to_numpy() for w in weeks}
    # team games per week, and each player's latest team before each week
    team_games = g.groupby(["TEAM_ABBREVIATION", "week"]).GAME_ID.nunique()
    first_team = g.sort_values("GAME_DATE").groupby("key").TEAM_ABBREVIATION.first()
    return g, base, keys, weeks, actual, team_games, first_team


def projections(g, base, keys, w):
    """What the model knew on the Monday of week w: per-game ROS line and availability (as League does)."""
    seen = g[g.week < w]
    cur = seen.groupby("key").agg(PLAYER_NAME=("PLAYER_NAME", "first"), GP=("GAME_ID", "count"),
                                  **{c: (c, "mean") for c in ["MIN"] + model.RAW})
    line = model.ros(base, cur).reindex(keys)
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
    g, base, keys, weeks, actual, team_games, first_team = season_setup()
    idx = {k: i for i, k in enumerate(keys)}
    line0, _ = projections(g, base, keys, 0)
    rosters = [[idx[k] for k in t] for s in range(LEAGUES) for t in draft(line0, s)]
    R = np.zeros((len(rosters), len(keys)))
    for i, t in enumerate(rosters):
        R[i, t] = 1
    pairs = [(l * TEAMS + a, l * TEAMS + b) for l in range(LEAGUES)
             for a, b in itertools.combinations(range(TEAMS), 2)]
    I, J = np.array(pairs).T
    E, Y = [], []
    for w in weeks:
        line, avail = projections(g, base, keys, w)
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


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for games in ("fixed", "schedule"):
        report(*build(games), f"{SEASON}, games per week: {games}")
