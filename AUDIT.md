# Audit, Nov 4 2026

**Checked Nov 4 (Claude Code, against the code, data and fresh backtest runs) — read this before acting on a finding:**
- **Done Nov 4:** #15, #23, #8 / #25 (ROADMAP A4: This week and Start / sit follow Healthy only; `live()` drops a rebuild started before a team switch, opponent pick or Refresh; `playoffs()`' docstring).
- **Done Nov 4:** #2 + #3 (ROADMAP A2: rookies blend the expert line at 75% and follow this season at 10 games, measured better in all four seasons; freezing Yahoo at the first snapshot measured worse on this season's snapshots, so the live line stays). #1 (ROADMAP A1: no-ESPN Yahoo rows under half of the baseline's or this season's are dropped; Kobe Sanders 7.0 → 7.9, you 57.9 → 57.8%, This week unchanged).
- **Valid, to do:** #1 (11 players with 3+ games on a junk Yahoo line, ESPN has none: Kobe Sanders 1.9 vs 11.0), #2 (snapshot Yahoo lines move in-season: Acuff 17.3 → 23.3, Wilson 16.1 → 18.8, Oct 27 → Nov 3), #3 (rookie prior 40% fringe: Boozer 15.8 on Yahoo 18.4 / 17.4 this season), #15 (`/api/week`, `/api/lineup` use `lg`, not `fl`), #23 (`live()` assigns `lg = new` whatever `/api/me` did meanwhile), #8 / #25 (`playoffs()` docstring predates M2).
- **Not valid:** #4: M8's `python backtest.py trend` already measured it (a 14-day form gap carries −0.07 to +0.02 into the next 14 days), so a recent-form line for This week would be worse. #22's "`backtest.py ahead` produced an empty log": it runs (re-run Nov 3, numbers as in ROADMAP M27).
- **Unproven, not on the list:** #11 (a hypothesis, no measurement).
- **Answered:** #8's bracket question: the snapshot says "Reseeding: yes", as the sim does.
- **Found while checking #22:** `python backtest.py recency` after M26 gives weeks 1–6 +1.3 / −0.6 / 0.0 / −0.3% (2022-23 to 2025-26), not −2.0%: `MIN_SHRINK` / `RECENT` need a rerun (ROADMAP "Nov 4 check"). **Done Nov 4 (ROADMAP A3):** rerun after A1/A2, both kept (4 games edged 3 on rest-of-season lines by 0.2% but the weekly matchup odds were worse in three of four seasons); the comments carry the new numbers.

Deep check of player values, team strength, this week, trades, data, backtests, and the app. No code was changed. `python test_model.py` passes. There is no `data/notes.md` (no open AI GM notes).

**Standings at audit:** fatty zhou 57.9% (1st), Mumbai 57.4%, Shanghai 55.0%, … Mathmagicians 35.1%. Playoff odds sum to 8.00, title odds to 1.00. You 93.4% to make it, 16.2% to win it. This week vs Mumbai ~75% (categories 5.4–3.6).

How to read this: **BUG** means a number the app shows is wrong. **GAP** means the maths is doing what it was written to do, but it will cost wins. **SMALL** is real and not worth a session on its own.

---

## Do these first (they move player values)

### 1. BUG — Yahoo's near-zero projection rows still used when ESPN has no line
**FIXED Nov 4 (ROADMAP A1).** Thybulle isn't one of them: ESPN has a row and Yahoo is 58% of it, so he's unchanged. **CONFIRMED.** `experts()` (`model.py` ~597–615) drops a Yahoo row only when its PTS+REB+AST is under half of *ESPN's*. If ESPN has no row, the comparison is NaN, the Yahoo row is kept, and it is 60% of `prior()`.

On today's league, 11 players with 3+ games this season are still on a Yahoo line under half of what they are actually doing. Most are free agents / streamers:

| player | Yahoo PTS | this season | model ROS | owner |
|---|---:|---:|---:|---|
| Kobe Sanders | 1.9 | 11.0 | 7.0 | FA |
| Brandon Williams | 1.2 | 11.6 | 7.5 | FA |
| Quenton Jackson | 1.8 | 10.1 | 7.6 | FA |
| Mo Bamba | 1.1 | 9.0 | 5.7 | FA |
| Matisse Thybulle | 3.5 | 8.6 | 7.7 | FA |
| Jay Huff | 3.6 | 8.3 | 6.6 | Toronto |
| Pat Spencer | 0.4 | 3.0 | 3.1 | Mathmagicians |

