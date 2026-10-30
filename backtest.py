"""Backtest the 9-cat matchup model on a real NBA season, no Yahoo needed:  python backtest.py [2025-26]
python backtest.py replay [2025-26]: rest-of-season error by expert-projection weight instead (fits PROJ_W).
python backtest.py teammates [2025-26]: next week's error with and without teammates' injuries (fits SHARE, USAGE).
python backtest.py availability: each Monday's games-played guess vs the real share, 2024-25 and 2025-26 (fits AVAIL_*).
python backtest.py playoffs [2025-26]: each Monday's playoff and title odds vs what happened (fits SEASON_SD, WEEK_SD).

Drafts 20 simulated 12-team leagues from preseason projections. Each week the model predicts every matchup from what
it would have known that Monday (last season blended with games so far, availability from games played, regulars who
sat their team's last game out until a rough guess at their return, as the app does with ESPN's dates), then the
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

ARGS = [a for a in sys.argv[1:] if a not in ("replay", "teammates", "availability", "playoffs")]
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
    cur = per_game(past)
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


def sat(g, line, team, w):
    """Regulars who sat their team's last game before week w, as ESPN would list them: {key: the day he played again}
    (Timestamp.max: never)."""
    past, T = g[g.week < w], "TEAM_ABBREVIATION"
    last, played = past.groupby(T).GAME_DATE.max(), set(zip(past.key, past.GAME_DATE))
    back = g[g.week >= w].groupby("key").GAME_DATE.min()
    return {k: back.get(k, pd.Timestamp.max) for k in line.index[line.MIN >= model.REGULAR]
            if team.get(k) in last and (k, last[team[k]]) not in played}


def guess(out, monday, rng):
    """ESPN's return dates for these real ones (sat()): days out from Monday off by a factor e^N(0, RETURN_NOISE)."""
    return {k: r if r == pd.Timestamp.max else
            monday + pd.Timedelta(days=round((r - monday).days * np.exp(RETURN_NOISE * rng.standard_normal())))
            for k, r in out.items()}


def team_weeks(games="fixed", leagues=LEAGUES):
    """Each week w: (w, every simulated team's expected totals, the model's view that Monday, and its real totals).
    Teams are league-major (league l's are l * TEAMS + 0..11). Regulars out that Monday (sat()) sit until a noisy
    return (guess()), as League does with ESPN's dates: this week's games from then on (schedule, like This week), or
    the share of his team's remaining games after it (fixed, like League.avail)."""
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
        for k, back in guess(sat(g, line, team, w), monday, rng).items():
            left = days[team[k]][days[team[k]] >= monday]
            if games != "fixed":
                left = left[left < monday + pd.Timedelta(weeks=1)]
            n[idx[k]] *= (left >= back).mean() if len(left) else 0
        if games != "fixed":  # this week, players who played their team's last game (week 0: everyone) play more of
            past = g[g.week < w]  # it than all season (League.rate_week, WEEK_K)
            last, played = past.groupby("TEAM_ABBREVIATION").GAME_DATE.max(), set(zip(past.key, past.GAME_DATE))
            fresh = np.array([(k, last.get(team[k])) in played for k in keys]) if len(past) else np.ones(len(keys), bool)
            avail = np.where(fresh, 1 - model.WEEK_K * (1 - avail), avail)
        yield w, R @ ((n * avail)[:, None] * line[model.RAW].to_numpy()), R @ actual[w]


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
            line = model.ros(pre, per_game(g[g.week < w])).reindex(left.index)[model.RAW].fillna(0)
            err.append(float(((line[model.RAW] - left[model.RAW]).abs() / left[model.RAW].mean()).mean(axis=1).mean()))
        print(f"  weight {x:<5} week 0 {err[0]:.4f}   weeks 1-6 {np.mean(err[1:7]):.4f}   season {np.mean(err):.4f}")


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
        cur = per_game(past)
        line = model.ros(pre, cur)
        team = past.sort_values("GAME_DATE").groupby("key")[T].last().combine_first(first)
        regs, out = line.index[line.MIN >= model.REGULAR], sat(g, line, team, w)
        yield {"pre": pre, "past": past, "cur": cur, "line": line, "team": team, "regs": regs, "out": out, "nxt": nxt,
               "expert": ex.index}


def lifted(m, deboost=True):
    """Week m's per-game lines with teammates' injuries (League does the same): this season net of the lifts players
    had (model.had()), plus each team's out regulars' lifts (model.lifts(), everyone out at once sitting together, in
    stretches between their returns: model.stretches(); less what the preseason projection counts: model.priced_in())
    over a player's games in each stretch."""
    pool = m["line"][m["line"].index.isin(m["cur"].index) | m["line"].index.isin(m["regs"])]
    line = m["line"]
    if deboost:
        h = model.had(m["past"], m["team"], pool, m["regs"]).reindex(m["cur"].index).dropna()
        full = m["cur"].copy()
        full.loc[h.index, h.columns] = (full.loc[h.index, h.columns] - h).clip(lower=0)
        line = model.ros(m["pre"], full)
    spans = model.stretches(m["out"], m["team"], m["nxt"].GAME_DATE.min())
    lift = model.priced_in(model.lifts(pool, m["team"], [s for _, _, s in spans]), m["cur"].index, m["cur"].GP, m["expert"])
    games = m["nxt"][["key", "GAME_DATE"]].merge(lift.index.to_frame(index=False), on="key")
    games["on"] = [spans[s][0] <= d < spans[s][1] for s, d in zip(games["set"], games.GAME_DATE)]
    share = games.groupby(["key", "by", "set"]).on.mean()
    boost = lift.loc[share.index].mul(share, axis=0).groupby(level="key").sum()
    return line, boost[boost.MIN > 0]


