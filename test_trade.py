import pandas as pd

import trade


def fake(n=200):
    # player i gets better at everything as i grows; ties broken by name
    rows = [{"PLAYER_NAME": f"Player {i}", "GP": 60, "MIN": 10 + i * 0.1, "FGM": 2 + i * 0.03, "FGA": 5 + i * 0.05,
             "FTM": 1 + i * 0.02, "FTA": 1.5 + i * 0.02, "FG3M": i * 0.01, "PTS": 5 + i * 0.1, "REB": 2 + i * 0.03,
             "AST": 1 + i * 0.03, "STL": 0.5 + i * 0.004, "BLK": 0.3 + i * 0.004, "TOV": 1 + i * 0.005}
            for i in range(n)]
    rows.append({**rows[-1], "PLAYER_NAME": "Nikola Jokić"})
    return pd.DataFrame(rows)


def test_all():
    df, repl = trade.value(fake())
    assert df.iloc[0].Z > df.iloc[-1].Z
    assert trade.find(df, "nikola jokic").PLAYER_NAME == "Nikola Jokić"  # accents + case
    assert trade.find(df, "Player 19 jr").PLAYER_NAME == "Player 19"  # suffix stripped
    # 1-for-1 upgrade is an accept, the reverse is a decline
    assert trade.analyze(df, repl, ["Player 50"], ["Player 150"])["verdict"] == "ACCEPT"
    assert trade.analyze(df, repl, ["Player 150"], ["Player 50"])["verdict"] == "DECLINE"
    # 2 scrubs for 1 star: replacement adjustment must make the star side win
    r = trade.analyze(df, repl, ["Player 199"], ["Player 100", "Player 101"])
    assert r["verdict"] == "DECLINE", r["net"]["Z"]
    # blending: projection-only player appears, shared player is averaged
    proj = pd.DataFrame([{**fake().iloc[0].to_dict(), "PLAYER_NAME": "Rookie Guy"},
                         {**fake().iloc[0].to_dict(), "PLAYER_NAME": "Player 1", "PTS": 100}])
    b = trade.blend(fake(), proj, 0.5)
    assert "Rookie Guy" in set(b.PLAYER_NAME)
    assert abs(b[b.PLAYER_NAME == "Player 1"].PTS.iloc[0] - (100 + 5.1) / 2) < 1e-9


if __name__ == "__main__":
    test_all()
    print("ok")
