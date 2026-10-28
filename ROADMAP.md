# Roadmap

What works, what's untested, and what to build next, in priority order. Each task lists where the data comes from, roughly what to change, and when it counts as done.

To pick this up in Claude Code: *"Read ROADMAP.md and do P3"*. Work through **Do first** (P1–P4) before **Next up**. Martin's league decisions are under "For you" → **Decisions**.

**Effort (always Opus):** each open task ends with the effort to start its session with: `xhigh` → `claude --effort xhigh`, `high` → `claude --effort high`, `medium` → `claude --effort medium`. Claude can't change its own effort mid-session, so set it when you start. Rule of thumb: engine maths, calibration and wrong-number bugs get xhigh; features, fixes and parser work get high; only really easy jobs (wording, docs, small UI tweaks, commits, snapshot imports) get medium. When starting a task, Claude says its tag in one line.

---

## Where things stand (Oct 26, 2026)

**Working and tested (on the demo league):**
- Web app with My team, This week, League, Trades, Waivers, Hot & cold and AI GM views (`python app.py`).
- 9-cat matchup model: weekly win odds, projected standings, odds per category, trade, waiver and finder scoring, punts.
- Points-league mode (simpler: per-game values, no matchup model).
- `python test_model.py` passes, and every view was clicked through in a real browser.

**Built but never run for real:**
- [ ] **Yahoo pull** (`python yahoo.py`). Written against yfpy 17's documented fields, but never run against a real league. Expect small fixes on the first run (see "First real Yahoo pull" below). **Blocked (Oct 26):** since summer 2026, Yahoo grants Fantasy API access only to applications it approves (sports.yahoo.com/developer/access), with no published timeline, and old apps reportedly get 403s. Workaround in use: Instinct's text snapshots. `data/league.json` holds the Oct 27 import (`python yahoo.py import mchc-fantasy-snapshot-2026-10-27.txt`, pulled 12:55 PM MST before any Oct 27 game: 167 players, 214 free agents, AJ Dybantsa left out with no NBA games yet). That file adds Yahoo's games played and games left per team to Part 6, shown on This week next to ours (35 vs 32 ours, 32 vs 27 Yahoo's: Yahoo counts the lineups as set now). Its header ("No Oct 27 games had started ... earlier games were final") made the parser skip today's games; fixed Oct 27.
- [x] **Oct 26 snapshot imported** (P1, Oct 27): `mchc-fantasy-snapshot-2026-10-26.txt`, re-pulled Oct 26, 11:37 PM MST, after every game was final. League settings, Pre/Cur/Ros ranks, this week's matchups, 30 transactions, Yahoo projections, adds used, waiver priority and the season schedule are all in `data/league.json`.
- [x] **AI GM chat** (N2, Oct 27): runs through `claude -p` on Martin's login, no key; tested in a real browser on the real league. `ai.py` (the API path, used when `ANTHROPIC_API_KEY` is set) still hasn't had a real conversation.

**Known gaps in the model:** listed under "Model accuracy" below.

---

## For you (not code)

1. **Sanity-check the Oct 26 import** (done in P1, see item 3). Instinct answered the asks below (Oct 26, 11:37 PM file).
2. **Apply for Yahoo API access** (sports.yahoo.com/developer/access, personal single-league use), since approval has no timeline. Then follow README → "Connect your Yahoo league".
3. **Sanity-check your real team** after P1. Does the projected finish look plausible? Do the punt suggestions match your gut? Do the waiver adds make sense? Write down anything that looks wrong. That's the best calibration data available.
4. ~~Optional: add `ANTHROPIC_API_KEY` to `.env` for the in-app chat.~~ Not needed since N2: the chat runs on your Claude Code login.

(Moving to the new computer is done: the repo runs on the Mac from `.venv`.)

### Decisions (Martin, Oct 26)

1. **Scoring: Head-to-Head – Categories** (P2). Each category won counts in the standings.
2. **Bench: 3 slots**, so 13 active (10 starters + 3 BN) plus 3 IL (P1). The 11:37 PM file confirms it (`Roster slots: ... (10 starters) + 3 BN + 3 IL = 16`). Eleven teams carry 13 active players, so a pickup needs a drop. **Birds that I hate has 12** (2 BN), an open spot (P3). Mathmagicians show 4 BN with an empty SF slot, still 13 active.
3. **4 adds per week** (Q11). Playoff teams and weeks, and the season's fantasy matchup schedule, were asked of Instinct (below).
4. **N0 Fair tier: either route works.** A deal is Fair when you gain and one of these holds:
   - the market is within ±15%, or
   - they gain in real odds too, and the market gap is moderate (starting guess ±30%). You may send slightly lesser players, as long as the fit helps them.

   The cap keeps the Trae Young lowball out of Fair.
5. **Other managers judge a trade on the trade itself** (Oct 27, P3), not on the waiver moves they could make instead. So only your side is scored against your free pickups.

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

### P2. Scoring format: H2H Categories (confirmed by Martin, Oct 26) ✅ done Oct 27 (branch `trade-lab`)
**Result:** `League._score(p)` (share of categories won, or `match_prob` when `settings.scoring` is `"one_win"`) replaces `match_prob` in `_contrib`, `outlook`, `_waivers_matchup`, `search` and This week's streams. `scoring` defaults to `"categories"` (Yahoo's H2H default); the text import reads it from `Scoring type:` (P1), the Yahoo pull from `scoring_type` (`headone` = one win). One-win cutoffs stay at the top of `model.py` (`TIERS`, `VERDICT`, `MOVE`, `GUARD`, `accept()`), scaled by `CAT_SCALE = 0.4` through `League.s`.
- Measured Oct 27 on the real league: the same trades score 0.42× as much (IQR 0.40–0.45, 54 deals), waiver fits 0.44×. Standings run 40–61% (fatty zhou 58.4%, 3rd), against 26–77% as One Win. Snake-drafted demo: 47–52% (42–55% as One Win).
- Best fair 2-for-1s are now about +1.1 for both sides (Kessler + George → Myles Turner, Lillard + Suggs → Mikal Bridges). Vučević for Achiuwa is +1.2 on its own (P3).
- UI: a `WIN()` helper labels it "Win %" (share of categories won, which is how the standings count) or "Win odds" in One Win. This week leads with the expected score in a categories league. README, `ai.py` SYSTEM, the `/gm` skill and CLAUDE.md say the same. `profile()` and `state()` return `scoring`.
- Tests cover both formats: categories win % = mean category odds, one-win = `match_prob`, and one-win trade deltas are bigger.
- Not done: inferring the format from records (the settings line covers it), and scaling the stream floor (0.1 is the display resolution, not a judgment). The backtest needed no change, since odds maths didn't move.
- N1 re-run: see N1 below.

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

### P3. Score trades against the free pickup (requested by Martin, Oct 26) ✅ done Oct 27 (branch `trade-lab`)
**Result:** `free_moves()` finds a team's waiver moves that need no trade: the best add/drop, or a plain add into an open spot (`drop: None`), repeated greedily while one clears `MOVE` (0.2 in categories). It uses `_waivers_matchup`'s maths, now shared as `_gains`. `after_free()` returns a copy of the league with those moves made. `trade()` and `search()` (so also `ideas()`, Improve this deal and Saved trades) score your side on top of it. The players a deal sends are never dropped (`keep`), and your pickups are never offered. `free=False` gives today's numbers. `trade()` and `waivers()` return `free`.
- Real league (Oct 27 file): your free moves are Vučević for Achiuwa (+1.2), Tobias Harris for Anthony Black (+0.5) and Jay Huff for P.J. Washington (+0.4), taking you from 58.4% to 60.8%.
- The deals below, you / them, categories scoring. The open spot now gets Daniel Gafford:

  | Trade | Today | After your free moves |
  |---|---|---|
  | Lillard + Kessler → Myles Turner | +1.2 / +0.8 | +0.6 / +0.9 |
  | Kessler + Collins → Turner | +1.1 / +1.0 | +0.5 / +1.0 |
  | Lillard + George → OG Anunoby | +1.0 / +1.0 | +0.5 / +1.1 |
  | Suggs + Podziemski → Mikal Bridges | +0.9 / +1.3 | +0.4 / +1.4 |
  | Kessler + George → Turner | +1.1 / +1.1 | +0.4 / +1.2 |
  | Lillard + Suggs → Bridges | +1.1 / +1.0 | +0.7 / +1.1 |

- **The partner:** measured first. Their own free moves would cut their gain by 0.3–1.6 points and flip it negative in 6 of the top 15 fair deals (Big Tuo would just swap Beringer for Grayson Allen, +1.7). Martin decided against it as the default (Decision 5): managers look at the deal itself, so the partner is judged on the trade alone. The Trades toolbar's **Count their waiver moves** turns it on (`their_free`, remembered per browser): then fair search finds 9 deals, best about +0.6 for both.
- Fair search: none of the old top 20 survive. The best are about +0.8 to +0.9 for you and +0.8 to +1.0 for them, all with Mathmagicians, e.g. Kessler + George + Edgecombe → Bridges + Champagnie. Search still takes 0.8s.
- UI: the trade result names your assumed moves under the verdict, the finder says what it's scored against, Waivers lists the chain, and an open spot reads "Open spot" / "into your open spot". `ai.py` SYSTEM, the `/gm` skill, README and CLAUDE.md say the same.
- Test: a 2-for-1 whose only gain is the open spot scores +1.5 today and 0.0 on top of the free move. Search never offers the pickup, and a short roster gets a plain add.
- Checked in the browser (Oct 27): the trade result's note, the finder text and the Waivers line name the moves; **Count their waiver moves** re-scores the open trade (Mathmagicians +1.1% → +0.5% on Lillard + Suggs → Bridges), the finder (9 deals) and saved trades, and is remembered. No script errors.
- Not done: the weekly adds cap (4). The greedy chain ignores it, since over the rest of the season a cap only delays moves. Also, deals that send an IL player still got an open-spot pickup (fixed in N5, Oct 27).

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

**After P2 (Oct 27):** the numbers above are One Win. Under categories scoring they're about 0.4× (Vučević for Achiuwa is +1.2, the best fair trades about +1.1), so re-measure them first.

**Change:**
- **Your best free move**: the best add/drop, or a plain add into an open spot, by win-% gain. Use `_waivers_matchup`'s maths; repeat while a move still clears the waiver threshold.
- **Simplest version** (what the numbers above used): apply that move to your roster and the free-agent pool first, then run `trade()` / `search()` unchanged. The open spot after a 2-for-1 then gets the next-best free agent.
- **The other team**: do the same for the partner only if it changes their numbers noticeably. Measure first.
- **UI and AI output** say it outright, e.g. "Compared with just adding Vučević for Achiuwa (+3.1)". Waivers and Trades use the same baseline.

