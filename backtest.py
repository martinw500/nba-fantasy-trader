"""Backtest the 9-cat matchup model on a real NBA season, no Yahoo needed:  python backtest.py [2025-26]
python backtest.py replay [2025-26]: rest-of-season error by expert-projection weight instead (fits PROJ_W).
python backtest.py teammates [2025-26]: next week's error with and without teammates' injuries (fits SHARE, USAGE).
python backtest.py onoff [2025-26]: the same by how far each team's own split counts (fits ONOFF_K).
python backtest.py rates [2025-26]: next week's and rest of season's stats error with a per-minute on/off split by stat
group (M22, measured Nov 3, not shipped: no better).
python backtest.py minor [2025-26]: the same with 15-25 minute sitters handing out minutes and a cap on what one sitter
hands out (M25, measured Nov 4, not shipped: stats worse, the cap no change).
python backtest.py bench: rest-of-season error by BENCH_MIN, the minutes players no expert projects are pulled toward,
2022-23 to 2025-26 (fits BENCH_MIN, M26).
python backtest.py rookies: rest-of-season error by a rookie's expert weight and rate shrink, 2022-23 to 2025-26 (fits
ROOKIE_W, ROOKIE_K: audit #3).
python backtest.py yahoo: this season's snapshots, each one's Yahoo lines vs each player's first-seen one, against the
games after it (audit #2: the live line stays, measured Nov 4).
python backtest.py trend: a 14-day form window's gap from rest of season by games in it (fits TREND, TREND_GP: M8).
python backtest.py gameday: how often players listed probable / questionable / doubtful for a game played it (fits
GAMEDAY), from the archived ESPN feeds and this season's box scores.
python backtest.py ahead: from every day of the week, how often a player who played his team's last game plays each of
his games left that week (checks fresh_plays(): This week's chance for his next game and later ones, M27).
python backtest.py news: how often a player who's playing is newly ruled out mid-week, and for how many of the week's
games (fits HAZARD, ONE_GAME: the add kept for late news, M17).
python backtest.py availability: each Monday's games-played guess vs the real share, 2024-25 and 2025-26 (fits AVAIL_*).
python backtest.py playoffs [2025-26]: each Monday's playoff and title odds vs what happened, with every later week an
average one and then from the real schedule (M2, scheduled()) (fits SEASON_SD, WEEK_SD).
python backtest.py recency: replay's error by minutes shrink and recent games' weight, 2022-23 to 2025-26 (fits
MIN_SHRINK, RECENT).

Drafts 20 simulated 12-team leagues from preseason projections. Each week the model predicts every matchup from what
it would have known that Monday (last season blended with games so far, availability from games played, regulars who
sat their team's last game out until a rough guess at their return, as the app does with ESPN's dates), then the
prediction is scored against the real box scores that week. Lines blend in ESPN's preseason projection (prior()). All games count (no daily lineup cap), rosters never change,
so predictions leave out League.week's FILL term (missed games covered at waiver level): nobody fills in here.
Prints calibration (predicted vs actual win rates), log loss and Brier score for categories and for whole matchups.
"""
import itertools
import random
import re
import sys

import numpy as np
import pandas as pd

import model

ARGS = [a for a in sys.argv[1:]
        if a not in ("replay", "teammates", "onoff", "minor", "bench", "rookies", "yahoo", "trend", "rates", "availability", "playoffs", "recency", "gameday", "news", "ahead")]
SEASON = ARGS[0] if ARGS else "2025-26"
PREV, PREV2 = (f"{int(SEASON[:4]) - n}-{int(SEASON[2:4]) - n + 1:02d}" for n in (1, 2))
LEAGUES, TEAMS, ROSTER = 20, 12, 13
RETURN_NOISE = 1.0  # ESPN's return dates vs the real ones: days out from Monday x e^N(0, this). A guess, nothing
                    # measures ESPN's misses yet (data/injuries_<date>.json starts that); 0 to 1.5 fit SCHED_CV 0.11-0.12


def logs(season):
    """Every player's box score for the regular season (model.gamelogs), with its week number."""
    g = model.gamelogs(season)
    start = g.GAME_DATE.min() - pd.Timedelta(days=g.GAME_DATE.min().weekday())  # Monday of week 1
    g["week"] = (g.GAME_DATE - start).dt.days // 7
    return g


def monday(g, w):
    """The Monday week w starts (logs()' weeks)."""
    return g.GAME_DATE.min() - pd.Timedelta(days=g.GAME_DATE.min().weekday() - 7 * w)


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
    baseline's games and this season's, of his team's games so far, as League does)."""
    past = g[g.week < w]
    cur = model.recent(past, monday(g, w))
    line = model.ros(pre, cur).reindex(keys)
    line[model.RAW] = line[model.RAW].fillna(0)
    team = past.sort_values("GAME_DATE").groupby("key").TEAM_ABBREVIATION.last()
    games = (team.map(past.groupby("TEAM_ABBREVIATION").GAME_DATE.nunique()).reindex(keys).astype(float)
             .fillna(cur.GP.max() if len(cur) else 0))
    avail = model.availability(base.GP.reindex(keys), cur.GP.reindex(keys).fillna(0), games)
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


def sat(g, line, team, w, low=None):
    """Regulars (or players with `low`+ minutes) who sat their team's last game before week w, as ESPN would list
    them: {key: the day he played again} (Timestamp.max: never)."""
    past, T = g[g.week < w], "TEAM_ABBREVIATION"
    last, played = past.groupby(T).GAME_DATE.max(), set(zip(past.key, past.GAME_DATE))
    back = g[g.week >= w].groupby("key").GAME_DATE.min()
    return {k: back.get(k, pd.Timestamp.max) for k in line.index[line.MIN >= (low or model.REGULAR)]
            if team.get(k) in last and (k, last[team[k]]) not in played}


def guess(out, monday, rng):
    """ESPN's return dates for these real ones (sat()): days out from Monday off by a factor e^N(0, RETURN_NOISE)."""
    return {k: r if r == pd.Timestamp.max else
            monday + pd.Timedelta(days=round((r - monday).days * np.exp(RETURN_NOISE * rng.standard_normal())))
            for k, r in out.items()}


def team_weeks(games="fixed", leagues=LEAGUES, info=None):
    """Each week w: (w, every simulated team's expected totals, the model's view that Monday, and its real totals).
    Teams are league-major (league l's are l * TEAMS + 0..11). Regulars out that Monday (sat()) sit until a noisy
    return (guess()), as League does with ESPN's dates: this week's games from then on (schedule, like This week), or
    the share of his team's remaining games after it (fixed, like League.avail). info: a list that gets each Monday's
    {w, M: availability x per-game line per player, team, back: {player: return}, R: rosters, days: each NBA team's
    game dates, week1} (playoffs()' schedule mode, M2)."""
    g, base, pre, keys, weeks, actual, team_games, first_team = season_setup()
    idx = {k: i for i, k in enumerate(keys)}
    line0, _ = projections(g, base, pre, keys, 0)
    rosters = [[idx[k] for k in t] for s in range(leagues) for t in draft(line0, s)]
    R = np.zeros((len(rosters), len(keys)))
    for i, t in enumerate(rosters):
        R[i, t] = 1
    week1 = g.GAME_DATE.min() - pd.Timedelta(days=g.GAME_DATE.min().weekday())
    days, rng = g.groupby("TEAM_ABBREVIATION").GAME_DATE.unique().map(np.sort), np.random.default_rng(0)
    for w in weeks:
        line, avail = projections(g, base, pre, keys, w)
        last = g[g.week < w].sort_values("GAME_DATE").groupby("key").TEAM_ABBREVIATION.last()
        team = pd.Series(keys, index=keys).map(last).fillna(pd.Series(keys, index=keys).map(first_team))
        if games == "fixed":
            n = np.full(len(keys), model.GAMES_WEEK)
        else:  # the real schedule: games the player's team played that week
            n = np.array([team_games.get((t, w), 0) for t in team], float)
        monday = week1 + pd.Timedelta(weeks=w)
        backs = guess(sat(g, line, team, w), monday, rng)
        if info is not None:
            info.append({"w": w, "M": avail[:, None] * line[model.RAW].to_numpy(), "team": team.to_numpy(), "back": backs,
                         "R": R, "days": days, "week1": week1, "idx": idx})
        for k, back in backs.items():
            left = days[team[k]][days[team[k]] >= monday]
            if games != "fixed":
                left = left[left < monday + pd.Timedelta(weeks=1)]
            n[idx[k]] *= (left >= back).mean() if len(left) else 0
        played_n = n * avail
        if games != "fixed":  # this week, players who played their team's last game (week 0: everyone) play their
            past = g[g.week < w]  # j-th game at fresh_plays() (League._week_p, M27): more of it than all season
            last, played = past.groupby("TEAM_ABBREVIATION").GAME_DATE.max(), set(zip(past.key, past.GAME_DATE))
            fresh = np.array([(k, last.get(team[k])) in played for k in keys]) if len(past) else np.ones(len(keys), bool)
            chain = sum(model.fresh_plays(avail, j) * np.clip(n - j + 1, 0, 1) for j in range(1, int(n.max()) + 1))
            played_n = np.where(fresh, chain, played_n)
        yield w, R @ (played_n[:, None] * line[model.RAW].to_numpy()), R @ actual[w]


