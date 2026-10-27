# nba-fantasy-trader (Fantasy GM)

Local Yahoo NBA fantasy GM app. Python 3.13 + pandas backend, one-file vanilla HTML/JS UI. User-facing docs and every workflow: README.md. **What's untested and what to build next: ROADMAP.md.** Read it before starting new work and tick items off as they're done.

## Files
- `model.py`: the engine (`League` class) plus a JSON CLI. All numbers come from here: UI, AI chat and the `/gm` skill.
- `yahoo.py`: league snapshot → `data/league.json`. Yahoo API (read-only, yfpy; access now needs Yahoo's approval), `python yahoo.py import <snapshot>.txt "Team"` (Instinct-style league snapshot or a hand-typed list, `parse_text`; Refresh re-reads the file; the top-bar dropdown switches your team via `/api/me`), or `python yahoo.py demo` (fake league from real stats).
- `model.py` also fetches the NBA schedule (`nba_api`, cached a day) and ESPN's injury feed (cached 3h) in `load()`. `League` built directly (tests) gets neither, so it stays offline.
- `app.py`: stdlib `ThreadingHTTPServer` on 127.0.0.1:8765, routes in `api()`.
- `ai.py`: Claude GM via `client.beta.messages.tool_runner`, tools wrap `League` methods, model `claude-opus-5-5`.
- `web/index.html`: the whole UI. State in `S`, views are `view*()` functions returning HTML strings, event delegation via `data-act` / `data-view` / `data-punt`.

## Commands
Python lives in `.venv` (Homebrew Python blocks global pip): use `.venv/bin/python` or `source .venv/bin/activate`.
- `python test_model.py`: self-check, synthetic data, no network. Run it after touching `model.py`.
- `python app.py --no-browser`: run the app without opening a tab. `#view` and `?punt=FT%25,TOV` deep-link a view and build.
- `python model.py {team|trade|ideas|search|waivers|movers|roster|week} [--punt ...]`: prints JSON.

## Model rules (don't break these)
- 9-cat advice (trade, ideas, waivers, standings) runs on the matchup model: `week` (per-game line × GAMES_WEEK × avail, missed games half-filled at waiver level) → `cat_probs` → `match_prob` → `outlook()`. Deltas are in win-% points, `unit: "win%"`. z-scores (`value()`) are only for player tables and candidate selection.
- DISPERSION, SCHED_CV and PCT_NOISE are fitted by `python backtest.py` (2025-26 box scores, 20 simulated leagues, predictions from what was known each Monday). Re-run it after changing projections (`baseline()`, `ros()`, availability) and keep the calibration tables near the diagonal. GAMES_WEEK, FILL and STATUS_AVAIL are still guesses. After any change, check demo standings stay in a realistic ~25–70% win range.
- Category odds are calibrated, so cutoffs live in odds terms: `WEAK` (35%) = weak spot / punt hint, and This week's Ahead/Behind calls use the same cutoff.
- Punts concede the category (`_concede` sets your odds to 0) for your side only. Opponents always use real odds.
- `trade()` rejects players you don't own or that come from more than one team (KeyError). Keep that check, since AI tool input comes through it.
- Values are per game, rest of season: `ros()` blends last season → this season with `GP/(GP+SHRINK)`. Rookies start from a fringe line.
- "cur"/"Now" = perceived per-game value (`HYPE` shrink). `their_view` uses `perceived()`: that over the games a player plays (`effective()`), floored at waiver level, and an incoming extra player costs the team its weakest players. This drives finesse trades and sell-high.
- `effective()` is the only right way to weight value by availability: values are mostly negative (relative to the rostered average), so `value * avail` would rank an injured player above a healthy one.
- Uneven trades always subtract `n * replacement()`. Replacement = top-5 FA average.
- `build()` = a team's weak spots (categories it wins <35% of weeks, `WEAK`). Points-mode trades value opponents on it.
- Player lookup always goes through `League.find()` (accents, Jr/III, typos).
- `SEASON`s are `OLDER`/`LAST`/`CUR` at the top of `model.py`; bump them each October. The baseline blends `LAST` and `OLDER` (`baseline()`). Delete `data/stats_*.csv` to refetch.

## Gotchas
- `nba_api` names have accents (Jokić). Windows consoles need `sys.stdout.reconfigure(encoding="utf-8")`, which the CLIs already do.
- `yahoo.py` hasn't been run against a real league yet. yfpy field access is defensive, but verify the first real pull.
- Browser checks go through the Playwright MCP, which runs headless Microsoft Edge (`PLAYWRIGHT_MCP_BROWSER=msedge` in `~/.claude/settings.json`; Chrome isn't installed). If it fails to start, tell Martin instead of working around it.
- Headless Chromium browsers clamp windows to 504px wide. For phone screenshots, load the app in a 390px iframe.
- Project skills added mid-session don't register until Claude Code restarts.

## UI / design
- Follow DESIGN.md (tokens, rules) and PRODUCT.md. Impeccable's surface brief is `.impeccable/surfaces/web-index-html.md`.
- Signature move: punt dimming (`.punted-col` / `.cat.punted`). Every new stat table must honour it.
- Use `/impeccable` for design changes and `humanizer` for any user-facing prose pitched to other managers.
