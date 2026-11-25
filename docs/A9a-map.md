# A9a. Where each load check reads

Map for the checks in ROADMAP A9 part 1. No code. Line numbers are `model.py`, `yahoo.py`, `app.py`, `web/index.html`, and `test_model.py` as of Nov 25, 2026.

Open AI GM notes: none.

`app.py` builds every app league with `load()` (`app.py:26`), which passes `whatif()` and `moves()`. `load()` (`model.py:5981`) does, in order: `positions(snap)` (`model.py:5967`, positions only), `claimed(snap)` (`model.py:1442`), `moved(snap, made)` (`model.py:1388`), then `League(snap, ...)` (`model.py:6003`). The file `data/league.json` is not rewritten. `replay()` (`model.py:6101`) and `test_model.league()` (`test_model.py:21`) call `League` on the snapshot as stored. They do not run `claimed()` or `moved()`.

`claimed()` and `moved()` have already changed `snap["teams"]` and `snap["free_agents"]` by the time `League.__init__` copies them into `self.rosters` and `self.fa` (`model.py:1519-1521`). A check on `self.rosters` after that line sees the claim and the plan pick. A check on the raw JSON, or inside `yahoo.build()` (`yahoo.py:716`, which constructs a `League` before `load()`), does not.

`roster_check()` does not look at these seven things. It is the place a failure can be shown. See the last section.

The synthetic league is `test_model.league()` (`test_model.py:21-32`): two teams, `"roster": 13`, no `il`, no `slots`, no `slot` on any player, no `matchup`, no `records`, no `adds`, no `max_adds`, no `projections`, no `claims`, no `schedule`. Me is players 150, 154, … 198 (13). Them is 151, 155, … 199 (13). Free agents are players 100–139. `League(snap, base, cur)` gets no schedule, no ESPN proj, no live scores.

Cheap place for all seven: a method on `League`, called at the end of `load()` and from `test_model`. The fields it needs exist once `__init__` has passed `self.il` (`model.py:1696`). `this_week()` (`model.py:4431`) also has the games and the totals, and it runs the stream search. `starts()` / `_so_far()` / `adds()` do not.

## 1. Actives ≤ roster spots, IL ≤ IL slots

**(a)** After `load()`, each team’s players are `self.rosters[team]`, stats keys from `snap["teams"]` via `_keys()` (`model.py:1519-1520`, `1730-1737`).

Active count: `len(self._playing(self.rosters[team]))` (`model.py:3971-3973`). `_playing` drops anyone in `self.il`. `self.il` (`model.py:1696`) is the keys whose `self.meta[k]["slot"]` is `IL`, `IL+`, or `NA`. `meta["slot"]` is the snapshot’s `slot`, copied in `_keys` (`model.py:1735`).

IL count: `len(self.il[team])`.

Roster spots: `self.roster_n` = `settings.roster`, default 13 (`model.py:1481`). Yahoo writes it as starters + BN, not IL (`yahoo.py:162`). A text snapshot writes starters + BN from `Roster slots: … (N starters) + M BN + K IL` (`yahoo.py:674-681`).

IL slots: `self.il_n` = `settings.il`, or, when that key is missing, the max number of players already parked (`model.py:1697`). Yahoo’s `il` is the sum of IL and IL+ roster positions only (`yahoo.py:163`). The text regex captures a single `+ N IL` (`yahoo.py:674`). It does not mention IL+.

`roster_check()` already counts your team the same way: `active` is `len(_playing(rosters[me]))`, `il` is `len(self.il[me])`, `spots` is `roster_n`, `il_slots` is `il_n` (`model.py:5759-5760`, `5819`). Its `drop` / `open` kinds use those counts plus `_stuck()` and `_parkable()`, which is a different question (who must be moved). It does not flag another team, and it does not flag IL count > IL slots.

