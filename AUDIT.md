# Model audit, Nov 9 2026

Martin asked for everything to be gone through, issues found and fixed, backtested, and left alone where it's already good. Claude Code read `model.py`, `backtest.py`, `yahoo.py`, `app.py` and the gm skill end to end, reran every backtest that reads this season's data, and measured each new idea on frozen copies (old and new code side by side, the same caches). `python test_model.py` passes. Details and numbers: each item's ROADMAP section.

**Shipped (all four are This week / data plumbing; no season number moved: standings identical, you 59.0%):**
- **M28, back-to-backs.** A healthy player sits the night after a game twice as often (87.0% played against 93.5%, every season 2022-23 to 2025-26; the least available fifth 75% against 92%). The chain treated every next game alike. Brier 84.26 → 83.60 a thousand games; the weekly matchups a hair better or equal in all four seasons.
- **B15, day-to-day returners.** `RETURN_LATE` was fitted on Out players and used for Day-To-Day ones, who are back sooner (1st game from the date 45% played against 23%). Fitted apart: 0.4 for Day-To-Day (47 players). Also a rest day's return date read as a guess (28%) on the day itself.
- **B14, a lineup that wasn't set.** On the snapshot's day a team with no starter at all banks nothing (John Ball, Nov 6 and 7: 413 and 212 stat-units the fill-in rule gave him). Eight-night replay 1,701 → 1,076.
- **S1, sturdiness.** An empty feed or a short stats table is a failed refresh, not the new copy; every cache and `league.json` is written whole (a copy swapped in).
- **`python backtest.py league`** (new check): the real league's two weeks of snapshots against the model, end to end.

**Real league, Nov 9 (1:23 AM pull), old → new:** you vs Wesburn 89.7 → 87.6% (6.1–2.9 → 6.0–3.0); the plan's adds the same; standings, trades, waivers' season column, playoff and title odds unchanged.

**Measured, nothing to change (don't redo without new data):**
- *The real league end to end:* totals within 2% but FTM +5%, BLK −5%, TO +6%, games +6% (streams); misses 0.8–1.3 of the model's sd; category log loss 0.377 against 0.415 if calibrated. The variance knobs fitted on simulated leagues hold on real managers.
- *Game-day listings:* 215 games, 0.89 / 0.49 / 0.19 against 0.92 / 0.48 / 0.10.
- *Yahoo's live line:* still better than the first-seen one (0.4781 against 0.4839).
- *`RETURN_LATE` for Out players:* refit 0.63 (was 0.62).
- *Nov 4 audit #11 (lifts to 10-minute teammates):* closed. They play more than line + lift (+2.4 min at 10–14), and each lifted minute shows up as 1.07–1.24 real ones in every minutes bucket.
- *ESPN's far dates:* near ones slip a median 4 days; not enough players to move the season numbers. M7 keeps its date.
- *A weaker carry in the play chain* (0.36 for 0.44): Brier 83.56 against 83.60, one more knob, not shipped.
- *This season on the simulated leagues* (`python backtest.py 2026-27`, 3 weeks): categories 0.5217 on the real schedule, in line with 2022-23 to 2025-26 (0.5137–0.5264).

**The open AI GM note (Nov 8, Keegan Murray "questionable" while RotoWire had him starting):** not a bug. ESPN's note said questionable, which plays 49% of the time; he played 23 minutes. The model reads ESPN only. Whether the chat should be able to tell the model "he's playing tonight" is Martin's call (ROADMAP, For Martin).

**Checked and fine while reading:** `cat_probs()` (banked totals add no variance, percentages from summed makes), `season_sim()` (ties, reseeding, byes), `_return()` / `_il_cut()` / `_week_roster()`, `_subs()` (exact by positions), `lineup()`, `match_prob()` with the shared factor, the market and `package()`, `adds()` and `_txday()` across New Year, `switched()`, `parse_text()`'s `from` rule, `live()`'s generation counter.

**Reviewed Nov 9 (a second agent, read-only, on the finished diff):** M28, B15, B14 and `backtest.py league` came back clean (types through `fresh_plays()`, the back-to-back flags per team and week, every `_late()` caller, an empty lineup through `_tipoff` / `_so_far()`; it reran `ahead` and `league` and got the same numbers). Two real problems in S1, both fixed: before opening night an empty stats table (nothing saved yet) raised and the app couldn't load, so the guard now only applies once a copy has rows; and on Windows the swapped-in CSV doubled its line ends, which would have made every later refresh look short. Left: on Windows a swap can fail while another process has the file open (the Mac doesn't care).

