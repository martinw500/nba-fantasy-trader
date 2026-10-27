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
        assert idea["net"] > 1 and idea["their_net"] > 0
    for idea in lg.ideas("finesse"):
        assert idea["net"] > 1.5 and idea["their_view"] > 0.3

    # search: tier thresholds hold. This 2-team league is zero-sum with z-scores spread +-46, so the real tiers find
    # little; the mechanics (pins always in, locks never in, 3-player sides, filters) run on a loose test-only tier
    for tier, (t0, t1, t2) in model.TIERS.items():
        for x in lg.search(tier=tier, limit=200):
            assert x["net"] >= t0 and x["their_net"] >= t1 and x["their_view"] >= t2, (tier, x)
    model.TIERS["hard"], hard = (0.5, -20, -999), model.TIERS["hard"]
    hits = lg.search(get=["Player 199"], lock=["Player 198"], tier="hard", limit=200)
    assert hits and all("Player 199" in x["get"] and "Player 198" not in x["give"] for x in hits)
    assert any(len(x["give"]) == 3 for x in hits)
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
                                           "totals": {"Me": {"PTS": 900, "FGM": 40, "FGA": 80}, "Them": {"PTS": 100}}}},
                      pd.concat([base, twins]), pd.concat([cur, twins]), today=model.date(2026, 10, 26),
                      sched={"AAA": week, "BBB": week[:1] + week[3:], "XXX": week})
    w2 = up.this_week()
    assert w2["opponent"] == "Them" and w2["live"] and w2["cats"]["PTS"]["call"] == "safe" and w2["win"] > wk["win"]
    assert w2["cats"]["PTS"]["you"][0] == 900 and w2["cats"]["FG%"]["you"][0] == 50.0
    assert up.this_week(punt=["PTS"])["cats"]["PTS"] == {**w2["cats"]["PTS"], "odds": 0, "call": "punt"}
    stale = model.League({**snap, "matchup": {"opponent": "Them", "week_end": "2026-10-25"}}, pd.concat([base, twins]),
                         pd.concat([cur, twins]), today=model.date(2026, 10, 26))
    assert stale.this_week()["opponent"] is None and stale.week_end == "2026-11-01"
    # banked totals add no noise: with the same final totals, a week that's over is surer than one just starting
    A, B = lg.vecs["Me"], lg.vecs["Them"]
    p_now, p_done = model.cat_probs(A, B, model.CATS), model.cat_probs(A * 1e-6, B * 1e-6, model.CATS, A, B)
    assert all(abs(d - 0.5) >= abs(n - 0.5) - 1e-9 for n, d in zip(p_now, p_done))
    assert np.isfinite(model.cat_probs(A * 0, B, model.CATS)).all()  # Sunday night, no games left, nothing banked

    # league text import: Instinct-style snapshot (numbered teams, fixed-width rows, [O: Out], invisible glyphs,
    # injured section skipped, FA section with roster % and waiver dates) and a hand-typed "## Team" list
    name, teams, fa = yahoo.parse_text("""Yahoo league "mchc fantasy" (ID 1)
PART 1 - ALL 12 TEAM ROSTERS
1. Toronto blue jays\ue002  (4-5-0, 7th place)
-------------------
   C     Ivica Zubac                 IND - C  [O: Out]
   Util  De'Aaron Fox                SAS - PG
PART 2 - INJURED PLAYERS ON ROSTERS (all 12 teams)
Out           Ivica Zubac                 IND - C            (Toronto blue jays, C)
PART 4 - EVERYTHING 15%+ ROSTERED, SORTED BY ROSTER %
1   63%   Keegan Murray                SAC - SF,PF       O    W Oct 28   189    no (waivers)
5   37%   Sandro Mamukelashvili        LAL - PF,C             FA         150    YES
## Sharks
Chet Holmgren (Q)
Sharks has an open SF slot.""")
    assert name == "mchc fantasy" and list(teams) == ["Toronto blue jays", "Sharks"]
    assert teams["Toronto blue jays"][0] == {"name": "Ivica Zubac", "nba": "IND", "pos": "C", "status": "O", "slot": "C"}
    assert teams["Toronto blue jays"][1]["name"] == "De'Aaron Fox"
    assert teams["Sharks"] == [{"name": "Chet Holmgren", "pos": "", "nba": "", "status": "GTD"}]
    assert fa[0] == {"name": "Keegan Murray", "nba": "SAC", "pos": "SF,PF", "status": "O", "owned": 63, "waiver": "Oct 28"}
    assert fa[1]["owned"] == 37 and "waiver" not in fa[1] and fa[1]["status"] == ""

    w = lg.waivers()
    assert all(m["gain"] > 0.5 for m in w["moves"])
    assert set(lg.profile()["odds"]) == set(model.CATS)


if __name__ == "__main__":
    test_all()
    print("ok")