**(b)** Yes. `claimed()` appends the add to your team before `League` (`model.py:1464-1465`). An Out add with no drop and a free IL slot gets `slot: "IL"`. Otherwise `slot: "BN"`. That BN case is the Nov 25 Morant bug (14th active). `moved()` does the same for a plan pick: add → BN, park → IL (`model.py:1428-1437`).

**(c)** After `model.py:1696`. No schedule and no `this_week()`. The synthetic league has 13 actives and 0 parked on each team, `roster_n` 13, and no `settings.il`, so `il_n` is 0 (`model.py:1697`). Both inequalities hold. It has no claim and no plan pick.

**(d)** 
- No `settings.il` (synthetic league, demo `yahoo.py:799`, a text file whose roster line has no `+ N IL`): `il_n` becomes the number parked, and IL ≤ slots holds by construction.
- A player in an `NA` slot counts in `self.il` (`model.py:1696`). `settings.il` does not include NA slots (`yahoo.py:163`). IL count can exceed `il_n` in a league that uses NA. mchc’s settings line is 3 IL, no IL+ and no NA (`docs/MODEL.md` §5). Not sure the text regex stores an IL+ count; the pattern is `IL` only (`yahoo.py:674`).
- A snapshot row with no `slot` (hand-typed list, `yahoo.py:670-672`) is active, including someone who is hurt. A full roster of unslotted players plus a real IL count is a false “too many actives” if the slots were simply not parsed.
- `_keys` keeps one stats key per team (`model.py:1734`). Two snapshot rows that `find()` maps to the same key count as one active. Counting `self.rosters` misses that. Counting `snap["teams"]` rows does not.
- `roster_check`’s `drop` kind subtracts `_parkable()` before comparing to `roster_n` (`model.py:5771`). An Out player in an active spot with a free IL slot is not “over the roster” there. Check 1, counted straight off slots, still sees him as active. That is a real over-cap until he is parked, and it will fire on a legal Yahoo roster that has not moved him yet.

## 2. Nobody on two rosters, or on a roster and the free agents

**(a)** Rosters: `self.rosters`, `{team: [stats key, …]}` (`model.py:1518-1520`). Free agents: `self.fa` (`model.py:1521`), keys from `snap["free_agents"]` through the same `_keys`. Owner lookup already exists: `owner()` (`model.py:2022-2023`).

The snapshot names are still on `self.snap["teams"]` and `self.snap["free_agents"]`. `build()` leaves a name `find()` cannot match on the team list and only prints it (`yahoo.py:717-718`). `_keys` then drops that name (`model.py:1733-1736`), so `self.rosters` can be shorter than `snap["teams"]`.

**(b)** Yes. `claimed()` `fa.remove(a)` before `mine.append` (`model.py:1460-1465`). A drop on the claim goes the other way (`model.py:1466-1468`). A claim that cannot be applied (add already gone, drop gone) is left out of `snap["claims"]` and stays where the pull put him (`model.py:1457-1459`). `moved()` moves the player from one list to the other (`model.py:1418-1429`). After either function the player is on one side.

**(c)** Same place as check 1, on `self.rosters` and `self.fa`. The synthetic lists are disjoint: Me 150–198 step 4, Them 151–199 step 4, free agents 100–139.

**(d)**
- A pending claim is on the roster and off `self.fa` once `claimed()` has run. Checking the raw `league.json` before `claimed()` sees him only as a free agent.
- A player on waivers with a `waiver` date, not a claim, stays in `free_agents` (`yahoo.py:234-237`, text parse `yahoo.py:666-668`). He is not on a roster. That is not a double listing.
- Two names on one team that `find()` resolves to one key are one roster entry (`model.py:1734`). The duplicate row is invisible on `self.rosters`.
- `self.meta[k]` is whichever team’s row `_keys` wrote last (`model.py:1735`). The key can still sit in two teams’ lists. The check has to walk `self.rosters`, not `meta`.

## 3. Every rostered player is found, and has a projection source

**(a)** `find()` (`model.py:2017-2020`) matches `self.p.index`. `self.p` is the rest-of-season frame (`model.py:1517`, replaced at `1661`). A name that misses is `None` when `strict=False`, which is what `_keys` uses.