Capela 0.4 *is* caught (ESPN has him at 6.6, so ESPN wins). The ROADMAP note about Capela is the wrong example; the leftover is everyone ESPN doesn't project.

**Impact:** waiver ranking and replacement level. Thybulle is 4th among FAs by per-game value (−1.36) but 4th by *effective* too (−3.24) because 66% avail; the junk line still holds his ROS down. Streamers you might actually add (Sanders, Brandon Williams, Jackson) are several points of production too low, so they lose to Gafford / Risacher / Portis.

**Fix:** drop a Yahoo row that is under half of ESPN *or* of `baseline()` / this season's volume, same as the Hayes/Capela rule. Then `prior()` for those players is ESPN or the M26 bench line.

### 2. GAP — in-season Yahoo updates double-count this season
**MEASURED Nov 4, not shipped (ROADMAP A2):** freezing each player's Yahoo line at the first snapshot made this season's lines worse (`python backtest.py yahoo`: 0.551 vs 0.530 for players whose line moved). **CONFIRMED.** `PROJ_W` 0.6 was fitted by `backtest.py replay` on ESPN's *preseason* projections, which barely move (6 of 348 players changed PTS > 0.05 between Oct 27 and Nov 2). Live Yahoo rows in the snapshot *do* chase the start: Acuff 17.3 → 23.3 PTS in a week. `ros()` then leans on this season's games *again* (`MIN_SHRINK` 3, `SHRINK` 20).

So a breakout is in the expert line *and* in `recent()`. A bust is too.

**Impact, today:** concentrated in rookies and role-changers. Stars barely moved (Jokić 27.1 → 26.9). Acuff: Yahoo 23.3, this season 26.1, model 21.8, rank 172. Caleb Wilson: 18.8 / 21.6 / 18.6, rank 75. Boozer: 18.4 / 17.4 / 15.8 (the 40% fringe prior plus slow per-minute shrink; see 3).

**Fix:** for live leagues, either (a) freeze Yahoo at the first snapshot / use ESPN's preseason as the expert, or (b) cut `PROJ_W` as GP grows (`PROJ_W * MIN_SHRINK / (GP + MIN_SHRINK)`), since this season is already in `ros()`. Re-run `python backtest.py replay` after, because the backtest still uses frozen ESPN.

### 3. GAP — rookies with an expert line are still 40% a bench player
**FIXED Nov 4 (ROADMAP A2):** `ROOKIE_W` 0.75, `ROOKIE_K` 10 (`python backtest.py rookies`). **CONFIRMED.** A rookie has no `baseline()`, so `prior()` is 40% the fringe-rotation line (26 min of replacement rates) + 60% expert. Then `ros()` only lets this season's *rates* in at `GP * MIN / (GP * MIN + SHRINK * prior_MIN)` ≈ 25% after 7 games of 32 minutes (`SHRINK` 20). Minutes move fast (`MIN_SHRINK` 3); rates do not.

Boozer, on your roster: prior 15.7 PTS, this season 17.4 in 32 min, ROS **15.8**. Wilson and Lendeborg are the same shape. This is the knob the rest-of-season backtest liked *on average* (hot starts fade). Two weeks in, the rookies who are clearly starters are low.

**Impact:** Boozer #72 by model value, Yahoo Cur #30. You are not starting him enough in your own head if you trust the table; This week also uses this line (see 4). Combined with (2), the two gaps partly cancel for Acuff (Yahoo chased him up, fringe pulled him down) and stack for Boozer (Yahoo didn't chase, fringe + slow rates both pull down).

**Fix with (2), not alone.** A rookie-only faster `SHRINK` is measurable (`python backtest.py replay` split by `base` missing). Don't ship it from one week of 2026-27.

### 4. GAP — This week uses rest-of-season lines, not the last week
**CONFIRMED.** `week_vec()` / `_pw` read `p_week`, which is `ros()` plus this week's injury lifts. `zform` (last 14 days) is only arrows and Hot & cold. A player on a heater is under-projected for *this matchup*; a cold star is over-projected.

