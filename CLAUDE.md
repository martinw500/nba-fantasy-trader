# nba-fantasy-trader (Fantasy GM)

Local Yahoo NBA fantasy GM app. Python 3.13 + pandas backend, one-file vanilla HTML/JS UI. User-facing docs and every workflow: README.md. **What's untested and what to build next: ROADMAP.md**, whose **pathway** at the top is the one ordered list of open work. Read it before starting new work and tick items off (in the pathway and under the item's brief) as they're done. Everything built before Nov 16 is in `docs/HISTORY.md` (410 KB: grep an item's ID, never load it whole).

## AI GM notes
The AI GM chat can't change code; it writes what Martin flags (wrong numbers, missed rules, feature asks) to `data/notes.md` with `model.py note --text` (`model.note()`), and can close one it wrote by mistake (`note --close N --text why`: ticked with its reason, never deleted). **Before new work, read the open ones (`- [ ]`)** and tell Martin what's there. When one is handled, tick it (`- [x]`) and add the date and what changed; if it's not a bug, say why on the line.

## Read before you change code
The detail lives in `docs/`, moved out of this file on Nov 15 so it isn't loaded into every session. Nothing was dropped. **Read the one that covers what you're about to touch before editing**, and add what you learn there, not here:
- **`docs/MODEL.md`**: before any change to `model.py` or `backtest.py`, and before explaining how a number is computed. Every model rule, the constants and what they were fitted on, and the bug each rule came from, in nine topics (contents at its top): the matchup model, projections and values, who plays this week, teammates' injuries, IL slots and open spots, trades and the market, this week's moves and the weeks ahead, playoff odds, what-ifs.
- **`docs/FILES.md`**: before changing `app.py`, `yahoo.py`, a cache or the page's data flow. Each file in full: caches and their refresh windows, the app's threads and endpoints, the snapshot import, the AI GM's two paths.
- **`docs/COMMANDS.md`**: every `backtest.py` mode (what it replays, which constants it fits, how long it takes) and every `model.py` command with its flags.

## Files
- `model.py`: the engine (`League` class) plus a JSON CLI. All numbers come from here: UI, AI chat and the `/gm` skill. It also fetches and caches the NBA schedule, ESPN's injuries, projections and news, box scores and live scores (`load()`).
- `yahoo.py`: Yahoo's API → `data/league.json` + `snapshots/yahoo-<day>.json` (`python yahoo.py`; `check` diffs a pull against the newest text snapshot). Text snapshots (`import <file>.txt`, `demo`) are the fallback, loaded by hand (`import`, then Refresh; nothing watches the folder since Y3).
- `app.py`: stdlib server on 127.0.0.1:8765, routes in `api()`; `live()` refetches injuries and live scores and pulls Yahoo every 15 min (rebuilds only on news, `yahoo.NEWS()`), `watch()` loads new snapshots and what-ifs.
- The AI GM chat: `/api/chat` runs `claude -p` on Martin's Claude Code login with the gm SKILL.md and only `model.py` allowed (no API-key path since Nov 19).
- `backtest.py`: replays real NBA seasons and the real league's snapshots; the evidence behind every fitted constant.
- `web/index.html`: the whole UI. State in `S`, views are `view*()` functions returning HTML strings, event delegation via `data-act` / `data-view` / `data-punt`.
- `test_model.py`: the self-check (synthetic data, no network).

## Commands
Python lives in `.venv` (Homebrew Python blocks global pip): use `.venv/bin/python` or `source .venv/bin/activate`.
- `python test_model.py`: run it after touching `model.py` or `app.py`'s model calls. Prints `ok`.
- `python backtest.py`: calibration check (~1 min). Run it after changing projections or odds maths. Its other modes (`replay`, `teammates`, `ahead`, `returns`, `gameday`, `league`, `lineups`, `playoffs`, `season`, ...): `docs/COMMANDS.md`.
- `python app.py --no-browser`: run the app without opening a tab. `#view` and `?punt=FT%25,TOV` deep-link a view and build.
- `python model.py {team|trade|ideas|search|waivers|movers|roster|week|strategy|news|compare|schedule|move|sources|wants|lineup|ahead|what|note|check|drop|today|recap|playoffs} [--punt ...]`: prints JSON; flags in `docs/COMMANDS.md`. `strategy` takes ~45 s.