Projection source: `self.proj` from `experts()` (`model.py:1506`, function at `842`). The column `src` is `"ESPN"` or `"Yahoo"` (`model.py:873-874`). ESPN is `proj` passed into `League` (`load()` passes `espn()`, `model.py:6004`). Yahoo is `snap["projections"]`. A player in neither, or rejected by the half-of-baseline cut (`model.py:869-870`), is absent from `self.proj.index`. He still has a row in `self.p` from `baseline()` / the fringe line (`prior()` `model.py:881-891`, `ros()` `model.py:918-920`).

`player()` reports the source as `proj` when the key is in `self.proj.index` (`model.py:2169`).

**(b)** The name is searched on the post-claim, post-pick snapshot. A claim’s add is rostered only if `claimed()` found him in `free_agents` by `norm(name)` (`model.py:1455-1457`). That match does not use `find()` or the stats index. He can be on `snap["teams"]` and then dropped by `_keys` when `find()` fails. A plan pick’s add is the same (`moved()` `model.py:1407`, `1428`).

**(c)** Walk `snap["teams"]` names through `find(name, strict=False)`, then check `self.proj`. Walking only `self.rosters` hides the names `_keys` already dropped. `experts()` has already run at `model.py:1506`.

The synthetic league passes no `proj` and no `snap["projections"]`. `experts({}, {}, base, …)` returns no rows. All 26 rostered players are in `self.p` (they are in `base` and `cur`) and none are in `self.proj`. A check that requires `src` fails on every one of them. `test_model.py:74-90` is the test that `experts()` itself has a `src`; `league()` does not give those rows to `League`.

**(d)**
- Requiring `src` on the synthetic league, the demo (`yahoo.py:796`, no projections key), and any `League` built in `replay()` with an empty ESPN file fails for players who still have a baseline line.
- A rookie or a free-agent call-up with no ESPN row and no Yahoo row is the M26 case: no expert source, fringe line on purpose (`docs/MODEL.md` §2). Flagging him is the check the brief asked for. He is common on a real roster early in the season.
- `build()` does not remove the “Left out” names (`yahoo.py:717-718`). They are the find() misses. They are already gone from `self.rosters`.
- Yahoo projections on an API league are the newest `*snapshot*.txt`, copied in `yahoo._pull` (`yahoo.py:338`, `_last_snapshot` `yahoo.py:431`). `src: "Yahoo"` means that file had a line, not that Yahoo’s API sent one.

## 4. Adds used ≤ max

**(a)** `adds()` (`model.py:3981-4000`). `used` is `max(counted, listed or 0) + claims`. `counted` is this week’s add/claim rows in `snap["transactions"]`. `listed` is `snap["adds"][team]` when `pulled` falls inside the matchup week (`model.py:3994-3995`). `claims` is your pending claims whose `clears` date is inside the week (`model.py:3997`), from `_claims()` (`model.py:5151-5158`). `max` is `settings.max_adds`. No key: `adds()` returns `None` (`model.py:3987-3989`).

Yahoo writes `adds` from `roster_adds/value` (`yahoo.py:217`) and `max_adds` from `max_weekly_adds` (`yahoo.py:166`). A text snapshot writes both from the adds section and `Max adds/week` (`yahoo.py:638-640`, `675`, `684`).

`state()` already returns `adds()` (`model.py:5511`).

**(b)** The claim count is read off `snap["claims"]` after `claimed()` has kept only the claims it applied (`model.py:1469-1470`). `moved()` also appends an `"add"` transaction and increments `snap["adds"][me]` when the pick’s day is inside the week (`model.py:1430-1432`). Both are inside `used`. `adds()` adds the claim on top of `listed`. Yahoo’s `roster_adds` does not include a claim that has not cleared (`docs/MODEL.md` §5, Q35). The `+ claims` term is what makes a pending claim count.