**Impact:** the weekly lever is the one that wins you the season (ROADMAP: this week's 3 adds +4.1 win % vs +0.4–1.2 for a good trade). Using a 20-game-shrink ROS line for 2 remaining games is the wrong horizon. Boozer 17.4 vs 15.8 is ~10% on his counting stats; Acuff 26 vs 22 is similar for Umbrella.

**Fix:** `p_week` from `recent(logs, today, hl=something short)` blended with `ros()` at a this-week weight, or just `recent()` with `FORM_DAYS`. Fit on `backtest.py ahead` / a new "this week's stats vs last week's line" split. Don't use raw last-3-games (that's what `RECENT` 45 beat).

---

## Team strength and odds

### 5. Fine — category odds are still calibrated
**CONFIRMED fine.** `python backtest.py` on 2025-26, 33k matchups:

- Categories with the real schedule: log loss 0.5046. Bins sit on the diagonal (30–40% predicted won 34.8%; 80–90% won 85.9%).
- Documented 0.5601 / 0.5062 is stale by ~0.001 after M26/M27. Ignore it.
- FG% and FT% are the noisiest (log loss ~0.62); counting stats are ~0.42–0.52. That's shooting variance, not a bug.

Standings 35–58% on the real league, in the documented 37–59% band. Mathmagicians at 35.1% is a real roster: empty IL slots, Jalen Williams (out until March) and Porziņģis on the bench, Pat Spencer in Util, Caruso starting while Out. The snapshot parser kept them correctly (`IL Empty slot` × 3).

### 6. GAP — P(win the matchup) is overconfident
**CONFIRMED, already M9.** Schedule-mode matchup bins: predicted 10–20% won 18.1%; 20–30% won 27.4%; 80–90% won 83.6%. Categories are independent in `match_prob()`. In a categories league this only feeds This week's "75% to win" headline, **not** standings, trades, or waivers (`_score` is the mean of category odds). Don't let it change a stream.

### 7. GAP — everyone is shrunk toward 70% availability, including your healthy stars
**CONFIRMED, by design, costly early.** `availability()` pulls toward `AVAIL_T` 0.7 with `AVAIL_K` 50 games. After 7 of 7 games a 70-GP veteran is ~76% ROS. Rostered players have played 89% of their teams' games so far; the model has them at 74%.

The backtest on 10,631 player-Mondays says the *rest of season* really is ~78.5% (chosen bias −0.7 points). So the 15-point gap vs "so far" is mostly "it's week 2" plus "stars get hurt later". Jokić at 76% avail with 6 GP is the model saying he'll miss ~1 in 4 from here, which is the league average, not Jokić.

**Impact:** season-long win % slightly pessimistic for healthy stars vs replacement (both get ~75%). Effective-value ranks barely move because the shrink is league-wide. This week already uses `fresh_plays()` (~92% for the next game) so streams are mostly fine. ROADMAP already notes weeks 1–2 run healthier.

**Don't retune `AVAIL_*` from two weeks of 2026-27.** The four-season backtest is the evidence.

### 8. Fine — playoff / title sim
Odds sum, 8 seats, 18 regular-season weeks left (this one included), playoff weeks from the snapshot's dateless rounds. Higher seed wins a tied playoff week. `_sched_week()` is what M2 shipped; `playoffs()`'s docstring still says "later weeks are average weeks" (stale, `model.py` ~1817–1821).

**Check with Martin:** Yahoo's playoff bracket. The sim *reseeds* each round. If mchc uses a fixed bracket, title odds move a little (your 16% vs Mumbai/Shanghai), not playoff-make odds.

### 9. GAP — season-long week still ignores the 10-slot cap and uses FILL 0.5
Documented. Average week = `GAMES_WEEK` 3.4 × (avail × line + (1 − avail) × 0.5 × waiver line). Daily cap costs 0.1–0.9% of games season-long. FILL is a guess (not in the backtest; those rosters never stream). A team that actually streams its open slot / empty IL (Mathmagicians does not) is stronger than the model vs one that doesn't. Small on *your* roster (full, 3 IL used).

---

## Injuries and teammates

### 10. Fine — current lifts are on the right NBA teams
0 cross-team lift rows of 390. 0 Yahoo-vs-stats team mismatches for rostered/FA players. Unmatched ESPN names are two-way/G-League (Veesaar, Bilodeau, …), none rostered.

