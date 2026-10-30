"""python test_model.py  (synthetic data, no network)"""
import numpy as np
import pandas as pd

import model
import yahoo


def line(name, i, gp=60, **over):
    # player i gets better at everything as i grows
    row = {"PLAYER_NAME": name, "TEAM_ABBREVIATION": "XXX", "GP": gp, "MIN": 10 + i * 0.12, "FGM": 2 + i * 0.03,
           "FGA": 5 + i * 0.05, "FTM": 1 + i * 0.02, "FTA": 1.5 + i * 0.02, "FG3M": i * 0.01, "PTS": 5 + i * 0.1,
           "REB": 2 + i * 0.03, "AST": 1 + i * 0.03, "STL": 0.5 + i * 0.004, "BLK": 0.3 + i * 0.004, "TOV": 1 + i * 0.005}
    return {**row, **over}


def league():
    base = [line(f"Player {i}", i) for i in range(200)] + [line("Nikola Jokić", 199)]
    cur = [line(f"Player {i}", i, gp=5) for i in range(200)]
    cur[150] = line("Player 150", 150, gp=5, PTS=40)  # red-hot start
    cur.append(line("Rookie Guy", 199, gp=3))          # no baseline, 3 great games
    base, cur = model.keyed(pd.DataFrame(base)), model.keyed(pd.DataFrame(cur))
    as_pl = lambda n: {"name": n, "pos": "", "status": ""}
    teams = {"Me": [as_pl(f"Player {i}") for i in range(150, 200, 4)],
             "Them": [as_pl(f"Player {i}") for i in range(151, 200, 4)]}
    snap = {"me": "Me", "teams": teams, "free_agents": [as_pl(f"Player {i}") for i in range(100, 140)],
            "settings": {"teams": 2, "roster": 13, "mode": "9cat", "cats": model.CATS}}
    return model.League(snap, base, cur), base, cur