**(c)** `adds(team)` for each team. No `this_week()`. The synthetic league has no `max_adds`, so `adds()` is `None`. There is no cap to exceed.

**(d)**
- `used` can be above `listed` because of `+ claims`, and above `counted` because `listed` wins the `max`. A legal week with a pending claim can show `used == max` (the Morant case: the claim is the 4th add). `used > max` is the failure. `used == max` is allowed.
- No `max_adds` (demo, `yahoo.py:799`; a text file with no `Max adds/week`): `adds()` is `None`. Treating `None` as 0 flags every league that has no cap.
- `listed` is omitted when `pulled` is outside the matchup week (`model.py:3995`). A rolled week (`model.py:1600`) then counts only the log. The log is the last 50 successful transactions (`yahoo.py:178`, `284`). `adds()`’s docstring still says 30 (`model.py:3986`). A short log plus a pulled-date outside the week undercounts. It does not false-alarm `used > max`.
- Only your claims are in the pull (`yahoo.py:320-326`). Another team’s pending claim is not in `used`.
- A plan pick increments `snap["adds"]` (`model.py:1431-1432`) and also adds a transaction `adds()` counts. `used` takes the max of those two, then adds claims. The pick is not added twice. Not sure a pick whose day is outside `week_start`/`week_end` skips the `snap["adds"]` bump and still lands in `counted` via the transaction’s `when`. The transaction uses the pick’s timestamp (`model.py:1416-1417`, `1430`).

## 5. Games left vs Yahoo’s `matchup.games`

**(a)** Yahoo’s side is `self.matchup["games"]`, `{team: {"played", "left"}}` for you and the saved opponent. `week_of()` (`yahoo.py:739-754`) copies it from the scoreboard. The API field is `team_remaining_games/total`: `played` = `completed_games + live_games`, `left` = `remaining_games` (`yahoo.py:252-255`). A text snapshot is `Games played N Games remaining M` (`yahoo.py:629-630`). The key is omitted when either side is missing (`yahoo.py:753`).

The model’s side, the number This week shows, is `sum(self.starts(*self._week_side(team, punt)).values())` (`model.py:4765-4766` and `4700`). `_week_side` (`model.py:2536` area; yours comes from `_week_mine`) is who plays, from which day, until which day. `starts(..., plays=True)` is expected starts: chance to play, bench fill-in, lineup slots. It is not “NBA games left on the roster.” `this_week()` returns that sum as `games.you` / `games.them`, and Yahoo’s dict as `yahoo_games` (`model.py:4761-4766`). The page prints both (`web/index.html:1039-1041`).

`_week_side` needs a schedule (`self.sched`). Without one, `this_week()`’s `games` key is absent (`model.py:4765`).

**(b)** Yes for the model’s count. `_week_side` / `_week_mine` read the roster `claimed()` and `moved()` already edited, and `_claims()` (`model.py:5151`) starts the add on `clears` and stops the drop the day before. Yahoo’s `left` is the scoreboard at pull time. It does not include the claim or the plan pick.

**(c)** `starts(*_week_side(team))` for you and `matchup["opponent"]`. That is the lineup pass, not `_plan()` / `_deep()`. Skip when `matchup` has no `games` or `self.sched` is empty. The synthetic league has neither a matchup nor a schedule. `this_week()` returns no opponent (`model.py:4482-4484`).

**(d)**
- The two numbers are different counts. The page tooltip says Yahoo counts a starter for every team game even if he sits, and is lower when a bench player fills in (`web/index.html:1041`). Equality fails on a normal week. The brief’s “31 left vs ours” is a gap, not a match.
- Yahoo’s `left` ignores a plan pick and a claim that has not cleared. The model’s `starts()` includes both. The gap widens by those games. That is the pick working, not a bad roster.
- `matchup.games` is only you and the saved opponent (`yahoo.py:750`). Other teams are not in that dict. Their scoreboard `games` sit on `snap["matchups"]` (`yahoo.py:244`, kept by `build()` at `yahoo.py:710`).
- No `games` key: text snapshot with no “Games remaining” line, or `week_of` dropping a None (`yahoo.py:753`). A missing key is absence, not zero.
- Playoff weeks are stored with empty `matchups` (`yahoo.py:276-278`). Not sure the live scoreboard still sends `team_remaining_games` in a playoff week. If it does, `week_of` still copies it. If the week has rolled (`model.py:1600-1604`), `self.matchup` is replaced from the season schedule and the `games` dict is gone.
- Punt is an argument of `_week_side` for your side (`_week_mine`). The opponent’s side does not concede. A check with a punt and a check with none can differ on your count. Yahoo’s `left` does not change with a punt.