def build(games="fixed"):
    """Every matchup of every week (all pairs in each league): expected totals for both sides and the real result."""
    pairs = [(l * TEAMS + a, l * TEAMS + b) for l in range(LEAGUES)
             for a, b in itertools.combinations(range(TEAMS), 2)]
    I, J = np.array(pairs).T
    E, Y = [], []
    for _, exp, act in team_weeks(games):
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
            line = model.ros(pre, model.recent(g[g.week < w], monday(g, w))).reindex(left.index)[model.RAW].fillna(0)
            err.append(float(((line[model.RAW] - left[model.RAW]).abs() / left[model.RAW].mean()).mean(axis=1).mean()))
        print(f"  weight {x:<5} week 0 {err[0]:.4f}   weeks 1-6 {np.mean(err[1:7]):.4f}   season {np.mean(err):.4f}")


def recency(seasons=("2022-23", "2023-24", "2024-25", "2025-26"),
            grid=((6, None), (3, None), (6, 45), (2, 45), (3, 30), (3, 45), (3, 60), (4, 45), (5, 45))):
    """replay() from week 1 at each minutes shrink (k_min) and recency half-life (days; None: every game alike), each
    season on its own ESPN preseason projection: error vs the old line (k_min 6, every game alike), weeks 1-6 and
    season. Fitted MIN_SHRINK 3 and RECENT 45 on all four (Oct 30). Rerun Nov 4 (after M26, A2): 4-6 now edge 3 here
    (season -0.04 to -0.32% at 4), but the weekly matchup backtest (python backtest.py [season]) is worse at 4 and 6 in
    three of four seasons, so both stay."""
    for season in seasons:
        prev, prev2 = (f"{int(season[:4]) - n}-{int(season[2:4]) - n + 1:02d}" for n in (1, 2))
        g, base = logs(season), model.baseline(model.fetch(prev), model.fetch(prev2))
        pre, err = model.prior(base, model.experts({}, model.espn(season), base, base.index.union(g.key.unique()))), {x: [] for x in grid}
        for w in range(1, int(g.week.max()) - 3):
            past, left = g[g.week < w], per_game(g[g.week >= w]).query("GP >= 10")
            for km, hl in grid:
                line = model.ros(pre, model.recent(past, monday(g, w), hl), k_min=km).reindex(left.index)[model.RAW].fillna(0)
                err[km, hl].append(float(((line - left[model.RAW]).abs() / left[model.RAW].mean()).mean(axis=1).mean()))
        e0 = np.array(err[grid[0]])
        print(f"\n== {season}: rest-of-season error by minutes shrink and half-life, vs k_min 6 with every game alike ==")
        for (km, hl), e in err.items():
            e = np.array(e)
            print(f"  k_min {km}, half-life {hl or '-':<3} weeks 1-6 {e[:6].mean():.4f} ({100 * (e[:6].mean() / e0[:6].mean() - 1):+.1f}%)"
                  f"   season {e.mean():.4f} ({100 * (e.mean() / e0.mean() - 1):+.1f}%)")


def bench(seasons=("2022-23", "2023-24", "2024-25", "2025-26"), grid=(None, 6, 8, 10, 12, 15, 18)):
    """M26: replay()'s rest-of-season error by model.BENCH_MIN (None: the fringe line's ~26 minutes, as shipped) for
    players no expert projects (each season's ESPN preseason projection), from week 0 to 4 weeks from the end: everyone
    with 10+ games left, and those players (their minutes bias too), week 0, weeks 1-6 and the season. Also how many
    minutes a game they projected and played, by their minutes this season so far."""
    old = model.BENCH_MIN
    for season in seasons:
        prev, prev2 = (f"{int(season[:4]) - n}-{int(season[2:4]) - n + 1:02d}" for n in (1, 2))
        g, base = logs(season), model.baseline(model.fetch(prev), model.fetch(prev2))
        ex = model.experts({}, model.espn(season), base, base.index.union(g.key.unique()))
        err = {x: {"all": [], "none": [], "bias": [], "n": []} for x in grid}
        for x in grid:
            model.BENCH_MIN = x
            pre = model.prior(base, ex)
            for w in range(int(g.week.max()) - 3):
                left = per_game(g[g.week >= w]).query("GP >= 10")
                line = model.ros(pre, model.recent(g[g.week < w], monday(g, w))).reindex(left.index)
                line[["MIN"] + model.RAW] = line[["MIN"] + model.RAW].fillna(0)
                e = ((line[model.RAW] - left[model.RAW]).abs() / left[model.RAW].mean()).mean(axis=1)
                no = ~left.index.isin(ex.index)
                err[x]["all"].append(float(e.mean())); err[x]["none"].append(float(e[no].mean()))
                err[x]["bias"].append(float((line.MIN - left.MIN)[no].mean())); err[x]["n"].append(int(no.sum()))
        print(f"\n== {season}: rest-of-season error by bench minutes for players no expert projects (M26) ==")
        e0 = {k: np.array(v) for k, v in err[grid[0]].items()}
        for x in grid:
            e = {k: np.array(v) for k, v in err[x].items()}
            print(f"  BENCH_MIN {str(x):5} everyone wk0 {e['all'][0]:.4f} wks1-6 {e['all'][1:7].mean():.4f} season "
                  f"{e['all'].mean():.4f} ({100 * (e['all'].mean() / e0['all'].mean() - 1):+.2f}%)   unprojected "
                  f"(n {e['n'].mean():.0f}) wk0 {e['none'][0]:.4f} wks1-6 {e['none'][1:7].mean():.4f} season "
                  f"{e['none'].mean():.4f} ({100 * (e['none'].mean() / e0['none'].mean() - 1):+.2f}%), minutes bias "
                  f"wks1-6 {e['bias'][1:7].mean():+.2f} season {e['bias'].mean():+.2f}")
    model.BENCH_MIN = old



