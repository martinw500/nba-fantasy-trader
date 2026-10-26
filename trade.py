"""NBA fantasy trade analyzer: 9-cat z-scores or points, from last season's stats + optional projections.

  python trade.py --give "Jalen Brunson" --get "Tyrese Haliburton, Jarrett Allen"
  python trade.py --give "..." --get "..." --mode points --proj projections.csv --explain
"""
import argparse
import difflib
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

SEASON = "2025-26"  # "last year" relative to the 2026-27 season
COUNTING = ["FG3M", "PTS", "REB", "AST", "STL", "BLK", "TOV"]
CATS = ["FG%", "FT%"] + COUNTING
RAW = ["FGM", "FGA", "FTM", "FTA"] + COUNTING
POINTS = {"PTS": 1, "REB": 1.2, "AST": 1.5, "STL": 3, "BLK": 3, "TOV": -1}  # Yahoo default; edit to match your league
DATA = Path(__file__).parent / "data"


def norm(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"[^a-z0-9 ]", "", name)
    return " ".join(re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name).split())


def load_stats(season=SEASON):
    """Per-game stats from stats.nba.com, cached to data/ so you only hit the API once."""
    cache = DATA / f"stats_{season}.csv"
    if not cache.exists():
        from nba_api.stats.endpoints import leaguedashplayerstats
        df = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season, per_mode_detailed="PerGame", timeout=60).get_data_frames()[0]
        DATA.mkdir(exist_ok=True)
        df[["PLAYER_NAME", "GP", "MIN"] + RAW].to_csv(cache, index=False)
    return pd.read_csv(cache)


def blend(last, proj, w):
    """w * projection + (1-w) * last season, per game. Players in only one source keep that source's numbers."""
    last, proj = last.set_index(last.PLAYER_NAME.map(norm)), proj.set_index(proj.PLAYER_NAME.map(norm))
    cols = ["MIN"] + RAW
    both = last.index.intersection(proj.index)
    out = pd.concat([last.drop(both), proj.drop(both)])
    mixed = last.loc[both].copy()
    mixed[cols] = w * proj.loc[both, cols] + (1 - w) * last.loc[both, cols]
    return pd.concat([out, mixed]).reset_index(drop=True)


def value(df, teams=12, roster=13, min_gp=20):
    """Adds a z-score per category + Z (9-cat total) + FP (points per game).

    Means/stds come from the draftable pool (teams*roster players by minutes), so z=0 is a typical rostered player.
    FG%/FT% are volume-weighted: (makes - pool% * attempts), so a 60% shooter on 2 shots barely moves the needle.
    """
    df = df.copy()
    if "GP" in df:
        df = df[df.GP.fillna(min_gp) >= min_gp]
    pool = df.nlargest(teams * roster, "MIN")
    for pct, m, a in [("FG%", "FGM", "FGA"), ("FT%", "FTM", "FTA")]:
        rate = pool[m].sum() / pool[a].sum()
        df[pct] = df[m] - rate * df[a]
        pool = pool.assign(**{pct: pool[m] - rate * pool[a]})
    for c in CATS:
        df["z_" + c] = (df[c] - pool[c].mean()) / pool[c].std()
    df["z_TOV"] *= -1
    df["Z"] = df[["z_" + c for c in CATS]].sum(axis=1)
    df["FP"] = sum(df[c] * w for c, w in POINTS.items())
    df["key"] = df.PLAYER_NAME.map(norm)
    df = df.sort_values("Z", ascending=False).reset_index(drop=True)
    return df, replacement(df, teams * roster)


def replacement(df, n):
    """Waiver-wire level: average of the ~10 players just outside the rostered pool."""
    cols = ["z_" + c for c in CATS] + ["Z", "FP"]
    return df.nlargest(n + 10, "Z").tail(10)[cols].mean()


