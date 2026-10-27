# Roadmap

What works, what's untested, and what to build next, in priority order. Each task lists where the data comes from, roughly what to change, and when it counts as done.

To pick this up in Claude Code: *"Read ROADMAP.md and do Trade lab T3"*.

---

## Where things stand (Oct 26, 2026)

**Working and tested (on the demo league):**
- Web app with My team, League, Trades, Waivers, Hot & cold and AI GM views (`python app.py`).
- 9-cat matchup model: weekly win odds, projected standings, odds per category, trade, waiver and finder scoring, punts.
- Points-league mode (simpler: per-game values, no matchup model).
- `python test_model.py` passes, and every view was clicked through in a real browser.

**Built but never run for real:**
- [ ] **Yahoo pull** (`python yahoo.py`). Written against yfpy 17's documented fields, but never run against a real league. Expect small fixes on the first run (see "First real Yahoo pull" below).
- [ ] **AI GM chat** (`ai.py`). Needs an `ANTHROPIC_API_KEY`. The tool definitions build correctly, but no real conversation has run. `/gm` in Claude Code is the no-key alternative.

**Known gaps in the model:** listed under "Model accuracy" below.

---

## For you (not code)

1. **Move to the new computer.** See README → "Moving to a new computer".
2. **Connect Yahoo** (README → "Connect your Yahoo league"), then run `python yahoo.py`.
3. **Sanity-check your real team.** Does the projected finish look plausible? Do the punt suggestions match your gut? Do the waiver adds make sense? Write down anything that looks wrong. That's the best calibration data available.
4. Optional: add `ANTHROPIC_API_KEY` to `.env` for the in-app chat.

### First real Yahoo pull: likely fixes

When `python yahoo.py` runs against a real league for the first time, check these spots in `yahoo.py`:
- `text(t.name)`: team names can come back as bytes. This is handled, but verify names look right.
- The free-agent query (`players;status=A;sort=AR`): confirm the list isn't empty and has real names. The result shape may be `{"player": Player}` dicts or `Player` objects; `pl()` handles both.
- `s.stat_categories.stats` mapping: confirm `settings.cats` comes out as the 9 categories (`FG%, FT%, FG3M, PTS, REB, AST, STL, BLK, TOV`). If your league uses other categories (e.g. 3P%, DD), they're ignored for now, so add them to `YAHOO_STAT` / `CATS`.
- `roster` size: it sums roster slots excluding IL/IL+/NA. Check it matches your league.
- `me`: comes from `is_owned_by_current_login`. If it's `None`, the wrong Yahoo account is logged in.
- Player-name matching: Yahoo names that don't match NBA.com names are silently skipped by `League._keys()`. Print any misses on the first run and add aliases if needed.

---

## Trade lab (requested Oct 26, 2026, build first)

Decided with Martin: real league is **H2H 9-cat**. "Difficulty" = how likely they say yes. Searches go **up to 3-for-3**. One **workbench** where the builder's checkboxes are the finder's constraints: check their player and Find = offers for him; check yours = buyers; both = complete the trade; none = open search. Points mode keeps today's `ideas()` and gets none of this.

### T1. Engine: `League.search()` ✅ done Oct 26 (branch `trade-lab`)

Built as planned, plus:
- `find()` takes a unique last name ("Jokic").
- The demo league now runs a few weeks of waiver moves. Before that, its free agents beat every team's 13th man, so any trade that freed a roster spot looked great.
- An open size-3 search takes about 2s on the demo league.
- Known limit: open spots are filled with the best free agents for both teams, so a trade still gets credit for a waiver pickup you could make anyway.

**Why:** the finder only sees your top 10 × their top 10, at most 2-for-2, with two fixed modes. You can't target a player, lock one, or pick a difficulty.

