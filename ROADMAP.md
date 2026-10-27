# Roadmap

What works, what's untested, and what to build next, in priority order. Each task lists where the data comes from, roughly what to change, and when it counts as done.

To pick this up in Claude Code: *"Read ROADMAP.md and do P1"*. Work through **Do first** (P1–P4) before **Next up**. Martin's league decisions are under "For you" → **Decisions**.

---

## Where things stand (Oct 26, 2026)

**Working and tested (on the demo league):**
- Web app with My team, This week, League, Trades, Waivers, Hot & cold and AI GM views (`python app.py`).
- 9-cat matchup model: weekly win odds, projected standings, odds per category, trade, waiver and finder scoring, punts.
- Points-league mode (simpler: per-game values, no matchup model).
- `python test_model.py` passes, and every view was clicked through in a real browser.

**Built but never run for real:**
- [ ] **Yahoo pull** (`python yahoo.py`). Written against yfpy 17's documented fields, but never run against a real league. Expect small fixes on the first run (see "First real Yahoo pull" below). **Blocked (Oct 26):** since summer 2026, Yahoo grants Fantasy API access only to applications it approves (sports.yahoo.com/developer/access), with no published timeline, and old apps reportedly get 403s. Workaround in use: Instinct's text snapshots. `data/league.json` holds the Oct 26 import (`python yahoo.py import mchc-fantasy-snapshot-2026-10-26.txt`: 167 of 168 players, 213 free agents, AJ Dybantsa left out with no NBA games yet).
- [x] **Oct 26 snapshot imported** (P1, Oct 27): `mchc-fantasy-snapshot-2026-10-26.txt`, re-pulled Oct 26, 11:37 PM MST, after every game was final. League settings, Pre/Cur/Ros ranks, this week's matchups, 30 transactions, Yahoo projections, adds used, waiver priority and the season schedule are all in `data/league.json`.
- [ ] **AI GM chat** (`ai.py`). Needs an `ANTHROPIC_API_KEY`. The tool definitions build correctly, but no real conversation has run. `/gm` in Claude Code is the no-key alternative.

**Known gaps in the model:** listed under "Model accuracy" below.

---

## For you (not code)

1. **Sanity-check the Oct 26 import** (done in P1, see item 3). Instinct answered the asks below (Oct 26, 11:37 PM file).
2. **Apply for Yahoo API access** (sports.yahoo.com/developer/access, personal single-league use), since approval has no timeline. Then follow README → "Connect your Yahoo league".
3. **Sanity-check your real team** after P1. Does the projected finish look plausible? Do the punt suggestions match your gut? Do the waiver adds make sense? Write down anything that looks wrong. That's the best calibration data available.
4. Optional: add `ANTHROPIC_API_KEY` to `.env` for the in-app chat.

(Moving to the new computer is done: the repo runs on the Mac from `.venv`.)

### Decisions (Martin, Oct 26)

1. **Scoring: Head-to-Head – Categories** (P2). Each category won counts in the standings.
2. **Bench: 3 slots**, so 13 active (10 starters + 3 BN) plus 3 IL (P1). The 11:37 PM file confirms it (`Roster slots: ... (10 starters) + 3 BN + 3 IL = 16`). Eleven teams carry 13 active players, so a pickup needs a drop. **Birds that I hate has 12** (2 BN), an open spot (P3). Mathmagicians show 4 BN with an empty SF slot, still 13 active.
3. **4 adds per week** (Q11). Playoff teams and weeks, and the season's fantasy matchup schedule, were asked of Instinct (below).
4. **N0 Fair tier: either route works.** A deal is Fair when you gain and one of these holds:
   - the market is within ±15%, or
   - they gain in real odds too, and the market gap is moderate (starting guess ±30%). You may send slightly lesser players, as long as the fit helps them.

   The cap keeps the Trae Young lowball out of Fair.

### Asked Instinct (Oct 26): answered in the 11:37 PM file

