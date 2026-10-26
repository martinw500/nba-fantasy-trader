# Roadmap

What works, what's untested, and what to build next, in priority order. Each task lists where the data comes from, roughly what to change, and when it counts as done.

To pick this up in Claude Code: *"Read ROADMAP.md and do task 1"*, or `/feature-dev` with the task text.

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
- [ ] **Positions and daily lineup slots.** Trades and adds ignore positions (e.g. you could end up with 6 centers). Yahoo's `roster_positions` is in the settings. Minimum version: warn when a trade leaves a position empty. Full version: a games-played penalty when more players have games than slots.
- [ ] **Drop choice in trades** uses the lowest z-score. Better: drop whoever costs the least win %.
- [ ] **Trade finder depth** is your top 10 vs their top 10, up to 2-for-2. 3-for-1 consolidation trades aren't searched.
- [ ] **Hot & cold** uses a fixed hype weight (`HYPE = 5`). Could compare against Yahoo's actual rank (`sort=AR`) instead.

## Later / maybe

- [ ] **Yahoo write actions** (propose trades, add/drop from the app). The Yahoo API supports them with write scope. Kept read-only on purpose; if added, always ask for confirmation first.
- [ ] **Hosting for league mates**: needs a server, a Yahoo login per user, and per-user snapshots.
- [ ] **Keeper/dynasty value**: age curves, multi-season values.
- [ ] **Alerts**: a scheduled Claude Code routine (`/schedule`) that refreshes daily and messages you when a good player is dropped or a sell-high window opens.

## Known small issues

- impeccable's design detector flags the "Segoe UI Variable" font stack (its parser misreads the name). Harmless.
- Headless Chrome won't go below 504px wide, so phone screenshots need the 390px-iframe trick (see CLAUDE.md).
- The demo league's teams are random. Don't read too much into demo numbers.
