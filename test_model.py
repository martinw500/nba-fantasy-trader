"""python test_model.py  (synthetic data, no network)"""
import numpy as np
import pandas as pd

import model


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

    # injuries: an "INJ" tag lowers a player's expected weekly contribution
    lg.meta["player 199"] = {**lg.meta["player 199"], "status": "INJ"}
    assert lg._availability(base, cur)["player 199"] < lg.avail["player 199"]

    # finder honours its own rules
    for idea in lg.ideas("fair"):
        assert idea["net"] > 1 and idea["their_net"] > 0.5
    for idea in lg.ideas("finesse"):
        assert idea["net"] > 1.5 and idea["their_view"] > 0.3

    # search (pinned on a target: this league's even spread makes open searches look awful to "Them"):
    # pins always in, locks never in, tier thresholds hold, 3-player sides and every filter work
    found = 0
    for tier, (t0, t1, t2) in model.TIERS.items():
        for x in lg.search(get=["Player 199"], tier=tier, limit=200):
            found += 1
            assert x["net"] >= t0 and x["their_net"] >= t1 and x["their_view"] >= t2, (tier, x)
    assert found
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

    # uneven trades drop whoever costs the least win %, two at a time if needed
    r = lg.trade(["Player 198"], ["Player 151", "Player 155", "Player 159"])
    keys = [k for k in lg.rosters["Me"] if k != "player 198"] + ["player 151", "player 155", "player 159"]
    c = lg._contrib("Me", keys)
    assert r["you_drop"] == lg.names(sorted(keys, key=c.get)[:2])

    w = lg.waivers()
    assert all(m["gain"] > 0.5 for m in w["moves"])
    assert set(lg.profile()["odds"]) == set(model.CATS)


if __name__ == "__main__":
    test_all()
    print("ok")