- ✅ League settings at the top: H2H Categories, 10 starters + 3 BN + 3 IL, 4 adds/week, rolling waivers (2 days), trade deadline Mar 4 2027, 8 playoff teams in weeks 21–23 (Mar 22 – Apr 11).
- ✅ Season schedule: weeks 1–20 with matchups. Week 1 ran Tue–Sun; **weeks 7 and 17 are 14-day matchups**. Playoff weeks have dates only (pairings set after week 20).
- ❌ Yahoo's projected end-of-week totals (Q12): Yahoo shows none for this league. Dropped.
- ✅ Adds used this week per team (Q11) and the waiver priority. Only your own count comes from Yahoo; the other 11 are counted from the transaction log.
- ✅ No "BN Empty slot" lines. Empty starting slots stay (Mathmagicians' SF), and "IL Empty slot" lines are filled up to 3.
- ✅ Pulled after the night's last game was final (the header says so).
- Trades: none in the last 30 moves yet. The only one (Oct 19: John Basketball's Peyton Watson for Birds' Kyshawn George) is described in NOTES, so the Part 7 trade line format is still unseen.

The NBA side (game schedule, box scores, injuries) already comes from `nba_api` and ESPN.

### First real Yahoo pull: likely fixes

When `python yahoo.py` runs against a real league for the first time, check these spots in `yahoo.py`:
- `text(t.name)`: team names can come back as bytes. This is handled, but verify names look right.
- The free-agent query (`players;status=A;sort=AR`): confirm the list isn't empty and has real names. The result shape may be `{"player": Player}` dicts or `Player` objects; `pl()` handles both.
- `s.stat_categories.stats` mapping: confirm `settings.cats` comes out as the 9 categories (`FG%, FT%, FG3M, PTS, REB, AST, STL, BLK, TOV`). If your league uses other categories (e.g. 3P%, DD), they're ignored for now, so add them to `YAHOO_STAT` / `CATS`.
- `roster` size: it sums roster slots excluding IL/IL+/NA. Check it matches your league.
- `me`: comes from `is_owned_by_current_login`. If it's `None`, the wrong Yahoo account is logged in.
- Player-name matching: Yahoo names that don't match NBA.com names are silently skipped by `League._keys()`. Print any misses on the first run and add aliases if needed.
- Scoring format (P2): store Yahoo's `scoring_type` as `settings.scoring` (`head` = categories, `headone` = one win; verify the values on the first pull).

---

## Do first (Oct 26 review)

Found by reading the whole codebase against the real league on Oct 26. Unless marked otherwise, the numbers come from the v3 import (`data/league.json`) via scratch scripts. Re-measure after P1.

### P1. Import the Oct 26 snapshot: parser rewrite ✅ done Oct 27 (branch `trade-lab`)
**Result:** `parse_text` returns a dict (teams, free agents, records, matchups, transactions, projections, adds, waiver order, schedule, settings lines, pull time). On the real file: 168 rostered players (only AJ Dybantsa left out), 113 listed free agents plus the 100 best unrostered (213), 256 projections, 30 transactions, 23 schedule weeks, roster 13 and scoring "categories" from the settings. This week shows fatty zhou vs kobe colorado with the live totals, and the rest of the week counts from Oct 27 (`matchup.from`, applied in `games_left`). Refresh imports the newest `*snapshot*.txt`. Checked in the browser (no console errors) and via `/api/refresh`.
- Real-league standings after the import: 26–78% (Sharks 78%, fatty zhou 71% and 2nd, Mathmagicians 26%). The v3 import ran 21–74%, so the spread isn't from the merged free agents (replacement stays −0.65). P2 narrows it.
- Not done: nothing reads `rank`, `projections`, `transactions`, `adds` or `schedule` yet. N0, N4, N3/Q11 and Q5 pick them up.

**Why:** `parse_text` (`yahoo.py`) picks each section's mode from loose keywords and falls back to "teams", so any part it doesn't recognise lands on the last team it read. On the Oct 26 file this gives:
- Kerala blasters 300 players: Part 6 matchup lines, every Part 8 projection row, the `Toronto blue jays:` sub-headings and `NOTES`.
- Every team fake players called `BN    Empty slot` / `IL    Empty slot`.
- Any heading containing "%" turns its rows into free agents. In a test, a "Pre / Cur / %Ros" heading made Jokić a free agent.

`from_text` then takes the roster size from the biggest team, which breaks the z-score pool (`teams × roster`).

**The Oct 26 file (1,076 lines, re-pulled 11:37 PM MST):**
- Header lines: `Pulled Monday, October 26, 2026, 11:37 PM MST (America/Creston, UTC-7) ... All NBA games of Oct 26 were final at pull time.`, `Week 2: Mon Oct 26 - Sun Nov 1`, `My team: fatty zhou`.
- Some headings have no `PART N -` prefix: `LEAGUE SETTINGS`, `ADDS USED THIS WEEK AND WAIVER PRIORITY`, `SEASON SCHEDULE (weeks 1-23)` and `NOTES`, each between `====` lines.
- **League settings**: `Scoring type:`, `Roster slots: ... (10 starters) + 3 BN + 3 IL = 16`, `Max adds/week: 4`, waiver type, trade deadline, playoffs.
- **Part 1, rosters.**
  - Team heading: `5. fatty zhou  (5-4-0, 5th place)`. The W-L-T is a *category* record (P2).
  - Player rows end in `Pre 102  Cur 74   Ros 76%`, and an injury tag like `[Q: Questionable]` sits before `Pre`.
  - `Empty slot` rows (`SF    Empty slot`, `IL    Empty slot`) are placeholders, not players. A `Note:` line sits under Mathmagicians.
  - Instinct cross-checked 168 rostered players.
- **Part 2**, injured players: skip, as today.
- **Parts 3–5**, free agents (now the top 100), the 15%+ list and the waiver wire: same columns as v3, 113 unique players. Parts 3 and 5 carry `Pre`/`Cur` columns before the name.
- **Part 6**, matchups. A pair line `fatty zhou vs kobe colorado`, then one line per team: `FGM/FGA 36/91   FTM/FTA 15/19   3PTM 13   PTS 100 ...`. Labels map through `YAHOO_STAT`. Starting-lineup totals with the night's games final.
- **Part 7**, transactions: `Oct 26 7:01pm   John Basketball       add           Kris Dunn (dropped Tristan da Silva)`. Types: add, drop, waiver claim. `Big Tuo Plumbing Inc.` fills its column, leaving a single space before the type, so match known team names. Times are league time (Central).
- **Part 8**, Yahoo's rest-of-season projections per game, under `Team:` sub-headings, then `TOP 100 FREE AGENTS (same list as Part 3)`. Columns `MIN FGM FGA FTM FTA 3PTM PTS REB AST ST BLK TO`, MIN always `-`. 12 rookies show `no projection shown by Yahoo`.
- **Adds used**: `  Toronto blue jays     0/4` per team, then the waiver priority `  1   Mathmagicians`.
- **Season schedule**: `Week 7: Mon Nov 30 - Sun Dec 13 (14-day week, one matchup)`, then `  fatty zhou vs Birds that I hate` lines. Playoff weeks have no pairs yet.
- **Part 9** was skipped (first season). **NOTES** is prose.

**Change (`yahoo.py`):**
- **Section modes** come from explicit words, and anything unknown is skipped (never "teams").
  - `TEAM ROSTERS` → teams; `INJURED` → skip.
  - `ADDS USED` / `PRIORITY` → adds (before the `WAIVER` check).
  - `FREE AGENTS` / `15%+` / `WAIVER` → fa.
  - `MATCHUPS`, `TRANSACTIONS`, `PROJECTIONS`, `SETTINGS`, `SCHEDULE` → their own modes.
  - Hand-typed `## Team` lists keep working.
- **Skip `Empty slot` rows** and anything else that isn't a player row.
- **Save in the snapshot:**
  - `rank: {pre, cur}` and `owned` for rostered players (Part 1) and free agents (Parts 3 and 5)
  - `matchup` for your team (opponent, `week_start`/`week_end` from the header, both teams' totals), plus `matchups` for all six pairs (Q5)
  - `transactions`, `projections` ({name: per-game line}, for N4) and `records` ({team: W, L, T, rank})
  - `adds` ({team: used}) and `waiver_order`, `schedule` ([{week, start, end, matchups}]) and the raw settings lines as `rules`
  - `pulled`: the pull time
  - `me` from the `My team:` line when the command line doesn't name a team
- **Settings**: `roster` = starters + BN from `Roster slots` (13; IL doesn't count), default 13 when the file has no settings. Don't derive it from the biggest team. Also `scoring` (for P2) and `max_adds` (Q11).
- **Free agents**: the file lists 113 of the ~576 available players. Merge them with the model's best unrostered players (anyone not on a roster is available) instead of today's `listed or generated`.
- **Live totals**: when the pull is after the day's games started (the header says they were final, or it's after 7 PM ET), count the rest of the week from the next day. Otherwise today counts twice (Build next #2's known limit).
- **Refresh** picks the newest `*snapshot*.txt` in the repo root, not only the last imported one.

**Done when:**
- The import parses all 168 rostered players with no fake rows, and its "Left out" list only has players with no NBA games yet.
- 113+ free agents.
- This week shows fatty zhou vs kobe colorado with the live totals.
- `test_model.py` parses a trimmed copy of each new part (same inline-text pattern as the v3 test), including an unknown part that must be skipped.

### P2. Scoring format: H2H Categories (confirmed by Martin, Oct 26)
**Why:** after week 1, every team-heading record adds up to 9 (4-5-0, 9-0-0, 1-7-1), and the opponents pair up: kobe 9-0 vs Big Tuo 0-9, Mumbai 7-1-1 vs Mathmagicians 1-7-1. That's Yahoo's "Head-to-Head – Categories", where each category won counts as a win in the standings.

The engine optimizes `match_prob` (the chance to win 5 of 9), which is the "One Win" goal. For Categories, the regular-season goal is the expected share of categories won (`p.mean(-1)`). `match_prob` only decides playoff matchups.

**Measured Oct 26 (objective swapped in a scratch run):**
- Projected team ranks didn't change.
- 16 of 33 fair deals dropped out and others came in.
- Every delta shrinks about 2.5×: fatty zhou goes from 63% "win" to 55% of categories, and a +2.7 trade becomes +1.1.

**Change:**
- **`settings.scoring`**: `"categories"` or `"one_win"`.
  - Text import: infer it from records summing to 9 × weeks played, or take a flag.
  - Yahoo pull: from `scoring_type`.
- **One `League` method** (e.g. `_score(p)`) wherever `match_prob` is called today:
  - `_contrib`, `outlook`, `_waivers_matchup` and the `this_week` streams
  - `search`: `m_o`, `m_t`, `t_o` and `match_prob(1 - p_mt)`
- **Rescale every one-win threshold by about 1/2.5** and keep them at the top of `model.py`: `TIERS`, `accept()` (`their_net > -1`), the trade verdict (±0.5), waiver moves (> 0.5), streams (≥ 0.1) and the goal guardrail (`dme > -1`).
- **Wording**: "win odds" becomes "share of categories won", or an expected weekly score like 5.0–4.0.
  - Update the UI, README, `ai.py` SYSTEM, `.claude/skills/gm/SKILL.md` and CLAUDE.md's model rules.
  - CLAUDE.md's 25–70% demo-standings sanity range narrows; the real league runs 38–60%.
- **This week** leads with the expected score (`score`, already computed). `win` stays, as the chance to win more categories.
- **Backtest**: category calibration is what matters now (already within about 3 points). Matchup calibration only matters for the playoffs (T4's playoff mode).

**Done when:** standings, trades, search, waivers and This week run on the category goal in a categories league, tests cover both formats, demo standings stay in a sane range, and N1's punt analysis is re-run.

### P3. Score trades against the free pickup (requested by Martin, Oct 26)
**Why:** T1's known limit ("a trade still gets credit for a waiver pickup you could make anyway") drives the real league's results.
- All six of fatty zhou's top fair deals were 2-for-1s whose open spot went to Vučević.
- Dropping Achiuwa for Vučević on its own was worth +3.1, more than any fair trade.
- With that move made first, the trades lose a third or more of their gain:

| Trade | As scored today | After the waiver move |
|---|---|---|
| Lillard + Kessler → Myles Turner | +2.9 | +2.1 |
| Kessler + Collins → Turner | +2.6 | +1.6 |
| Lillard + George → OG Anunoby | +2.5 | +1.6 |
| Suggs + Podziemski → Mikal Bridges | +2.7 | +1.7 |

**Change:**
- **Your best free move**: the best add/drop, or a plain add into an open spot, by win-% gain. Use `_waivers_matchup`'s maths; repeat while a move still clears the waiver threshold.
- **Simplest version** (what the numbers above used): apply that move to your roster and the free-agent pool first, then run `trade()` / `search()` unchanged. The open spot after a 2-for-1 then gets the next-best free agent.
- **The other team**: do the same for the partner only if it changes their numbers noticeably. Measure first.
- **UI and AI output** say it outright, e.g. "Compared with just adding Vučević for Achiuwa (+3.1)". Waivers and Trades use the same baseline.

**Done when:** a test trade whose only gain is the open spot scores about 0, the real-league deals above show their after-waiver numbers, and the Trades view names the pickup it assumed.

### P4. Small fixes
- **Injured free agents in the waiver baseline.** `_waiver_level` ranks free agents on per-game value only, so Kevin Porter Jr. (NA, plays 0% of games) counts as one of the top 5. Replacement is −0.65 with him and −0.83 with healthy players only, and it feeds `replacement()` and the `FILL` line. Skip `OUT` statuses. `effective()` can't be used here: it needs `zfill`, which is built from `_waiver_level`.
- **Waiver dates and New Year.** Claim dates assume the current year (`this_week` → `games()`), so a "Jan 2" claim read on Dec 30 counts as already cleared. Roll the year forward when the date is more than about two months in the past.
- **Backtest without the fill-in.** `backtest.py` predicts weekly totals without the `FILL` term that `League.week` adds. Add it, or note it next to the knobs.
- **Doc drift:**
  - README Setup says `pip install -r requirements.txt`, which Homebrew Python blocks. Use `.venv`.
  - README "Not modelled yet" says this week's schedule isn't in the weekly matchup. This week uses it; trades and season odds don't.
  - `.claude/skills/gm/SKILL.md` step 1 refreshes with `python yahoo.py`, which needs a `.env` that doesn't exist. Point it at importing the newest snapshot, and use `.venv/bin/python` in every command (N2 depends on this).

---

## Trade lab (requested Oct 26, 2026; T1–T4 done)

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
- `TIERS` constants at the top of the file (retuned Oct 26 after calibration into a gradual ladder for the other team: +1 / 0 / −2.5 / −6. Easy and Fair sort by the smaller gain, Finesse and Hard by yours. Table below is the original plan; u = 1 in 9-cat):

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
- `build_after`: categories you'd win under 35% of weeks after the trade (`WEAK`, the "consider punting" hint).
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

## Next up (planned Oct 26, after calibration)

One task per session: *"Read ROADMAP.md and do Next up N0"*. Run `python test_model.py` after model changes, and `python backtest.py` after anything that changes projections or odds. Suggested order: Do first (P1–P4), then N0, N1, N2, N3, Q1–Q3, N5, the rest. After P2, every "win %" below means share of categories won.

### N0. Market value: trades priced like real managers price them (requested Oct 26, top priority)
**Why:** the finder only asks "do both teams' win odds go up?". Category fit ignores what players cost on the trade market, so it suggests deals no manager makes:
- Precious Achiuwa + Lillard + Kessler for Trae Young came out "Fair" (+3.5% you, +3.7% them). The model ranks Trae ~106th (15 GP last season, bad TO/FG%), but the market sees a star.
- With `goal="cats"`, Wembanyama + Sengun for Curry can look "worth it" for 3PM/PTS, while giving away far more value than you get.

`their_view` ("looks to them") was meant to be this, but it's the model's own hype guess, not real rankings. Market value has to come from outside the model, or it just repeats the model's opinion.
**Data (arrived Oct 26, parsed by P1):** Yahoo's preseason (Pre) and current (Cur) rank for every rostered player (Part 1) and free agent (Parts 3 and 5), plus roster %.
- **Cur is noisy early.** It's the season-to-date rank in this league's scoring: Paul George is Cur 1 and Anthony Black Cur 14 after a handful of games. Injured players sink to 600–700 (Jimmy Butler 700, Brandon Ingram 637).
- **So don't use Instinct's flat `(Pre + Cur) / 2`.** Weight Cur by `GP / (GP + HYPE)` (the model's own hype weight), then re-rank every player on that blend.
- Later, an expert ROS rank (Hashtag Basketball / FantasyPros) can be blended in.
- Players with no Yahoo rank fall back to the model's own rank, labelled as such.

**Change (`model.py`):**
- `market(k)`: trade value from rank on a steep curve, so stars are worth more than the sum of spare parts. Starting guess `100 x 0.97^(rank - 1)`: rank 1 = 100, 25 = 48, 50 = 22, 100 = 5, 150 = 1. Tune it with the trade log (N6).
- Roster spots: the side receiving more players subtracts the market value of the players it drops. The side with open spots adds the market value of the best real free agent (Gafford, Pre 135 / Cur 115: about 3 on the curve), not a fixed rank 150 (about 1).
- Every trade and search result gets `market: {give, get, diff_pct}`, and a verdict such as "you overpay 35%" or "even".
- Tiers become two-dimensional:
  - **Fair:** your odds up, plus one of two routes (Martin, Oct 26): the market within ±15%, or both teams gaining in real odds with the market within ±30% (starting guess). The cap keeps the Trae lowball out.
  - **Easy:** they win on market, and you still gain.
  - **Finesse:** market even or in their favour, but the category fit is strongly yours. This is the real edge: they pay market price for players worth more to you.
  - **Hard:** you win on market.
- `accept()` uses the market diff instead of the hype guess. `HYPE`-based `their_view` stays as a "how hot/cold starts read" note, not the acceptance signal.
- `goal="cats"` and `goal="team"` get a guardrail: never overpay more than the tier allows.
**UI:**
- A "Mkt" column (value + rank) in every player table.
- Deal tables and the trade verdict show "Market: give 34 · get 41 (+21%)".
- The Trades toolbar gets a "Max overpay" control.
**Done when:**
- The Trae Young deal (Achiuwa + Lillard + Kessler for Trae) is flagged as a lowball. Both sides gain in fit, but you'd get far more market value than you give, so it lands in Hard / Long shot, not Fair.
- A deal where fatty zhou gives up more stars than it gets, with `goal="cats"`, is flagged "you overpay". Use a real one, e.g. Jokić for two lesser players. The old example, Wembanyama + Şengün for Curry, can't run as fatty zhou: Wemby is on John Basketball and Şengün is on Mumbai Meteors.
- Tests cover the curve, the drop cost and the tier rules.
- Fair-tier results on the real league all sit within ±15% market.

### N1. Strategy panel (punt planner)
**Why:** toggling a punt chip only concedes categories on today's roster, so it always lowers your odds. A punt pays off through the moves it unlocks. The Oct 26 analysis on fatty zhou showed no punt beats staying balanced (best fair trade +3.7% unpunted, +3.6% punting FG%, +0.5% punting FT%+TO), but nothing in the app says that.
**Change:** `League.strategy()` loops over builds (no punt, each single category, FT%+TO, FG%+TO, FG%+FT%, AST+TO, 3PM+FT%, FG%+3PM). For each build: the best `search(tier="fair", punt=build)` deals and best `waivers(build)` moves, re-scored with `trade()` / `outlook()` on true odds (no concession). Return builds ranked by true win % after the best move. Add a panel on My team with build, best move, true odds after, and a one-line verdict ("Stay balanced" / "Punt X: it unlocks Y"). Also list "cheapest categories to move": the ones near 50%, where a trade or stream changes the odds most.
**Before building:** redo the Oct 26 analysis after P2 and P3. Its numbers are one-win odds and include free-pickup credit, and under Categories each conceded category is a straight loss in the standings, which makes punting cost more.
**Speed:** 12 builds × an open search. Size 2 takes about 0.2s on the real league and size 3 about 2s on the demo, so use size 2 or run it on demand behind a "thinking" state.
**Done when:** the panel matches a script run of the same numbers, and the test covers a league where one punt clearly wins.

### N2. AI GM chat through Claude Code (no API key)
**Why:** `ai.py` needs a paid API key (billed separately from a Claude subscription). `claude -p` runs Claude Code headless with your own login.
**Change:** `/api/chat` shells out to `claude -p "<message>" --output-format json --resume <session_id>`, with `--allowedTools "Bash(.venv/bin/python model.py:*)"`, `--append-system-prompt` from `.claude/skills/gm/SKILL.md`, and the current punt in the prompt. Save the session id per chat. Keep `ai.py` as the fallback when `ANTHROPIC_API_KEY` is set. Show a "thinking" state, since answers take 10–30s.
**Watch out:**
- SKILL.md's commands use bare `python model.py`, which the `--allowedTools` pattern won't match. Fix it first (P4).
- `claude -p` also loads Martin's user-level settings, hooks (e.g. the ponytail SessionStart hook) and global CLAUDE.md. Check `claude --help` for a way to limit which settings it loads, so none of that leaks into GM answers.
**Done when:** a chat question in the browser gets an answer quoting `model.py` numbers, a follow-up question remembers context, and New conversation starts fresh.

### N3. Snapshot refresh loop
**Why:** the league comes from Instinct text snapshots; re-importing by hand is the main chore.
**Mostly covered by P1** (Oct 26): Instinct's dated file has the matchup totals, ranks, Yahoo projections, transactions and records, and P1 parses them and makes Refresh pick the newest file.
**Left:**
- The sidebar shows the snapshot's pulled time (Q9).
- A "Recently dropped" list from `transactions` (Build next #3).
- Use the season schedule P1 parses (`schedule`, `rules`): next week's opponent when the snapshot is stale, the 14-day weeks 7 and 17, Q5 and playoff mode.

Keep checking that apostrophe names (De'Aaron Fox) survive in each new file.
**Done when:** dropping a new snapshot in the folder and pressing Refresh updates everything, including the live score.

### N4. Expert projections (Build next #4)
**Why:** the baseline is home-made (two seasons + regression). Expert ROS projections know about role changes and rookies.
**Source arrived Oct 26 (snapshot Part 8, parsed by P1):** Yahoo's ROS projections per game for all 168 rostered players and the top 100 free agents.
- No MIN column. Keep MIN from our own line, since the z-score pool is picked by minutes.
- No projection for 12 rookies, including Boozer, Peterson and Dybantsa, which is where projections would help most. ESPN (below) can fill them.
- Each dated snapshot is a record of that week's projections, so keep the files for a later backtest.

**Other source found Oct 26:** ESPN's public fantasy API has free rest-of-season projections. `lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/2027/segments/0/leaguedefaults/3?view=kona_player_info` returns 50 players by default; projections are the `stats` entries with `statSourceId 1`, `seasonId 2027`, `statSplitTypeId 0`, in `averageStats` keyed by ESPN stat ids (0 PTS, 1 BLK, 2 STL, 3 AST, 6 REB, 11 TO, 13 FGM, 14 FGA; verify the rest, e.g. FTM/FTA/3PM/MIN). Getting every player needs ESPN's `X-Fantasy-Filter` request header (a paging/filter parameter). It's unofficial, like the ESPN injury feed: cache it daily and degrade quietly. Rotowire projections are behind a paid subscription. Instinct's Part 8 (Yahoo projections) is the alternative.
**Change:** as in Build next #4, fed by ESPN (above), Instinct, or a CSV. Blend with `baseline()` rather than replace it. Backtesting needs archived projections, so if none exist, start logging this season's projections weekly to compare later.

### N5. Daily lineup cap (This week done Oct 27)
**Done for This week (Oct 27):** `starts()` counts only the best `lineup` (10, `settings.starters`) players with a game each day, in `week_vec()`, the games-left count and streaming adds. Martin spotted it: the app showed 35 games left for both teams, Yahoo 32 (fatty zhou) and 27 (kobe). It now shows 35 and 32: kobe had 13 players with games on Wednesday. The rest of the gap is lineups set by hand (Yahoo counts each day's set lineup, the app assumes the best players start). Positions are still ignored. Left: the season-average week (`League.week`, trades, standings) and `backtest.py`.
**Why:** every rostered game counts today, so depth (and streaming the 13th man) is overvalued vs stars, and 3-for-1 deals look better for the side taking 3. Positions now come from the snapshot.
**Change:** simulate the 10 active slots per day from the NBA schedule (best players start), in `week_vec()` and the season-average week. Add the cap to `backtest.py` and re-fit if the odds shift.
**Done when:** a 3-for-1 consolidation scores higher than before for the side getting the star, and the backtest stays calibrated.

### N6. Smaller model items
- Matchup correlation: a shared games-played factor across counting stats (backtest shows ±3 points at the matchup level).
- Trade log: record offers sent and accepted/declined. That's the only real data for tuning `HYPE`, `TIERS` and `accept()`.
- (Applying for Yahoo API access moved to "For you".)

### Quality of life and genuinely useful views (Oct 26 list, rough priority)
All inline (no modals, per DESIGN.md); every new stat table honours punt dimming.
- **Q1. Player card.** Click any player name anywhere to open an inline panel:
  - ROS line, this season, and last 7/14 days
  - games this week and the next 3 weeks
  - injury note and return date
  - market rank vs model rank (N0), and owner
  - buttons: Find offers / Find buyers / Compare

  It replaces hunting through tables.
- **Q2. Recent form.** One `LeagueGameLog` call for the current season (cached daily) gives last-7/14/30-day averages. Show trend arrows in player tables, and rebuild Hot & cold on real windows instead of the hype blend.
- **Q3. Compare players.** Pick 2–4 players to see per-category value, ROS line, recent form, schedule, availability and market value side by side. For "who do I keep / start / trade for".
- **Q4. Schedule planner.** Your roster's games per day for this week and the next 3. Overflow days (more than 10 players with games, once N5 exists) and open slots for streaming are highlighted. Also the NBA teams with the most games in the coming weeks, for buying ahead of a heavy schedule (and fantasy playoff weeks).
- **Q5. Playoff odds.** Actual W-L from the snapshot plus the projected weekly odds for the remaining weeks, simulated to final standings, gives each team's playoff probability. It turns "projected 3rd" into "78% to make the playoffs". The W-L is a category record (P2), so simulate category wins per week. Needs the season's matchup schedule and the playoff format (asked of Instinct, Oct 26).
- **Q6. Global player search** in the top bar (today it's only on Trades). Opens the player card.
- **Q7. Custom add/drop evaluator** on Waivers: pick any add and any drop and see the win-% change this week and for the season, like the trade builder.
- **Q8. Copy offer.** One click copies "I give X, Y for Z" plus a short pitch (via the AI GM / humanizer) to paste into Yahoo. Also logs it to the trade log (N6).
- **Q9. Freshness.** Show when stats, schedule, injuries and the snapshot were last updated. Warn when the snapshot is older than a day, or the week rolled over since the last import.
- **Q10. Daily brief.** A scheduled routine (`/schedule`) that writes a morning summary:
  - injuries on your team
  - waiver claims clearing today
  - This week's chase/behind calls
  - the best stream for today
- **Q11. Weekly add limit.** The league allows **4 adds per week** (Martin, Oct 26). Streaming adds count how many you have left this week (from `transactions`, or the per-team count Instinct was asked for) and save the last one for the best day.
- **Q12. Yahoo's weekly projection next to ours.** ❌ Not available: Yahoo shows no projected totals for this league (Oct 26 file). If that changes: when the snapshot has Yahoo's projected end-of-week totals per team, show them beside This week's projection and note big gaps. It's a cheap check on `week_vec`, and a sign of what the other manager sees.

## Build next (highest impact for 9-cat first)

### 1. This week's NBA schedule ✅ done Oct 26

Built: `model.schedule()` (nba_api `ScheduleLeagueV2`, cached as `data/schedule_<season>.json` for a day), `League.games_week`, `League.week_vec()`, and a "Wk" column in every player table. NBA team = the league snapshot's `nba` field, falling back to the stats team. The test covers 4 games vs 2.
Not done: Yahoo's actual week dates (weeks are assumed to run Monday–Sunday until a real Yahoo pull stores `week_start`/`week_end`). `week_vec` drives task 2's This week view; season-long advice still uses the average week.

**Why:** H2H is weekly. A player with 4 games this week is worth about twice one with 2 games. The model currently assumes 3.4 games for everyone.

**Data:** `nba_api.stats.endpoints.scheduleleaguev2.ScheduleLeagueV2(season="2026-27")` (free, no key, import confirmed working). Yahoo week start/end dates: `YahooFantasySportsQuery.get_game_weeks_by_game_id(game_id)`, plus the league's `current_week` from `get_league_metadata()`.

**Change:**
- `yahoo.py`: store `current_week`, `week_start` and `week_end` in the snapshot.
- `model.py`: fetch and cache the schedule (`data/schedule_2026-27.csv`), and build `games_this_week[team_abbr]` for the remaining days of the current week. Add `TEAM_ABBREVIATION` to `self.p` (it's already in the cached stats).
- New `League.week_vec(keys)` = per-game line × games left this week × availability. Keep `vec()` (season average) for trades.

**Done when:** each player shows "games this week", and the test checks a 4-game player contributes more to `week_vec` than an identical 2-game player.

### 2. Current matchup ("This week" view) ✅ built Oct 26, live score waits on Yahoo

Built: `League.this_week(opp, punt)` (CLI `python model.py week --vs "Team"`, `/api/week`, AI tool `this_week`), and a **This week** view under My team: verdict, category table (so far → projected, odds, Safe / Chase / Lost call, punted rows dimmed) and streaming adds.
- `cat_probs` takes banked totals (`A0`, `B0`): they move the means but add no variance. A team with no shots left and nothing banked gets 0%, not NaN.
- Players in IL / IL+ / NA slots sit. A waiver claim counts only from the day it clears ("claim by Oct 28").
- Streams: each healthy free agent swapped for whichever of your 3 cheapest healthy players (season win-% cost) helps this week most. `season` shows what keeping him does long-term, so a one-week stream that hurts the season is visible.
- Opponent: until Yahoo works, you pick it in the view. It's saved in the snapshot as `matchup` with `week_end`, survives a text re-import, is cleared when you switch teams, and is ignored once the week ends. `yahoo.py` now pulls `matchup` (opponent, week dates, both teams' totals, FGM/FGA split) from the scoreboard, but that's **unverified** like the rest of the Yahoo pull.
- Known limits: no live score without Yahoo (totals start at zero, right on Mondays only). The daily lineup cap is in (N5, Oct 27), but positions are ignored. If live totals already include today's games, today counts twice (fixed in P1). When first built, the Playwright MCP couldn't start in Cursor (it looked for Chrome), so the view was rendered in Node against the real API. It now runs on Edge, and This week was checked in it after P1 (Oct 27) with the live opponent.

Original plan:

**Why:** this unlocks advice like "you're up 5–4; you're losing STL by 3, so stream a steals guy, and don't chase PTS."

**Data (Yahoo API, already in yfpy):** `get_team_matchups(team_id)` or `get_league_scoreboard_by_week(week)` gives this week's opponent and each team's category totals so far.

**Change:**
- `yahoo.py`: save opponent name, current totals for both teams (FGM/FGA/FTM/FTA plus counting stats), and the week dates.
- `model.py`: new `League.this_week()`. Projected final = current totals + `week_vec` for the games left. Per-category odds use the existing `cat_probs` (the variance only covers remaining games). Matchup odds use `match_prob`.
- Streaming: for each free agent, the gain in this-week matchup odds if added (drop your weakest), weighted by games left. Show the top 5.
- `web/index.html`: a "This week" view or panel on My team showing the category score, odds per category, "chase / safe / lost" labels, and streaming adds.
- `ai.py` + `/gm`: add a `this_week` tool.

**Done when:** the view shows the real opponent and live category totals, and the streaming list changes when a free agent has more games. (The Oct 26 snapshot has both; P1.)

### 3. Injury notes, ownership %, transactions, real standings (partly done Oct 26)

Done without Yahoo: injury notes on hover (ESPN), plus "Owned" % and "claim by <date>" in Waivers (from the text snapshot). Not done: recently dropped list, real W-L next to projected. P1 parses both (`transactions`, `records`); the UI part is still to do.

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

### 5. Injury return dates ✅ done Oct 26 (ESPN, not Rotowire)

Built: `model.injuries()` reads ESPN's public JSON (`site.api.espn.com/.../nba/injuries`: status, note and `returnDate`), cached in `data/injuries.json` for 3 hours. Rotowire's injury table came back nearly empty to plain requests, and ESPN's notes carry the same news. A return date replaces the status multiplier: rest-of-season availability = history rate × share of remaining games after the return date, and `week_vec` counts no games before it. Hovering an injury tag shows the note and "back <date>". This is an unofficial endpoint used for personal purposes; if ESPN changes it, it goes quiet and the league's tags still work.

Original plan:

**Why:** availability currently comes from games-played history plus Yahoo's tag. "Out 4–6 weeks" should mean 0 games for 4–6 weeks, then normal.

**Data:** ESPN's public JSON (reportedly `site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries`, which needs verifying) or Rotowire's injury page. Check each site's terms first, cache results, and refresh at most a few times a day.

**Change:** `injuries.py` fetches `{player: expected_return_date}`. Availability for the rest of the season becomes the share of remaining games after the return date, times the history-based rate. For this week (task 1), it's 0 before the return date.

**Done when:** a player listed "out until X" gets reduced availability and shows the return date in the UI.

---

## Model accuracy

- [x] **Calibrate the randomness settings** ✅ Oct 26, without Yahoo: `backtest.py` replays 2025-26 box scores in 20 simulated 12-team leagues, predicting each week from what was known that Monday. The old knobs were overconfident (predicted 0–10% won ~15%, 90–100% won ~86%). Fitted: DISPERSION per category, SCHED_CV 0.15, PCT_NOISE 1.75. Category and matchup calibration now sit within ~3 points of the diagonal. Category log loss went from 0.633 to 0.608 for an average week and from 0.614 to 0.580 with the real schedule. Also backtested and adopted a two-season baseline regressed by 25 games (`baseline()`). The availability formula held up against 2025-26 games played (players who missed a season: predicted 40%, actual 27%). Still guesses: GAMES_WEEK, FILL, STATUS_AVAIL, HYPE (perception can't be backtested without trade data).
- [ ] **Matchup correlation**: categories are treated as independent. After calibration, matchup odds are within ~3 points, slightly bold with the real schedule and slightly timid for an average week. A shared games-played factor would fix both.
- [ ] **Positions and daily lineup slots.** Trades and adds ignore positions (e.g. you could end up with 6 centers). Yahoo's `roster_positions` is in the settings. Minimum version: warn when a trade leaves a position empty *(done in Trade lab T2, untested on real `pos` data)*. Full version: a games-played penalty when more players have games than slots.
- [x] **Drop choice in trades**: drops whoever costs the least win % (Trade lab T1).
- [x] **Trade finder depth**: up to 3-for-3, pinned players searched across the whole roster (Trade lab T1).
- [ ] **Hot & cold** uses a fixed hype weight (`HYPE = 10`, was 5: 3 games carried 37% of how a player looked). Could compare against Yahoo's actual rank instead (Pre/Cur arrive with P1; see N0).

## Later / maybe

- [ ] **Yahoo write actions** (propose trades, add/drop from the app). The Yahoo API supports them with write scope. Kept read-only on purpose; if added, always ask for confirmation first.
- [ ] **Hosting for league mates**: needs a server, a Yahoo login per user, and per-user snapshots.
- [ ] **Keeper/dynasty value**: age curves, multi-season values.
- [ ] **Alerts**: a scheduled Claude Code routine (`/schedule`) that refreshes daily and messages you when a good player is dropped or a sell-high window opens.

## Known small issues

- impeccable's design detector flags the "Segoe UI Variable" font stack (its parser misreads the name). Harmless.
- Headless Chromium/Edge won't go below 504px wide, so phone screenshots need the 390px-iframe trick (see CLAUDE.md).
- The demo league's teams are random. Don't read too much into demo numbers.
