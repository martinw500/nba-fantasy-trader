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

    # punting a category removes it from value
    full, punted = lg.value(["player 190"]).iloc[0], lg.value(["player 190"], punt=["PTS"]).iloc[0]
    assert abs((full - punted) - lg.z.at["player 190", "PTS"]) < 1e-9

    # hot start shows up as sell-high for the owner; perceived value is above ROS value
    sells = [p["name"] for p in lg.movers()["sell_high"]]
    assert "Player 150" in sells and all(p["gap"] > 0.5 for p in lg.movers()["sell_high"])

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

    # finder honours its own rules
    for idea in lg.ideas("fair"):
        d = idea["market"]["diff_pct"]
        assert idea["net"] >= lg.s - 0.05 and (abs(d) <= 15 or idea["their_net"] > 0 and abs(d) <= model.FAIR_FIT)
    for idea in lg.ideas("finesse"):
        assert idea["net"] >= 1.5 * lg.s - 0.05 and idea["market"]["diff_pct"] <= model.EVEN

    # tiers: your gain, theirs (win %) and the market gap (% you gain on market). Fair has two routes (Martin, Oct 26):
    # the market within 15%, or both teams gaining and within 30%. No tier overpays over 30%; max_overpay only tightens
    s = lg.s
    assert lg._tier("fair", 2 * s, -5 * s, -14) and not lg._tier("fair", 2 * s, -5 * s, -20)
    assert lg._tier("fair", 2 * s, 0.1, 25) and not lg._tier("fair", 2 * s, 0.1, 35)
    assert not lg._tier("fair", 2 * s, 0.1, -12, max_overpay=10) and lg._tier("fair", 2 * s, 0.1, -8, max_overpay=10)
    assert lg._tier("easy", s, 0, -20) and not lg._tier("easy", s, 0, 0)  # easy: they win on market
    assert lg._tier("hard", 4 * s, -5 * s, 60) and not lg._tier("hard", 4 * s, 0, 0)  # hard: you do
    assert lg._tier("finesse", 2 * s, -2 * s, 0) and not lg._tier("finesse", 2 * s, -2 * s, 10)
    assert not any(lg._tier(t, 9, 9, -31) for t in model.TIERS)
    assert model.accept(-10, 0) == "Likely yes" and model.accept(25, 0.1) == "Maybe" and model.accept(25, -0.1) == "Long shot"
    assert model.priced(100, 65)["verdict"] == "you overpay 35%" and model.priced(65, 100)["diff_pct"] == 35
    assert model.priced(50, 52)["verdict"] == "even" and model.priced(0, 0)["diff_pct"] == 0

    # market: Yahoo's preseason rank blended with its current one by GP / (GP + HYPE) (5 games: a third), re-ranked;
    # players Yahoo didn't rank take the model's. Value = 100 x MARKET^(rank - 1)
    rk = lambda ps, name, pre, now: [{**p, "rank": {"pre": pre, "cur": now}} if p["name"] == name else p for p in ps]
    mk = model.League({**lg.snap, "teams": {"Me": rk(lg.snap["teams"]["Me"], "Player 170", 1, 400),
                                            "Them": rk(lg.snap["teams"]["Them"], "Player 171", 60, 60)}}, base, cur)
    m = mk.market
    assert m.at["player 170", "yahoo"] and not m.at["player 174", "yahoo"] and mk.player("player 174")["mkt_model"]
    assert m.at["player 171", "rank"] < m.at["player 170", "rank"]  # 2/3 x 1 + 1/3 x 400 = 134, behind 60
    assert sorted(m["rank"]) == list(range(1, len(m) + 1)) and np.allclose(m.value, 100 * model.MARKET ** (m["rank"] - 1))
    # drop cost: the side taking more players gives up its drops' market value, the other adds its pickups'
    r = lg.trade(["Player 198"], ["Player 151", "Player 155"])
    mv = lambda ns: sum(lg.market.at[lg.find(n), "value"] for n in ns)
    assert abs(r["market"]["get"] - (mv(["Player 151", "Player 155"]) - mv(r["you_drop"]))) < 0.1
    assert r["they_add"] and abs(r["market"]["give"] - (mv(["Player 198"]) + mv(r["they_add"]))) < 0.1
    assert any(f.startswith("You overpay on the market") for f in lg.trade(["Player 198"], ["Player 151"])["flags"])
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
    hits = lg.search(get=["Player 199"], lock=["Player 198"], tier="hard", limit=200)
    assert hits and all("Player 199" in x["get"] and "Player 198" not in x["give"] for x in hits)
    assert any(len(x["give"]) == 3 for x in hits)
    # search prices a deal like trade() does (same drops and pickups), 3-for-1s included
    for x in [h for h in hits if len(h["give"]) == 3][:3] + hits[:3]:
        assert lg.trade(x["give"], x["get"])["market"] == x["market"], x
    shop = lg.search(give=["Player 150"], get=["Player 199"], tier="hard")
    assert shop and all("Player 150" in x["give"] for x in shop)
    cons = lg.search(get=["Player 199"], tier="hard", shape="consolidate")
    assert cons and all(len(x["give"]) > len(x["get"]) for x in cons)
    assert all(x["score"] > 0 for x in lg.search(get=["Player 199"], goal="cats", goal_cats=["STL"], tier="hard"))
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
    assert short.search(tier="hard", their_free=True, limit=200) == short.search(team="Them", tier="hard",
                                                                                 their_free=True, limit=200)[:3]

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
    assert sl.week_vec(["twin a"])[model.RAW.index("PTS")] > sl.week_vec(["twin b"])[model.RAW.index("PTS")]
    assert abs(sl.avail["twin c"] / sl.rate["twin c"] - 2 / 6) < 1e-9 and sl.player("twin c")["injury"] == "Ankle."
    # only the best `lineup` players with a game count each day: a third twin adds nothing on days two better ones play
    sl.lineup = 2
    assert sl.starts(["player 198", "twin a", "twin d"]) == {"player 198": 4, "twin a": 4}
    # ... and a bench player fills in on a light day: twin a (a claim) only plays from Oct 30, so twin b starts Monday
    assert sl.starts(["player 198", "twin a", "twin b"], {"twin a": "2026-10-30"}) == {"player 198": 4, "twin a": 2,
                                                                                         "twin b": 1}
    sl.lineup = 10
    # player card: games this matchup week and the next three (Monday-Sunday without a season schedule), after his
    # return date; without box scores, this season's stats line
    c = sl.card("Twin C")
    assert [w["games"] for w in c["weeks"]] == [0, 2, 0, 0] and c["weeks"][1]["start"] == "2026-11-02"
    assert set(c["lines"]) == {"ros", "season"} and c["lines"]["season"]["gp"] == 60 and c["lines"]["ros"]["gp"] is None
    assert c["fit_rank"] >= 1 and c["mkt_rank"] >= 1 and c["team"] == "Me"
    # with box scores: this season, last 14 and last 7 days (Oct 26: since Oct 12 and Oct 19)
    box = pd.DataFrame([{**line("Twin A", 180, PTS=p), "GAME_DATE": pd.Timestamp(d)}
                        for p, d in ((30, "2026-10-24"), (20, "2026-10-20"), (10, "2026-10-15"), (40, "2026-10-01"))])
    sl.logs = box.assign(key=box.PLAYER_NAME.map(model.norm))
    c = sl.card("Twin A")["lines"]
    assert [(w, c[w]["gp"], c[w]["PTS"]) for w in ("season", "last14", "last7")] == [
        ("season", 4, 25), ("last14", 3, 20), ("last7", 2, 25)]
    sl.logs = sl.logs[sl.logs.GAME_DATE > "2026-10-16"]  # the same 2 games in every window: shown once
    assert set(sl.card("Twin A")["lines"]) == {"ros", "season"}
    sl.logs = None

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
    # banked totals add no noise: with the same final totals, a week that's over is surer than one just starting
    A, B = lg.vecs["Me"], lg.vecs["Them"]
    p_now, p_done = model.cat_probs(A, B, model.CATS), model.cat_probs(A * 1e-6, B * 1e-6, model.CATS, A, B)
    assert all(abs(d - 0.5) >= abs(n - 0.5) - 1e-9 for n, d in zip(p_now, p_done))
    assert np.isfinite(model.cat_probs(A * 0, B, model.CATS)).all()  # Sunday night, no games left, nothing banked

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
    assert got["settings"] == {"roster": 13, "starters": 10, "scoring": "categories", "max_adds": 4}
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
        assert abs(d) <= 15 or s["trade"]["their_net"] > 0 and abs(d) <= model.FAIR_FIT
    assert {p["name"] for p in ft["buy"][:3]} == {"Brick A", "Brick B", "Brick C"}  # far below their worth to FT%
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


if __name__ == "__main__":
    test_all()
    print("ok")
