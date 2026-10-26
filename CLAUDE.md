# nba-fantasy-trader

Yahoo NBA fantasy trade analyzer. Python 3.13, pandas.

- `trade.py` — everything: stats fetch (nba_api, cached in `data/`), projection blend, 9-cat z-scores + points, trade eval, optional Claude verdict (`--explain`).
- `yahoo_league.py` — pulls league rosters via yfpy; needs `YAHOO_CONSUMER_KEY/SECRET` in `.env`.
- `test_trade.py` — run `python test_trade.py`; uses fake data, no network.

## Conventions
- Keep it in few files; add a module only when `trade.py` gets genuinely unwieldy.
- Player lookup always goes through `trade.find()` (handles accents, Jr/III, typos).
- Z-scores are per game vs the rostered pool (`teams*roster` by minutes). TOV is inverted. FG%/FT% are volume-weighted.
- Uneven trades are adjusted by replacement level (`replacement()`), don't remove that.
- `POINTS` weights = Yahoo default; change them to match the league.
- `SEASON` is last season; bump it each year. Delete `data/` to refetch.

## Writing
- Use the `humanizer` skill (`.claude/skills/humanizer`) on any trade pitch text sent to other managers.