def rookies(seasons=("2022-23", "2023-24", "2024-25", "2025-26"),
            grid=((None, None), (0.75, 10), (0.7, 10), (0.8, 10), (0.75, 8), (0.75, 12), (0.75, 15), (0.75, None),
                  (None, 10), (1.0, None))):
    """Audit #3: replay()'s rest-of-season error by model.ROOKIE_W (a rookie's expert weight in prior(); None: PROJ_W)
    and model.ROOKIE_K (games of his minutes his production per minute leans on in ros(); None: SHRINK), each season
    on its own ESPN preseason projection, players with 10+ games left from week 0 to 4 weeks from the end: everyone,
    rookies an expert projects (no baseline) and rookies none does, week 0, weeks 1-6 and the season, against both off.
    Fitted ROOKIE_W 0.75, ROOKIE_K 10 (Nov 4)."""
    old = model.ROOKIE_W, model.ROOKIE_K
    for season in seasons:
        prev, prev2 = (f"{int(season[:4]) - n}-{int(season[2:4]) - n + 1:02d}" for n in (1, 2))
        g, base = logs(season), model.baseline(model.fetch(prev), model.fetch(prev2))
        ex = model.experts({}, model.espn(season), base, base.index.union(g.key.unique()))
        err = {x: {"all": [], "pro": [], "none": [], "n": []} for x in grid}
        for x in grid:
            model.ROOKIE_W, model.ROOKIE_K = x
            pre = model.prior(base, ex)
            for w in range(int(g.week.max()) - 3):
                left = per_game(g[g.week >= w]).query("GP >= 10")
                line = model.ros(pre, model.recent(g[g.week < w], monday(g, w))).reindex(left.index)[model.RAW].fillna(0)
                e = ((line - left[model.RAW]).abs() / left[model.RAW].mean()).mean(axis=1)
                new = ~left.index.isin(base.index)
                err[x]["all"].append(float(e.mean())); err[x]["n"].append(int((new & left.index.isin(ex.index)).sum()))
                err[x]["pro"].append(float(e[new & left.index.isin(ex.index)].mean()))
                err[x]["none"].append(float(e[new & ~left.index.isin(ex.index)].mean()))
        e0 = {k: np.array(v) for k, v in err[grid[0]].items()}
        pct = lambda a, b: f"{100 * (a / b - 1):+.1f}%"
        print(f"\n== {season}: rest-of-season error by a rookie's expert weight and rate shrink (projected rookies: "
              f"n {e0['n'].mean():.0f} a Monday) ==")
        for x in grid:
            e = {k: np.array(v) for k, v in err[x].items()}
            print(f"  ROOKIE_W {str(x[0]):4} ROOKIE_K {str(x[1]):4} everyone wk0 {e['all'][0]:.4f} wks1-6 {e['all'][1:7].mean():.4f}"
                  f" season {e['all'].mean():.4f} ({100 * (e['all'].mean() / e0['all'].mean() - 1):+.2f}%) | projected rookies"
                  f" wk0 {pct(e['pro'][0], e0['pro'][0])} wks1-6 {pct(e['pro'][1:7].mean(), e0['pro'][1:7].mean())}"
                  f" season {pct(e['pro'].mean(), e0['pro'].mean())} | unprojected rookies wks1-6 "
                  f"{pct(np.nanmean(e['none'][1:7]), np.nanmean(e0['none'][1:7]))} season {pct(np.nanmean(e['none']), np.nanmean(e0['none']))}")
    model.ROOKIE_W, model.ROOKIE_K = old


def yahoo_lines(top=1.0):
    """Audit #2: the snapshots' Yahoo lines move in-season (Acuff 17.3 -> 23.3 points, Oct 27 -> Nov 3) and PROJ_W was
    fitted on frozen preseason lines, so is the live line counting this season twice? No past season's in-season Yahoo
    lines exist, so this season's own: from each snapshot in the folder (games before it, ESPN's copy of that day),
    each player's rest-of-season line with that snapshot's Yahoo line vs the one from the first snapshot that lists him,
    against his games after it (2+), for everyone and for players whose Yahoo line moved `top`+ points. Nov 4: the live
    line was better (moved players 0.530 vs 0.551, 39 of 54 player-weeks), so it stays. Rerun as snapshots pile up."""
    import glob, json, yahoo
    g, base = model.gamelogs(model.CUR), model.baseline(model.fetch(model.LAST), model.fetch(model.OLDER))
    keys, first, out = base.index.union(g.key.unique()), {}, {"day": [], "first": []}
    for f in sorted(glob.glob(str(model.ROOT / "*snapshot*.txt"))):
        s = yahoo.parse_text(open(f, encoding="utf-8").read())
        if not s.get("pulled"):
            continue
        for n, x in s["projections"].items():
            first.setdefault(n, x)
        d = pd.Timestamp(s["pulled"][:10]) + pd.Timedelta(days=1 if s["pulled"][11:13] >= "17" else 0)  # evening: games in
        past, left = g[g.GAME_DATE < d], per_game(g[g.GAME_DATE >= d]).query("GP >= 2")
        if not len(left):
            continue
        ep = sorted(p for p in glob.glob(str(model.DATA / "projections_20??-??-??.json")) if p[-15:-5] <= str(d.date()))
        esp = json.load(open(ep[-1] if ep else model.DATA / f"projections_{model.CUR}.json", encoding="utf-8"))
        cur, err, ex = per_game(past), {}, {}
        for kind, yl in (("day", s["projections"]), ("first", {n: first[n] for n in s["projections"]})):
            ex[kind] = model.experts(yl, esp, base, keys, cur)
            line = model.ros(model.prior(base, ex[kind]), model.recent(past, d)).reindex(left.index)[model.RAW].fillna(0)
            err[kind] = ((line - left[model.RAW]).abs() / left[model.RAW].mean()).mean(axis=1)
        yd, yf = (ex[k][ex[k].src == "Yahoo"].PTS for k in ("day", "first"))
        both = yd.index.intersection(yf.index).intersection(left.index)
        moved = both[(yd[both] - yf[both]).abs() >= top]
        for k in out:
            out[k].append(err[k].reindex(moved))
        print(f"  {s['pulled'][:10]}: everyone {err['day'].mean():.4f} with its Yahoo lines, {err['first'].mean():.4f} with the "
              f"first; Yahoo line moved {top}+ pts (n {len(moved)}): {err['day'].reindex(moved).mean():.4f} vs "
              f"{err['first'].reindex(moved).mean():.4f}")
    day, fst = pd.concat(out["day"]), pd.concat(out["first"])
    print(f"moved, pooled ({len(day)} player-weeks): live {day.mean():.4f}, first {fst.mean():.4f}; first better in {int((fst < day).sum())}")

def trend(seasons=("2022-23", "2023-24", "2024-25", "2025-26")):
    """M8: how far a FORM_DAYS window's value runs from rest of season by the games it holds. Each Monday from week 3 to
    4 weeks from the end, the TEAMS x ROSTER best on that Monday's rest-of-season line (ros() on prior(), as League)
    with FORM_GP+ games in the last FORM_DAYS: gap = form value - rest-of-season value (zscores() against the same pool,
    as League.zform), and the next FORM_DAYS' gap. By games in the window: the gap's spread (sd, and its fit
    sd^2 = a + b / games), the share past TREND, and how much of the gap the next window keeps (slope)."""
    rows = []
    for season in seasons:
        prev, prev2 = (f"{int(season[:4]) - n}-{int(season[2:4]) - n + 1:02d}" for n in (1, 2))
        g, base = logs(season), model.baseline(model.fetch(prev), model.fetch(prev2))
        pre = model.prior(base, model.experts({}, model.espn(season), base, base.index.union(g.key.unique())))
        cols, n = ["MIN"] + model.RAW, TEAMS * ROSTER
        for w in range(3, int(g.week.max()) - 3):
            mon = monday(g, w)
            line = model.ros(pre, model.recent(g[g.GAME_DATE < mon], mon)).dropna(subset=["MIN"])
            zr = model.zscores(line, n).sum(axis=1)
            top = zr.nlargest(n).index
            win = lambda a, b: g[(g.GAME_DATE >= a) & (g.GAME_DATE < b)]
            now = win(mon - pd.Timedelta(days=model.FORM_DAYS), mon)
            nxt = win(mon, mon + pd.Timedelta(days=model.FORM_DAYS))
            f, f2 = (x.groupby("key")[cols].mean().assign(GP=x.groupby("key").size()) for x in (now, nxt))
            f, f2 = f[f.GP >= model.FORM_GP], f2[f2.GP >= model.FORM_GP]
            gap = (model.zscores(f, n, line).sum(axis=1) - zr).reindex(top).dropna()
            gap2 = (model.zscores(f2, n, line).sum(axis=1) - zr).reindex(gap.index)
            rows.append(pd.DataFrame({"season": season, "week": w, "gp": f.GP.reindex(gap.index), "gap": gap, "next": gap2}))
    d = pd.concat(rows)
    d["gp"] = d.gp.clip(upper=8)
    def tab(x):
        nx = x.dropna(subset=["next"])
        return pd.Series({"n": len(x), "sd": x.gap.std(), "past TREND": (x.gap.abs() >= model.TREND).mean(),
                          "keeps": np.polyfit(nx.gap, nx.next, 1)[0] if len(nx) > 2 else np.nan})
    t = d.groupby("gp").apply(tab)
    a, b = np.linalg.lstsq(np.c_[np.ones(len(t)), 1 / t.index.to_numpy(float)], t.sd.to_numpy() ** 2, rcond=None)[0]
    t["fit sd"] = np.sqrt(a + b / t.index.to_numpy(float))
    print(f"\n== form gap by games in the last {model.FORM_DAYS} days, {', '.join(seasons)} (8 = 8+) ==")
    print(t.round(3).to_string())
    print(f"sd^2 = {a:.2f} + {b:.2f} / games; by season (sd at 3 / 5 / 7 games):")
    for s_, x in d.groupby("season"):
        print(" ", s_, [round(float(x[x.gp == k].gap.std()), 2) for k in (3, 5, 7)])
    return d