def teammates(grid=((0.37, 0.25),)):
    """Next week's per-game lines without teammates' injuries (off), with them (on: model.lifts net of model.had) and
    with lifts but no had() correction (fwd), vs what players did that week, as in replay(), plus the minutes bias
    (actual - line). Scored on players who played every one of their team's games that week: one who missed some (hurt
    mid-game, back on a minutes limit, in and out of the rotation) averaged 3.6 minutes under his line in the games he
    did play, which availability covers, not the line (2025-26: -1.1 min over everyone, +0.5 over full weeks). 20+:
    projected 20+ minutes, where playing every game is about health rather than teammates. All weeks: the partial ones
    too. Fitted SHARE and USAGE on 2025-26."""
    weeks, cols = list(mondays()), ["MIN"] + model.RAW
    print(f"\n== {SEASON}: next week's lines with teammates' injuries ==")
    for share, usage in grid:
        model.SHARE, model.USAGE = share, usage
        rows = {k: [] for k in ("act", "off", "on", "fwd", "hit", "full")}
        for m in weeks:
            act = per_game(m["nxt"])
            act = act[act.index.isin(m["line"].index)]
            on, boost = lifted(m)
            fwd, _ = lifted(m, deboost=False)
            add = boost.reindex(act.index).fillna(0)
            games = m["team"].reindex(act.index).map(m["nxt"].groupby("TEAM_ABBREVIATION").GAME_ID.nunique())
            rows["act"].append(act[cols]); rows["off"].append(m["line"].loc[act.index, cols])
            rows["on"].append(on.loc[act.index, cols] + add); rows["fwd"].append(fwd.loc[act.index, cols] + add)
            rows["hit"].append(pd.Series(act.index.isin(boost.index), index=act.index))
            rows["full"].append(act.GP >= games.fillna(np.inf))
        act, hit, full = pd.concat(rows["act"]), pd.concat(rows["hit"]).to_numpy(), pd.concat(rows["full"]).to_numpy()
        big = (pd.concat(rows["off"]).MIN >= 20).to_numpy()
        print(f"  SHARE {share} USAGE {usage}")
        for name, sub in (("injured teammate", hit & full), ("  20+ min", hit & full & big), ("everyone", full),
                          ("  20+ min", full & big), ("injured, all weeks", hit)):
            a = act[sub]
            err = lambda p: ((p[sub] - a).abs() / a.mean()).mean()
            out = {v: (err(pd.concat(rows[v])), (a - pd.concat(rows[v])[sub]).MIN.mean()) for v in ("off", "on", "fwd")}
            print(f"    {name:18} n {sub.sum():5}  " + "   ".join(
                f"{v} MIN {e.MIN:.4f} stats {e[model.RAW].mean():.4f} bias {b:+.2f}" for v, (e, b) in out.items()))


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


def playoffs(grid=((0, 0), (0.35, 0), (0, 0.6), (0.25, 0.6), (0.35, 0.6), (0.45, 0.6)), leagues=60, sims=1000):
    """Each Monday's playoff and title odds (model.season_sim on the model's view that Monday, with each grid's
    (SEASON_SD, WEEK_SD)) vs what happened: the real box scores played out over a round-robin schedule, top SEATS in, a
    reseeded bracket with ties to the higher seed. Log loss and calibration, pooled over Mondays 0-18 and early (0-5).
    Fitted SEASON_SD and WEEK_SD on 2025-26 (Oct 28). Title odds are hard to judge: each league has one real champion."""
    E, A = {}, {}
    for w, exp, act in team_weeks("fixed", leagues):
        E[w], A[w] = exp, act
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
            weeks = [(I, J, *model.cat_probs(len(SPANS[s]) * e[I], len(SPANS[s]) * e[J], model.CATS, ties=True))
                     for s, (I, J) in list(enumerate(sched))[s0:]]
            bracket = [model.cat_probs(e[:, None], e[None], model.CATS, ties=True)]
            for x in grid:
                m, c, _ = model.season_sim(wins[s0], weeks, bracket, SEATS, sims, s0, x)
                out[x]["made"].append(m); out[x]["title"].append(c)
            truth["made"].append(made); truth["title"].append(champ); truth["monday"].append(np.full(TEAMS, s0))
    y = {k: np.concatenate(v) for k, v in truth.items()}
    early = y["monday"] <= 5
    print(f"\n== {SEASON}: playoff odds, {leagues} leagues x {REGULAR_WEEKS - 1} Mondays, top {SEATS} of {TEAMS} ==")
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
    elif "availability" in sys.argv:
        availability()
    elif "playoffs" in sys.argv:
        playoffs()
    elif "teammates" in sys.argv:
        teammates([(s, u) for s in (0.25, 0.37, 0.5) for u in (0, 0.25, 0.5)])
    else:
        for games in ("fixed", "schedule"):
            report(*build(games), f"{SEASON}, games per week: {games}")