def find(df, name):
    hit = df[df.key == norm(name)]
    if hit.empty:
        close = difflib.get_close_matches(norm(name), df.key, n=1, cutoff=0.75)
        if not close:
            sys.exit(f"Player not found: {name!r}")
        hit = df[df.key == close[0]]
    return hit.iloc[0]


def analyze(df, repl, give, get, mode="9cat"):
    """Net change for YOUR team. Uneven trades: receiving an extra player costs a drop (replacement level),
    sending an extra player frees a spot you fill from waivers (+replacement level)."""
    give = [find(df, n) for n in give]
    get = [find(df, n) for n in get]
    cols = ["z_" + c for c in CATS] + ["Z", "FP"]
    sides = lambda ps: pd.DataFrame(ps)[cols].sum() if ps else pd.Series(0.0, index=cols)
    net = sides(get) - sides(give) - (len(get) - len(give)) * repl
    key = "Z" if mode == "9cat" else "FP"
    return {"give": give, "get": get, "net": net, "verdict": "ACCEPT" if net[key] > 0 else "DECLINE", "key": key}


def report(r):
    show = lambda ps: "\n".join(f"  {p.PLAYER_NAME:<26} Z {p.Z:+5.2f}   FP/G {p.FP:5.1f}" for p in ps)
    lines = [f"You give:\n{show(r['give'])}", f"You get:\n{show(r['get'])}", "", "Category change (z, per game):"]
    lines += [f"  {c:<5} {r['net']['z_' + c]:+.2f}" for c in CATS]
    lines += ["", f"Net 9-cat Z: {r['net']['Z']:+.2f}   Net FP/G: {r['net']['FP']:+.1f}",
              f"Verdict ({r['key']}): {r['verdict']}"]
    return "\n".join(lines)


def explain(text, mode):
    """Optional AI read on the numbers. Needs ANTHROPIC_API_KEY (or `ant auth login`)."""
    import anthropic
    resp = anthropic.Anthropic().beta.messages.create(
        model="claude-opus-5-5",
        max_tokens=16000,
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content":
                   f"Fantasy basketball ({mode} league) trade analysis from a z-score model:\n\n{text}\n\n"
                   "Give a short verdict: who wins, which categories I gain/lose, and whether this fits a punt "
                   "strategy. Mention injury/role/age risk you know about. Then write a 2-sentence casual message "
                   "I could send the other manager to pitch it. Plain text, no headers."}],
    )
    if resp.stop_reason == "refusal":
        return "(AI declined to answer)"
    return "".join(b.text for b in resp.content if b.type == "text")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--give", default="", help="comma-separated players you send")
    ap.add_argument("--get", default="", help="comma-separated players you receive")
    ap.add_argument("--mode", choices=["9cat", "points"], default="9cat")
    ap.add_argument("--proj", help="projections CSV with PLAYER_NAME,MIN," + ",".join(RAW) + " (per game)")
    ap.add_argument("--proj-weight", type=float, default=0.6, help="weight on projections vs last season")
    ap.add_argument("--teams", type=int, default=12)
    ap.add_argument("--roster", type=int, default=13)
    ap.add_argument("--explain", action="store_true", help="ask Claude for a verdict + pitch message")
    ap.add_argument("--top", type=int, help="just print the top N players by value and exit")
    a = ap.parse_args()

    stats = load_stats()
    if a.proj:
        stats = blend(stats, pd.read_csv(a.proj), a.proj_weight)
    df, repl = value(stats, a.teams, a.roster)
    if a.top:
        print(df.head(a.top)[["PLAYER_NAME", "Z", "FP"] + ["z_" + c for c in CATS]].round(2).to_string())
        return
    split = lambda s: [n.strip() for n in s.split(",") if n.strip()]
    text = report(analyze(df, repl, split(a.give), split(a.get), a.mode))
    print(text)
    if a.explain:
        print("\n--- AI take ---\n" + explain(text, a.mode))


if __name__ == "__main__":
    main()