def mondays():
    """What the model knew each Monday from week 1 on: this season so far (past, cur), the rest-of-season line, each
    player's team, the regulars, who was out (sat his team's last game, until his next one: exact return dates, which
    ESPN's feed only estimates) and next week's box scores."""
    g, base = logs(SEASON), model.baseline(model.fetch(PREV), model.fetch(PREV2))
    ex, T = experts(g, base), "TEAM_ABBREVIATION"
    pre = model.prior(base, ex)
    first = g.sort_values("GAME_DATE").groupby("key")[T].first()
    for w in range(1, int(g.week.max()) + 1):
        past, nxt = g[g.week < w], g[g.week == w]
        cur = model.recent(past, monday(g, w))
        line = model.ros(pre, cur)
        team = past.sort_values("GAME_DATE").groupby("key")[T].last().combine_first(first)
        regs, out = line.index[line.MIN >= model.REGULAR], sat(g, line, team, w, 10)
        yield {"pre": pre, "past": past, "cur": cur, "line": line, "team": team, "regs": regs, "out": out, "nxt": nxt,
               "rest": g[g.week >= w], "expert": ex.index, "rookies": ex.index.difference(base.index), "monday": monday(g, w)}


def lifted(m, deboost=True):
    """Week m's per-game lines with teammates' injuries (League does the same): this season net of the lifts players
    had (model.had()), plus each team's out regulars' lifts (model.lifts(), everyone out at once sitting together, in
    stretches between their returns: model.stretches(); less what the preseason projection counts: model.priced_in())
    over a player's games in each stretch."""
    regs = m["line"].index[m["line"].MIN >= model.REGULAR]  # model.REGULAR as it is now (minor() moves it)
    m = {**m, "regs": regs, "out": {k: r for k, r in m["out"].items() if k in regs}}
    pool = m["line"][m["line"].index.isin(m["cur"].index) | m["line"].index.isin(m["regs"])]
    line = m["line"]
    adj = model.onoff(m["past"], m["team"], pool, m["regs"], m["monday"]) if model.ONOFF_K else None
    if deboost:
        h = model.had(m["past"], m["team"], pool, m["regs"], m["monday"], adj).reindex(m["cur"].index).dropna()
        full = m["cur"].copy()
        full.loc[h.index, h.columns] = (full.loc[h.index, h.columns] - h).clip(lower=0)
        line = model.ros(m["pre"], full)
    spans = model.stretches(m["out"], m["team"], m["nxt"].GAME_DATE.min())
    lift = model.priced_in(model.lifts(pool, m["team"], [s for _, _, s in spans], adj), m["cur"].index, m["cur"].GP,
                           m["expert"], m["rookies"])
    games = m["nxt"][["key", "GAME_DATE"]].merge(lift.index.to_frame(index=False), on="key")
    games["on"] = [spans[s][0] <= d < spans[s][1] for s, d in zip(games["set"], games.GAME_DATE)]
    share = games.groupby(["key", "by", "set"]).on.mean()
    boost = lift.loc[share.index].mul(share, axis=0).groupby(level="key").sum()
    return line, boost[boost.MIN > 0]


def teammates(grid=((model.SHARE, model.USAGE),), ks=(model.ONOFF_K,)):
    """Next week's per-game lines without teammates' injuries (off), with them (on: model.lifts net of model.had) and
    with lifts but no had() correction (fwd), vs what players did that week, as in replay(), plus the minutes bias
    (actual - line). Scored on players who played every one of their team's games that week: one who missed some (hurt
    mid-game, back on a minutes limit, in and out of the rotation) averaged 3.6 minutes under his line in the games he
    did play, which availability covers, not the line (2025-26: -1.1 min over everyone, +0.5 over full weeks). 20+:
    projected 20+ minutes, where playing every game is about health rather than teammates. All weeks: the partial ones
    too. Fitted SHARE and USAGE on 2025-26; ks: each ONOFF_K (0: the rule alone, no team's own split; fitted on
    2025-26 and 2024-25, M13)."""
    print(f"\n== {SEASON}: next week's lines with teammates' injuries ==")
    scored(list(mondays()), [{"SHARE": s, "USAGE": u, "ONOFF_K": k} for s, u in grid for k in ks])


def knobs(**kv):
    """Set model's knobs; returns their old values."""
    old = {k: getattr(model, k) for k in kv}
    for k, v in kv.items():
        setattr(model, k, v)
    return old


def scored(weeks, configs, wide=None):
    """teammates()' table for each config (model knobs to set): next week's error per player group. wide: knobs for
    a second "injured teammate" group, the players a wider rule lifts (M25: sitters at 15+ minutes) that the shipped
    rule doesn't."""
    cols = ["MIN"] + model.RAW
    old = knobs(ONOFF_K=0)  # injured teammate: the players the rule lifts, the same group whatever ONOFF_K
    hits = [lifted(m)[1].index for m in weeks]
    knobs(**old)
    extra = None
    if wide:
        old = knobs(ONOFF_K=0, **wide)
        extra = [lifted(m)[1].index.difference(h) for m, h in zip(weeks, hits)]
        knobs(**old)
    for cfg in configs:
        old = knobs(**cfg)
        rows = {k: [] for k in ("act", "off", "on", "fwd", "hit", "new", "full")}
        for m, lift_rule, new_rule in zip(weeks, hits, extra or [None] * len(weeks)):
            act = per_game(m["nxt"])
            act = act[act.index.isin(m["line"].index)]
            on, boost = lifted(m)
            fwd, _ = lifted(m, deboost=False)
            add = boost.reindex(act.index).fillna(0)
            games = m["team"].reindex(act.index).map(m["nxt"].groupby("TEAM_ABBREVIATION").GAME_ID.nunique())
            rows["act"].append(act[cols]); rows["off"].append(m["line"].loc[act.index, cols])
            rows["on"].append(on.loc[act.index, cols] + add); rows["fwd"].append(fwd.loc[act.index, cols] + add)
            rows["hit"].append(pd.Series(act.index.isin(lift_rule), index=act.index))
            rows["new"].append(pd.Series(act.index.isin(new_rule) if new_rule is not None else False, index=act.index))
            rows["full"].append(act.GP >= games.fillna(np.inf))
        knobs(**old)
        act, hit, full = pd.concat(rows["act"]), pd.concat(rows["hit"]).to_numpy(), pd.concat(rows["full"]).to_numpy()
        new, big = pd.concat(rows["new"]).to_numpy(bool), (pd.concat(rows["off"]).MIN >= 20).to_numpy()
        print("  " + " ".join(f"{k} {v}" for k, v in cfg.items()))
        groups = (("injured teammate", hit & full), ("  20+ min", hit & full & big), ("everyone", full),
                  ("  20+ min", full & big), ("injured, all weeks", hit))
        for name, sub in groups + ((("lifted by 15+ only", new & full),) if extra else ()):
            a = act[sub]
            err = lambda p: ((p[sub] - a).abs() / a.mean()).mean()
            out = {v: (err(pd.concat(rows[v])), (a - pd.concat(rows[v])[sub]).MIN.mean()) for v in ("off", "on", "fwd")}
            print(f"    {name:18} n {sub.sum():5}  " + "   ".join(
                f"{v} MIN {e.MIN:.4f} stats {e[model.RAW].mean():.4f} bias {b:+.2f}" for v, (e, b) in out.items()))