### 11. GAP — ROOM sends star-sitter minutes to 10-minute guys
**CONFIRMED, by design.** Biggest lifts right now: Kobe Brown and Braden Smith +5.3–5.6 min from Haliburton; Jaylen Clark / Bones Hyland +5 min from Edwards (out until Dec 21); Bennett Stirtz from Jalen Williams (out until March). A 10-minute player has 26 minutes of room under `ROOM` 36; a 32-minute starter has 4. M25 measured letting 15–25 min sitters *hand out* minutes as worse, and left it off. The leftover is the *receivers*.

**Impact:** deep-bench FAs and end-of-rotation guys on injured teams look like streamers. If they don't actually play those minutes, This week and Waivers overstate them. `onoff()` (M13) only moves minutes for players with games this season on that team, and ONOFF_K 4 is a strong ridge after 2 weeks, so it does not fully pull this back.

### 12. ESPN return dates — not yet a bug, still M7
Of unique Out listings that have already returned: 26/35 on or before the listed date, 9 after, median 0 days late. Most Out rows are still out (far dates: Simmons/Hukporti Achilles 2027-10-01, Jalen Williams / Sharpe / Wallace March). An earlier 74% "missed" figure was counting daily archive copies and still-out players as misses. Keep M7 for ~Nov 27.

Game-day listings (`GAMEDAY` 0.92 / 0.48 / 0.10) still rest on 104 player-games (5 probable, 29 Q, 4 doubtful). M16's December refit stands.

---

## This week and streams

### 13. Fine — dates, lock, IL-back, adds
Mac is MST (UTC−7). `date.today()` is the local NBA date, not UTC. Week Nov 2–8, not rolled. No games under way at audit (live.json stale is fine overnight). Name matching: **0 unmatched rostered players, 0 unmatched listed FAs.**

Start/sit, `_clears()` (rolling-waiver claim day), `_handoff()` (IL return cuts the stream), and `_reserve()` (last add for late news) match the docs. `python backtest.py news` still gives HAZARD 0.33, ONE_GAME 0.56. `gameday` still matches the shipped table.

### 14. GAP — streams assume a free agent is yours tomorrow
A player on waivers only starts from `_clears()`. A player *not* on waivers is treated as a free add tonight/tomorrow. Rolling waivers (2 days) mean a *drop you just made* isn't addable; the plan doesn't simulate the rest of the league's claims. Fine for "best stream if you get him"; it is not a FAAB/waiver-priority model.

### 15. SMALL — `/api/week` ignores Healthy only
**FIXED Nov 4 (ROADMAP A4)** for `/api/week` and `/api/lineup` (`/api/player` left as is: a card shows one player, not a pick).
`app.py` `/api/state` / trades use `fl = lg.healthy_fa()` when the toggle is on; `/api/week` and `/api/lineup` always use `lg`. Injured FAs can still show as streams. Same for `/api/player`.

---

## Trades, waivers, market

### 16. Fine — finder and builder still agree (tests + a real deal)
`test_model.py` checks search = trade on uneven, IL, open spots, chips. A real 3-for-3 (Booker + Suggs + Kessler → SGA + Bane + Wallace) JSON-serializes, parks Wallace (out until 2027-03-02, 21% avail, 29% of games left) as a low-value piece, and does not need an IL slot on Wesburn (3 empty) to "work". Net +1.3 you / +1.7 them, market they overpay 9%, Maybe. Wallace is a throw-in who sits; the deal is SGA + Bane for Booker + Kessler + a returning Suggs. Don't send it just because Strategy listed it under a FG% punt.

### 17. GAP — accept() / MARKET / PACKAGE are still guesses
Already M6. Almost every finder deal is Maybe. One real trade in the log (Tatum for Chet + Reed) went through as a Long shot. Until ~20 marked offers exist, "will they say yes" is not evidence.

### 18. SMALL — Strategy's best build is a FG% punt you should not commit to
Balanced 57.9%. FG% punt 62.0% after three trades. Pick empty, edge 0.2: the planner itself says don't. The first step (Risacher for Black +0.5) is a waiver move, not a punt.

### 19. Waivers, today
Best add: Gafford for Collins +0.6 season win %. Then a pile of +0.2 dropping Black. Black is REST (one game), form +1.7 over 4 GP, market #116 — a sharp manager keeps him. The model is ranking him as a drop because ROS value −1.80 and REST doesn't cut availability the way Out does (by design: rest is one game). Don't auto-drop him.

---

## Data and backtests