**Done when:** a test trade whose only gain is the open spot scores about 0, the real-league deals above show their after-waiver numbers, and the Trades view names the pickup it assumed.

### P4. Small fixes ✅ done Oct 27 (branch `trade-lab`)
- **Injured free agents in the waiver baseline:** `_waiver_level` skips `OUT` statuses (falls back to every FA if all are out). Real league: Kevin Porter Jr. (NA) and Tobias Harris (O) leave the top 5, Christian Braun and Grayson Allen come in, and replacement goes from −0.58 to −0.76. Standings barely move (40.4–61.9%, was 40.4–61.8%), and your free moves are unchanged (Jay Huff for P.J. Washington +0.3 → +0.4).
- **Waiver dates and New Year:** `this_week`'s claim date rolls into next year when it's more than 60 days in the past.
- **Backtest without the fill-in:** noted rather than added, next to `FILL` and in `backtest.py`'s docstring. Its rosters never change, so the real weekly totals have no fill-in either, and adding the term would bias the predictions. FILL stays a guess.
- **Doc drift:** README Setup and the new-computer steps use `.venv` (with a line on the bare `python` elsewhere), "Not modelled yet" says only trades and season odds use the average week, and the `/gm` skill imports the newest snapshot and runs every command as `.venv/bin/python` (unblocks N2).
- Tests: an NA free agent leaves the waiver level and lowers replacement, and a "Jan 2" claim read on Dec 30 gets 2 of 4 games left. Both fail on the old code.

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
- Later: playoff mode, which weights likely playoff opponents' weeks (needs task 1's schedule). `xhigh`
- Later: manager tendencies from Yahoo trade history (needs task 3's transactions). This is a weak signal, since rejected offers aren't visible. `high`
- Not doing: 3-team trades. Yahoo doesn't support them.

---

## Next up (planned Oct 26, after calibration)

One task per session: *"Read ROADMAP.md and do Next up N0"*. Run `python test_model.py` after model changes, and `python backtest.py` after anything that changes projections or odds. Suggested order: Do first (P1–P4, done), N0 and N1 (done), then N1b, N2, N3 (done), Q1 (done), **then N4 (done), N7 (done), N8 (done) (Martin, Oct 27: the numbers must react to expert projections, teammates' injuries and news)**, then N5 (done), N9 (done) then N10 (done) (Martin, Oct 27: sell on games missed, once that guess is trustworthy), Q2, Q3, the rest. After P2, every "win %" below means share of categories won.

### N0. Market value: trades priced like real managers price them (requested Oct 26, top priority) ✅ done Oct 27 (branch `trade-lab`)
**Result:** `League.market` ranks every player on Yahoo's Pre/Cur blend (Cur weighted `GP/(GP+HYPE)`, re-ranked; the model's perceived rank when Yahoo has none, shown as `#76*`) and values them `100 × MARKET^(rank−1)` with `MARKET = 0.97`. `priced()` turns a deal into `market: {give, get, diff_pct, verdict}`: each side's market value net of the players it drops and the free agents it picks up. Those are the same drops and pickups the matchup model makes, so `search()` and `trade()` give the same number (tested). `diff_pct` is the gain as a share of the bigger side, so a deal is ±the same from both ends. `TIERS` are now (your gain, theirs, market range): easy −30% to −5%, fair ±15% or Decision 4's second route (both gain, ±30%, `FAIR_FIT`), finesse −30% to +5% with your gain ≥ 1.5, hard ≥ +5%. No tier overpays over 30%, which is the cats/team guardrail. `accept()` reads the market: Likely yes when they win on it (and don't lose more than 0.4 in real odds), Long shot only when it's not fair-priced. `trade()` adds `tiers` and flags "You overpay on the market" / "Lowball" beyond 15%. `search(max_overpay=)`, `--max-overpay`, `/api/search` and the AI's `find_trades` take a cap that only tightens. Search still takes 0.7s.
- UI: a **Mkt** column (value + `#rank`) in every player table, a **Market** column (send → get, gap %) replacing "Looks to them" in deal tables, **Market** in the trade verdict and Saved trades, and a **Max overpay** select in the finder toolbar. "How it looks to them" is now a one-line note under the verdict. README, CLAUDE.md, DESIGN.md, `ai.py` SYSTEM and the `/gm` skill say the same.
- Real league (Oct 27 file): P3's best "fair" deals were huge overpays on the market. Lillard + Kessler → Myles Turner (Mkt #122) is "you overpay 93%", and Lillard + Suggs → Mikal Bridges (#134) is 89%. Both now clear no tier. Today's fair search returns 30 deals between −30% and +28%: 9 Likely yes, 21 Maybe. For example, Kessler + LaMelo + Suggs → Kevin Durant (+1.2 / +1.5, you overpay 8%) and Lillard + George + Ingram → Anthony Edwards (+1.3 / +1.3, they overpay 27%).
- **Trae Young, not a lowball after all.** On today's ranks Lillard (Pre 54 / Cur 10, market #33) and Kessler (Pre 38 / Cur 31, #28) are worth more than Trae (#17): Achiuwa + Lillard + Kessler → Trae is "you overpay 17%" (78 vs 65 with the two pickups), easy + fair, Likely yes. The done-when assumed the market saw Lillard and Kessler as spare parts. It doesn't, so this deal can't land in Hard. The curve wasn't bent to force it. A steeper curve (0.95) would make the trade even on the market.
- **Fair isn't all within ±15%**: 13 of the 30 sit between 15% and 30%, all with both teams gaining. That's Decision 4's second route, which came after this done-when.
- `goal="cats"` (TO + FG%, Jokić pinned): the old top deal, Jokić + Lillard + Ingram → SGA + DeRozan, is "you overpay 31%". It's now excluded, and `trade()` flags it.
- Tests: the curve, the rank blend (Cur weighted by games), the drop cost on both sides, every tier rule and both fair routes, `max_overpay`, `accept()`, `priced()`, both flags, and search pricing deals exactly like `trade()` (3-for-1s included).
- **Not checked in the browser**: the Playwright MCP's Edge profile was locked by another process ("Browser is already in use … use --isolated"). The API returns the new fields and the page script parses, but nobody has looked at the new columns yet, at desktop or 390px.
- N1's fair-search numbers will change, since fair now runs on the market.
- **Cur blend fixed (Martin, Oct 28):** "people obviously value Jalen Brunson way more." Achiuwa + Paul George → Brunson read "you overpay 48–57%, Likely yes". Two causes: Cur counted `GP/(GP+HYPE)` (4 games = 29%), and averaging ranks punished cold starts far more than hot starts were rewarded (Cur runs to 700 but can't beat 1). Brunson (Pre 27, Cur 217) sat at #81 behind George (Pre 75, Cur 23) at #50, and Giannis (Pre 5) at #22. Now `CHASE = 20` (market only: games until Cur counts half; `HYPE` still drives perceived value) and a geometric mean: Brunson #35, George #59, Giannis #8, LaMelo #24, Sabonis (Pre 36, Cur 3) #22. That deal reads "they overpay 52%" (Long shot, Hard), and Achiuwa + George + Lillard → Barnes "they overpay 25%" (Long shot). Checked against K = 10 / 20 / 30 and linear vs geometric on the Oct 28 file. 10 geometric still had Brunson #43 vs George #47. Test: the Brunson / George pair keeps its order. Note: Instinct's "Pre" moves between pulls (Kessler 38 → 24, Lillard 54 → 45 from Oct 27 to 28), so it's probably Yahoo's updated projected rank, not a frozen preseason one.
- **Left (Martin, Oct 27):** "current rank shouldn't matter much when I buy, but I should leverage it when I sell." The split already works that way: your side is the model's box-score value, and only the market (acceptance) sees Yahoo's Cur. But the market weights Cur only `GP/(GP+HYPE)` (Anthony Black, Cur 13 after one game, counts 9% and stays near his Pre 149), so hot starts barely register as trade chips. Add a separate setting for how hard the league chases Cur (market only, not `HYPE`). `high`
- AJ Dybantsa (Big Tuo, IL, Out) is the only snapshot player left out: a rookie with no NBA games, so no stat line. He's picked up automatically once he plays. Using Yahoo's Part 8 projection until then is optional (N4). Done in N4: he now has a line from Yahoo's projection.

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

### N1. Strategy panel (punt planner) ✅ done Oct 27 (branch `trade-lab`)
**Result:** `League.strategy(size=3, top=5)` runs each of 16 builds (none, 9 singles, `PAIRS`). Each build gets its own free moves (`after_free(build)`) and the top 5 fair deals from `search(punt=build)`, re-scored with `trade(free=False)` on top of those moves, on true odds. A build's `win` is the best of "waivers only" or "waivers + one trade". `pick` is the top build only when it beats balanced by more than `VERDICT` (0.2 in categories), otherwise `[]`. `python model.py strategy`, `/api/strategy` and the `/gm` skill expose it. `profile()["cheap"]` ranks categories by the odds one more player-z buys (a week of games at one pool std), which replaces "near 50%".
- **One design fix found while building:** with each build scored on its own top 5, single punts (FG%, 3PM, PTS, REB) "beat" balanced with no waiver moves at all. Their searches had surfaced a deal balanced's top 5 missed (ranking by min(you, them) and 3 per team), so the verdict would have said "Punt FG%" for a trade balanced could make too. Balanced is now re-scored on every build's deals. A punt only wins through what it alone unlocks: its waiver moves, or a trade that only pays after them. Tested as an invariant: a build with the same free moves as balanced never beats it.
- **Real league (Oct 27 file, fatty zhou, 58.6% today): stay balanced.** Balanced reaches 62.0: Vučević, Harris and Huff off waivers, then Lillard + George + Ingram → Durant (Big Tuo: +1.4 you, +1.2 them, they overpay 19%, Maybe). REB is top at 62.1 (+0.1, too close to call), on five adds (Huff, Allen, Vučević, Ellis, Harris) and the same Durant deal. Then STL 62.0, AST 61.9, PTS 61.8, FG%+3PM 61.6 … FT% 59.4, and 3PM+FT% last at 58.0, with no fair trade that helps. FT% punts rank near the bottom, as the Oct 26 analysis found.
- **Cheapest to move:** BLK +4.0, FT% +3.6, AST +3.4 per z. AST still moves a lot at 71% odds, and STL only +2.0 at 70% (noisy weeks), so "near 50%" would have ranked them wrong.
- **Test:** a league where three "bricks" (stars in everything but 1-for-12 at the line) sit on waivers. The first one costs more FT% odds than he adds, so balanced never takes one. With FT% punted all three pay, and the FT% punt wins by 3.5. That's the S-curve, which greedy balanced moves can't cross. Plus the invariant above and `cheapest()`'s ordering.
- **Speed:** 14s on the real league (16 size-3 searches at 0.8s each, plus re-scoring), so it runs on demand behind "Comparing builds…".
- **UI:** a Strategy panel on My team, after the roster: a verdict pill (Stay balanced / Punt X) with the plan in one line, and a builds table (waiver adds, best fair trade + partner, Them, Market, win % after, vs now, Load). A "current" tag marks your punt. Cheapest to move is a footer line on Category odds (punted categories drop out). The API output matches a `python model.py strategy` run exactly, and every panel state renders on real data (checked in Node).
- **Not checked in the browser:** the Playwright MCP's Edge profile was locked again ("Browser is already in use … use --isolated", held by a headless Edge started 15:33 Oct 27). Nobody has looked at the panel at desktop or 390px yet.
- **Left:** the AI GM chat has no strategy tool (`ai.py`); `/gm` covers "should I punt?". Load opens the trade scored on your current punt and the balanced free moves, so its numbers differ from the panel's build totals. `medium`

**Why:** toggling a punt chip only concedes categories on today's roster, so it always lowers your odds. A punt pays off through the moves it unlocks. The Oct 26 analysis on fatty zhou showed no punt beats staying balanced (best fair trade +3.7% unpunted, +3.6% punting FG%, +0.5% punting FT%+TO), but nothing in the app says that.
**Change:** `League.strategy()` loops over builds (no punt, each single category, FT%+TO, FG%+TO, FG%+FT%, AST+TO, 3PM+FT%, FG%+3PM). For each build: the best `search(tier="fair", punt=build)` deals and best `waivers(build)` moves, re-scored with `trade()` / `outlook()` on true odds (no concession). Return builds ranked by true win % after the best move. Add a panel on My team with build, best move, true odds after, and a one-line verdict ("Stay balanced" / "Punt X: it unlocks Y"). Also list "cheapest categories to move": the ones near 50%, where a trade or stream changes the odds most.
**Before building:** redo the Oct 26 analysis after P2 and P3 (both done, below). Its numbers are one-win odds and include free-pickup credit, and under Categories each conceded category is a straight loss in the standings, which makes punting cost more.
**Re-run after P2 (Oct 27, categories scoring, still with free-pickup credit):** no punt beats staying balanced. The best fair trade found with each build, re-scored on true odds: none +1.2 (Lillard + Suggs → Bridges), PTS +1.2, FG%/AST/TO +1.1, single 3PM/BLK +1.0, FT%+TO +0.4, FT% +0.2, 3PM+FT% −0.1. Conceding a category today costs 3.5 (TO) to 7.9 (AST) points of win %, and pairs 8–13.5, so a punt has to unlock far more than any fair trade offers.
**Re-run after P3 (Oct 27, your free moves made first):** still no punt beats staying balanced. Best fair trade per build, re-scored on true odds: none +0.9 (Kessler + George + Edgecombe → Bridges + Champagnie), PTS +0.8, FG%+FT% +0.7, FG% +0.6, 3PM/REB/BLK +0.5, STL +0.4, AST+TO +0.2, TO, FG%+TO and FG%+3PM +0.1, AST −0.4, FT% and FT%+TO −0.6, 3PM+FT% −0.8. Conceding costs 3.4 (TO) to 7.9 (STL, AST) points of win %, pairs 8–13.7. The whole loop (16 builds, size 3) took 14s.
**Speed:** 12 builds × an open search. Size 2 takes about 0.2s on the real league and size 3 about 2s on the demo, so use size 2 or run it on demand behind a "thinking" state.
**Done when:** the panel matches a script run of the same numbers, and the test covers a league where one punt clearly wins.

### N1b. Deeper punt planner: star fit, buy list, multi-trade plans (requested by Martin, Oct 27) ✅ done Oct 27 (branch `trade-lab`)
**Result:** `League.after_trade(give, get, punt)` returns a League copy after a deal. It uses the same rosters `trade()` scores (`_after()`'s drops and open-spot pickups), puts the drops back on waivers, and records the trade (with your drops, pickups and market price) in `.trades`. It shares `_copy()` with `after_free()`, which now accumulates `.free` across calls. `strategy(size=3, top=5, steps=3)` gives every build a plan: its free moves, then up to 3 trades in a row. Each trade is the one of the build's top 5 fair deals that lifts its own punted odds most, searched on the rosters the step before left. After the first step it searches size 2. Each trade is followed by the build's free moves again (`_step`). A step must clear the fair tier's terms for the other side (their odds and the market) against the rosters at that point, and raise the build's own odds. Players the plan brought in or sent away are never traded again. Every step is scored on true odds, and a build keeps its best prefix, or N1's single trade that lifts true odds most. Balanced tries every build's first deals (N1's rule) and replays every build's chain for as long as each trade lifts true odds. So a punt only wins through what it alone unlocks: its waiver moves, or trades that only pay off after a costly first one. Each step reports `win`, `add` / `drop` (net waiver moves, so an open-spot pickup a free move swaps out isn't one) and `trade` (with `later`: it needs an earlier step first). Builds add `stars` (your top 5 players' z ≤ −1 in the conceded categories) and `buy`. The buy list is the 5 players on other teams with the biggest gap between the market value of their rank on the build (`effective(punt)`, on the MARKET curve) and their real market value.
- **Speed: chains for all 16 builds, not a "promising 5".** Size-2 searches take 0.1s on the real league, so steps 2–3 add about 2s: 15.5s in all (N1: 14s). That beat the planned heuristic, which picked builds by their single-trade result and star fit. That would skip exactly the build whose first trade costs, so it was never built.
- **Real league (Oct 27 file, fatty zhou, 58.6% today): still stay balanced, and balanced gets much better.** Balanced reaches **63.8%** (N1: 62.0). The plan: Vučević, Harris and Huff off waivers (60.6), Lillard + George + Ingram → Durant (Big Tuo, +1.2 them, market +8%, Maybe; then add Gafford, Braun and Allen and drop Boozer: 62.2), Kessler + LaMelo → Karl-Anthony Towns (Mathmagicians, −19% market, Likely yes: 63.0), Suggs + Collins → Desmond Bane (Wesburn incident, +12%, Maybe: 63.8). Next come PTS 63.4, 3PM and STL 63.2, TO 62.7, FG% 62.3 … FT% 60.9, FT%+TO 60.0, 3PM+FT% 58.0. No best plan on the real league dips today, but 3PM's first trade is flat (+0.1) and its second is worth +3.8. Caveats: three trades in a row each need a yes. Two plan trades send Ingram and Suggs (both Out), whose IL-slot accounting was still loose (N5). So treat 63.8 as an upper bound. N5 fixed it (Oct 27): balanced now reads 56.5 → 61.2, see N5.
- **Star fit (real):** TO fits Jokić (−2.0) and Booker (−1.2). FT%+TO adds Kessler (FT% −1.9). 3PM fits Kessler (−1.7). **Buy lists:** Dyson Daniels (market #52) tops most builds. Rudy Gobert (#141) tops 3PM+FT% and is near the top for FT% builds. James Harden (#38) tops the TO and FG% punts. LeBron (#56) shows up for FT%/TO builds.
- **Tests:** `after_trade` matches `trade()` (both teams' win %, market, the drop on waivers and the pickup off them, the original untouched). The new chain test puts bricks (level 300, 1-for-12 at the line, Yahoo rank 40) on three other teams. On true odds the FT% plan goes 49.8 → 47.6 → 49.7 → 54.8: one trade can't get past the first-move cost, and two still leave you below today. `strategy(steps=1)` stays balanced, three trades make Punt FT% win by 5.0, each trade clears fair on its own, balanced makes no trade, and the bricks top FT%'s buy list. The invariant is generalised: a build that never lowers true odds and makes no waiver moves can't beat balanced. Also tested: each trade step's waiver moves keep your roster size, and star fit.
- **UI (checked in the real browser, desktop 1280px and 390px):** the Strategy panel shows the verdict, then the plan for the selected build. That's a steps table (Waivers, Trade 1–3 with partner, give, get, Them, Market, Will they say yes, win % after, Change and Load), with each trade's waiver moves on a muted line below. Load is greyed out on a `later` trade, and star fit is a footer line for punt builds. A **Buy list** panel uses `playerTable` with Team, Fit (#rank on the build) and Find offers. The build's conceded columns are dimmed along with your punt chips (`playerTable`'s new `dim`). **Every build** lists all 16 with moves, a path of win % after each step (red where a step lowers it) and the totals. Picking one shows its plan and buy list (selected row in `accent-soft`, DESIGN.md "Selectable rows"). N1's panel is now checked in a browser too.
- **Left:**
  - A free move under a punt can drop a real trade chip. Under FT%+TO, Lillard (market #33) is dropped for Vučević, because `free_moves` ignores market value. Such drops should be offered in a trade first. `high`
  - Receiving a player parked in an IL slot frees an active spot (FG3M's Kessler + Collins → Jalen Duren, then Huff into the spot). That assumes you have a free IL slot. N5 (Oct 27) now counts IL capacity. `xhigh`
  - The AI GM chat still has no strategy tool (`ai.py`); `/gm` covers it. `medium`

**Why:** Martin asked why the planner says "stay balanced" when the usual advice is that punting is efficient in 9-cat H2H, and whether the plan should start from his stars' weaknesses and say what to trade for. What the N1 session found (Oct 27 file, fatty zhou):
- **"Balanced" here already leans into weak categories.** The online case for punting is made against building by overall 9-cat rankings. Our engine scores moves by how much they change the odds, so it already gives TO (31% odds, 3.5 points to concede vs 6–8 for most others) about half the weight of the others.
- **The stars point to TO:** Jokić's only weakness is TO (−2.0 z), Booker's is TO (−1.2), Kessler's is FT% (−1.9).
- **Ruled out as the main cause:** scoring format (as One Win, a PTS punt edges balanced by +0.6, just over the bar; under Categories the edge is +0.1) and fair-only trades (on the finesse tier every build gains ~1.4, but punts still tie balanced).
- **The real gap: one trade per build.** A punt pays off over a series of moves, and the first move in costs the most (N1's brick test shows it with waiver adds). With waivers plus one trade, the planner can't see the path. Online punt advice is mostly about the draft, where the roster is built around the punt for free.

**Change (`model.py`, on top of N1's `strategy()`, `cheapest()`, `PAIRS`):**
- **`after_trade(give, get)`:** a League copy with both rosters changed, like `after_free()` does for waiver moves. Use `_after()`'s drops and adds, update `fa`, and clear the cached `vecs`, `outlooks` and `totals`.
- **Trade chains:** for each build, up to 3 trades in a row. Run `search(tier="fair", punt=build)` on the current copy, take the step, then re-run the build's free moves (open spots, new drops) and search again. Score every step on true odds, and keep the best prefix (0–3 trades). The punted search is what lets a chain take the costly first step. Each trade must clear fair on its own, against the rosters at that point. Balanced gets the same 3-trade chain (and keeps N1's rule: it's also scored on every build's deals), so the comparison stays fair.
- **Star fit:** for each build, your top players whose z in the conceded categories is ≤ −1 ("fits Jokić (TO −2.0), Booker (TO −1.2)").
- **Buy list:** for each build, players on other teams whose rank by `value(punt=build)` beats their market rank by the most. Yahoo's ranks count all 9 categories, so a high-TO or bad-FT% player is cheap on the market. Show the top 5 with team, market and value.
- **Speed:** N1's 16 single-trade searches already take 14s, and 3-step chains for every build would take ~45s. Run chains only for balanced plus the ~5 most promising builds (best single-trade result and best star fit). Use size 2 for steps 2–3 if needed, aiming for about 30s or less.

**UI:** the Strategy panel shows each build's plan as steps (adds, trade 1, trade 2, trade 3, with win % after each step), its star fit and its buy list, with Load on each trade. Honour punt dimming. Still check N1's panel in a real browser, desktop and 390px (the Edge profile was locked on Oct 27).
**Done when:** a test league where one trade can't get past the first-move cost but two or three trades do (the brick idea, with the bricks on other rosters instead of on waivers), so a multi-trade punt clearly wins. Record the real-league result here, even if it's still "stay balanced". The panel matches a `python model.py strategy` run.

### N2. AI GM chat through Claude Code (no API key) ✅ done Oct 27 (branch `trade-lab`)
**Settings (Oct 27):** the AI GM tab picks Model (Opus 5.5 default, Fable 5.1, which needs usage credits on Martin's Pro plan), Effort (medium default, low to max) and Web search (on: adds the `WebSearch` tool and a line in `WEB`). They're saved in localStorage, sent with each message, and checked against `MODELS` / `EFFORTS` in `app.py`. They don't reach `ai.py`. Tested in Edge: a Suggs question searched the news, cited 4 sites and quoted `model.py` numbers (23s).
**History (Oct 27):** `app.py` `chats()` keeps every conversation in `data/chats.json` (`/api/chats`: list, save, open, delete; written to a temp file and swapped in). New conversation keeps the old one, History reopens it with its Claude session id (so `--resume` remembers), Delete removes it. A chat from before history existed is saved the first time the tab opens. Tested in Edge: import, new, a live question, reopen, delete, and the 390px layout. On the `ai.py` path a reopened chat shows its messages but the model doesn't remember them.
**Built:** with no `ANTHROPIC_API_KEY`, `/api/chat` runs `claude -p --output-format json --safe-mode --model claude-opus-5-5 --effort medium --tools Bash --allowedTools "Bash(.venv/bin/python model.py:*)" --permission-mode dontAsk`, the gm SKILL.md (plus a short preamble: skip the import step, plain text, no markdown) as `--system-prompt`, and the message on stdin prefixed with your team and punt. The browser keeps the session id next to the chat in localStorage and sends `--resume` with it, so a follow-up survives an app restart. New conversation clears it. With a key set, `ai.py` runs as before.
- `--bare` was the obvious flag but forces API-key auth. `--safe-mode` keeps the login and drops CLAUDE.md, memory, plugins, hooks (ponytail) and MCP. A probe confirmed none of it reaches the GM's context. Read-only shell commands (`ls`, `head`) are still auto-allowed; anything that writes is denied.
- Checked in a real browser (Oct 27, real league): "who should I drop and pick up?" → Achiuwa for Vučević, +1.2 (from `model.py waivers`), 8–17s per answer. The follow-up "what was the player I'd drop?" answered Achiuwa, and after New conversation the same question got "nothing yet".
- Each chat is a Claude Code session in this folder, so they show up in `/resume`.

**Original plan:**
**Why:** `ai.py` needs a paid API key (billed separately from a Claude subscription). `claude -p` runs Claude Code headless with your own login.
**Change:** `/api/chat` shells out to `claude -p "<message>" --output-format json --resume <session_id>`, with `--allowedTools "Bash(.venv/bin/python model.py:*)"`, `--append-system-prompt` from `.claude/skills/gm/SKILL.md`, and the current punt in the prompt. Save the session id per chat. Keep `ai.py` as the fallback when `ANTHROPIC_API_KEY` is set. Show a "thinking" state, since answers take 10–30s.
**Watch out:**
- SKILL.md's commands now run as `.venv/bin/python model.py` (P4), so the `--allowedTools` pattern matches.
- `claude -p` also loads Martin's user-level settings, hooks (e.g. the ponytail SessionStart hook) and global CLAUDE.md. Check `claude --help` for a way to limit which settings it loads, so none of that leaks into GM answers.
**Done when:** a chat question in the browser gets an answer quoting `model.py` numbers, a follow-up question remembers context, and New conversation starts fresh.

### N3. Snapshot refresh loop ✅ done Oct 27 (branch `trade-lab`)
**Result:** the sidebar shows the snapshot's pull time (`state()["pulled"]`), and every view shows a notice once it's over a day old or its matchup week is over (`League.rolled`). That notice is at the top of the view, since the sidebar is hidden on phones. Waivers has **Recently dropped** (`League.dropped()`): players let go in `transactions`, newest first, that nobody has picked up since (10 on the Oct 27 file, from Aday Mara to Yves Missi). Once the snapshot's week is over, `League.__init__` takes this week's opponent and dates from `schedule`, with no score yet, so week 7 (Nov 30 – Dec 13) runs 14 days. Playoff weeks have dates but no opponent, so you pick one. Checked in the browser: a fake Oct 28 copy of the snapshot with PTS 250 dropped in the folder, then Refresh. The sidebar and the live score (100 → 250) both updated. Also checked at 390px, with no console errors. Q5 and playoff mode stay their own tasks. Apostrophe names (De'Aaron Fox) still parse.

**Original plan:**
**Why:** the league comes from Instinct text snapshots; re-importing by hand is the main chore.
**Mostly covered by P1** (Oct 26): Instinct's dated file has the matchup totals, ranks, Yahoo projections, transactions and records, and P1 parses them and makes Refresh pick the newest file.
**Left:**
- The sidebar shows the snapshot's pulled time (Q9).
- A "Recently dropped" list from `transactions` (Build next #3).
- Use the season schedule P1 parses (`schedule`, `rules`): next week's opponent when the snapshot is stale, the 14-day weeks 7 and 17, Q5 and playoff mode.

Keep checking that apostrophe names (De'Aaron Fox) survive in each new file.
**Done when:** dropping a new snapshot in the folder and pressing Refresh updates everything, including the live score.

### N4. Expert projections (Build next #4) ✅ done Oct 27 (branch `trade-lab`)
**Result:** every line now starts from `prior()`: `baseline()` blended with an expert line at `PROJ_W = 0.6`, then `ros()` leans on this season as before, so the expert part fades as games pile up. Martin asked for projections to count for more than the box scores, and the fit agrees: experts 60%, box scores 40%.
- **Sources** (`experts()`): Yahoo's rest-of-season projection from the snapshot (Part 8, 266 players) where it has the player, else ESPN's (`espn()`, 104 more). ESPN's endpoint needs a sort key in `X-Fantasy-Filter` (400 without one), gives 348 players this season in one 1.6 MB call, is cached a day and archived as `data/projections_<date>.json`. ESPN's 2026-27 numbers look like preseason projections that haven't been updated (Edwards still 72 GP / 35.8 min after knee surgery, Vučević 25 min after his trade), which is why Yahoo's go first. Yahoo has no minutes, so they're ESPN's (else ours) scaled by Yahoo's PTS+REB+AST over that line's: Yahoo's 7.8 pts for Vučević reads as a smaller role (about 14 min), not worse per-minute play. Names go through the same matching as `find()` (`match()`, now shared), so a spelling variant lands on the right player. A rookie's baseline is the fringe line, blended the same way.
- **Fit:** ESPN serves 2025-26's preseason projections (`seasons/2026`), and they weren't updated with hindsight. Their error on that season is the same as `baseline()`'s (0.246 vs 0.243), games played correlate only 0.25, and rookies miss (Flagg 19.5 projected, 21.0 actual). `python backtest.py replay` (new, and it saves the replay `ros()`'s docstring cites) scores each Monday's rest-of-season line against what players did after, stats scaled equally, for players with 10+ games left. Weight 0 → 0.6: week 0 error 0.3657 → 0.3463 (−5%), weeks 1–6 0.3021 → 0.2932 (−3%), season 0.2879 → 0.2826 (−2%). Top 200 by minutes: −7%, −3%, −2%. Rookies, weeks 1–6: 0.468 → 0.437 (−6%). 0.5–0.75 is flat; 1.0 is worse. Separate weights for minutes and production gained nothing, and `SHRINK` 20 / `MIN_SHRINK` 6 are still best.
- **Backtest:** `backtest.py` now drafts and predicts from `prior()` with ESPN's preseason projections. With the same draft, experts barely move weekly category log loss (0.6062 → 0.6075 average week, 0.5782 → 0.5788 real schedule): team-week noise swamps better player lines. The better-informed draft makes leagues more even, so the reported numbers rise (0.6186 / 0.5937 now) and aren't comparable with the old ones. Odds came out 1–3 points bold at the edges, so `SCHED_CV` went 0.15 → 0.17 (it includes projection error). That brought calibration back within 2 points (the 80–90% bin wins 83.9% / 83.8%), with slightly lower log loss. `DISPERSION` and `PCT_NOISE` didn't need to move.
- **Real league:** standings 38.2–61.2% (were 40.3–61.2%; Mathmagicians at the bottom), fatty zhou 58.9%, 2nd. One Win: 21–77%. Vučević 21.8 min / 11.4 pts → 16.1 / 8.4 (value −2.45 → −5.44): he's no longer a top waiver add. Achiuwa is still the top drop, and Tobias Harris for him is now +1.0. AJ Dybantsa now has a line (Yahoo's, blended with the fringe line) and sits on Big Tuo's roster instead of being left out.
- **Shown:** `player()` has `proj` ("Yahoo" / "ESPN"), so team, trade and search JSON say which projection a line came from. The player card has a "Yahoo projection" or "ESPN projection" row under "Rest of season", with Yahoo's minutes shown as a dash. README, CLAUDE.md, `ai.py` SYSTEM and the `/gm` skill say the same.
- **Also fixed:** `search()` pinned to a player you `get` without naming his team returned only 3 offers, because the outer `_rank()` call applied its 3-per-team cap. The UI always names the team, so only the CLI, `/gm` and the AI were hit. A pinned get now fixes the team before the cap.
- **Browser check (Oct 27, after commit `d2fc9ae`):** it found a bug. ESPN leaves out stats it projects as zero (27 players, all 3PM: Duren, Gobert, Zubac…), so their lines had `NaN` and `/api/state` broke the page ("Server returned 200"). `experts()` now reads a missing stat as 0 (Duren's PTS = 2 FGM + FTM confirms it), and a test covers it. After the fix every read-only API returns valid JSON (all 172 cards, strategy included), all seven views load with no console errors, and the card's Yahoo row shows the dash for minutes and dims punted columns.
- Tests: Yahoo over ESPN, Yahoo's minutes scaled from ESPN's, a spelling variant matched, a Yahoo-only rookie with no minutes left out, the `prior()` blend for a veteran and a rookie, unprojected players unchanged, `proj` in `player()`, and the card's Yahoo line.
- Not done: in-season expert updates can't be backtested (only preseason projections are archived anywhere), so an updated projection gets the same 0.6 as a preseason one, and this season's games are partly counted twice (the expert already saw them). Revisit once a few weeks of `data/projections_<date>.json` and dated snapshots exist.

**Original plan:**
**Why:** the baseline is home-made (two seasons + regression). Expert ROS projections know about role changes and rookies.
**Source arrived Oct 26 (snapshot Part 8, parsed by P1):** Yahoo's ROS projections per game for all 168 rostered players and the top 100 free agents.
- No MIN column. Keep MIN from our own line, since the z-score pool is picked by minutes.
- No projection for 12 rookies, including Boozer, Peterson and Dybantsa, which is where projections would help most. ESPN (below) can fill them.
- Each dated snapshot is a record of that week's projections, so keep the files for a later backtest.

**Other source found Oct 26:** ESPN's public fantasy API has free rest-of-season projections. `lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/2027/segments/0/leaguedefaults/3?view=kona_player_info` returns 50 players by default; projections are the `stats` entries with `statSourceId 1`, `seasonId 2027`, `statSplitTypeId 0`, in `averageStats` keyed by ESPN stat ids (0 PTS, 1 BLK, 2 STL, 3 AST, 6 REB, 11 TO, 13 FGM, 14 FGA; verify the rest, e.g. FTM/FTA/3PM/MIN). Getting every player needs ESPN's `X-Fantasy-Filter` request header (a paging/filter parameter). It's unofficial, like the ESPN injury feed: cache it daily and degrade quietly. Rotowire projections are behind a paid subscription. Instinct's Part 8 (Yahoo projections) is the alternative.
**Change:** as in Build next #4, fed by ESPN (above), Instinct, or a CSV. Blend with `baseline()` rather than replace it. Backtesting needs archived projections, so if none exist, start logging this season's projections weekly to compare later.
**Martin, Oct 27: projections should count for more than the box-score blend.** Experts update them for trades, role changes and depth charts, which box scores only show after games are played (Yahoo projects Vučević at 7.8 pts, the model 11.4). Plan:
- Fetch ESPN's projections for every player daily (`_cached`, like the injury feed). Use Yahoo's from the snapshot where present. Yahoo's minutes are missing, so take minutes from ESPN or keep ours.
- Blend: projection weight high early, easing as this season's games pile up. Check first whether ESPN serves 2025-26's preseason projections (`seasons/2026`). If it does, `backtest.py` can fit the weight against our baseline. If not, pick it by judgment, say so, and save each day's projections to `data/projections_<date>.json` for a later fit.
- Show it: the player card gets a "Yahoo / ESPN projection" line next to ours, and `team` / `trade` JSON say which source a player's line came from.
- Done when: re-run the replay (`ros()`'s docstring) and `backtest.py`, standings stay realistic, and the Vučević / Achiuwa swap reflects Yahoo's projection.

### N7. Teammates' injuries move minutes (Martin, Oct 27) ✅ done Oct 27 (branch `trade-lab`)
**Result:** while a regular (25+ min) is out with an ESPN return date, his teammates' lines go up for their games before the return. `lifts(line, team, keys)`: `SHARE` (0.37) of his minutes go to NBA teammates playing 10+, split by room under `ROOM` (36 min) × e^(`ROLE` 0.5 × role similarity: cosine of per-minute AST, REB, BLK and 3PM). They play them at their own rates, and take `USAGE` (0.25) of the FGM/FGA/FTM/FTA/PTS/AST/TOV those minutes don't replace, split by output. `had(logs, team, lift)`: the average lift each player had this season while a teammate sat. `League.__init__` reads this season's box scores net of it before `ros()`, so a boost from games a teammate missed isn't taken as the new normal and goes away when he's back. `_boost(end)` adds the injured regulars' lifts (`self.lift`) for the share of each player's games before the return: rest of season in `self.p`, this week in `self.p_week` (`week_vec()`, so This week and streams). `player()["lift"]` (`lifted`): who, back when, how many of the player's games come before that, and the per-game MIN and counting stats, for lifts of 0.5+ min with at least one game before the return. The real league loads in 0.5s.
- **Fit (in-sample, 2025-26 box scores, team-games with a 25+ min regular out, teammates' full-strength lines):** 37% of his minutes go to teammates playing 10+, split by room under 36 min × e^(0.5 × role similarity). Similar roles get about 2× the minutes, and 36+ min players get about none. Minutes MSE 53.5 → 45.5. Usage: about 0.25 of the unreplaced FGM/FGA/FTM/FTA/PTS/AST/TOV is absorbed (0.17–0.29 each); REB, STL, BLK and 3PM are about 0, so they're left out. Absentees under 25 min fit far lower shares (0.15 at 15+), hence `REGULAR`.
- **The "~1.1 min `ros()` bias" was selection, not a bug.** Next week's actual line is averaged over the games a player played. Players who missed some of their team's games that week (hurt mid-game, back on a minutes limit, in and out of the rotation) ran 3.6 min under their line in the games they did play. Players who played every game were 0.5 min *under*-projected. Availability covers the missed games, not the per-game line, so the replay now scores on full weeks. Full weeks select too, since a fringe player plays every game partly because a teammate is out, so the knobs were also checked on players projected 20+ min, where playing every game is about health.
- **Replay (`python backtest.py teammates`, ~3 min, full weeks):** with an injured teammate (3,996 player-weeks), minutes error 0.1696 → 0.1655 (−2.4%) and stats 0.4141 → 0.4119 (−0.5%). Projected 20+ min (2,621): minutes 0.1250 → 0.1229, stats 0.3730 → 0.3712. Over everyone at 20+ min, the minutes bias goes +0.08 → −0.02. Without the `had()` correction, full-week minutes error is a bit lower (0.1632 vs 0.1655), but stats are worse in every group, everyone at 20+ min ends up overprojected (bias −0.38 vs −0.02), and minutes over all weeks get worse (0.2248 vs 0.2212). So the correction stays. Lifts still run small on full weeks (actual minus line, 4+ min lifts: +6.4 → +3.5; 2–4 min: +3.3 → +1.9). Still, on 20+ min players SHARE 0.37 to 0.8 lands within 0.1% on minutes, and stats prefer 0.37; USAGE 0.25 vs 0.5 is within 0.1% too. So the knobs stay at the in-sample fit. With partial weeks included the error barely moves (minutes 0.2219 → 0.2212, stats 0.4842 → 0.4853).
- **Matchup backtest (scratch run, not added to `backtest.py`):** N7's lines in `build()`, with regulars who sat their team's last game sitting until their next one (exact returns, in both runs): category log loss 0.5072 → 0.5073, matchups 0.4977 → 0.4984, every calibration bin within 0.2 points. That's neutral, as with N4: team-week noise swamps better player lines. Without the sitting, an injured player's minutes count twice (his own at his usual availability, plus his teammates' lifts) and log loss gets worse (0.5868 → 0.5877). The sitting itself turned up a bigger gap, now under Model accuracy.
- **Real league (Oct 27 file):** LaMelo Ball gets +0.7 min, +1.2 pts and +0.3 ast a game while Anthony Edwards is out (back Dec 21, 23 games), and +0.6 min while DiVincenzo is out (Apr 2). His This-week line is 28.6 min / 20.7 pts vs 28.0 / 19.8 rest of season, so the done-when holds. Achiuwa gets +1.0 min from Keegan Murray (Nov 6, 5 games). Biggest lifts on waivers: Aday Mara +2.3 and Grayson Allen +1.4 (a top This-week stream). 35 of 167 rostered players and 131 of 214 free agents have some lift, mostly 0.5–1 min, because a star's minutes spread over a whole rotation and low-minute players have the most room. Standings 38.6–61.1% (One Win 22.3–76.4%), fatty zhou 59.1%, 2nd.
- **UI (checked in Cursor's browser on the real league, desktop and 390px):** every `playerTable` shows a `+1.3 min` tag after the name when a player's lifts add up to a minute or more. Hovering it lists each teammate, his return date and the games. The player card lists every lift in a sentence ("While Anthony Edwards is out (back Dec 21, 23 games): +0.7 min, +1.2 PTS, +0.3 AST, +0.2 TO a game.") with punted categories dimmed. Tags show on My team, This week's streams, Waivers and League rosters.
- **Tests:** `lifts()` (same team only; 10+ min rotation; regulars only; the total is SHARE × his minutes; similar roles get more; no minutes past ROOM but still a usage share; REB and STL only through minutes; the usage share of PTS). `had()` (games since his first for the team, averaged over the player's games). `_boost()` (4 of 6 games left in `p`, all of this week's in `p_week`, another team's player unchanged, `week_vec` up). `player()["lift"]`, no lift without a game before the return, and box scores read net of `had()`.
- **Left / known gaps:** two sitters on one team are each spread as if the other played. An expert projection may already price in a long injury (DiVincenzo), which then counts twice for its 60%. Lifts follow ESPN's estimated returns, and a player hurt mid-week only counts once ESPN lists him. `high`

**Why:** when a starter is out, his minutes and shots go to teammates, and that's often the biggest short-term value swing in fantasy. Today the ESPN feed (every 3h, with return dates) only marks the injured player out. Keegan Murray (out to ~Nov 6) explains Achiuwa's 31 minutes. Anthony Edwards (knee surgery, out to ~Dec 21) should lift LaMelo Ball and the other Minnesota players for about 25 games. The model sees neither until games are played, and then can't tell it's temporary.
**Change:**
- Learn the redistribution from 2025-26 game logs. For each team-game where a regular (25+ min) sat, how did teammates' minutes and per-minute usage (FGA, AST) move vs games he played? Expect most of it to go to same-position players already in the rotation.
- Apply it per game: until an injured player's return date, his teammates get the boost. This flows into This week (`week_vec`), streaming, and rest-of-season lines through the share of games before the return. The boost leaves when he's back.
- The fast-minutes blend (`MIN_SHRINK`) already picks up a role change after a few games. Remove the injured player's absence from it so the boost isn't counted twice, and so Achiuwa's minutes drop back when Murray returns.
- Backtest it: does projecting next week with "teammate X is out" beat not knowing it? Keep the matchup calibration.
- Show it: the player card and tables note "+3.5 min while Edwards is out (to Dec 21)".
**Done when:** the replay shows next-week error falls for players with an injured teammate, and LaMelo's This-week value rises with Edwards out.

### N8. News on the player and the team (Martin, Oct 27) `high` ✅ done Oct 27
**Result:** `news_index()` pulls ESPN's 1000 most-owned players (id and time of each player's latest news, cached 3h). `news(keys, since)` fetches a player's Rotowire blurbs (`site.api.espn.com/apis/fantasy/v2/games/fba/news/players?playerId=`; one player per request, videos dropped) only when the index shows newer news than `data/news.json` has, 8 at a time. `load()` passes it to `League(news=)`, so tests stay offline. `card()` adds `news` (latest 3). `roster_news()` (`/api/news`, `python model.py news`) lists news on your players and their NBA teammates playing 10+ min, since the snapshot or the last 2 days (whichever is longer), newest first, `new` = after the snapshot.
- UI (checked in Cursor's browser on the real league, desktop and 390px): a **News** panel on My team under Roster: your players' items, then teammates' (5 shown, the rest behind a native `<details>` toggle). The card lists his 3 latest items with the story. Both load after the page, so My team doesn't wait on ESPN.
- Real league (Oct 27, 10pm): 142 items over 2 days, 13 on Martin's players, 24 since the snapshot. Cold fetch 3s, cached 0.8s. Most teammate items are box-score recaps. Edwards' surgery news (Oct 27, 3:52pm) is there, and it matches the injury feed's Dec 21 return.
- Tests: `roster_news()` (who's asked, the window, order, `mine` / `new` / owner), the card, and `news()` with ESPN stubbed (name matching, refetch only on newer news, `since`, videos dropped).
- **Left:** Q10's daily brief can call `python model.py news`. The API-key chat (`ai.py`) has no news tool (the `claude -p` chat gets it through SKILL.md). Teammates follow our NBA team map, which can lag a trade for unrostered players: Santi Aldama shows as MEM while his news says Dallas. Recaps could be filtered out of the teammates list if they crowd out the injury and rotation items. `medium`

**Why:** some news isn't in any feed: rotation changes, coach quotes, trade rumours, minutes limits. The AI GM can search it now (Web search, Oct 27), but the app's views don't show it.
**Change:**
- Player card: the latest ESPN player news (headline, date, text), cached like the injury feed.
- A **News** panel on My team: anything new on your players and their teammates since the last snapshot.
- Q10's daily brief (a `/schedule` routine) runs the same check and messages you.
- News text never changes a number by itself. The engine reacts through data: return dates (N7), minutes (done), expert projections (N4). The AI GM explains the rest with sources.
**Done when:** a player card shows his news, and My team lists news on your roster's teammates.

### N5. Daily lineup cap and IL slots ✅ done Oct 27 (branch `trade-lab`)
**Result (Oct 27, second session):** the IL slots were the real distortion. The season-long lineup cap was measured and left out.
- **Season-long cap: measured, not built.** On the real schedule (153 days left, 15 of 30 NBA teams play on an average day), each real roster's 13 active players with their availability lose **0.1–0.9% of their games** to the 10-starter cap (fatty zhou 0.2%). Starters 1–10 never sit, and the 11th/12th/13th men start 99/97/95% of their games. A 3-for-1's two pickups land at ranks 12–13 and lose ~0.1 games a week between them, worth hundredths of a win %. That's not worth breaking `vec()`'s additivity, which the vectorized search relies on. So `week` stays uncapped and the backtest needs no change (nothing it scores moved). The cap stays where it bites: This week's heavy days (`starts()`).
- **IL slots: done.** Every parked player's row used to count, with his missed games "covered" as if he held an active spot. That was worth **+5 win %** to each team with 2–3 parked players. Now `League.il` tracks who's parked per team, `il_n` slots (`settings.il`, read from `+ 3 IL`; else the most any team uses), and a parked player counts `week_il`: no cover, and from his return he bumps a waiver-level week (`back` = share of games left after it; 0 if he wouldn't beat a waiver player on `effective()`: Edey, Dybantsa and Naji Marshall today). Banchero nets +26.5 PTS/week, Ingram +19, Suggs +5.
- Trades keep each side's active count: injured players coming in park in free IL slots (longest out first), sending a parked player frees nothing, and an injured player coming in is never the drop. Same maths in `_after()` and `search()`, including `their_view`'s drops and pickups. 1,799 real-league deals sending Ingram/Suggs or getting an injured player score identically in both. Test: a parked player counts less than an active one, sending him costs a drop, getting an injured player into a free slot gives a pickup, search = trade.
- **Real league, categories:** standings now run 40.7–56.7% (was 37.1–62.6%). Sharks 62.6 → 56.7, fatty zhou 60.6 → 56.5, John Basketball 59.1 → 54.7, Big Tuo 48.3 → 43.2. Teams with empty IL slots rise about 4 points (Wesburn 47.5 → 51.7, Mathmagicians 37.1 → 40.7). The punt planner still says stay balanced: 56.5 → 61.2 (free move Harris for Achiuwa; Lillard + Edgecombe + Kessler → KAT + Jalen Williams, who parks in your third IL slot, so you add Gafford and Allen; George + Boozer → Trey Murphy; Ingram + PJ Washington → Bane + Cason Wallace). `strategy` takes ~21s (19 before).
- Ingram → Bane alone now costs a drop (Lillard, after the free moves), where it used to be free: Ingram leaves an IL slot and Bane needs an active spot.
- **Market fix (Martin, Oct 27):** Achiuwa + George + Lillard → Scottie Barnes read anywhere from "you overpay 15%" to "they overpay 39%" depending on the punt chip. Open-spot pickups were priced at the picked player's own market value, and on a free-move copy that could be your own just-dropped Kessler (#28), LaMelo or Edgecombe, or Tobias Harris (no Yahoo rank, model #56). An open spot is now worth `League.spot`, the waiver level's market value (1.1 today), whoever fills it. The deal reads "you overpay 15%" (Likely yes) under every punt and toggle. The Cur weight stays `GP/(GP+HYPE)`. For reference, `GP/(GP+20)` would flip this deal to "you underpay 30%" (Barnes #20, Lillard #42, George #63), a big swing for one knob.
- Not done: IL eligibility is `status in OUT` (Yahoo's IL / IL+ / NA rules differ a little), IL / IL+ / NA share one pool of `il_n`, and a returning player bumps the waiver level, not his team's actual worst player. Positions are still ignored.

**Done for This week (Oct 27):** `starts()` counts only the best `lineup` (10, `settings.starters`) players with a game each day, in `week_vec()`, the games-left count and streaming adds. Martin spotted it: the app showed 35 games left for both teams, Yahoo 32 (fatty zhou) and 27 (kobe). It now shows 35 and 32: kobe had 13 players with games on Wednesday. The rest of the gap is lineups set by hand (Yahoo counts each day's set lineup, the app assumes the best players start). Positions are still ignored. Left: the season-average week (`League.week`, trades, standings) and `backtest.py`.
**Why:** every rostered game counts today, so depth (and streaming the 13th man) is overvalued vs stars, and 3-for-1 deals look better for the side taking 3. Positions now come from the snapshot.
**Change:** simulate the 10 active slots per day from the NBA schedule (best players start), in `week_vec()` and the season-average week. Add the cap to `backtest.py` and re-fit if the odds shift.
**IL slots (found in P3, Oct 27):** team totals (`vec`) add up every rostered player, IL slots included, which is why `_after` holds the total roster size. So a trade that sends an IL player still gets an open-spot pickup it couldn't make in Yahoo (after P3, the next-best free agent), and most top fair deals send Suggs or Ingram. Lineup-aware totals should leave IL slots out and count active spots in `_after` and `search`.
**Done when:** a 3-for-1 consolidation scores higher than before for the side getting the star, and the backtest stays calibrated. (Measured instead: the cap moves a 3-for-1 by hundredths of a win %, see Result.)

### N6. Smaller model items `high`
- Matchup correlation: a shared games-played factor across counting stats (backtest shows ±3 points at the matchup level).
- Trade log: record offers sent and accepted/declined. That's the only real data for tuning `HYPE`, `TIERS` and `accept()`.
- (Applying for Yahoo API access moved to "For you".)

### N9. Availability: one lost season isn't fragility (Martin, Oct 27) ✅ done Oct 28 (branch `trade-lab`)
**Result:** `availability(best, played, games)`, shared by `League._availability()` and `backtest.py`: `(AVAIL_PAST × best + played + AVAIL_K × AVAIL_T) / (AVAIL_PAST × 82 + games + AVAIL_K)` with 0.2 / 50 / 0.70. `best` is the games in the better of the last two seasons: `baseline()`'s `GP` now means that, not the weighted sum. Rookies get `(played + 50 × 0.85) / (games + 50)`. The status tag or ESPN return date then applies as before.
- **Backtest (`python backtest.py availability`, a few seconds):** on each Monday from week 1 to 4 weeks before the end of 2024-25 and 2025-26, it takes the 200 best players on that Monday's line who played their team's last game. It compares the guess with the share of his team's remaining games he played, as squared error weighted by games left. That's 5,409 player-Mondays, and players actually played 76.3%. 2024-25 needed `stats_2022-23.csv` and `gamelogs_2024-25.csv`, both fetched now.

  | group (player-Mondays) | before: error / bias | now |
  |---|---|---|
  | all (5,409) | 0.0471 / +4.6 pts | 0.0408 / +0.3 |
  | 2024-25 / 2025-26 | 0.0452 / 0.0490 | 0.0410 / 0.0407 |
  | weeks 1–6 (1,633) | 0.0424 / +4.5 | 0.0375 / −0.5 |
  | lost one season (<30 GP, 60+ in the other; 244) | 0.0585 / −11.8 | 0.0374 / −4.0 |
  | repeat absentees (<60 in both; 425) | 0.0636 / −7.8 | 0.0516 / −2.6 |
  | sophomores (318) | 0.0641 / −0.3 | 0.0644 / +7.6 |
  | rookies (183) | 0.0409 / +2.1 | 0.0365 / +1.9 |

  Held out: the best knobs on 2024-25 alone (0.5, 50, 0.65) score 0.0416 on 2025-26 (before: 0.0490), and the best on 2025-26 (0.2, 50, 0.7) score 0.0410 on 2024-25 (0.0452). The fit is flat for `AVAIL_PAST` 0.2–0.33 and `AVAIL_K` 40–70.
- **What the data said:** for players who are playing now, past games played barely predict the rest of the season. Correlation with a past season's GP is 0.04 (2025-26) or 0.18 (2024-25), and 0.26 with this season's rate. 82-game iron men played about 77% of the games left. Dropping the worse past season won in every fit. That also fixes sophomores: last year's rookies had their missing older season counted as 82 games missed (Cooper Flagg 67% → 74%). Players out on a Monday (scratch check, not in the mode), scored on the rate from their return on, 1,542 player-Mondays: 0.0697 / +7.7 → 0.0630 / +1.4.
- **Matchup backtest (`python backtest.py`):** category log loss went 0.5760 → 0.5601 (average week) and 0.5119 → 0.5062 (real schedule). Matchups went 0.5992 → 0.5703 and 0.5141 → 0.5043. At `SCHED_CV` 0.12 the odds turned timid (real schedule: 90–100% won 96.5%), so it's now 0.10, which had the best category log loss in both modes (0.08 was worse). Every category bin is within 1.5 points (real schedule: 0–10% won 4.4%, 80–90% won 86.2%, 90–100% won 95.9%), and matchups are within about 4 (10–20% won 19.4%).
- **Real league (Oct 28 file), plays % and rank on your build:** Kessler 48% → 72% (#98 → #30, market #26). Lillard 45 → 70% (#122 → #42, market #41). Haliburton 45 → 69% (#71 → #15, market #10). Kyrie 39 → 61%, Tatum 58 → 76%, Trae 58 → 77%. Paul George 61 → 67% (#65 → #47) and Embiid 55 → 65% stay below the returners. Iron men lose their edge: Mikal Bridges 95 → 79% (#68 → #100), Brunson 86 → 76%. Rostered players now run 61–86% (std 0.04), not 39–95% (0.12). Standings went from 39.9–55.9% to 37.4–58.6%.
- **Finesse edges shrink:** Achiuwa + George + Lillard → Scottie Barnes went from +2.2 for you (accept) to −0.5 (decline), since Lillard no longer counts as missing half his games. For N10, Kessler is no longer a sell.
- **Tests:** `baseline()`'s GP is the better season (5 / 58 → 58), one lost season ranks above a repeat absentee, this season's games count, and the rookie formula.
- **Left:** sophomores now run 7.6 points high (318 player-Mondays, not fixed). `STATUS_AVAIL` is still a guess, and it now multiplies a higher rate for players who lost a season and are out with no ESPN date (the Oct 26 note says players who missed a season played 27%). Past seasons use a flat 82 games.

**Why:** the finder already sells players who'll miss games at Yahoo-rank prices: your side counts expected games, the other manager's yes runs on the market, which mostly doesn't. The Achiuwa + George + Lillard → Scottie Barnes deal is that play. But `_availability()` reads games played over the last two seasons plus this one, shrunk halfway to 85%. It can't tell a fragile player from one who lost a single season to one injury and is healthy now (games 2024-25 / 2025-26 / this season, Oct 27):
- Walker Kessler 58 / 5 / 3 of 3 (shoulder surgery): plays 48%, so market #28 but #92 to you.
- Damian Lillard 58 / 0 / 2 (Achilles, 36): 45%. Haliburton 73 / 0: 50%. Kyrie 50 / 0: 43%. Tatum 72 / 16: 57%. Trae 76 / 15: 58%.
- Paul George 41 / 37: 60%, the genuinely fragile case.

So the biggest market-vs-worth gaps, the edges finesse deals run on, partly come from this blind spot. The finder would sell Kessler as if he'll miss half his games, and steer you off Haliburton-type returners who might be buys.
**Data:** per-season GP in `data/stats_*.csv` (2023-24 on), this season's box scores (`gamelogs()`), ESPN's injury feed archived daily since Oct 27 (`data/injuries_<date>.json`, no older injury history).
**Change:** fit the rule instead of guessing. Add a `backtest.py` mode that predicts each player's rest-of-season share of games played on 2025-26 from 2023-24 / 2024-25 GP and games so far (each Monday, as `replay` does), then compares the current formula with alternatives: one lost season counting less, this season's games counting more once he's playing, the worse past season down-weighted, a different shrink target. Keep `effective()` as the only way value gets weighted by availability. Then re-run `python backtest.py` (availability feeds the matchup odds) and check the standings range.
**Done when:** the backtest scores availability guesses against real games played, the chosen rule beats today's, single-injury returners (Kessler, Haliburton) move up while repeat absentees (George) stay down, and calibration holds.

### N10. Sell list: players the market overrates (Martin, Oct 27) ✅ done Oct 28 (branch `trade-lab`)
**Result:** `League.sell(punt, n=5)`, the buy list's mirror: your players whose market value beats their worth to the build (`effective(punt)` rank on the MARKET curve) by 5 points or more (the player card's "about right" band), biggest gap first. `strategy()` gives every build a `sell` list. Each row has `why`: the gap splits into parts on the MARKET curve that add up to it, and the biggest ones (a third of the top part or more) are named. **Hot start:** market rank above Yahoo's Pre, only when Cur beats Pre. **Yahoo:** the rest of the market rank against our per-game rank (no Yahoo rank: the model's read of this season). **Games:** per-game rank against the one over the games he'll play, shown against the rostered average. **Build:** his value is in the punted categories (z shown), or "the punt lifts other players past him".
- **Games barely explain sells anymore.** Since N9, rostered players average 73% plays (not ~85%), and my players run 65–86%. So the real-league gaps (Oct 28 file, fatty zhou) mostly come from Yahoo projecting players higher than we do. No punt: LaMelo (market #24, fit #41, "Yahoo projects him #18, we have him #46"), Boozer (#51 / #75, Yahoo #50 vs our #110), Booker (#21 / #27), Kessler (#26 / #30, 5.4 points: Yahoo #24 vs #28, plus he plays 72%). Lillard (0.9) and George (−7.5) aren't sells now. Under a punt the build takes over: FT% puts Booker on top ("his value is in FT% (+2.4)", fit #60) and Lillard (#64), FG% puts Kessler on top (FG% +2.8, fit #87), 3PM puts LaMelo there (+2.3, fit #99).
- **UI:** a **Sell list** panel after the Buy list (Fit, Why, Find buyers), with the build's punt dimmed. `playerTable` extras can take a `cls` for text columns (`td.why`, 240px). Checked in Edge on the real league at 1280px and 390px. Find buyers opens Trades with "Buyers for LaMelo Ball".
- **Tests:** Yahoo overrating (rank #1, our #49) and a hot start (Cur 1, Pre 30) top the list in that order with their reasons, a free-throw specialist is only a sell with FT% punted ("His value is in FT%"), every row is yours with a gap of 5+, sorted.
- **Left:** the buy list still lists gaps under 5 (any gap > 0), so a buy-list player's card can say "about right". The AI GM chat still has no strategy tool (`ai.py`); `/gm` covers it.

**Why:** the mirror of the punt planner's buy list (N1b): your players the market prices furthest above their worth to your build. Today (Oct 27): Kessler (market #28, #92 to you), Lillard (#33, #111), George (#51, #77), Boozer (#49, #88: rookie hype, not games). Hot & cold's sell-high only covers hot starts (per-game `HYPE` gap), not missed games.
**Change:** `strategy()`'s `buy` maths flipped for your roster (`sell`: market value − `effective(build)` value on the `MARKET` curve, largest first), shown next to Buy in the Strategy panel, each row with its reason: plays % vs the league's ~85% (games), or Cur vs Pre rank (hype). Find buyers from the row (the player card has it). Honour punt dimming.
**Do after N9**, or it tells you to sell Kessler.
**Done when:** Strategy shows a Sell list for the chosen build with each player's reason, and Find buyers works from it.

### Quality of life and genuinely useful views (Oct 26 list, rough priority)
All inline (no modals, per DESIGN.md); every new stat table honours punt dimming.
- **Q1. Player card.** ✅ done Oct 27 (branch `trade-lab`). `League.card()` (`/api/player`): `player()` plus per-game lines (rest of season; this season, last 14 and last 7 days from `gamelogs()`, this season's box scores cached 12h; a window with the same games as the one before is left out), NBA games left this matchup week and in the next 3 (`weeks()`: the season schedule's weeks, else Monday–Sunday), and his rank on your build (`effective(punt)` on the MARKET curve, like the buy list) next to his market rank. Clicking a name in any `playerTable` opens it in a row under the name, pinned to the visible part of the table, with punt dimming, a one-line market read and Find buyers / Find offers. Checked in Edge on the real league at desktop width and 390px. `backtest.py` now fetches through `gamelogs()`. **Left:** ~~Compare (Q3)~~ done Oct 28. Names in deal rows, waiver moves and sentences don't open cards (Load a deal to see its players in a table). Q2 can reuse `League.logs` for trend arrows and Hot & cold.
  Original plan: click any player name anywhere to open an inline panel:
  - ROS line, this season, and last 7/14 days
  - games this week and the next 3 weeks
  - injury note and return date
  - market rank vs model rank (N0), and owner
  - buttons: Find offers / Find buyers / Compare

  It replaces hunting through tables.
- **Q2. Recent form.** ✅ done Oct 28 (branch `trade-lab`). `League.zform` / `fp_form`: per-game lines over the last `FORM_DAYS` (14) of `League.logs` for players with `FORM_GP` (3)+ games, z-scored against the rest-of-season pool (`zscores(ref=)`) so `value(frame="form")` sits on the same scale as `value()` (this season's stats without box scores). `player()` adds `form`, `form_gp` and `trend` (up / down when the gap is `TREND` 3.0+, about one standard deviation of a 14-day window among 2025-26 regulars on Dec 15 and Feb 15: a third of them get one; today 69 of 153 rostered players, since two weeks is 3–4 games). `movers()` (Hot & cold) runs on the same gap and cutoff instead of the hype blend (`HYPE` still drives "Now", `their_view` and trade flags). Player tables put an arrow before Value (hover: 14-day value and games), the card adds a last-30-days line (hidden this early: it holds the same games as This season). Real league, no punt: sell high is Paul George (+4.1 over 4 games), buy low starts James Harden, Trey Murphy, Lauri Markkanen, SGA. Checked in Edge at desktop width (not 390px). **Left:** a trend arrow's cutoff doesn't shrink with games in the window (3 games and 7 games get the same 3.0).
  Original plan: One `LeagueGameLog` call for the current season (cached daily) gives last-7/14/30-day averages. Show trend arrows in player tables, and rebuild Hot & cold on real windows instead of the hype blend.
- **Q3. Compare players.** ✅ done Oct 28 (branch q-builds). `League.compare(names, punt)` (`/api/compare`, `python model.py compare --players "A, B"`, AI tool `compare_players`, a line in the gm SKILL.md): 2 to 4 different players (KeyError otherwise, via `find()`), each one's `card()` without news and with every box-score window that holds games (`every=True`, so windows line up across players), plus `slot` in 9-cat: your season win % with him in one of your roster spots. The compared players you own all leave, he comes in, and `_after()` keeps your active count like a trade (injured: a free IL slot; a spot too many drops whoever you'd miss least, which can be him: `cut`; a spot short takes the best free agent outside the comparison). `delta` is against today's `win`. Other teams stay as they are, so a 1-for-1 with another team scores higher in `trade()` (it also weakens them). `best` names who's ahead on each higher-is-better number (value, now, form, plays, build rank, slot, games per week and in total, each category's z); nobody when they're level. **UI:** a **Compare** tab (after Hot & cold): search any rostered player or free agent (`/api/players` with `fa`), or press **Compare** on a player card (it opens the tab once two are in). One table, a column per player: injury, Win % with him (+ who'd be dropped or fill the spot), Value, Now, form, Plays, Market, Your build (with the card's market read), NBA games this week and the next 3, then GP / MIN and every category (z cell + the per-game line picked in a segmented control: rest of season, this season, last 30 / 14 / 7 days). Bold = ahead; punted categories dim their row. The list is kept in localStorage, `#compare?players=A,B` deep-links it, and punt / team / Refresh reload it. Find buyers / Find offers under each column. **Tests:** 1, duplicate or 5 players raise; keep-or-get with two of yours, two free agents (the filler is never a compared player, and the slot win matches the roster maths), an outsider who'd be your worst is cut at today's win %, an IL-parked player goes back to IL with a waiver pickup in his spot, `best` on 4 vs 2 games and nobody on a tie, compare keeps every window. `python test_model.py` passes. **Defaults picked:** the keep-or-get number is season-long win % (not this week's), holds other rosters fixed, and counts a parked player in an IL slot as he is; "best" is bold text, not colour (DESIGN.md's colour is for signs and sizes); market value gets no "best" (higher is pricier, not better). Real-league numbers weren't checked (stats.nba.com / ESPN / NBA schedule blocked in this environment). Not checked in the browser (the view was rendered in Node on test_model.py's synthetic league). **Left:** no this-week version of the slot number (start/sit for this matchup, Q4/Q7 territory); names elsewhere (deal rows, waiver moves) can't be added to Compare without opening a card; the points-league view shows lines only, no slot row.
- **Q4. Schedule planner.** `high` Your roster's games per day for this week and the next 3. Overflow days (more than 10 players with games, once N5 exists) and open slots for streaming are highlighted. Also the NBA teams with the most games in the coming weeks, for buying ahead of a heavy schedule (and fantasy playoff weeks).
- **Q5. Playoff odds.** `xhigh` Actual W-L from the snapshot plus the projected weekly odds for the remaining weeks, simulated to final standings, gives each team's playoff probability. It turns "projected 3rd" into "78% to make the playoffs". The W-L is a category record (P2), so simulate category wins per week. Needs the season's matchup schedule and the playoff format (asked of Instinct, Oct 26).
- **Q6. Global player search** in the top bar (today it's only on Trades). Opens the player card. `medium`
- **Q7. Custom add/drop evaluator** on Waivers: pick any add and any drop and see the win-% change this week and for the season, like the trade builder. `high`
- **Q8. Copy offer.** `medium` One click copies "I give X, Y for Z" plus a short pitch (via the AI GM / humanizer) to paste into Yahoo. Also logs it to the trade log (N6).
- **Q9. Freshness.** `medium` Snapshot part done in N3 (Oct 27): pull time in the sidebar, warning when over a day old or the week rolled over. Left: stats, schedule and injury cache times. Original: show when stats, schedule, injuries and the snapshot were last updated. Warn when the snapshot is older than a day, or the week rolled over since the last import.
- **Q10. Daily brief.** `medium` A scheduled routine (`/schedule`) that writes a morning summary:
  - injuries on your team
  - waiver claims clearing today
  - This week's chase/behind calls
  - the best stream for today
- **Q11. Weekly add limit.** `high` The league allows **4 adds per week** (Martin, Oct 26). Streaming adds count how many you have left this week (from `transactions`, or the per-team count Instinct was asked for) and save the last one for the best day.
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

### 3. Injury notes, ownership %, transactions, real standings (partly done Oct 26) `medium`

Done without Yahoo: injury notes on hover (ESPN), plus "Owned" % and "claim by <date>" in Waivers (from the text snapshot). Recently dropped list done in N3 (Oct 27). Not done: real W-L next to projected. P1 parses both (`transactions`, `records`); the UI part is still to do.

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

### 4. Bring back projections import ✅ done Oct 27 as N4 (Yahoo snapshot + ESPN, no CSV)

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

- [x] **Minutes in the projection** ✅ Oct 27 (Martin: "why drop Achiuwa for Vučević?"). `ros()` gave this season `GP/(GP+20)` weight for everything, minutes included, so Vučević (28.7 min baseline, 8 min in 3 games for ORL) still projected 26 minutes and +1.2% as the top waiver add over Achiuwa (31 min this season, 24.5 projected). Now minutes `GP/(GP+6)` × production per minute weighted by minutes played vs 20 games of baseline minutes. Picked by replaying 2025-26 (each Monday's rest-of-season line vs what players did after, stats scaled equally): error 0.274 → 0.254 in weeks 1–6, 0.260 → 0.236 for players whose minutes moved 6+, 0.265 → 0.258 over the season. Production at 10 games was a bit better again (0.255) but would equal `HYPE` and wipe out Hot & cold, so it stays 20. A faster minutes rule for players on a new team gained nothing. `backtest.py`: category log loss 0.6081 → 0.6062 (average week), 0.5800 → 0.5782 (real schedule); calibration unchanged; real-league standings 39.8–61.8%. On the real league Vučević now projects 21.8 min / 11.4 pts (value −0.02 → −2.45), and the swap is +0.4%, fourth. Achiuwa is still the top drop: he's weak in FT%, 3PM, AST and PTS, Yahoo's own current rank has him 155th, and part of his minutes come from Keegan Murray's ankle. Expert projections (N4) are the next fix for role changes the box scores can't show yet (Yahoo projects Vučević at 7.8 pts). Done in N4: he now projects 16.1 min / 8.4 pts and is out of the top adds.

- [x] **Calibrate the randomness settings** ✅ Oct 26, without Yahoo: `backtest.py` replays 2025-26 box scores in 20 simulated 12-team leagues, predicting each week from what was known that Monday. The old knobs were overconfident (predicted 0–10% won ~15%, 90–100% won ~86%). Fitted: DISPERSION per category, SCHED_CV 0.15, PCT_NOISE 1.75. Category and matchup calibration now sit within ~3 points of the diagonal. Category log loss went from 0.633 to 0.608 for an average week and from 0.614 to 0.580 with the real schedule. Also backtested and adopted a two-season baseline regressed by 25 games (`baseline()`). The availability formula held up against 2025-26 games played (players who missed a season: predicted 40%, actual 27%). Still guesses: GAMES_WEEK, FILL, STATUS_AVAIL, HYPE (perception can't be backtested without trade data).
- [x] **The backtest doesn't know who's injured** ✅ Oct 27. `backtest.py` `build()` now sits regulars who sat their team's last game (`sat()`, shared with `mondays()`) until a noisy guess at their return (`guess()`: days out from Monday × e^N(0, `RETURN_NOISE` 1.0)), the way the app uses ESPN's dates: real schedule = this week's games from then on (This week), average week = the share of his team's remaining games after it (`League.avail`). Before (no sitting): category log loss 0.6132 average week / 0.5868 real schedule, matchups 0.6443 / 0.6083. Sitting with the old knobs: 0.5776 / 0.5158, and timid (real schedule: 80–90% won 89.1%, 90–100% won 97.4%, 0–10% won 2.9%). Refit: `SCHED_CV` 0.17 → 0.12, `PCT_NOISE` 1.75 → 1.5, `DISPERSION` unchanged. Now 0.5760 / 0.5119, matchups 0.5992 / 0.5141, and every category bin within 1.5 points (real schedule: 0–10% won 4.4%, 80–90% 86.2%, 90–100% 95.9%).
  - **Robust to the noise guess:** nobody has measured ESPN's misses, so the fit was run at `RETURN_NOISE` 0, 0.5, 1 and 1.5, and with ESPN 40% early or 65% late on average: every one fits `SCHED_CV` 0.11–0.12 and `PCT_NOISE` 1.375–1.5. With the chosen knobs, real-schedule calibration stays within ~2 points at every noise level (exact returns would make it slightly timid: 80–90% won 87.2%). 0.12 / 1.5 is the cautious end, since ESPN's dates are usually the earliest return ("re-evaluated in two weeks", "questionable for Wednesday").
  - **Why `DISPERSION` stayed:** every team's weekly totals are similar sizes, so per-category `DISPERSION` and `SCHED_CV` trade off and a full refit jumped around (PTS 1.0–6.0 between runs) for 0.0005 of log loss. Sitting players removes minutes-type noise, which grows with the square of the total: that's `SCHED_CV`.
  - **Real league:** standings 37.1–62.6% as categories (was 38.6–61.1), 18.7–79.7% as One Win (was 22.3–76.4). Free moves unchanged (Harris for Achiuwa +1.0 → +1.1).
  - **Bug the sharper odds exposed:** `_contrib()` (drop choice in `trade()` and `search()`) measured a team's own players as a loss without them but incoming ones as a gain with them. Those sit on opposite sides of the odds curve, so a team that's behind kept an incoming scrub over its own strictly better player (`test_model.py` caught it). Every player is now measured as a loss without his line.
  - **Left:** `injuries()` now keeps each day's feed as `data/injuries_<date>.json`. After a few weeks, compare each date ESPN gave with the player's first game back (`gamelogs()`) and set `RETURN_NOISE` from that. The backtest only sits regulars (25+ min) who sat the team's last game; ESPN also lists bench players and players hurt mid-game. N7's lifts aren't in `build()` (neutral in the N7 scratch run). `medium` for the measurement once the archive has a month in it.
- [ ] **Matchup correlation**: categories are treated as independent. After the Oct 27 refit (injured players sitting), matchup odds are within ~3–4 points and bold at the edges in both modes (10–20% won 19.0% average week / 18.7% real schedule, 80–90% won 82.8% / 81.3%). A shared games-played factor would fix both.
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