## 6. This week’s category totals vs Yahoo’s scoreboard

**(a)** Yahoo’s totals for the current pairing are `self.matchup["totals"]`, `{team: {stat: float}}`, written by `week_of()` (`yahoo.py:752`) from `snap["matchups"]`. Every pairing’s totals are also on `snap["matchups"][i]["totals"]` (`yahoo.py:244-251`, text `yahoo.py:627-628`). Percent stats are dropped (`"%" not in k`, `yahoo.py:251` and `628`). Stored keys are the counting stats plus FGM/FGA/FTM/FTA via `YAHOO_STAT` (`yahoo.py:26-27`). FG% and FT% are not stored.

The model’s score so far is `_so_far()` (`model.py:4824-4856`). It starts from those Yahoo totals (`model.py:4835-4836`) and adds box scores from `matchup["from"]` through yesterday, plus today’s live lines for tipped games, skipping teams in `matchup["held"]` so a game Yahoo already counted is not added again (`model.py:4844-4855`).

The totals This week prints are that score so far plus the rest of the week (`model.py:4767-4768`: `total(so_far) ` and `total(so_far + left)`). `left` is `week_vec` on `_week_side`, the same expected-starts line as check 5.

**(b)** Yahoo’s dict is the pull, before `claimed()` / `moved()`. `_so_far()` adds games for whoever is on the roster now, so a plan pick’s games since `from` can be included. A claim’s games are included only once `clears` is in the past; `claimed()` does not put a future claim into Yahoo’s totals. Not sure `_so_far` filters the claim’s add out before `clears`. It uses `_playing(rosters)` (`model.py:4848`), and the claim is already on that roster.

**(c)** Compare `matchup["totals"]` to the Yahoo half of `_so_far`, stat by stat, for you and the opponent. `_so_far()` is one pass over the box scores. Comparing to `so_far + left` compares a projection to Yahoo’s score so far and fails every day. The synthetic league has no `matchup` and no `matchups`. `_so_far()` returns `{}` (`model.py:4835-4836`).

**(d)**
- `_so_far()` is not equal to `matchup["totals"]` once `from` is before today: it adds nights the snapshot did not hold (`model.py:4837-4851`). A text snapshot whose `from` is yesterday will not match its own totals after that add. An API pull sets `from` to today (`yahoo.py:336-337`), so the added piece is today’s live lines for teams not in `held`.
- When the scoreboard cannot be read, `from` becomes tomorrow after 7 PM ET and `held` is unset (`yahoo.py:333-335`). `_so_far()` then returns the Yahoo totals unchanged (`model.py:4838-4839`). The model’s “games left” still includes tonight (`model.py:1607-1610` clears `tipped` only when you have no totals). Games and totals then describe different sets of games.
- Small gaps are stat corrections. `docs/MODEL.md` §3 records Anthony Black’s REB and AST each 1 short of the final box on Nov 16.
- FG% / FT% have to be rebuilt from FGM/FGA and FTM/FTA. Comparing a percentage column to the stored totals does not match a key.
- A switched team (`switched()`, `model.py:5941`) keeps the old totals and replaces the opponent from the schedule. `week_of` (`yahoo.py:747-752`) copies the picked opponent’s own scoreboard row when the pull has it. An opponent with no row keeps your pairing’s totals (`theirs = mine`). The model then shows one team’s totals under the other team’s name.
- `matchups[].won` (`yahoo.py:256-260`) is categories won so far this week. It is not on `matchup` after `week_of`. It is not the season record.