### 20. Fine — snapshots, names, ESPN stat ids
`parse_text` roster sizes, records, IL empty slots, Pre/Cur ranks, adds, schedule uniqueness (`schedule_ok`). ESPN_STAT ids still produce FGM < FGA, sensible REB/BLK for bigs. `keyed()` one row per name. No FGM > FGA, no MIN > 42, no NaN z-scores, avail in (0, 1].

### 21. SMALL — stats CSVs are per-game rounded to 1 decimal
`fetch()` writes NBA.com PerGame already rounded. Baseline FG% / low-volume STL/BLK for last season is slightly noisy. Not worth a Totals fetch unless you are refitting `baseline()`.

### 22. Backtest leakage / misfit
- **Same maths, mostly.** `projections()` / `replay()` / `mondays()` use `recent()` like the app. `lifted()` matches `had()` + `lifts()` + `priced_in()`. Playoff sim is shared. **Live diverges** by using in-season Yahoo (finding 2) and ESPN injury dates (backtest uses exact next-game returns + `RETURN_NOISE`).
- **Leakage:** `mondays()` "out" = sat last game until actual next game (perfect hindsight of the return date, then noise). Live uses ESPN's date. That's M7, not silent leakage in the variance knobs.
- **Simulated leagues** are snake-drafted, no IL, no streaming. Variance knobs transfer anyway (real standings spread matches). `SEASON_SD` / `WEEK_SD` may be a hair low because real rosters change; documented.
- **Recency docs:** shipped `MIN_SHRINK` 3 + `RECENT` 45 is *not* −2.0% in weeks 1–6 on a re-run (2022-23 +1.3%, 2025-26 −0.3% vs k_min 6 every-game-alike). Season totals −0.4 to −1.3%. Knob still reasonable; the −2.0% line in the comment is stale.
- `backtest.py ahead` was launched and produced an empty log in this audit; `news` / `gameday` / `availability` reproduced.

Trust of fitted constants: DISPERSION / SCHED_CV / PCT_NOISE **high**; PROJ_W **high on ESPN preseason, mis-applied to live Yahoo**; AVAIL_* **high for ROS, pessimistic week 1–3**; SHARE 0.5 **medium** (minutes still under when two sit); GAMEDAY **low-n**; MARKET / PACKAGE / CHASE **unfitted**.

---

## App / UI

### 23. SMALL — `live()` can overwrite a team switch
**FIXED Nov 4 (ROADMAP A4):** a generation counter.
`/api/me` writes `me` and `lg = load()`. `live()` may have started `load()` on the old file and assigns `lg = new` when it finishes. One user, rare. Fix: compare `loaded` / mtime, or a generation counter.

### 24. Fine — XSS, punt dimming, JSON
`esc()` on names, news, teams. Main stat tables use `.punted-col`. `json.dumps(state/trade/week)` succeeds (numpy scalars in `market.sum` are still JSON-safe here). `/` only serves `index.html`; API is a fixed set; bound to 127.0.0.1.

### 25. DOC — `playoffs()` docstring and a few backtest numbers are behind M2/M26/M27
**FIXED Nov 4 (ROADMAP A4).**
Not user-facing.

---

## Checked and fine (so you don't re-audit them)

- `cat_probs` FG%/FT% as ratios of summed makes/attempts; TOV inverted; banked totals add no variance.
- `lineup()` augmenting-path slot fill (matroid / greedy-by-value).
- `effective()` = floor + avail × (value − floor), not value × avail.
- `season_sim` logit shifts centered, ties half a category win.
- IL `_return()` / `_week_roster()` / open-spot streaming (M20/M21).
- Chip-last forced drops (M24); `healthy_fa()` shallow copy keeps cached team totals.
- 0 unmatched roster names; Jr/III/accents through `norm()` + `match()`.
- REST regex vs Out; every ESPN row has a return date today.
- `python test_model.py` ok.

---

## What to do next (for winning, not for a clean repo)

1. **Finding 1** — junk Yahoo rows. Small code change, moves waiver order this week. Effort `high`.
2. **Finding 4** — This week's line from a shorter window. Biggest weekly lever. Effort `xhigh`, needs a backtest split.
3. **Finding 2+3** together — freeze or decay the expert once this season is in `ros()`, and only then consider a rookie `SHRINK`. Effort `xhigh`.
4. Leave M6 / M7 / M16 / M9 as they are (waiting on data, or not a categories-league number).

Don't retune `AVAIL_*`, `SHARE`, or `GAMEDAY` from this week of 2026-27.
