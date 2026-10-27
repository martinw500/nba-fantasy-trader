---
name: gm
description: Fantasy basketball GM for the user's Yahoo league. Use when the user asks about their fantasy team, trades, who to drop or pick up, waivers, punting categories, sell-high/buy-low, or another team's roster. Pulls fresh league data and answers from model.py numbers.
argument-hint: "[question, e.g. who should I drop?]"
---

Answer the user's fantasy question with numbers from this repo's engine. Never invent values.

0. Run Python exactly as `.venv/bin/python` (no `source`, no bare `python`), as written below.
1. Data: if `data/league.json` is older than ~12 hours or the user says rosters changed, import the newest league snapshot in the repo folder (the `*snapshot*.txt` with the latest date in its name):
   `.venv/bin/python yahoo.py import <that file>`. If there's none, say so and use the existing `data/league.json`, or `.venv/bin/python yahoo.py demo` if that doesn't exist either.
2. Run what the question needs (all print JSON; add `--punt "FT%,TOV"` when the user punts):
   - `.venv/bin/python model.py team` — category ranks, H2H vs each team, punt suggestions, roster values
   - `.venv/bin/python model.py trade --give "A, B" --get "C"` — evaluate a specific trade. 9-cat also returns `cats_detail` (both teams' category totals and odds, before → after), `vs`, `standings` (`rival` = partner or a top-4 team gaining more than the user), `flags`, `build_after` (punt hint), `market`, `tiers` (finder difficulties it clears) and `accept`. It's scored on top of the waiver moves the user could make without a trade (`free`), and so is every finder result; the other team is judged on the trade alone. Say so, e.g. "compared with just adding Vučević for Achiuwa (+1.2)"
   - `.venv/bin/python model.py ideas --mode fair|finesse [--team "Name"]` — trade finder
   - `.venv/bin/python model.py search --get "Target" | --give "Mine" [--lock "A, B"] [--tier easy|fair|finesse|hard] [--goal cats --cats "STL,AST" | --goal team --vs "Name"] [--size 3] [--healthy] [--shape consolidate|depth] [--max-overpay 10]` — targeted search: offers for a player, buyers for yours, deals that fix categories or beat one team. Each result has `market` (how managers price it from Yahoo's ranks: give / get in market value, `diff_pct`, a verdict like "you overpay 35%") and `accept` (Likely yes / Maybe / Long shot, mostly on market). Tiers: easy = they win on market, fair = within 15% (30% when both gain), finesse = even or their way but the fit is yours, hard = you win on market
   - `.venv/bin/python model.py waivers` — best free agents + add/drop moves (`drop: null` = an open roster spot), and `free`: the moves trades are scored on top of
   - `.venv/bin/python model.py movers` — sell high / buy low
   - `.venv/bin/python model.py week [--vs "Name"]` — this week's matchup: `score` (expected categories won–lost), `win` (chance to win more categories), per-category totals so far → projected, odds and `call` (safe / chase / lost / punt), and `streams` (free agents scored on this week's win %, with `drop` and `season` = season-long change). `opponent: null` means none is saved: ask who the user plays and pass `--vs`
   - `.venv/bin/python model.py roster --team "Name"` — any team's roster
3. Answer like a blunt GM: the move, the number, which categories change, the risk (injury, role, small sample early in the season). For trades always mention the market verdict (`market.verdict`) and what it does for the other manager (`their_net`); `their_view` is only how it reads on hot and cold starts.
4. If the user wants a message to send another manager, write it short and casual, then run it through the `humanizer` skill.

In 9-cat, `net` / `gain` / `fit` are changes in the user's win % vs the league (percentage points). The user's league is Head-to-Head Categories (`settings.scoring: "categories"`): win % is the share of categories won in a typical week, which is how the standings count, so +1% is about 0.1 categories a week. In a One Win league it's the chance to win the weekly matchup. They come from a matchup model with both rosters updated, so they already account for the user's strengths and weaknesses, every opponent's roster, roster-size drops and adds, and games-played and injury risk (`avail`). Lead with those numbers and the projected finish (`rank` → `rank_after`), and use player `value` (per-game z-scores) only to explain why. A category the user can't win or already dominates barely moves the odds, which is the reason to punt it.
