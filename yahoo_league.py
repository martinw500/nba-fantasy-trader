"""Dump every roster in your Yahoo league so you can see who to trade with.

Setup (one time):
  1. https://developer.yahoo.com/apps/create -> Fantasy Sports: Read, redirect URI https://localhost:8080
  2. Put YAHOO_CONSUMER_KEY=... and YAHOO_CONSUMER_SECRET=... in .env next to this file
  3. python yahoo_league.py <league_id>   (league id is the number in your Yahoo league URL)
     First run opens a browser to authorize; the token is saved to .env.
"""
import sys
from pathlib import Path

from yfpy.query import YahooFantasySportsQuery

import trade


def rosters(league_id):
    q = YahooFantasySportsQuery(league_id, game_code="nba", env_file_location=Path(__file__).parent,
                                save_token_data_to_env_file=True)
    return {str(t.name): [p.full_name for p in q.get_team_roster_player_info_by_week(t.team_id)]
            for t in q.get_league_teams()}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    df, _ = trade.value(trade.load_stats())
    for team, players in rosters(sys.argv[1]).items():
        rows = [trade.find(df, n) for n in players if not df[df.key == trade.norm(n)].empty]
        z = sum(r.Z for r in rows)
        print(f"\n{team}  (total Z {z:+.1f})")
        for r in sorted(rows, key=lambda r: -r.Z):
            print(f"  {r.PLAYER_NAME:<26} Z {r.Z:+5.2f}  FP/G {r.FP:5.1f}")
