# nba-fantasy-trader (Fantasy GM)

Local Yahoo NBA fantasy GM app. Python 3.13 + pandas backend, one-file vanilla HTML/JS UI. User-facing docs and every workflow: README.md. **What's untested and what to build next: ROADMAP.md.** Read it before starting new work and tick items off as they're done.

## Files
- `model.py`: the engine (`League` class) plus a JSON CLI. All numbers come from here: UI, AI chat and the `/gm` skill.
- `yahoo.py`: league snapshot → `data/league.json`. Yahoo API (read-only, yfpy; access now needs Yahoo's approval), `python yahoo.py import <snapshot>.txt ["Team"]` (Instinct-style league snapshot or a hand-typed list, `parse_text`; unknown sections are skipped; Refresh imports the newest `*snapshot*.txt`; the top-bar dropdown switches your team via `/api/me`), or `python yahoo.py demo` (fake league from real stats).
- `model.py` also fetches the NBA schedule (`nba_api`, cached a day), ESPN's injury feed (cached 3h), ESPN's projections (`espn()`, cached a day, archived per day) and this season's box scores (`gamelogs()`, cached 12h, for the player card's recent lines) in `load()`. `League` built directly (tests) gets none of them, so it stays offline.
- `app.py`: stdlib `ThreadingHTTPServer` on 127.0.0.1:8765, routes in `api()`.
- `ai.py`: Claude GM via `client.beta.messages.tool_runner`, tools wrap `League` methods, model `claude-opus-5-5`. Only used when `ANTHROPIC_API_KEY` is set; otherwise `/api/chat` runs `claude -p --safe-mode` (`app.py` `claude_chat`) with the gm SKILL.md as system prompt and only `model.py` allowed, plus `WebSearch` when the tab's Web search is on (Model / Effort / Web search are picked on the AI GM tab, validated by `MODELS` / `EFFORTS`). Edit SKILL.md and both `/gm` and the in-app chat follow.
- `backtest.py`: replays a real NBA season (game logs cached in `data/gamelogs_*.csv`) in 20 simulated leagues and prints predicted vs actual win rates. It's the evidence behind the matchup knobs.
- `web/index.html`: the whole UI. State in `S`, views are `view*()` functions returning HTML strings, event delegation via `data-act` / `data-view` / `data-punt`.

## Commands
Python lives in `.venv` (Homebrew Python blocks global pip): use `.venv/bin/python` or `source .venv/bin/activate`.
- `python test_model.py`: self-check, synthetic data, no network. Run it after touching `model.py`.
- `python backtest.py`: calibration check (~1 min). Run it after changing projections or odds maths. `python backtest.py replay`: rest-of-season error by expert weight (fits `PROJ_W`).
- `python app.py --no-browser`: run the app without opening a tab. `#view` and `?punt=FT%25,TOV` deep-link a view and build.
- `python model.py {team|trade|ideas|search|waivers|movers|roster|week|strategy} [--punt ...]`: prints JSON. `strategy` (the punt planner) takes ~15s.

## Model rules (don't break these)
- 9-cat advice (trade, ideas, waivers, standings) runs on the matchup model: `week` (per-game line × GAMES_WEEK × avail, missed games half-filled at waiver level) → `cat_probs` → `_score` → `outlook()`. `_score` is the share of categories won (`settings.scoring` "categories", the default and Martin's league) or `match_prob` ("one_win"). Deltas are in win-% points, `unit: "win%"`. Win-% cutoffs (`TIERS`, `VERDICT`, `MOVE`, `GUARD`, `accept()`) are in One Win terms, scaled by `League.s` (`CAT_SCALE` 0.4 in categories). z-scores (`value()`) are only for player tables and candidate selection.
- DISPERSION, SCHED_CV and PCT_NOISE are fitted by `python backtest.py` (2025-26 box scores, 20 simulated leagues, predictions from what was known each Monday). Re-run it after changing projections (`baseline()`, `prior()`, `ros()`, availability) and keep the calibration tables near the diagonal. GAMES_WEEK, FILL and STATUS_AVAIL are still guesses. After any change, check standings stay realistic: about 38–61% on the real league as categories (21–77% as One Win; 40–61% and 26–77% before expert projections). The snake-drafted demo is tighter (~47–52%).
- Category odds are calibrated, so cutoffs live in odds terms: `WEAK` (35%) = weak spot / punt hint, and This week's Ahead/Behind calls use the same cutoff.
- Punts concede the category (`_concede` sets your odds to 0) for your side only. Opponents always use real odds.
- `trade()` rejects players you don't own or that come from more than one team (KeyError). Keep that check, since AI tool input comes through it.
- Values are per game, rest of season: `ros()` = minutes (`GP/(GP+MIN_SHRINK)`, fast: roles change) × production per minute (this season's minutes played vs `SHRINK` games of baseline minutes, slow). Both were picked by replaying 2025-26 rest-of-season lines (Oct 27). `ros()` runs on `prior()`: `baseline()` blended with an expert line at `PROJ_W` (0.6, fitted by `python backtest.py replay` on ESPN's 2025-26 preseason projections). The expert line (`experts()`) is Yahoo's from the snapshot where it has the player, else ESPN's. Yahoo has no minutes: they're ESPN's or ours scaled by output. A rookie's baseline is the fringe line. Availability still uses the box-score `baseline()` (rookies 85%).
- "cur"/"Now" = perceived per-game value (`HYPE` shrink). `their_view` uses `perceived()`: that over the games a player plays (`effective()`), floored at waiver level, and an incoming extra player costs the team its weakest players. It drives sell-high and is only a note on trades.
- Trade acceptance runs on the market (N0), never the model's own values: `League.market` = Yahoo Pre/Cur rank blended by `GP/(GP+HYPE)`, re-ranked (model's perceived rank when Yahoo has none, `yahoo` False), valued `100 × MARKET^(rank−1)`. A deal's market (`priced()`) nets out each side's drops and pickups, the same ones the matchup model makes, so `search()` and `trade()` agree. `TIERS` are (your win %, theirs, market gap range); fair also passes with both gaining within `FAIR_FIT`. `accept()` reads the market gap.
- `effective()` is the only right way to weight value by availability: values are mostly negative (relative to the rostered average), so `value * avail` would rank an injured player above a healthy one.
- Uneven trades always subtract `n * replacement()`. Replacement = top-5 FA average.
- 9-cat `trade()` and `search()` score on top of `after_free()`: your `free_moves()` (greedy add/drop or open-spot add while one clears `MOVE`) are made first, so a deal gets no credit for a free pickup. The partner is judged on the trade alone (Martin: managers look at the deal, not their waiver options) unless `their_free=True` (Trades toolbar: "Count their waiver moves"). Your pickups are never offered, and the players a deal sends are never dropped (`keep`). `free=False` scores on today's rosters.
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