**Change (`model.py`):**
- `search(give=(), get=(), lock=(), team=None, size=3, tier="fair", goal="win", goal_cats=(), goal_team=None, healthy_only=False, shape=None, punt=(), limit=30, depth=10)`. Same vectorized maths as `_ideas_matchup`, which it replaces. `ideas(mode)` becomes a thin wrapper (fair → `tier="fair"`, finesse → `tier="finesse"`), so the CLI, `ai.py` and `/gm` keep working.
- Candidates: top `depth` per side, plus pinned players even outside the top 10, minus `lock`. Pinned `get` players fix the team; enforce the same one-team rule as `trade()` (KeyError).
- Combos: 1 to `size` players per side, and every combo must contain all pinned players (filter before stacking vectors). At depth 10 and size 3 that's 175 combos per side.
- Roster fill for uneven deals generalizes from ±1 to ±2: drop the n cheapest, or add the n best FAs. "Cheapest" = least win-% cost, precomputed once per roster (single-player removal). Use it in `trade()._after()` too. This clears the "drop choice" item under Model accuracy.
- `TIERS` constants at the top of the file (starting guesses, tune like the calibration knobs; u = 1 in 9-cat):

  | tier | you (win %) | them (win %) | looks to them |
  |---|---|---|---|
  | easy | > +0.5 | > 0 | > +0.3u |
  | fair | > +1 | > +0.5 | > −u |
  | finesse | > +1.5 | > −5 | > +0.3u |
  | hard | > +3 | > −10 | > −u |

- Every result gets an `accept` label: **Likely yes** (looks > +0.3u and them > −1), **Long shot** (looks < −0.5u), otherwise **Maybe**. The same helper labels `trade()` results.
- `goal`: `win` (average over the league), `cats` (average odds in `goal_cats`; skip deals that lose more than 1% win odds), or `team` (head-to-head vs `goal_team`, e.g. a playoff rival). Sort by the goal; fair still sorts by min(you, them).
- Filters: `healthy_only` skips incoming players with OUT statuses. `shape="consolidate"` (you give more players) or `"depth"` (you get more). The 3-per-team cap applies only when no `get` is pinned.
- CLI: `python model.py search --get "Wembanyama" --tier easy --lock "Jokic"`. API: `/api/search` in `app.py` (add to `API`).

**Done when:**
- `test_model.py` covers it:
  - a pinned player appears in every result
  - a locked player never appears
  - each tier's thresholds hold
  - 3-player combos come up
  - the drop choice picks the lowest win-% cost
- An open size-3 search on the demo league takes under ~5s.

### T2. "What changes" breakdown ✅ done Oct 26 (branch `trade-lab`)