def minor(seasons=None):
    """M25: teammates()' table with sitters of 15-25 minutes handing out SHARE_LOW of theirs (REGULAR 15), and with
    what one sitter hands out capped at his own minutes (CAP_GIVE), against the shipped rule (REGULAR 25)."""
    base = {"REGULAR": model.REGULAR, "SHARE_LOW": model.SHARE_LOW, "CAP_GIVE": model.CAP_GIVE}
    configs = [base, {**base, "CAP_GIVE": True}] + [{"REGULAR": 15, "SHARE_LOW": x, "CAP_GIVE": c}
                                                   for x in (0.1, 0.2, 0.35, 0.5) for c in (False, True)]
    print(f"\n== {SEASON}: next week's lines, sitters under 25 minutes and the cap (M25) ==")
    scored(list(mondays()), configs, wide={"REGULAR": 15, "SHARE_LOW": 0.5})
    knobs(**base)


LIFTS = model.lifts


def rates(m, pool, k):
    """M22 (measured Nov 3, not shipped): each receiver's production per minute in the games each regular sat vs his
    games with him, beyond the per-minute change lifts() already makes (USAGE), as {(receiver, sitter): per-minute
    change to RAW}. Per receiver in the rotation, a ridge like model.onoff()'s on per-minute stats, each game weighted
    by recent()'s weight x its minutes, each sitter's effect pulled toward lifts()' as if that were k more games at his
    minutes. pool: lifted()'s."""
    T, RAW, logs, team = "TEAM_ABBREVIATION", model.RAW, m["past"], m["team"]
    s = model.sat(logs, team, m["regs"])
    if not len(s):
        return None
    rot = pool.loc[pool.MIN >= 10, ["MIN"] + RAW].astype(float)
    one = LIFTS(pool, team, [frozenset([j]) for j in s.by.unique()]).droplevel("set")  # each sitter alone
    r = rot.loc[one.index.get_level_values("key")]
    rule = pd.DataFrame((one[RAW].to_numpy() - one[["MIN"]].to_numpy() * r[RAW].div(r.MIN, axis=0).to_numpy())
                        / (r.MIN.to_numpy() + one.MIN.to_numpy())[:, None], index=one.index, columns=RAW)
    out = {}
    for t, st in s.groupby(T):
        by = list(st.by.unique())
        g = logs[(logs[T] == t) & logs.key.isin(rot.index) & (logs.key.map(team) == t)]
        x = pd.crosstab(st.GAME_ID, st.by).reindex(index=g.GAME_ID, columns=by, fill_value=0).clip(upper=1).to_numpy(float)
        age, mins = model.age_wt(g.GAME_DATE, m["monday"]).to_numpy(), g.MIN.to_numpy(float)
        y = g[RAW].to_numpy(float) / np.maximum(mins, 1)[:, None]
        for i, rows in g.groupby("key").indices.items():
            cols, w = [c for c, j in enumerate(by) if j != i and x[rows, c].any()], age[rows] * mins[rows]
            if not cols or w.sum() <= 0:
                continue
            X = np.column_stack([np.ones(len(rows)), x[rows][:, cols]])
            p = np.vstack([np.zeros(len(RAW)), rule.reindex(pd.MultiIndex.from_tuples([(i, by[c]) for c in cols])).fillna(0)])
            pen = np.diag([0.0] + [k * w.sum() / age[rows].sum()] * len(cols))
            beta = np.linalg.solve(X.T @ (w[:, None] * X) + pen, X.T @ (w[:, None] * y[rows]) + pen @ p)
            out.update({(i, by[c]): d for c, d in zip(cols, beta[1:] - p[1:])})
    return pd.DataFrame(list(out.values()), index=pd.MultiIndex.from_tuples(list(out)), columns=RAW) if out else None


def rated(radj):
    """model.lifts plus rates()' per-minute change on a receiver's minutes while that sitter sits (his line's and the
    lift's), so had() and the lifts both carry it."""
    def lifts(line, team, sets, adj=None):
        out = LIFTS(line, team, sets, adj)
        if radj is None or not len(out):
            return out
        kb = out.index.droplevel("set")
        out = out.copy()
        out[model.RAW] += radj.reindex(kb).fillna(0).to_numpy() * (
            line.MIN.reindex(kb.get_level_values("key")).to_numpy() + out.MIN.to_numpy())[:, None]
        return out
    return lifts


def ratesplit(ks=(None, 10, 20, 40, 80)):
    """M22 (Nov 3, not shipped): stats error as in teammates() by stat group with rates()' per-minute split at each k
    (None: off, the shipped model), over everything lifted() does: next week (full weeks; lifted: players the shipped
    model lifts) and rest of season (10+ games left, the boost over all of them, as replay() scores)."""
    groups = {"shooting": model.RAW[:5] + ["PTS"], "REB": ["REB"], "AST/TOV": ["AST", "TOV"], "STL/BLK": ["STL", "BLK"]}
    act, pred, lift = {"wk": [], "ros": []}, {(k, s): [] for k in ks for s in ("wk", "ros")}, []
    for m in mondays():
        wk = per_game(m["nxt"])
        wk = wk[wk.index.isin(m["line"].index)]
        games = m["team"].reindex(wk.index).map(m["nxt"].groupby("TEAM_ABBREVIATION").GAME_ID.nunique())
        wk = wk[wk.GP >= games.fillna(np.inf)]
        ros = per_game(m["rest"]).query("GP >= 10")
        ros = ros[ros.index.isin(m["line"].index)]
        act["wk"].append(wk); act["ros"].append(ros)
        pool = m["line"][m["line"].index.isin(m["cur"].index) | m["line"].index.isin(m["regs"])]
        for k in ks:
            model.lifts = rated(rates(m, pool, k) if k else None)
            try:
                for s, a, nxt in (("wk", wk, m["nxt"]), ("ros", ros, m["rest"])):
                    line, boost = lifted({**m, "nxt": nxt})
                    pred[k, s].append(line.loc[a.index, model.RAW] + boost.reindex(a.index).fillna(0)[model.RAW])
                    if k is None and s == "wk":
                        lift.append(pd.Series(a.index.isin(boost.index), index=a.index))
            finally:
                model.lifts = LIFTS
    lift = pd.concat(lift).to_numpy()
    print(f"\n== {SEASON}: stats error by per-minute split shrink (games), M22 ==")
    for name, s, sub in (("next week", "wk", None), ("  lifted", "wk", lift), ("rest of season", "ros", None)):
        a = pd.concat(act[s])[model.RAW]
        sub = np.ones(len(a), bool) if sub is None else sub
        print(f"  {name} (n {sub.sum()})")
        for k in ks:
            e = ((pd.concat(pred[k, s])[sub] - a[sub]).abs() / a[sub].mean()).mean()
            print(f"    k {k or 'off':<4} all {e.mean():.5f}  " + "  ".join(f"{g} {e[c].mean():.5f}" for g, c in groups.items()))