**Still guesses (no data to fit them here):** `FILL`, `GAMES_WEEK`, `MARKET`, `PACKAGE`, `CHASE`, `CHIP`, `TIERS`, `accept()` (M6: no offers marked yet).

---

# Projection audit, Nov 6 2026

Martin asked for a deep check of the player projections: how this season's minutes feed them, why players are down or up (mechanical or real), whether they bounce back, against analysts, backtested on 2022-23 to 2025-26. Five agents worked in scratch copies (code and data, player by player, analysts, per-stat rates, minutes); Claude Code reproduced every number below that led to a change, old and new code side by side. `python test_model.py` passes. No `data/notes.md`.

**Shipped:**
- **ROADMAP A5:** each stat regresses at its own pace (FG% / FT% by attempts at 40 / 60 games, steals 60, FGA 5, AST 15) plus a veteran age term. Better in all four seasons on rest-of-season lines and on the weekly matchups (rosters held fixed). The weekly numbers were reproduced exactly.
- **ROADMAP B12:** Tobias Harris (and five others yet to play) sat on last season's team, so Detroit's players lost minutes to a phantom lift; Porziņģis ("stepping away") had ESPN's placeholder Jan 2 date and ranked 4th among free agents.
- **Data:** ESPN's 2022-23 projection file had corrupt FTM (FT% 4.9 points low). Repaired.

**Measured, not shipped (don't redo without new data):**
- *Minutes over-react, but no fix passes.* Early minutes gaps (3+ min off the prior, weeks 1–8) kept 52% rest of season against `ros()`'s 71%. Mechanical causes bounce back more: blowouts 44%, ramp games 47%, injury exits 50%, late starts 47%, none of these 56%; new teams persist (70%); rookies' minutes grow (+2.5 rest of season). Every `MIN_SHRINK` variant (5, 6, 8 for 28+ minute priors, with a minutes-only half-life) wins rest of season in all four seasons but loses the weekly gate in at least one. Down-weighting blowout games is worse in every season (blowout minutes are real information); dividing ramp games by `RAMP` is neutral; a minutes-only half-life or trend term is ±0.1%.
- *Regulars picked on prior minutes, or the higher of prior and line* (Duren, Zubac and Coby White under 25 from their own short games): the extra netting and lift cancel. Reed moves by under 0.1 min; `teammates` 0.3983 = 0.3983.
- *Yahoo's minutes rule in `experts()`* (its line over ESPN's minutes): today you'd be 58.75 → 59.25%, but over this season's 10 snapshots the gain is −0.0015 with an interval of −0.0042 to +0.0011, and it reverses with 4–5 games after. Rerun as snapshots pile up.
- *3PA / 3P% split, long-run shooting prior, rookie growth bump:* each loses the weekly gate in a season (A5).
- *Season lines vs `had()`:* the season line looks better without netting in 3 of 4 seasons. Open: ROADMAP A6.