def test_all():
    lg, base, cur = league()
    assert lg.find("nikola jokic") == "nikola jokic" and lg.p.at["nikola jokic", "PLAYER_NAME"] == "Nikola Jokić"
    assert lg.find("Player 19 jr") == "player 19"
    assert lg.find("Jokic") == "nikola jokic"

    # ROS: 5 games of 40 PTS barely moves a 20-PTS baseline (k=20 -> 20% weight); rookies start near fringe level
    ros = model.ros(base, cur)
    assert abs(ros.at["player 150", "PTS"] - (0.2 * 40 + 0.8 * base.at["player 150", "PTS"])) < 1e-9
    assert ros.at["rookie guy", "PTS"] < base.at["player 199", "PTS"]
    # experts(): Yahoo's line where it has him, but one under half of ESPN's isn't a per-game line (Oct 30: Jaxson
    # Hayes 1.7 points while playing 27 minutes a game), so ESPN's wins
    yl = {"Player 100": {"PTS": 1.7, "REB": 1.0, "AST": 0.2}, "Player 101": {"PTS": 9.0, "REB": 4.0, "AST": 1.0}}
    el = {n: {"MIN": 20.0, "PTS": 8.0, "REB": 5.0, "AST": 1.0} for n in yl}
    ex = model.experts(yl, el, base, base.index)
    assert ex.at["player 100", "src"] == "ESPN" and ex.at["player 101", "src"] == "Yahoo"

    # trades: upgrade accepted, downgrade declined, 2 scrubs for a star declined (replacement adjustment)
    assert lg.trade(["Player 150"], ["Player 199"])["verdict"] == "accept"
    assert lg.trade(["Player 198"], ["Player 151"])["verdict"] == "decline"
    for bad in ([["Player 199"], ["Player 151"]], [["Player 150"], ["Player 154"]], [["Player 150"], ["Player 120"]]):
        try:
            lg.trade(*bad)
            raise AssertionError(f"accepted impossible trade {bad}")
        except KeyError:
            pass
    assert lg.trade(["Player 198"], ["Player 151", "Player 155"])["verdict"] == "decline"

    # what they want: every player's worth to them (win % with him minus with the best free agent in his spot), your
    # best first, who they'd drop; one yardstick both sides, so a 1-for-1's gain for them is about the difference
    w = lg.wants("Them")
    worth = [x["worth"] for x in w["give"]]
    assert worth == sorted(worth, reverse=True) and w["give"][0]["name"] == "Player 198" and worth[0] > 0
    assert all(len(x["they_drop"]) == 1 for x in w["give"])  # a full roster drops one for him
    assert set(w["keep"]) == set(lg.names(lg.rosters["Them"])) and w["keep"]["Player 199"] == max(w["keep"].values())
    # (their side only: trade() also counts you weaker as their opponent, all of it in a 2-team league, 1/11 in 12)
    mine, now = {x["name"]: x["worth"] for x in w["give"]}, lg.outlooks["Them"]["win"]
    for a, b in (("Player 198", "Player 151"), ("Player 174", "Player 175"), ("Player 150", "Player 199")):
        tk, _, _, til = lg._after("Them", [lg.find(b)], [lg.find(a)])
        swap = 100 * (lg.outlook("Them", {"Them": lg.vec(tk, til)})["win"] - now)
        assert abs(swap - (mine[a] - w["keep"][b])) < 0.5, (a, b, swap)  # about: odds aren't additive
    try:
        lg.wants("Me")
        raise AssertionError("wants accepted your own team")
    except KeyError:
        pass

    # punting a category removes it from value
    full, punted = lg.value(["player 190"]).iloc[0], lg.value(["player 190"], punt=["PTS"]).iloc[0]
    assert abs((full - punted) - lg.z.at["player 190", "PTS"]) < 1e-9

    # hot start shows up as sell-high for the owner; perceived value is above ROS value
    sells = [p["name"] for p in lg.movers()["sell_high"]]
    assert "Player 150" in sells and all(p["gap"] >= model.TREND for p in lg.movers()["sell_high"])
    assert lg.player("player 150")["trend"] == "up" and lg.movers()["window"] == "this season"  # no box scores here
    # buy low: any rank above the waiver level on other teams; pickups: hot free agents
    cur2 = cur.copy()
    cur2.loc["player 151", "PTS"], cur2.loc["player 139", "PTS"] = 0, 40  # Them's worst player cold, a free agent hot
    lg2 = model.League(lg.snap, base, cur2)
    mv, floor = lg2.movers(), lg2.replacement()[0]
    assert "Player 151" in [p["name"] for p in mv["buy_low"]] and "Player 139" in [p["name"] for p in mv["pickups"]]
    assert all(p["team"] not in (lg2.me, "FA") and p["value"] > floor and p["gap"] <= -model.TREND for p in mv["buy_low"])
    assert all(p["team"] == "FA" and p["form"] > floor and p["gap"] >= model.TREND for p in mv["pickups"])
    # ... unless he's out for over a tenth of the games left (Oct 30: Champagnie, out till Feb 25, was listed)
    days = [str(model.date(2026, 10, 26) + model.timedelta(3 * i)) for i in range(55)]
    hurt = lambda back: model.League(lg.snap, base, cur2, today=model.date(2026, 10, 26), sched={"XXX": days},
                                     inj={"Player 139": {"status": "O", "note": "", "return": back}}).movers()["pickups"]
    assert "Player 139" not in [p["name"] for p in hurt("2027-02-25")] and "Player 139" in [p["name"] for p in hurt("2026-10-27")]

    # matchup math: symmetric, identical teams are a coin flip, better players raise your odds
    A, B = lg.vecs["Me"], lg.vecs["Them"]
    p_ab, p_ba = model.match_prob(model.cat_probs(A, B, model.CATS)), model.match_prob(model.cat_probs(B, A, model.CATS))
    assert abs(p_ab + p_ba - 1) < 1e-6
    assert abs(model.match_prob(model.cat_probs(A, A, model.CATS)) - 0.5) < 1e-9
    assert abs(model.phi(np.array(1.96)) - 0.975) < 1e-3
    upgraded = A - lg.vec(["player 150"]) + lg.vec(["player 199"])
    assert model.match_prob(model.cat_probs(upgraded, B, model.CATS)) > p_ab
    # the better roster ("Them" holds the odd-numbered, slightly better players) is favoured and ranked first
    st = lg.standings()
    assert st["Them"]["rank"] == 1 and st["Them"]["win"] > 0.5 > st["Me"]["win"]
    # scoring: a categories league (the default) scores a week by the share of categories won, a one-win league by
    # the chance to win the matchup, which moves more per trade (its cutoffs are scaled by CAT_SCALE)
    o = lg.outlook("Me")
    assert lg.scoring == "categories" and abs(o["win"] - np.mean(list(o["odds"].values()))) < 1e-9
    ow = model.League({**lg.snap, "settings": {**lg.snap["settings"], "scoring": "one_win"}}, base, cur)
    assert ow.s == 1 and lg.s == model.CAT_SCALE
    assert ow.standings()["Me"]["win"] == round(float(p_ab), 3) != st["Me"]["win"]
    assert ow.trade(["Player 150"], ["Player 199"])["net"] > lg.trade(["Player 150"], ["Player 199"])["net"] > 0

    # trade verdicts come from the matchup model in 9-cat and update both rosters
    r = lg.trade(["Player 150"], ["Player 199"])
    assert r["unit"] == "win%" and r["win_after"] > r["win"] and r["their_win_after"] < r["their_win"]
    r = lg.trade(["Player 198"], ["Player 151", "Player 155"])
    assert len(r["you_drop"]) == 1 and len(r["they_add"]) == 1
    # breakdown: both sides per category, every opponent, both rosters with moves marked, flags
    assert set(r["cats_detail"]) == set(model.CATS) and 30 < r["cats_detail"]["FG%"]["you"]["total"][0] < 70
    assert set(r["vs"]) == {"Them"} and set(r["standings"]) == {"Me", "Them"}
    moves = {p["name"]: p["move"] for p in r["roster_after"]["you"]}
    assert moves[r["you_drop"][0]] == "drop" and "Player 198" not in moves
    assert {p["name"] for p in r["roster_after"]["them"] if p["move"] == "add"} == set(r["they_add"])
    assert any("only 5 games" in f for f in r["flags"])  # synthetic season is 5 games old
    bad = lg.trade(["Player 198"], ["Player 151"])
    assert bad["standings"]["Them"]["rival"] and any("trade partner" in f for f in bad["flags"])

    # injuries: an "INJ" tag lowers a player's expected weekly contribution
    lg.meta["player 199"] = {**lg.meta["player 199"], "status": "INJ"}
    assert lg._availability(base, cur)[1]["player 199"] < lg.avail["player 199"]
    # ...if he sat his team's last game. Played it: the tag cuts nothing (M5, backtest.py availability)
    day = lambda d, *ks: pd.DataFrame({"key": list(ks), "TEAM_ABBREVIATION": "XXX", "GAME_DATE": pd.Timestamp(d)})
    lg.logs = pd.concat([day("2026-10-26", "player 199"), day("2026-10-27", "player 198")])
    assert lg._availability(base, cur)[1]["player 199"] < lg.avail["player 199"]
    lg.logs = day("2026-10-27", "player 199")
    tagged = lg._availability(base, cur)
    lg.meta["player 199"]["status"] = ""
    assert tagged[1]["player 199"] == lg._availability(base, cur)[1]["player 199"]
    lg.meta["player 199"]["status"] = "INJ"
    # ... of his team's games in the box scores, not the most anyone has played (Oct 30: PHX had 3 when others had
    # 6), and this week he misses WEEK_K of what he misses all season, having played his team's last game; a player
    # who sat it keeps his rest-of-season rate
    rate, _, _, week = tagged
    assert rate["player 199"] == model.availability(base.GP[["player 199"]], cur.GP[["player 199"]], 1)["player 199"]
    assert abs(week["player 199"] - (1 - model.WEEK_K * (1 - rate["player 199"]))) < 1e-12 and week["player 198"] == rate["player 198"]
    lg.logs = None
    # availability (N9): one lost season isn't fragility (the baseline keeps his better season: 58, not 5), a repeat
    # absentee (41 at best) stays below him, this season's games count, and a rookie starts from 85%
    kess = model.baseline(*(model.keyed(pd.DataFrame([line("Kess", 100, gp=n)])) for n in (5, 58)))
    a = model.availability(pd.Series([kess.GP["kess"], 41, np.nan]), pd.Series([3, 3, 3]), 3)
    assert kess.GP["kess"] == 58 and a[0] > a[1] and a[2] == (3 + model.AVAIL_K * 0.85) / (3 + model.AVAIL_K)
    assert model.availability(pd.Series([82]), pd.Series([20]), 20)[0] > model.availability(pd.Series([82]), pd.Series([10]), 20)[0]

    # finder honours its own rules
    for idea in lg.ideas("fair"):
        d = idea["market"]["diff_pct"]
        assert idea["net"] >= lg.s - 0.05 and abs(d) <= 15
    for idea in lg.ideas("finesse"):
        assert idea["net"] >= 1.5 * lg.s - 0.05 and idea["market"]["diff_pct"] <= model.EVEN

    # tiers: your gain, theirs (win %) and the market gap (% you gain on market). Fair is the market within 15% either
    # way; hard (you win on market) also takes a gap up to 30% your way when both teams gain (Martin, Oct 26; in fair,
    # both ways, until Oct 28). No tier overpays over 30%; max_overpay only tightens
    s = lg.s
    assert lg._tier("fair", 2 * s, -5 * s, -14) and not lg._tier("fair", 2 * s, -5 * s, -20)
    assert not lg._tier("fair", 2 * s, 0.1, 25) and not lg._tier("fair", 2 * s, 0.1, -25)
    assert lg._tier("hard", 2 * s, 0.1, 25) and not lg._tier("hard", 2 * s, -0.1, 25) and not lg._tier("hard", 2 * s, 0.1, 35)
    assert not lg._tier("hard", 0.5 * s, 0.1, 25) and lg._tier("easy", 2 * s, 0.1, -25)
    assert not lg._tier("fair", 2 * s, 0.1, -12, max_overpay=10) and lg._tier("fair", 2 * s, 0.1, -8, max_overpay=10)
    assert lg._tier("easy", s, 0, -20) and not lg._tier("easy", s, 0, 0)  # easy: they win on market
    assert lg._tier("hard", 4 * s, -5 * s, 60) and not lg._tier("hard", 4 * s, 0, 0)  # hard: you do
    assert lg._tier("finesse", 2 * s, -2 * s, 0) and not lg._tier("finesse", 2 * s, -2 * s, 10)
    assert not any(lg._tier(t, 9, 9, -31) for t in model.TIERS)
    assert model.accept(-10, 0) == "Likely yes" and model.accept(25, 0.1) == "Maybe" and model.accept(25, -0.1) == "Long shot"
    assert model.priced(100, 65)["verdict"] == "you overpay 35%" and model.priced(65, 100)["diff_pct"] == 35
    assert model.priced(50, 52)["verdict"] == "even" and model.priced(0, 0)["diff_pct"] == 0

    # market: Yahoo's preseason rank blended with its current one by GP / (GP + CHASE) (5 games: a fifth) as a geometric
    # mean, re-ranked; players Yahoo didn't rank take the model's. Value = 100 x MARKET^(rank - 1). Oct 28's real case:
    # a cold-starting Brunson (Pre 27, Cur 217) stays ahead of a hot Paul George (Pre 75, Cur 23): 41 vs 59. Averaging
    # ranks at HYPE's weight put him behind (90 vs 58)
    rk = lambda ps, name, pre, now: [{**p, "rank": {"pre": pre, "cur": now}} if p["name"] == name else p for p in ps]
    mk = model.League({**lg.snap, "teams": {"Me": rk(lg.snap["teams"]["Me"], "Player 170", 27, 217),
                                            "Them": rk(lg.snap["teams"]["Them"], "Player 171", 75, 23)}}, base, cur)
    m = mk.market
    assert m.at["player 170", "yahoo"] and not m.at["player 174", "yahoo"] and mk.player("player 174")["mkt_model"]
    assert m.at["player 170", "rank"] < m.at["player 171", "rank"]
    assert sorted(m["rank"]) == list(range(1, len(m) + 1)) and np.allclose(m.value, 100 * model.MARKET ** (m["rank"] - 1))
    # ... and a player outside the league (no game this season, no injury listing, not in the snapshot) ranks after
    # everyone in it, however good his line: Jokić here (Oct 30: Westbrook #170 and Chris Paul #164 pushed Yahoo's down)
    assert m.at["nikola jokic", "rank"] == len(m) and "nikola jokic" not in mk.here
    # drop cost: the side taking more players gives up its drops' market value; the other's open spot is worth the
    # waiver level (spot), whoever fills it: a free agent is free. Players traded together count as a package
    r = lg.trade(["Player 198"], ["Player 151", "Player 155"])
    mv = lambda ns: sum(lg.market.at[lg.find(n), "value"] for n in ns)
    pk = lambda ns: model.package(lg.market.at[lg.find(n), "value"] for n in ns)
    assert abs(r["market"]["get"] - (pk(["Player 151", "Player 155"]) - mv(r["you_drop"]))) < 0.1
    assert r["they_add"] and abs(r["market"]["give"] - (mv(["Player 198"]) + lg.spot)) < 0.1
    # consolidation (Martin, Oct 30): a package counts its best player in full and each next one at PACKAGE times the
    # one before, whatever order it comes in, so three players for one cost more than their market sum, a 1-for-1 is
    # priced as before, and the side sending more players looks worse on the market than on the plain sum
    assert model.package([10, 40, 20]) == 40 + 20 * model.PACKAGE + 10 * model.PACKAGE ** 2 and model.package([7]) == 7
    three = (["Player 198", "Player 194", "Player 190"], ["Player 199"])
    r = lg.trade(*three)
    assert abs(r["market"]["give"] - (pk(three[0]) - mv(r["they_drop"]))) < 0.1
    assert any(f.startswith("3-for-1: the market counts the 3 players you send as") for f in r["flags"])
    model.PACKAGE, package = 1.0, model.PACKAGE
    plain = lg.trade(*three)["market"]
    model.PACKAGE = package
    assert plain["give"] > r["market"]["give"] and plain["diff_pct"] < r["market"]["diff_pct"]
    assert abs(lg.trade(["Player 198"], ["Player 199"])["market"]["give"] - mv(["Player 198"])) < 0.06
    assert abs(lg.spot - lg.market["value"].reindex(lg._waiver_level(lg.value())).mean()) < 1e-9
    assert any(f.startswith("You overpay on the market") for f in lg.trade(["Player 198"], ["Player 151"])["flags"])
    # a player named twice counts once (AI tool input comes through _check)
    assert lg.trade(["Player 198", "player 198"], ["Player 151"])["market"] == lg.trade(["Player 198"], ["Player 151"])["market"]
    assert any(f.startswith("Lowball") for f in lg.trade(["Player 150"], ["Player 199"])["flags"])
    # after_trade: the league as trade() scores it, the drop back on waivers and the pickup off them
    deal = (["Player 198"], ["Player 151", "Player 155"])
    r, at = lg.trade(*deal, free=False), lg.after_trade(*deal)
    assert round(100 * at.outlook("Me")["win"], 1) == r["win_after"] and at.trades[0]["market"] == r["market"]
    assert round(100 * at.outlook("Them")["win"], 1) == r["their_win_after"] and lg.trades == []
    assert lg.find(r["you_drop"][0]) in at.fa and lg.find(r["they_add"][0]) in at.rosters["Them"]
    assert lg.find(r["they_add"][0]) not in at.fa and "player 198" in at.rosters["Them"]
    assert lg.names(at.trades[0]["drop"]) == r["you_drop"] and at.trades[0]["add"] == []

    # search: this 2-team league is zero-sum with z-scores spread +-46, so the real tiers find little; the mechanics
    # (pins always in, locks never in, 3-player sides, filters) run on a loose test-only tier
    model.TIERS["hard"], hard = (0.5, -20, -np.inf, np.inf), model.TIERS["hard"]
    hits = lg.search(get=["Player 199"], lock=["Player 198"], tier="hard", limit=20000)
    assert hits and all("Player 199" in x["get"] and "Player 198" not in x["give"] for x in hits)
    assert any(len(x["give"]) == 3 for x in hits)
    # role players only (Q14): nobody in the market's top 10 on either side, except a pinned player; with everyone
    # on your side locked there's nothing to search
    top = set(lg.names(lg.market.index[lg.market["rank"] <= 10]))
    role = lg.search(get=["Player 199"], tier="hard", limit=200, outside=10)
    assert lg.search(get=["Player 199"], tier="hard", outside=60) == []
    assert "Player 199" in top and role and all(not (set(x["give"]) | set(x["get"]) - {"Player 199"}) & top for x in role)
    assert any(top & set(x["give"]) for x in hits)  # without it, top players are offered
    # search prices a deal like trade() does (same drops and pickups), 3-for-1s included
    for x in [h for h in hits if len(h["give"]) == 3][:3] + hits[:3]:
        assert lg.trade(x["give"], x["get"])["market"] == x["market"], x
    shop = lg.search(give=["Player 150"], get=["Player 199"], tier="hard")
    assert shop and all("Player 150" in x["give"] for x in shop)
    cons = lg.search(get=["Player 199"], tier="hard", shape="consolidate")
    assert cons and all(len(x["give"]) > len(x["get"]) for x in cons)
    assert all(x["score"] > 0 for x in lg.search(get=["Player 199"], goal="cats", goal_cats=["STL"], tier="hard"))
    # a punted goal category is measured on real odds, like an unpunted one (its conceded 0% read +30 for every deal)
    goal = lambda p: [x["score"] for x in lg.search(["Player 198"], ["Player 199"], size=1, goal="cats", goal_cats=["STL"],
                                                    punt=p, tier="hard", free=False)]
    model.TIERS["hard"] = (-99, -99, -np.inf, np.inf)  # every deal
    assert goal([]) and goal(["STL"]) == goal([])
    model.TIERS["hard"] = (0.5, -20, -np.inf, np.inf)
    for bad in ({"get": ["Player 150"]}, {"get": ["Player 199"], "team": "Me"}, {"tier": "nope"}, {"goal": "team"}):
        try:
            lg.search(**bad)
            raise AssertionError(f"accepted bad search {bad}")
        except KeyError:
            pass
    model.TIERS["hard"] = hard

    # "looks to them": nobody looks worse than the waiver level, and taking back extra players costs a team the
    # perceived value of its own weakest (the ones it would drop), not a free agent's
    cv, pv = lg.perceived()
    assert abs(pv.min() - cv) < 1e-9 and (pv >= cv - 1e-9).all()
    them_low = pv.reindex(lg.rosters["Them"]).nsmallest(2).sum()
    r = lg.trade(["Player 198", "Player 194", "Player 190"], ["Player 199"])  # Them takes back 2 extra players
    expect = pv[["player 198", "player 194", "player 190"]].sum() - pv["player 199"] - them_low
    assert abs(r["their_view"] - expect) < 0.01, (r["their_view"], expect)

    # uneven trades drop whoever costs the least win %, two at a time if needed
    r = lg.trade(["Player 198"], ["Player 151", "Player 155", "Player 159"])
    keys = [k for k in lg.rosters["Me"] if k != "player 198"] + ["player 151", "player 155", "player 159"]
    c = lg._contrib("Me", keys)
    assert r["you_drop"] == lg.names(sorted(keys, key=c.get)[:2])

    # free moves: trades are scored on top of them. A 2-for-1 whose only gain is the open spot (Twin Y = Player 170,
    # Scrub A = Scrub B) scores 0 once the star free agent is already in (the free move drops Scrub B, who comes back
    # for the open spot), not the star's worth. Search never offers the pickup; a short roster gets a plain add
    extra = [line("Star FA", 195), line("Twin Y", 170), line("Scrub A", 145), line("Scrub B", 145)]
    as_pl = lambda n: {"name": n, "pos": "", "status": ""}
    fl = model.League({**lg.snap, "teams": {"Me": lg.snap["teams"]["Me"] + [as_pl("Scrub A"), as_pl("Scrub B")],
                                            "Them": lg.snap["teams"]["Them"] + [as_pl("Twin Y")]},
                       "free_agents": lg.snap["free_agents"] + [as_pl("Star FA")]},
                      pd.concat([base, model.keyed(pd.DataFrame(extra))]),
                      pd.concat([cur, model.keyed(pd.DataFrame([{**x, "GP": 5} for x in extra]))]))
    deal = (["Player 170", "Scrub A"], ["Twin Y"])
    r0, r = fl.trade(*deal, free=False), fl.trade(*deal)
    assert r0["you_add"] == ["Star FA"] and r0["net"] > 1
    assert r["free"] == [{"add": "Star FA", "drop": "Scrub B", "gain": r0["net"]}] and r["you_add"] == ["Scrub B"]
    assert abs(r["net"]) < 0.05 and fl.waivers()["free"][0]["add"] == "Star FA"
    assert not any("Star FA" in x["give"] for x in fl.search(tier="hard", limit=200))
    # Waivers' Healthy only: injured, he's off waivers on that copy (so no free move), still on the league's
    fl.meta["star fa"] = {**fl.meta["star fa"], "status": "O"}
    assert "star fa" in fl.fa and all(m["add"] != "Star FA" for m in fl.healthy_fa().waivers()["free"])
    fl.meta["star fa"]["status"] = ""
    # an NA free agent isn't part of the waiver level (replacement, FILL)
    na = model.League({**lg.snap, "free_agents": [{**f, "status": "NA" if f["name"] == "Player 139" else ""}
                                                  for f in lg.snap["free_agents"]]}, base, cur)
    assert "player 139" in lg._waiver_level(lg.value()) and "player 139" not in na._waiver_level(na.value())
    assert na.replacement()[0] < lg.replacement()[0]
    short = model.League({**lg.snap, "settings": {**lg.snap["settings"], "roster": 14}}, base, cur)
    assert [d for _, d, _ in short.free_moves()] == [None] and short.waivers()["moves"][0]["drop"] is None
    # the other team is judged on the trade alone unless their_free: then it fills its own open spot first
    assert short.trade(["Player 198"], ["Player 151"])["their_free"] == []
    assert [m["drop"] for m in short.trade(["Player 198"], ["Player 151"], their_free=True)["their_free"]] == [None]
    assert [m["drop"] for m in short.trade(["Player 198"], ["Player 151"], free=False, their_free=True)["their_free"]] == [None]
    assert short.search(tier="hard", their_free=True, limit=200) == short.search(team="Them", tier="hard",
                                                                                 their_free=True, limit=200)[:3]

    # an open active spot takes an extra player without a drop (Birds had one on Oct 27, when they took Chet Holmgren +
    # Paul Reed for Tatum); a spot the trade opens gets a pickup, one it already had stays open; search agrees
    op = model.League({**lg.snap, "teams": {**lg.snap["teams"], "Them": lg.snap["teams"]["Them"][:-1]}}, base, cur)
    assert op._room("Them") == 1 and op._room("Me") == 0
    r = op.trade(["Player 194", "Player 190"], ["Player 151"], free=False)
    assert r["they_drop"] == [] and r["they_add"] == [] and len(r["you_add"]) == 1
    r1 = op.trade(["Player 194"], ["Player 195", "Player 191"], free=False)
    assert len(r1["you_drop"]) == 1 and len(r1["they_add"]) == 1  # they had one open, the trade opens another
    model.TIERS["hard"] = (-99, -99, -np.inf, np.inf)  # every deal
    for r, deal in ((r, (["Player 194", "Player 190"], ["Player 151"])), (r1, (["Player 194"], ["Player 195", "Player 191"]))):
        x = [y for y in op.search(*deal, size=2, tier="hard", free=False, limit=500)
             if sorted(y["give"]) == sorted(deal[0]) and sorted(y["get"]) == sorted(deal[1])]
        assert [(x[0]["net"], x[0]["their_net"], x[0]["market"])] == [(r["net"], r["their_net"], r["market"])], (x, r)
    # with a punt, a spot your trade opens gets your build's best free agent (as free_moves() picks), and search agrees
    blk = model.keyed(pd.DataFrame([line("Block Guy", 130, BLK=3.0)]))  # the best free agent, until BLK is punted
    bl = model.League({**lg.snap, "free_agents": lg.snap["free_agents"] + [{"name": "Block Guy", "pos": "", "status": ""}]},
                      pd.concat([base, blk]), pd.concat([cur, blk.assign(GP=5)]))
    P, deal = ["BLK"], (["Player 194", "Player 190"], ["Player 151"])
    assert bl.names(bl.best_fa(1)) == ["Block Guy"] != bl.names(bl.best_fa(1, punt=P))
    r = bl.trade(*deal, punt=P, free=False)
    assert r["you_add"] == bl.names(bl.best_fa(1, punt=P)) and bl.trade(*deal, free=False)["you_add"] == ["Block Guy"]
    x = [y for y in bl.search(*deal, size=2, tier="hard", punt=P, free=False, limit=500)
         if sorted(y["give"]) == sorted(deal[0]) and y["get"] == deal[1]]
    assert (x[0]["net"], x[0]["their_net"], x[0]["market"]) == (r["net"], r["their_net"], r["market"]), (x, r)
    # a deal never has a player the side taking him would cut on arrival: it's the same deal without him, plus a
    # roster spot for the side sending him. trade() says so
    xs = lg.search(get=["Player 195"], size=3, tier="hard", free=False, limit=5000)
    assert any(len(x["get"]) > len(x["give"]) for x in xs) and any(len(x["get"]) < len(x["give"]) for x in xs)
    for x in xs[:40] + [x for x in xs if len(x["get"]) != len(x["give"])][:40]:
        r = lg.trade(x["give"], x["get"], free=False)
        assert not set(r["you_drop"]) & set(x["get"]) and not set(r["they_drop"]) & set(x["give"]), x
    junk = (["Player 150"], ["Player 151", "Player 155"])  # Them's two worst: you'd cut one at once
    found = lg.search(*junk, tier="hard", free=False, limit=500)
    assert found and not [x for x in found if x["give"] == junk[0] and sorted(x["get"]) == junk[1]]
    r = lg.trade(*junk, free=False)
    assert set(r["you_drop"]) & set(junk[1]) and any(f.startswith("You'd drop Player 151") for f in r["flags"])
    model.TIERS["hard"] = hard

    # IL slots (N5): a parked player holds no active spot and nobody covers his missed games, so he counts for less
    # than an active one. Sending him frees no spot (the player coming back costs a drop), an injured player coming in
    # parks in a free slot (an open spot: a pickup), and search scores both deals like trade()
    hurt = model.keyed(pd.DataFrame([line("Hurt Guy", 196), line("Sore Guy", 193)]))
    ill = model.League({**lg.snap, "settings": {**lg.snap["settings"], "il": 2}, "teams": {
        "Me": lg.snap["teams"]["Me"] + [{"name": "Hurt Guy", "pos": "", "status": "O", "slot": "IL"}],
        "Them": lg.snap["teams"]["Them"] + [{"name": "Sore Guy", "pos": "", "status": "O", "slot": "BN"}]}},
        pd.concat([base, hurt]), pd.concat([cur, hurt.assign(GP=5)]))
    pts = model.RAW.index("PTS")
    assert ill.il == {"Me": {"hurt guy"}, "Them": set()} and ill.rows_il["hurt guy"][pts] < ill.rows["hurt guy"][pts]
    sent, got = ill.trade(["Hurt Guy"], ["Player 195"], free=False), ill.trade(["Player 150"], ["Sore Guy"], free=False)
    assert len(sent["you_drop"]) == 1 and sent["you_drop"] != ["Player 195"] and sent["you_add"] == [] and len(sent["they_add"]) == 1
    assert got["you_drop"] == [] and len(got["you_add"]) == 1
    model.TIERS["hard"] = (-99, -99, -np.inf, np.inf)  # every deal, as above
    for r in (sent, got):
        x = ill.search([p["name"] for p in r["give"]], [p["name"] for p in r["get"]], size=1, tier="hard", free=False)
        assert [(x[0]["net"], x[0]["their_net"], x[0]["market"], x[0]["their_view"])] == [
            (r["net"], r["their_net"], r["market"], r["their_view"])], (x, r)
    # both sides open a spot (each parks the injured player it gets): one free agent can't fill both, so you pick
    # first and they take the next (Oct 30: both got Tobias Harris, and after_trade put him on two rosters)
    both = model.League({**ill.snap, "teams": {**ill.snap["teams"], "Me": lg.snap["teams"]["Me"] + [
        {"name": "Hurt Guy", "pos": "", "status": "O", "slot": "BN"}]}}, pd.concat([base, hurt]), pd.concat([cur, hurt.assign(GP=5)]))
    r = both.trade(["Hurt Guy"], ["Sore Guy"], free=False)
    assert len(r["you_add"]) == len(r["they_add"]) == 1 and r["you_add"] != r["they_add"]
    x = both.search(["Hurt Guy"], ["Sore Guy"], size=1, tier="hard", free=False)
    assert [(x[0]["net"], x[0]["their_net"], x[0]["market"])] == [(r["net"], r["their_net"], r["market"])], (x, r)
    at = both.after_trade(["Hurt Guy"], ["Sore Guy"])
    assert not set(at.rosters["Me"]) & set(at.rosters["Them"]) and not set(at.fa) & set(at.rosters["Them"])
    model.TIERS["hard"] = hard

    # compare (Q3): 2 to 4 different players; each one's card plus your win % with him in one of your roster spots.
    # Your compared players all leave, so "keep A or B" is your roster with A (or B) and the best free agent outside
    # the comparison in the other spot; an outsider who'd be your worst player is cut and leaves it as it is
    for bad in (["Player 150"], ["Player 150", "player 150"], [f"Player {i}" for i in range(150, 170, 4)]):
        try:
            lg.compare(bad)
            raise AssertionError(f"compared {bad}")
        except KeyError:
            pass
    w0, me = lg.outlook("Me")["win"], lg.vecs["Me"]
    c = lg.compare(["Player 150", "Player 198", "Player 139", "Player 101"])
    keep, star, fa, scrub = (x["slot"] for x in c["players"])
    assert c["win"] == round(100 * w0, 1) and [x["team"] for x in c["players"]] == ["Me", "Me", "FA", "FA"]
    assert star["win"] > keep["win"] and c["best"]["slot"] == c["best"]["value"] == ["Player 198"]
    assert keep["add"] == star["add"] == fa["add"] and not {"Player 139", "Player 101"} & set(keep["add"])
    fill = lg.find(keep["add"][0])
    swap = lambda inn: 100 * lg.outlook("Me", {"Me": me - lg.rows["player 150"] - lg.rows["player 198"]
                                                     + lg.rows[fill] + lg.rows[inn]})["win"]
    assert abs(keep["win"] - swap("player 150")) < 0.06 and abs(fa["win"] - swap("player 139")) < 0.06
    assert scrub["cut"] is False  # two of your spots open up: he makes the roster
    out = lg.compare(["Player 199", "Player 101"])["players"]
    assert out[1]["slot"]["cut"] and out[1]["slot"]["win"] == round(100 * w0, 1) and out[0]["slot"]["drop"]
    assert "news" not in out[0] and set(out[0]["lines"]) == {"ros", "season"}
    # parked in an IL slot, he goes back there: the active spot he'd hold is filled from waivers
    h = ill.compare(["Hurt Guy", "Player 150"])["players"][0]["slot"]
    assert h["add"] and not h["cut"]

    # player search (Q6, Trades / Compare / top bar): rostered players (IL-parked too), plus free agents with fa; every
    # name it lists opens that player's card, owned by the team it lists
    for g, fa in ((lg, False), (lg, True), (ill, True)):
        pl = g.pool(fa)
        assert len(pl) == sum(map(len, g.rosters.values())) + fa * len(g.fa) and pl == sorted(pl, key=lambda p: p["name"])
        assert all((c := g.card(p["name"]))["name"] == p["name"] and c["team"] == p["team"] for p in pl)
    assert {"name": "Hurt Guy", "team": "Me"} in ill.pool() and "FA" not in {p["team"] for p in lg.pool()}
    assert "Nikola Jokić" not in {p["name"] for p in lg.pool(True)}  # in the stats, but on no roster or FA list

    # add/drop evaluator (Q7): any free agent for any of your players, scored on today's roster like the Waivers
    # table (not on top of the free moves). The best suggested move scores what Waivers says; no drop on a full roster
    # drops whoever you'd miss least (never the add); an open spot takes a plain add; a parked drop frees no spot; an
    # injured add parks in a free IL slot and a spot the move opens stays open. Only free agents in, only yours out
    top = fl.waivers()["moves"][0]
    mv = fl.add_drop(top["add"], top["drop"])
    assert mv["net"] == top["gain"] and mv["add"]["name"] == "Star FA" and [x["name"] for x in mv["drop"]] == [top["drop"]]
    a, d = fl.find(top["add"]), fl.find(top["drop"])
    assert abs(mv["win_after"] - 100 * fl.outlook("Me", {"Me": fl.vecs["Me"] - fl.rows[d] + fl.rows[a]})["win"]) < 0.06
    assert mv["verdict"] == "accept" and mv["week"] is None and mv["open"] == 0 and not mv["add"]["parks"]
    auto = lg.add_drop("Player 139")
    c = lg._contrib("Me", lg.rosters["Me"])
    assert [(x["name"], x["auto"]) for x in auto["drop"]] == [(lg.names([min(c, key=c.get)])[0], True)]
    assert auto["net"] < 0 and auto["verdict"] == "decline" and set(auto["cats"]) == set(model.CATS)
    plain = short.add_drop("Player 139")
    assert plain["drop"] == [] and plain["open"] == 0 and plain["net"] == short.waivers()["moves"][0]["gain"] > 0
    assert short.add_drop("Player 139", "Player 198")["open"] == 1  # a spot it opens stays open: filling it is an add
    for bad in (("Player 151", None), ("Player 139", "Player 151"), ("Nikola Jokic", None), ("Player 139", "Player 101")):
        try:
            lg.add_drop(*bad)
            raise AssertionError(f"evaluated {bad}")
        except KeyError:
            pass
    sore = model.keyed(pd.DataFrame([line("Sore FA", 190)]))
    ilf = model.League({**ill.snap, "free_agents": ill.snap["free_agents"] + [{"name": "Sore FA", "pos": "", "status": "O"}]},
                       pd.concat([base, hurt, sore]), pd.concat([cur, hurt.assign(GP=5), sore.assign(GP=5)]))
    freed = ilf.add_drop("Player 139", "Hurt Guy")
    assert [(x["name"], x["auto"], x["parked"]) for x in freed["drop"]][0] == ("Hurt Guy", False, True)
    assert len(freed["drop"]) == 2 and freed["drop"][1]["auto"] and not freed["drop"][1]["parked"]
    park = ilf.add_drop("Sore FA")
    assert park["add"]["parks"] and park["drop"] == [] and park["open"] == 0
    assert abs(park["win_after"] - 100 * ilf.outlook("Me", {"Me": ilf.vecs["Me"] + ilf.rows_il["sore fa"]})["win"]) < 0.06
    both = ilf.add_drop("Sore FA", "Player 150")
    assert both["open"] == 1 and both["net"] < park["net"]
    pl = model.League({**lg.snap, "settings": {**lg.snap["settings"], "mode": "points"}}, base, cur).add_drop("Player 139", "Player 150")
    assert pl["unit"] == "value" and pl["week"] is None and pl["net"] < 0

    # schedule: identical twins, one with 4 games left this week and one with 2; a return date wipes this week
    # and cuts rest-of-season availability to the share of games after it
    twins = model.keyed(pd.DataFrame([line(f"Twin {x}", 180) for x in "ABCDEF"]))
    week = ["2026-10-26", "2026-10-28", "2026-10-30", "2026-11-01", "2026-11-03", "2026-11-05"]
    snap = {**lg.snap, "teams": {**lg.snap["teams"], "Me": lg.snap["teams"]["Me"] + [
        {"name": "Twin A", "nba": "AAA"}, {"name": "Twin B", "nba": "BBB"}, {"name": "Twin C", "nba": "AAA"}]},
        "free_agents": lg.snap["free_agents"] + [{"name": "Twin D", "nba": "AAA"}, {"name": "Twin E", "nba": "BBB"},
                                                 {"name": "Twin F", "nba": "AAA", "waiver": "Nov 1"}]}
    sl = model.League(snap, pd.concat([base, twins]), pd.concat([cur, twins]), today=model.date(2026, 10, 26),
                      sched={"AAA": week, "BBB": week[:1] + week[3:], "XXX": week},
                      inj={"Twin C": {"status": "O", "note": "Ankle.", "return": "2026-11-03"}})
    assert sl.games_week["twin a"] == 4 and sl.games_week["twin b"] == 2 and sl.games_week["twin c"] == 0
    # an ESPN return date of today cuts nothing (Oct 30: 14 of 20 players back that day were still cut, Sengun 0.75 -> 0.70)
    sl.meta["twin b"]["status"], sl.inj["twin b"] = "O", {"status": "O", "note": "", "return": sl.today}
    assert sl._availability(pd.concat([base, twins]), pd.concat([cur, twins]))[0]["twin b"] == sl.rate["twin b"]
    sl.meta["twin b"]["status"] = ""
    del sl.inj["twin b"]
    assert sl.week_vec(["twin a"])[model.RAW.index("PTS")] > sl.week_vec(["twin b"])[model.RAW.index("PTS")]
    assert abs(sl.avail["twin c"] / sl.rate["twin c"] - 2 / 6) < 1e-9 and sl.player("twin c")["injury"] == "Ankle."
    # only the best `lineup` players with a game count each day: a third twin adds nothing on days two better ones play
    sl.lineup = 2
    assert sl.starts(["player 198", "twin a", "twin d"]) == {"player 198": 4, "twin a": 4}
    # ... and a bench player fills in on a light day: twin a (a claim) only plays from Oct 30, so twin b starts Monday
    assert sl.starts(["player 198", "twin a", "twin b"], {"twin a": "2026-10-30"}) == {"player 198": 4, "twin a": 2,
                                                                                         "twin b": 1}
    # positions (the snapshot's slots): starters fill the slots best first, moving one between slots to fit another in;
    # a player with no listed position fits anywhere. Twin A is a C behind a better C, so the PG Twin D starts over him
    assert model.lineup(["PG", "G"], [{"PG", "SG"}, {"PG"}]) == {0: 1, 1: 0}
    assert sorted(model.lineup(["PG", "C", "Util"], [{"C"}, {"C"}, {"C"}, {"PG"}]).values()) == [0, 1, 3]
    assert sorted(model.lineup(["F", "C"], [{"PG"}, set(), {"PF"}]).values()) == [1, 2]
    old = dict(sl.meta)
    sl.slots, sl._lineups = ["PG", "C"], {}
    for k, x in (("player 198", "C"), ("twin a", "C"), ("twin d", "PG")):
        sl.meta[k] = {**sl.meta[k], "pos": x}
    assert sl.starts(["player 198", "twin a", "twin d"]) == {"player 198": 4, "twin d": 4}
    assert sl._fill(["player 198"]) == ["PG"] and sl._fill(["player 198", "twin d"]) == []
    sl.slots, sl.meta, sl._lineups = [], old, {}
    sl.lineup = 10
    # a trade that leaves a slot nobody active fits is flagged; one that keeps a C isn't
    ps = league()[0]
    ps.slots, ps.lineup = ["C", "Util"], 2
    for k in ps.rosters["Me"] + ps.rosters["Them"]:
        ps.meta[k] = {**ps.meta[k], "pos": "C" if k == "player 198" else "PG"}
    assert "Leaves 1 of your 2 starting slots with nobody to fill it: you'd need a C." in ps.trade(["Player 198"], ["Player 199"], free=False)["flags"]
    assert not any("starting slots" in f for f in ps.trade(["Player 194"], ["Player 195"], free=False)["flags"])
    # player card: games this matchup week and the next three (Monday-Sunday without a season schedule), after his
    # return date; without box scores, this season's stats line
    c = sl.card("Twin C")
    assert [w["games"] for w in c["weeks"]] == [0, 2, 0, 0] and c["weeks"][1]["start"] == "2026-11-02"
    assert set(c["lines"]) == {"ros", "season"} and c["lines"]["season"]["gp"] == 60 and c["lines"]["ros"]["gp"] is None
    assert c["fit_rank"] >= 1 and c["mkt_rank"] >= 1 and c["team"] == "Me"
    # compare: twin A's 4 games this week beat twin B's 2; level numbers (2 games each next week) name nobody
    b = sl.compare(["Twin A", "Twin B"])["best"]
    assert b["week0"] == ["Twin A"] and "week1" not in b
    # schedule planner (Q4): 15 of your 16 play Monday (Twin C is out until Nov 3), so 5 sit, your worst 5; the
    # week's starts are This week's starts()
    pl = sl.planner()
    w0, play = pl["weeks"][0], sl._playing(sl.rosters["Me"])
    mon = w0["days"][0]
    assert mon["date"] == "2026-10-26" and len(mon["players"]) == 10 and mon["open"] == 0
    assert set(mon["sit"]) == set(sl.names([k for k in sorted(play, key=sl._value.get) if k != "twin c"][:5]))
    assert w0["starts"] == sum(sl.starts(play).values()) and w0["sit"] == w0["games"] - w0["starts"]
    assert [w["start"] for w in pl["weeks"]] == ["2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16"]
    tc = next(r for r in pl["roster"] if r["name"] == "Twin C")
    assert [x["games"] for x in tc["sched"]] == [0, 2, 0, 0]
    # a 15-man lineup: Wednesday and Friday have one open slot (only XXX and AAA play), Tuesday has no NBA games at all.
    # So an AAA streamer starts 2 games this week and a BBB one none, though BBB plays twice too
    sl.lineup = 15
    pl = sl.planner()
    assert [(d["nba"], d["open"]) for d in pl["weeks"][0]["days"]][:5] == [(3, 0), (0, 0), (2, 1), (0, 0), (2, 1)]
    assert pl["weeks"][0]["open"] == 2 and pl["weeks"][0]["sit"] == 0
    t = {x["nba"]: x for x in pl["teams"]}
    assert t["AAA"]["weeks"][:2] == [4, 2] and t["AAA"]["open"][0] == 2 and t["BBB"]["weeks"][0] == 2 and t["BBB"]["open"][0] == 0
    assert t["AAA"]["fa"]["name"] == "Twin D" and set(t["AAA"]["mine"]) == {"Twin A", "Twin C"}
    sl.lineup = 10
    # playoff weeks: the schedule's weeks with no pairings after the last with some, unless settings name them; a
    # player parked in an IL slot sits out the planner, listed with his return date
    po = [{"week": 2, "start": "2026-10-26", "end": "2026-11-01", "matchups": [["Me", "Them"]]},
          {"week": 3, "start": "2026-11-02", "end": "2026-11-08", "matchups": []}]
    teams = {**snap["teams"], "Me": [{**x, "slot": "IL"} if x["name"] == "Twin C" else x for x in snap["teams"]["Me"]]}
    pw = model.League({**snap, "schedule": po, "teams": teams}, pd.concat([base, twins]), pd.concat([cur, twins]),
                      today=model.date(2026, 10, 26), sched=sl.sched, inj={"Twin C": sl.inj["twin c"]})
    pp = pw.planner()
    assert pp["playoffs"] == [{"week": 3, "start": "2026-11-02", "end": "2026-11-08"}] and pp["weeks"][0]["week"] == 2
    assert next(x for x in pp["teams"] if x["nba"] == "AAA")["playoffs"] == [2]
    assert pp["il"] == [{"name": "Twin C", "return": "2026-11-03"}] and "Twin C" not in {r["name"] for r in pp["roster"]}
    assert model.League({**pw.snap, "settings": {**pw.snap["settings"], "playoff_weeks": []}}, pd.concat([base, twins]),
                        pd.concat([cur, twins]), today=model.date(2026, 10, 26), sched=sl.sched).planner()["playoffs"] == []
    assert lg.planner()["weeks"] == []  # no NBA schedule: nothing to plan
    # with box scores: this season, last 14 and last 7 days (Oct 26: since Oct 12 and Oct 19)
    box = pd.DataFrame([{**line("Twin A", 180, PTS=p), "GAME_DATE": pd.Timestamp(d)}
                        for p, d in ((30, "2026-10-24"), (20, "2026-10-20"), (10, "2026-10-15"), (40, "2026-10-01"))])
    sl.logs = box.assign(key=box.PLAYER_NAME.map(model.norm))
    c = sl.card("Twin A")["lines"]
    assert [(w, c[w]["gp"], c[w]["PTS"]) for w in ("season", "last14", "last7")] == [
        ("season", 4, 25), ("last14", 3, 20), ("last7", 2, 25)]
    sl.logs = sl.logs[sl.logs.GAME_DATE > "2026-10-16"]  # the same 2 games in every window: shown once
    assert set(sl.card("Twin A")["lines"]) == {"ros", "season"}
    cl = sl.compare(["Twin A", "Twin B"])["players"]  # ... but compare keeps every window with games, to line them up
    assert set(cl[0]["lines"]) == {"ros", "season", "last30", "last14", "last7"} and set(cl[1]["lines"]) == {"ros"}
    sl.logs = None

    # news (N8): My team's panel asks news() about your players and their NBA teammates (Twin D and E are free agents
    # on AAA and BBB) since the snapshot or two days back, whichever is earlier; newest first, new = after the
    # snapshot. The card shows a player's news; a League without news() (offline) has none.
    now = model.datetime.now(model.timezone.utc)
    iso = lambda h: (now - model.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    asked = []
    def fake(keys, since=""):
        asked.append((set(keys), since))
        return {k: [{"date": iso(h), "headline": k, "story": ""}] for k, h in (("twin a", 3), ("twin d", 0.5)) if k in keys}
    nw = model.League({**snap, "pulled": (now - model.timedelta(hours=1)).isoformat()}, pd.concat([base, twins]),
                      pd.concat([cur, twins]), news=fake)
    assert [(x["name"], x["mine"], x["new"], x["team"]) for x in nw.roster_news()] == [
        ("Twin D", False, True, "FA"), ("Twin A", True, False, "Me")]
    keys, since = asked[0]
    assert {"twin a", "twin b", "twin d", "twin e", "player 150"} <= keys and iso(48.01) < since < iso(47.99)
    assert nw.card("Twin A")["news"][0]["headline"] == "twin a" and "news" not in sl.card("Twin A")
    # news() itself: ESPN's names land on stats keys, a player is fetched again only when the index shows newer news,
    # and only news since `since` comes back (network and data/ swapped out)
    import io, json, tempfile, urllib.request
    from pathlib import Path
    ix, hits, old = {"Twin Á": [7, "2026-10-27T10:00:00Z"], "Nobody": [8, None]}, [], (model.news_index, urllib.request.urlopen, model.NEWS)
    feed = {"feed": [{"type": "Rotowire", "published": "2026-10-27T10:00:00Z", "headline": "Out", "story": "Knee."},
                     {"type": "Rotowire", "published": "2026-10-20T10:00:00Z", "headline": "Old", "story": ""},
                     {"type": "Media", "published": "2026-10-27T11:00:00Z", "headline": "Highlights"}]}
    model.news_index, model.NEWS = lambda: ix, Path(tempfile.mkdtemp()) / "news.json"
    urllib.request.urlopen = lambda url, timeout: hits.append(url) or io.BytesIO(json.dumps(feed).encode())
    try:
        assert model.news(["twin a"], "2026-10-26") == {"twin a": [{"date": "2026-10-27T10:00:00Z", "headline": "Out", "story": "Knee."}]}
        assert len(model.news(["twin a"])["twin a"]) == 2 and len(hits) == 1  # cached
        ix["Twin Á"][1] = "2026-10-28T00:00:00Z"
        assert model.news(["twin a"], "2026-10-28") == {"twin a": []} and len(hits) == 2  # newer news: fetched again
        assert model.news(["twin a"], "2026-10-29") == {} and len(hits) == 2  # nothing that recent: not even asked
    finally:
        model.news_index, urllib.request.urlopen, model.NEWS = old

    # teammates' injuries (N7): while a regular (REGULAR+ minutes) sits, SHARE of his minutes go to NBA teammates in
    # the rotation (10+), by room under ROOM and more to similar roles, at their own rates. USAGE of the shots, points,
    # assists and turnovers those minutes don't replace go to the same players by output, even one with no room
    rates = lambda big: ({"REB": 0.3, "BLK": 0.06, "AST": 0.03, "FG3M": 0.005} if big
                         else {"REB": 0.1, "BLK": 0.005, "AST": 0.2, "FG3M": 0.07})
    mate = lambda n, mins, big=False, t="AAA": line(n, 150, MIN=mins, TEAM_ABBREVIATION=t,
                                                    **{c: r * mins for c, r in rates(big).items()})
    ml = model.keyed(pd.DataFrame([mate("Star", 34), mate("Guard", 20), mate("Big", 20, True), mate("Iron", 38),
                                   mate("Deep", 8), mate("Sixth", 24, True), mate("Other", 20, t="BBB")]))
    team = ml.TEAM_ABBREVIATION.to_dict()
    lf = model.lifts(ml, team, [[k] for k in ml.index]).droplevel("set")  # each one sitting alone
    assert set(lf.index.get_level_values("by")) == {"star", "iron"}  # Sixth (24 min) isn't a regular, Other has no mates
    s = lf.xs("star", level="by")
    assert set(s.index) == {"guard", "big", "iron", "sixth"}  # same team, 10+ minutes
    assert abs(s.MIN.sum() - model.SHARE * 34) < 1e-9 and s.at["guard", "MIN"] > s.at["big", "MIN"]
    assert s.at["iron", "MIN"] == 0 < s.at["iron", "PTS"]
    own = ml.loc[s.index, model.RAW].div(ml.loc[s.index, "MIN"], axis=0).mul(s.MIN, axis=0)  # his minutes, their rates
    assert np.allclose(s.REB, own.REB) and np.allclose(s.STL, own.STL)
    assert abs((s.PTS - own.PTS).sum() - model.USAGE * (ml.at["star", "PTS"] - own.PTS.sum())) < 1e-9
    # sitting together (M4): Star and Iron out at once hand out SHARE of both their minutes to the players left (Iron
    # takes none of Star's, nor his usage), most minutes first, and nobody passes ROOM with both
    both = model.lifts(ml, team, [["star", "iron"]]).droplevel("set")
    got = both.groupby(level="key").sum()
    assert set(got.index) == {"guard", "big", "sixth"} and abs(got.MIN.sum() - model.SHARE * (34 + 38)) < 1e-9
    assert (ml.MIN[got.index] + got.MIN <= model.ROOM + 1e-9).all()
    bs = both.xs("star", level="by")  # Star's unreplaced shots go to the players left, Iron (out too) takes none
    own2 = ml.loc[bs.index, model.RAW].div(ml.loc[bs.index, "MIN"], axis=0).mul(bs.MIN, axis=0)
    assert abs((bs.PTS - own2.PTS).sum() - model.USAGE * (ml.at["star", "PTS"] - own2.PTS.sum())) < 1e-9
    # ROOM is a cap: a lone sitter with more to give than the rotation has room fills it, the rest goes to the bench
    big = model.lifts(ml.assign(MIN=ml.MIN.where(ml.index != "star", 120.0)), team, [["star"]]).droplevel("set")
    room = model.ROOM - ml.MIN[big.index.get_level_values("key")].to_numpy()
    assert np.allclose(big.MIN.to_numpy()[room > 0], room[room > 0]) and big.MIN.sum() < model.SHARE * 120
    # stretches between returns, per team: who's still out in each
    sp = model.stretches({"a": "2026-11-03", "b": "2026-11-10", "c": "2026-11-03"}, {"a": "AAA", "b": "AAA", "c": "BBB"},
                         "2026-10-28")
    assert sorted(sp, key=lambda x: (sorted(x[2]), x[0])) == [
        ("2026-10-28", "2026-11-03", frozenset({"a", "b"})), ("2026-11-03", "2026-11-10", frozenset({"b"})),
        ("2026-10-28", "2026-11-03", frozenset({"c"}))]
    # priced_in(): a sitter out since before the season (no games in it) was in the preseason projection, so a player
    # with an expert line keeps 1 - PROJ_W x MIN_SHRINK / (GP + MIN_SHRINK) of his lift; Iron has played, no cut
    pi = model.priced_in(lf, ["iron"], {"guard": 4}, ["guard"])
    keep = 1 - model.PROJ_W * model.MIN_SHRINK / (4 + model.MIN_SHRINK)
    assert np.allclose(pi.loc[("guard", "star")], keep * lf.loc[("guard", "star")])
    assert np.allclose(pi.loc[("big", "star")], lf.loc[("big", "star")]) and np.allclose(pi.xs("iron", level="by"), lf.xs("iron", level="by"))
    # had(): the lift a player had per game in the box scores. Star joined in game 1 and sat games 3 and 4, so Guard
    # (5 games) had 2/5 of it; game 0 came before Star's first and doesn't count
    box = pd.DataFrame([{"key": k, "TEAM_ABBREVIATION": "AAA", "GAME_ID": g, "GAME_DATE": pd.Timestamp(2026, 10, 20 + g)}
                        for g in range(5) for k in ("guard", "star") if k == "guard" or g in (1, 2)])
    h = model.had(box, team, ml, ["star"])
    assert list(h.index) == ["guard"] and np.allclose(h.loc["guard"], 2 / 5 * s.loc["guard"])
    # ... and a game two regulars sat counts their lifts together: Iron played games 0-3 and sat game 4 with Star
    box2 = pd.concat([box, pd.DataFrame([{"key": "iron", "TEAM_ABBREVIATION": "AAA", "GAME_ID": g,
                                          "GAME_DATE": pd.Timestamp(2026, 10, 20 + g)} for g in range(4)])])
    h2 = model.had(box2, team, ml, ["star", "iron"])
    assert np.allclose(h2.loc["guard"], (s.loc["guard"] + got.loc["guard"]) / 5)
    # in the league: Twin C (AAA, out until Nov 3) lifts Twins A, D and F (AAA) for their games before then: every
    # game this week (p_week, which week_vec uses), 4 of the 6 left (p, rest of season). BBB's Twin B gets nothing
    no = model.League(snap, pd.concat([base, twins]), pd.concat([cur, twins]), today=model.date(2026, 10, 26),
                      sched={"AAA": week, "BBB": week[:1] + week[3:], "XXX": week})
    up = sl.lift.xs(("twin a", "twin c"), level=("key", "by")).iloc[0]
    gap = lambda a, b, k: (a.loc[k, model.RAW] - b.loc[k, model.RAW]).astype(float).to_numpy()
    assert up.MIN > 0.5 and not len(no.lift)
    assert np.allclose(gap(sl.p, no.p, "twin a"), 4 / 6 * up[model.RAW]) and np.allclose(gap(sl.p_week, no.p, "twin a"), up[model.RAW])
    assert not gap(sl.p_week, no.p, "twin b").any() and sl.value(["twin a"]).iloc[0] > no.value(["twin a"]).iloc[0]
    assert sl.week_vec(["twin a"])[model.RAW.index("PTS")] > no.week_vec(["twin a"])[model.RAW.index("PTS")]
    assert sl.player("twin a")["lift"] == [{"by": "Twin C", "return": "2026-11-03", "games": 4,
                                           **{c: round(float(up[c]), 1) for c in ["MIN"] + model.COUNTING}}]
    assert "lift" not in no.player("twin a") and "lift" not in sl.player("twin c") and "lift" in sl.card("Twin D")
    # no games before his return (Twin C back Oct 27, no AAA game on the 26th): no lift, and none listed
    gone = model.League(snap, pd.concat([base, twins]), pd.concat([cur, twins]), today=model.date(2026, 10, 26),
                        sched={"AAA": week[1:], "BBB": week, "XXX": week},
                        inj={"Twin C": {"status": "O", "note": "Ankle.", "return": "2026-10-27"}})
    assert len(gone.lift) and not gone.lifted and not gap(gone.p_week, no.p, "twin a").any()
    # two out at once: Twin D (AAA) is out until Oct 30 too, so until then Twin C's and Twin D's minutes go to the
    # AAA players left, and from Oct 30 Twin D takes his share of Twin C's. The twins play 31.6, so Twin C (first of
    # two equal) fills Twin A's room both times and Twin D's minutes go to the bench, but his unreplaced shots still
    # come Twin A's way. Twin A's week (Oct 26, 28, 30, Nov 1) is half of each stretch
    two = model.League(snap, pd.concat([base, twins]), pd.concat([cur, twins]), today=model.date(2026, 10, 26),
                       sched={"AAA": week, "BBB": week[:1] + week[3:], "XXX": week},
                       inj={"Twin C": {"status": "O", "note": "Ankle.", "return": "2026-11-03"},
                            "Twin D": {"status": "O", "note": "Knee.", "return": "2026-10-30"}})
    ta = two.lift.xs("twin a", level="key").groupby(level="from").sum()
    assert list(ta.index) == ["2026-10-26", "2026-10-30"] and np.allclose(ta.MIN, model.ROOM - no.p.at["twin a", "MIN"])
    assert ta.PTS.iloc[0] > ta.PTS.iloc[1] > 0
    assert np.allclose(gap(two.p_week, no.p, "twin a"), (ta[model.RAW].iloc[0] + ta[model.RAW].iloc[1]) / 2)
    assert [(x["by"], x["games"]) for x in two.player("twin a")["lift"]] == [("Twin C", 4)]  # Twin D's: no minutes
    assert "twin d" not in two.lift.xs("2026-10-26", level="from").index.get_level_values("key")  # out: takes nothing
    # ... and the box scores are read net of the lifts players had: Twin C sat 2 of Twin A's 4 games, so Twin A's line
    # drops (back to what it'd be with everyone playing); Twin D, with no games, keeps his
    bx = pd.DataFrame([{**line(f"Twin {t}", 180), "TEAM_ABBREVIATION": "AAA", "GAME_ID": g,
                        "GAME_DATE": pd.Timestamp(2026, 10, 20 + g)} for g in range(4) for t in "AC" if t == "A" or g < 2])
    wl = model.League(snap, pd.concat([base, twins]), pd.concat([cur, twins]), today=model.date(2026, 10, 26),
                      sched={"AAA": week, "BBB": week[:1] + week[3:], "XXX": week}, logs=bx.assign(key=bx.PLAYER_NAME.map(model.norm)))
    assert wl.p.at["twin a", "MIN"] < no.p.at["twin a", "MIN"] and wl.p.at["twin d", "MIN"] == no.p.at["twin d", "MIN"]
    # ... but a regular with no game this season and no injury listing isn't sitting out: he's left the league (Oct 30:
    # Westbrook and Rozier took up to 9 minutes a game off their old teammates' lines). Twin C in no box score: played
    # this season, he's a sitter and Twin A's shots in those games come off his line (his minutes are capped at ROOM
    # by Twins D and F already); never, he isn't
    solo = bx[bx.PLAYER_NAME == "Twin A"].assign(key="twin a")
    at = lambda c: model.League(snap, pd.concat([base, twins]), pd.concat([cur, c]), today=model.date(2026, 10, 26), logs=solo)
    phantom, sat = at(twins.drop("twin c")), at(twins)
    assert "twin c" not in phantom.here and "twin c" in sat.here and phantom.p.at["twin a", "PTS"] > sat.p.at["twin a", "PTS"]
    # recent form: the last FORM_DAYS of box scores, for players with FORM_GP+ games in them (Twin C played 2)
    assert wl.player("twin a")["form_gp"] == 4 and "form" not in wl.player("twin c")
    late = model.League(snap, pd.concat([base, twins]), pd.concat([cur, twins]), today=model.date(2026, 11, 5),
                        logs=bx.assign(key=bx.PLAYER_NAME.map(model.norm)))
    assert "form" not in late.player("twin a") and late.movers()["window"] == "the last 14 days"  # 2 games since Oct 22

    # this week: no opponent until one is known; streams score more games higher, a waiver claim plays from the day
    # it clears; banked totals decide a category, a punt concedes it, last week's matchup is ignored
    assert sl.this_week()["opponent"] is None
    wk = sl.this_week("Them")
    gain = {s["name"]: s["gain"] for s in wk["streams"]}
    assert "Twin D" in gain and gain["Twin D"] > gain.get("Twin E", -99) and gain["Twin D"] > gain.get("Twin F", -99)
    assert next(s for s in wk["streams"] if s["name"] == "Twin D")["games"] == 4
    assert sl.this_week("Them", limit=50)["streams"][-1]["gain"] >= 0.1 and wk["games"]["you"] > 0
    assert all(s["drop"] != "Twin C" for s in sl.this_week("Them", limit=50)["streams"])  # injured: never dropped
    assert wk["cats"]["FG%"]["you"][0] is None and 0 < wk["cats"]["PTS"]["you"][1] and not wk["live"]
    up = model.League({**snap, "matchup": {"opponent": "Them", "week_end": "2026-11-01",
                                           "totals": {"Me": {"PTS": 900, "FGM": 40, "FGA": 80}, "Them": {"PTS": 100}},
                                           "games": {"Me": {"played": 7, "left": 32}, "Them": {"played": 10, "left": 27}}}},
                      pd.concat([base, twins]), pd.concat([cur, twins]), today=model.date(2026, 10, 26),
                      sched={"AAA": week, "BBB": week[:1] + week[3:], "XXX": week})
    w2 = up.this_week()
    assert w2["opponent"] == "Them" and w2["live"] and w2["cats"]["PTS"]["call"] == "safe" and w2["win"] > wk["win"]
    assert w2["cats"]["PTS"]["you"][0] == 900 and w2["cats"]["FG%"]["you"][0] == 50.0
    assert w2["yahoo_games"]["them"] == {"played": 10, "left": 27} and "yahoo_games" not in wk
    assert up.this_week(punt=["PTS"])["cats"]["PTS"] == {**w2["cats"]["PTS"], "odds": 0, "call": "punt"}
    # the week so far comes from every pairing in the snapshot, so an opponent picked in the app has his too (Oct 30:
    # one without read 100%, your week against his nothing); a side with none zeroes both, as on a Monday
    at = lambda **m: model.League({**snap, **m}, pd.concat([base, twins]), pd.concat([cur, twins]), today=model.date(2026, 10, 26),
                                  sched={"AAA": week, "BBB": week[:1] + week[3:], "XXX": week},
                                  inj={"Twin C": {"status": "O", "note": "Ankle.", "return": "2026-11-03"}}).this_week()
    w5 = at(matchup={"opponent": "Them", "week_end": "2026-11-01"},
            matchups=[{"teams": ["Me", "Them"], "totals": {"Me": {"PTS": 900}, "Them": {"PTS": 100}}}])
    assert w5["live"] and w5["cats"]["PTS"]["you"][0] == 900 and w5["cats"]["PTS"]["them"][0] == 100
    w6 = at(matchup={"opponent": "Them", "week_end": "2026-11-01", "totals": {"Me": {"PTS": 900}}})
    assert not w6["live"] and w6["cats"]["PTS"]["you"][0] == 0 and w6["win"] == wk["win"]
    stale = model.League({**snap, "matchup": {"opponent": "Them", "week_end": "2026-10-25"}}, pd.concat([base, twins]),
                         pd.concat([cur, twins]), today=model.date(2026, 10, 26))
    assert stale.this_week()["opponent"] is None and stale.week_end == "2026-11-01" and stale.rolled
    # ... unless the season schedule names the next one: a 14-day week, no score yet
    sched = [{"week": 1, "start": "2026-10-20", "end": "2026-10-25", "matchups": [["Them", "Me"]]},
             {"week": 2, "start": "2026-10-26", "end": "2026-11-08", "matchups": [["Them", "Me"]]}]
    nxt = model.League({**stale.snap, "schedule": sched}, pd.concat([base, twins]), pd.concat([cur, twins]),
                       today=model.date(2026, 10, 26))
    assert nxt.week_end == "2026-11-08" and nxt.this_week()["opponent"] == "Them" and not nxt.this_week()["live"]
    # totals pulled after Monday's games already hold them: the rest of the week counts from Tuesday
    late = model.League({**snap, "matchup": {"opponent": "Them", "week_end": "2026-11-01", "from": "2026-10-27"}},
                        pd.concat([base, twins]), pd.concat([cur, twins]), today=model.date(2026, 10, 26),
                        sched={"AAA": week, "BBB": week[:1] + week[3:], "XXX": week})
    assert late.games_week["twin a"] == 3 and late.games_week["twin b"] == 1
    # a "Jan 2" claim read on Dec 30 clears next year: Twin F gets 2 of the 4 games left, not all 4
    ny = ["2026-12-30", "2027-01-01", "2027-01-02", "2027-01-03"]
    nl = model.League({**snap, "free_agents": [f for f in snap["free_agents"] if f["name"] != "Twin F"]
                       + [{"name": "Twin F", "nba": "AAA", "waiver": "Jan 2"}]}, pd.concat([base, twins]),
                      pd.concat([cur, twins]), today=model.date(2026, 12, 30), sched={"AAA": ny, "XXX": ny})
    assert next(s for s in nl.this_week("Them", limit=50)["streams"] if s["name"] == "Twin F")["games"] == 2
    # ... and the add/drop evaluator scores a stream's move the same way: this week's gain, the season's, his games
    # (a claim from the day it clears), nothing for this week without an opponent, a punt concedes the category
    for st in sl.this_week("Them", limit=50)["streams"]:
        r = sl.add_drop(st["name"], st["drop"], opp="Them")
        assert abs(r["week"]["net"] - st["gain"]) < 0.06 and abs(r["net"] - st["season"]) < 0.06
        assert r["week"]["games"]["add"] == st["games"]
    f = sl.add_drop("Twin F", "Player 150", opp="Them")["week"]
    assert f["claim"] == "2026-11-01" and f["games"]["add"] == 1 and sl.add_drop("Twin D", "Player 150")["week"] is None
    pp = up.add_drop("Twin D", "Player 150", punt=["PTS"])
    assert pp["week"]["opponent"] == "Them" and pp["cats"]["PTS"] == {"season": [0, 0], "week": [0, 0]}
    # start/sit (Oct 30): on a full day the players who add most to this week's matchup start, not the best per-game
    # values. Player 150, your worst, blocks 6 a game this week only: he starts over a better player every day and
    # BLK and the week go up; with BLK punted his blocks count for nothing and per-game value's lineups stand
    ss = sl.start_sit("Them")
    assert len(ss["days"]) == 4 and all(len(d["start"]) <= 10 for d in ss["days"]) and ss["gain"] >= 0
    sl.p_week.at["player 150", "BLK"] = 6
    ss = sl.start_sit("Them")
    assert all(d["in"] == ["Player 150"] and len(d["out"]) == 1 for d in ss["days"]) and ss["gain"] > 0
    assert ss["cats"]["BLK"][1] > ss["cats"]["BLK"][0] and all(d["why"]["BLK"] > 0 for d in ss["days"])
    assert not any(d["in"] for d in sl.start_sit("Them", punt=["BLK"])["days"])
    assert sl.this_week("Them")["lineups"] == ss and sl.start_sit() is None  # no opponent saved
    # ... at the best plan no one change helps (options: what flipping each player's call does), and playing for AST
    # alone can only raise AST's odds over playing for every category
    assert all(o["flip"] <= 0 for d in ss["days"] for o in d["options"] if "flip" in o)
    ast = sl.start_sit("Them", cats=["AST"])
    assert ast["goal"] == ["AST"] and ast["goal_gain"] >= 0 and sl.start_sit("Them", cats=["BLK"], punt=["BLK"]) == sl.start_sit("Them", punt=["BLK"])
    sl.p_week.at["player 150", "BLK"] = sl.p.at["player 150", "BLK"]
    # ... and a player who only turns it over sits even with a slot open (a 15-man lineup): he's on the bench every day
    sl.lineup, tov = 15, sl.p_week.loc["player 150"].copy()
    sl.p_week.loc["player 150", model.RAW] = 0.0
    sl.p_week.at["player 150", "TOV"] = 10
    assert all(d["bench"] == ["Player 150"] for d in sl.start_sit("Them")["days"] if "Player 150" in d["sit"] + d["start"])
    sl.lineup, sl.p_week.loc["player 150"] = 10, tov

    # weekly add limit (Q11). No cap in the settings: nothing counted, and the plan chains streams (a later one can
    # replace an earlier streamer) in day order (Q13: gains recounted in that order); its first pick is the top
    # stream (here also first by day, so the same gain), and each stream's day is his best one
    wk = sl.this_week("Them")
    assert wk["adds"] is None and sl.adds() is None and sl.planner()["adds"] is None and wk["today"] == "2026-10-26"
    mv, top = wk["plan"]["moves"], wk["streams"][0]
    assert (mv[0]["add"], mv[0]["drop"], mv[0]["day"], mv[0]["gain"], mv[0]["games"]) == (top["name"], top["drop"], top["day"], top["gain"], top["games"])
    assert [x["day"] for x in mv] == sorted(x["day"] for x in mv)
    assert 1 < len(mv) <= 5 and all(x["gain"] >= 0.1 for x in mv) and len({x["add"] for x in mv}) == len(mv)
    assert abs(wk["plan"]["gain"] - sum(x["gain"] for x in mv)) <= 0.05 * len(mv) + 0.01 and wk["plan"]["keep"]
    for st in wk["streams"]:  # the best day beats adding him on any other of his game days
        k = sl.find(st["name"])
        assert st["day"] in sl.game_days(k, sl.week_end, sl._clears(k).get(k))
        for d in sl.game_days(k, sl.week_end):
            assert sl.add_drop(st["name"], st["drop"], opp="Them", day=d)["week"]["net"] <= st["gain"] + 0.06
    # ... a drop plays until the day before: swapping Twin A (AAA: Mon, Wed, Fri, Sun) for Twin E (BBB: Mon, Sun) is
    # best on Sunday, keeping Twin A's first three games; on Monday it costs him all four
    sun, mon = sl.add_drop("Twin E", "Twin A", opp="Them"), sl.add_drop("Twin E", "Twin A", opp="Them", day="2026-10-26")
    assert sun["week"]["day"] == "2026-11-01" and sun["week"]["games"] == {"add": 1, "drop": 1, "kept": 3, "you": [40, 40]}
    assert mon["week"]["games"]["drop"] == 4 and mon["week"]["net"] < sun["week"]["net"] and sun["net"] == mon["net"]
    assert sl.starts(["twin a", "twin e"], {"twin e": "2026-11-01"}, {"twin a": "2026-10-31"}) == {"twin a": 3, "twin e": 1}
    # ... and when none of the add's own days pays, the move waits for the day after the drop's last game this week,
    # costing nothing this week (Oct 30: Portis for Achiuwa read -1.5 on Portis's only game day)
    sched, sl.sched = sl.sched, {**sl.sched, "AAA": week[:3], "BBB": week[:1]}  # Twin A: Mon, Wed, Fri; Twin E: Mon
    sat = sl.add_drop("Twin E", "Twin A", opp="Them")
    assert sat["week"]["day"] == "2026-10-31" and sat["week"]["net"] == 0 and sat["week"]["games"]["kept"] == 3
    assert sl.add_drop("Twin E", "Twin A", opp="Them", day="2026-10-26")["week"]["net"] < 0
    sl.sched = sched
    for bad in ("2026-11-02", "Friday"):  # after this matchup week, or not a date
        try:
            sl.add_drop("Twin E", "Twin A", opp="Them", day=bad)
            raise AssertionError(f"took day {bad}")
        except KeyError:
            pass
    # ... with an open lineup spot most days, a stream for a player who plays Mon-Sat is best on Sunday (limit=50: the
    # 10 candidates a default call times are the best to add today, and adding Twin E today costs his drop 4 games)
    sl.lineup = 15
    e = next(s for s in sl.this_week("Them", limit=50)["streams"] if s["name"] == "Twin E")
    assert e["day"] == "2026-11-01" and e["games"] == 1 and e["kept"] == 3
    assert sl.add_drop("Twin E", e["drop"], opp="Them", day="2026-10-26")["week"]["net"] < e["gain"]
    sl.lineup = 10
    # 4 adds a week: used = the higher of the snapshot's count (only while it was pulled this week) and the log's adds
    # and claims dated this week (trades, drops, last week's and other teams' moves don't count)
    tx = [{"when": "Oct 27 3:02am", "team": "Me", "type": "waiver claim", "player": "Player 100"},
          {"when": "Oct 26 7:00pm", "team": "Me", "type": "add", "player": "Player 101", "dropped": "Player 150"},
          {"when": "Oct 26 3:02am", "team": "Me", "type": "trade", "player": "sent Player 154 to Them (received Player 151)"},
          {"when": "Oct 26 1:00am", "team": "Me", "type": "drop", "player": "Player 158"},
          {"when": "Oct 26 1:00am", "team": "Them", "type": "add", "player": "Player 102"},
          {"when": "Oct 25 5:00pm", "team": "Me", "type": "add", "player": "Player 103"}]
    capped = lambda **x: model.League({**snap, "settings": {**snap["settings"], "max_adds": 4}, "transactions": tx,
                                       "matchup": {"opponent": "Them", "week_start": "2026-10-26", "week_end": "2026-11-01"},
                                       "pulled": "2026-10-27T09:30:00-07:00", **x}, pd.concat([base, twins]),
                                      pd.concat([cur, twins]), today=model.date(2026, 10, 27), sched=sl.sched, inj={"Twin C": sl.inj["twin c"]})
    c2 = capped(adds={"Me": 1})
    assert c2.adds() == {"used": 2, "max": 4, "left": 2, "counted": 2, "listed": 1, "week_start": "2026-10-26",
                         "resets": "2026-11-02"}
    assert c2.adds("Them")["used"] == 1 and c2.state()["adds"]["left"] == 2 and c2.planner()["adds"]["left"] == 2
    assert len(c2.this_week()["plan"]["moves"]) <= 2
    assert capped(adds={"Me": 3}, pulled="2026-10-25T23:00:00-07:00").adds()["used"] == 2  # last week's count
    c2.today = "2027-01-02"
    assert c2._txday("Dec 30 1:00pm") == "2026-12-30" and c2._txday("Jan 2 1:00am") == "2027-01-02"
    # ... one left: the plan is the single best stream on its best day; none left: no streams, and a move waits for
    # next week (the season number still counts)
    c3, c4 = capped(adds={"Me": 3}), capped(adds={"Me": 4})
    w3, w4 = c3.this_week(), c4.this_week()
    assert w3["adds"]["left"] == 1 and len(w3["plan"]["moves"]) == 1 and w3["plan"]["moves"][0]["add"] == w3["streams"][0]["name"]
    assert w3["plan"]["moves"][0]["day"] == w3["streams"][0]["day"] and w3["plan"]["moves"][0]["gain"] == w3["streams"][0]["gain"]
    assert w4["adds"]["left"] == 0 and w4["streams"] == [] and w4["plan"] is None
    m3, m4 = c3.add_drop("Twin D", "Player 150"), c4.add_drop("Twin D", "Player 150")
    assert not m3["capped"] and m3["week"] and m3["adds"]["left"] == 1
    assert m4["capped"] and m4["week"] is None and m4["net"] == m3["net"] and m4["cats"]["PTS"]["week"] is None

    # banked totals add no noise: with the same final totals, a week that's over is surer than one just starting
    A, B = lg.vecs["Me"], lg.vecs["Them"]
    p_now, p_done = model.cat_probs(A, B, model.CATS), model.cat_probs(A * 1e-6, B * 1e-6, model.CATS, A, B)
    assert all(abs(d - 0.5) >= abs(n - 0.5) - 1e-9 for n, d in zip(p_now, p_done))
    assert np.isfinite(model.cat_probs(A * 0, B, model.CATS)).all()  # Sunday night, no games left, nothing banked
    # ties: counting stats can land level (more often the fewer there are), percentages never; level and nothing
    # left to play is a sure tie, so a win is p - tie / 2
    _, tie = model.cat_probs(A, A, model.CATS, ties=True)
    c = model.CATS.index
    assert tie[c("FG%")] == 0 and tie[c("STL")] > tie[c("PTS")] > 0
    _, tie = model.cat_probs(A * 0, A * 0, model.CATS, A, A, ties=True)
    assert np.allclose(tie[2:], 1)

    # playoff odds (season_sim): no noise, sure categories. Team 0 sweeps everyone, team 3 loses to everyone, and
    # 1 vs 2 is a 4.5-4.5 tie (8 coin flips that always split, one tied category): the higher seed goes through
    p = np.full((4, 4, 9), 0.5)
    p[0, 1:], p[1:, 0], p[:3, 3], p[3, :3] = 1, 0, 1, 0
    tie = np.zeros((4, 4, 9))
    p[1, 2] = p[2, 1] = [1, 0] * 4 + [0.5]
    tie[1, 2, 8] = tie[2, 1, 8] = 1
    made, champ, final = model.season_sim([30, 20, 10, 0], [], [(p, tie)], 4, 50, sd=(0, 0))
    assert (made == 1).all() and champ[0] == 1 and list(final) == [30, 20, 10, 0]
    made, champ, _ = model.season_sim([0, 30, 20, 10], [], [(p, tie)], 4, 50, sd=(0, 0))  # team 0 is the 4th seed
    assert champ[0] == 1
    made, champ, _ = model.season_sim([0, 30, 20, 10], [], [(p, tie)], 2, 50, sd=(0, 0))  # top 2 only: 1 vs 2, tied
    assert list(made) == [0, 1, 1, 0] and champ[1] == 1
    # a week left: 0 sweeps 3, 1 and 2 split 4.5-4.5 (a tie counts half), so 3 drops out of the top 3
    I, J = np.array([0, 1]), np.array([3, 2])
    made, _, final = model.season_sim([25, 30, 20, 10], [(I, J, p[I, J], tie[I, J])], [(p, tie)], 3, 50, sd=(0, 0))
    assert list(final) == [34, 34.5, 24.5, 10] and list(made) == [1, 1, 1, 0]
    # 3 seats: the top seed sits out round 1. Rock-paper-scissors (0 beats 1, 1 beats 2, 2 beats 0), so each bye
    # gives a different champion: 0's bye, 1 beats 2, then 0 beats 1
    q = np.full((3, 3, 9), 0.5)
    q[0, 1], q[1, 0], q[1, 2], q[2, 1], q[2, 0], q[0, 2] = 1, 0, 1, 0, 1, 0
    assert model.season_sim([30, 20, 10], [], [(q, np.zeros_like(q))], 3, 50, sd=(0, 0))[1][0] == 1
    # with noise the favourite still wins most often, the odds add up, and the same seed gives the same answer
    made, champ, _ = model.season_sim([30, 20, 10, 0], [], [(np.where(p == 1, 0.8, np.where(p == 0, 0.2, p)), tie)], 2)
    assert abs(made.sum() - 2) < 1e-9 and abs(champ.sum() - 1) < 1e-9 and champ.argmax() == 0
    assert (model.season_sim([30, 20, 10, 0], [], [(p * 0.9, tie)], 2)[1] == model.season_sim([30, 20, 10, 0], [], [(p * 0.9, tie)], 2)[1]).all()

    # League.playoffs: records so far + the schedule's weeks left. None without a schedule. Level records, 4 weeks
    # left and 1 seat: the stronger roster (Them, against Me's 13 from players 100-148) is favoured, and 1 seat makes
    # the title the same odds. 27-0 with 1 week left can't be caught (9 categories a week)
    assert lg.playoffs is None
    day = lambda n: (model.date(2026, 11, 2) + model.timedelta(n)).isoformat()
    wk = lambda i, m=True: {"week": i, "start": day(7 * i), "end": day(7 * i + 6), "matchups": [["Me", "Them"]] if m else []}
    sn = {**lg.snap, "schedule": [wk(i) for i in range(4)] + [wk(4, False)],
          "settings": {**lg.snap["settings"], "playoff_teams": 1},
          "teams": {**lg.snap["teams"], "Me": [{"name": f"Player {i}"} for i in range(100, 150, 4)]}}
    po = model.League(sn, base, cur, today=model.date(2026, 11, 1)).playoffs
    assert po["seats"] == 1 and po["weeks_left"] == 4
    assert model.League({**sn, "settings": {**sn["settings"], "scoring": "one_win"}}, base, cur).playoffs is None
    po = po["teams"]
    assert po["Them"]["playoffs"] > 0.8 and abs(po["Me"]["playoffs"] + po["Them"]["playoffs"] - 1) < 1e-9
    assert po["Me"]["playoffs"] == po["Me"]["title"] and sum(po["Me"]["proj"]) == 36
    po = model.League({**sn, "records": {"Me": {"w": 27, "l": 0, "t": 0}, "Them": {"w": 0, "l": 27, "t": 0}}}, base, cur,
                      today=model.date(2026, 11, 1)).playoffs["teams"]
    assert po["Me"]["playoffs"] == 1 and po["Me"]["w"] == 27 and 27 < po["Me"]["proj"][0] < 31.5
    # the live week: its totals so far count (Me 900 PTS up by Tuesday), only once its dates match the snapshot's
    live = {"matchup": {"opponent": "Them", "week_start": "2026-11-02", "week_end": "2026-11-08"},
            "matchups": [{"teams": ["Me", "Them"], "totals": {"Me": {"PTS": 900}, "Them": {"PTS": 100}}}]}
    tue = lambda extra: model.League({**sn, **extra}, base, cur, today=model.date(2026, 11, 3)).playoffs["teams"]["Me"]["proj"][0]
    assert tue(live) > tue({}) + 0.3
    # a trade's playoff odds, before -> after for both sides: after_trade()'s rosters, None without a schedule
    pl, deal = model.League(sn, base, cur, today=model.date(2026, 11, 1)), (["Player 148"], ["Player 199"])
    pct = lambda po, t: round(100 * po["teams"][t]["playoffs"], 1)
    tr, at = pl.trade(*deal, free=False), pl.after_trade(*deal).playoffs
    r = tr["playoffs"]
    assert r == {"you": [pct(pl.playoffs, "Me"), pct(at, "Me")], "them": [pct(pl.playoffs, "Them"), pct(at, "Them")]}
    assert r["you"][1] > r["you"][0] and r["them"][1] < r["them"][0]
    assert tr["title"]["you"] == [round(100 * x["teams"]["Me"]["title"], 1) for x in (pl.playoffs, at)]
    assert lg.trade(["Player 150"], ["Player 199"])["playoffs"] is None

    # league text import: a trimmed Instinct snapshot (settings, rosters with ranks and "Empty slot" rows, injured
    # list skipped, free agents across three lists, matchups, transactions, projections, adds, schedule), sections it
    # doesn't know skipped (PART 9, NOTES), invisible glyphs, and a hand-typed "## Team" list
    got = yahoo.parse_text("""MCHC FANTASY BASKETBALL - FULL LEAGUE SNAPSHOT
Yahoo league "mchc fantasy" (ID 231900) - 12 teams, head-to-head
Pulled Monday, October 26, 2026, 11:37 PM MST (America/Creston, UTC-7) from Yahoo Fantasy. All NBA games of Oct 26 were final at pull time.
Week 2: Mon Oct 26 - Sun Nov 1
My team: fatty zhou
=====
LEAGUE SETTINGS
=====
Scoring type:     Head-to-Head Categories, 9 cats (FG%, FT%, 3PTM, PTS, REB, AST, ST, BLK, TO)
Roster slots:     PG, SG, G, SF, PF, F, C, C, Util, Util (10 starters) + 3 BN + 3 IL = 16
Max adds/week:    4 (no season limit on adds or trades)
Playoffs:         8 teams, Weeks 21, 22 and 23 (Mon Mar 22 - Sun Apr 11, 2027). Reseeding: yes. Tiebreaker: higher seed wins
=====
PART 1 - ALL 12 TEAM ROSTERS
=====
5. fatty zhou  (5-4-0, 5th place)
---------------
   C     Nikola Jokić                DEN - C                     Pre 1    Cur 8    Ros 100%
   BN    VJ Edgecombe                PHI - PG,SG  [Q: Questionable]  Pre 88   Cur 58   Ros 91%
   IL    Empty slot
10. Big Tuo Plumbing Inc.  (0-9-0, 12th place)
   SF    Empty slot
   Util  Kel'el Ware                 MIL - C                     Pre 69   Cur 170  Ros 88%
   Note: Yahoo shows 4 bench players here; league limit is 3 BN (sf is empty).
PART 2 - INJURED PLAYERS ON ROSTERS (all 12 teams)
Questionable  VJ Edgecombe                PHI - PG,SG         (fatty zhou, BN)
PART 3 - TOP 100 FREE AGENTS (blended rank: average of preseason + current)
#   Blend  Pre   Cur   Player                         Team - Pos        Inj  %Ros
1   125    135   115   Daniel Gafford                 DAL - C                34%
PART 4 - EVERYTHING 15%+ ROSTERED, SORTED BY ROSTER % (your 15% cutoff)
#   %Ros  Player                         Team - Pos        Inj  Status    Blend  In T100
1   63%   Keegan Murray                  SAC - SF,PF       O    W Oct 28  182    no (waivers)
9   34%   Daniel Gafford                 DAL - C                FA        125    YES
PART 5 - FULL WAIVER WIRE (all 11 players, can't add directly; claim by date)
Clears   %Ros  Blend  Pre   Cur   Player                         Team - Pos        Inj
Oct 28   63%   182    108   256   Keegan Murray                  SAC - SF,PF       O
PART 6 - THIS WEEK'S MATCHUPS (week 2, Oct 26 - Nov 1)
Category totals so far this week (as of ~11:37 PM MST, all of Oct 26's games final).
fatty zhou vs Big Tuo Plumbing Inc.
  fatty zhou             FGM/FGA 36/91   FTM/FTA 15/19   3PTM 13   PTS 100   REB 37   AST 37   ST 5   BLK 5   TO 16
                         Games played 7   Games remaining 32   (week total 39)
  Big Tuo Plumbing Inc.  FGM/FGA 68/147   FTM/FTA 47/61   3PTM 14   PTS 197   REB 81   AST 37   ST 11   BLK 18   TO 22
PART 7 - RECENT TRANSACTIONS (last 30, newest first)
  Oct 26 3:08am   fatty zhou            waiver claim  Brandin Podziemski (dropped Jeremiah Fears)
  Oct 26 3:02am   Big Tuo Plumbing Inc. trade         sent Joan Beringer to fatty zhou (received Kris Dunn)
  Oct 25 12:22pm  Big Tuo Plumbing Inc. add           Joan Beringer
PART 8 - YAHOO PROJECTIONS (rest of season, per game)
fatty zhou:
  Player                      Team - Pos        MIN   FGM   FGA   FTM   FTA   3PTM  PTS   REB   AST   ST    BLK   TO
  Nikola Jokić                DEN - C           -     10.0  17.9  5.1   6.2   1.9   27.0  12.5  9.9   1.6   0.7   3.0
  VJ Edgecombe                PHI - PG,SG       no projection shown by Yahoo
PART 9 - LAST SEASON'S WEEKLY RESULTS
   PG    Ghost Player                DET - PG                    Pre 5    Cur 41   Ros 100%
=====
ADDS USED THIS WEEK AND WAIVER PRIORITY
=====
  fatty zhou            1/4
  1   Big Tuo Plumbing Inc.
  2   fatty zhou
=====
SEASON SCHEDULE (weeks 1-23)
=====
Week 10: Mon Dec 28 - Sun Jan 3
  fatty zhou vs Big Tuo Plumbing Inc.
Week 21: Mon Mar 22 - Sun Mar 28
  Playoffs - pairings not set yet (8 teams, seeded after Week 20; Yahoo lists none)
=====
NOTES
=====
   BN    Ghost Player                DET - PG                    Pre 5    Cur 41   Ros 100%
## Sharks
Chet Holmgren (Q)
Sharks has an open SF slot.""")
    assert got["name"] == "mchc fantasy" and got["me"] == "fatty zhou"
    assert list(got["teams"]) == ["fatty zhou", "Big Tuo Plumbing Inc.", "Sharks"]
    assert got["pulled"] == "2026-10-26T23:37:00-07:00" and got["from"] == "2026-10-27"  # Oct 26's games are banked
    assert got["week"] == {"week": 2, "start": "2026-10-26", "end": "2026-11-01"}
    assert got["settings"] == {"roster": 13, "starters": 10, "il": 3, "scoring": "categories", "max_adds": 4,
                               "slots": ["PG", "SG", "G", "SF", "PF", "F", "C", "C", "Util", "Util"],
                               "playoff_teams": 8, "playoff_weeks": [21, 22, 23]}
    fz = got["teams"]["fatty zhou"]
    assert fz[0] == {"name": "Nikola Jokić", "nba": "DEN", "pos": "C", "status": "", "slot": "C",
                     "rank": {"pre": 1, "cur": 8}, "owned": 100}
    assert [p["name"] for p in fz] == ["Nikola Jokić", "VJ Edgecombe"] and fz[1]["status"] == "GTD"
    assert [p["name"] for p in got["teams"]["Big Tuo Plumbing Inc."]] == ["Kel'el Ware"]
    assert got["records"]["fatty zhou"] == {"w": 5, "l": 4, "t": 0, "rank": 5}
    fa = {p["name"]: p for p in got["free_agents"]}
    assert fa["Daniel Gafford"] == {"name": "Daniel Gafford", "nba": "DAL", "pos": "C", "status": "",
                                    "rank": {"pre": 135, "cur": 115}, "owned": 34}
    assert fa["Keegan Murray"] == {"name": "Keegan Murray", "nba": "SAC", "pos": "SF,PF", "status": "O", "owned": 63,
                                   "waiver": "Oct 28", "rank": {"pre": 108, "cur": 256}}
    assert got["matchups"] == [{"teams": ["fatty zhou", "Big Tuo Plumbing Inc."], "totals": {
        "fatty zhou": {"FGM": 36, "FGA": 91, "FTM": 15, "FTA": 19, "FG3M": 13, "PTS": 100, "REB": 37, "AST": 37,
                       "STL": 5, "BLK": 5, "TOV": 16},
        "Big Tuo Plumbing Inc.": {"FGM": 68, "FGA": 147, "FTM": 47, "FTA": 61, "FG3M": 14, "PTS": 197, "REB": 81,
                                  "AST": 37, "STL": 11, "BLK": 18, "TOV": 22}},
        "games": {"fatty zhou": {"played": 7, "left": 32}}}]
    assert got["transactions"] == [
        {"when": "Oct 26 3:08am", "team": "fatty zhou", "type": "waiver claim", "player": "Brandin Podziemski",
         "dropped": "Jeremiah Fears"},
        {"when": "Oct 26 3:02am", "team": "Big Tuo Plumbing Inc.", "type": "trade",  # one line per team; no add (Q11)
         "player": "sent Joan Beringer to fatty zhou (received Kris Dunn)"},
        {"when": "Oct 25 12:22pm", "team": "Big Tuo Plumbing Inc.", "type": "add", "player": "Joan Beringer"}]
    assert got["projections"] == {"Nikola Jokić": {"FGM": 10.0, "FGA": 17.9, "FTM": 5.1, "FTA": 6.2, "FG3M": 1.9,
                                                   "PTS": 27.0, "REB": 12.5, "AST": 9.9, "STL": 1.6, "BLK": 0.7, "TOV": 3.0}}
    assert got["adds"] == {"fatty zhou": 1} and got["waiver_order"] == ["Big Tuo Plumbing Inc.", "fatty zhou"]
    assert got["schedule"] == [
        {"week": 10, "start": "2026-12-28", "end": "2027-01-03", "matchups": [["fatty zhou", "Big Tuo Plumbing Inc."]]},
        {"week": 21, "start": "2027-03-22", "end": "2027-03-28", "matchups": []}]
    assert got["teams"]["Sharks"] == [{"name": "Chet Holmgren", "pos": "", "nba": "", "status": "GTD"}]
    # pulled mid-games: the rest of the week starts tomorrow; pulled before tip-off: today still counts
    assert yahoo.parse_text("Pulled Monday, October 26, 2026, ~8:30 PM MST (UTC-7)")["from"] == "2026-10-27"
    assert yahoo.parse_text("Pulled Saturday, October 31, 2026, 10:00 AM MST (UTC-7)")["from"] == "2026-10-31"
    assert yahoo.parse_text("Pulled Tuesday, October 27, 2026, 12:55 PM MST (UTC-7) from Yahoo Fantasy. No Oct 27 games "
                            "had started at pull time; all earlier games were final.")["from"] == "2026-10-27"

    # punt planner: each build's free moves and best fair trades, re-scored on true odds. Bricks (stars everywhere but
    # 1 for 12 at the line) sink FT% from 50% to nothing, more than the first one adds, so staying balanced never takes
    # one; with FT% punted all three pay, and the punt clearly wins on true odds
    bricks = [line(f"Brick {j}", 260, FTM=1, FTA=12) for j in "ABC"]
    bl = model.League({**lg.snap, "free_agents": lg.snap["free_agents"] + [as_pl(f"Brick {j}") for j in "ABC"]},
                      pd.concat([base, model.keyed(pd.DataFrame(bricks))]),
                      pd.concat([cur, model.keyed(pd.DataFrame([{**x, "GP": 5} for x in bricks]))]))
    plan = bl.strategy(size=1)
    assert plan["pick"] == ["FT%"] and plan["edge"] > 2
    assert next(b for b in plan["builds"] if not b["punt"])["steps"][0]["free"] == []
    assert {m["add"] for m in plan["builds"][0]["steps"][0]["free"]} == {"Brick A", "Brick B", "Brick C"}
    assert plan["builds"][0]["win"] == round(100 * bl.after_free(["FT%"]).outlook("Me")["win"], 1)  # nothing conceded
    # trades in a row: bricks on three other teams. The first one sinks FT% and lowers your true odds, so one trade per
    # build stays balanced; three pay off and the FT% punt wins. The market prices bricks on all 9 categories (Yahoo
    # rank 40), so each trade clears fair on its own, against the rosters at that point
    brick = lambda j: {**as_pl(f"Brick {j}"), "rank": {"pre": 40, "cur": 40}}
    bricks = [line(f"Brick {j}", 300, FTM=1, FTA=12) for j in "ABC"]
    teams = {"Me": lg.snap["teams"]["Me"], "Them": lg.snap["teams"]["Them"] + [brick("C")],
             "Third": [as_pl(f"Player {i}") for i in range(152, 200, 4)] + [brick("A")],
             "Fourth": [as_pl(f"Player {i}") for i in range(153, 200, 4)] + [brick("B")]}
    tl = model.League({**lg.snap, "teams": teams, "settings": {**lg.snap["settings"], "teams": 4}},
                      pd.concat([base, model.keyed(pd.DataFrame(bricks))]),
                      pd.concat([cur, model.keyed(pd.DataFrame([{**x, "GP": 5} for x in bricks]))]))
    plan = tl.strategy(size=1)
    ft, flat = plan["builds"][0], next(b for b in plan["builds"] if not b["punt"])
    wins = [s["win"] for s in ft["steps"]]
    assert plan["pick"] == ["FT%"] and plan["edge"] > 2 and len(flat["steps"]) == 1 and tl.strategy(size=1, steps=1)["pick"] == []
    assert len(wins) == 4 and wins[1] < wins[0] < wins[3] and {g for s in ft["steps"][1:] for g in s["trade"]["get"]} == {
        "Brick A", "Brick B", "Brick C"} and not any(s["trade"]["later"] for s in ft["steps"][1:])
    for s in ft["steps"][1:]:
        d = s["trade"]["market"]["diff_pct"]
        assert abs(d) <= 15
    assert {p["name"] for p in ft["buy"][:3]} == {"Brick A", "Brick B", "Brick C"}  # far below their worth to FT%
    # sell list, its mirror: your players the market prices 5+ above their worth to the build, biggest gap first, with
    # why. Yahoo ranks Player 154 (about #46 to us) #1, Player 158 runs hot (Cur 1, Pre 30), and a free-throw
    # specialist is only overpriced once FT% is punted
    shooter = line("Shooter", 190, FTM=12, FTA=12.5)
    me = rk(rk(lg.snap["teams"]["Me"], "Player 154", 1, 1), "Player 158", 30, 1) + [as_pl("Shooter")]
    sl = model.League({**lg.snap, "teams": {**lg.snap["teams"], "Me": me}}, pd.concat([base, model.keyed(pd.DataFrame([shooter]))]),
                      pd.concat([cur, model.keyed(pd.DataFrame([{**shooter, "GP": 5}]))]))
    flat, ft = sl.sell(), sl.sell(["FT%"])
    assert [p["name"] for p in flat[:2]] == ["Player 154", "Player 158"] and "Shooter" not in {p["name"] for p in flat}
    assert all(p["team"] == "Me" and p["gap"] >= 5 for p in flat + ft) and [p["gap"] for p in flat] == sorted(
        (p["gap"] for p in flat), reverse=True)
    assert flat[0]["why"].startswith("Yahoo projects him #1, we have him #4") and flat[1]["why"].startswith(
        "Hot start: Yahoo #1 this season, #30 projected")
    assert next(p for p in ft if p["name"] == "Shooter")["why"].startswith("His value is in FT% (+")
    # balanced tries every build's deals and replays every chain while each trade lifts true odds, so a build that
    # never lowers them and makes no waiver moves can't beat it
    plan = lg.strategy(size=2)
    flat = plan["builds"][0]
    assert plan["pick"] == [] and not flat["punt"] and len(plan["builds"]) == 1 + len(model.CATS) + len(model.PAIRS)
    steady = lambda b: all(x["win"] <= y["win"] for x, y in zip(b["steps"], b["steps"][1:]))
    assert all(b["win"] <= flat["win"] for b in plan["builds"] if steady(b) and not any(s["free"] for s in b["steps"]))
    t = flat["steps"][1]["trade"]
    assert flat["steps"][1]["win"] == lg.trade(t["give"], t["get"])["win_after"] > flat["steps"][0]["win"]
    # each trade's waiver moves (its drops and open-spot pickups, then the free moves it opens up) keep your roster size
    assert all(len(s["trade"]["get"]) - len(s["trade"]["give"]) + len(s["add"]) - len(s["drop"]) == 0
               for b in plan["builds"] for s in b["steps"][1:])
    # star fit: your top 5 players' weak spots in the categories a build concedes
    assert all(s["cat"] in b["punt"] and s["z"] <= -1 for b in plan["builds"] for s in b["stars"]) and not flat["stars"]
    assert [s["name"] for s in next(b for b in plan["builds"] if b["punt"] == ["TOV"])["stars"]] == ["Player 198"]
    # cheapest categories: one more player-z helps in every category, most where it moves the odds most
    cheap = lg.cheapest()
    assert {x["cat"] for x in cheap} == set(model.CATS) and all(x["per_z"] > 0 for x in cheap)
    assert [x["per_z"] for x in cheap] == sorted((x["per_z"] for x in cheap), reverse=True)

    w = lg.waivers()
    assert all(m["gain"] >= model.MOVE * lg.s for m in w["moves"])
    # recently dropped: only players nobody has picked up since, newest first, each once
    tx = [{"when": "Oct 27 1:20pm", "team": "Them", "type": "add", "player": "Player 101", "dropped": "Player 103"},
          {"when": "Oct 26 7:00pm", "team": "Me", "type": "drop", "player": "Player 102"},
          {"when": "Oct 25 7:00pm", "team": "Them", "type": "drop", "player": "Player 151"},  # back on a roster
          {"when": "Oct 24 7:00pm", "team": "Them", "type": "drop", "player": "Player 103"}]
    dr = model.League({**lg.snap, "transactions": tx}, base, cur).dropped()
    assert [(p["name"], p["by"]) for p in dr] == [("Player 103", "Them"), ("Player 102", "Me")]
    assert set(lg.profile()["odds"]) == set(model.CATS)

    # expert projections (N4): Yahoo's (the snapshot's) where it has him, else ESPN's, blended into the pre-season
    # line at PROJ_W. Yahoo's minutes are ESPN's scaled by output, a spelling variant lands on the player it means,
    # and a rookie with no games yet gets a row: his projection blended with the fringe line. Nobody else moves.
    stat = lambda i, x=1.0, cols=model.RAW: {c: line("", i)[c] * x for c in cols}
    full = ["MIN"] + model.RAW
    ex = model.League({**lg.snap, "projections": {"Player 150": stat(150, 2), "Zed Unknown": stat(150)}}, base, cur,
                      proj={"Player 150": stat(150, cols=full),
                            "Player 152": {c: v for c, v in stat(152, 1.5, full).items() if c != "FG3M"},  # ESPN drops 0s
                            "Nikola Jokich": stat(199, 0.5, full), "New Rookie": stat(190, cols=full)})
    pj = ex.proj
    assert pj.at["player 150", "src"] == "Yahoo" and pj.at["player 152", "src"] == "ESPN"
    assert pj.at["player 152", "FG3M"] == 0 and not ex.z.isna().any().any()  # a missing stat is 0, never NaN
    assert abs(pj.at["player 150", "MIN"] - 2 * line("", 150)["MIN"]) < 1e-9  # twice the output: twice ESPN's minutes
    assert "nikola jokic" in pj.index and "zed unknown" not in pj.index  # Yahoo-only, no minutes to go on: left out
    pre = model.prior(base, pj)
    assert abs(pre.at["player 152", "PTS"] - (1 - model.PROJ_W + 1.5 * model.PROJ_W) * base.at["player 152", "PTS"]) < 1e-9
    fringe = base.nlargest(200, "MIN").tail(60).PTS.mean()
    assert abs(pre.at["new rookie", "PTS"] - ((1 - model.PROJ_W) * fringe + model.PROJ_W * line("", 190)["PTS"])) < 1e-9
    assert ex.find("New Rookie") == "new rookie" and ex.find("Zed Unknown", strict=False) is None
    assert ex.p.at["player 150", "PTS"] > lg.p.at["player 150", "PTS"] and ex.p.at["player 152", "PTS"] > lg.p.at["player 152", "PTS"]
    assert np.allclose(ex.p.loc["player 160", model.RAW].astype(float), lg.p.loc["player 160", model.RAW].astype(float))
    assert ex.player("player 150")["proj"] == "Yahoo" and "proj" not in lg.player("player 150")
    yc = ex.card("Player 150")["lines"]["yahoo"]
    assert yc["MIN"] is None and yc["PTS"] == round(2 * line("", 150)["PTS"], 1) and "espn" in ex.card("Player 152")["lines"]

    test_freshness(lg)


def test_freshness(lg):
    """Q9: freshness() reads each cache's file time against its window; a failed refresh keeps the old copy and says
    so, a good one clears it; a League built directly (no caches) has no sources."""
    import os, tempfile, time
    from pathlib import Path
    assert lg.sources() == [] and lg.state()["sources"] == []
    now, old_data, old_failed = time.time(), model.DATA, dict(model.FAILED)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        model.DATA = d
        try:
            model.FAILED.clear()
            name = {k: f for k, _, f, _, _ in model.SOURCES}
            for k, hours_ago in {"stats": 2, "logs": 13, "schedule": 23, "injuries": 1}.items():
                (d / name[k]).write_text("{}")
                os.utime(d / name[k], (now - hours_ago * 3600,) * 2)
            got = {x["key"]: x for x in model.freshness(now)}
            assert list(got) == ["stats", "logs", "schedule", "injuries", "projections", "news"]
            assert {k: x["status"] for k, x in got.items()} == {"stats": "ok", "logs": "stale", "schedule": "ok",
                                                                "injuries": "ok", "projections": "missing", "news": "missing"}
            assert got["logs"]["age"] == 13 and got["projections"]["fetched"] == "" and got["projections"]["lost"]
            assert got["stats"]["fetched"].endswith("Z") and got["stats"]["hours"] == 12
            # a refresh that fails keeps the old copy, flagged "failed" with the reason; the next good one clears it
            inj = d / name["injuries"]
            inj.write_text('{"A": 1}')
            os.utime(inj, (now - 4 * 3600,) * 2)
            def down():
                raise OSError("403 blocked")
            assert model._cached(inj, 3, down) == {"A": 1}
            x = next(x for x in model.freshness() if x["key"] == "injuries")
            assert x["status"] == "failed" and "403" in x["error"] and 3.9 < x["age"] < 4.1
            assert model._cached(inj, 3, lambda: {"B": 2}, force=True) == {"B": 2}  # Refresh's refetch
            assert model._cached(inj, 3, lambda: {"C": 3}) == {"B": 2}  # inside its window: not refetched
            x = next(x for x in model.freshness() if x["key"] == "injuries")
            assert x["status"] == "ok" and x["error"] == "" and x["age"] < 0.1
            assert model._cached(d / name["projections"], 24, down) == {}  # nothing to fall back on
            assert next(x for x in model.freshness() if x["key"] == "projections")["status"] == "missing"
            # this season's stats past their window, network down: the old copy, flagged; none at all still raises
            from nba_api.stats.endpoints import leaguedashplayerstats as lds
            real, lds.LeagueDashPlayerStats = lds.LeagueDashPlayerStats, lambda **kw: down()
            try:
                pd.DataFrame([line("Player 1", 1)]).to_csv(d / name["stats"], index=False)
                os.utime(d / name["stats"], (now - 13 * 3600,) * 2)
                assert list(model.fetch(model.CUR, max_age_hours=12).index) == ["player 1"]
                assert next(x for x in model.freshness() if x["key"] == "stats")["status"] == "failed"
                try:
                    model.fetch(model.LAST)
                    assert False, "no stats and no network must raise"
                except OSError:
                    pass
            finally:
                lds.LeagueDashPlayerStats = real
            # a League keeps the file times it was built from: a later refetch elsewhere doesn't show as its own
            lg.loaded = {name["stats"]: now - 3600, name["logs"]: None}
            got = {x["key"]: x for x in lg.state()["sources"]}
            assert got["stats"]["status"] == "failed" and 0.9 < got["stats"]["age"] < 1.1  # the failure is live
            assert got["logs"]["status"] == "missing" and got["injuries"]["status"] == "ok"  # not in loaded: the file
        finally:
            model.DATA, lg.loaded = old_data, None
            model.FAILED.clear()
            model.FAILED.update(old_failed)


if __name__ == "__main__":
    test_all()
    print("ok")