def mondays_played(season):
    """Each Monday from week 1 to 4 weeks from the end, the 200 best players on that Monday's line: games in his better
    past season (best, of 82) and each of the last three, games so far (played, of `games`: his team's, as League
    does), how many of his team's games in a row he's sat (out: 0 if he played the last one) and the share of
    his team's games left (y) and this week's (wk) he played."""
    prev, prev2, prev3 = (f"{int(season[:4]) - n}-{int(season[2:4]) - n + 1:02d}" for n in (1, 2, 3))
    g, last, older, T = logs(season), model.fetch(prev), model.fetch(prev2), "TEAM_ABBREVIATION"
    base, first, rows = model.baseline(last, older), g.sort_values("GAME_DATE").groupby("key")[T].first(), []
    third = model.fetch(prev3).GP
    for w in range(1, int(g.week.max()) - 3):
        past, fut, nxt = g[g.week < w], g[g.week >= w], g[g.week == w]
        cur = per_game(past)
        team = past.sort_values("GAME_DATE").groupby("key")[T].last().combine_first(first)
        pool = model.zscores(model.ros(base, cur).dropna(subset=["MIN"]), TEAMS * ROSTER).sum(axis=1).nlargest(200)
        days, seen = past.groupby(T).GAME_DATE.unique(), past.groupby("key").GAME_DATE.max()
        on = [k for k in pool.index if team.get(k) in days]
        share = lambda games: (games.groupby("key").GAME_ID.count().reindex(on).fillna(0)
                               / team[on].map(games.groupby(T).GAME_ID.nunique())).clip(upper=1)
        rows.append(pd.DataFrame({"season": season, "week": w, "best": base.GP.reindex(on), "last": last.GP.reindex(on),
                                  "older": older.GP.reindex(on), "third": third.reindex(on),
                                  "played": cur.GP.reindex(on).fillna(0),
                                  "games": team[on].map(past.groupby(T).GAME_DATE.nunique()),
                                  "out": [(days[team[k]] > seen.get(k, pd.Timestamp.min)).sum() for k in on],
                                  "left": team[on].map(fut.groupby(T).GAME_ID.nunique()), "y": share(fut),
                                  "wk": share(nxt).fillna(0), "wk_n": team[on].map(nxt.groupby(T).GAME_ID.nunique())}))
    return pd.concat(rows)


def availability(seasons=("2022-23", "2023-24", "2024-25", "2025-26")):
    """Each Monday's availability guess (model.availability() over a grid of its knobs, and the rule before N9) vs the
    share of games left that players who played their team's last game suited up for (mondays_played()): squared error
    weighted by games left, and bias (guess - actual), pooled, per season, on the first 6 weeks and for players who lost
    one of the two past seasons (under 30 games, 60+ in the other), repeat absentees (under 60 in both), sophomores
    (no games before last season) and rookies. Fitted AVAIL_PAST, AVAIL_K and AVAIL_T on 2024-25 and 2025-26 (Oct 28);
    refitting on all four seasons gains 1% and holds out worse on those two (M5). Then, for players who sat their
    team's last game (no return date known: STATUS_AVAIL's case), games played over the guess by how many they'd sat."""
    d = pd.concat(map(mondays_played, seasons), ignore_index=True)
    sat, d = d[d.out > 0].copy(), d[d.out == 0]
    lo, hi = d[["last", "older"]].fillna(0).min(axis=1), d[["last", "older"]].fillna(0).max(axis=1)
    subs = {"all": d.week > 0, **{s: d.season == s for s in seasons}, "weeks 1-6": d.week <= 6,
            "lost one": (lo < 30) & (hi >= 60) & d.older.notna(), "repeat": (hi < 60) & d.older.notna(),
            "sophomore": d.older.isna() & d["last"].notna() & d.third.isna(), "rookie": d.best.isna()}
    old = (d["last"].fillna(0) + 0.5 * d.older.fillna(0) + d.played + 0.85 * (123 + d.games) / 2) / (1.5 * (123 + d.games))
    old = old.where(d.best.notna(), 0.85)  # before N9: both seasons (the older at half) and this one, 1/3 toward 85%

    def err(p, sub):
        e, w = (p - d.y)[sub], d.left[sub]
        return float((w * e ** 2).sum() / w.sum()), float((w * e).sum() / w.sum())

    def guess(knobs):
        model.AVAIL_PAST, model.AVAIL_K, model.AVAIL_T = knobs
        return model.availability(d.best, d.played, d.games)

    chosen = model.AVAIL_PAST, model.AVAIL_K, model.AVAIL_T
    grid = list(itertools.product((0, 0.1, 0.2, 0.33, 0.5, 1), (20, 30, 50, 70), (0.6, 0.65, 0.7, 0.75, 0.8, 0.85)))
    p = {x: guess(x) for x in grid}
    print(f"\n== availability: {len(d)} player-Mondays, actual {d.y.mean():.3f} ==")
    print(" " * 26 + "".join(f"{s} ({sub.sum()})".rjust(19) for s, sub in subs.items()))
    for name, x in [("before N9", old), (f"chosen {chosen}", guess(chosen))] + [
            (f"{k}", p[k]) for k in sorted(grid, key=lambda k: err(p[k], subs["all"]))[:5]]:
        print(f"{name:26}" + "".join(f"{e:.4f} {b:+.3f}".rjust(19) for e, b in (err(x, s) for s in subs.values())))
    for s in seasons:
        k = min(grid, key=lambda k: err(p[k], d.season != s)[0])
        print(f"best without {s} {k}: {s} {err(p[k], subs[s])[0]:.4f} (chosen {err(guess(chosen), subs[s])[0]:.4f})")
    guess(chosen)
    sat["p"] = model.availability(sat.best, sat.played, sat.games)
    ratio = lambda x: pd.Series({"n": len(x), "rest of season": (x.y * x.left).sum() / (x.p * x.left).sum(),
                                 "this week": (x.wk * x.wk_n).sum() / (x.p * x.wk_n).sum()})
    print(f"\n== sat his team's last game: games played / availability(), by games in a row sat ==")
    print(pd.concat([sat.groupby(pd.cut(sat.out, [0, 1, 2, 5, 10, 999], labels=["1", "2", "3-5", "6-10", "11+"]),
                                 observed=True).apply(ratio).T, ratio(sat).rename("all")], axis=1).round(3).to_string())


def news(seasons=("2022-23", "2023-24", "2024-25", "2025-26")):
    """Late news (M17): how often a player who's playing is newly ruled out mid-week. Each Monday from week 1 to 4 weeks
    from the end, the 156 best players on that Monday's line who played their team's last game; then each of his
    team's games that week after one he played: did he sit it (a new absence), and if so how many of the week's games
    left he missed. Fits model.HAZARD (new absences per game / (1 - availability()), as mondays_played() guesses it)
    and model.ONE_GAME (each absence either that one game or the rest of the week: the share of one-game ones that
    matches the games missed, least squares over absences with 2+ games left)."""
    rows = []
    for season in seasons:
        prev, prev2 = (f"{int(season[:4]) - n}-{int(season[2:4]) - n + 1:02d}" for n in (1, 2))
        g, T = logs(season), "TEAM_ABBREVIATION"
        base, first = model.baseline(model.fetch(prev), model.fetch(prev2)), g.sort_values("GAME_DATE").groupby("key")[T].first()
        days, played = g.groupby(T).GAME_DATE.unique().map(sorted), set(zip(g.key, g.GAME_DATE))
        for w in range(1, int(g.week.max()) - 3):
            past, mon = g[g.week < w], monday(g, w)
            cur, games = per_game(past), past.groupby(T).GAME_DATE.nunique()
            team = past.sort_values("GAME_DATE").groupby("key")[T].last().combine_first(first)
            pool = model.zscores(model.ros(base, cur).dropna(subset=["MIN"]), TEAMS * ROSTER).sum(axis=1).nlargest(TEAMS * ROSTER)
            on = [k for k in pool.index if team.get(k) in days.index]
            av = model.availability(base.GP.reindex(on), cur.GP.reindex(on).fillna(0), team[on].map(games).fillna(0))
            for k in on:
                before = [d for d in days[team[k]] if d < mon]
                wk = [(k, d) in played for d in days[team[k]] if mon <= d < mon + pd.Timedelta(days=7)]
                if not before or (k, before[-1]) not in played:
                    continue
                for j, p in enumerate(wk):
                    if j == 0 or wk[j - 1]:
                        rows.append((season, 1 - av[k], not p, len(wk) - j, 0 if p else len(wk) - j - sum(wk[j:])))
    d = pd.DataFrame(rows, columns=["season", "miss", "new", "left", "missed"])
    fifth = pd.qcut(d.miss, 5, labels=False)
    n = d[d.new & (d.left > 1)]
    print(f"\n== new absences: {len(d)} player-games after a game played, {int(d.new.sum())} sat ==")
    for s, x in [*d.groupby("season"), ("all", d)]:
        m = n if s == "all" else n[n.season == s]
        z, f = (m.left - 1) / m.left, m.missed / m.left
        print(f"{s:8} {len(x):6} games, sat {x.new.mean():.3f}, HAZARD {x.new.sum() / x.miss.sum():.3f}, ONE_GAME "
              f"{((1 - f) * z).sum() / (z ** 2).sum():.3f} (missed one {(m.missed == 1).mean():.3f}, the rest of the "
              f"week {(m.missed == m.left).mean():.3f}, of {len(m)} with 2+ games left)")
    print("by availability fifth (1 - availability, sat, sat / (1 - availability)):")
    print(d.groupby(fifth).apply(lambda x: pd.Series({"1-avail": x.miss.mean(), "sat": x.new.mean(),
                                                     "HAZARD": x.new.sum() / x.miss.sum()})).round(3).to_string())
    print("model.HAZARD, ONE_GAME:", model.HAZARD, model.ONE_GAME)