## Rules that hold everywhere
The short list. Each has its full rule, numbers and history in `docs/MODEL.md`.
- Every number comes from `model.py`. The page, the AI chat and the gm skill never compute one.
- After changing projections, availability or odds maths, rerun `python backtest.py` and keep the calibration tables near the diagonal; standings on the real league stay about 36–59% as categories. Write what you measured in ROADMAP.md / AUDIT.md, shipped or not.
- `backtest.py` mirrors the model: `projections()`, `replay()`, `mondays()`, `season_parts()` / `season_line()`, `lifted()` and its schedule mode repeat `ros()`, `recent()`, `had()`, `lifts()` and `fresh_plays()`. Change them together. `season_sim()` is shared: keep it the one place the playoff maths lives.
- Deltas are win-% points. Win-% cutoffs (`TIERS`, `VERDICT`, `MOVE`, `GUARD`, `accept()`) are in One Win terms, scaled by `League.s`. z-scores (`value()`) are only for player tables and picking candidates.
- `effective()` is the only right way to weight value by availability (values are mostly negative, so `value * avail` ranks an injured player above a healthy one).
- Punts concede the category for your side only; opponents and This week always use real odds.
- Trade acceptance runs on the market (`League.market`), never the model's own values. Hot & cold's `hot_market` never leaks into trades.
- `trade()` rejects players you don't own or from more than one team (KeyError): AI tool input comes through it. `_after()` and `search()` do the same roster maths; `test_model.py` checks they agree.
- Player lookup goes through `League.find()`, punts through `punts()`.
- Lifts only count while the injured player sits; `ros()` reads this season net of `had()` so a boost isn't counted twice.
- What-ifs and saved roster moves (`data/whatif.json`; moves go on the snapshot in `load(made=)` before the league is built) are read by `app.py` and `model.py`'s CLI only, never by tests or backtests.
- Keep `model.py`'s CLI output under ~30 KB: the in-app chat sees a 2 KB preview of anything bigger (`brief()` cuts `news` and `strategy`).
- `SEASON`s are `OLDER` / `LAST` / `CUR` at the top of `model.py`; bump them each October.

## Gotchas
- `nba_api` names have accents (Jokić). Windows consoles need `sys.stdout.reconfigure(encoding="utf-8")`, which the CLIs already do.
- Yahoo's API (live since Nov 23) differs from its docs: made/attempted stats are "FGM/A" / "FTM/A" (`yahoo._pair()`), the snapshots' Cur rank is `sort_type=average_season` and Pre is the `OR` rank, and it has no projections. Verify field paths with `python yahoo.py check` (raw XML in `data/yahoo_raw/`).
- Browser checks go through the Playwright MCP, which runs headless Microsoft Edge (`PLAYWRIGHT_MCP_BROWSER=msedge` in `~/.claude/settings.json`; Chrome isn't installed). If it fails to start, tell Martin instead of working around it.
- Headless Chromium browsers clamp windows to 504px wide. For phone screenshots, load the app in a 390px iframe.
- Project skills added mid-session don't register until Claude Code restarts.
- The in-app AI GM chat (`claude -p`) sees only a 2 KB preview of any command output over ~30 KB and can't open the saved file. Keep `model.py`'s JSON under that: `main()` prints it unindented, a trade's player cards last (Q24). `brief()` cuts `news` (53–104 KB → ≤27 KB: your players' items whole, a teammate's latest headline) and `strategy` (82 → ~20 KB: short buy / sell rows, plans for the best 8 builds, balanced and the picks) for the CLI only (Nov 11); the app's views keep everything. Rerun the sizes after adding fields to a command's output.

## UI / design
- Follow DESIGN.md (tokens, rules) and PRODUCT.md. Impeccable's surface brief is `.impeccable/surfaces/web-index-html.md`.
- Signature move: punt dimming (`.punted-col` / `.cat.punted`). Every new stat table must honour it.
- Use `/impeccable` for design changes and `humanizer` for any user-facing prose pitched to other managers.