Built as planned, plus:
- `cats_detail`, `vs` and `standings` use win-% numbers (0–100), like `win` / `win_after`. Standings rows carry `rival: true` for the flagged teams.
- Toggling a punt chip now re-runs the evaluated trade instead of clearing it.
- The category table replaces the old odds strip on a 9-cat result.
- Known limits: early in the season every incoming player trips the "GP < 10" flag. On phones the 5-number verdict row is cramped (T3's `/impeccable polish` should fix it). The position check is untested until a real Yahoo pull, since the demo has no `pos`.

**Why:** today a result shows only your category odds deltas. You can't see their side, totals, who you now beat, or whether you just made a rival better.

**Change:** `trade()` already calls `outlook()` for both teams; return what it throws away:
- `cats_detail`: per category, for both teams, weekly total before → after (FG%/FT% as %) and odds before → after.
- `vs`: your head-to-head odds vs every team, before → after.
- `standings`: every team's rank and win %, before → after. Flag when the partner or a top-4 team gains more than you.
- `roster_after` for both teams (drops and adds marked).
- `flags`:
  - incoming player injured, `avail` < 0.7, or GP < 10
  - buying at peak hype (incoming `value_now − value` > 0.5)
  - a position left empty, when Yahoo `pos` data exists
- `build_after`: categories you'd win under 30% of weeks after the trade (the "consider punting" hint).
- `accept` label (T1 helper).

**UI:**
- A category table with you and them columns, both before → after. Punted rows dimmed (signature move).
- Head-to-head movers, then standings movers.
- `playerTable` for the give and get sides, plus the flags.

**Done when:** every section renders on the demo league, and toggling a punt chip dims the new tables.

### T3. Trades workbench UI ✅ done Oct 26 (branch `trade-lab`)

Built as planned, plus:
- Layout: builder → finder → evaluated result → Improve (right under the verdict) → rest of the breakdown → Saved trades.
- The finder keeps an "All teams" filter, as the old finder did. It's locked to the partner while one of their players is checked.
- Choosing "Categories…" pre-selects your two weakest non-punted categories. "Beat a team…" pre-selects the top-ranked other team.
- Deep-link names go through `find()`, so `#trades?give=Tatum&get=Wembanyama` works and the URL is rewritten with full names. An ambiguous last name (e.g. "Wiggins") shows the server's error toast.
- `/api/players` now returns `{name, team}` for rostered players only (no FAs), which feeds the datalist.
- Result tables scroll inside a ~10-row box. On phones, the verdict pairs wrap instead of squeezing into 5 columns (the T2 known limit).
- Known limits: Improve results stay listed after you Load one; a note says which deal they were built from. Saved trades are keyed by player names, so a trade-away or the October season bump turns them into "can't evaluate" columns (Remove clears them).

**Change (`web/index.html`, follow DESIGN.md, no modals, then `/impeccable polish`):**
- **Toolbar:**
  - Difficulty segmented control (Easy / Fair / Finesse / Hard)
  - Optimize for (Win % / Categories… / Beat a team…)
  - Max size, Healthy only, Shape
- **Builder:**
  - Lock toggle on every player (`store` key `locks`).
  - "Find any player" input: a native `<datalist>` fed by the existing (unused) `/api/players`. Picking a name switches the team and checks him.
- **Find:**
  - Reads the checked players as pins.
  - The panel title says what it's doing: "Offers for X", "Buyers for Y", "Complete this trade" or "Open search".
  - Results: sortable columns, accept label, Load, Save.
- **Improve this deal** (after Evaluate):
  - Throw-ins: the current deal pinned, size + 1.
  - Swaps: each player unpinned in turn.
- **Saved trades** (`store` key `saved`): side-by-side compare (you, them, looks, rank, accept, category deltas). Re-evaluate on open, since saved numbers go stale after a refresh.
- **Deep links:** `#trades?give=A,B&get=C`, same pattern as `?punt=`.
- **Wiring:** "Find a buyer" (My team) and buy-low rows (Hot & cold) run a pinned search instead of just preloading the builder.

**Done when:** every flow has been clicked through in a real browser, at desktop width and at 390px (iframe trick).

### T4. AI GM and extras

- ✅ done Oct 26: `find_trades` replaces `trade_ideas` (points leagues fall back to `ideas()`). `evaluate_trade` returns the T2 breakdown with `roster_after` cut to name/value/move (~5k chars, down from ~13k). Tools tested offline; a live chat still needs an API key (see "Built but never run for real").
- Originally: `ai.py`: replace `trade_ideas` with `find_trades(give, get, lock, tier, goal, goal_cats, goal_team, size)`; `evaluate_trade` returns the T2 breakdown. Document `model.py search` in `.claude/skills/gm/SKILL.md`.
- Later: playoff mode, which weights likely playoff opponents' weeks (needs task 1's schedule).
- Later: manager tendencies from Yahoo trade history (needs task 3's transactions). This is a weak signal, since rejected offers aren't visible.
- Not doing: 3-team trades. Yahoo doesn't support them.

---

## Build next (highest impact for 9-cat first)

### 1. This week's NBA schedule

**Why:** H2H is weekly. A player with 4 games this week is worth about twice one with 2 games. The model currently assumes 3.4 games for everyone.

**Data:** `nba_api.stats.endpoints.scheduleleaguev2.ScheduleLeagueV2(season="2026-27")` (free, no key, import confirmed working). Yahoo week start/end dates: `YahooFantasySportsQuery.get_game_weeks_by_game_id(game_id)`, plus the league's `current_week` from `get_league_metadata()`.

**Change:**
- `yahoo.py`: store `current_week`, `week_start` and `week_end` in the snapshot.
- `model.py`: fetch and cache the schedule (`data/schedule_2026-27.csv`), and build `games_this_week[team_abbr]` for the remaining days of the current week. Add `TEAM_ABBREVIATION` to `self.p` (it's already in the cached stats).
- New `League.week_vec(keys)` = per-game line × games left this week × availability. Keep `vec()` (season average) for trades.

**Done when:** each player shows "games this week", and the test checks a 4-game player contributes more to `week_vec` than an identical 2-game player.

### 2. Current matchup ("This week" view)

**Why:** this unlocks advice like "you're up 5–4; you're losing STL by 3, so stream a steals guy, and don't chase PTS."

**Data (Yahoo API, already in yfpy):** `get_team_matchups(team_id)` or `get_league_scoreboard_by_week(week)` gives this week's opponent and each team's category totals so far.

**Change:**
- `yahoo.py`: save opponent name, current totals for both teams (FGM/FGA/FTM/FTA plus counting stats), and the week dates.
- `model.py`: new `League.this_week()`. Projected final = current totals + `week_vec` for the games left. Per-category odds use the existing `cat_probs` (the variance only covers remaining games). Matchup odds use `match_prob`.
- Streaming: for each free agent, the gain in this-week matchup odds if added (drop your weakest), weighted by games left. Show the top 5.
- `web/index.html`: a "This week" view or panel on My team showing the category score, odds per category, "chase / safe / lost" labels, and streaming adds.
- `ai.py` + `/gm`: add a `this_week` tool.

**Done when:** the view shows the real opponent and live category totals, and the streaming list changes when a free agent has more games.

### 3. Injury notes, ownership %, transactions, real standings

**Why:** cheap context the API already has.

**Data (yfpy `Player` fields and methods, confirmed to exist):** `injury_note`, `status_full`, `percent_owned_value`; `get_league_transactions()`; `get_league_standings()`.

**Change:**
- `yahoo.py` `pl()`: add `injury`, `status_full` and `owned` fields. Snapshot `transactions` (last ~30) and `standings` (W-L-T, rank).
- UI:
  - Hover an injury tag to see the note.
  - "Owned %" column in Waivers.
  - "Recently dropped" list (good players just released).
  - League view shows actual record next to projected.

**Done when:** all four show up on a real league pull.

### 4. Bring back projections import

**Why:** expert ROS projections (Hashtag Basketball, FantasyPros) beat a last-season blend, especially for rookies and role changes. (The first draft had this as `trade.py --proj`; it was dropped in the engine rewrite.)

**Change:** `model.py`: optional `data/projections.csv` with columns `PLAYER_NAME, MIN, FGM, FGA, FTM, FTA, FG3M, PTS, REB, AST, STL, BLK, TOV` (per game). When present, use it as `base` in `ros()` for players it covers (keyed with `keyed()`). Mention it in the README.

**Done when:** a test with a fake projections file changes a player's ROS line, and players missing from the file fall back to last season.

### 5. Injury return dates (needs scraping)

**Why:** availability currently comes from games-played history plus Yahoo's tag. "Out 4–6 weeks" should mean 0 games for 4–6 weeks, then normal.

**Data:** ESPN's public JSON (reportedly `site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries`, which needs verifying) or Rotowire's injury page. Check each site's terms first, cache results, and refresh at most a few times a day.

**Change:** `injuries.py` fetches `{player: expected_return_date}`. Availability for the rest of the season becomes the share of remaining games after the return date, times the history-based rate. For this week (task 1), it's 0 before the return date.

**Done when:** a player listed "out until X" gets reduced availability and shows the return date in the UI.

---

## Model accuracy

- [ ] **Calibrate the randomness settings** (`GAMES_WEEK`, `DISPERSION`, `SCHED_CV`, `PCT_NOISE`, `FILL`, `STATUS_AVAIL` at the top of `model.py`). They're educated guesses. Best method: pull last season's weekly scoreboards for a real league (`get_league_scoreboard_by_week`), predict each category with `cat_probs` from each team's totals, and tune the settings until predicted win rates match actual ones.
- [ ] **Positions and daily lineup slots.** Trades and adds ignore positions (e.g. you could end up with 6 centers). Yahoo's `roster_positions` is in the settings. Minimum version: warn when a trade leaves a position empty *(done in Trade lab T2, untested on real `pos` data)*. Full version: a games-played penalty when more players have games than slots.
- [ ] **Drop choice in trades** uses the lowest z-score. Better: drop whoever costs the least win %. *(Part of Trade lab T1.)*
- [ ] **Trade finder depth** is your top 10 vs their top 10, up to 2-for-2. 3-for-1 consolidation trades aren't searched. *(Part of Trade lab T1.)*
- [ ] **Hot & cold** uses a fixed hype weight (`HYPE = 5`). Could compare against Yahoo's actual rank (`sort=AR`) instead.

## Later / maybe

- [ ] **Yahoo write actions** (propose trades, add/drop from the app). The Yahoo API supports them with write scope. Kept read-only on purpose; if added, always ask for confirmation first.
- [ ] **Hosting for league mates**: needs a server, a Yahoo login per user, and per-user snapshots.
- [ ] **Keeper/dynasty value**: age curves, multi-season values.
- [ ] **Alerts**: a scheduled Claude Code routine (`/schedule`) that refreshes daily and messages you when a good player is dropped or a sell-high window opens.

## Known small issues

- impeccable's design detector flags the "Segoe UI Variable" font stack (its parser misreads the name). Harmless.
- Headless Chromium/Edge won't go below 504px wide, so phone screenshots need the 390px-iframe trick (see CLAUDE.md).
- The demo league's teams are random. Don't read too much into demo numbers.