def ahead(seasons=("2022-23", "2023-24", "2024-25", "2025-26")):
    """Tomorrow's game at tomorrow's odds (M27): from every day of the week (today, as the app sees it before tip-off),
    the 156 best players on that Monday's line who played their team's last game, each of his team's games left that
    week (to Sunday): did he play it, by days ahead (0 = today), by game (1 = his team's next) and by week of the
    season, against this week's chance (model.fresh_plays() for the game's place, availability() as League has it that
    day) and the flat rule before it (1 - 0.45 x (1 - availability()), WEEK_K); the rest of the week's games played
    against each one's sum, by weekday. K = (1 - played) / (1 - availability()). Nov 4: the chain within 2% of every
    game's share and 1% of every weekday's (the flat rule +3.2% low on the next game, 4.9% high on the 4th); weeks 1-2
    run healthier still (next game K 0.23, not 0.33: 2.7% more played than the chain says), not shipped (Brier 83.59 ->
    83.51 with a hazard by part of the season)."""
    rows = []
    for season in seasons:
        prev, prev2 = (f"{int(season[:4]) - n}-{int(season[2:4]) - n + 1:02d}" for n in (1, 2))
        g, T = logs(season), "TEAM_ABBREVIATION"
        base, first = model.baseline(model.fetch(prev), model.fetch(prev2)), g.sort_values("GAME_DATE").groupby("key")[T].first()
        days, played = g.groupby(T).GAME_DATE.unique().map(sorted), set(zip(g.key, g.GAME_DATE))
        for w in range(1, int(g.week.max()) - 3):
            mon = monday(g, w)
            cur = per_game(g[g.week < w])
            team = g[g.week < w].sort_values("GAME_DATE").groupby("key")[T].last().combine_first(first)
            pool = model.zscores(model.ros(base, cur).dropna(subset=["MIN"]), TEAMS * ROSTER).sum(axis=1).nlargest(TEAMS * ROSTER)
            on = [k for k in pool.index if team.get(k) in days.index]
            for t in (mon + pd.Timedelta(days=i) for i in range(7)):
                past = g[g.GAME_DATE < t]
                n = past.groupby("key").GAME_ID.count().reindex(on).fillna(0)
                av = model.availability(base.GP.reindex(on), n, team[on].map(past.groupby(T).GAME_DATE.nunique()).fillna(0))
                for k in on:
                    before = [d for d in days[team[k]] if d < t]
                    if not before or (k, before[-1]) not in played:
                        continue
                    left = [d for d in days[team[k]] if t <= d < mon + pd.Timedelta(days=7)]
                    for j, d in enumerate(left):
                        rows.append((season, w, t.weekday(), (d - t).days, j + 1, 1 - av[k], (k, d) in played))
    d = pd.DataFrame(rows, columns=["season", "week", "dow", "ahead", "game", "miss", "played"])
    d["flat"], d["chain"] = 1 - 0.45 * d.miss, model.fresh_plays(1 - d.miss, d.game)
    tab = lambda x: pd.Series({"n": len(x), "played": x.played.mean(), "chain": x.chain.mean(), "flat": x.flat.mean(),
                               "K": (1 - x.played).sum() / x.miss.sum()})
    brier = lambda c: 1000 * ((d[c] - d.played) ** 2).mean()
    print(f"\n== {len(d)} player-games from each day of the week, after he played his team's last game ==")
    print("by days ahead (0 = today):")
    print(d.groupby("ahead").apply(tab).round(3).to_string())
    print("by game (1 = his team's next):")
    print(d.groupby("game").apply(tab).round(3).to_string())
    print("by season, today's game / next game (K):")
    print(pd.DataFrame({"today": d[d.ahead == 0].groupby("season").apply(tab).K,
                        "next": d[d.game == 1].groupby("season").apply(tab).K,
                        "later": d[d.game > 1].groupby("season").apply(tab).K}).round(3).to_string())
    print("next game's K by availability fifth:")
    print(d[d.game == 1].groupby(pd.qcut(d.miss, 5)).apply(tab).round(3).to_string())
    print("next game / later games by week of the season:")
    wk = pd.cut(d.week, [0, 2, 5, 10, 15, 40])
    print(pd.concat([d[d.game == 1].groupby(wk, observed=True).apply(tab).add_prefix("next "),
                     d[d.game > 1].groupby(wk, observed=True).apply(tab).add_prefix("later ")], axis=1).round(3).to_string())
    print("rest of the week from each weekday (0 = Monday): games played / each rule's sum")
    print(d.groupby("dow").apply(lambda x: pd.Series({"games": len(x), "played": x.played.sum(),
                                                      "chain": x.played.sum() / x.chain.sum(),
                                                      "flat": x.played.sum() / x.flat.sum()})).round(3).to_string())
    print(f"Brier (per 1000 games): chain {brier('chain'):.2f}, flat {brier('flat'):.2f}; model.HAZARD, ONE_GAME:",
          model.HAZARD, model.ONE_GAME)
    return d


def gameday():
    """How often players ESPN listed for a game (probable / questionable / doubtful, and out) played it: every note in
    the daily feeds (data/injuries_<date>.json from Oct 28: the first day's notes can name earlier games) and the note
    log (data/injury_notes.jsonl, from Nov 3, with ESPN's note time), resolved to the game it names as League does,
    against this season's box scores. Fits model.GAMEDAY (Nov 3: 104 player-games). A note's game is his team's first
    on that weekday from the day before it was written (or first seen), within a week; each player-game counts once."""
    import glob
    import json
    from datetime import date, timedelta
    lg = model.load()
    done, played = lg.logs.GAME_DATE.max().date(), set(zip(lg.logs.key, lg.logs.GAME_DATE.dt.date))
    pat = re.compile(r"\b(probable|questionable|doubtful|game-time decision|out|ruled out|won't play|will not play)\b"
                     r"[^.]{0,60}?\b(" + "|".join(model.DAYS) + r")'s\b", re.I)
    seen = {}  # (name, note) -> the day it was written (ESPN's time) or first seen
    for f in sorted(glob.glob(str(model.DATA / "injuries_2026-*.json"))):
        day = date.fromisoformat(f[-15:-5])
        for name, i in json.loads(open(f, encoding="utf-8").read()).items():
            seen.setdefault((name, i.get("note", "")), (i.get("date") or "")[:10] or (None if day == date(2026, 10, 27) else str(day)))
    log = model.DATA / "injury_notes.jsonl"
    for r in (map(json.loads, log.read_text(encoding="utf-8").splitlines()) if log.exists() else ()):
        seen[r["name"], r["note"]] = (r.get("date") or r["seen"])[:10]
    rows = {}
    for (name, note), when in seen.items():
        if not when or not (m := pat.search(note)) or not (k := lg.find(name, strict=False)):
            continue
        lo, want = date.fromisoformat(when) - timedelta(1), model.DAYS.index(m[2].lower())
        g = next((date.fromisoformat(d) for d in lg.sched.get(lg.nba.get(k), ()) if lo <= date.fromisoformat(d) <= lo + timedelta(7)
                  and date.fromisoformat(d).weekday() == want), None)
        if g and g <= done:
            a = m[1].lower()
            rows.setdefault((k, g), "questionable" if a == "game-time decision" else "out" if a not in model.GAMEDAY else a)
    d = pd.DataFrame([{"as": a, "played": (k, g) in played} for (k, g), a in rows.items()])
    t = d.groupby("as").played.agg(["count", "sum"])
    t["rate"], t["jeffreys"] = (t["sum"] / t["count"]).round(3), ((t["sum"] + 0.5) / (t["count"] + 1)).round(2)
    print(f"\n== game-day listings vs box scores through {done}: {len(d)} player-games ==")
    print(t.to_string())
    print("model.GAMEDAY:", model.GAMEDAY)