## 7. Each team’s record vs Yahoo’s standings

**(a)** The only season W-L-T in the league file is `snap["records"]`, `{team: {"w", "l", "t", "rank"}}`.

Yahoo writes it from the standings call, `outcome_totals/wins|losses|ties` and `rank` (`yahoo.py:224-227`). A text snapshot writes it from the team header `1. Name (W-L-T …)` (`yahoo.py:614-616`).

`season()` reads that dict (`model.py:2739-2740`) and copies `w`/`l`/`t` onto `playoffs["teams"]` (`model.py:2767-2768`). The League page prints that copy (`web/index.html:1420`). `standings()` (`model.py:2704-2712`) is a different number: projected win % and a rank. `profile()["standings"]` is that projection (`model.py:3610-3614`). It is not Yahoo’s W-L.

There is no second standings object to compare against. `results` (`yahoo.py:267-272`) is last week’s final category score, kept once the week is `postevent`. `matchups[].won` is this week’s categories so far (`yahoo.py:256-260`).

**(b)** Claims and plan picks do not change `records`. `moved()` copies `adds` and `transactions` only (`model.py:1401`).

**(c)** Read `snap["records"]` for each team in `self.rosters`. Nothing to compute. The synthetic league has no `records` and no schedule, so `season()` returns `None` (`model.py:2735-2737`) and the page has no record column. A missing dict should skip. Comparing to `standings()` fails: those ranks are the model’s win %.

**(d)**
- Adding `matchups[].won` into `records` counts this week’s unfinished categories as season wins. Yahoo’s standings are completed weeks.
- `season()` treats a missing team as 0–0–0 (`model.py:2740`). A team absent from `records` looks like a zero record next to a real one. The synthetic league is all zeros if a check defaults the missing dict that way.
- One Win leagues store matchup wins in the same `w`/`l`/`t` keys (`season()`’s note, `model.py:2733-2734`). `season()` returns `None` for `scoring == "one_win"`. The record dict is still there.
- Playoff weeks do not get a new record field. `records` stays the regular-season standings. `results` is only the previous week (`yahoo.py:174-175`, `267-272`).
- Text header parse takes the first four integers (`yahoo.py:616`). Not sure a header with an extra number (a tiebreak or a points total) shifts `w`/`l`/`t`/`rank`. The API path does not use that regex.

## Where a failure can show

`roster_check()` (`model.py:5748-5819`) returns `{active, il, spots, il_slots, problems, notes}`. `problems` is `[{kind, players, text, fix}]`, worst first. Kinds today: `il`, `drop`, `park`, `open`, `lineup`. `notes` are `kind: "back"` and do not raise the nav dot.

`state()` puts that dict on the payload as `check` (`model.py:5522`). `/api/check` returns the same object (`app.py:293-294`).

The page:

- Nav dot on My team while `check.problems.length` (`web/index.html:2236-2238`).
- Banner on My team and This week from `rosterAlert()` (`web/index.html:871-879`), called in `render()` for those two views (`web/index.html:2273`). It prints `problems[].text` and, for `kind === "drop"`, the Who-to-drop pick. `notes` render in the same banner with a muted dot when there are no problems.
- My team’s header uses `check.active` and `check.il` (`web/index.html:844`).

A new problem shows up if it is appended to `problems` with a `kind`, `text`, and `fix`. `rosterAlert` does not special-case unknown kinds: it prints `text` and `fix` (`web/index.html:876`). The nav dot follows `problems.length`, so a note-only entry does not light it.

`roster_check` runs your team only, and it calls `_week_mine` for the notes (`model.py:5801`). The seven checks cover every team and do not need that call. Running them inside `roster_check` ties them to every `/api/state`. Running them once at the end of `load()` and having `roster_check` copy the stored list does the same, because `app.py` keeps one `lg` until the next `load()`.