**Analysts (Nov 3–6: ESPN's 9-cat top 150, Hashtag, NBA Fantasy Edge, Parlae, Fantrax points, Basketball Monster season-to-date; FantasyPros needs JavaScript, several are paywalled):** they lean on this season 16–29% against the model's 51%, and correlate .85 with Yahoo's preseason ranks and .48 with current ones. Of 65 players off by 1.5× in rank, 42 had a steals or shooting swing in the disagreement's direction (fixed by A5). The other big group is injured players valued on ESPN's dates read literally, "at least two weeks" included: Haliburton, Holmgren, Edwards, Kawhi. Season-long `RETURN_LATE` / `RAMP` waits for M7's month of feeds.

**Checked and fine:** the `recent()` fallback (1 of 452 players: Chris / Christopher Cenac, 3 min); name keys (no collisions across 2019-20 to 2026-27 stats, box-score player ids, snapshot, Yahoo, ESPN or injuries); line consistency in `p` / `p_week` (no FGM > FGA, FTM > FTA, FG3M > FGM or negatives; PTS identity within 0.1 / 0.23); `had()` netting traced on MIN / TOR / GSW / OKC (no double counting; preseason absentees net in full and `priced_in()` hands the right share back); `seen` on the flat line (intended: Yahoo shows flat averages); box-score minutes rounded, not truncated; games in progress filtered; fringe pools 24.4–26.1 min.

**Checked Nov 7 (Claude Code, local, Nov 7 snapshot with Nov 6's box scores, old and new code side by side):**
- *Did A5 bump Martin's players?* No. His roster's lean above the market went from −0.15 (mean log₂ of model rank / market rank, 2nd biggest in the league) to −0.06; his season win % fell 58.7 → 58.4%. Washington 129 → 97 is mostly A5 (39.8% FG on 98 shots against 45% the last two seasons); his Nov 6 game (26 pts, 6 threes) moves him 102 → 97 on the new code, 129 → 102 on the old. His line (15.0 pts, 7.4 reb, 33 min) is his last two seasons' (14.2–14.7 pts, 7.0–7.8 reb in 31–32 min); the risk is minutes: all 9 games without Lively (back ~Nov 12, a 23.6-minute line, under `REGULAR`, so no netting), about a minute high by the minutes agent's persistence numbers (ROADMAP A7).
- *Trades vs IL returns:* counted, but the quick cut rule misprices consolidation by 0.1–0.2 while the IL is full (ROADMAP B13).
- *Model vs market on 2022-23 to 2025-26 (proxy market):* the model ranks the rest of the season better in 3 of 4 seasons; when they disagree by 1.4×+ players land 72–90% of the way to the model, at 1.15–1.4× about halfway; the market is worth ~17% in a blend (ROADMAP A8).

**Second check Nov 7, evening (Claude Code, local; B13, A6, A7 and Q22 rerun from scratch on two frozen copies: the write-up's file, the Nov 6 11:28 PM pull copied from that session with its clock, and today's, the 9:36 AM re-pull after the 2:15 PM Refresh; old and new knobs side by side). Details in each ROADMAP item's "Checked Nov 7":**
- *Reproduces:* `test_model.py`; `backtest.py season` (every setting, four seasons), `playoffs` (0.3306, title 0.2750), `teammates 2025-26` (byte-identical to the pre-A6/A7 run) and `2025-26` (A5's levels); A7's real-league numbers digit for digit; This week unchanged (`p_week` gap 0.0, 98.2% both, same plan); B13's numbers to the third decimal, though only on the pre-A6/A7 line, since B13 was measured first.
- *Not right, reported to Martin, fixed the same evening (ROADMAP B13 "Fixed Nov 7"):* (1) Find trades listed deals under a tier the builder doesn't give them: 119 of 482 (default search 5 of 20), because the finder tiered on the quick IL rule and only re-scored the shown net. Now 0 of 432 over 11 searches. (2) B13's "the Kyrie pair comes out equal" holds on the old line only: on the shipped line +1.09 vs +1.05 today, shown as +1.1 vs +1.0; the gap is mostly real, so only the write-up changed. (3) Trades' rival flag and standings stayed on the rule while the headline used the best cut (+1.0 next to "+1.2%"); your standings row takes the cut now. (4) Mathmagicians at 35.8% were under CLAUDE.md's 37–59% standings range, but were 35.3% before A6/A7: CLAUDE.md says about 36–59% now. (5) The browser check first couldn't run (the earlier session's Edge held the Playwright profile); after Martin closed it, the Trades checks all pass (ROADMAP Q22).
- *Is it smart? Our top 40 (per-game Value, rest of season) vs Yahoo, the five biggest gaps:* **Giannis** 34th, market 8th (Yahoo preseason 5th, season to date 30th): FT% (64% on heavy volume, z −4.9) and turnovers (−1.7) cost more in 9-cat than his FG%, points and boards make up. Model right, and Yahoo's own season-to-date rank agrees; he's worth most to a team punting FT%. **Durant** 38th, market 16th (season to date 43rd): age 38, steals −1.2, turnovers −1.1. Model right. **Edwards** 6th per game, 31st after availability, market 42nd (knee scope, ESPN Dec 21): mostly right, a buy-low for a team with an IL slot, though 6th per game leans a little on a 4-game start. **Holmgren** 13th per game, 73rd after availability, market 136th (patellar tendon tear, "at least 8–10 weeks", ESPN Jan 2): direction right, size optimistic. 73rd takes Jan 2 at face value, and "at least" dates slip; season numbers don't use B11's late-return odds. A stash, not a buy at that price. **Kawhi** 10th per game, 26th after availability, market 41st (36, knee swelling, ESPN Nov 23): lean market. The per-game line is his usual, but 60% of the games left is generous for a 36-year-old knee, and the model's own title odds drop when you trade for him. Also Ajay Mitchell 36th vs market 80th: his line carries OKC's long absences (Jalen Williams to March, Holmgren to January), right while they sit.
- *Five of today's default Find trades (you 58.7%):* **#1** Booker + LaMelo + Ingram for Gilgeous-Alexander (Wesburn): +1.5, them +1.7, market even, title 20.3 → 20.5%. Yes: three for a top-3 9-cat player plus a streamed spot, and they gain too. **#4** Kessler + Suggs + Ingram for Ty Jerome + Clingan (Kerala): +1.0, title +1.0. Maybe: it sells two IL players at full price, but leans on Jerome's 8-game run (flagged as hype), and Clingan for Kessler is a step down in blocks and FG%. **#6** the same three for Dončić (Kerala): +1.1, but title 20.3 → 18.0% (Kerala, 5th, gets deeper). No: the same package buys SGA in #1. **#7** LaMelo + Suggs + Ingram for Kawhi + Trey Murphy (langangsters): +0.9, playoffs 95.4 → 95.2%, title → 18.5%. No. **#13** George + LaMelo + Ingram for Curry (Birds): +0.5, they gain +1.1, title → 19.2%. No: barely over Fair's line for a 38-year-old. The finder ranks on season win % only; the title change (fixed seed, so signal, not noise) caught all three bad ones. Showing it in Find trades is an idea, not a bug. Ingram, back Nov 8, is in 15 of the 20 deals; #2 and #3 add Cason Wallace (out) as a throw-in that changes nothing.
- *TO punt:* Value moves the right way for everyone (ROADMAP Q22).
- *Martin, Nov 7 evening: "KAT for Dame, Anthony Black and PJ Washington is 0% for me… I need to get rid of 2 players for Suggs and Ingram to return, would that not be a win?"* The freed spots are counted: after the deal the best cut keeps Ingram and Suggs. It's −0.0 on his FT% punt (+0.3 with none) because on that build the best cut without the deal is Collins, Ingram and Murray (Ingram's value is points and free throws: −1.33 vs Black −1.12, Gafford −0.66), so the spot for Ingram wasn't needed. Forcing Ingram and Suggs to stay costs only 0.07, so the deal is +0.1 even then. Black is cut on Ingram's return either way, so the deal is really Lillard + Washington for KAT + a pickup (Bey): FG% +14, REB +11 (already won 74% of weeks), BLK +4, TO +3 against 3PM −12, STL −7, AST −7, PTS −3 (a third big next to Jokić and Kessler), and KAT's 83% FT counts for nothing on the punt. Not a bug. Better uses of the free Black spot on the FT% punt (Find trades, give Black, consolidate): Black + Booker + Lillard for Giannis (langangsters, +1.5, them +2.6) or for Scottie Barnes + Deni Avdija (Wesburn, +1.5, "Likely yes").

**Small backtest parity notes:** `lifted()` keeps only regulars as sitters, while the app lets every ESPN-listed sitter take no minutes; `mondays()` gives a player yet to play his first team later that season (minor look-ahead).

---

# Audit, Nov 4 2026

**Checked Nov 4 (Claude Code, against the code, data and fresh backtest runs) — read this before acting on a finding:**
- **Done Nov 4:** #15, #23, #8 / #25 (ROADMAP A4: This week and Start / sit follow Healthy only; `live()` drops a rebuild started before a team switch, opponent pick or Refresh; `playoffs()`' docstring).
- **Done Nov 4:** #2 + #3 (ROADMAP A2: rookies blend the expert line at 75% and follow this season at 10 games, measured better in all four seasons; freezing Yahoo at the first snapshot measured worse on this season's snapshots, so the live line stays). #1 (ROADMAP A1: no-ESPN Yahoo rows under half of the baseline's or this season's are dropped; Kobe Sanders 7.0 → 7.9, you 57.9 → 57.8%, This week unchanged).
- **Second check Nov 4, after A1–A4 (Claude Code, local; old and new code side by side in scratch copies):** everything the cloud claimed reproduces. `test_model.py` passes, and undoing each A1/A2 change (5 ways) fails it. Real league on the Nov 3 snapshot: every before / after number in ROADMAP A1 / A2 (title 16.9% vs 16.8%, rounding). Backtests `rookies`, `yahoo`, `recency`, `replay`, `teammates`, `bench`, `playoffs`, the 2025-26 calibration and A3's 3-vs-4 weekly runs (four seasons): same numbers to the fourth decimal. App: Healthy only reaches This week (206 → 195 free agents) and a team switch survives `live()` (the old code reverted it). #2's other fix, a lower expert weight on the live Yahoo line, also measured worse, so #2 is closed both ways. Corrected in ROADMAP: A2's "−0.6" (it's +0.6) and A3's "best in three of four" (best outright in two; best on average and head to head). Details in each A item's "Checked" line. On today's Nov 4 snapshot the new code gives you 57.8 → 58.0% (1st), title 16.1 → 16.3%, This week vs Mumbai 74.5 → 75.2%, the same plan.
- **Valid, to do:** ~~#13~~ **done Nov 4 (ROADMAP B1):** a team switch keeps `from`, the week's dates, Yahoo's games and the totals, and takes the new team's opponent from the schedule; on a copy with `from` two days back Toronto's score so far 159 → 315 PTS (the missing nights back), games left unchanged, switching back restores the file's matchup. #1, #2, #3, #15, #23 and #8 / #25 are done (above).
- **Not valid:** #4: M8's `python backtest.py trend` already measured it (a 14-day form gap carries −0.07 to +0.02 into the next 14 days), so a recent-form line for This week would be worse. #22's "`backtest.py ahead` produced an empty log": it runs (re-run Nov 3, numbers as in ROADMAP M27).
- **Unproven, not on the list:** #11 (a hypothesis, no measurement).
- **Answered:** #8's bracket question: the snapshot says "Reseeding: yes", as the sim does.
- **Found while checking #22:** `python backtest.py recency` after M26 gives weeks 1–6 +1.3 / −0.6 / 0.0 / −0.3% (2022-23 to 2025-26), not −2.0%: `MIN_SHRINK` / `RECENT` need a rerun (ROADMAP "Nov 4 check"). **Done Nov 4 (ROADMAP A3):** rerun after A1/A2, both kept (4 games edged 3 on rest-of-season lines by 0.2% but the weekly matchup odds were worse in three of four seasons); the comments carry the new numbers.

**Bugs found after the audit (ROADMAP B1–B7, Q21), fixed Nov 4 in a cloud session.** Measured on the Nov 4 snapshot (`mchc-fantasy-snapshot-2026-11-04.txt`, the newest in the folder; `data/league.json` was already that import). The cloud session had no Playwright MCP, so the page-only items are built but not checked in a browser.
- **B1 (Nov 4):** see #13 below. A team switch keeps the week; Toronto's score so far on a copy with `from` two days back 159 → 315 PTS, games left unchanged, switching back gives the file's matchup.
- **B2 (Nov 4):** My team's **Punt …** button goes through the chips' path (`setPunt()`): every view on the punt reloads, an open trade and Try a move are scored again. Page only; script parses; browser check pending.
- **B3 (Nov 4):** Evaluate, Find and Try a move drop a reply that isn't for the latest request (a token per request; Clear, a new tier / goal / add, a punt change or a team switch cancel it). Page only; script parses; browser check pending.
- **B4 (Nov 4):** Schedule's week Starts / Sit are the sums of the player rows; Ingram's Sunday has a row. Nov 4 file: 34 = 34 (rows were 33); Denver tipped: 33 = 33 (total was stuck at 34). Standings don't move.
- **B5 (Nov 4):** an injured free agent goes into a free IL slot with no drop in free moves, Waivers, Hot & cold, the moves under trades and Try a move (one rule), and This week plays him from his return. Your waivers and standings unchanged (full IL); Wesburn +1.0 for stashing Mark Williams after Gafford, five other teams +0.3 to +0.9; Dëmin this week +0.0 → +1.8.
- **B6 (Nov 4):** the finder's tier is on win % for every goal (the goal only ranks). Fair TOV + FG%: 17 of 20 deals failed the builder, now 0 of 10; Fair beat Mumbai: 17 of 30, now 0 of 30; win % searches unchanged.
- **B7 (Nov 4):** the 5-minute This week reload keeps Play for and the picked day; "back DATE" uses the local date (a Nov 4 return still shows at 6:30 PM Arizona). Page only; browser check pending.
- **Q21 (Nov 4):** names in finder rows, suggested waiver moves, This week's plan and Strategy steps open the player card. Page only; browser check pending.
- **Third check Nov 4, after B1–B7 and Q21 (Claude Code, local; old and new code side by side on the committed Nov 4 file, clock pinned to 10 AM; the page in Playwright on a copy at port 8766):** every number the cloud claimed reproduces (B1 159 → 315 PTS; B4 34 = 34, Denver tipped 33 = 33; B5's stashes team by team and Dëmin +0.0 → +1.8; B6 17 of 20 → 0 of 10 and 17 of 30 → 0 of 30). B2, B3, B7 and Q21 pass in the browser. Three fixes, each in its ROADMAP section's "Checked Nov 4, local": **B5 broke Strategy** (`_step()` read a stash's `PARK` drop as a player: KeyError on the real league, now skipped, test_model covers it); **Q21 put a list's commas at the start of lines** (the names are inline spans with `role="button"` now); **B3 kept Find / Evaluate busy after a cancel** (`running()`). `test_model.py` passes.

**M9 (Nov 5, cloud):** This week's chance to win counts the categories moving together (see #6): matchup log loss better in all four seasons on the real schedule, no category, standings, trade or waiver number moves; you vs Mumbai 93.4% → 90.1%.
**B8 (Nov 5, cloud):** the live score counted guessed lineups. On the day a snapshot is pulled, its lineup now counts (its starters, a starting slot empty at the pull taken by the best bench player who plays; Yahoo has no auto-subs), live and from box scores alike; a later day keeps the guess. `python backtest.py lineups`, five nights with a pre-tip snapshot (Oct 27–30, Nov 4) against the next one's totals: |error| 1,071 → 576; the Nov 4 night file's own lineups reproduce every team's totals (5 = stat corrections). Your Nov 4: 416 → 410 PTS, 80 → 77 AST (now Yahoo's exactly). Today's file moves nothing. This week's expected starts for games not yet tipped measured worse from the snapshot's lineup, so they keep the per-game order. The cloud session's fetches from nba.com and ESPN were blocked (403): every number is from the committed caches.
**Checked Nov 5 (Claude Code, local; old and new code side by side):** B8 and M9 both reproduce. `test_model.py` passes; `backtest.py lineups` and `backtest.py corr` give the same tables; on the real league only This week's and Try a move's chance to win move (vs Mumbai 93.5% → 90.1%), everything else identical; last night replayed through the app now matches Yahoo for your team exactly (8 of 12 teams, was 4). The new This week footnote renders in the browser. No fixes needed. Details in ROADMAP's B8 and M9 "Checked Nov 5, local".

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
**FIXED Nov 5 (ROADMAP M9)** for This week: one shared factor (counting stats +, TO −, percentages 0) at `WEEK_LOAD` 0.5; real-schedule matchup log loss better in all four seasons (2025-26 0.5066 → 0.5046), 10–20% bins won 15–17% (were 17–19%). Category odds, standings, trades and waivers unchanged; an average week (One Win's season numbers) measured no better, so it stays independent there. **CONFIRMED, already M9.** Schedule-mode matchup bins: predicted 10–20% won 18.1%; 20–30% won 27.4%; 80–90% won 83.6%. Categories are independent in `match_prob()`. In a categories league this only feeds This week's "75% to win" headline, **not** standings, trades, or waivers (`_score` is the mean of category odds). Don't let it change a stream.

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

### 13. BUG — `/api/me` clears `matchup`, so days after the snapshot drop out of This week
**FIXED Nov 4 (ROADMAP B1):** `model.switched()` keeps the week, rebuilds only the opponent.
**CONFIRMED** (the This-week auditor's hung repro; checked in `data/league.json`: `"matchup": null`). `/api/me` does `remember(me=..., matchup=None)`. `League` then rebuilds the pairing from the season schedule **without** `from` or `totals`. `_so_far()` still reads the snapshot's `matchups` totals (Nov 2's 202 pts), but `from` defaults to **today**, so box scores between the snapshot and yesterday are not added. `game_days()` also starts at today, so those days are in neither the score nor the rest of the week.

Fine tonight (Election Day had no games; Nov 4 not in the totals yet). After one more game night without Refresh, This week's score-so-far and remaining games will both skip that night. Re-importing the snapshot, or keeping `from` / `totals` when switching teams, fixes it.

### 13b. Fine — dates, lock, IL-back, adds
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