# 2026-27's season on 2025-26's NBA weeks: 20 matchups (weeks 7 and 17 run two NBA weeks, 6-7 and 16-17), then three
# one-week playoff rounds (NBA weeks 22-24, Mar 23 - Apr 12)
SPANS = [[w] for w in range(6)] + [[6, 7]] + [[w] for w in range(8, 16)] + [[16, 17]] + [[w] for w in range(18, 25)]
REGULAR_WEEKS, SEATS = 20, 8


def round_robin(n=TEAMS):
    """Every team plays every other once in n - 1 weeks (the circle method), as Yahoo's schedule does."""
    t, out = list(range(n)), []
    for _ in range(n - 1):
        out.append([(t[i], t[n - 1 - i]) for i in range(n // 2)])
        t = [t[0], t[-1]] + t[1:-1]
    return out


def scheduled(v, spans):
    """M2: each span's expected totals per simulated team from Monday v's view (team_weeks' info): each player's NBA
    games in the span's weeks (from his return date, if he's out) x availability x per-game line, as League.playoffs
    does with the real schedule. -> [(teams, RAW)] per span."""
    week = lambda w: (v["week1"] + pd.Timedelta(weeks=w), v["week1"] + pd.Timedelta(weeks=w + 1))
    cnt = {}
    for t, d in v["days"].items():
        for w in {w for ws in spans for w in ws}:
            a, b = week(w)
            cnt[t, w] = int(((d >= a) & (d < b)).sum())
    out = []
    for ws in spans:
        n = np.array([sum(cnt.get((t, w), 0) for w in ws) for t in v["team"]], float)
        for k, back in v["back"].items():
            d = v["days"].get(v["team"][v["idx"][k]], np.array([], "datetime64[ns]"))
            n[v["idx"][k]] = sum(int(((d >= max(week(w)[0], back)) & (d < week(w)[1])).sum()) for w in ws)
        out.append(v["R"] @ (n[:, None] * v["M"]))
    return out


def playoffs(grid=((0, 0), (0.35, 0), (0, 0.6), (0.25, 0.6), (0.35, 0.6), (0.45, 0.6)), leagues=60, sims=1000,
             games="fixed"):
    """Each Monday's playoff and title odds (model.season_sim on the model's view that Monday, with each grid's
    (SEASON_SD, WEEK_SD)) vs what happened: the real box scores played out over a round-robin schedule, top SEATS in, a
    reseeded bracket with ties to the higher seed. Log loss and calibration, pooled over Mondays 0-18 and early (0-5).
    Fitted SEASON_SD and WEEK_SD on 2025-26 (Oct 28). Title odds are hard to judge: each league has one real champion.
    games: "fixed" (every later week an average one, GAMES_WEEK games a player x weeks in the span) or "schedule" (each
    span's real NBA games per player, from his return date: scheduled(), as League.playoffs since M2)."""
    E, A, info = {}, {}, []
    for w, exp, act in team_weeks("fixed", leagues, info):
        E[w], A[w] = exp, act
    view = {v["w"]: v for v in info}
    rr, cats = round_robin(), len(model.CATS)
    real = [sum(A[w] for w in ws) for ws in SPANS]
    out = {x: {"made": [], "title": []} for x in grid}
    truth = {"made": [], "title": [], "monday": []}
    for l in range(leagues):
        T = slice(l * TEAMS, (l + 1) * TEAMS)
        sched = [np.array(rr[s % (TEAMS - 1)]).T for s in range(REGULAR_WEEKS)]
        res = [outcome(real[s][T][I], real[s][T][J]) for s, (I, J) in enumerate(sched)]
        wins = np.zeros((REGULAR_WEEKS + 1, TEAMS))
        for s, ((I, J), y) in enumerate(zip(sched, res)):
            wins[s + 1] = wins[s]
            np.add.at(wins[s + 1], I, y.sum(-1)); np.add.at(wins[s + 1], J, cats - y.sum(-1))
        post = [outcome(real[s][T][:, None], real[s][T][None]) for s in range(REGULAR_WEEKS, len(SPANS))]
        made, champ, _ = model.season_sim(wins[-1], [], [(y, (y == 0.5) * 1.0) for y in post], SEATS, 1, l, (0, 0))
        for s0 in range(REGULAR_WEEKS - 1):
            e = E[SPANS[s0][0]][T]
            if games == "schedule":
                ev = [x[T] for x in scheduled(view[SPANS[s0][0]], SPANS[s0:])]
                weeks = [(I, J, *model.cat_probs(ev[s - s0][I], ev[s - s0][J], model.CATS, ties=True))
                         for s, (I, J) in list(enumerate(sched))[s0:]]
                bracket = [model.cat_probs(x[:, None], x[None], model.CATS, ties=True) for x in ev[REGULAR_WEEKS - s0:]]
            else:
                weeks = [(I, J, *model.cat_probs(len(SPANS[s]) * e[I], len(SPANS[s]) * e[J], model.CATS, ties=True))
                         for s, (I, J) in list(enumerate(sched))[s0:]]
                bracket = [model.cat_probs(e[:, None], e[None], model.CATS, ties=True)]
            for x in grid:
                m, c, _ = model.season_sim(wins[s0], weeks, bracket, SEATS, sims, s0, x)
                out[x]["made"].append(m); out[x]["title"].append(c)
            truth["made"].append(made); truth["title"].append(champ); truth["monday"].append(np.full(TEAMS, s0))
    y = {k: np.concatenate(v) for k, v in truth.items()}
    early = y["monday"] <= 5
    print(f"\n== {SEASON}: playoff odds, {leagues} leagues x {REGULAR_WEEKS - 1} Mondays, top {SEATS} of {TEAMS}, "
          f"games: {games} ==")
    for x in grid:
        p = {k: np.concatenate(v) for k, v in out[x].items()}
        made, title = score(p["made"], y["made"]), score(p["title"], y["title"])
        print(f"  SEASON_SD {x[0]} WEEK_SD {x[1]}: playoffs logloss {made['logloss']:.4f} (weeks 0-5 "
              f"{score(p['made'][early], y['made'][early])['logloss']:.4f}), title logloss {title['logloss']:.4f}")
        print("    playoff calibration:", calibration(p["made"], y["made"]))
        print("    title calibration:", calibration(p["title"], y["title"], 5))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "replay" in sys.argv:
        replay()
    elif "recency" in sys.argv:
        recency()
    elif "availability" in sys.argv:
        availability()
    elif "playoffs" in sys.argv:
        for games in ("fixed", "schedule"):
            playoffs(games=games, grid=((0.25, 0.6), (0.35, 0.6), (0.45, 0.6), (0.35, 0.5), (0.35, 0.7)))
    elif "gameday" in sys.argv:
        gameday()
    elif "news" in sys.argv:
        news()
    elif "ahead" in sys.argv:
        ahead()
    elif "rates" in sys.argv:
        ratesplit()
    elif "trend" in sys.argv:
        trend()
    elif "bench" in sys.argv:
        bench()
    elif "rookies" in sys.argv:
        rookies()
    elif "yahoo" in sys.argv:
        yahoo_lines()
    elif "minor" in sys.argv:
        minor()
    elif "onoff" in sys.argv:
        teammates(ks=(0, 1, 2, 4, 8, 16))
    elif "teammates" in sys.argv:
        teammates([(s, u) for s in (0.25, 0.37, 0.5) for u in (0, 0.25, 0.5)])
    else:
        for games in ("fixed", "schedule"):
            report(*build(games), f"{SEASON}, games per week: {games}")
