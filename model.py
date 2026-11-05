"""Fantasy basketball engine: player values, team outlooks, trades, waivers, sell-high/buy-low.

Values are per game, rest of season (ROS): expert projections (Yahoo's, ESPN's) blended with the last two seasons,
shrunk toward this season as games pile up. 9-cat uses z-scores vs the rostered pool; points uses the league's
scoring weights.

9-cat decisions (trades, waivers, standings) use a matchup model on top of that: each team's expected weekly
totals -> odds of winning each category vs every other team -> what the week is worth in the standings: the share
of categories won (Head-to-Head Categories, each one counts) or the odds of winning the matchup, 5 of 9 (One Win).
A move is judged by how much it changes that win %, so a category you already dominate or can't win
stops mattering on its own (punting emerges), and the other teams' rosters are part of every answer.

CLI (prints JSON, used by Claude Code and for quick checks):
  python model.py team                      # win %, projected standings, category odds, punt suggestions
  python model.py trade --give "A" --get "B, C"
  python model.py ideas --mode finesse      # fair | finesse
  python model.py search --get "Wembanyama" --tier easy --lock "Jokic"   # offers for a target, see --help
                                            # every trade and search result is priced on the market too
  python model.py waivers                   # trades and search are scored on top of the free moves it lists
  python model.py movers                    # sell-high / buy-low
  python model.py week --vs "Team"          # this week's matchup, adds left and streams (--vs: the snapshot's opponent)
  python model.py strategy                  # punt planner: every build's waiver adds + up to 3 trades, true odds (~15s)
  python model.py news                      # ESPN news on your players and their teammates since the snapshot
  python model.py compare --players "A, B"  # 2 to 4 players side by side, with your win % with each in a roster spot
  python model.py move --add "A" --drop "B" # any add/drop: win % change this week (--day, else its best day) and season
  python model.py sources                   # when stats, box scores, schedule, injuries, projections, news, live scores were fetched
  add --punt "FT%,TOV" to any of them
"""
import argparse
import collections
import copy
import difflib
import functools
import itertools
import json
import re
import sys
import threading
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OLDER, LAST, CUR = "2024-25", "2025-26", "2026-27"
SHORT = {"2019-20": 70, "2020-21": 72}  # seasons under 82 games (COVID; 2019-20's teams played 64-75): fetch() scales GP
CATS = ["FG%", "FT%", "FG3M", "PTS", "REB", "AST", "STL", "BLK", "TOV"]
COUNTING = CATS[2:]
RAW = ["FGM", "FGA", "FTM", "FTA"] + COUNTING
POINTS = {"PTS": 1, "REB": 1.2, "AST": 1.5, "STL": 3, "BLK": 3, "TOV": -1}  # Yahoo default
SHRINK = 20  # production per minute: games of baseline minutes that count as much as this season's minutes played
MIN_SHRINK = 3  # minutes per game: games of baseline that count as much as this season's (a new role shows at once)
RECENT = 45  # days: this season's games weigh 0.5^(days ago / RECENT) in its per-game line (recent()). Fitted with
             # MIN_SHRINK 3 on 2022-23 to 2025-26 (python backtest.py recency, Oct 30). Rerun Nov 4 after M26 and A2
             # (rookies): against the old line (MIN_SHRINK 6, every game alike) weeks 1-6 +1.3 / -0.8 / +0.1 / -0.2%,
             # season -0.4 / -1.4 / -0.3 / -1.3% (was -2.0% everywhere). MIN_SHRINK 4 was 0.04-0.32% better over the
             # season in all four, but the weekly matchups (python backtest.py [season], what win % runs on) were worse
             # at 4 and 6 in three of four seasons (category log loss 0.5135 / 0.5128 / 0.5208 / 0.5141 at 3, real
             # schedule), so both stay; 30-60 days within 0.3%. A 7-day half-life is +5.5%: streaks don't last.
             # Slower minutes for established players' early dips gained nothing
OLDER_W, REGRESS = 0.5, 25  # baseline: a game two seasons ago counts half; 25 games of a fringe line pull small samples in
# M26: a player no expert projects (deep bench, two-way, undrafted) is pulled toward BENCH_MIN minutes at the fringe
# line's rates, not the fringe line's ~26 minutes (prior(); ros() for a rookie with no line); None = off. Fitted Nov 4
# (python backtest.py bench, 2022-23 to 2025-26): those players ran ~2 min a game high in weeks 1-6 (15.8 projected,
# 13.8 played); rest-of-season stats error -2.2 / -3.5 / -2.1 / -0.6% for everyone (theirs -7.0 / -10.6 / -6.4 / -1.6%),
# 10-15 about as good. Next week's stats (teammates) better in 2025-26 and 2024-25 too, minutes 0.3 a game lower.
# Rerun Nov 4 after A2 (rookies' faster rates, empty expert rows): everyone -2.1 / -4.3 / -2.0 / -0.4% (theirs -6.5 /
# -10.3 / -5.9 / -1.0%), still better than the fringe line every season; 15 ties it pooled (-2.2%), so 12 stays
BENCH_MIN = 12
PROJ_W = 0.6  # expert projections' weight in the pre-season line (prior()); fitted by python backtest.py replay
# a rookie (no NBA past, so no baseline): the expert line weighs ROOKIE_W in prior() (the rest is the fringe line), and
# his production per minute leans on this season at ROOKIE_K games of his minutes in ros(), not SHRINK (prior and
# rookies no expert projects alike); None = PROJ_W / SHRINK. Fitted Nov 4 (python backtest.py rookies, 2022-23 to
# 2025-26, audit #3): projected rookies' rest-of-season error -1.5 / -5.5 / -2.5 / -0.5% (weeks 1-6 -1.1 / -5.0 / -2.7
# / -1.8%, week 0 -7.9 / -1.0 / -2.3 / -2.2%), everyone's -0.07 / -0.28 / -0.21 / -0.13%; every weight 0.7-0.8 with
# 8-15 games wins in all four seasons. The expert line alone (1.0) is worse in 2 of 4: a rookie's projection is no
# surer than a veteran's, it just has nothing to blend with
ROOKIE_W, ROOKIE_K = 0.75, 10
HYPE = 10    # same, for how other managers see players (hot starts count more: 3 games = 23%, 10 games = half)
FORM_DAYS, FORM_GP = 14, 3  # recent form: value over the last 14 days, for players with 3+ games in them
# form this far (x League.u) from rest-of-season value: a trend arrow and Hot & cold, at TREND for a window of TREND_GP
# games and x sqrt(TREND_GP / games) for others (trend_cut(), M8): one standard deviation of a 14-day window's gap among
# the 156 best each Monday, 2022-23 to 2025-26 (python backtest.py trend, Nov 4: 4.1 at 3 games, 2.4 at 7, sd x
# sqrt(games) 6.3-7.2, 6.5 pooled, 6.7 in 2025-26), so a third get one whatever the window holds (30-37%; a flat 3.0
# gave 49% of 3-game windows and 22% of 7-game ones). The gap doesn't carry into the next 14 days (slope -0.07 to
# +0.02): an arrow says what happened, not what will
TREND, TREND_GP = 2.5, 7
OUT = {"O", "INJ", "IL", "IL+", "NA", "SUSP"}  # Yahoo statuses that mean "not playing now"
BENCH = {"BN", "IL", "IL+", "NA"}  # Yahoo roster slots whose games don't count; any other slot (PG ... Util) starts
# a waiver move with no drop that puts an injured free agent in a free IL slot (B5: mchc allows adding straight to IL;
# Yahoo's default doesn't). _drops() offers it, _gains() scores it, free_moves() returns it as the drop
PARK = "(IL slot)"
# ESPN notes for a rest day ("George will be rested Monday", "Robinson (rest) ...", not "the rest of Sunday's game"): he
# sits one game, so he's tagged REST, not O, and isn't treated as injured (Martin, Nov 2: George and Black sat the second
# night of a back-to-back). ESPN's return date is his team's next game, so availability already counts just that one
REST = re.compile(r"\(rest\)|\brest(ed|ing)\b|load management", re.I)
POS = ["PG", "SG", "SF", "PF", "C"]
SLOT_POS = {"G": {"PG", "SG"}, "F": {"SF", "PF"}, "Util": set(POS)}  # starting slots that take more than their own name

# Matchup model. DISPERSION, SCHED_CV and PCT_NOISE are fitted to real weeks of 2025-26 box scores (backtest.py: 20
# simulated leagues, predictions from what was known each Monday, injured regulars sitting until a rough return date as
# with ESPN's); re-run it after changing how lines are projected.
GAMES_WEEK = 3.4                                   # NBA games per team per week
DISPERSION = {"FG3M": 1.75, "PTS": 3.0, "REB": 2.5, "AST": 3.0, "STL": 1.0, "BLK": 1.5, "TOV": 1.25}  # variance / mean
SCHED_CV = 0.10                                    # week-to-week swing beyond box-score noise (schedule, rest, minutes,
                                                   # and projection error); 0.12 before N9, 0.17 before it knew who's out
PCT_NOISE = 1.5                                    # FG%/FT% variance vs coin-flip shooting at each player's rate (was 1.75)
# M9: a week's categories move together. A team that gets more games or minutes than expected (or whose players were
# over-projected) wins PTS, REB, AST, 3PM, STL and BLK together and loses TO; FG% and FT% barely move with it. Measured
# on backtest.py's matchups 2022-23 to 2025-26 (the miss of each category's margin over its sd): counting stats 0.2-0.7
# apart, TO -0.3 to -0.6, one factor carrying most of it. So this week's chance to win the matchup (match_prob) has
# each category's margin load WEEK_LOAD on one shared factor (counting +, TO -, FG%/FT% 0), independent given it. With
# the week's real schedule (This week) matchup log loss 0.5459 / 0.5425 / 0.5497 / 0.5066 -> 0.5436 / 0.5418 / 0.5469 /
# 0.5046 (every season, and with the load fitted on the other three), 10-20% predictions that won 17-19% now win 15-17%.
# On an average week (One Win's season numbers) it measured no better (1 of 4 seasons) and stays off there. A loading
# per category, or one on FG%, gained nothing (python backtest.py corr)
WEEK_LOAD = 0.5
GH = (lambda x, w: (x, w / w.sum()))(*np.polynomial.hermite_e.hermegauss(5))  # N(0, 1) nodes, weights (3 give the same)
WEAK = 0.35                                        # a "weak spot" / punt hint: winning a category under 35% of
                                                   # weeks (about the bottom 10% of team-categories once odds are calibrated)
FILL = 0.5                                         # share of a sat-out player's games your bench/streamers cover
                                                   # (not in backtest.py: its rosters never change, so nobody fills in)
# A status tag with no ESPN return date, on a player who sat his team's last game: the share of availability() he
# plays rest of season (python backtest.py availability, 2022-23 to 2025-26, of each team's games since Oct 30: 0.91
# after sitting 1 game, 0.79 over all, 0.69 after 11+). SUSP is a guess. A tagged player who played his team's last game
# isn't cut (_availability()).
STATUS_AVAIL = {"GTD": 0.91, "DTD": 0.91, "O": 0.8, "SUSP": 0.8, "INJ": 0.7, "IL": 0.7, "IL+": 0.7, "NA": 0.0}
# Game-day designations (M16): ESPN's note names the NBA's listing for one game ("is listed as questionable for
# Wednesday's game"), and that game alone counts at how often players so listed played. Measured Nov 3 on the daily
# feeds since Oct 28 against box scores (python backtest.py gameday, 104 player-games): probable 5 of 5, questionable
# 14 of 29, doubtful 0 of 4 (out / ruled out 2 of 66), each pulled toward 50% by one game split evenly (Jeffreys).
# Small samples: refit in December on a month of the note log (data/injury_notes.jsonl)
GAMEDAY = {"probable": 0.92, "questionable": 0.48, "doubtful": 0.10}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
LISTED = re.compile(r"\b(probable|questionable|doubtful|game-time decision)\b[^.]{0,60}?\b(" + "|".join(DAYS) + r")'s\b", re.I)
# Availability (availability()): a game of his better past season counts AVAIL_PAST of one this season, pulled toward
# AVAIL_T by AVAIL_K games. Fitted on 2024-25 and 2025-26 (python backtest.py availability: each Monday's guess vs the
# share of games left that players who were playing then suited up for). Past games barely predict it (correlation
# 0.04-0.18, this season's 0.26), and the worse past season adds nothing, so one lost season isn't fragility.
AVAIL_PAST, AVAIL_K, AVAIL_T = 0.2, 50, 0.7
# Late news (M17): a player who played his team's last game is ruled out for the next one at HAZARD x (1 - his
# availability) a game, and ONE_GAME of those absences last only that game (else he misses the rest of the week).
# 2022-23 to 2025-26, the 156 best players each Monday (python backtest.py news, Nov 3): 7.2% of games, 5.4% in the
# healthiest fifth to 10.1% in the least, HAZARD 0.30-0.36 a season; ONE_GAME 0.55-0.57. The add plan keeps its last add
# for that unless the move beats it (_reserve())
HAZARD, ONE_GAME = 0.33, 0.56
# This week (M27, fresh_plays()): a player who played his team's last game plays his j-th game from today at that same
# chain, (1 - h)(1 - h(1 - ONE_GAME))^(j-1), h = HAZARD x (1 - availability): injuries come in spells, so the next game
# runs healthiest and each later one a little less. 2022-23 to 2025-26 from every day of the week (python backtest.py
# ahead, Nov 4, 121k player-games): played 0.930 / 0.893 / 0.874 / 0.858 of his next / 2nd / 3rd / 4th game, the chain
# within 2% of each (the flat WEEK_K 0.45 it replaces: +3.2% / -1.0% / -3.1% / -4.9%), the rest of the week from each
# weekday within 1% (was 0.998 Monday to 1.026 Sunday), Brier 84.2 -> 83.6 a thousand games, the same every season
# Teammates' injuries (lifts()), fitted on 2025-26 box scores (python backtest.py teammates): while a regular (REGULAR+
# minutes) sits, SHARE of his minutes go to teammates playing 10+, split by their room under ROOM minutes x
# e^(ROLE x role similarity) and played at their own rates; USAGE of the USAGE_STATS those minutes don't replace go to
# the same players by their output. The rest goes to the end of the bench. SHARE was 0.37 (the in-sample fit, each
# sitter alone) until M4 (Oct 28): with everyone out at once spread together and the preseason projection's part taken
# out (priced_in()), next week's stats are best at 0.5 in 2025-26 and out of sample in 2024-25, minutes better still
REGULAR, SHARE, ROOM, ROLE, USAGE = 25, 0.5, 36, 0.5, 0.25
# M25 (measured by python backtest.py minor): a sitter under FULL minutes (only when REGULAR is below it) hands out
# SHARE_LOW of his, not SHARE; CAP_GIVE caps what one sitter hands out, the team's own split (onoff()) included, at
# his own minutes
FULL, SHARE_LOW, CAP_GIVE = 25, 0.15, False
# Each team's own split (onoff(), M13): a receiver's minutes in this season's games a regular sat, vs his games with him,
# pull that sitter's lifts() minutes toward what really happened, as if the rule's guess were worth ONOFF_K games.
# Minutes only: shots in a couple of games are mostly noise (Bey's 25 on his usual 26 min)
ONOFF_K = 4
TERM_DAYS = (7, 30)  # a teammate's absence that lifts a player: short-term if he's back within a week, medium within a month
USAGE_STATS = ["FGM", "FGA", "FTM", "FTA", "PTS", "AST", "TOV"]  # rebounds, steals, blocks and threes barely move

# Playoff odds (season_sim()): how far a team's real weeks land from its projected odds, as logit shifts on every
# category's odds. SEASON_SD: one per team for the whole season (players beating or missing their projection, injuries);
# WEEK_SD: one per matchup (a big week moves most categories together). Without them the rest of a season's category
# wins would spread about 2x too narrowly (2.6x from week 0, 1.8x from week 10). Fitted on 2025-26 (python backtest.py
# playoffs): WEEK_SD matches a week's spread, SEASON_SD the rest of the season's from each Monday (0.37-0.39 in weeks
# 0-1, 0.31-0.36 after), and both give the best playoff log loss (0.351, naive 0.441). A team's strength also drifts
# (its edge in weeks 0-4 carries 0.57 into weeks 5-9, 0.32 into the playoff weeks), but a weekly drift term gained
# nothing. Its rosters never change, so real leagues swing a bit more. Rechecked with the real schedule (M2, Nov 4):
# 0.35 / 0.6 still on the diagonal (log loss 0.3444, title 0.2631); 0.4 / 0.65 0.3430 / 0.2614, too close to move
# Rerun after A2 (Nov 4; its leagues draft differently now, so the levels aren't comparable): 0.35 / 0.6 0.3233 /
# 0.2588, within 0.0001 of 0.35 / 0.5 and 0.7; 0.25 and 0.45 0.3266 / 0.3263
SEASON_SD, WEEK_SD, SIMS = 0.35, 0.6, 4000

# Trade market: what managers pay for a player, from his market rank (League.market): 100 x MARKET^(rank - 1).
# Steep, so a star is worth more than the sum of spare parts: rank 1 = 100, 25 = 48, 50 = 22, 100 = 5, 150 = 1.
# ponytail: starting guess; tune with the trade log (N6).
MARKET = 0.97
# Games until Yahoo's Cur rank counts half in the market (League.market). Market only: HYPE's 10, averaging ranks, let 4
# cold games sink Jalen Brunson (Pre 27, Cur 217) to #81 behind a hot Paul George (Pre 75, Cur 23) at #50 (Martin, Oct 28:
# managers don't trade like that). ponytail: a guess like MARKET; tune with the trade log (N6)
CHASE = 20
# Hot & cold ONLY, never trades (Martin, Nov 2: "a lot of ppl lean on yahoo current rank ... it should be a mix tho ...
# only for hot and cold and not regular trading"): sell high and buy low price players with Yahoo's current rank
# counting half after this many games instead of CHASE's, so a run moves the price (6 games in: 55% current, 45%
# preseason). Trades, Will they say yes and the finder keep CHASE (League.market). ponytail: a guess like CHASE; tune with M6
HOT_CHASE = 5
# Players traded together are worth less than their sum: managers pay for the best player in a deal (the side taking
# the rest needs roster spots for them), so a package counts its best player in full and each next one at PACKAGE
# times the one before: 100%, 70%, 49%. Three players for one star cost more than their market sum (Martin, Oct 30:
# "if you're consolidating a lot like 3 for 1 then it's hard for ppl to accept even if market is similar"). At 0.7 a
# 2nd- plus a 3rd-round player is even for a 1st-rounder, the usual rule of thumb (it puts the second at 0.64). Balanced
# deals barely move; a throw-in can't dodge it (he counts least). ponytail: a guess like MARKET; tune with the trade log (M6)
PACKAGE = 0.7
EVEN = 5  # market gap (%) that still reads as "even"
# A free move never drops a player the market ranks in the first CHIP rounds (CHIP x teams: 96 in a 12-team league):
# he's a trade chip, sold rather than cut (M3: under FT%+TO the model dropped Lillard, market #33, for Vucevic, and
# every trade and plan was scored on top of it). Waivers' Suggested moves skip him too and say "sell him instead".
# ponytail: a rank cutoff; tune if chips pile up
CHIP = 8
CHIPPED = 1e6  # search()'s drop order: a chip's value plus this, so he's cut last (and a deal cutting one is skipped)
# Trade search difficulty: (min gain for you, min gain for them, both win %; market gap range, % you gain on market).
# easy: they win on market and you still gain. fair: market within 15%, an offer that looks even. finesse: market even
# or in their favour, but the fit is strongly yours. hard: you win on market, needs a pitch (or FAIR_FIT, below).
# No tier overpays over 30%.
# ponytail: starting guesses like the knobs above; tune once real offers get accepted or turned down.
TIERS = {"easy": (0.5, -1, -30, -EVEN), "fair": (1, -np.inf, -15, 15), "finesse": (1.5, -2.5, -30, EVEN),
         "hard": (3, -6, EVEN, np.inf)}
# hard's second route: you win on market by up to this and both teams gain in real odds, so the fit is the pitch ("you
# may send slightly lesser players, as long as the fit helps them", Martin, Oct 26). It was part of fair (both ways)
# until Oct 28, when fair's 15-29% gaps read as unfair (Martin: "some of them don't seem really fair")
FAIR_FIT = 30
VERDICT, MOVE, GUARD = 0.5, 0.5, 1  # trade accept/decline beyond +-; waiver move worth listing; most a cats/team goal
                                    # search may cost you
# The win-% cutoffs above (and accept()'s) are in One Win terms. A categories league's win % moves about 2.5x less
# per move (measured Oct 26 on the real league), so League scales them by this.
CAT_SCALE = 0.4
# Punt planner (strategy()): staying balanced, every single-category punt, and these pairs
PAIRS = [("FT%", "TOV"), ("FG%", "TOV"), ("FG%", "FT%"), ("AST", "TOV"), ("FG3M", "FT%"), ("FG%", "FG3M")]


ACCEPT = ["Long shot", "Maybe", "Likely yes"]


def accept_level(mkt, their_net, s=1.0):
    """How likely the other manager says yes, as an index into ACCEPT: mostly on market value (priced()'s diff_pct), a
    little on real fit. Within 15% on the market, or a lowball up to FAIR_FIT that helps them, is at least a Maybe.
    Works on arrays (search() ranks with it)."""
    likely = (mkt <= -EVEN) & (their_net > -s)
    return np.where(likely, 2, np.where((mkt <= TIERS["fair"][3]) | ((their_net > 0) & (mkt <= FAIR_FIT)), 1, 0))


def accept(mkt, their_net, s=1.0):
    return ACCEPT[int(accept_level(mkt, their_net, s))]


def package(values):
    """Market value of players traded together: the best in full, each next one at PACKAGE times the one before."""
    return float(sum(v * PACKAGE ** i for i, v in enumerate(sorted(values, reverse=True))))


def priced(give, get, sums=None):
    """A trade on the market, from the market value each side ends up with (roster spots included). diff_pct: what
    you gain as a share of the bigger side, so a deal is the same size from both ends (+20% for you = -20% for them).
    give / get are packages (package()), what acceptance reads; sums: the same with each side's plain sum, shown next
    to it as "sum" (Martin, Oct 31: see both)."""
    d = round(100 * (get - give) / max(give, get, 1), 1)
    out = {"give": round(give, 1), "get": round(get, 1), "diff_pct": d,
           "verdict": "even" if abs(d) < EVEN else f"you overpay {-d:.0f}%" if d < 0 else f"they overpay {d:.0f}%"}
    return {**out, "sum": priced(*sums)} if sums else out


def punts(p):
    """Punted categories from the app, the CLI or the AI GM: a list or "FT%,TOV" text, any case, Yahoo's TO / 3PTM
    for TOV / FG3M. KeyError for anything else, so a typo never quietly scores unpunted."""
    p = [{"TO": "TOV", "3PTM": "FG3M", "3PM": "FG3M"}.get(c.strip().upper(), c.strip().upper())
         for c in (p.split(",") if isinstance(p, str) else p or []) if c.strip()]
    if bad := [c for c in p if c not in CATS]:
        raise KeyError(f"Can't punt {', '.join(bad)}: pick from {', '.join(CATS)}")
    return list(dict.fromkeys(p))


def norm(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"[^a-z0-9 ]", "", name)
    return " ".join(re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name).split())


def match(name, keys):
    """The stats key a name means (League.find): the same name once accents and Jr/III are dropped, a unique last name
    ("Jokic", "Gilgeous-Alexander"), or a close spelling. None when nothing fits, or when it could be more than one
    player: a shared last name ("Johnson") or two spellings about as close ("Jaylen Williams": Jalen or Jaylin), so a
    trade never quietly gets the wrong one."""
    k = norm(name)
    if k in keys:
        return k
    if last := [x for x in keys if x.endswith(" " + k)]:
        return last[0] if len(last) == 1 else None
    close = difflib.get_close_matches(k, keys, n=2, cutoff=0.8)
    score = [difflib.SequenceMatcher(None, k, x).ratio() for x in close]
    return close[0] if len(close) == 1 or len(close) == 2 and score[0] - score[1] > 0.05 else None


# Freshness (Q9): the caches load() reads, as (key, label, file in data/, refresh window in hours, what's lost without it)
SOURCES = [("stats", "Stats", f"stats_{CUR}.csv", 12, "this season's stats"),
           ("logs", "Box scores", f"gamelogs_{CUR}.csv", 12, "recent form, the card's recent lines and teammates' injury boosts"),
           ("schedule", "Schedule", f"schedule_{CUR}.json", 24, "NBA games per week (a flat 3.4 is used)"),
           ("injuries", "Injuries", "injuries.json", 3, "ESPN's injury notes and return dates (Yahoo's tags still count)"),
           ("projections", "Projections", f"projections_{CUR}.json", 24, "ESPN's projections (Yahoo's still count)"),
           ("news", "News", "news_index.json", 3, "ESPN's news"),
           ("live", "Live scores", "live.json", 0.5, "tonight's games: the score waits for tomorrow's box scores and "
            "games under way still count as to play")]
FAILED = {}  # cache file name -> why its last refresh failed in this process; a good refresh clears it


def fetch(season, max_age_hours=None):
    """Per-game stats from stats.nba.com, cached in data/, GP out of 82 games (SHORT). The current season refreshes when
    the cache is stale; offline, the old copy (logged in FAILED for freshness()). No copy at all and no network raises."""
    cache = DATA / f"stats_{season}.csv"
    stale = max_age_hours and cache.exists() and time.time() - cache.stat().st_mtime > max_age_hours * 3600
    if not cache.exists() or stale:
        try:
            from nba_api.stats.endpoints import leaguedashplayerstats
            df = leaguedashplayerstats.LeagueDashPlayerStats(
                season=season, per_mode_detailed="PerGame", timeout=60).get_data_frames()[0]
            DATA.mkdir(exist_ok=True)
            df[["PLAYER_NAME", "TEAM_ABBREVIATION", "GP", "MIN"] + RAW].to_csv(cache, index=False)
            FAILED.pop(cache.name, None)
        except Exception as e:
            FAILED[cache.name] = str(e)[:200]
            if not cache.exists():
                raise
            print(f"Couldn't refresh {cache.name} ({e}); using the old copy.", file=sys.stderr)
    df = keyed(pd.read_csv(cache))
    return df.assign(GP=df.GP * 82 / SHORT[season]) if season in SHORT else df


TRICODE = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "PHO": "PHX", "WSH": "WAS", "UTAH": "UTA", "BRK": "BKN",
           "CHO": "CHA"}  # Yahoo/ESPN short forms -> NBA tricodes


def _cached(cache, hours, get, force=False):
    """Read a JSON cache, refreshing it with get() when older than `hours` (or `force`). Offline: the stale cache, or {}.
    A failed refresh is logged in FAILED for freshness()."""
    if force or not cache.exists() or time.time() - cache.stat().st_mtime > hours * 3600:
        try:
            DATA.mkdir(exist_ok=True)
            cache.write_text(json.dumps(get(), ensure_ascii=False), encoding="utf-8")
            FAILED.pop(cache.name, None)
        except Exception as e:
            FAILED[cache.name] = str(e)[:200]
            print(f"Couldn't refresh {cache.name} ({e}); using {'the old copy' if cache.exists() else 'nothing'}.",
                  file=sys.stderr)
    return json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}


def schedule(season=CUR):
    """{NBA tricode: sorted game dates "YYYY-MM-DD"} from stats.nba.com, cached in data/ for a day."""
    def get():
        from nba_api.stats.endpoints import scheduleleaguev2
        d = scheduleleaguev2.ScheduleLeagueV2(season=season, timeout=60).get_data_frames()[0]
        day = pd.to_datetime(d.gameDate).dt.strftime("%Y-%m-%d")
        out = {}
        for side in ("homeTeam_teamTricode", "awayTeam_teamTricode"):
            for t, dt in zip(d[side], day):
                out.setdefault(t, []).append(dt)
        return {t: sorted(ds) for t, ds in out.items()}
    return _cached(DATA / f"schedule_{season}.json", 24, get)


def injuries(force=False):
    """{player name: {status, note, return}} from ESPN's public injury feed, cached for 3 hours (`force`: refetch now,
    keeping the old copy if that fails), each day's last copy kept as data/injuries_<date>.json: the return dates it
    gave, to measure backtest.py's RETURN_NOISE against later.
    ponytail: unofficial endpoint (personal use); if ESPN changes it, this goes quiet and Yahoo's status tags still work."""
    def get():
        url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
        with urllib.request.urlopen(url, timeout=30) as r:
            feed = json.load(r)
        status = {"Out": "O", "Day-To-Day": "DTD", "Suspension": "SUSP"}
        out = {i["athlete"]["displayName"]: {"status": status.get(i.get("status"), ""), "note": i.get("shortComment", ""),
                                             "return": i.get("details", {}).get("returnDate", ""), "date": i.get("date", "")}
               for team in feed.get("injuries", []) for i in team.get("injuries", [])}
        (DATA / f"injuries_{date.today()}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        # every note the first time it's seen (the day's archive keeps only its last copy, so a morning "questionable"
        # upgraded by tip-off was lost): what M16's rates are refit on
        log, seen = DATA / "injury_notes.jsonl", set()
        if log.exists():
            seen = {(r["name"], r["note"]) for r in map(json.loads, log.read_text(encoding="utf-8").splitlines())}
        with open(log, "a", encoding="utf-8") as f:
            for name, i in out.items():
                if (name, i["note"]) not in seen:
                    f.write(json.dumps({"name": name, "seen": datetime.now(timezone.utc).isoformat(timespec="seconds"), **i},
                                       ensure_ascii=False) + "\n")
        return out
    return _cached(DATA / "injuries.json", 3, get, force)


ESPN_STAT = {"MIN": "40", "FGM": "13", "FGA": "14", "FTM": "15", "FTA": "16", "FG3M": "17", "PTS": "0", "REB": "6",
             "AST": "3", "STL": "2", "BLK": "1", "TOV": "11"}  # ESPN's stat ids (checked Oct 27: PTS = 2 FGM + 3PM + FTM)


def espn(season=CUR):
    """{player name: per-game projection} from ESPN's public fantasy API (its season projection), cached a day, each
    day's copy kept as data/projections_<date>.json for a later fit. A past season gives its preseason projection,
    fetched once: 2025-26's misses that season as much as baseline() does, so it wasn't updated with hindsight.
    ponytail: unofficial endpoint like injuries(); if ESPN changes it, this goes quiet and Yahoo's projections still work."""
    yr = int(season[:4]) + 1
    def get():
        only = {"players": {"limit": 2000, "sortPercOwned": {"sortPriority": 1, "sortAsc": False},  # a sort is required
                            "filterStatsForSourceIds": {"value": [1]},  # 1 = projections
                            "filterStatsForSplitTypeIds": {"value": [0]}, "filterStatsForExternalIds": {"value": [yr]}}}
        req = urllib.request.Request(f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{yr}/segments/0"
                                     "/leaguedefaults/3?view=kona_player_info", headers={"X-Fantasy-Filter": json.dumps(only)})
        with urllib.request.urlopen(req, timeout=60) as r:
            players = json.load(r)["players"]
        out = {p["player"]["fullName"]: {c: round(a[i], 2) for c, i in ESPN_STAT.items() if i in a}
               for p in players for s in p["player"].get("stats", ())
               if s.get("statSourceId") == 1 and s.get("seasonId") == yr and (a := s.get("averageStats"))}
        if season == CUR:
            (DATA / f"projections_{date.today()}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out
    return _cached(DATA / f"projections_{season}.json", 24 if season == CUR else np.inf, get)


def news_index():
    """{player name: [ESPN id, time of his latest news (UTC ISO) or None]} for ESPN's 1000 most-owned players, cached
    3 hours. ponytail: unofficial endpoint like espn()."""
    yr = int(CUR[:4]) + 1
    def get():
        only = {"players": {"limit": 1000, "sortPercOwned": {"sortPriority": 1, "sortAsc": False},  # a sort is required
                            "filterStatsForSourceIds": {"value": [1]},  # any stats filter: none at all sends 3 MB
                            "filterStatsForSplitTypeIds": {"value": [0]}, "filterStatsForExternalIds": {"value": [yr]}}}
        req = urllib.request.Request(f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{yr}/segments/0"
                                     "/leaguedefaults/3?view=kona_player_info", headers={"X-Fantasy-Filter": json.dumps(only)})
        with urllib.request.urlopen(req, timeout=60) as r:
            players = json.load(r)["players"]
        utc = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {p["player"]["fullName"]: [p["player"]["id"], (t := p["player"].get("lastNewsDate")) and utc(t)]
                for p in players}
    return _cached(DATA / "news_index.json", 3, get)


NEWS, news_lock = DATA / "news.json", threading.Lock()


def news(keys, since=""):
    """{stats key: ESPN's fantasy news on him (Rotowire's blurbs), newest first: [{date (UTC ISO), headline, story}]}
    for the keys ESPN lists (news_index()) with news since `since`. A player's news is fetched again only once the index
    shows something newer; kept in data/news.json. Offline: what's saved."""
    ix = news_index()
    names = {norm(n): n for n in ix}
    found = {k: ix[names[m]] for k in keys if (m := match(k, names)) and (ix[names[m]][1] or "") >= since}
    read = lambda: json.loads(NEWS.read_text(encoding="utf-8")) if NEWS.exists() else {}
    with news_lock:
        saved = read()
    def get(pid):
        url = f"https://site.api.espn.com/apis/fantasy/v2/games/fba/news/players?playerId={pid}"
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                feed = json.load(r).get("feed", [])
        except Exception as e:
            print(f"Couldn't fetch news for ESPN player {pid} ({e}).", file=sys.stderr)
            return None
        return [{"date": x["published"], "headline": x.get("headline", ""), "story": x.get("story", "")}
                for x in feed if x.get("type") == "Rotowire"]
    stale = {str(pid): last for pid, last in found.values() if saved.get(str(pid), {}).get("last") != last}
    if stale:
        with ThreadPoolExecutor(8) as ex:
            got = {pid: {"last": stale[pid], "items": items} for pid, items in zip(stale, ex.map(get, stale)) if items is not None}
        with news_lock:  # re-read: the card and the panel can fetch at once
            saved = {**read(), **got}
            DATA.mkdir(exist_ok=True)
            NEWS.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    return {k: [x for x in saved[str(pid)]["items"] if x["date"] >= since]
            for k, (pid, _) in found.items() if str(pid) in saved}


def gamelogs(season=CUR, max_age_hours=None):
    """Every player's regular-season box scores from stats.nba.com, keyed like the stats, cached in data/ and refetched
    once older than max_age_hours. Offline: the old copy, or None."""
    cache = DATA / f"gamelogs_{season}.csv"
    if not cache.exists() or max_age_hours and time.time() - cache.stat().st_mtime > max_age_hours * 3600:
        try:
            from nba_api.stats.endpoints import leaguegamelog
            g = leaguegamelog.LeagueGameLog(season=season, player_or_team_abbreviation="P", timeout=90,
                                            season_type_all_star="Regular Season").get_data_frames()[0]
            DATA.mkdir(exist_ok=True)
            g.to_csv(cache, index=False)
            FAILED.pop(cache.name, None)
        except Exception as e:
            FAILED[cache.name] = str(e)[:200]
            print(f"Couldn't refresh {cache.name} ({e}); using {'the old copy' if cache.exists() else 'nothing'}.",
                  file=sys.stderr)
    if not cache.exists():
        return None
    g = pd.read_csv(cache, parse_dates=["GAME_DATE"])
    g = g[g.WL.notna()]  # a game still being played has no result yet (Oct 31: CHA-PHI at halftime, Maxey 17 min)
    return g.assign(key=g.PLAYER_NAME.map(norm))


# Live scores (M18): nba_api's live endpoints (cdn.nba.com): today's games with tip-off and status (1 not started, 2 under
# way, 3 final), and each started game's box score. Checked Nov 3 on 4 of Nov 2's games: its played lines equal
# stats.nba.com's game logs (84 players, every stat)
LIVE_POLL = 5  # minutes between live fetches while a game is on (live_on()); else SOURCES' half hour
LIVE_STAT = {"FGM": "fieldGoalsMade", "FGA": "fieldGoalsAttempted", "FTM": "freeThrowsMade", "FTA": "freeThrowsAttempted",
             "FG3M": "threePointersMade", "PTS": "points", "REB": "reboundsTotal", "AST": "assists", "STL": "steals",
             "BLK": "blocks", "TOV": "turnovers"}


def live_on(games, now=None):
    """A game under way, or past its tip-off (within 3 hours: a postponed one never starts) and not yet marked: live
    scores refetch every LIVE_POLL minutes then, and app.py's live() polls that often."""
    now = now or datetime.now(timezone.utc)
    return any(g["status"] == 2 or g["status"] == 1 and timedelta(0) <= now - datetime.fromisoformat(g["tip"]) < timedelta(hours=3)
               for g in games.values())


def next_poll(games, idle, now=None):
    """Seconds until app.py's live() polls again: LIVE_POLL minutes while a game is on (live_on()), else `idle` seconds
    or a minute after the next tip-off if that comes first, so its players lock on time."""
    now = now or datetime.now(timezone.utc)
    if live_on(games, now):
        return LIVE_POLL * 60
    return min([idle] + [(t - now).total_seconds() + 60 for g in games.values()
                         if g["status"] == 1 and (t := datetime.fromisoformat(g["tip"])) > now])


def _box(game):
    """{player name: {team, played, sat: inactive or a not-playing reason (DND_INJURY, DND_REST...), RAW}} from a live
    box score's game."""
    return {p["name"]: {"team": t["teamTricode"], "played": p.get("played") == "1",
                        "sat": p.get("status") != "ACTIVE" or bool(p.get("notPlayingReason")),
                        **{c: p.get("statistics", {}).get(f, 0) for c, f in LIVE_STAT.items()}}
            for t in (game["homeTeam"], game["awayTeam"]) for p in t.get("players", ())}


def scores(force=False):
    """Live games (M18): {"games": {game id: {date (ET, as the schedule's), tip (UTC), status, teams [away, home],
    players: _box()}}}: today's from nba_api's live scoreboard, each under way or final with its box score (a final one
    fetched once), and the last two days' kept from the last copy, so last night's late games count until the box scores
    (gamelogs()) have them. Cached in data/live.json: refetched every LIVE_POLL minutes while a game is on (live_on()),
    else every half hour, now with `force`. Fails soft: no scoreboard keeps the old copy (FAILED), no box score that
    game's last one."""
    cache = DATA / "live.json"
    old = json.loads(cache.read_text(encoding="utf-8")).get("games", {}) if cache.exists() else {}

    def get():
        from nba_api.live.nba.endpoints import boxscore, scoreboard
        keep = (date.today() - timedelta(2)).isoformat()
        games = {i: g for i, g in old.items() if g["date"] >= keep}
        for g in scoreboard.ScoreBoard(timeout=30).get_dict()["scoreboard"]["games"]:
            games[g["gameId"]] = {**games.get(g["gameId"], {}), "date": g["gameEt"][:10], "tip": g["gameTimeUTC"],
                                  "status": g["gameStatus"], "teams": [g["awayTeam"]["teamTricode"], g["homeTeam"]["teamTricode"]]}
        for i, g in games.items():
            if g["status"] > 1 and g.get("box") != 3:  # under way, or final without its final box score yet
                try:
                    b = boxscore.BoxScore(i, timeout=30).get_dict()["game"]
                    g.update(status=b["gameStatus"], box=b["gameStatus"], players=_box(b))
                except Exception as e:  # the box only exists from tip-off; keep the last one
                    print(f"Couldn't fetch game {i}'s box score ({e}).", file=sys.stderr)
        return {"games": games}
    return _cached(cache, LIVE_POLL / 60 if live_on(old) else 0.5, get, force)


def freshness(now=None, data=None, failed=None, times=None):
    """When each cache load() reads was last fetched (SOURCES): [{key, label, fetched (UTC ISO, "" when missing),
    hours (its refresh window), age (hours), status, error, lost}]. status: "ok" inside the window; "stale" past it
    (refetched on the next load, i.e. Refresh); "failed" = the last refresh failed, so this is the old copy; "missing" =
    no copy at all, so the model runs without it (`lost`). `times` = {file name: mtime or None} in place of the files'
    own (a League's copies as loaded). Never fetches."""
    now, data, failed = now or time.time(), Path(data or DATA), FAILED if failed is None else failed
    out = []
    for key, label, name, hours, lost in SOURCES:
        t = times[name] if times and name in times else mtime(data / name)
        age = None if t is None else (now - t) / 3600
        status = "missing" if t is None else "failed" if name in failed else "stale" if age > hours else "ok"
        out.append({"key": key, "label": label, "hours": hours, "status": status, "error": failed.get(name, ""),
                    "fetched": "" if t is None else datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "age": None if age is None else round(age, 1), "lost": lost})
    return out


def mtime(f):
    return f.stat().st_mtime if f.exists() else None


def keyed(df):
    return df.assign(key=df.PLAYER_NAME.map(norm)).drop_duplicates("key").set_index("key")


def baseline(last, older):
    """Pre-season per-game line from the last two seasons: GP-weighted, a game two seasons ago counting OLDER_W of one
    last season, pulled toward a fringe-rotation line by REGRESS games (so a 5-game season doesn't count as gospel, and
    a player who missed all of last season isn't treated as a rookie). GP = games in his better season (of 82: fetch()
    scales SHORT seasons), for availability(). Backtested on 2025-26 (backtest.py): slightly better weekly odds than
    last season alone."""
    cols = ["MIN"] + RAW
    keys = last.index.union(older.index)
    fringe = last.nlargest(200, "MIN").tail(60)[cols].mean().to_numpy()
    w1, w2 = last.GP.reindex(keys).fillna(0), OLDER_W * older.GP.reindex(keys).fillna(0)
    x1, x2 = last[cols].reindex(keys).fillna(0), older[cols].reindex(keys).fillna(0)
    line = (x1.mul(w1, axis=0) + x2.mul(w2, axis=0) + REGRESS * fringe).div(w1 + w2 + REGRESS, axis=0)
    pull = REGRESS / (w1 + w2 + REGRESS)  # the fringe line's share of his line, and its minutes (prior()'s bench, M26)
    return line.assign(PLAYER_NAME=last.PLAYER_NAME.combine_first(older.PLAYER_NAME).reindex(keys),
                       TEAM_ABBREVIATION=last.TEAM_ABBREVIATION.combine_first(older.TEAM_ABBREVIATION).reindex(keys),
                       GP=pd.concat([last.GP, older.GP], axis=1).max(axis=1).reindex(keys), PULL=pull,
                       PULL_MIN=pull * fringe[0])


def trend_cut(games):
    """How far form must run from rest-of-season value for a trend arrow / Hot & cold (M8): TREND at TREND_GP games in
    the window, scaled by sqrt(TREND_GP / games) (Series work)."""
    return TREND * np.sqrt(TREND_GP / games)


def fresh_plays(rate, j):
    """This week (M27): the chance a player who played his team's last game plays his j-th game from today (1 = his
    next), at availability `rate`: each game he's newly ruled out at HAZARD x (1 - rate), ONE_GAME of those for that game
    only, the rest for the rest of the week (M17's late news), so (1 - h)(1 - h(1 - ONE_GAME))^(j - 1). Arrays work."""
    h = HAZARD * (1 - rate)
    return (1 - h) * (1 - h * (1 - ONE_GAME)) ** (j - 1)


def availability(best, played, games):
    """Share of the games left a player suits up for, before injury news: games in his better past season (`best`, of
    82; NaN for a rookie), this season's `played` of the `games` his team has played so far (AVAIL_PAST and friends).
    A rookie: this season pulled toward 85%."""
    a = (AVAIL_PAST * best + played + AVAIL_K * AVAIL_T) / (AVAIL_PAST * 82 + games + AVAIL_K)
    return a.where(best.notna(), (played + AVAIL_K * 0.85) / (games + AVAIL_K))


def experts(yahoo, espn, base, keys=(), cur=None):
    """One expert per-game line per player, with its source: Yahoo's (the snapshot's, updated in-season) where it has
    him, else ESPN's. A Yahoo line under half of ESPN's (points + rebounds + assists) isn't a per-game line (Oct 30:
    Jaxson Hayes 1.7 points while playing 27 minutes a game, Capela 0.4; games missed look baked in), so ESPN's wins.
    With no ESPN line, under half of the baseline's or of this season's (`cur`, 3+ games) is the same junk (Nov 4 audit:
    Kobe Sanders 1.9 points, 11.0 this season; Brandon Williams, Quenton Jackson, Huff, Bamba), and the player goes on
    as one no expert projects (prior()'s BENCH_MIN). Checked on the Oct 26-31 snapshots against the games after each:
    those players' error -2.0% (40 of 70 player-weeks better), everyone else's unchanged. Names are matched to `keys`
    (the stats' players) like League.find, so a spelling variant lands on the player it means; the rest are rookies yet
    to play. Yahoo shows no minutes, so his are ESPN's (else the baseline's) scaled by how much more or less Yahoo has
    him scoring, rebounding and assisting: a role change moves minutes, not rates. A stat a source leaves out is 0:
    ESPN drops zeros (Duren: no 3PM, PTS = 2 FGM + FTM)."""
    def frame(d):  # a row with no numbers at all is no projection, not a zero line (ESPN's 2023-24 file lists 43 of
        d = {n: x for n, x in d.items() if any(x.get(c) for c in RAW)}  # that season's rookies that way, D'Angelo Russell now)
        df = pd.DataFrame([{"PLAYER_NAME": n, **x} for n, x in d.items()], columns=["PLAYER_NAME", "MIN"] + RAW)
        df[RAW] = df[RAW].fillna(0)
        df["key"] = [match(n, keys) or norm(n) for n in df.PLAYER_NAME]
        return df.drop_duplicates("key").set_index("key").astype({c: float for c in ["MIN"] + RAW})
    y, e = frame(yahoo), frame(espn)
    vol = lambda d: d[["PTS", "REB", "AST"]].sum(axis=1)
    seen = vol(cur).where(cur.GP >= 3) if cur is not None else pd.Series(dtype=float)
    own = pd.concat([vol(base).reindex(y.index), seen.reindex(y.index)], axis=1).max(axis=1)  # under half of either
    y = y[~(vol(y) < 0.5 * vol(e).reindex(y.index).fillna(own))]
    ref = e.reindex(y.index).combine_first(base.reindex(y.index))
    y["MIN"] = ref.MIN * vol(y) / vol(ref).clip(lower=1)
    return pd.concat([y.dropna(subset=["MIN"]).assign(src="Yahoo"),  # a rookie only Yahoo projects: no minutes to go on
                      e[~e.index.isin(y.index)].assign(src="ESPN")])


def prior(base, proj, w=PROJ_W):
    """Pre-season line: baseline() blended with the experts' line (experts(), weighing w). A rookie's baseline is the
    fringe-roster line ros() starts unprojected rookies from, and his expert line weighs ROOKIE_W (GP 0 marks him for
    ros()). ros() then leans on this season as games pile up.
    BENCH_MIN (M26): one no expert projects has the fringe line's part of his baseline at BENCH_MIN minutes instead
    (his rates kept)."""
    cols = ["MIN"] + RAW
    fringe = base.nlargest(200, "MIN").tail(60)[cols].mean()
    new = proj.loc[proj.index.difference(base.index), ["PLAYER_NAME"]].assign(GP=0.0, **fringe.to_dict())
    out = pd.concat([base, new]) if len(new) else base.copy()
    if BENCH_MIN is not None and "PULL" in out:
        k = out.index.difference(proj.index)
        mins = (out.loc[k, "MIN"] - out.loc[k, "PULL_MIN"] + out.loc[k, "PULL"] * BENCH_MIN).clip(lower=0.1)
        out.loc[k, RAW] = out.loc[k, RAW].mul(mins / out.loc[k, "MIN"].clip(lower=0.1), axis=0)
        out.loc[k, "MIN"] = mins
    wt = pd.Series(float(w), index=proj.index)
    if ROOKIE_W is not None:
        wt[new.index] = ROOKIE_W
    out.loc[proj.index, cols] = out.loc[proj.index, cols].mul(1 - wt, axis=0) + proj[cols].mul(wt, axis=0)
    return out


def ros(base, cur, k=SHRINK, k_min=MIN_SHRINK):
    """Rest-of-season per-game line = projected minutes x production per minute. Minutes lean on this season fast,
    GP / (GP + k_min), since a new role or team shows up at once and sticks. Production per minute leans on it slowly:
    this season's minutes played / (those + k games of baseline minutes), so a player stuck at 8 minutes a night barely
    moves his rates. Rookies no expert projects (prior()) start from a fringe-roster line, so 3 hot games don't make
    them stars. A rookie (no baseline: prior()'s GP 0, or none at all) has his rates lean on ROOKIE_K games instead of k
    (k if lower: HYPE).
    Replayed on 2025-26 (each Monday's rest-of-season line vs what players did after, stats scaled equally): 8% less
    error than one GP / (GP + 20) blend in weeks 1-6, 9% less for players whose minutes moved 6+, even late in the
    season. Faster minutes for players on a new team gained nothing."""
    rookies = cur.index.difference(base.index)
    new = rookies.union(base.index[base.GP.eq(0)])  # no NBA past: unprojected rookies and prior()'s projected ones
    out = pd.concat([base, cur.loc[rookies]])
    fringe = base.nlargest(200, "MIN").tail(60)[["MIN"] + RAW].mean()
    if BENCH_MIN is not None:  # M26: at the fringe line's rates, bench minutes
        fringe = fringe * BENCH_MIN / fringe.MIN
    out.loc[rookies, ["MIN"] + RAW] = fringe.to_numpy()
    both = out.index.intersection(cur.index)
    gp, cm, bm = cur.loc[both, "GP"], cur.loc[both, "MIN"], out.loc[both, "MIN"]
    mins = (gp * cm + k_min * bm) / (gp + k_min)
    kr = np.where(both.isin(new), min(k, ROOKIE_K if ROOKIE_K is not None else k), k)
    w = (gp * cm / (gp * cm + kr * bm)).to_numpy()[:, None]
    rate = (w * cur.loc[both, RAW].div(cm.clip(lower=1), axis=0).to_numpy()
            + (1 - w) * out.loc[both, RAW].div(bm, axis=0).to_numpy())
    out.loc[both, RAW] = rate * mins.to_numpy()[:, None]
    out.loc[both, "MIN"] = mins
    out["GP_CUR"] = cur.GP.reindex(out.index).fillna(0)
    return out


def lifts(line, team, sets, adj=None):
    """Per-game change to teammates' lines (MIN + RAW) while each set of players sits together (one set per stretch of
    days or per game; players out in a set can't take minutes). A team's regulars in a set (REGULAR+ minutes) hand
    SHARE of their minutes, most minutes first, to its rotation (10+) minus everyone in the set, by room under ROOM
    (less what the set's earlier sitters already gave) x e^(ROLE x role similarity: cosine of per-minute AST, REB,
    BLK and 3PM, standardized over the rotation), nobody past ROOM (what nobody has room for goes to the end of the
    bench). adj (onoff()): each team's own split moves those minutes, still within ROOM and never below zero minutes.
    They play them at their own rates, and take USAGE of the shots,
    points, assists and turnovers those minutes don't replace, by output. line: per-game lines of everyone who can
    take minutes (this season's players) and of the regulars; team: {player: NBA team}. Rows (player, by, set: its
    position in `sets`)."""
    adj = {} if adj is None or not len(adj) else {j: s.droplevel("by") for j, s in adj.groupby(level="by")}
    cols = ["MIN"] + RAW
    rot = line.loc[line.MIN >= 10, cols].astype(float)
    pm = rot[["AST", "REB", "BLK", "FG3M"]].div(rot.MIN, axis=0)
    prof = ((pm - pm.mean()) / pm.std()).to_numpy()
    at, tm = {k: i for i, k in enumerate(rot.index)}, pd.Series(team).reindex(rot.index).to_numpy()
    mins_all, rate_all = rot.MIN.to_numpy(), rot[RAW].div(rot.MIN, axis=0).to_numpy()
    use = [RAW.index(c) for c in USAGE_STATS]
    done, out = {}, []
    for s, members in enumerate(sets):
        for t in {team.get(j) for j in members}:
            sit = frozenset(j for j in members if team.get(j) == t)
            if (t, sit) not in done:  # the same players out on the same team: the same lifts (had()'s games repeat)
                done[t, sit] = rows = []
                mates = (tm == t) & ~rot.index.isin(list(sit))
                given = np.zeros(mates.sum())
                if not mates.any():
                    continue
                for j in sorted((j for j in sit if j in at and mins_all[at[j]] >= REGULAR), key=lambda j: (-mins_all[at[j]], j)):
                    p, q = prof[mates], prof[at[j]]
                    sim = p @ q / np.sqrt((p * p).sum(1) * (q @ q) + 1e-9)
                    room = np.clip(ROOM - mins_all[mates] - given, 0, None)
                    w = room * np.exp(ROLE * sim)
                    share = SHARE if mins_all[at[j]] >= FULL else SHARE_LOW
                    m, todo, on = np.zeros(len(w)), share * mins_all[at[j]], w > 0  # no room at all: usage only
                    while todo > 1e-9 and on.any():  # split by weight; whoever reaches ROOM is full, the rest re-split
                        x = todo * np.where(on, w, 0) / w[on].sum()
                        full = on & (m + x > room)
                        if not full.any():
                            m += x
                            break
                        todo -= (room - m)[full].sum()
                        m[full], on = room[full], on & ~full
                    if j in adj:
                        m = np.clip(m + adj[j].reindex(rot.index[mates]).fillna(0).to_numpy(), -mins_all[mates], room)
                    if CAP_GIVE and (tot := m.clip(min=0).sum()) > mins_all[at[j]]:  # never more than he plays
                        m = np.where(m > 0, m * mins_all[at[j]] / tot, m)
                    given += m
                    rate = rate_all[mates]
                    d = rate * m[:, None]  # his minutes at their own rates
                    used = rate[:, use] * (mins_all[mates] + given)[:, None]  # and what those don't replace, by output
                    left = rot.loc[j, USAGE_STATS].to_numpy() - d[:, use].sum(0)
                    d[:, use] += USAGE * used / used.sum(0).clip(min=1e-9) * left
                    rows.append(pd.DataFrame(np.column_stack([m, d]), index=rot.index[mates], columns=cols).assign(by=j))
            out += [r.assign(set=s) for r in done[t, sit]]
    if not out:
        return pd.DataFrame(columns=cols, index=pd.MultiIndex.from_tuples([], names=["key", "by", "set"]), dtype=float)
    return pd.concat(out).set_index(["by", "set"], append=True).rename_axis(["key", "by", "set"])[cols]


def stretches(out, team, start):
    """[(from, until, players out)]: each team's stretches between its injured players' returns. out: {player: the
    day he plays again}; a stretch runs from its first day up to (not including) `until`, with everyone not back yet."""
    spans = []
    for t in {team.get(k) for k in out}:
        mine = {k: r for k, r in out.items() if team.get(k) == t}
        ends = sorted(set(mine.values()))
        spans += [(a, b, frozenset(k for k, r in mine.items() if r > a)) for a, b in zip([start] + ends[:-1], ends)]
    return spans


def priced_in(lift, played, gp, expert, rookies=()):
    """lifts() less what an expert projection already counts: a sitter out since before the season (no games in it)
    was in the preseason line, which is PROJ_W of a player's prior (ROOKIE_W of a rookie's: `rookies`, his expert line
    and no baseline), and the prior holds MIN_SHRINK / (GP + MIN_SHRINK) of his minutes (ros()). played: players with
    games this season; gp: {player: games this season}; expert: players with an expert line. Without it, teammates of
    preseason absentees ran 0.15-0.3 min a game over at SHARE 0.65-0.8 (2025-26), the rest under. ponytail: a
    projection updated after a mid-season injury isn't spotted (Yahoo's may be)."""
    k, j = lift.index.get_level_values("key"), lift.index.get_level_values("by")
    w = np.where(k.isin(list(rookies)) & (ROOKIE_W is not None), ROOKIE_W or 0, PROJ_W)
    keep = 1 - w * MIN_SHRINK / (pd.Series(gp).reindex(k).fillna(0).to_numpy() + MIN_SHRINK) * k.isin(list(expert))
    return lift.mul(np.where(j.isin(list(played)), 1.0, keep), axis=0)


def recent(logs, today, hl=RECENT):
    """This season's per-game line from box scores (MIN + RAW; GP = games played), each game weighted 0.5^(days before
    `today` / hl), so later games say more about the rest of the season (hl None: every game alike)."""
    cols, wt = ["MIN"] + RAW, age_wt(logs.GAME_DATE, today, hl)
    s = logs[cols].mul(wt, axis=0).assign(key=logs.key, wt=wt).groupby("key").sum()
    return s[cols].div(s.wt, axis=0).assign(GP=logs.groupby("key").size())


def age_wt(dates, today, hl=RECENT):
    """recent()'s weight for games on these dates: 0.5^(days before today / hl); 1 with hl None."""
    return 0.5 ** ((pd.Timestamp(today) - dates).dt.days / hl) if hl else pd.Series(1.0, index=dates.index)


def sat(logs, team, regs):
    """The games each of the regulars `regs` sat out: rows (TEAM_ABBREVIATION, GAME_ID, by), one of his team's games
    from his first for it, or all of them if he's yet to play."""
    T = "TEAM_ABBREVIATION"
    games, out = logs.drop_duplicates([T, "GAME_ID"]), []
    for j in regs:
        mine = logs[(logs.key == j) & (logs[T] == team.get(j))]
        tg = games[(games[T] == team.get(j)) & (games.GAME_DATE >= mine.GAME_DATE.min() if len(mine) else True)]
        out.append(tg.loc[~tg.GAME_ID.isin(mine.GAME_ID), [T, "GAME_ID"]].assign(by=j))
    return pd.concat(out) if out else pd.DataFrame(columns=[T, "GAME_ID", "by"])


def onoff(logs, team, line, regs, today=None):
    """Each team's own split of a sitter's minutes (M13): {(receiver, sitter): minutes to add to lifts()'s}. Per
    receiver in the rotation (lifts()'s 10+), his minutes in his games for his team so far, weighted as recent() weighs
    them from `today`, on which of its regulars sat each one (sat()), each sitter's effect pulled toward what lifts()
    gives the receiver while that sitter sits alone, as if that guess were ONOFF_K more games (a ridge on it, his minutes with
    everyone in free). Several out at once are split apart where the games allow, else stay at the rule's guess.
    line, regs: as had()'s."""
    T = "TEAM_ABBREVIATION"
    s, none = sat(logs, team, regs), pd.Series(dtype=float, index=pd.MultiIndex.from_tuples([], names=["key", "by"]))
    if not len(s):
        return none
    prior =lifts(line, team, [frozenset([j]) for j in s.by.unique()]).MIN.droplevel("set")
    rot, out = set(line.index[line.MIN >= 10]), {}
    for t, st in s.groupby(T):
        by = list(st.by.unique())
        g = logs[(logs[T] == t) & logs.key.isin(rot) & (logs.key.map(team) == t)]
        x = pd.crosstab(st.GAME_ID, st.by).reindex(index=g.GAME_ID, columns=by, fill_value=0).clip(upper=1).to_numpy(float)
        w = (age_wt(g.GAME_DATE, today) if today else pd.Series(1.0, index=g.index)).to_numpy()
        for i, rows in g.groupby("key").indices.items():
            xi, wi, yi = x[rows], w[rows], g.MIN.to_numpy(float)[rows]
            cols = [c for c, j in enumerate(by) if j != i and xi[:, c].any()]
            if not cols:
                continue
            xi = np.column_stack([np.ones(len(rows)), xi[:, cols]])
            p = np.array([prior.get((i, by[c]), 0.0) for c in cols])
            pen = np.diag([0.0] + [ONOFF_K] * len(cols))
            beta = np.linalg.solve(xi.T @ (wi[:, None] * xi) + pen, xi.T @ (wi * yi) + pen @ np.r_[0.0, p])[1:]
            out.update({(i, by[c]): float(b - q) for c, b, q in zip(cols, beta, p)})
    return pd.Series(out, dtype=float).rename_axis(["key", "by"]) if out else none


def had(logs, team, line, regs, today=None, adj=None):
    """The lift (lifts(), with each team's own split `adj`: onoff()) each player had per game in these box scores:
    every game some of the regulars `regs` sat out (sat()) adds what those players out together handed him, averaged
    over the player's games (each weighted as recent() weighs it, from `today`; None: alike). ros() reads this season
    net of it, as if everyone played. line: as lifts()'."""
    T, cols = "TEAM_ABBREVIATION", ["MIN"] + RAW
    s = sat(logs, team, regs)
    if not len(s):
        return pd.DataFrame(columns=cols, dtype=float)
    out = s.groupby([T, "GAME_ID"]).by.agg(frozenset).rename("sit").reset_index()
    sets = list(out.sit.unique())
    lift = lifts(line, team, sets, adj).groupby(level=["key", "set"]).sum()  # each player's lift per set, all sitters
    out["set"] = out.sit.map({s: i for i, s in enumerate(sets)})
    wt = logs[[T, "GAME_ID", "key"]].assign(wt=age_wt(logs.GAME_DATE, today) if today else 1.0)
    pg = wt.merge(out[[T, "GAME_ID", "set"]], on=[T, "GAME_ID"])  # games played while some sat
    pg = pg[pd.MultiIndex.from_frame(pg[["key", "set"]]).isin(lift.index)]
    n = pg.groupby(["key", "set"]).wt.sum()
    return lift.loc[n.index].mul(n, axis=0).groupby(level="key").sum().div(wt.groupby("key").wt.sum(), axis=0).dropna()


def zscores(df, pool_size, ref=None):
    """z per category vs the top `pool_size` players by minutes (of `ref`, default df itself). FG%/FT% are
    volume-weighted, TOV inverted."""
    pool = (df if ref is None else ref).nlargest(pool_size, "MIN")
    z = pd.DataFrame(index=df.index)
    for pct, m, a in [("FG%", "FGM", "FGA"), ("FT%", "FTM", "FTA")]:
        rate = pool[m].sum() / pool[a].sum()
        val, pv = df[m] - rate * df[a], pool[m] - rate * pool[a]
        z[pct] = (val - pv.mean()) / pv.std()
    for c in COUNTING:
        z[c] = (df[c] - pool[c].mean()) / pool[c].std()
    z["TOV"] *= -1
    return z[CATS]


def phi(x):
    """Standard normal CDF (Abramowitz-Stegun erf, error < 2e-7), vectorized without scipy."""
    z = np.abs(x) / np.sqrt(2)
    t = 1 / (1 + 0.3275911 * z)
    y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-z * z)
    return 0.5 * (1 + np.sign(x) * y)


def cat_probs(A, B, cats, A0=None, B0=None, ties=False):
    """P(team A beats team B) in each category. A, B: (..., len(RAW)) weekly totals -> (..., len(cats)).
    A0, B0: totals already banked this week. They move the final totals but add no variance.
    ties: also return P(tie) per category, the odds the counting stat's difference lands on 0 (a win is p - tie / 2).
    Percentages never tie. Matches 2025-26 (backtest.py): STL 3.9% predicted vs 3.4% real, BLK 4.1% vs 4.3%."""
    TA, TB = (A, B) if A0 is None else (A + A0, B + B0)
    out, tie = [], []
    for c in cats:
        if c in ("FG%", "FT%"):
            m, a = RAW.index(c[:2] + "M"), RAW.index(c[:2] + "A")
            na, nb = np.maximum(TA[..., a], 1e-9), np.maximum(TB[..., a], 1e-9)  # no shots at all: 0%, not NaN
            pa, pb = np.clip(TA[..., m] / na, 0, 1), np.clip(TB[..., m] / nb, 0, 1)  # IL rows can net makes > tries
            var = PCT_NOISE * (pa * (1 - pa) * A[..., a] / na ** 2 + pb * (1 - pb) * B[..., a] / nb ** 2)
            diff = pa - pb
            tie.append(np.zeros_like(diff))
        else:
            i, d = RAW.index(c), DISPERSION.get(c, 1.0)
            ta, tb = A[..., i], B[..., i]
            var = d * (ta + tb) + (SCHED_CV * ta) ** 2 + (SCHED_CV * tb) ** 2
            diff = TB[..., i] - TA[..., i] if c == "TOV" else TA[..., i] - TB[..., i]
            sd = np.sqrt(var + 1e-12)
            tie.append(phi((diff + 0.5) / sd) - phi((diff - 0.5) / sd))
        out.append(phi(diff / np.sqrt(var + 1e-12)))  # no games left: a banked lead is a sure win
    return (np.stack(out, -1), np.stack(tie, -1)) if ties else np.stack(out, -1)


def lineup(slots, pos):
    """{slot index: player index} for the day's starters, given the starting slots (["PG", "G", "Util", ...]) and each
    player's positions (sets, best player first; an empty set, position unknown, fits anywhere). Each player starts if
    the ones kept so far can still be fitted around him (an augmenting path moves them between slots), which gives the
    best lineup: the sets of players that fit together form a matroid, so greedy by value is optimal."""
    held = {}

    def place(j, seen):
        for i, s in enumerate(slots):
            if i not in seen and (not pos[j] or pos[j] & SLOT_POS.get(s, {s})):
                seen.add(i)
                if i not in held or place(held[i], seen):
                    held[i] = j
                    return True
        return False

    for j in range(len(pos)):
        if len(held) == len(slots):
            break
        place(j, set())
    return held


def total(v, c):
    """A weekly total as shown: FG%/FT% in percent (None before any attempts), counting stats to one decimal."""
    if c in ("FG%", "FT%"):
        a = v[RAW.index(c[:2] + "A")]
        return round(100 * float(v[RAW.index(c[:2] + "M")] / a), 1) if a else None
    return round(float(v[RAW.index(c)]), 1)


def phinv(p):
    """Inverse of phi() (Acklam's rational approximation, error < 1.2e-9), vectorized without scipy; p is clipped to
    1e-12 from 0 and 1 (a punted category's 0 stays a sure loss)."""
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
    p = np.clip(p, 1e-12, 1 - 1e-12)
    q = np.minimum(p, 1 - p)  # the lower tail, then the sign
    r = np.sqrt(-2 * np.log(np.where(q < 0.02425, q, 0.5)))
    tail = (((((c[0] * r + c[1]) * r + c[2]) * r + c[3]) * r + c[4]) * r + c[5]) / ((((d[0] * r + d[1]) * r + d[2]) * r + d[3]) * r + 1)
    u = q - 0.5
    t = u * u
    mid = (((((a[0] * t + a[1]) * t + a[2]) * t + a[3]) * t + a[4]) * t + a[5]) * u / (((((b[0] * t + b[1]) * t + b[2]) * t + b[3]) * t + b[4]) * t + 1)
    x = np.where(q < 0.02425, tail, mid)
    return np.where(p < 0.5, x, -x)


def match_prob(p, cats=CATS, load=0.0):
    """P(win the matchup) = P(more category wins than losses); a tie counts half. load 0: categories independent.
    load (M9, WEEK_LOAD: this week on its real schedule): each category's margin loads `load` on one shared factor F
    (counting stats +, TOV -, FG% / FT% 0), independent given it, so category c is won with Phi((phinv(p_c) + l_c F) /
    sqrt(1 - l_c^2)) at F, integrated over GH's nodes. Each category's own odds stay p (the average over F); only how
    they go together changes. A banked lead is in p already, so it moves only what's left of the week."""
    if not load:
        return _match_indep(p)
    lam = np.array([0.0 if c in ("FG%", "FT%") else -load if c == "TOV" else load for c in cats])
    mu, s = phinv(p), np.sqrt(1 - lam ** 2)
    return sum(w * _match_indep(phi((mu + lam * x) / s)) for x, w in zip(*GH))


def _match_indep(p):
    """match_prob() with the categories independent: the distribution of categories won, one category at a time."""
    n = p.shape[-1]
    dist = np.zeros(p.shape[:-1] + (n + 1,))
    dist[..., 0] = 1
    for k in range(n):
        pk = p[..., k:k + 1]
        dist = dist * (1 - pk) + np.concatenate([np.zeros_like(pk), dist[..., :-1]], -1) * pk
    wins = np.arange(n + 1)
    return (dist * (wins > n / 2)).sum(-1) + 0.5 * (dist * (wins == n / 2)).sum(-1)


def season_sim(wins, weeks, bracket, seats, sims=SIMS, seed=0, sd=None):
    """Playoff and title odds by simulating the rest of a categories season. wins: (n,) category wins so far (a tie
    counts half). weeks: [(I, J, p, tie)] for each regular-season week left: both teams of each pairing and their
    category odds and tie odds (pairings, cats), cat_probs(ties=True). bracket: [(p, tie)] for each playoff round,
    (n, n, cats) for every pairing. The top `seats` make it, reseeded each round (best seed plays the worst left, byes
    to the top seeds), and a playoff week goes to whoever wins more categories, the higher seed on a tie.
    sd: (SEASON_SD, WEEK_SD) to try others. -> (P(playoffs), P(title), expected final wins) per team.
    ponytail: equal wins are seeded at random; Yahoo's tiebreak isn't in the snapshot."""
    rng, n = np.random.default_rng(seed), len(wins)
    season_sd, week_sd = sd or (SEASON_SD, WEEK_SD)
    u = rng.normal(0, season_sd, (sims, n))
    logit = lambda p: np.log(p / (1 - p))

    def play(I, J, p, tie):  # I, J: (sims, pairings) -> categories won by I (ties half) in each pairing
        shift = np.take_along_axis(u, I, 1) - np.take_along_axis(u, J, 1) + rng.normal(0, week_sd, I.shape)
        z = logit(np.clip(p, 1e-6, 1 - 1e-6)) + shift[..., None]
        q = 1 / (1 + np.exp(-z))
        t = np.minimum(tie, 2 * np.minimum(q, 1 - q))
        r = rng.random(q.shape)
        return ((r < q - t / 2) + 0.5 * ((r >= q - t / 2) & (r < q + t / 2))).sum(-1)

    total = np.broadcast_to(np.asarray(wins, float), (sims, n)).copy()
    for I, J, p, tie in weeks:
        w = play(np.broadcast_to(I, (sims, len(I))), np.broadcast_to(J, (sims, len(J))), p, tie)
        np.add.at(total, (np.arange(sims)[:, None], I), w)
        np.add.at(total, (np.arange(sims)[:, None], J), p.shape[-1] - w)
    order = np.argsort(-(total + rng.random(total.shape) * 1e-3), 1)  # best first
    seed_of = np.argsort(order, 1)
    alive, rows = order[:, :seats], np.arange(sims)[:, None]
    rounds = int(np.ceil(np.log2(seats))) if seats > 1 else 0
    for r in range(rounds):
        bye = 2 ** rounds - seats if r == 0 else 0
        hi, lo = alive[:, bye:], alive[:, bye:][:, ::-1]
        k = hi.shape[1] // 2
        hi, lo = hi[:, :k], lo[:, :k]
        p, tie = bracket[min(r, len(bracket) - 1)]
        w = play(hi, lo, p[hi, lo], tie[hi, lo])
        alive = np.concatenate([alive[:, :bye], np.where(w >= p.shape[-1] / 2, hi, lo)], 1)
        alive = np.take_along_axis(alive, np.argsort(seed_of[rows, alive], 1), 1)  # reseed
    made = np.zeros((sims, n), bool)
    made[rows, order[:, :seats]] = True
    champ = np.bincount(alive[:, 0], minlength=n) / sims if seats else np.zeros(n)
    return made.mean(0), champ, total.mean(0)


class League:
    """A league snapshot (see yahoo.py) joined with NBA stats."""

    def __init__(self, snap, base=None, cur=None, sched=None, inj=None, today=None, logs=None, proj=None, news=None,
                 live=None, now=None):
        s = snap["settings"]
        self.snap, self.teams_n, self.roster_n = snap, s.get("teams", 12), s.get("roster", 13)
        self.slots = s.get("slots") or []  # starting slots by position (yahoo.py reads them); none = any players start
        self.lineup = len(self.slots) or s.get("starters", 10)  # players whose games count each day
        self._weeks = {}  # _sched_week() cache: (start, end) -> a schedule week's arrays (M2)
        self._lineups = {}  # _lineup() cache: players with a game -> starters; _subs(): (them, chances) -> starts
        self.mode = s.get("mode", "9cat")
        self.scoring = s.get("scoring", "categories")  # Yahoo's H2H default; "one_win" = only the matchup counts
        self.s = 1.0 if self.scoring == "one_win" else CAT_SCALE  # scales the One Win cutoffs
        self.u = 3.0 if self.mode == "points" else 1.0  # FP/game spreads ~3x wider than 9-cat z-sums; scales thresholds
        self.cats = [c for c in s.get("cats", CATS) if c in CATS] or CATS
        self.points = s.get("points") or POINTS
        self.me = snap["me"]
        self.free = {}  # {team: free moves already made on this copy} (after_free)
        self.trades = []  # trades already made on this copy (after_trade)
        base = baseline(fetch(LAST), fetch(OLDER)) if base is None else base
        cur = fetch(CUR, max_age_hours=12) if cur is None else cur
        self.cur, self.logs = cur, logs  # this season's per-game stats and box scores (gamelogs()), for card()
        self.news = news  # news() from load(), for card() and roster_news(); never moves a number
        self.loaded = None  # {cache file: mtime or None} this copy was built from (load()), for freshness()
        pool = self.teams_n * self.roster_n
        self.today = (today or date.today()).isoformat()
        # expert projections: the snapshot's (Yahoo), and ESPN's (proj, from load()) for players Yahoo doesn't list
        self.proj = experts(snap.get("projections", {}), proj or {}, base, base.index.union(cur.index), cur)
        pre = prior(base, self.proj)
        # this season from the box scores, recent games weighing more (recent()), where they hold the games fetch()
        # counts (a stale cache either side: fetch()'s line). Regulars come from it too, as in backtest.py's mondays()
        wcur = cur.copy()
        if logs is not None:
            r = recent(logs, self.today).reindex(cur.index).dropna()
            r = r[r.GP == cur.GP.reindex(r.index)]
            wcur.loc[r.index, ["MIN"] + RAW] = r[["MIN"] + RAW]
        line = ros(pre, wcur)
        cols = ["PLAYER_NAME", "MIN", "GP_CUR"] + RAW
        self.p = line[cols].copy()  # find() works from here; teammates' injuries adjust the numbers below
        self.meta, self.rosters = {}, {}
        for team, players in snap["teams"].items():
            self.rosters[team] = self._keys(players)
        self.fa = self._keys(snap.get("free_agents", []))
        # NBA schedule + injury news: games left this week, and return dates for availability
        self.sched, self.inj = sched or {}, {}
        sunday = (date.fromisoformat(self.today) + timedelta(6 - date.fromisoformat(self.today).weekday())).isoformat()
        nba = cur.TEAM_ABBREVIATION.combine_first(base.TEAM_ABBREVIATION).to_dict()
        nba.update({k: m["nba"] for k, m in self.meta.items() if m.get("nba")})  # the league's view is more current
        self.nba = {k: TRICODE.get(str(t).upper(), str(t).upper()) for k, t in nba.items()}
        for name, i in (inj or {}).items():
            if k := self.find(name, strict=False):
                m = self.meta.setdefault(k, {})
                if REST.search(i.get("note", "")):  # one game off: REST until it's played, then nothing (over
                    i = {**i, "status": "REST" if (i.get("return") or "") > self.today else ""}  # Yahoo's O or GTD too)
                    if m.get("status", "") in ("", "O", "GTD", "DTD"):
                        m["status"] = i["status"]
                self.inj[k] = i
                m["status"] = m.get("status") or i["status"]  # the league's own tag wins
        # game-day listings (M16): {player: (the game's date, how often players so listed play, listing)}, today's and later
        # games only. The note names a weekday: his team's first game on it from the day before ESPN wrote the note
        # (its time is UTC, so an evening note is dated a day late), else from today
        self.gameday = {}
        for k, i in self.inj.items():
            if m := LISTED.search(i.get("note", "")):
                lo = date.fromisoformat((i.get("date") or self.today)[:10]) - timedelta(1)
                want, rate = DAYS.index(m[2].lower()), GAMEDAY.get(m[1].lower(), GAMEDAY["questionable"])
                d = next((d for d in self.sched.get(self.nba.get(k), ()) if lo <= date.fromisoformat(d) <= lo + timedelta(7)
                          and date.fromisoformat(d).weekday() == want), None)
                if d and d >= self.today:
                    self.gameday[k] = (d, rate, m[1].lower() if m[1].lower() in GAMEDAY else "questionable")
        # games under way (M18): live scores from load() (scores(), regular season only). Today's games as [(teams,
        # status)], a game past its tip-off counted under way (Yahoo locks a player at the scheduled tip; nba.com marks
        # it at the real one), and the NBA teams whose game has tipped off: their players' game today is locked, its
        # stats banked (_so_far, _held), no longer one to play (game_days). No schedule, no rest of the week to take it
        # from: nothing locks
        games = {i: g for i, g in (live or {}).get("games", {}).items() if i.startswith("002")}
        now = now or datetime.now(timezone.utc)
        self.live = games
        self.tonight = [(g["teams"], max(g["status"], 2 if datetime.fromisoformat(g["tip"]) <= now else 1))
                        for g in games.values() if g["date"] == self.today] if self.sched else []
        self.tipped = {t for ts, st in self.tonight if st > 1 for t in ts}
        # their box scores: one row per player and game, like logs (played: he got in; sat: inactive or not playing)
        box = pd.DataFrame([{"key": norm(n), "GAME_ID": int(i), "GAME_DATE": pd.Timestamp(g["date"]), **x}
                            for i, g in games.items() for n, x in g.get("players", {}).items()],
                           columns=["key", "GAME_ID", "GAME_DATE", "team", "played", "sat"] + RAW)
        self.box = box = box.astype({"played": bool, "sat": bool})
        now_box = box[box.GAME_DATE == pd.Timestamp(self.today)]
        self.played_today, self.sat_today = set(now_box.key[now_box.played]), set(now_box.key[now_box.sat])
        # this week's opponent and totals so far (Yahoo pull, snapshot, or picked in the app). Once that week is over,
        # the season schedule names the next opponent and its dates (weeks 7 and 17 run 14 days), with no score yet.
        # ponytail: without either, weeks are assumed to run Monday-Sunday
        m = snap.get("matchup") or {}
        self.rolled = bool(m) and (m.get("week_end") or "") < self.today
        if not m or self.rolled:
            wk = next((w for w in snap.get("schedule", ()) if w["start"] <= self.today <= w["end"]), None)
            opp = next((t for pr in wk["matchups"] if self.me in pr for t in pr if t != self.me), None) if wk else None
            m = {"week_start": wk["start"], "week_end": wk["end"], **({"opponent": opp} if opp else {})} if wk else {}
        self.matchup = m
        self.week_end = m.get("week_end") or sunday
        # games under way lock only while this week's score carries them (M18: _so_far): with no totals for you (last
        # week's snapshot) tonight's games count as to play, as before
        if self.rolled or self.me not in {t for pr in snap.get("matchups", ()) for t in pr.get("totals", {})} | set(m.get("totals", {})):
            self.tonight, self.tipped = [], set()
        # teammates' injuries: the lifts players had in this season's box scores while regulars sat (had()) come off,
        # so a teammate's minutes while he sat aren't counted as the new normal. Then the injured go back on: each
        # team's players out with a return date sit together in stretches between their returns (stretches()), each
        # stretch's lifts (lifts()) counted over each teammate's games in it (_boost): rest of season here, this week
        # in p_week
        # in the league now: a game this season or on ESPN's injury list (before any games, everyone). A regular who's
        # neither (Westbrook, Rozier, deep-bench players regressed onto the fringe line) isn't sitting out: counting him
        # took up to 9 minutes a game off his old teammates' lines (Oct 30 audit: Dean Wade 27.3 -> 19.4)
        played = cur.index[cur.GP > 0]
        self.here = line.index[line.index.isin(played) | line.index.isin(list(self.inj)) | (len(played) == 0)]
        regs = line.index[(line.MIN >= REGULAR) & line.index.isin(self.here)]
        takers = line[line.index.isin(cur.index) | line.index.isin(regs)]  # who can take minutes, and the regulars
        # each team's own split of its sitters' minutes (onoff(), M13): Fears 21-24 min -> 29-31 while Murray sat
        self.adj = onoff(logs, self.nba, takers, regs, self.today) if logs is not None and ONOFF_K else None
        if logs is not None:  # net of the lifts they had
            h = had(logs, self.nba, takers, regs, self.today, self.adj).reindex(cur.index).dropna()
            full = wcur.copy()
            full.loc[h.index, h.columns] = (full.loc[h.index, h.columns] - h).clip(lower=0)
            line = ros(pre, full)
        out = {k: i["return"] for k, i in self.inj.items() if (i.get("return") or "") > self.today}
        spans = stretches(out, self.nba, self.today)
        lift = priced_in(lifts(takers, self.nba, [s for _, _, s in spans], self.adj), cur.index[cur.GP > 0], cur.GP,
                         self.proj.index, self.proj.index.difference(base.index))
        self.lift = lift.set_axis(pd.MultiIndex.from_tuples([(k, j, *spans[s][:2]) for k, j, s in lift.index],
                                                            names=["key", "by", "from", "until"]))
        self.ahead = ahead = self._boost("9999")  # each player's lift averaged over the rest of the season
        line.loc[ahead.index, ahead.columns] += ahead
        self.p = line[cols].copy()
        self.z = zscores(line, pool)[self.cats]                      # ROS value
        # "cur" = perceived value: reputation plus a heavily weighted hot/cold start (roughly what Yahoo ranks show)
        seen = ros(pre, cur, HYPE)
        self.zcur = zscores(seen, pool)[self.cats]
        self.fp = sum(line[c] * w for c, w in self.points.items() if c in line)
        self.fp_cur = sum(seen[c] * w for c, w in self.points.items() if c in seen)
        # recent form: per-game lines over the last FORM_DAYS (this season's without box scores), scored against the
        # rest-of-season pool so form - value is a real gap
        if logs is not None:
            g = logs[logs.GAME_DATE >= pd.Timestamp(self.today) - pd.Timedelta(days=FORM_DAYS)]
            form = g.groupby("key")[["MIN"] + RAW].mean().assign(GP=g.groupby("key").size())
        else:
            form = cur[["MIN", "GP"] + RAW]
        form = form[form.GP >= FORM_GP]
        self.form_gp, self.zform = form.GP, zscores(form, pool, line)[self.cats]
        self.fp_form = sum(form[c] * w for c, w in self.points.items() if c in form)
        self.p_week = self.p.copy()  # this week's per-game lines: the lifts for this week's games instead
        wk = self._boost(self.week_end).sub(ahead, fill_value=0)
        self.p_week.loc[wk.index, wk.columns] += wk
        self.rate, self.avail, self.back, self.rate_week, self.fresh = self._availability(base, cur)
        self.games_week = pd.Series({k: len(self.game_days(k, self.week_end)) for k in self.p.index}, dtype=float)
        # expected weekly contribution: games played at his line, missed games partly covered at waiver level
        fill = self.p.loc[self._waiver_level(self.value()), RAW].mean()
        self._fill_line = fill.to_numpy()  # a missed game's half-covered waiver line (_sched_week(), M2)
        # what a missed game is worth in value units: the half-covered waiver line (zero point for effective())
        f = pd.DataFrame([FILL * fill], index=["_fill"]).assign(MIN=0.0)
        self.zfill = {"ros": zscores(pd.concat([line, f]), pool).loc["_fill", self.cats],
                      "cur": zscores(pd.concat([seen, f]), pool).loc["_fill", self.cats]}
        a = self.avail.to_numpy()[:, None]
        self.week = GAMES_WEEK * (a * self.p[RAW] + (1 - a) * FILL * fill.to_numpy())
        # parked in an IL slot (IL, IL+, NA): he holds no active spot, so nobody covers his missed games, and from his
        # return (the share `back` of games left) he takes an open active spot, else his team's weakest player's (_return())
        self.il = {t: {k for k in ks if self.meta[k].get("slot") in ("IL", "IL+", "NA")} for t, ks in self.rosters.items()}
        self.il_n = s.get("il", max(map(len, self.il.values()), default=0))  # IL slots per team (yahoo.py reads them)
        # the lineups the snapshot shows (B8): each team's slots on the day it was pulled (its `pulled` date; Yahoo
        # shows today's). Yahoo scores starting slots only and has no auto-subs, so that day counts these, not a guess
        # (_counted). From the next day they're stale. A snapshot with no slots read (a hand-typed list, the demo): none
        self.slot_day = (snap.get("pulled") or "")[:10] or None
        self.slotted = {t: {k: self.meta[k]["slot"] for k in ks if self.meta[k].get("slot")} for t, ks in self.rosters.items()}
        if not any(self.slotted.values()):
            self.slot_day = None
        w, r = self._waiver_level(self.value()), self.rate.to_numpy()[:, None]
        # an open active spot on another team holds the waiver level's week until a player back from its IL slots takes
        # it (vec(), _open()): its manager can stream it, and has to drop someone only once more come back than it has
        # spots. Nov 2: Shanghai dropped two IL players and parked Murray, Sarr and Johnson (all back Nov 4); with its 2
        # open spots counted empty all season and the three pushing out its weakest, it fell from 55.9% to 45.1%
        self.level = self.week.reindex(w).mean().to_numpy()
        self.level_keys = list(w)  # who'd stream it this week (_week_side, M20)
        healthy = GAMES_WEEK * (r * self.p[RAW] + (1 - r) * FILL * fill.to_numpy())  # his week once he's back
        self.week_il = healthy.mul(self.back, axis=0)  # from his return, before the player he pushes out
        floor = float(self.zfill["ros"].sum())
        # _return()'s arrays, one row per player (_ix) plus nobody at the end: value as he is (effective) and back
        # healthy, share of games after his return, week and week from his return
        self._ix = dict(zip(self.p.index, range(len(self.p))))
        hv = (floor + self.rate * (self.value() - floor)).where(self.back > 0, -np.inf)  # out for good: never back
        # then two open active spots (_open()): the weakest, so a player back from an IL slot takes one before he pushes
        # anyone out; another team's holds a waiver-level week until then, yours nothing
        self._ev = np.r_[self.effective().to_numpy(), np.inf, -np.inf, -np.inf]
        self._hv = np.r_[hv.to_numpy(), -np.inf, -np.inf, -np.inf]
        self._bk = np.r_[self.back.to_numpy(), 0.0, 0.0, 0.0]
        self._wk = np.vstack([self.week.to_numpy(), np.zeros(len(RAW)), self.level, np.zeros(len(RAW))])
        self._cr = np.vstack([self.week_il.to_numpy(), np.zeros((3, len(RAW)))])
        # an open roster spot on the trade market: the waiver level's market value, fixed now so a player dropped on a
        # copy (free moves) never prices a pickup at his own rank
        self.spot = float(self.market["value"].reindex(w).mean())

    def _keys(self, players):
        keys = []
        for pl in players:
            k = self.find(pl["name"], strict=False)
            if k:
                self.meta[k] = dict(pl)  # a copy: ESPN's injury tags land here, never in the snapshot it came from
                keys.append(k)
        return keys

    def game_days(self, k, end="9999", start=None):
        """Dates k's NBA team plays from today (or `start`, his return date, or the first day the matchup totals don't
        cover yet, if later) through `end`. A game today that has tipped off isn't one left to play (M18: _held)."""
        start = max(start or self.today, self.today, self.matchup.get("from", ""),
                    self.inj.get(k, {}).get("return") or "")
        return [d for d in self.sched.get(self.nba.get(k), ()) if start <= d <= end and not (d == self.today and self._locked(k))]

    def _locked(self, k):
        """His team's game today has tipped off (M18): Yahoo locks him for the day (no lineup change, no drop)."""
        return self.nba.get(k) in self.tipped

    def _held(self, keys, start=None):
        """Today's starters in games that have tipped off among keys (M18, _tipoff), best first: the slots they hold are
        set, so today's other games fill around them (_days), and their games count in the score so far (_so_far), not
        as games left (starts()). None for a later week (`start` a date after today)."""
        if isinstance(start, str) and start > self.today:
            return []
        return [k for k in sorted(keys, key=self._value.get, reverse=True) if k in self._tipoff]

    @functools.cached_property
    def _tipoff(self):
        """Every team's starters today whose games have tipped off (M18): each team's lineup over its active players
        with a game today (_counted: the rule _so_far() uses for a finished day; the snapshot's lineup when it was
        pulled today, B8, else the best per-game values), as it stood at tip-off, so before any move this week's plans
        make (all later), the ones whose game has tipped. A player with a game today counts unless he's out with a
        return date after today or sat it (the live box score's inactive / not playing; one who got in always counts).
        Empty once the snapshot's totals hold today (`from`).
        ponytail: on a day after the snapshot's, the lineup the manager really set may have differed"""
        if not self.tipped or self.matchup.get("from", "") > self.today:
            return set()
        plays = lambda k: self.today in self.sched.get(self.nba.get(k), ()) and (k in self.played_today or k not in self.sat_today
                                                                               and (self.inj.get(k, {}).get("return") or "") <= self.today)
        return {k for t, ks in self.rosters.items()
                for k in self._counted(t, filter(plays, self._playing(ks)), self.today) if self._locked(k)}

    def _counted(self, team, keys, d):
        """Whose games count for `team` on day d among keys (its active players with a game that day who play: the
        box score's, or today's expected): Yahoo scores starting slots only, no auto-subs. On the snapshot's own day
        (slot_day, B8) the lineup it shows: its starters, and a starting slot empty at the pull taken by the best bench
        player who plays and fits it (a manager fills one: 3 of 3 in the Oct 27-Nov 4 replay). Any other day, or a
        team with no slots read: the best per-game values that fit the slots (_lineup). Best first. The same rule for
        today's games under way (_tipoff) and for a finished day (_so_far), so the score doesn't jump at midnight.
        `python backtest.py lineups` (Oct 27-30, Nov 4: each pre-tip snapshot's night against the next snapshot's
        totals): |error| over teams and stats 1,071 with the guess, 660 with the starters, 576 with the empty slot.
        ponytail: a starter swapped out or a player added after the pull isn't seen (the rest of that error)"""
        keys = sorted(keys, key=self._value.get, reverse=True)
        slot = self.slotted.get(team) if d == self.slot_day else None
        if not slot:
            return self._lineup(keys)
        starters = [k for k, s in slot.items() if s not in BENCH and k in self.rosters.get(team, ())]
        held, bench = set(starters), [k for k in keys if slot.get(k) == "BN"]
        empty = [k for k in self._lineup(starters + bench) if k not in held][:max(self.lineup - len(starters), 0)]
        return [k for k in keys if k in held] + empty

    def _move_day(self, drops):
        """The first day a move dropping these can be made: tomorrow once one's game today has tipped off (Yahoo locks
        him for the day, M18), else today."""
        return (date.fromisoformat(self.today) + timedelta(1)).isoformat() if any(map(self._locked, drops)) else self.today

    def starts(self, keys, start=None, until=None, by=None, plays=False):
        """{player: games he starts the rest of this week}. Yahoo only scores starters, so each day the best `lineup`
        players with a game count and bench games fill in on light days. start: {player: first day}, for claims and
        timed adds; until: {player: last day}, for a drop made later in the week (Q11); by: the ranking (_days).
        plays: his expected starts instead: each game at his chance to play it (_plays()), a bench player filling in
        for a starter who sits (M19, _subs). Today's starters in games under way hold their slots and count nothing:
        their stats are in the score so far (M18, _held).
        ponytail: best = per-game value, each in a slot his positions fit (_lineup); a lineup set by hand can count fewer."""
        day, n = self._days(keys, self.week_end, start or {}, until, by), collections.Counter()
        for d, ks in day.items():
            if plays:  # a locked player here is one of today's held starters (game_days() drops the rest): his slot is set
                w = self._subs(ks, [1.0 if d == self.today and self._locked(k) else self._plays(k, d) for k in ks])
            else:
                on = set(self._lineup(ks))
                w = [k in on for k in ks]
            for k, x in zip(ks, w):
                if x and (d != self.today or not self._locked(k)):
                    n[k] += x
        return n

    def _subs(self, ks, p):
        """A day's expected starts (M19): ks = the players with a game in the order the lineup is filled (best first),
        p = each one's chance to play. Whoever plays starts, best first, while the slots fit him (_lineup), so a bench
        player fills in for a starter who sits. One on everyone's lineup starts whenever he plays (a sitter only frees
        slots); a bench player when he plays and the players above him who do leave him a slot: decided one at a time
        (plays / sits) until the rest can't change it, so it's exact and stops early. Only positions matter to who fits
        (_fit_n)."""
        key = (tuple(ks), tuple(p), self.lineup)
        if (w := self._lineups.get(key)) is None:
            on, w, pos = set(self._lineup(ks)), list(p), [",".join(sorted(self._pos(k))) for k in ks]
            fits = lambda xs, b: self._fit_n(tuple(sorted((*xs, b)))) > self._fit_n(tuple(sorted(xs)))

            @functools.cache
            def room(j, i, up):  # P(player j gets a slot | the ones above him before i: up (positions) play)
                if fits(up + tuple(pos[i:j]), pos[j]):  # a slot even if all the rest play
                    return 1.0
                if not fits(up, pos[j]):  # none even if they all sit
                    return 0.0
                return p[i] * room(j, i + 1, tuple(sorted((*up, pos[i])))) + (1 - p[i]) * room(j, i + 1, up)
            for j, b in enumerate(ks):
                if b not in on and p[j]:
                    w[j] = p[j] * room(j, 0, ())
            self._lineups[key] = w
        return w

    def _fit_n(self, pos):
        """How many of these players (sorted position strings) can start together: lineup() on the slots, else up to
        `lineup`."""
        if not self.slots:
            return min(len(pos), self.lineup)
        if (n := self._lineups.get(("rank", pos))) is None:
            n = self._lineups["rank", pos] = len(lineup(self.slots, [set(filter(None, x.split(","))) for x in pos]))
        return n

    @functools.cached_property
    def _rw(self):  # rate_week as a dict: starts() reads it for every game
        return self.rate_week.to_dict()

    def _plays(self, k, d, rate=None):
        """His chance to play the game on day d: a game-day listing's rate for that game (self.gameday, M16), else
        `rate` (a later week: the season's), else this week's: one who played his team's last game at fresh_plays() for
        the game's place among his games left (M27, _week_p), anyone else at rate_week."""
        g = self.gameday.get(k)
        if g and g[0] == d:
            return g[1]
        if rate is not None:
            return float(rate[k])
        return x if (x := self._week_p.get((k, d))) is not None else self._rw[k]

    @functools.cached_property
    def _week_p(self):
        """{(player, date): his chance to play that game} this week for players who played their team's last game
        (M27): fresh_plays() by its place among his games left from today (game_days(): a locked game isn't one)."""
        return {(k, d): float(fresh_plays(self.rate[k], j))
                for k in self.fresh if k in self.rate.index
                for j, d in enumerate(self.game_days(k, self.week_end), 1)}

    def _pos(self, k):
        return {x for x in self.meta.get(k, {}).get("pos", "").split(",") if x}

    def _lineup(self, ks):
        """A day's starters among ks (players with a game, best first): lineup() on the league's starting slots, else
        the best `lineup`. Best first too."""
        if not self.slots:
            return ks[:self.lineup]
        key = tuple(ks)
        if key not in self._lineups:
            self._lineups[key] = [ks[j] for j in sorted(lineup(self.slots, [self._pos(k) for k in ks]).values())]
        return self._lineups[key]

    def _days(self, keys, end, start=None, until=None, by=None):
        """{date: players with a game that day, best first (per-game value, or the Series `by`: the planner's build)},
        from today through `end`. start: a first day for everyone (a later week), or {player: first day} (claims);
        until: {player: last day} (a later drop); game_days() adds return dates. Today's starters in games under way
        (_held, M18) come first today, so the day's other players fill the slots they leave."""
        day, until = {}, until or {}
        for k in sorted(keys, key=(self._value if by is None else by).get, reverse=True):
            for d in self.game_days(k, min(end, until.get(k, end)), start.get(k) if isinstance(start, dict) else start):
                day.setdefault(d, []).append(k)
        if self.today <= end and (held := self._held(keys, start)):
            day = {self.today: held + day.pop(self.today, []), **day}
        return day

    def _boost(self, end):
        """Per-game lift each player gets from injured teammates (self.lift) over his games from today through `end`:
        each stretch's lift (who's out together then) times the share of those games in it."""
        share = []
        for k, j, a, b in self.lift.index:
            days = self.game_days(k, end)
            share.append(sum(a <= d < b for d in days) / max(len(days), 1))
        return self.lift.mul(share, axis=0).groupby(level="key").sum()

    def _availability(self, base, cur):
        """Share of games a player suits up for: availability() (his better past season and this one, of the games his
        team has played in the box scores; without them, the most anyone has played), then cut by injury status.
        Returns (rate, rest-of-season, share of games left after his return, this week's rate, fresh). A known return date
        (today counts) replaces the status guess: rest of season he plays the share of remaining games after it.
        Without one, a tag cuts nothing if he played his team's last game (players who did play what availability()
        says). This week (self.fresh: a player who played his team's last game; before any games, anyone untagged) his
        j-th game from today plays at fresh_plays() (M27: rate_week holds his next game's chance, _week_p each game's).
        ponytail: one status cut for both horizons; This week runs high for an undated O who sat (0.47 of his rate this
        week, 0.8 rest of season, backtest.py availability). ESPN dates everyone it lists, so it's rare; split it if not."""
        games, played_last = cur.GP.max() if len(cur) else 0, None
        if self.logs is not None and len(self.logs):  # each team's games so far (PHX had played 3 when others had 6)
            team = self.logs.drop_duplicates(["TEAM_ABBREVIATION", "GAME_DATE"]).groupby("TEAM_ABBREVIATION").size()
            games = pd.Series([team.get(self.nba.get(k), games) for k in self.p.index], index=self.p.index, dtype=float)
            last, seen = self.logs.groupby("TEAM_ABBREVIATION").GAME_DATE.max(), set(zip(self.logs.key, self.logs.GAME_DATE))
            played_last = self.p.index[[(k, last.get(self.nba.get(k))) in seen for k in self.p.index]]
        a = availability(base.GP.reindex(self.p.index), cur.GP.reindex(self.p.index).fillna(0), games)
        status = pd.Series({k: STATUS_AVAIL.get(m.get("status", ""), 1.0) for k, m in self.meta.items()}, dtype=float)
        if played_last is None:  # before any games: whoever has no tag
            fresh = status.reindex(a.index).fillna(1.0).eq(1.0)
        else:
            fresh = a.index.to_series().isin(played_last)
            status[status.index.intersection(played_last)] = 1.0
        share = pd.Series(1.0, index=a.index)
        for k, i in self.inj.items():
            rest = [d for d in self.sched.get(self.nba.get(k), ()) if d >= self.today]
            if (i.get("return") or "") >= self.today and rest and k in share.index:
                status[k], share[k] = 1.0, sum(d >= i["return"] for d in rest) / len(rest)
        rate = a * status.reindex(a.index).fillna(1.0)
        return rate, rate * share, share, rate.where(~fresh, fresh_plays(rate, 1)), set(fresh.index[fresh])

    def find(self, name, strict=True):
        if (k := match(name, self.p.index)) or not strict:
            return k
        raise KeyError(f"Player not found: {name}")

    def owner(self, k):
        return next((t for t, ks in self.rosters.items() if k in ks), "FA" if k in self.fa else None)

    # --- value -------------------------------------------------------------
    def active(self, punt=()):
        return [c for c in self.cats if c not in punt]

    def value(self, keys=None, punt=(), frame="ros"):
        """One number per player: z-sum over non-punted cats (9cat) or fantasy points per game."""
        if self.mode == "points":
            v = {"ros": self.fp, "cur": self.fp_cur, "form": self.fp_form}.get(frame, self.fp)
        else:  # form: only players with FORM_GP+ games in the window
            v = {"cur": self.zcur, "form": self.zform}.get(frame, self.z)[self.active(punt)].sum(axis=1)
        return v if keys is None else v.reindex(keys).fillna(0)

    def replacement(self, punt=(), frame="ros"):
        """What you can grab off waivers: average of the 5 best free agents (or the pool edge without FA data)."""
        v = self.value(punt=punt, frame=frame)
        pick = self._waiver_level(v)
        z = self.zcur if frame == "cur" else self.z
        return float(v[pick].mean()), z.loc[pick].mean()

    def effective(self, punt=(), frame="ros"):
        """Value over the games a player will actually play: his per-game value where he plays (avail), the
        half-covered waiver line the matchup model uses where he sits. A player who never plays sits below any
        healthy free agent (a plain value x avail would rank him above them, since most values are negative)."""
        v, floor = self.value(punt=punt, frame=frame), float(self.zfill[frame][self.active(punt)].sum())
        return floor + self.avail.reindex(v.index).fillna(0.85) * (v - floor)

    def perceived(self):
        """(waiver level, every player's perceived value): how a player looks on this season's hype over the games he
        plays (Yahoo ranks run on totals, and managers remember who missed a season), floored at what the best free
        agents look like, since anyone worse is just a droppable roster spot."""
        raw = self.effective(frame="cur")
        cv = float(raw[self._waiver_level(raw)].mean())
        return cv, raw.clip(lower=cv)

    @functools.cached_property
    def market(self):
        """Every player's trade-market rank and value (MARKET curve), _market() at CHASE."""
        return self._market(CHASE)

    @functools.cached_property
    def hot_market(self):
        """Hot & cold's price (movers() only, not trades): _market() at HOT_CHASE, leaning on Yahoo's current rank."""
        return self._market(HOT_CHASE)

    def _market(self, chase):
        """Rank: Yahoo's preseason rank blended with its season-to-date one, weighted GP / (GP + chase) (Cur is noise
        early, and injured players sink to 600+), as a geometric mean so a cold start moves a player as much as an
        equally hot one (Cur runs to 700 but can't beat 1), then re-ranked. Players Yahoo didn't rank take the model's
        perceived rank (yahoo False). Players outside the league (not in the snapshot or League.here: Westbrook, Chris
        Paul) rank after everyone in it."""
        rank = self.effective(frame="cur").rank(ascending=False)
        yahoo = [k for k, m in self.meta.items() if m.get("rank")]
        for k in yahoo:
            w = self.p.at[k, "GP_CUR"] / (self.p.at[k, "GP_CUR"] + chase)
            rank[k] = self.meta[k]["rank"]["pre"] ** (1 - w) * max(self.meta[k]["rank"]["cur"], 1) ** w
        inside = rank.index.isin(self.here) | rank.index.isin(list(self.meta))
        rank = (rank + np.where(inside, 0, len(rank))).rank(method="first")
        return pd.DataFrame({"rank": rank.astype(int), "value": 100 * MARKET ** (rank - 1),
                             "yahoo": rank.index.isin(yahoo)})

    def _tier(self, tier, you, them, mkt, max_overpay=None):
        """Which deals clear a difficulty tier (TIERS): win-% gains for you and them, market gap in % (priced()).
        max_overpay (%) can only tighten a tier's floor. Works on scalars and arrays."""
        y, t, lo, hi = TIERS[tier]
        floor = -np.inf if max_overpay is None else -max_overpay
        gain = you > y * self.s
        ok = gain & (them > t * self.s) & (mkt >= max(lo, floor)) & (mkt <= hi)
        if tier == "hard":  # or you win on market by up to FAIR_FIT and both teams gain: the fit is the pitch
            ok = ok | ((you > TIERS["fair"][0] * self.s) & (them > 0) & (mkt >= EVEN) & (mkt <= FAIR_FIT))
        return ok

    def weakest(self, team, m, pv):
        """Perceived cost of the m players `team` would drop to make room: its weakest active ones by perceived value."""
        return float(pv.reindex(self._playing(self.rosters[team])).nsmallest(m).sum()) if m > 0 else 0.0

    def best_fa(self, n, skip=(), punt=()):
        """The n free agents a team would pick up for open spots: per-game value times share of games played, so an
        injured name on waivers isn't who fills the spot. punt: yours only, as in free_moves() (Oct 29: punting FG% and
        TO, a spot your trade opened got Gafford, a shot-blocker, not Wagler: 0.4 win % on average over 18 deals, 1.1 at
        most, and about 11 points of BLK odds)."""
        if not self.fa:
            return []
        v = self.effective(punt).reindex(self.fa)
        return v[~v.index.isin(list(skip))].nlargest(n).index.tolist()

    def _waiver_level(self, v):
        """The 5 best free agents by v, or the 10 players just outside the rostered pool without FA data."""
        if self.fa:  # healthy ones only: an NA or IL free agent isn't who you'd grab
            fa = [k for k in self.fa if self.meta.get(k, {}).get("status", "") not in OUT]
            return v.reindex(fa or self.fa).nlargest(5).index
        n = self.teams_n * self.roster_n
        return v.nlargest(n + 10).index[n:]

    def player(self, k, punt=()):
        m = self.meta.get(k, {})
        return {"name": self.p.at[k, "PLAYER_NAME"], "team": self.owner(k), "pos": m.get("pos", ""),
                "status": m.get("status", ""), "value": round(float(self.value([k], punt).iloc[0]), 2),
                "value_now": round(float(self.value([k], punt, "cur").iloc[0]), 2),
                **self._form(k, punt),
                "fp": round(float(self.fp[k]), 1), "gp": int(self.p.at[k, "GP_CUR"]),
                "avail": round(float(self.avail[k]), 2),
                "mkt": round(float(self.market.at[k, "value"]), 1), "mkt_rank": int(self.market.at[k, "rank"]),
                **({} if self.market.at[k, "yahoo"] else {"mkt_model": True}),  # no Yahoo rank: the model's own
                **({"games": int(self.games_week[k])} if self.sched else {}),
                **({"injury": self.inj[k]["note"], "return": self.inj[k]["return"]} if k in self.inj else {}),
                **({"listed": {"as": g[2], "day": g[0], "plays": round(100 * g[1])}} if (g := self.gameday.get(k)) else {}),
                **({"lift": self.lifted[k]} if k in self.lifted else {}),  # injured teammates whose minutes he takes
                **({"il": True} if k in self.il.get(self.owner(k), ()) else {}),  # parked in an IL / IL+ / NA slot
                **({"proj": self.proj.at[k, "src"]} if k in self.proj.index else {}),  # whose projection his line blends in
                **{f: m[f] for f in ("owned", "waiver") if f in m},  # Yahoo roster % / on waivers until
                "z": {c: round(float(self.z.at[k, c]), 2) for c in self.cats}}

    def _form(self, k, punt=()):
        """His value over the last FORM_DAYS (form, form_gp games) and trend: up / down when it's trend_cut() off his
        rest-of-season value (fewer games, a bigger gap: M8). Nothing with under FORM_GP games in the window."""
        if k not in self.form_gp.index:
            return {}
        f, v = (float(self.value([k], punt, fr).iloc[0]) for fr in ("form", "ros"))
        return {"form": round(f, 2), "form_gp": int(self.form_gp[k]),
                **({"trend": "up" if f > v else "down"} if abs(f - v) >= trend_cut(self.form_gp[k]) * self.u else {})}

    @functools.cached_property
    def lifted(self):
        """{player: [each injured teammate whose minutes he takes (self.lift), back when (days from today, and term:
        short / medium / long by TERM_DAYS), how many of the player's games come before that (game_days(), so after his
        own return), and his per-game lift in them, averaged over the stretches with whoever else is out, plus what he
        really did this season `with` and `without` that teammate (games, MIN, PTS; onoff()'s evidence) when he has
        games both ways]}, for lifts of half a minute or more in at least one game."""
        out, T = {}, "TEAM_ABBREVIATION"
        logs = self.logs if self.logs is not None else pd.DataFrame(columns=["key", T, "GAME_ID", "GAME_DATE", "MIN", "PTS"])
        gone = sat(logs, self.nba, self.lift.index.get_level_values("by").unique())
        for (k, j), rows in self.lift.groupby(level=["key", "by"]):
            n = [sum(a <= x < b for x in self.game_days(k)) for _, _, a, b in rows.index]  # his games in each stretch
            d = rows.mul(n, axis=0).sum() / max(sum(n), 1)  # the lift over all of them
            if d.MIN >= 0.5 and sum(n):
                mine = logs[(logs.key == k) & (logs[T] == self.nba.get(k))]
                off = mine.GAME_ID.isin(gone.GAME_ID[(gone.by == j) & (gone[T] == self.nba.get(k))])
                seen = lambda g: {"games": len(g), "MIN": round(float(g.MIN.mean()), 1), "PTS": round(float(g.PTS.mean()), 1)}
                out.setdefault(k, []).append({"by": self.p.at[j, "PLAYER_NAME"], "return": self.inj[j]["return"],
                                              "games": sum(n), **self._term(j),
                                              **{c: round(float(d[c]), 1) for c in ["MIN"] + COUNTING},
                                              **({"with": seen(mine[~off]), "without": seen(mine[off])}
                                                 if off.any() and (~off).any() else {})})
        return out

    def _term(self, j):
        """How long injured player j is out from today: days, and term (short / medium / long by TERM_DAYS)."""
        days = (date.fromisoformat(self.inj[j]["return"]) - date.fromisoformat(self.today)).days
        return {"days": days, "term": "short" if days <= TERM_DAYS[0] else "medium" if days <= TERM_DAYS[1] else "long"}

    def weeks(self, n=3):
        """The n matchup weeks after this one, (start, end): the season schedule's (some run 14 days), else
        Monday to Sunday."""
        out = [(w["start"], w["end"]) for w in self.snap.get("schedule", ()) if w["start"] > self.week_end][:n]
        while len(out) < n:
            s = date.fromisoformat(out[-1][1] if out else self.week_end) + timedelta(1)
            out.append((s.isoformat(), (s + timedelta(6)).isoformat()))
        return out

    def card(self, name, punt=(), news=True, every=False):
        """One player in full (the app's player card): player() plus per-game lines (rest of season; the Yahoo or ESPN
        projection blended into it; this season, the last 30, 14 and 7 days from the box scores, or this season's stats
        without them; a window is left out when it holds no games, or the same games as the one before unless `every`), his NBA games left this
        matchup week and in the next 3, and his rank on your build (effective(), valued on the MARKET curve like
        strategy()'s buy list) to set against his market rank."""
        k, cols = self.find(name), ["MIN"] + RAW
        lines = {"ros": (self.p.loc[k, cols], None)}
        if k in self.proj.index:  # the expert line blended into his
            x, src = self.proj.loc[k, cols].astype(float), self.proj.at[k, "src"]
            if src == "Yahoo":
                x["MIN"] = np.nan  # Yahoo shows none; experts() estimates them
            lines[src.lower()] = (x, None)
        if self.logs is None:
            if k in self.cur.index:
                lines["season"] = (self.cur.loc[k, cols], self.cur.at[k, "GP"])
        else:
            g, n = self.logs[self.logs.key == k], 0
            for w, days in (("season", None), ("last30", 30), ("last14", 14), ("last7", 7)):
                x = g if days is None else g[g.GAME_DATE >= pd.Timestamp(self.today) - pd.Timedelta(days=days)]
                if len(x) and (len(x) != n or every):
                    lines[w], n = (x[cols].mean(), len(x)), len(x)
        pct = lambda s, m, a: round(100 * float(s[m] / s[a]), 1) if s[a] else None
        fit = self.effective(punt).rank(ascending=False, method="first")[k]
        weeks = [(self.today, self.week_end)] + self.weeks(3) if self.sched else []
        return {**self.player(k, punt), "nba": self.nba.get(k, ""),
                "lines": {w: {"gp": gp and int(gp), "MIN": None if pd.isna(s.MIN) else round(float(s.MIN), 1),
                              "FG%": pct(s, "FGM", "FGA"), "FT%": pct(s, "FTM", "FTA"),
                              **{c: round(float(s[c]), 1) for c in COUNTING}}
                          for w, (s, gp) in lines.items()},
                "weeks": [{"start": s, "end": e, "games": len(self.game_days(k, e, s))} for s, e in weeks],
                "fit_rank": int(fit), "fit": round(100 * MARKET ** (fit - 1), 1),
                **({"news": self.news([k]).get(k, [])[:3]} if self.news and news else {})}

    def compare(self, names, punt=()):
        """2 to 4 players side by side (Q3): each one's card() without news, every box-score window that holds games
        (so the same window lines up across players), and, in 9-cat, `slot`: your season win %
        with him in one of your roster spots. The compared players you own all leave, he comes in, and _after() keeps
        your number of active players as a trade would (an injured one parks in a free IL slot, a spot too many drops
        whoever you'd miss least, which can be him: `cut`; a spot short takes the best free agent outside the
        comparison). So "keep A or B" and "trade for C or D" read off the same number. `win` is today's win %; `best`
        names who's ahead on each number where higher is better (nobody when they're all level)."""
        keys = list(dict.fromkeys(self.find(n) for n in names))
        if not 2 <= len(keys) <= 4:
            raise KeyError("Pick 2 to 4 different players to compare")
        cards = [self.card(self.p.at[k, "PLAYER_NAME"], punt, news=False, every=True) for k in keys]
        out = {"players": cards}
        if self.mode != "points":
            mine, w0 = [k for k in keys if k in self.rosters[self.me]], self.outlook(self.me, punt=punt)["win"]
            for c, k in zip(cards, keys):
                ks, dropped, added, il = self._after(self.me, mine, [k], punt)
                if set(added) & set(keys):  # another compared free agent never fills the open spot
                    ks = [x for x in ks if x not in added]
                    added = self.best_fa(len(added), skip=ks + keys, punt=punt)
                    ks += added
                win = self.outlook(self.me, {self.me: self.vec(ks, il)}, punt)["win"]
                c["slot"] = {"win": round(100 * win, 1), "delta": round(100 * (win - w0), 1) + 0.0, "cut": k in dropped,
                             "drop": self.names([x for x in dropped if x != k]), "add": self.names(added)}
            out["win"] = round(100 * w0, 1)
        nums = {"value": lambda c: c["value"], "value_now": lambda c: c["value_now"], "form": lambda c: c.get("form"),
                "avail": lambda c: c["avail"], "fit": lambda c: c["fit"],
                "games": lambda c: sum(w["games"] for w in c["weeks"]) if c["weeks"] else None,
                "slot": lambda c: c["slot"]["win"] if "slot" in c else None,
                **{f"z.{cat}": (lambda cat: lambda c: c["z"][cat])(cat) for cat in self.cats},
                **{f"week{i}": (lambda i: lambda c: c["weeks"][i]["games"] if c["weeks"] else None)(i) for i in range(4)}}
        best = {}
        for f, get in nums.items():
            xs = {c["name"]: get(c) for c in cards if get(c) is not None}
            if len(set(xs.values())) > 1:
                best[f] = [n for n, v in xs.items() if v == max(xs.values())]
        return {**out, "best": best}

    def roster_news(self, days=2):
        """News on your players and their NBA teammates in the rotation (10+ min), newest first: since the snapshot or
        the last `days` days, whichever is longer. new = after the snapshot."""
        mine = set(self.rosters[self.me])
        teams = {self.nba.get(k) for k in mine} - {None, ""}
        keys = mine | {k for k in self.p.index if self.nba.get(k) in teams and self.p.at[k, "MIN"] >= 10}
        utc = lambda t: t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        pulled = utc(datetime.fromisoformat(self.snap["pulled"])) if self.snap.get("pulled") else ""
        since = min(pulled or "9", utc(datetime.now(timezone.utc) - timedelta(days)))
        got = self.news(keys, since) if self.news else {}
        return sorted(({"name": self.p.at[k, "PLAYER_NAME"], "nba": self.nba.get(k, ""), "team": self.owner(k),
                        "mine": k in mine, "new": x["date"] > pulled, **x} for k, xs in got.items() for x in xs),
                      key=lambda x: x["date"], reverse=True)

    # --- matchup model (9-cat) -----------------------------------------------
    def vec(self, keys, il=None, team=None, wk=None):
        """Weekly totals: each active player's week, plus what the ones in `il` (default: parked here) add when they
        return (_return()): into an open active spot first (_open()), then in place of the weakest. Until then another
        team's (`team`, not yours) open spot holds a waiver-level week (self.level); yours holds nothing, so Waivers says
        to fill it. wk: one schedule week's (week, back, from-return) arrays instead of the average week's
        (_sched_week(), M2)."""
        il = self._parked if il is None else set(il)
        on, off = [k for k in keys if k not in il], [k for k in keys if k in il]
        S = np.r_[self._ids(on), self._open(team, self.roster_n - len(on))].astype(int)
        return (self._wk if wk is None else wk[0])[S].sum(0) + self._return(S, self._ids(off), wk=wk)

    def _sched_week(self, start, end):
        """One week of the season schedule as vec()'s arrays (M2), from each player's NBA games from start through end
        (self.sched): his line x games from his return (an ESPN date after today) x his rate, the rest half-covered at
        the waiver level, as `week` does over an average week; parked players' healthy weeks from their return, and the
        share of the week's games that is (_return()'s back); open spots the waiver level's week x days / 7. A player
        with no NBA team in the schedule keeps his average week x days / 7. Cached per week (copies share it: nothing
        here reads rosters). -> (week, back share, from-return week)"""
        if (hit := self._weeks.get((start, end))) is not None:
            return hit
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        rate, line = self.rate.reindex(self.p.index).fillna(1.0).to_numpy(), self.p[RAW].to_numpy(float)
        cal = {t: np.array(sorted(ds)) for t, ds in self.sched.items() if ds}
        n = lambda t, a, b: max(int(np.searchsorted(cal[t], b, "right") - np.searchsorted(cal[t], a)), 0) if t in cal else np.nan
        team = {t: n(t, start, end) for t in cal}
        g = np.array([team.get(self.nba.get(k), np.nan) for k in self.p.index], float)
        a = g.copy()
        for k, i in self.inj.items():  # from his return date only
            if (i.get("return") or "") > self.today and k in self._ix and self.nba.get(k) in cal:
                a[self._ix[k]] = n(self.nba[k], max(start, i["return"]), end)
        no = np.isnan(g)
        played = (a * rate)[:, None]
        wk = played * line + (np.nan_to_num(g)[:, None] - played) * FILL * self._fill_line
        cr = a[:, None] * (rate[:, None] * line + (1 - rate[:, None]) * FILL * self._fill_line)
        bk = np.where(g > 0, a / np.where(g > 0, g, 1), 0.0)
        wk[no], cr[no], bk[no] = self.week.to_numpy()[no] * days / 7, self.week_il.to_numpy()[no] * days / 7, self.back.to_numpy()[no]
        self._weeks[start, end] = out = (np.vstack([wk, np.zeros(len(RAW)), self.level * days / 7, np.zeros(len(RAW))]),
                                         np.r_[bk, 0.0, 0.0, 0.0], np.vstack([cr, np.zeros((3, len(RAW)))]))
        return out

    def _open(self, team, n):
        """n open active spots as _return() ids: another team's (holds a waiver-level week) or yours (nothing)."""
        return [len(self._ix) + (1 if team not in (None, self.me) else 2)] * max(n, 0)

    def _ids(self, keys):
        """Players' rows in _return()'s arrays (anyone unknown, or None: nobody)."""
        return np.array([self._ix.get(k, len(self._ix)) for k in keys], dtype=int)

    def _return(self, S, Q, n=None, wk=None):
        """What players parked in IL slots add, back from injury: each takes the spot of the weakest active player left
        (effective value) from his return (`back`), the best one first, while he beats him (else he's the one cut).
        S / Q: active and parked players (_ids), padded with nobody, any leading dims; n: at most that many parked.
        -> weekly totals per leading index. wk: a schedule week's arrays (vec()). ponytail: punt-free values pick who
        goes, and the best one back pushes out the weakest, whoever returns first"""
        n = min(S.shape[-1], Q.shape[-1], n if n is not None else Q.shape[-1])
        S = np.take_along_axis(S, np.argsort(self._ev[S], -1)[..., :n], -1)
        Q = np.take_along_axis(Q, np.argsort(-self._hv[Q], -1)[..., :n], -1)
        on = np.cumprod(self._hv[Q] > self._ev[S], -1).astype(bool)[..., None]
        W, B, C = (self._wk, self._bk, self._cr) if wk is None else wk
        return (on * (C[Q] - B[Q][..., None] * W[S])).sum(-2)

    @property
    def _parked(self):
        return set().union(*self.il.values())

    def week_vec(self, keys, start=None, until=None, by=None):
        """This week's expected totals: per-game line x games he starts the rest of this week (starts(), start /
        until / by as there), his expected starts: each game at his chance to play it (rate_week, or a game-day listing's
        rate: _plays()), a bench player filling in for a starter who sits (M19). Without a schedule it's the
        season-average week, vec()."""
        if not self.sched:
            return self.vec(keys)
        g = self.starts([k for k in keys if k in self._ix], start, until, by, plays=True)
        return np.fromiter(g.values(), float, len(g)) @ self._pw[[self._ix[k] for k in g]] if g else np.zeros(len(RAW))

    @functools.cached_property
    def _pw(self):  # p_week's RAW lines as an array, rows as _ix (week_vec() runs thousands of times a view); p_week
        return self.p_week.loc[list(self._ix), RAW].to_numpy(float)  # is fixed at load

    def _week_roster(self, team, punt=()):
        """This week's players (M21): the team's active ones plus each one parked in an IL slot with a game left this
        week from his return (game_days(): his return date; a game-day listing's chance through _plays(), M16), who
        starts in place of the player his team cuts then. He takes an open active spot first (it holds nothing this
        week), else yours cut il_plan()'s players (the earliest back cuts the one with the fewest games left from his
        return, then the weakest by effective(punt): Nov 3, Black for Ingram, as Black's games are done), another
        team's _return()'s rule (the best one back pushes out its weakest active player by effective() while he beats
        him; else he's the one cut and stays parked). The cut plays until the day before (one whose game today has
        tipped off, through today: M18). -> (keys, {cut: last day})
        ponytail: today's roster plans the cut; one returning days after another is cut in the same order; one back for
        a later game today than his locked cut's still plays it"""
        act, room = self._playing(self.rosters[team]), self._room(team)
        back = {k: g[0] for k in self.il[team] if self.back.get(k, 0) > 0 and (g := self.game_days(k, self.week_end))}
        keys, until = list(act), {}
        # the cut plays until the day before; one locked today can't go before tomorrow (M18: _move_day)
        prev = lambda d: (date.fromisoformat(d) - timedelta(1)).isoformat()
        day = lambda c, d: max(prev(d), prev(self._move_day([c])))
        if team == self.me:
            cut, ev = (self._il_cut(punt) or ((), ()))[1], self.effective(punt)
            cuts = [k for k in act if k in cut]
            for i, q in enumerate(sorted((k for k in back if k not in cut), key=back.get)):
                keys.append(q)
                if i >= room:  # of the plan's cuts, the one with the fewest games left from then, then the weakest
                    c = min(cuts, key=lambda k: (len(self.game_days(k, self.week_end, back[q])), ev[k]))
                    cuts.remove(c)
                    until[c] = day(c, back[q])
            return keys, until
        ev, pool = lambda k: self._ev[self._ix[k]], list(act)
        for i, q in enumerate(sorted(back, key=lambda k: -self._hv[self._ix[k]])):
            if i >= room:
                w = min(pool, key=ev)
                if self._hv[self._ix[q]] <= ev(w):
                    break
                pool.remove(w)
                until[w] = day(w, back[q])
            keys.append(q)
        return keys, until

    def _week_side(self, team, punt=()):
        """A team's rest of this week as week_vec()'s (keys, start, until): _week_roster()'s, plus (M20) another team's
        open active spots streamed from tomorrow, while it has adds left (adds()), each by the waiver level's free agent
        (level_keys) with the most games in it, until a player back from its IL slots takes the spot (the day before
        his first game, M21). Yours stays empty, as season-long (vec()): Waivers says to fill it.
        ponytail: the stream plays his own line, not the level's average; a free agent you'd stream can be theirs too"""
        keys, until = self._week_roster(team, punt)
        room, cap = self._room(team), self.adds(team)
        n = min(room, cap["left"] if cap else room)
        if team == self.me or not self.sched or n <= 0:
            return keys, {}, until
        day = lambda d, k: (date.fromisoformat(d) + timedelta(k)).isoformat()
        tom, until = day(self.today, 1), dict(until)
        # the first room() players back from IL slots took the open spots (_week_roster): each holds one until then
        rets = keys[len(self._playing(self.rosters[team])):][:room]
        ends = [day(self.game_days(q, self.week_end)[0], -1) for q in rets] + [self.week_end] * (room - len(rets))
        free, start = [k for k in self.level_keys if k in set(self.fa) and k in self._ix], {}
        for e in sorted(ends, reverse=True)[:n]:
            g = {k: len(self.game_days(k, e, tom)) for k in free if k not in start}
            if g and (a := max(g, key=lambda k: (g[k], self._value.get(k, 0)))) and g[a]:
                keys, start[a] = keys + [a], tom
                if e < self.week_end:
                    until[a] = e
        return keys, start, until

    @staticmethod
    def _handoff(until, ends, drops, a, s):
        """starts()'s until once a is added on day s for drops: each drop plays until the day before, and one his team
        cuts anyway for a player back from its IL slots (ends: {cut: last day}, _week_roster) hands a that last day,
        since a is the one cut then (M21)."""
        day = (date.fromisoformat(s) - timedelta(1)).isoformat()
        un = {**until, **{d: min(day, ends.get(d, day)) for d in drops}}
        un.update({a: ends[d] for d in drops if d in ends})
        return un

    @functools.cached_property
    def _value(self):
        return self.value()

    @functools.cached_property
    def vecs(self):
        return {t: self.vec(ks, team=t) for t, ks in self.rosters.items()}

    @functools.cached_property
    def rows(self):
        """Weekly vector per player, as a dict of arrays for fast summing in loops."""
        return dict(zip(self.week.index, self.week.to_numpy()))

    def _contrib(self, team, keys, punt=()):
        """Each player's worth to `team` in win %, for ranking drops: what its current roster loses without his line,
        for its own players and anyone else alike. (Gains with an outsider, losses without an own player, sat on opposite
        sides of the odds curve: a team behind kept a worse incoming scrub over its own better player.)
        ponytail: measured on the current roster, not the post-trade one; exact would mean a pass per combo."""
        keys, base = list(keys), self.vecs[team]
        if not keys:
            return {}
        V = np.stack([base - self.rows[k] for k in keys])
        _, O = self._others(team)
        w = self._score(self._concede(cat_probs(V[:, None], O[None], self.cats), punt)).mean(-1)
        w0 = self.outlook(team, punt=punt)["win"]
        return {k: float(w0 - x) for k, x in zip(keys, w)}

    def _score(self, p):
        """What a week is worth in the standings, from category odds p (..., cats): the expected share of categories
        won (categories: each one counts as a win), or the chance to win the matchup (one win). An average week of
        the season: categories independent (M9's shared factor measured no better there)."""
        return match_prob(p) if self.scoring == "one_win" else p.mean(-1)

    def _wscore(self, p):
        """_score() for this week on its real schedule (streams, the add plan, Start / sit, Try a move's week): in One
        Win, the chance to win the matchup with the categories moving together (M9, WEEK_LOAD); in categories the same
        share of categories won."""
        return match_prob(p, self.cats, WEEK_LOAD) if self.scoring == "one_win" else p.mean(-1)

    def _concede(self, p, punt):
        """Punted categories count as losses for you: that's what committing to a punt means."""
        idx = [self.cats.index(c) for c in punt if c in self.cats]
        if idx:
            p = p.copy()
            p[..., idx] = 0.0
        return p

    def _others(self, *skip):
        names = [t for t in self.rosters if t not in skip]
        return names, (np.stack([self.vecs[t] for t in names]) if names else np.zeros((0, len(RAW))))

    def outlook(self, team, override=None, punt=()):
        """A team's win % (_score) vs every other team and on average, and per-category odds (averaged)."""
        override = override or {}
        v = override.get(team, self.vecs[team])
        opp = [t for t in self.rosters if t != team]
        O = np.stack([override.get(t, self.vecs[t]) for t in opp])
        p = self._concede(cat_probs(v[None], O, self.cats), punt)
        m = self._score(p)
        return {"win": float(m.mean()), "odds": dict(zip(self.cats, p.mean(0).tolist())),
                "vs": dict(zip(opp, m.tolist())), "cats_won": dict(zip(opp, p.sum(-1).tolist()))}

    @functools.cached_property
    def outlooks(self):
        return {t: self.outlook(t) for t in self.rosters}

    def standings(self, override=None):
        """Projected finish: every team's average weekly win % vs the rest of the league."""
        wins = ({t: o["win"] for t, o in self.outlooks.items()} if not override
                else {t: self.outlook(t, override)["win"] for t in self.rosters})
        order = sorted(wins, key=lambda t: -wins[t])
        return {t: {"win": round(wins[t], 3), "rank": i + 1} for i, t in enumerate(order)}

    @functools.cached_property
    def playoffs(self):
        """Each team's odds to make the playoffs and win the title (season_sim()), from its record so far (the
        snapshot's category W-L-T) and the season schedule's weeks left. The live week counts its totals so far plus
        the rest of it (week_vec on _week_side(), as This week does); each later week is that week on the real NBA
        schedule (M2, _sched_week(): each player's games in it, none before his ESPN return date, missed games half
        filled; weeks 7 and 17 run 14 days), or without a schedule an average week (vecs) scaled by its days. Real
        odds: nobody concedes a punt. {seats, weeks_left, teams: {team: w, l, t, proj (expected final W-L), playoffs,
        title}}, or None without a schedule or outside a categories league.
        ponytail: a stale snapshot's week plays as a fresh one. One Win records count matchups, not categories: None
        there until a One Win league needs it."""
        weeks = [w for w in self.snap.get("schedule", ()) if w.get("matchups")]
        if self.mode == "points" or self.scoring == "one_win" or not weeks:
            return None
        teams, n = list(self.rosters), len(self.cats)
        ix, rec = {t: i for i, t in enumerate(teams)}, self.snap.get("records", {})
        r = lambda t, k: rec.get(t, {}).get(k, 0)
        done = round(max(r(t, "w") + r(t, "l") + r(t, "t") for t in teams) / n)
        days = lambda w: ((date.fromisoformat(w["end"]) - date.fromisoformat(w["start"])).days + 1) / 7
        V = np.stack([self.vecs[t] for t in teams])
        # each later week from the real NBA schedule (M2): players' games that week, from their return dates; without
        # a schedule, the average week x its days
        week_v = lambda w: (np.stack([self.vec(self.rosters[t], team=t, wk=self._sched_week(w["start"], w["end"]))
                                      for t in teams]) if self.sched else days(w) * V)
        live = self._so_far()
        left = []
        for w in weeks[done:]:
            pr = [(ix[a], ix[b]) for a, b in w["matchups"] if a in ix and b in ix]
            I, J = np.array(pr, int).reshape(-1, 2).T
            if not self.rolled and w["start"] == self.matchup.get("week_start") and all(t in live for t in teams):
                so_far = np.array([[float(live[t].get(c, 0)) for c in RAW] for t in teams])
                rest = np.stack([self.week_vec(*self._week_side(t)) for t in teams])
                left.append((I, J, *cat_probs(rest[I], rest[J], self.cats, so_far[I], so_far[J], ties=True)))
            else:
                Vw = week_v(w)
                left.append((I, J, *cat_probs(Vw[I], Vw[J], self.cats, ties=True)))
        post = [w for w in self.snap["schedule"] if not w.get("matchups") and w["start"] > weeks[-1]["end"]]
        bracket = [cat_probs((Vw := week_v(w))[:, None], Vw[None], self.cats, ties=True) for w in post]
        seats = min(self.snap["settings"].get("playoff_teams", 6), len(teams))  # 6: Yahoo's default for 12 teams
        made, champ, final = season_sim([r(t, "w") + r(t, "t") / 2 for t in teams], left,
                                         bracket or [cat_probs(V[:, None], V[None], self.cats, ties=True)], seats)
        return {"seats": seats, "weeks_left": len(left),
                "teams": {t: {"w": r(t, "w"), "l": r(t, "l"), "t": r(t, "t"),
                              "proj": [round(float(final[i]), 1), round(n * len(weeks) - float(final[i]), 1)],
                              "playoffs": round(float(made[i]), 3), "title": round(float(champ[i]), 3)}
                          for t, i in ix.items()}}

    def _after(self, team, out, inn, punt=(), skip=()):
        """Roster after a trade, keeping its number of active players: injured players coming in take its free IL slots
        (longest out first), extra active players fill its open active spots (_room) and past those mean dropping
        whoever costs the least win % (an injured one coming in isn't dropped; a trade chip only when nobody else is
        left, M24: Yahoo has the manager pick the drop, and nobody cuts one, as in free_moves()), spots the trade opens get
        the best free agents (one it already had stays open: filling it isn't the trade), never one in `skip` (the other
        side's pickups: you pick first). Players parked in IL slots hold no active spot. -> (keys, dropped, added, parked)"""
        hurt = sorted((k for k in inn if self.meta.get(k, {}).get("status", "") in OUT), key=lambda k: (self.back[k], k))
        il = [k for k in self.il[team] if k not in out]
        il += hurt[:max(self.il_n - len(il), 0)]
        keys = [k for k in self.rosters[team] if k not in out] + list(inn)
        n = len(keys) - len(il) - len(self._playing(self.rosters[team]))
        n = max(n - self._room(team), 0) if n > 0 else n
        dropped, added = [], []
        if n > 0:
            c = self._contrib(team, [k for k in keys if k not in il and k not in hurt], punt)
            chip = self.chips(c)
            dropped = sorted(c, key=lambda k: (k in chip, c[k]))[:n]
            keys = [k for k in keys if k not in dropped]
        elif n < 0 and self.fa:
            added = self.best_fa(-n, skip=[*keys, *skip], punt=punt)
            keys += added
        return keys, dropped, added, il

    def _room(self, team):
        """Open active spots: how many players a team can take in a trade before it has to drop one."""
        return max(self.roster_n - len(self._playing(self.rosters[team])), 0)

    # --- trades ------------------------------------------------------------
    def names(self, keys):
        return [self.p.at[k, "PLAYER_NAME"] for k in keys]

    def pool(self, fa=False):
        """Every rostered player as {name, team}, plus the snapshot's free agents (team "FA") with `fa`, by name: the
        app's player searches (Trades, Compare, the top bar). Each name opens his card through find()."""
        teams = {**self.rosters, **({"FA": self.fa} if fa else {})}
        return sorted(({"name": n, "team": t} for t, ks in teams.items() for n in self.names(ks)), key=lambda p: p["name"])

    def _check(self, give, get):
        """Keys for a trade's players. Every give must be yours and every get from one other team (KeyError)."""
        give, get = list(dict.fromkeys(map(self.find, give))), list(dict.fromkeys(map(self.find, get)))  # a name twice: once
        if not_mine := [k for k in give if k not in self.rosters[self.me]]:
            raise KeyError(f"Not on your roster: {', '.join(self.names(not_mine))}")
        if len({self.owner(k) for k in get}) > 1 or any(self.owner(k) in (None, "FA", self.me) for k in get):
            raise KeyError("Players you get must all come from one other team: "
                           + ", ".join(f"{p} ({self.owner(k) or 'no team'})" for k, p in zip(get, self.names(get))))
        return give, get

    def trade(self, give, get, punt=(), free=True, their_free=False):
        """Evaluate a trade for you. 9-cat: change in your weekly win % vs the league, both rosters updated
        (extra players dropped, open spots filled from waivers), on top of your free moves (after_free; free=False
        scores it on today's rosters). their_free: the other team's too; off by default, since managers judge the
        deal itself, not their own waiver options (Martin, Oct 27). Points: value net of replacement level."""
        give, get = self._check(give, get)
        if free or their_free:
            lg = self.after_free(punt, keep=give) if free else self
            if their_free and get:
                lg = lg.after_free(keep=get, team=lg.owner(get[0]))
            return lg.trade(give, get, punt, free=False)
        name = self.names
        n = len(get) - len(give)
        rv, rz = self.replacement(punt)
        cats = (self.z.loc[get].sum() - self.z.loc[give].sum() - n * rz)
        mine = float(self.value(get, punt).sum() - self.value(give, punt).sum() - n * rv)
        other = self.owner(get[0]) if get else None
        tp = self.build(other) if other in self.rosters else []
        theirs = float(self.value(give, tp).sum() - self.value(get, tp).sum() + n * self.replacement(tp)[0])
        cv, pv = self.perceived()
        swap = float(pv.reindex(give).sum() - pv.reindex(get).sum())
        seen = swap + (-self.weakest(other, -n, pv) if n < 0 and other in self.rosters else n * cv)
        out = {"give": [self.player(k, punt) for k in give], "get": [self.player(k, punt) for k in get],
               "cats": {c: round(float(cats[c]), 2) for c in self.cats}, "net": round(mine, 2), "unit": "value",
               "other_team": other, "their_build": tp, "their_net": round(theirs, 2), "their_view": round(seen, 2),
               "free": self._moves_out(self.free.get(self.me, ())),
               "their_free": self._moves_out(self.free.get(other, ())),
               "verdict": "accept" if mine > 0.25 * self.u else "decline" if mine < -0.25 * self.u else "toss-up"}
        if self.mode == "points" or other not in self.rosters:
            return out
        mk, md, ma, mil = self._after(self.me, give, get, punt)
        tk, td, ta, til = self._after(other, get, give, skip=ma)
        ov = {self.me: self.vec(mk, mil), other: self.vec(tk, til, other)}
        out["their_view"] = round(swap - self.weakest(other, len(td), pv) + len(ta) * cv, 2)  # IL slots counted
        b, a = self.outlook(self.me, punt=punt), self.outlook(self.me, ov, punt)
        to = self.outlook(other, ov)
        tb, tw = self.outlooks[other]["win"], to["win"]
        sb, sa = self.standings(), self.standings(ov)
        net = 100 * (a["win"] - b["win"])
        pct1 = lambda x: round(100 * x, 1)
        po = ti = None  # playoff and title odds (Q5) before -> after, both sides: real odds, a punt concedes nothing there
        if self.playoffs:
            after = self._copy({self.me: mk, other: tk}, self.fa, {self.me: set(mil), other: set(til)}).playoffs
            po, ti = ({s: [pct1(x["teams"][t][c]) for x in (self.playoffs, after)] for s, t in (("you", self.me), ("them", other))}
                      for c in ("playoffs", "title"))
        side = lambda v0, v1, o0, o1, c: {"total": [total(v0, c), total(v1, c)], "odds": [pct1(o0[c]), pct1(o1[c])]}
        cats_detail = {c: {"you": side(self.vecs[self.me], ov[self.me], b["odds"], a["odds"], c),
                           "them": side(self.vecs[other], ov[other], self.outlooks[other]["odds"], to["odds"], c)}
                       for c in self.cats}
        roster = lambda keys, inn, dropped, added, il: (  # il: who's parked in an IL slot after the trade
            sorted(({**self.player(k, punt), "il": k in il, "move": "in" if k in inn else "add" if k in added else ""}
                    for k in keys), key=lambda p: -p["value"]) + [{**self.player(k, punt), "move": "drop"} for k in dropped])
        mine_d = sa[self.me]["win"] - sb[self.me]["win"]
        rival = {t for t in sa if t != self.me and (t == other or sb[t]["rank"] <= 4)
                 and sa[t]["win"] - sb[t]["win"] > mine_d}
        flags = []
        for k in get:
            p, why = self.player(k), []
            if p["status"] in OUT:
                why.append(f"listed {p['status']}")
            if p["avail"] < 0.7:
                why.append(f"expected to play {round(100 * p['avail'])}% of games")
            if p["gp"] < 10:
                why.append(f"only {p['gp']} games this season")
            if why:
                flags.append(f"{p['name']}: {', '.join(why)}.")
            if p["value_now"] - p["value"] > 0.5:
                flags.append(f"{p['name']} is playing {p['value_now'] - p['value']:.1f} above his real value: "
                             "you'd be buying at peak hype.")
        # positions: a starting slot none of your active players fits stays empty every day. The season numbers don't
        # see it (no C-eligible player costs fatty zhou ~6% of his games, Oct 28), so it's a flag
        on = lambda ks, il: self._lineup([k for k in ks if k not in il])
        if self.slots and (n := self.lineup - len(on(mk, mil))) > self.lineup - len(on(self.rosters[self.me], self.il[self.me])):
            flags.append(f"Leaves {n} of your {self.lineup} starting slots with nobody to fill {'it' if n == 1 else 'them'}: "
                         f"you'd need a {' or '.join(self._fill(on(mk, mil)))}.")
        if cut := [k for k in md if k in get]:
            flags.append(f"You'd drop {' and '.join(name(cut))} as soon as he arrives: leave him out. It's the same deal "
                         "for you, and it only hands them a roster spot.")
        if cut := [k for k in td if k in give]:
            flags.append(f"They'd drop {' and '.join(name(cut))} as soon as he arrives: leave him out. It's the same deal "
                         "for them, and you keep him.")
        # a drop the deal forces that's a trade chip (M24): nobody else was left to cut, so the deal gives him away
        if cut := [k for k in self.chips(md) if k not in get]:
            flags.append(f"To fit this you'd have to drop {' and '.join(name(cut))}, a trade chip: put him in the deal "
                         "(or another one) instead of cutting him.")
        if cut := [k for k in self.chips(td) if k not in give]:
            flags.append(f"To fit this they'd have to drop {' and '.join(name(cut))}, a trade chip: they won't. Take "
                         "fewer players or send more.")
        flags += [f"{t} gains more than you ({100 * (sa[t]['win'] - sb[t]['win']):+.1f}% vs {100 * mine_d:+.1f}%)"
                  + (", and they're your trade partner." if t == other else f", and they're projected {sb[t]['rank']}.")
                  for t in sorted(rival, key=lambda t: sb[t]["rank"])]
        mkt = self._priced(give, get, md, ma, td, ta)
        d, fair = mkt["diff_pct"], TIERS["fair"][3]
        if d < -fair:
            flags.append(f"You overpay on the market: you send {mkt['give']:.0f} in market value for {mkt['get']:.0f}.")
        elif d > fair:
            flags.append(f"Lowball on the market: you get {mkt['get']:.0f} in market value for {mkt['give']:.0f}. "
                         "Expect a no unless the fit sells it.")
        if len(give) != len(get):  # the side with more players counts them as a package (PACKAGE), not their sum
            big = give if len(give) > len(get) else get
            v = self.market["value"].reindex(big)
            if v.sum() - package(v) >= 1:
                flags.append(f"{len(give)}-for-{len(get)}: the market counts the {len(big)} players you "
                             f"{'send' if big is give else 'get'} as {package(v):.0f}, not their sum of {v.sum():.0f}. "
                             "Managers pay extra for the best player in a deal.")
        out.update({
            "cats_detail": cats_detail,
            "vs": {t: [pct1(b["vs"][t]), pct1(a["vs"][t])] for t in b["vs"]},
            "standings": {t: {"rank": [sb[t]["rank"], sa[t]["rank"]], "win": [pct1(sb[t]["win"]), pct1(sa[t]["win"])],
                              "rival": t in rival} for t in sb},
            "roster_after": {"you": roster(mk, get, md, ma, mil), "them": roster(tk, give, td, ta, til)},
            "flags": flags, "build_after": self._weak(self.outlook(self.me, ov)["odds"])})
        out.update({
            "unit": "win%", "value_net": out["net"], "net": round(net, 1), "their_net": round(100 * (tw - tb), 1),
            "win": round(100 * b["win"], 1), "win_after": round(100 * a["win"], 1),
            "their_win": round(100 * tb, 1), "their_win_after": round(100 * tw, 1),
            "rank": sb[self.me]["rank"], "rank_after": sa[self.me]["rank"], "playoffs": po, "title": ti,
            "odds": {c: round(100 * (a["odds"][c] - b["odds"][c]), 1) for c in self.cats},
            "you_drop": name(md), "you_add": name(ma), "they_drop": name(td), "they_add": name(ta), "market": mkt,
            "tiers": [t for t in TIERS if self._tier(t, net, 100 * (tw - tb), d)],
            "accept": accept(d, 100 * (tw - tb), self.s),
            "verdict": "accept" if net > VERDICT * self.s else "decline" if net < -VERDICT * self.s else "toss-up"})
        return out

    def _priced(self, give, get, md, ma, td, ta):
        """priced() for a trade: the market value each side ends up with: the players it gets as a package (package()),
        net of the players it drops (md, td) and the open spots it fills (ma, ta: a free agent is free, so each is
        worth `spot`, whoever it is)."""
        mv = lambda ks: self.market["value"].reindex(list(ks))
        side = lambda f, inn, out, n: f(mv(inn)) - float(mv(out).sum()) + n * self.spot
        return priced(side(package, give, td, len(ta)), side(package, get, md, len(ma)),
                      (side(np.sum, give, td, len(ta)), side(np.sum, get, md, len(ma))))

    def after_trade(self, give, get, punt=()):
        """The league after a trade (checked as in trade()): both rosters as trade() scores them (extra players dropped,
        yours by `punt`; open spots filled from waivers), the drops back on waivers. Recorded in .trades, with your
        drops and pickups and its market price."""
        give, get = self._check(give, get)
        other = self.owner(get[0])
        mk, md, ma, mil = self._after(self.me, give, get, punt)
        tk, td, ta, til = self._after(other, get, give, skip=ma)
        lg = self._copy({self.me: mk, other: tk}, [k for k in self.fa if k not in ma + ta] + md + td,
                        {self.me: set(mil), other: set(til)})
        lg.trades = [*self.trades, {"team": other, "give": give, "get": get, "drop": md, "add": ma,
                                    "market": self._priced(give, get, md, ma, td, ta)}]
        return lg

    def _step(self, x, punt=()):
        """One trade of a strategy() plan on this league (x: give and get names), then its follow-up free moves (the
        players the plan brought in are kept). None when it offers a player who's gone, or fails the fair tier's terms
        for the other side (their odds and the market, any gain for you). win: your true odds after, obj: on `punt`.
        add / drop: your waiver moves around it, net (an open spot's pickup that a free move swaps out isn't one)."""
        try:
            lg = self.after_trade(x["give"], x["get"], punt)
        except KeyError:
            return None
        t = lg.trades[-1]
        them, mkt = 100 * (lg.outlook(t["team"])["win"] - self.outlooks[t["team"]]["win"]), t["market"]["diff_pct"]
        if not self._tier("fair", np.inf, them, mkt):
            return None
        n = len(lg.free.get(self.me, ()))
        lg = lg.after_free(punt, keep=[k for tr in lg.trades for k in tr["get"]])
        new = lg.free.get(self.me, [])[n:]
        add, drop = t["add"] + [a for a, _, _ in new], t["drop"] + [d for _, d, _ in new if d not in (None, PARK)]
        return {"lg": lg, "free": lg._moves_out(new), "add": self.names(k for k in add if k not in drop),
                "drop": self.names(k for k in drop if k not in add),
                "win": lg.outlook(self.me)["win"], "obj": lg.outlook(self.me, punt=punt)["win"],
                "trade": {"team": t["team"], "give": self.names(t["give"]), "get": self.names(t["get"]),
                          "their_net": round(them, 1), "market": t["market"], "accept": accept(mkt, them, self.s)}}

    def ideas(self, mode="fair", punt=(), team=None, limit=20, depth=10):
        """Trade finder. 9-cat goes through search() with mode as the tier.

        Points leagues search 1-for-1 up to 2-for-2 (no categories to trade around, so every deal is zero-sum):
        fair:    you gain a little, it's near even for them and doesn't look bad to them
        finesse: you gain more, they *look* like winners on hype (hot starts) but lose a little in reality
        """
        if self.mode != "points":
            return self.search(team=team, tier=mode, punt=punt, limit=limit, depth=depth)
        cv, pv = self.perceived()
        vm, vc = self.value(punt=punt).to_dict(), pv.to_dict()
        mine = self.value(self.rosters[self.me], punt).nlargest(depth).index.tolist()
        rv = self.replacement(punt)[0]
        u = self.u
        combos = lambda ks: [c for r in (1, 2) for c in itertools.combinations(ks, r)]
        total = lambda v, ks: sum(v[k] for k in ks)
        out = []
        for t, keys in self.rosters.items():
            if t == self.me or (team and t != team):
                continue
            vg, gv = self.value().to_dict(), self.replacement()[0]
            theirs = self.value(keys).nlargest(depth).index.tolist()
            for g in combos(mine):
                for r in combos(theirs):
                    n = len(r) - len(g)
                    me = total(vm, r) - total(vm, g) - n * rv
                    them = total(vg, g) - total(vg, r) + n * gv
                    seen = total(vc, g) - total(vc, r) + n * cv
                    if mode == "finesse":
                        ok = me > 0.6 * u and seen > 0.3 * u and them > -3 * u
                    else:
                        ok = me > 0.3 * u and them > -u and seen > -0.5 * u
                    if ok:
                        out.append((me, t, g, r, them, seen))
        return self._top_ideas(out, mode, limit, "value", 2)

    def search(self, give=(), get=(), lock=(), team=None, size=3, tier="fair", goal="win", goal_cats=(),
               goal_team=None, healthy_only=False, shape=None, punt=(), limit=20, depth=None, free=True,
               their_free=False, max_overpay=None, outside=0):
        """9-cat trade search scored by the matchup model, vectorized over every give/get combo per team.

        give / get: players every deal must include. Pinning a get fixes the team and searches both whole rosters,
               otherwise it's your top `depth` vs theirs: 10, or everyone when you pick a team.
        lock:  players never offered or asked for. outside: also every player in the market's top `outside`
               (League.market rank), both sides: a role-player search (Q14). Pinned give / get stay in.
        tier:  how likely they say yes (TIERS: your gain, theirs, and the market gap). easy: they win on market, you
               still gain. fair: market within 15%. finesse: market even or in their favour, the fit strongly yours.
               hard: you win on market (up to 30% when both gain). max_overpay (%): tightens the tier's market floor.
               No deal has a player the side taking him would cut on arrival (the deal without him is the same), or
               fits only by a side cutting a trade chip (M24: chips go last, trade() flags it).
        goal:  "win" (weekly win %), "cats" (your odds in goal_cats) or "team" (head-to-head vs goal_team): what
               ranks the deals. The tier is always on win % (as trade()'s), and the last two also skip deals that cost
               you more than GUARD win %.
        shape: "consolidate" (you send more players) or "depth" (you get more).
        free:  score deals on top of your free moves (after_free), never offering the players they pick up.
               their_free: each partner's too (as in trade()).
        """
        if self.mode == "points":
            raise KeyError("Trade search needs a category league")
        if tier not in TIERS:
            raise KeyError(f"Unknown tier {tier}: pick one of {', '.join(TIERS)}")
        if goal == "cats" and not (goal_cats and set(goal_cats) <= set(self.cats)):
            raise KeyError(f"Goal 'cats' needs categories from {', '.join(self.cats)}")
        if goal == "team" and (goal_team not in self.rosters or goal_team == self.me):
            raise KeyError(f"Goal 'team' needs another team: {', '.join(t for t in self.rosters if t != self.me)}")
        give, get = self._check(give, get)
        team = team or (self.owner(get[0]) if get else None)  # a pinned get fixes the team (and lifts _rank's cap)
        depth = depth or (max(map(len, self.rosters.values())) if team else 10)
        if free or their_free:
            lg, out = self.after_free(punt, keep=give) if free else self, []
            each = their_free and not team  # one search per partner, each on top of its own free moves
            for t in [x for x in self.rosters if x != self.me] if each else [team]:
                lt = lg.after_free(keep=get, team=t) if their_free and t else lg
                out += lt.search(give, get, [*lock, *(a for ms in lt.free.values() for a, _, _ in ms)], t, size, tier,
                                 goal, goal_cats, goal_team, healthy_only, shape, punt, limit, depth, free=False,
                                 max_overpay=max_overpay, outside=outside)
            return self._rank(out, tier, team, limit)
        if max(len(give), len(get)) > size:
            raise KeyError(f"More than {size} pinned players on one side")
        if get:
            if team and team != self.owner(get[0]):
                raise KeyError(f"{self.names(get)[0]} plays for {self.owner(get[0])}, not {team}")
            team = self.owner(get[0])
        lock = ({self.find(n) for n in lock} | set(self.market.index[self.market["rank"] <= outside])) - set(give) - set(get)
        cats, me, wide = self.cats, self.me, bool(get)
        cv, pv = self.perceived()
        vg, vc, vm = self.value().to_dict(), pv.to_dict(), self.value(punt=punt).to_dict()
        mv = self.market["value"].to_dict()
        chip = self.chips(self.market.index)

        def combos(keys, pins, v, skip=()):
            free = sorted((k for k in keys if k not in lock and k not in pins and k not in skip), key=lambda k: -v[k])
            free = free if wide else free[:depth]
            return [tuple(pins) + c for s in range(max(1, len(pins)), size + 1)
                    for c in itertools.combinations(free, s - len(pins))]

        injured = lambda k: self.meta.get(k, {}).get("status", "") in OUT
        healthy = lambda cs: [[k for k in c if not injured(k)] for c in cs]  # an injured player coming in stays
        # IL slots: who a side's parked players push out on their return moves with the deal (_return())
        nobody, L, ev = len(self._ix), max([self.il_n, *map(len, self.il.values())]), lambda k: self._ev[self._ix[k]]
        pad = lambda ls, w: np.array([[*self._ids(x), *[nobody] * (w - len(x))] for x in ls], dtype=int).reshape(len(ls), w)

        def stack(cs, il):  # summed weekly vectors (active, and as held: parked ones count in _return()), players,
            # active players, parked ones, summed perceived value and market value as a package
            return (np.stack([sum(self.rows[k] for k in c) for c in cs]),
                    np.stack([sum((self.rows[k] for k in c if k not in il), np.zeros(len(RAW))) for c in cs]),
                    np.array([len(c) for c in cs]), np.array([sum(k not in il for k in c) for c in cs]),
                    np.array([sum(k in il for k in c) for c in cs]),
                    np.array([sum(vc[k] for k in c) for c in cs]), np.array([package(mv[k] for k in c) for c in cs]))

        def parked(cs):  # per combo: injured players (they take the receiver's free IL slots, as in _after), what
            # parking the first 0..size of them takes off its active week, and who they are in that order (_ids)
            cut, hurt = np.zeros((len(cs), size + 1, len(RAW))), [sorted(filter(injured, c), key=lambda k: (self.back[k], k)) for c in cs]
            for i, h in enumerate(hurt):
                for j, k in enumerate(h):
                    cut[i, j + 1:] += self.rows[k]
            return np.array(list(map(len, hurt))), cut, pad(hurt, size)

        def side(team, cs):  # per combo, for _return(): the team's weakest active players left (enough to cut `size`
            # and still push out L) and its parked ones left
            il = self.il[team]
            return (pad([sorted((k for k in self.rosters[team] if k not in c and k not in il), key=ev)[:L + size] for c in cs], L + size),
                    pad([[k for k in il if k not in c] for c in cs], len(il)))

        def returns(rest, park, ok, hurt, n_park, drop, fa_ids, n_fa, team, n_open):  # what a side's parked players
            # add after the deal: its active players are `rest` less `drop`, plus the healthy ones coming in (ok), the
            # injured ones past the first n_park (those park, next to `park`), n_fa free agents and n_open open spots
            if not park.shape[-1] and not n_park.any():  # nobody parked, before or after
                return 0.0
            j, b = np.arange(size), lambda x: np.broadcast_to(x, n_park.shape + x.shape[-1:])
            o = np.arange(self._room(team))
            S = np.concatenate([b(rest), b(ok), np.where(j >= n_park[..., None], b(hurt), nobody),
                                np.where(j < n_fa[..., None], b(fa_ids), nobody),
                                np.where(o < n_open[..., None], self._open(team, 1)[0], nobody)], -1)
            S = np.where((S[..., None] == drop[..., None, :]).any(-1), nobody, S)
            return self._return(S, np.concatenate([b(park), np.where(j < n_park[..., None], b(hurt), nobody)], -1), L)

        def low(groups, c):  # per group: the `size` players the team would miss least (padded with inf), vectors,
            # market, _ids. A trade chip goes last (CHIPPED on top of his value: _after()'s rule, M24)
            vals, vecs, mk = (np.full((len(groups), size), np.inf), np.zeros((len(groups), size, len(RAW))),
                              np.zeros((len(groups), size)))
            low = [sorted(g, key=lambda k: (k in chip, c[k]))[:size] for g in groups]
            for i, g in enumerate(low):
                for j, k in enumerate(g):
                    vals[i, j], vecs[i, j], mk[i, j] = c[k] + CHIPPED * (k in chip), self.rows[k], mv[k]
            return vals, vecs, mk, pad(low, size)

        def drops(A, B, n, inn):  # vector and market value of the n cheapest of A's (per give combo) and B's (per get
            # combo), whether one of them is a player coming in (inn: "A" or "B"), cut as soon as he arrives, or a trade
            # chip (M24: the deal only fits by cutting one), and who
            a, b = len(A[0]), len(B[0])
            pair = lambda x, y: np.concatenate([np.broadcast_to(x[:, None], (a, b) + x.shape[1:]),
                                                np.broadcast_to(y[None], (a, b) + y.shape[1:])], 2)
            vals = pair(A[0], B[0])
            pick = np.argsort(vals, -1)[..., :size]
            cut, mk = np.take_along_axis(pair(A[1], B[1]), pick[..., None], 2), np.take_along_axis(pair(A[2], B[2]), pick, 2)
            on = np.arange(size) < n[..., None]
            v = np.take_along_axis(vals, pick, 2)
            new = on & np.isfinite(v) & ((pick >= size) == (inn == "B"))
            chipped = on & np.isfinite(v) & (v >= CHIPPED / 2)
            who = np.where(on, np.take_along_axis(pair(A[3], B[3]), pick, 2), nobody)
            return (on[..., None] * cut).sum(2), (on * mk).sum(2), new.any(-1) | chipped.any(-1), who

        def fills(p, skip=()):  # the best 0..size free agents' vectors summed, for open spots: yours on your build,
            f, ks = np.zeros((size + 1, len(RAW))), self.best_fa(size, skip=skip, punt=p)  # theirs real; and who
            for i, k in enumerate(ks):
                f[i + 1:] += self.rows[k]
            return f, pad([ks], size)[0]
        # you pick first (as in trade()): theirs after your first j pickups, [j, theirs]. Market: any free agent is worth
        # spot, as many as there are
        mine_fa, j = self.best_fa(size, punt=punt), np.arange(size + 1)
        (fa_me, fa_me_ids), fs = fills(punt), [fills((), mine_fa[:i]) for i in j]
        fa, fa_ids = np.stack([f for f, _ in fs]), np.stack([x for _, x in fs])
        fam_me, fam = np.minimum(j, len(self.fa)) * self.spot, np.minimum(j[None], np.maximum(len(self.fa) - j[:, None], 0)) * self.spot

        roster, il_me = self.rosters[me], self.il[me]
        mine = combos(roster, give, vm)
        if not mine:  # everyone you have is locked (or outside=)
            return []
        GV, GH, gn, ga, gp, gseen, gmk = stack(mine, il_me)
        g_hurt, g_cut, g_ids = parked(mine)
        (rest_g, park_g), ok_g = side(me, mine), pad(healthy(mine), size)
        now_me = self._return(np.r_[self._ids(self._playing(roster)), self._open(me, self._room(me))].astype(int), self._ids(il_me))
        my_rest = low([[k for k in roster if k not in c and k not in il_me] for c in mine], self._contrib(me, roster, punt))
        I = np.arange(len(mine))[:, None]
        before = self.outlook(me, punt=punt)
        out = []
        for t, keys in self.rosters.items():
            if t == me or (team and t != team):
                continue
            hurt = [k for k in keys if healthy_only and self.meta.get(k, {}).get("status", "") in OUT]
            theirs = combos(keys, get, vg, hurt)
            if not theirs:
                continue
            il_t, J = self.il[t], np.arange(len(theirs))[None]
            RV, RH, rn, ra, rp, rseen, rmk = stack(theirs, il_t)
            r_hurt, r_cut, r_ids = parked(theirs)
            (rest_r, park_r), ok_r = side(t, theirs), pad(healthy(theirs), size)
            c_in = self._contrib(me, {k for c in theirs for k in c}, punt)  # what their players would add for you
            c_t = self._contrib(t, set(keys) | {k for c in mine for k in c})
            # injured players coming in park in free IL slots (the ones a side sends free theirs up); the rest need
            # active spots: n_me / n_t extra active players you / they end up with
            to_me = np.minimum(r_hurt[None], np.maximum(self.il_n - len(il_me) + gp[:, None], 0))
            to_t = np.minimum(g_hurt[:, None], np.maximum(self.il_n - len(il_t) + rp[None], 0))
            n_me0, n_t0 = rn[None] - to_me - ga[:, None], gn[:, None] - to_t - ra[None]
            n_me = np.where(n_me0 > 0, np.maximum(n_me0 - self._room(me), 0), n_me0)  # open active spots take extras
            n_t = np.where(n_t0 > 0, np.maximum(n_t0 - self._room(t), 0), n_t0)
            took_me, took_t = np.clip(n_me0, 0, self._room(me)), np.clip(n_t0, 0, self._room(t))  # open spots filled
            filled = took_t[..., None] * self.level  # what their open spots held (vec())
            my_drop, my_drop_m, my_cut, my_who = drops(my_rest, low(healthy(theirs), c_in), n_me, "B")
            their_drop, their_drop_m, their_cut, their_who = drops(
                low(healthy(mine), c_t), low([[k for k in keys if k not in c and k not in il_t] for c in theirs], c_t), n_t, "A")
            adds = np.clip(-n_me, 0, size), np.clip(-n_t, 0, size)  # open spots each side fills
            back_me = returns(rest_g[:, None], park_g[:, None], ok_r[None], r_ids[None], to_me, my_who, fa_me_ids, adds[0],
                              me, self._room(me) - took_me)
            back_t = returns(rest_r[None], park_r[None], ok_g[:, None], g_ids[:, None], to_t, their_who, fa_ids[adds[0]],
                             adds[1], t, self._room(t) - took_t)
            now_t = self._return(np.r_[self._ids(self._playing(keys)), self._open(t, self._room(t))].astype(int), self._ids(il_t))
            me_after = self.vecs[me] - now_me - GH[:, None] + RV[None] - r_cut[J, to_me] - my_drop + fa_me[adds[0]] + back_me
            them_after = (self.vecs[t] - now_t - RH[None] + GV[:, None] - g_cut[I, to_t] - their_drop + fa[adds] + back_t
                          - filled)
            give_m = gmk[:, None] - their_drop_m + fam[adds]  # market value each side ends up with
            get_m = rmk[None] - my_drop_m + fam_me[adds[0]]
            mkt = 100 * (get_m - give_m) / np.maximum(np.maximum(give_m, get_m), 1)  # as in priced()
            names, O = self._others(me, t)
            k = len(O) + 1
            p_mt = cat_probs(me_after, them_after, cats)
            # f(odds vs each other team), (give combo, get combo, other team, cat), 64 give combos at a time: whole
            # rosters (one team picked) are ~480k deals, 2 GB at once
            vs = lambda X, f: np.concatenate([f(cat_probs(X[i:i + 64, :, None], O[None, None], cats)) for i in range(0, len(X), 64)])
            m_o, m_t = vs(me_after, lambda p: self._score(self._concede(p, punt))), self._score(self._concede(p_mt, punt))
            dme = 100 * ((m_o.sum(-1) + m_t) / k - before["win"])
            t_o = vs(them_after, self._score).sum(-1)
            dthem = 100 * ((t_o + self._score(1 - p_mt)) / k - self.outlooks[t]["win"])
            weak = np.concatenate([[0], np.cumsum(np.sort(pv.reindex(self._playing(keys)).to_numpy()))])  # for drops
            seen = gseen[:, None] - rseen[None] + np.where(n_t < 0, -n_t * cv, -weak[np.clip(n_t, 0, len(weak) - 1)])
            if goal == "team":
                h2h = m_t if goal_team == t else m_o[..., names.index(goal_team)]
                score = 100 * (h2h - before["vs"][goal_team])
            elif goal == "cats":
                idx = [cats.index(c) for c in goal_cats]
                odds = ((vs(me_after, lambda p: p.sum(-2)) + p_mt) / k)[..., idx].mean(-1)
                # real odds before, as after: a punted goal category read its conceded 0% before (+30 for every deal)
                score = 100 * (odds - np.mean([self.outlooks[me]["odds"][c] for c in goal_cats]))
            else:
                score = dme
            # a player cut on arrival adds nothing to the side taking him and hands the other a roster spot (a waiver
            # move it could make anyway): the deal without him is the real one. A deal that only fits by cutting a
            # trade chip (M24) is out too: trade() flags it. The tier is on win % for every goal, as trade()'s tiers
            # (B6: a cats or team goal's score passed Fair with win % down, and the builder then listed no tier); the
            # goal's score only ranks the deals that clear it
            ok = self._tier(tier, dme, dthem, mkt, max_overpay) & ~my_cut & ~their_cut
            if goal != "win":
                ok &= dme > -GUARD * self.s
            if shape == "consolidate":
                ok &= gn[:, None] > rn[None]
            elif shape == "depth":
                ok &= gn[:, None] < rn[None]
            G, R = np.nonzero(ok)  # fair, easy: both gain; finesse, hard: likelier to be accepted, then your gain
            rank = (np.minimum(score, dthem) if tier in ("easy", "fair") else
                    1000 * accept_level(mkt, dthem, self.s) + score)[G, R]
            top = np.argsort(-rank)[:limit]
            for gi, ri in zip(G[top], R[top]):
                gs, rs = sum(mv[k] for k in mine[gi]) - gmk[gi], sum(mv[k] for k in theirs[ri]) - rmk[ri]  # sum - package
                m = priced(float(give_m[gi, ri]), float(get_m[gi, ri]), (float(give_m[gi, ri] + gs), float(get_m[gi, ri] + rs)))
                out.append({"team": t, "give": self.names(mine[gi]), "get": self.names(theirs[ri]),
                            "net": round(float(dme[gi, ri]), 1), "their_net": round(float(dthem[gi, ri]), 1),
                            "their_view": round(float(seen[gi, ri]), 2), "unit": "win%", "market": m,
                            "accept": accept(m["diff_pct"], dthem[gi, ri], self.s),
                            **({"score": round(float(score[gi, ri]), 1)} if goal != "win" else {})})
        return self._rank(out, tier, team, limit)

    @staticmethod
    def _rank(out, tier, team, limit):
        s = lambda x: x.get("score", x["net"])
        out.sort(key=lambda x: -min(s(x), x["their_net"]) if tier in ("easy", "fair") else (-ACCEPT.index(x["accept"]), -s(x)))
        if not team:  # spread suggestions across teams
            per_team = {}
            out = [o for o in out if per_team.setdefault(o["team"], []).append(o) or len(per_team[o["team"]]) <= 3]
        return out[:limit]

    def _top_ideas(self, out, mode, limit, unit, digits):
        out.sort(key=lambda x: -min(x[0], x[4]) if mode == "fair" else -x[0])
        per_team = {}
        out = [o for o in out if per_team.setdefault(o[1], []).append(o) or len(per_team[o[1]]) <= 3]
        names = lambda ks: [self.p.at[k, "PLAYER_NAME"] for k in ks]
        return [{"team": t, "give": names(g), "get": names(r), "net": round(me, digits), "their_net": round(th, digits),
                 "their_view": round(se, 2), "unit": unit} for me, t, g, r, th, se in out[:limit]]

    # --- team / waivers / movers -------------------------------------------
    @functools.cached_property
    def totals(self):
        """Per-team category totals: sum of player z per game."""
        return pd.DataFrame({t: self.z.loc[ks].sum() for t, ks in self.rosters.items()}).T

    def build(self, team):
        """Categories a team is effectively punting: up to 2 it wins under WEAK of the time across the league."""
        return [] if self.mode == "points" else self._weak(self.outlooks[team]["odds"])

    @staticmethod
    def _weak(odds):
        return [c for c in sorted(odds, key=odds.get)[:2] if odds[c] < WEAK]

    def profile(self):
        """Your weekly win %, projected standings, per-category odds and category standings (every team's odds
        and rank in each category), H2H vs each team, punt suggestions."""
        tot = self.totals
        odds = pd.DataFrame({t: o["odds"] for t, o in self.outlooks.items()}).T[self.cats]  # team x category
        ranks = odds.rank(ascending=False, method="min").astype(int)  # projected category standings
        o, n = self.outlooks[self.me], len(self.cats)
        vs = {t: {"pct": round(o["vs"][t], 3), "win": round(o["cats_won"][t], 1), "loss": round(n - o["cats_won"][t], 1)}
              for t in o["vs"]}
        st = self.standings()
        return {"totals": tot.round(2).to_dict("index"), "ranks": ranks.to_dict("index"), "vs": vs,
                "cat_odds": odds.round(3).to_dict("index"), "scoring": self.scoring,
                "odds": {c: round(p, 3) for c, p in o["odds"].items()}, "win": round(o["win"], 3),
                "rank": st[self.me]["rank"], "standings": st, "playoffs": self.playoffs,
                "punt_suggestions": self.build(self.me), "builds": {t: self.build(t) for t in self.rosters},
                "strengths": sorted(o["odds"], key=lambda c: -o["odds"][c])[:3], "cheap": self.cheapest()}

    def cheapest(self):
        """Categories where one more player-z (a week of games) raises your odds most, averaged over the league: where a
        trade or stream pays off. Odds near 50% move most, but a noisy category (STL) moves less than its odds suggest."""
        pool = self.p.nlargest(self.teams_n * self.roster_n, "MIN")  # zscores()' pool: one z = one pool std
        unit = np.zeros((len(self.cats), len(RAW)))
        for i, c in enumerate(self.cats):
            if c in ("FG%", "FT%"):  # makes over the pool's rate at the same attempts
                m, a = c[:2] + "M", c[:2] + "A"
                unit[i, RAW.index(m)] = (pool[m] - pool[m].sum() / pool[a].sum() * pool[a]).std()
            else:
                unit[i, RAW.index(c)] = pool[c].std() * (-1 if c == "TOV" else 1)
        _, O = self._others(self.me)
        up = cat_probs((self.vecs[self.me] + GAMES_WEEK * unit)[:, None], O[None], self.cats).mean(1).diagonal()
        odds = self.outlooks[self.me]["odds"]
        return sorted(({"cat": c, "odds": round(odds[c], 3), "per_z": round(100 * float(u - odds[c]), 1)}
                       for c, u in zip(self.cats, up)), key=lambda x: -x["per_z"])

    def strategy(self, size=3, top=5, steps=3):
        """Punt planner. Conceding a category only lowers today's odds: a punt pays off through the moves it unlocks,
        often over several trades, the first of which costs the most. So each build (staying balanced, each category,
        PAIRS) makes its own free moves, then up to `steps` trades in a row: each the one that lifts its own (punted)
        odds most of its top `top` fair deals (search() with that punt on the rosters by then, size 2 after the first
        for speed), followed by its free moves again (_step). Every step is scored on true odds, nothing conceded, and
        a build keeps its best prefix, or the single trade that lifts true odds most. Balanced also tries every build's
        first deals and replays every build's chain while each trade lifts true odds, since it could make them too: a
        punt only wins through what it alone unlocks (its waiver moves, or trades that only pay after a costly first
        one). pick: the build to play, [] (stay balanced) unless a punt beats balanced by more than VERDICT.
        stars: your top 5 players' weak spots (z <= -1) the build concedes. buy: the 5 players on other teams the
        market prices furthest below their worth to the build (its effective() rank on the MARKET curve). sell: its
        mirror on your team, each with why (sell())."""
        if self.mode == "points":
            raise KeyError("The punt planner needs a category league")
        me, now = self.me, round(100 * self.outlook(self.me)["win"], 1)
        builds = [[]] + [[c] for c in self.cats] + [list(p) for p in PAIRS if set(p) <= set(self.cats)]
        found = [self.search(tier="fair", punt=b, size=size, limit=top) for b in builds]
        every = list({(frozenset(x["give"]), frozenset(x["get"])): x for xs in found for x in xs}.values())
        lifts = lambda st, xs, b: [s for x in xs if (s := st["lg"]._step(x, b)) and s["obj"] > st["obj"]]
        plans = []
        for b, xs in zip(builds, found):
            lg = self.after_free(b)
            free = lg._moves_out(lg.free.get(me, ()))
            path = [{"lg": lg, "free": free, "trade": None, "add": [m["add"] for m in free],
                     "drop": [m["drop"] for m in free if m["drop"]],
                     "win": lg.outlook(me)["win"], "obj": lg.outlook(me, punt=b)["win"]}]
            nxt = lifts(path[0], xs if b else every, b)
            opts = [path[:1]] + ([[path[0], max(nxt, key=lambda s: s["win"])]] if nxt else [])
            while nxt:
                path.append(max(nxt, key=lambda s: s["obj"]))
                if len(path) > steps:
                    break
                cur = path[-1]["lg"]  # players the plan brought in or sent away are never traded again
                nxt = lifts(path[-1], cur.search(tier="fair", punt=b, size=min(size, 2), limit=top, free=False,
                                                 lock=set(cur.rosters[me]) ^ set(self.rosters[me])), b)
            plans.append((b, path, opts + [path[:i] for i in range(2, len(path) + 1)]))
        (_, (start, *_), flat_opts) = plans[0]
        for _, path, _ in plans[1:]:  # balanced replays each chain while every trade lifts true odds
            r = [start]
            while len(r) < len(path) and (s := lifts(r[-1], [path[len(r)]["trade"]], [])):
                r += s
            flat_opts.extend(r[:i] for i in range(2, len(r) + 1))
        top5 = self.value(self.rosters[me]).nlargest(5).index
        theirs = [k for t, ks in self.rosters.items() if t != me for k in ks]
        later = lambda t: not (set(t["give"]) <= set(self.rosters[me])  # needs an earlier step first
                               and {self.owner(k) for k in t["get"]} == {t["team"]})
        out = []
        for b, _, opts in plans:
            best = max(opts, key=lambda p: p[-1]["win"])  # the first best, so the fewest moves on a tie
            win = round(100 * best[-1]["win"], 1)
            fit = self.effective(b).rank(ascending=False)
            gap = 100 * MARKET ** (fit - 1) - self.market["value"]
            out.append({"punt": b, "win": win, "gain": round(win - now, 1),
                        "steps": [{"trade": s["trade"] and {**s["trade"], "later": later(s["lg"].trades[-1])},
                                   **{k: s[k] for k in ("free", "add", "drop")}, "win": round(100 * s["win"], 1)}
                                  for s in best],
                        "stars": [{"name": self.p.at[k, "PLAYER_NAME"], "cat": c, "z": round(float(self.z.at[k, c]), 1)}
                                  for k in top5 for c in b if self.z.at[k, c] <= -1],
                        "buy": [{**self.player(k, b), "fit_rank": int(fit[k]), "gap": round(float(g), 1)}
                                for k, g in gap.reindex(theirs).nlargest(5).items() if g > 0],
                        "sell": self.sell(b)})
        out.sort(key=lambda x: -x["win"])  # stable: balanced wins ties
        edge = round(out[0]["win"] - next(x["win"] for x in out if not x["punt"]), 1)  # 62.1 - 61.9 isn't 0.2 in floats
        return {"win": now, "builds": out, "pick": out[0]["punt"] if edge > VERDICT * self.s else [], "edge": edge}

    def sell(self, punt=(), n=5):
        """The buy list's mirror: up to n players on your team the market prices furthest above their worth to the build
        (its effective() rank on the MARKET curve), 5 points or more, each with why. The gap splits into parts on that
        curve that add up to it, and why names the biggest: a hot start (market rank above Yahoo's projected one, Cur
        ahead of Pre), Yahoo (the rest of the market rank against our per-game one; without a Yahoo rank, the model's
        read of this season), games (our per-game rank against the one over the games he'll play) and the build (his
        value is in what it punts)."""
        V = lambda r: 100 * MARKET ** (r - 1)
        fit, eff = self.effective(punt).rank(ascending=False), self.effective().rank(ascending=False)
        per, mk = self.value().rank(ascending=False), self.market
        rostered = self.avail.reindex([k for ks in self.rosters.values() for k in ks]).mean()
        out = []
        for k, g in (mk["value"] - V(fit)).reindex(self.rosters[self.me]).nlargest(n).items():
            if g < 5:  # the player card's "about right" band
                break
            y, z, r = self.meta[k]["rank"] if mk.at[k, "yahoo"] else None, self.z.loc[k], mk.at[k, "rank"]
            hot = V(r) - V(y["pre"]) if y and y["cur"] < y["pre"] else 0
            cats = sorted((c for c in punt if round(z[c], 1) > 0), key=lambda c: -z[c])
            parts = [(hot, y and f"hot start: Yahoo #{y['cur']} this season, #{y['pre']} projected"),
                     (V(r) - V(per[k]) - hot, f"Yahoo projects him #{y['pre']}, we have him #{per[k]:.0f}" if y
                      else "no Yahoo rank, so the market reads his season so far"),
                     (V(per[k]) - V(eff[k]), f"plays {self.avail[k]:.0%} of games (rostered players: {rostered:.0%})"),
                     (V(eff[k]) - V(fit[k]), "his value is in " + ", ".join(f"{c} ({z[c]:+.1f})" for c in cats) if cats
                      else "the punt lifts other players past him")]
            top = max(p for p, _ in parts)
            why = "; ".join(t for p, t in sorted(parts, key=lambda x: -x[0]) if p >= top / 3)
            out.append({**self.player(k, punt), "fit_rank": int(fit[k]), "gap": round(float(g), 1),
                        "why": why[0].upper() + why[1:]})
        return out

    def waivers(self, punt=(), limit=15):
        """Best free agents for your team + add/drop pairs. 9-cat: scored by the change in your weekly win %."""
        if self.mode != "points" and self.fa:
            return self._waivers_matchup(punt, limit)
        fa = self.value(self.fa, punt).nlargest(limit)
        roster = [k for k in self.rosters[self.me] if self.meta.get(k, {}).get("status", "") not in OUT]
        drops = self.value(roster, punt).nsmallest(3)
        moves = [{"add": self.p.at[a, "PLAYER_NAME"], "drop": self.p.at[d, "PLAYER_NAME"],
                  "gain": round(float(fa[a] - dv), 2), "unit": "value"}
                 for a in fa.index for d, dv in drops.items() if fa[a] - dv > 0.3 * self.u]
        moves.sort(key=lambda m: -m["gain"])
        return {"free_agents": [self.player(k, punt) for k in fa.index],
                "drop_candidates": [self.player(k, punt) for k in drops.index], "moves": moves[:8]}

    def _waivers_matchup(self, punt, limit):
        """Every (add, drop) pair scored by the change in your weekly win % vs the league (drop None: an open spot),
        and the free moves trades are scored on top of."""
        roster = self.rosters[self.me]
        adds, drops = self._adds(punt), self._drops(roster, team=self.me)  # PARK with a free IL slot (B5)
        gain = self._gains(self.me, roster, adds, drops, punt)
        chip = np.array([d in self.chips(roster) for d in drops])  # never the drop, as in free_moves(): sell him
        fit, best_drop = np.where(chip, -np.inf, gain).max(1), np.where(chip, -np.inf, gain).argmax(1)
        sell = lambda i: {"sell": self.p.at[drops[gain[i].argmax()], "PLAYER_NAME"]} if chip[gain[i].argmax()] else {}
        order = np.argsort(-fit)[:limit]
        moves = [{**self._moves_out([(adds[i], drops[best_drop[i]], fit[i])])[0], "unit": "win%", **sell(i)}
                 for i in order if fit[i] > MOVE * self.s][:8]
        cheap = [j for j in np.argsort(-gain.max(0)) if drops[j] not in (None, PARK) and not chip[j]][:3]  # drops that cost least when swapped for the best add
        return {"free_agents": [{**self.player(adds[i], punt), "fit": round(float(fit[i]), 1)} for i in order],
                "drop_candidates": [self.player(drops[j], punt) for j in cheap], "moves": moves,
                "free": self._moves_out(self.free_moves(punt))}

    def chips(self, keys):
        """The players in keys the market ranks in the first CHIP rounds: free moves don't drop them, they're sold."""
        r = self.market["rank"].reindex(list(keys))
        return set(r.index[r <= CHIP * len(self.rosters)])

    def _adds(self, punt):
        return self.value(self.fa, punt).nlargest(40).index.tolist()

    def _drops(self, roster, keep=(), il=None, team=None):
        """Who an add can replace: healthy active players outside `keep`, plus nobody (None) while the active roster has
        an open spot, plus a free IL slot (PARK, B5) when `team`'s roster has one: an injured add's no-drop move.
        il: who's parked (default: the league's IL slots)."""
        il = self._parked if il is None else set(il)
        act = [k for k in roster if k not in il]
        drops = [k for k in act if k not in keep and self.meta.get(k, {}).get("status", "") not in OUT]
        return (drops + [None] * (len(act) < self.roster_n)
                + [PARK] * (team is not None and len(roster) - len(act) < self.il_n))

    def _stash(self, k, il):
        """B5: an injured free agent (OUT) goes straight into a free IL slot (`il`: the team's parked players): no active
        spot, no cover for the games he misses, and from his return he takes an open active spot, else the weakest
        (_return(), as anyone parked). The one rule for free_moves(), Waivers, Hot & cold, after_free() and add_drop()."""
        return self.meta.get(k, {}).get("status", "") in OUT and len(il) < self.il_n

    def _gains(self, team, roster, adds, drops, punt, il=None):
        """Change in team's win % vs the league from swapping each add in for each drop on `roster`: (adds, drops).
        il: who's parked on it (default: the league's IL slots). An injured add with a free IL slot parks (_stash(), B5):
        his only move is PARK, no drop and no active spot, and he counts through _return() from his return. Dropping
        someone as well would be a second move (another team's open spot holds a stream, so it'd ride on his), and
        nobody else can take PARK (-inf)."""
        il = self._parked if il is None else set(il)
        act, (spot,), nobody = [k for k in roster if k not in il], self._open(team, 1), len(self._ix)
        offk = [k for k in roster if k in il]
        pk, parks = np.array([self._stash(a, offk) for a in adds]), np.array([k == PARK for k in drops])
        V = self.vec(roster, il, team)  # an add into an open spot replaces what it held
        D = np.stack([np.zeros(len(RAW)) if k == PARK else self.rows[k] if k else self._wk[spot] for k in drops])
        # who his parked players push out on their return (an open spot first) moves with the swap
        on = np.r_[self._ids(act), self._open(team, self.roster_n - len(act))].astype(int)
        off = self._ids(offk)
        S = np.where(on[None] == self._ids(drops)[:, None], nobody, on[None])
        for i in [i for i, k in enumerate(drops) if k is None][:1]:  # an add into an open spot takes the first one
            S[i, np.flatnonzero(on == spot)[:1]] = nobody
        last = np.where(pk, nobody, self._ids(adds))[:, None].repeat(len(drops), 1)  # the add's active spot (none: parked)
        S = np.concatenate([np.broadcast_to(S, (len(adds), *S.shape)), last[..., None]], -1)
        Q = np.concatenate([np.broadcast_to(off, S.shape[:2] + off.shape),
                            np.where(pk, self._ids(adds), nobody)[:, None, None].repeat(len(drops), 1)], -1)
        back = self._return(S, Q) - self._return(on, off)
        _, O = self._others(team)
        win = lambda X: self._score(self._concede(cat_probs(X[..., None, :], O, self.cats), punt)).mean(-1)
        g = 100 * (win(V - D[None] + self._wk[last] + back) - win(V))
        return np.where(parks[None] == pk[:, None], g, -np.inf)

    def free_moves(self, punt=(), keep=(), team=None):
        """Waiver moves worth making without a trade: the best add/drop (or plain add into an open spot) by win %,
        repeated while one still clears MOVE. keep: players never dropped (a trade's). team: yours by default (punt is
        only yours). [(add, drop or None, gain)]
        ponytail: greedy and ignores the weekly adds cap; a cap only delays moves, rest of season."""
        if self.mode == "points" or not self.fa:
            return []
        team = team or self.me
        roster, il, adds, moves = list(self.rosters[team]), list(self.il[team]), self._adds(punt), []
        while adds:
            drops = self._drops(roster, {*keep, *self.chips(roster)}, il, team)
            if not drops:
                break
            g = self._gains(team, roster, adds, drops, punt, il)
            i, j = np.unravel_index(g.argmax(), g.shape)
            if g[i, j] <= MOVE * self.s:
                break
            moves.append((adds.pop(i), drops[j], float(g[i, j])))
            il += [moves[-1][0]] * self._stash(moves[-1][0], il)  # B5: an injured add parks (drops[j] PARK: no drop)
            roster = [k for k in roster if k != drops[j]] + [moves[-1][0]]
        return moves

    def after_free(self, punt=(), keep=(), team=None):
        """The league after a team's free moves (yours by default): trades and search are scored on top of it, so a
        deal gets no credit for a pickup that team could make anyway, and an open spot it leaves gets the next-best
        free agent. .free[team] holds every free move made on the copy so far."""
        team = team or self.me
        moves = self.free_moves(punt, keep, team)
        if not moves:
            return self
        roster, fa, il = list(self.rosters[team]), list(self.fa), list(self.il[team])
        for a, d, _ in moves:  # as free_moves() made them: an injured add parks in a free IL slot (B5)
            il += [a] * self._stash(a, il)
            roster, fa = [k for k in roster if k != d] + [a], [k for k in fa if k != a] + [d] * (d not in (None, PARK))
        lg = self._copy({team: roster}, fa, {team: set(il)})
        lg.free = {**self.free, team: [*self.free.get(team, ()), *moves]}
        return lg

    def healthy_fa(self):
        """This league with injured free agents (OUT) off waivers: Waivers' "Healthy only". Its suggestions, the free
        moves trades and Strategy are scored on top of and the open spots trades fill skip them. Rosters don't change,
        so the cached team numbers stay."""
        lg = copy.copy(self)
        lg.fa = [k for k in self.fa if self.meta.get(k, {}).get("status", "") not in OUT]
        return lg

    def _copy(self, rosters, fa, il=None):
        """A copy of the league with these rosters, free agents and IL slots (after_free, after_trade)."""
        lg = copy.copy(self)
        for c in ("vecs", "outlooks", "totals", "playoffs", "_tipoff"):  # cached on today's rosters
            lg.__dict__.pop(c, None)
        lg.rosters, lg.fa, lg.il = {**self.rosters, **rosters}, fa, {**self.il, **(il or {})}
        return lg

    def _moves_out(self, moves):
        """[(add, drop, gain)] as {add, drop, gain}; a PARK move (B5) is drop None and park True."""
        name = lambda k: self.p.at[k, "PLAYER_NAME"] if k and k != PARK else None
        return [{"add": name(a), "drop": name(d), "gain": round(float(g), 1), **({"park": True} if d == PARK else {})}
                for a, d, g in moves]

    def _playing(self, keys):
        """Players not parked in an IL slot: the ones on the active roster."""
        parked = self._parked
        return [k for k in keys if k not in parked]

    def _txday(self, when):
        """A transaction's date ("Oct 28 3:55am", league time) as ISO; a date after today is last year's."""
        d = datetime.strptime(f"{' '.join(when.split()[:2])} {self.today[:4]}", "%b %d %Y").date()
        return (d.replace(year=d.year - 1) if d > date.fromisoformat(self.today) + timedelta(1) else d).isoformat()

    def adds(self, team=None):
        """Adds used this matchup week and left under settings' max_adds (Q11: 4 in Martin's league), None when the
        league sets no cap (demo, Yahoo pull). used = the higher of the snapshot's count (`adds`, only while the
        snapshot was pulled this week: Yahoo's own for you, counted from the log for the rest) and the transaction
        log's adds and waiver claims dated this matchup week (trades and drops don't count), so it never shows an add
        you don't have. ponytail: moves made after the snapshot aren't seen, and the log holds only the last 30."""
        cap = self.snap["settings"].get("max_adds")
        if cap is None:
            return None
        team, today = team or self.me, date.fromisoformat(self.today)
        start = self.matchup.get("week_start") or (today - timedelta(today.weekday())).isoformat()
        counted = sum(1 for t in self.snap.get("transactions", ()) if t.get("team") == team
                      and re.search(r"add|claim", t.get("type", "")) and start <= self._txday(t["when"]) <= self.week_end)
        pulled = (self.snap.get("pulled") or "")[:10]
        listed = self.snap.get("adds", {}).get(team) if (start <= pulled <= self.week_end if pulled else not self.rolled) else None
        used = max(counted, listed or 0)
        return {"used": used, "max": cap, "left": max(cap - used, 0), "counted": counted, "listed": listed,
                "week_start": start, "resets": (date.fromisoformat(self.week_end) + timedelta(1)).isoformat()}

    def _timed(self, keys, a, drops, odds, s0, start=None, until=None, ends=None):
        """The best day this week to add a for drops (Q11): each of a's games left (from the day his claim clears, after
        any of drops was added himself, and from tomorrow when a drop's game today has tipped off: M18), or the day after a drop's last game this week (a move that only costs
        this week is cheapest once the drop has played), the drops playing until the day before. keys: the lineup
        before the move (drops in it); start / until: earlier moves' (starts()); ends: drops cut anyway for players back
        from IL slots (_week_roster), whose last day a takes (_handoff). -> (gain in this week's win %, day,
        start, until), the earliest day on a tie: waiting longer only risks losing him."""
        first = max([self.today, self.matchup.get("from", ""), self._clears(a).get(a, ""), self._move_day(drops)]
                    + [(date.fromisoformat(start[d]) + timedelta(1)).isoformat() for d in drops if d in (start or {})])
        after = {(date.fromisoformat(g[-1]) + timedelta(1)).isoformat() for d in drops
                 if (g := self.game_days(d, min(self.week_end, (until or {}).get(d, "9999")), (start or {}).get(d)))}
        best, days = None, sorted(set(self.game_days(a, self.week_end, first)) | {x for x in after if first <= x <= self.week_end})
        for s in days or [first]:
            st, un = {**(start or {}), a: s}, self._handoff(until or {}, ends or {}, drops, a, s)
            g = 100 * (float(self._wscore(odds(self.week_vec(list(keys) + [a], st, un)))) - s0)
            if best is None or g > best[0] + 1e-9:
                best = (g, s, st, un)
        return best

    def _reserve(self, keys, cands, drops, odds, start, until, ends):
        """What an add kept for late news is worth this week (M17), in this week's win %: any player in `keys` can be
        ruled out from any of his games left (HAZARD x (1 - rate) a game; ONE_GAME of those last only that game, the
        rest run to the week's end, as his last game always does), and the add then streams the best of `cands` for
        one of `drops` (or for nobody, with him in a free IL slot) from that day (from his claim, _clears()), scored
        with him out the rest of the week. Played day by day from the last: a day's news, the biggest first, takes the
        add when it beats keeping it for later. keys / start / until / ends: the plan so far (_plan).
        ponytail: one-game absences take no stream (a game's worth); the stream starts on the news day (its best day
        from then, as _timed() picks, moved Nov 3's +0.76 to +0.80 at twice the time); news before a later planned
        move could take that move's add instead, which this counts as a spare one; an add unused at the week's end
        counts nothing"""
        day = lambda d, n: (date.fromisoformat(d) + timedelta(n)).isoformat()
        ev, park = [], len(self.il[self.me]) < self.il_n  # (day, chance, gain); a free IL slot takes him, no drop
        for k in keys:
            games = self.game_days(k, min(self.week_end, until.get(k, "9999")), start.get(k))
            h = HAZARD * (1 - float(self.rate.get(k, 1)))
            for i, d in enumerate(games):
                un = {**until, k: day(d, -1)}
                s = float(self._wscore(odds(self.week_vec(keys, start, un))))
                V = [self.week_vec(keys + [a], {**start, a: f}, self._handoff(un, ends, [x], a, f))
                     for a in cands if a not in keys for x in [*drops, *([k] if park and k not in drops else [])]
                     for f in [max(d, self._clears(a).get(a, d), self._move_day([x]))]]
                if V:
                    p = h * (1 if i == len(games) - 1 else 1 - ONE_GAME)
                    ev.append((d, p, 100 * (float(self._wscore(odds(np.array(V))).max()) - s)))
        v = 0.0
        for d in sorted({e[0] for e in ev}, reverse=True):
            stay, add = 1.0, 0.0
            for g, p in sorted(((g, p) for x, p, g in ev if x == d and g > v), reverse=True):
                add, stay = add + stay * p * (g - v), stay * (1 - p)
            v += add
        return v

    def _plan(self, keys, cands, drops, odds, s0, n, ends=None, hold=True):
        """Streams for the adds left (Q11): up to n moves, each the best (add, drop, day) on top of the ones before
        (_timed): a drop is one of `drops` or a streamer added earlier in the plan, so a later stream can replace an
        earlier one once his games are used. Stops when the next one adds under 0.1, and the last add only goes on a
        move that beats keeping it for late news (_reserve(), M17). -> (moves, start, until, news): each
        move's day, add, drop and gain, in day order (Q13: each gain recounted on top of the days before, so one can
        come out under 0.1; the total stays); start / until for starts(); news: {worth: the kept add's value, move:
        the last add's best move, kept}, None when the plan stops before its last add or hold is off (no weekly cap).
        ends: players cut for ones back from IL slots (_week_roster): each can still be a stream's drop, the stream then
        cut in his place (end: that day)."""
        ends0 = ends = dict(ends or {})
        start, until, moves, keys, base, prev, news = {}, dict(ends), [], list(keys), list(keys), s0, None
        for i in range(n):
            best, dr = None, [x for x in [*drops, *start] if x not in until or x in ends]
            for a in (x for x in cands if x not in keys):
                for d in dr:
                    g, s, st, un = self._timed(keys, a, [d], odds, s0, start, until, ends)
                    if best is None or g > best[0] + 1e-9:
                        best = (g, s, st, un, a, d)
            if best is None or best[0] < 0.1:
                break
            if hold and i == n - 1:  # the last add: spend it only on a move worth more than keeping it for late news
                r = self._reserve(keys, cands, dr, odds, start, until, ends)
                news = {"worth": r, "move": {"day": best[1], "add": best[4], "drop": best[5], "gain": best[0]}, "kept": best[0] < r}
                if news["kept"]:
                    break
            g, s, start, until, a, d = best
            keys.append(a)
            s0 += g / 100
            ends = {**{k: x for k, x in ends.items() if k != d}, **({a: ends[d]} if d in ends else {})}
            moves.append({"day": s, "add": a, "drop": d, "gain": g, "end": until.get(a)})
        moves.sort(key=lambda x: x["day"])
        for i, x in enumerate(moves):
            done = moves[:i + 1]
            s = float(self._wscore(odds(self.week_vec(base + [y["add"] for y in done], {y["add"]: y["day"] for y in done},
                                                     {**ends0, **{y["add"]: y["end"] for y in done if y["end"]},
                                                      **{y["drop"]: until[y["drop"]] for y in done}}))))
            x["gain"], prev = 100 * (s - prev), s
        return moves, start, until, news

    def this_week(self, opp=None, punt=(), limit=5):
        """This week's matchup: totals so far + the rest of the week (week_vec; players in IL slots sit until they're
        back, then start in place of who's cut: _week_roster, M21) -> odds per
        category, the expected score, the chance to win more categories (win), a safe / chase / lost call per
        category, and streaming adds scored on this week's win % (_score; the 0.1 floor is display resolution).
        Games under way (M18): their starters' live lines are in the score so far, the rest of today fills around them,
        a move dropping one waits for tomorrow; tonight = today's NBA games under way, final and to come.
        opp defaults to the snapshot's opponent; with none, returns {"opponent": None, "teams": [...]} to pick from."""
        if self.mode == "points":
            raise KeyError("This week needs a category league")
        me, m = self.me, self.matchup
        others = [t for t in self.rosters if t != me]
        opp = self._opponent(opp)
        if not opp:
            return {"opponent": None, "teams": others, "week_end": self.week_end}
        so_far, left, odds = self._matchup(opp, punt)
        play = lambda t: self._playing(self.rosters[t])
        wk = {t: self._week_side(t, punt) for t in (me, opp)}
        mine, _, ends = wk[me]  # with players back from IL slots, and who's cut for them then
        p = odds(left[me])
        win = float(match_prob(p, self.cats, WEEK_LOAD))  # M9: this week's categories move together
        clears = self._clears

        # streams: each healthy free agent for whichever of your 3 cheapest healthy players (season win-% cost) helps most,
        # scored on the whole lineup since an add only counts on days he cracks your top `lineup`. The 10 best (added
        # today) are then timed: the drop plays until the day before the add's best day (_timed). Each one is an add,
        # so with none left this week there are none; plan = the best way to spend the adds left (_plan)
        healthy = lambda ks: [k for k in ks if self.meta.get(k, {}).get("status", "") not in OUT]
        cost = self._contrib(me, healthy(play(me)), punt)
        drops, fas, cap = sorted(cost, key=cost.get)[:3], healthy(self.fa), self.adds()
        swap = lambda a, d: [k for k in mine if k != d] + [a]
        streams, plan, name = [], None, lambda k: self.p.at[k, "PLAYER_NAME"]
        if drops and fas and (cap is None or cap["left"]):
            V = np.array([[self.week_vec(swap(a, d), clears(a), self._handoff(ends, ends, [d], a, self.today))
                           for d in drops] for a in fas])
            s0 = float(self._wscore(p))
            gain = 100 * (self._wscore(odds(V)) - s0)
            cands = [fas[i] for i in np.argsort(-gain.max(1))[:max(limit, 10)]]
            season0 = self.outlook(me, punt=punt)["win"]
            season = lambda out, inn: round(100 * (self.outlook(me, {me: self.vec(
                [k for k in self.rosters[me] if k not in out] + list(inn))}, punt)["win"] - season0), 1)
            timed = sorted(((*self._timed(mine, a, [d], odds, s0, None, ends, ends), a, d) for a in cands for d in drops),
                           key=lambda x: -x[0])
            seen = set()
            for g, s, st, un, a, d in timed:
                if g < 0.1 or len(streams) == limit:
                    break
                if a not in seen:
                    seen.add(a)
                    c = self.starts(mine + [a], st, un)  # kept: the drop's starts before the add
                    streams.append({**self.player(a, punt), **({"games": c[a], "kept": c[d]} if self.sched else {}),
                                    "gain": round(g, 1), "drop": name(d), "day": s, "season": season([d], [a])})
            moves, st, un, news = self._plan(mine, cands, drops, odds, s0, limit if cap is None else cap["left"], ends,
                                             cap is not None)
            if moves or news:
                g = self.starts(mine + [x["add"] for x in moves], st, un)
                kept = [x["add"] for x in moves if x["add"] not in un]
                plan = {"moves": [{**x, "add": name(x["add"]), "drop": name(x["drop"]), "gain": round(x["gain"], 1),
                                   **({"games": g[x["add"]], "kept": g[x["drop"]]} if self.sched else {})} for x in moves],
                        "gain": round(sum(x["gain"] for x in moves), 1), "keep": [name(k) for k in kept],
                        "season": season([x["drop"] for x in moves if x["drop"] in drops], kept),
                        # the last add kept for late news (M17) unless its move beats what that's worth
                        "news": news and {"worth": round(news["worth"], 1), "kept": news["kept"],
                                          "move": {**news["move"], "add": name(news["move"]["add"]),
                                                   "drop": name(news["move"]["drop"]), "gain": round(news["move"]["gain"], 1)}}}
        call = lambda c, x: "punt" if c in punt else "safe" if x >= 1 - WEAK else "lost" if x <= WEAK else "chase"
        return {"opponent": opp, "teams": others, "week_end": self.week_end,
                "live": all(t in self._so_far() for t in (me, opp)),
                **({"yahoo_games": {"you": m["games"][me], "them": m["games"][opp]}}  # Yahoo's played / left, as set
                   if m.get("opponent") == opp and all(t in m.get("games", {}) for t in (me, opp)) else {}),
                "win": round(100 * win, 1), "score": [round(float(p.sum()), 1), round(len(self.cats) - float(p.sum()), 1)],
                "lineups": self.start_sit(opp, punt),
                **({"games": {t: sum(self.starts(*wk[x]).values()) for t, x in (("you", me), ("them", opp))}}
                   if self.sched else {}),
                "cats": {c: {"you": [total(so_far[me], c), total(so_far[me] + left[me], c)],
                             "them": [total(so_far[opp], c), total(so_far[opp] + left[opp], c)],
                             "odds": round(100 * float(x), 1), "call": call(c, x)} for c, x in zip(self.cats, p)},
                "today": max(self.today, m.get("from", "")), "adds": cap, "streams": streams, "plan": plan,
                # today's NBA games (M18): under way (tipped off), final, still to come; when the live scores were fetched
                **({"tonight": {"on": sum(s == 2 for _, s in self.tonight), "final": sum(s == 3 for _, s in self.tonight),
                                "to_come": sum(s == 1 for _, s in self.tonight),
                                "lineups": self.slot_day == self.today,  # B8: the snapshot's lineup counts (pulled today)
                                "fetched": (ft := (self.loaded or {}).get("live.json")) and
                                datetime.fromtimestamp(ft, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}}
                   if self.tonight and m.get("from", self.today) <= self.today else {}),
                # players back from IL slots this week (M21): from their first game, in place of who's cut after his last
                "back": {t: {"in": [{"name": name(k), "day": self.game_days(k, self.week_end)[0]} for k in wk[x][0] if k in self.il[x]],
                             "cut": [{"name": name(k), "last": d} for k, d in wk[x][2].items() if k not in wk[x][1]],
                             # their open spot's stream from tomorrow (M20), until a player back takes it
                             "stream": [{"name": name(k), "from": d, "until": wk[x][2].get(k)} for k, d in wk[x][1].items()]}
                         for t, x in (("you", me), ("them", opp))}}

    def _opponent(self, opp=None):
        """This week's opponent: opp, else the snapshot's (None when neither names one). KeyError for an unknown team."""
        opp = opp or self.matchup.get("opponent")
        others = [t for t in self.rosters if t != self.me]
        if opp and opp not in others:
            raise KeyError(f"Unknown opponent {opp}: pick one of {', '.join(others)}")
        return opp

    def _so_far(self):
        """{team: this week's totals so far}, from every pairing in the snapshot (an opponent picked in the app has
        them too), {} once the snapshot's week is over. A snapshot from an earlier day misses the days from its `from`
        to yesterday: their box scores go on, each day's starters by _counted() (the snapshot's own day: its lineup,
        B8; a later one: the best active players), so the rest of the week can still start today; a game the box
        scores don't have yet comes from the live ones (M18: last night's late games). Today (M18), the live lines of
        the starters in games that have tipped off (_held: the same rule) go on as they're played, unless the
        snapshot's totals already hold today (`from`).
        ponytail: after the snapshot's day, the lineup a manager set by hand may have differed"""
        if self.rolled:
            return {}
        tot = {**{t: x for pr in self.snap.get("matchups", ()) for t, x in pr.get("totals", {}).items()},
               **self.matchup.get("totals", {})}
        f = self.matchup.get("from", self.today)
        if f > self.today:
            return tot
        live, today = self.box[self.box.played], pd.Timestamp(self.today)
        g = live.iloc[:0] if self.logs is None else self.logs
        g = pd.concat([g[(g.GAME_DATE >= f) & (g.GAME_DATE < today)],
                       live[(live.GAME_DATE >= f) & (live.GAME_DATE < today) & ~live.GAME_ID.isin(g.GAME_ID)]])
        now = self.box[self.box.GAME_DATE == today].set_index("key")
        for t in tot:
            on = self._playing(self.rosters.get(t, ()))
            for d, day in g[g.key.isin(on)].groupby("GAME_DATE"):
                got = day.set_index("key").loc[self._counted(t, day.key, d.date().isoformat()), RAW].sum()
                tot[t] = {**tot[t], **{s: float(tot[t].get(s, 0)) + float(got[s]) for s in RAW}}
            if held := [k for k in self._held(on) if k in now.index]:
                got = now.loc[held, RAW].sum()
                tot[t] = {**tot[t], **{s: float(tot[t].get(s, 0)) + float(got[s]) for s in RAW}}
        return tot

    def _matchup(self, opp, punt=()):
        """This week vs opp: ({team: totals so far}, {team: the rest of the week's, week_vec of its players this week
        (_week_side: players back from IL slots in for who's cut then, their open spot streamed, M20)},
        odds(v): your category odds with v as your rest of the week, any leading shape, punts conceded). A side with
        no totals so far zeroes both: the rest of the week alone, never your week against their nothing."""
        me, tot = self.me, self._so_far()
        tot = tot if me in tot and opp in tot else {}
        so_far = {t: np.array([float(tot.get(t, {}).get(s, 0)) for s in RAW]) for t in (me, opp)}
        left = {t: self.week_vec(*self._week_side(t, punt)) for t in (me, opp)}  # their open spot streamed (M20)
        return so_far, left, lambda v: self._concede(cat_probs(v, left[opp], self.cats, so_far[me], so_far[opp]), punt)

    def _week_plan(self, opp=None, punt=(), span=None, cats=(), bench=True):
        """Who starts each day this week vs opp for the most category wins (Q18). From per-game value's lineups, every
        one-game change (sit a starter, start a bench player who fits, swap the two) is scored on the week's odds
        (_matchup) and the best one made while one helps, so a close category counts, a decided or punted one doesn't,
        and a FG%, FT% or TO lead can be worth sitting someone for even with a slot open (bench=False: swaps only, the
        Schedule's lineups). cats: play for those categories only (the share of them won). span: a later matchup week
        (start, end) vs the team the season schedule pairs you with, on rest-of-season lines and no score yet.
        A day's value is its expected line with the plan's starters (dv): a bench player who doesn't fit next to them
        fills in for one who sits, best first (M19, _subs); one who'd fit with a slot open sits by choice.
        -> {opp, day: {date: players with a game, best first}, base / plan: {date: starters} on per-game value / here,
        one: {(date, player): that game's expected line, with the lift from teammates out that day, not the week's
        average}, dv(date, starters): that day's expected line, bump: {player: [(from, until, teammate out, MIN + RAW
        lift)]}, rate, fits, odds, goal, v0 / v: your rest of the week on each, now: goal of v, moves: the last round's
        changes [(date, sits, starts)], g: their goal}, None with no opponent or schedule.
        Today's starters in games that have tipped off (_held, M18) are set (lock: (date, player)): they hold their
        slots, count nothing here (their stats are in the score so far), no change moves them and nobody fills in
        for them.
        ponytail: a fill-in is counted whenever a starter sits, though one ruled out after the bench's game tipped off
        can't be replaced; one change at a time finds a local best, not every lineup's."""
        if self.mode == "points" or not self.sched:
            return None
        play = lambda t: [k for k in self._playing(self.rosters[t]) if k in self.p.index]
        if span:
            s, e = span
            wk = next((w for w in self.snap.get("schedule", ()) if w["start"] == s), {})
            if not (opp := next((t for pr in wk.get("matchups", ()) if self.me in pr for t in pr if t != self.me), None)):
                return None
            rate, line, days, mine = self.rate, self.p, lambda ks: self._days(ks, e, s), play(self.me)
        else:
            if not (opp := self._opponent(opp)):
                return None
            mine, until = self._week_roster(self.me, punt)  # players back from IL slots in, who's cut out then (M21)
            rate, line, days = None, self.p_week, lambda ks: self._days(ks, self.week_end, until=until)
        zero = np.zeros(len(RAW))
        if span:
            ks = play(opp)  # their lines with the lift averaged over the season (they set their own lineups)
            g = dict(zip(ks, line.loc[ks, RAW].to_numpy(float)))
            theirs = sum((w * g[k] for d, x in days(ks).items() for k, w in zip(x, self._subs(x, [self._plays(k, d, rate) for k in x]))), zero)
            odds = lambda v: self._concede(cat_probs(v, theirs, self.cats), punt)
        else:
            _, _, odds = self._matchup(opp, punt)
        # a game each, with the lift from teammates out that day (self.lift) in place of the one `line` averages in
        day, avg = days(mine), self.ahead if span else self._boost(self.week_end)
        flat = dict(zip(mine, (line.loc[mine, RAW] - avg.reindex(mine, fill_value=0)[RAW]).to_numpy(float)))
        bump = collections.defaultdict(list)  # {player: [(from, until, who's out, MIN + RAW)]}
        for (k, j, a, b), x in zip(self.lift.index, self.lift[["MIN"] + RAW].to_numpy(float)):
            if k in flat:
                bump[k].append((a, b, j, x))
        # today's starters in games under way (_held, M18): set (they hold their slots: plays 1), and their stats are
        # in the score so far (a line of 0)
        lock = {(d, k) for d, ks in day.items() if d == self.today for k in ks if self._locked(k)}
        per = {(d, k): zero if (d, k) in lock else flat[k] + sum((x[1:] for a, b, _, x in bump[k] if a <= d < b), zero)
               for d, ks in day.items() for k in ks}
        pl = {x: 1.0 if x in lock else self._plays(x[1], x[0], rate) for x in per}
        one = {x: pl[x] * per[x] for x in per}
        idx = [self.cats.index(c) for c in cats if c in self.cats and c not in punt]
        goal = (lambda p: p[..., idx].mean(-1)) if idx else self._wscore
        fit = {}

        def fits(ks):
            if (key := frozenset(ks)) not in fit:
                fit[key] = len(lineup(self.slots, [self._pos(k) for k in ks])) == len(ks) if self.slots else len(ks) <= self.lineup
            return fit[key]
        memo = {}

        def dv(d, on):  # the day's expected line with `on` (day order) starting and the bench who don't fit filling in
            if (key := (d, frozenset(on))) not in memo:
                ks = on + [k for k in day[d] if k not in on and not fits(on + [k])]
                memo[key] = sum((w * per[d, k] for k, w in zip(ks, self._subs(ks, [pl[d, k] for k in ks]))), zero)
            return memo[key]
        new = lambda d, j, k: [x for x in day[d] if x == k or (x != j and x in plan[d])]  # plan[d] after a change
        base = {d: self._lineup(ks) for d, ks in day.items()}
        plan = dict(base)
        v0 = sum((dv(d, on) for d, on in base.items()), zero)
        v, now = v0, float(goal(odds(v0)))
        while True:
            moves = [(d, j, None) for d, on in plan.items() for j in on if (d, j) not in lock] if bench else []
            moves += [(d, j, k) for d, ks in day.items() for k in ks if k not in plan[d]
                      for j in [None, *plan[d]] if (d, j) not in lock and fits([x for x in plan[d] if x != j] + [k])]
            D = np.array([dv(d, new(d, j, k)) - dv(d, plan[d]) for d, j, k in moves]).reshape(-1, len(RAW))
            g = goal(odds(v + D)) if moves else np.zeros(0)
            if not moves or g.max() <= now + 1e-6:
                break
            d, j, k = moves[i := int(g.argmax())]
            plan[d] = new(d, j, k)
            v, now = v + D[i], float(g[i])
        return {"opp": opp, "day": day, "base": base, "plan": plan, "one": one, "dv": dv, "bump": bump, "rate": rate, "fits": fits,
                "odds": odds, "goal": goal, "v0": v0, "v": v, "now": now, "moves": moves, "g": g, "lock": lock}

    def start_sit(self, opp=None, punt=(), cats=()):
        """This week's lineups (Q18): each day left with a game for you, who starts for the most category wins vs opp
        (_week_plan; cats: playing for those categories only) and who sits. in / out = who starts or sits only on this
        plan, against starting the best per-game values; bench = sits with a slot open he fits; why = the category odds
        that day's changes move; slots = [slot, starter]; options = each player with a game, the chance he plays (plays,
        %), what he gets from teammates out that day (lift: MIN, PTS, out: who, back when, term), and what changing his call does (flip:
        points of the goal, with = who he'd swap with), locked: his game today has tipped off (M18: he keeps his slot,
        his stats are in the score so far). gain: win-% points over per-game value's lineups;
        cats: {category: [odds on per-game value's lineups, on these]} that move; goal / goal_gain: the categories
        played for and theirs. None with no opponent or schedule."""
        if not (r := self._week_plan(opp, punt, cats=cats)):
            return None
        odds, dv, fits, plan, base = r["odds"], r["dv"], r["fits"], r["plan"], r["base"]
        name = lambda k: self.p.at[k, "PLAYER_NAME"]
        names = lambda ks: [name(k) for k in ks]
        p, p0 = odds(r["v"]), odds(r["v0"])
        def lift(d, k):  # what he gets that day from teammates out (minutes, or shots without them): half a minute or point
            x = [(j, m) for a, b, j, m in r["bump"][k] if a <= d < b]
            t = sum((m for _, m in x), np.zeros(len(RAW) + 1))[[0, 1 + RAW.index("PTS")]]
            out = [{"name": name(j), "return": self.inj[j]["return"], **self._term(j)} for j, _ in x]
            return {"lift": {"MIN": round(float(t[0]), 1), "PTS": round(float(t[1]), 1), "out": out}} if t.max() >= 0.5 else {}
        flip = {}  # (date, player): the best change of his call, (its goal, who it swaps him with)
        for (d, j, k), x in zip(r["moves"], r["g"]):
            for a, b in ((j, k), (k, j)):
                if a and x > flip.get((d, a), (-1,))[0]:
                    flip[d, a] = (float(x), b)
        days = []
        for d, ks in sorted(r["day"].items()):
            on, was = plan[d], base[d]
            sit = [k for k in ks if k not in on]
            why = 100 * (p - odds(r["v"] - dv(d, on) + dv(d, was)))
            held = lineup(self.slots, [self._pos(k) for k in on]) if self.slots else {}
            days.append({"date": d, "start": names(on), "sit": names(sit), "in": names(k for k in on if k not in was),
                         "out": names(k for k in was if k not in on), "bench": names(k for k in sit if fits(on + [k])),
                         "why": {c: round(float(x), 1) for c, x in zip(self.cats, why) if abs(x) >= 0.5},
                         # every starting slot in Yahoo's order, None where nobody starts (Nov 2: no SF with George
                         # and Black resting)
                         "slots": [[sl, name(on[held[i]]) if i in held else None] for i, sl in enumerate(self.slots)],
                         "options": [{"name": name(k), "start": k in on, "plays": round(100 * self._plays(k, d, r["rate"])),
                                      **({"locked": True} if (d, k) in r["lock"] else {}),
                                      **lift(d, k), **({"flip": round(100 * (flip[d, k][0] - r["now"]), 1),
                                          "with": flip[d, k][1] and name(flip[d, k][1])} if (d, k) in flip else {})}
                                     for k in ks]})
        goal = [c for c in cats if c in self.cats and c not in punt]
        return {"opponent": r["opp"], "gain": round(100 * float(self._wscore(p) - self._wscore(p0)), 1), "days": days,
                "cats": {c: [round(100 * float(a), 1), round(100 * float(b), 1)] for c, a, b in zip(self.cats, p0, p)
                         if abs(b - a) >= 0.005},
                **({"goal": goal, "goal_gain": round(100 * (r["now"] - float(r["goal"](p0))), 1)} if goal else {})}

    def _clears(self, k):
        """{k: the day his waiver claim clears} ("Jan 2" read on Dec 30 is next year), {} when he isn't on waivers:
        a claim only plays from then (starts(), week_vec())."""
        w = self.meta.get(k, {}).get("waiver")
        if not w:
            return {}
        d = datetime.strptime(f"{w} {self.today[:4]}", "%b %d %Y").date()
        return {k: (d.replace(year=d.year + 1) if d < date.fromisoformat(self.today) - timedelta(60) else d).isoformat()}

    def add_drop(self, add, drop=None, punt=(), opp=None, day=None):
        """Any add and any drop, scored like the trade builder (Q7). 9-cat: the change in your season win % (outlook())
        and in this week's (this_week()'s maths vs opp, else the saved opponent: starts() from the day a claim clears,
        players in IL slots sit). This week's move is made on `day` (ISO), else on its best day (_timed, Q11: the
        drops play until the day before); with no adds left this week (adds()) it waits for next week (`capped`,
        no week numbers). Scored on today's roster, like the Waivers table (_gains()), not on top of your free
        moves: it answers "what does this move do", and the best free move would already have made it.
        IL slots as in _after(): an injured add parks in a free IL slot; dropping a parked player frees no active
        spot, so whoever you'd miss least goes too (healthy players first, like _drops(); never the add). drop None:
        an open active spot, else that same pick. A spot the move leaves open stays open (`open`), since filling it
        is another add. Points: value per game, the add's minus the drop's. KeyError unless add is a free agent and
        drop is on your roster (AI tool input comes through here)."""
        k, me = self.find(add), self.rosters[self.me]
        if k not in self.fa:
            who = self.owner(k)
            raise KeyError(f"{self.names([k])[0]} " + (f"is on {who}: that takes a trade" if who else "isn't a free agent"))
        d = self.find(drop) if drop else None
        if d is not None and d not in me:
            raise KeyError(f"Not on your roster: {self.names([d])[0]}")
        il = [x for x in self.il[self.me] if x != d]
        il += [k] * self._stash(k, il)  # B5: the rule free_moves() and Waivers use
        keys = [x for x in me if x != d] + [k]
        n = len(keys) - len(il) - max(self.roster_n, len(self._playing(me)))  # active players over the limit
        cands = [x for x in self._drops([x for x in keys if x not in il], keep=[k]) if x]
        cands = cands or [x for x in keys if x not in il and x != k]
        if n > 0 and self.mode == "points":
            auto = self.value(cands, punt).nsmallest(n).index.tolist()
        elif n > 0:
            c = self._contrib(self.me, cands, punt)
            auto = sorted(c, key=c.get)[:n]
        else:
            auto = []
        keys = [x for x in keys if x not in auto]
        cap = self.adds()
        out = {"add": {**self.player(k, punt), "parks": k in il}, "adds": cap, "capped": bool(cap) and not cap["left"],
               "drop": [{**self.player(x, punt), "auto": x != d, "parked": x in self.il[self.me]} for x in [d] * bool(d) + auto],
               "open": max(-n, 0)}
        if self.mode == "points":
            net = float(self.value([k], punt).sum() - self.value([d] * bool(d) + auto, punt).sum())
            return {**out, "unit": "value", "net": round(net, 2), "week": None,
                    "verdict": "accept" if net > 0.3 * self.u else "decline" if net < -0.3 * self.u else "toss-up"}
        ov = {self.me: self.vec(keys, il)}
        b, a = self.outlook(self.me, punt=punt), self.outlook(self.me, ov, punt)
        sb, sa = self.standings(), self.standings(ov)
        net, pct1 = 100 * (a["win"] - b["win"]), lambda x: round(100 * float(x), 1)
        cats = {c: {"season": [pct1(b["odds"][c]), pct1(a["odds"][c])], "week": None} for c in self.cats}
        week, opp = None, self._opponent(opp)
        if day is not None and not re.fullmatch(r"\d{4}-\d\d-\d\d", str(day)) or (day or "") > self.week_end:
            raise KeyError(f"Pick a day from today to {self.week_end} (YYYY-MM-DD), not {day}")
        if opp and not out["capped"]:
            _, left, odds = self._matchup(opp, punt)
            (act, ends), claim = self._week_roster(self.me, punt), self._clears(k)  # with players back from IL slots (M21)
            gone = [x for x in [d] * bool(d) + auto if x in act]  # active players leaving: they play until the move
            p0 = odds(left[self.me])
            if k in il:  # parked: the drops go now (one locked today, tomorrow: M18), and he plays from his return in
                # place of who's cut then, as a player already in an IL slot does (B5: _week_roster on the roster after it,
                # M21; he used to sit the whole week)
                stay = {x: self.today for x in gone if self._locked(x)}
                lc = self._copy({self.me: keys}, [x for x in self.fa if x != k], {self.me: set(il)})
                back, ends2 = lc._week_roster(self.me, punt)
                when, start = None, ({k: claim[k]} if claim else {})
                after = back + [x for x in stay if x not in back]
                until = {**{x: e for x, e in ends2.items() if x not in gone}, **stay}
            elif day:
                when = max(day, self.today, self.matchup.get("from", ""), claim.get(k, ""), self._move_day(gone))
                start, until = {k: when}, self._handoff(ends, ends, gone, k, when)
                after = act + [k]
            else:
                _, when, start, until = self._timed(act, k, gone, odds, float(self._wscore(p0)), None, ends, ends)
                after = act + [k]
            p1 = odds(self.week_vec(after, start, until))
            for c, x0, x1 in zip(self.cats, p0, p1):
                cats[c]["week"] = [pct1(x0), pct1(x1)]
            week = {"opponent": opp, "net": round(100 * float(self._wscore(p1) - self._wscore(p0)), 1) + 0.0,
                    "win": [pct1(match_prob(p0, self.cats, WEEK_LOAD)), pct1(match_prob(p1, self.cats, WEEK_LOAD))],
                    "score": [round(float(p0.sum()), 1), round(float(p1.sum()), 1)],
                    **({"claim": claim[k]} if claim else {}), **({"day": when} if when else {}),
                    "today": max(self.today, self.matchup.get("from", ""))}
            if self.sched:
                g0, g1 = self.starts(act, until=ends), self.starts(after, start, until)
                week["games"] = {"add": g1[k], "drop": sum(g0[x] - g1[x] for x in gone), "kept": sum(g1[x] for x in gone),
                                 "you": [sum(g0.values()), sum(g1.values())]}
        return {**out, "unit": "win%", "net": round(net, 1) + 0.0, "win": pct1(b["win"]), "win_after": pct1(a["win"]),
                "rank": sb[self.me]["rank"], "rank_after": sa[self.me]["rank"], "cats": cats, "week": week,
                "verdict": "accept" if net > MOVE * self.s else "decline" if net < -MOVE * self.s else "toss-up"}

    def playoff_weeks(self):
        """The fantasy playoffs' matchup weeks as (week, start, end): settings' playoff_weeks (yahoo.py reads the
        snapshot's Playoffs line), else the season schedule's weeks with no pairings after the last week with some."""
        sched = self.snap.get("schedule", ())
        nums = self.snap["settings"].get("playoff_weeks")
        if nums is None:
            last = max((w["week"] for w in sched if w["matchups"]), default=None)
            nums = [w["week"] for w in sched if last is not None and w["week"] > last and not w["matchups"]]
        return [(w["week"], w["start"], w["end"]) for w in sched if w["week"] in nums]

    def planner(self, punt=(), n=3):
        """Schedule planner (Q4): your active players' NBA games per day for the rest of this matchup week and the
        next n (weeks()), each day's starters (the best `lineup` with a game, as starts() picks them but on the build's
        value, Q13; for the most category wins vs the week's opponent, `vs`, when one is known: _week_plan, swaps only), who sits on an overflow
        day, and open slots (fewer than `lineup` of yours play while the NBA does: room to stream). Players in IL slots sit, injured players count from their return date;
        one back from an IL slot this week plays from his first game in place of who's cut then (_week_roster, M21) and has
        a row (sched None for later weeks). A week's starts and sit are the sums of its rows' (B4): a game under way
        today counts on neither (the day's players / sit still list him), a cut player's games after his cut on neither. teams: every NBA
        team's games in those weeks, on your open days (games a streamer from there would start), and in the fantasy playoff weeks, with your
        players there and the best healthy free agent on your build (effective()). Starters fill the league's slots by
        position (_lineup), so a player can sit with a spot open (fill: the positions that could take it).
        ponytail: today's roster in every week, a player tagged out with no return date still counts, and the teams'
        open-day counts don't check the streamer's position."""
        if not self.sched:
            return {"lineup": self.lineup, "slots": self.slots, "weeks": [], "teams": [], "roster": [], "il": [], "playoffs": [], "adds": self.adds()}
        me = self.rosters[self.me]
        play, name = self._playing(me), lambda ks: [self.p.at[k, "PLAYER_NAME"] for k in ks]
        spans = [(max(self.today, self.matchup.get("from", "")), self.week_end)] + self.weeks(n)
        sched = self.snap.get("schedule", ())
        nba = {t: ds for t, ds in self.sched.items() if t in set(self.nba.values())}  # no All-Star or exhibition teams
        busy = collections.Counter(d for ds in nba.values() for d in ds)  # NBA teams playing each day
        weeks, opened, starts = [], [], collections.defaultdict(list)
        eff, val = self.effective(punt), self.value(punt=punt)
        now = self._week_roster(self.me, punt)  # this week: players back from IL slots in, who's cut out then (M21)
        rows = list(play) + [k for k in now[0] if k not in play]  # B4: one back from an IL slot this week has a row too
        for i, (s, e) in enumerate(spans):  # starters for the most category wins vs each week's opponent (_week_plan)
            wp = self._week_plan(punt=punt, span=(s, e) if i else None, bench=False)
            day, days, c, g = self._days(now[0] if i == 0 else play, e, s, until=now[1] if i == 0 else None, by=val), [], collections.Counter(), collections.Counter()
            d = date.fromisoformat(s)
            while d.isoformat() <= e:
                x, ks = d.isoformat(), day.get(d.isoformat(), [])
                fit = self._lineup(ks)
                on = wp["plan"].get(x, fit) if wp else fit
                left = [k for k in ks if x != self.today or not self._locked(k)]  # under way: not a game left (M18)
                c.update(k for k in on if k in left)
                g.update(left)
                o = self.lineup - len(fit) if busy[x] else 0
                days.append({"date": x, "nba": busy[x], "players": name(on), "sit": name(k for k in ks if k not in on),
                             "open": o, **({"fill": self._fill(fit)} if o and self.slots else {})})
                d += timedelta(1)
            for k in rows:  # B4: the rows are the week's counts, so Starts and Sit are their sums (an IL player back this
                starts[k].append({"games": g[k], "starts": c[k]} if i == 0 or k in play else None)  # week only in it)
            opened.append({x["date"] for x in days if x["open"]})
            weeks.append({"start": s, "end": e, "week": next((w["week"] for w in sched if w["start"] <= s <= w["end"]), None),
                          **({"vs": wp["opp"]} if wp else {}),
                          "games": sum(len(x["players"]) + len(x["sit"]) for x in days),
                          "starts": sum(c.values()), "sit": sum(g.values()) - sum(c.values()),
                          "open": sum(x["open"] for x in days), "days": days})
        po = self.playoff_weeks()
        within = lambda ds, s, e: sum(s <= d <= e for d in ds)
        healthy = [k for k in self.fa if self.meta.get(k, {}).get("status", "") not in OUT]
        fa = {}
        for k in sorted(healthy, key=eff.get, reverse=True):
            fa.setdefault(self.nba.get(k), k)
        teams = [{"nba": t, "weeks": [within(ds, s, e) for s, e in spans], "open": [len(o & set(ds)) for o in opened],
                  "playoffs": [within(ds, s, e) for _, s, e in po], "mine": name(k for k in me if self.nba.get(k) == t),
                  **({"fa": {"name": self.p.at[fa[t], "PLAYER_NAME"], "value": round(float(val[fa[t]]), 2)}} if t in fa else {})}
                 for t, ds in nba.items()]
        for t in teams:
            t["total"] = sum(t["weeks"])
        teams.sort(key=lambda t: (-t["total"], -sum(t["playoffs"]), t["nba"]))
        roster = [{**self.player(k, punt), "sched": starts[k], **({"il": True} if k not in play else {})}
                  for k in sorted(rows, key=val.get, reverse=True)]
        return {"lineup": self.lineup, "slots": self.slots, "weeks": weeks, "teams": teams, "roster": roster,
                "il": [{"name": self.p.at[k, "PLAYER_NAME"], **({"return": self.inj[k]["return"]} if self.inj.get(k, {}).get("return") else {})}
                       for k in me if k not in rows],
                "playoffs": [{"week": w, "start": s, "end": e} for w, s, e in po], "adds": self.adds()}

    def _fill(self, on):
        """Positions a streamer could play to start next to these starters (an open slot he fits, maybe after moving
        theirs)."""
        pos = [self._pos(k) for k in on]
        return [x for x in POS if len(lineup(self.slots, pos + [{x}])) > len(on)]

    def movers(self, punt=(), limit=10):
        """Hot and cold runs worth acting on. gap = value on your build over the last FORM_DAYS (this season without
        box scores) minus rest of season, past trend_cut() for his games in it (M8). edge = Hot & cold's price (League.hot_market: Yahoo's current
        rank counts more than in trades, Nov 2) against his worth to you (his rank on your build, effective(), on the
        same curve), in % as priced(); past fair's 15% either way.
        Sell high: your players running hot that the market prices above their worth to you. Buy low: players on
        other teams running cold, worth more to you than the waiver level over the games they'll play (Nov 2: on
        per-game value Jalen Williams, out till March and 20% plays, was listed), that the market prices below it. Nov 1 the
        gap alone listed SGA (market #4) to buy low and Anthony Black (#122) to sell high. Hot pickups: free agents
        running hot that would raise your win % by MOVE (_gains(), as Waivers; points: value over your weakest
        healthy player), back for 90%+ of the games left: the gap alone listed Mo Bamba, below the waiver level."""
        form = self.value(punt=punt, frame="form")
        gap = form - self.value(form.index, punt)
        need = trend_cut(self.form_gp.reindex(gap.index)) * self.u  # fewer games in the window, a bigger gap (M8)
        hot, cold = gap[gap >= need], gap[gap <= -need]
        mk, cut = self.hot_market, TIERS["fair"][3]  # not the trade market (HOT_CHASE)
        mine = mk.index.isin(list(self.here)) | mk.index.isin(list(self.meta))  # ranked as the market is
        eff = self.effective(punt)
        worth = 100 * MARKET ** (eff.reindex(mk.index[mine]).rank(ascending=False, method="first") - 1)
        floor = float(eff[self._waiver_level(eff)].mean())  # the best free agents, over the games they'll play too
        edge = (100 * (mk["value"] - worth) / np.maximum(mk["value"], worth)).reindex(gap.index)
        others = {k for t, ks in self.rosters.items() if t != self.me for k in ks}
        sell = edge[hot.index[hot.index.isin(self.rosters[self.me])]]
        sell = sell[sell >= cut].nlargest(limit)
        buy = edge[[k for k in cold.index if k in others and eff.get(k, -np.inf) > floor]]
        buy = buy[buy <= -cut].nsmallest(limit)
        # a pickup has to play: not one out past a tenth of the games left (Champagnie, out till Feb 25, was listed)
        adds = [k for k in hot.index if k in self.fa and self.back.get(k, 1.0) >= 0.9]
        roster, gain = self.rosters[self.me], pd.Series(dtype=float)
        drops = self._drops(roster, self.chips(roster), team=self.me)  # never a trade chip, as in free_moves(); PARK (B5)
        if adds and self.mode == "points" and any(k not in (None, PARK) for k in drops):
            gain = self.value(adds, punt) - self.value([k for k in drops if k not in (None, PARK)], punt).min()
        elif adds and drops:
            gain = pd.Series(self._gains(self.me, roster, adds, drops, punt).max(1), adds)
        pick = gain[gain >= (0.3 * self.u if self.mode == "points" else MOVE * self.s)].nlargest(limit)
        fmt = lambda s, k2: [{**self.player(k, punt), "gap": round(float(gap[k]), 2), k2: round(float(v), 1),
                              "price_rank": int(mk.at[k, "rank"])} for k, v in s.items()]
        window = f"the last {FORM_DAYS} days" if self.logs is not None else "this season"
        return {"sell_high": fmt(sell, "edge"), "buy_low": fmt(buy, "edge"), "pickups": fmt(pick, "gain"),
                "unit": "value" if self.mode == "points" else "win%",
                "window": window, "note": f"Only players with {FORM_GP}+ games in it. Small samples swing hard."}

    def dropped(self, punt=(), limit=10):
        """Players dropped in the snapshot's transactions (newest first) that nobody has picked up since."""
        out, seen = [], set()
        for t in self.snap.get("transactions", ()):
            name = t.get("dropped") or (t["player"] if t["type"] == "drop" else None)
            k = self.find(name, strict=False) if name else None
            if k and k not in seen and not any(k in ks for ks in self.rosters.values()):
                seen.add(k)
                out.append({**self.player(k, punt), "by": t["team"], "when": t["when"]})
        return out[:limit]

    def state(self, punt=()):
        return {"league": self.snap.get("name", "League"), "mode": self.mode, "scoring": self.scoring, "cats": self.cats,
                "me": self.me, "teams": list(self.rosters), "fetched": self.snap.get("fetched", ""), "demo": self.snap.get("demo", False),
                "pulled": self.snap.get("pulled", ""), "rolled": self.rolled,  # snapshot time; its week is over
                "adds": self.adds(),  # adds used and left this matchup week (None: no weekly cap)
                "matchup_days": [max(self.today, self.matchup.get("from", "")), self.week_end],  # days a move can still count
                "sources": self.sources(),  # when stats, box scores, schedule, injuries, projections, news were fetched
                "roster": sorted((self.player(k, punt) for k in self.rosters[self.me]), key=lambda p: -p["value"]),
                "profile": self.profile() if self.mode != "points" else None,
                "il_plan": self.il_plan(punt),  # who to cut once your IL players are back
                "waivers": {**self.waivers(punt), "dropped": self.dropped(punt)}, "movers": self.movers(punt)}

    def sources(self):
        """freshness() of the caches this copy was built from (news live: news() reads its index on each call).
        [] for a League built directly (tests), which reads no caches."""
        return [] if self.loaded is None else freshness(times=self.loaded)

    def il_plan(self, punt=()):
        """Who to cut once your players in IL slots are back (Martin, Nov 2: "replace the right person, not just the
        worst player"): every way to keep your active spots' worth of your active players plus them, healthy, scored on
        your build like the season numbers. {back: [{name, return}], cut: the best set's leftovers, gain: its win % over
        the season numbers' own rule (_return(): each one back pushes out the weakest by effective value), rule: who that
        rule cuts}; None with nobody coming back or nothing to cut. Nov 3: it keeps Keegan Murray and cuts Anthony
        Black, a guard on a guard-heavy roster, +0.2; the rule matched the best cut on 7 of 9 teams. Categories only:
        positions move season numbers 0.0-0.1% (This week's start/sit fills them)."""
        if not (c := self._il_cut(punt)):
            return None
        back, cut, gain, rule = c
        return {"back": [{"name": self.p.at[k, "PLAYER_NAME"], "return": self.inj.get(k, {}).get("return")} for k in back],
                "cut": self.names(cut), "gain": gain, "rule": self.names(rule)}

    def _il_cut(self, punt=()):
        """il_plan()'s keys: (back, cut, gain, rule cuts), None as there. This week reads the cut too (_week_roster)."""
        if self.mode == "points":
            return None
        me, act = self.me, self._playing(self.rosters[self.me])
        back = sorted((k for k in self.il[me] if self.back.get(k, 0) > 0), key=lambda k: (self.inj.get(k, {}).get("return", ""), k))
        pool, n = act + back, len(act) + self._room(me)
        if not back or len(pool) <= n:
            return None
        W = np.stack([self._cr[self._ix[k]] / self.back[k] if k in back else self.rows[k] for k in pool])  # healthy weeks
        keep = np.array(list(itertools.combinations(range(len(pool)), n)))
        _, O = self._others(me)
        win = 100 * self._score(self._concede(cat_probs(W[keep].sum(1)[:, None], O[None], self.cats), punt)).mean(-1)
        best = set(keep[win.argmax()])
        rule, ev = list(act), self.effective()  # the season numbers' rule, as _return() runs it: open spots first
        for i, q in enumerate(sorted(back, key=lambda k: -self._hv[self._ix[k]])):
            w = min(rule, key=ev.get)
            if i < self._room(me) or self._hv[self._ix[q]] > ev[w]:
                rule = [k for k in rule if i < self._room(me) or k != w] + [q]
        at = {k: i for i, k in enumerate(pool)}
        r = win[np.flatnonzero((np.isin(keep, [at[k] for k in rule])).all(1))[0]]
        return (back, [k for i, k in enumerate(pool) if i not in best], round(float(win.max() - r), 1),
                [k for k in pool if k not in rule])

    def team_roster(self, team, punt=()):
        return sorted((self.player(k, punt) for k in self.rosters[team]), key=lambda p: -p["value"])

    def wants(self, team):
        """What `team` wants from you (Trades): every player's worth to it in win %, its win % with him minus with the
        best healthy free agent (`spot`) in his spot, both through _after() as in a trade (drops, IL slots). One
        yardstick for both sides, so in a 1-for-1 its gain is about yours minus its own (Oct 29, real league: within
        0.3 on average, 0.8 at worst, over 210 deals with three teams). give: your players, best for it first, with who
        it'd drop; keep: its own players (low or negative = it'd let him go). Real odds, nothing conceded: a player
        strong where it's far behind barely moves it, so a punt shows without guessing it (value on its build() misread
        it: langangsters are weak in BLK and REB, Kessler is -3.6 there and among their best gets)."""
        if self.mode == "points" or team not in self.rosters or team == self.me:
            raise KeyError(f"What a team wants needs a category league and another team: "
                           f"{', '.join(t for t in self.rosters if t != self.me)}")
        name = lambda k: self.p.at[k, "PLAYER_NAME"]
        fa = self._waiver_level(self.effective())[:1].tolist()  # healthy: an injured one would sit in an IL slot
        win = lambda r: 100 * self.outlook(team, {team: self.vec(r[0], r[3], team)})["win"]
        spot, now = win(self._after(team, [], fa)), 100 * self.outlooks[team]["win"]
        give = [{"name": name(k), "worth": round(win(r) - spot, 1), "they_drop": self.names(r[1])}
                for k in self.rosters[self.me] for r in [self._after(team, [], [k])]]
        keep = {name(k): round(now - win(self._after(team, [k], fa)), 1) for k in self.rosters[team]}
        return {"team": team, "build": self.build(team), "spot": self.names(fa),
                "give": sorted(give, key=lambda p: -p["worth"]), "keep": keep}


def switched(snap, team):
    """This week's matchup once `team` is yours (/api/me): the week stays (its dates, `from`, Yahoo's games played /
    left, the totals); the opponent was the old team's, so it's the new team's from the season schedule (none without
    one: This week asks). B1 (audit #13): the switch saved no matchup, so `from` fell back to today and the nights
    from the snapshot's `from` to yesterday were in neither the score so far (_so_far) nor the games left (game_days),
    and an opponent picked after it saved that until Refresh."""
    m = {k: v for k, v in (snap.get("matchup") or {}).items() if k != "opponent"}
    if not m:
        return None
    wk = next((w for w in snap.get("schedule", ()) if w["start"] <= (m.get("week_start") or "") <= w["end"]), {})
    opp = next((t for pr in wk.get("matchups", ()) if team in pr for t in pr if t != team), None)
    return {**m, **({"opponent": opp} if opp else {})}


def load(path=DATA / "league.json", refetch=()):
    """The league from the snapshot, with every cache refreshed once past its window. refetch: "injuries" and "live"
    (scores()) now, "stats" (this season's stats and box scores) once 15 minutes old: Refresh (Nov 1: a 9:57pm Refresh
    read 1:06pm stats, before the day's games). It keeps each cache's file time (`loaded`), so sources() shows what this copy was
    built from."""
    if not Path(path).exists() and Path(path) == DATA / "league.json":  # first run: the demo league (yahoo.demo()
        import yahoo                                                    # writes there, never to another path)
        yahoo.demo()
    live = scores("live" in refetch)
    hours = 0.25 if "stats" in refetch else 12
    logs = gamelogs(CUR, hours)
    # a regular-season game gone final that the box scores don't have yet (M18): this season's stats and box scores
    # again (together, so their games agree, as Refresh does) once half an hour old, not on the 12-hour clock
    if logs is not None and {int(i) for i, g in live.get("games", {}).items()
                             if i.startswith("002") and g["status"] == 3} - set(logs.GAME_ID):
        hours = min(hours, 0.5)
        logs = gamelogs(CUR, hours)
    lg = League(json.loads(Path(path).read_text(encoding="utf-8")), cur=fetch(CUR, max_age_hours=hours), sched=schedule(),
                inj=injuries("injuries" in refetch), logs=logs, proj=espn(), news=news, live=live)
    news_index()  # news() reads it when a card or the News panel asks (My team, at once); fetch it now so its time shows
    lg.loaded = {name: mtime(DATA / name) for _, _, name, _, _ in SOURCES if name != "news_index.json"}
    return lg


NOTES = DATA / "notes.md"


def note(text=""):
    """The AI GM's notes for Claude Code (Martin, Nov 3: "can it write stuff down for you to read and see if there's
    stuff to fix"): what Martin flags in the chat that the model gets wrong or misses, appended to data/notes.md as an
    open item (- [ ]), never a code change. Claude Code reads the open ones before new work and ticks them. No text:
    the open notes."""
    text = " ".join(text.split())
    if text:
        DATA.mkdir(exist_ok=True)
        with open(NOTES, "a", encoding="utf-8") as f:
            f.write(f"- [ ] {datetime.now():%Y-%m-%d %H:%M} {text}\n")
    open_ = [x[6:] for x in (NOTES.read_text(encoding="utf-8").splitlines() if NOTES.exists() else ()) if x.startswith("- [ ] ")]
    return {"saved": text, "file": "data/notes.md", "open": open_} if text else {"open": open_}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["team", "trade", "ideas", "search", "waivers", "movers", "roster", "week",
                                    "strategy", "news", "compare", "schedule", "move", "sources", "wants", "lineup",
                                    "note"])
    ap.add_argument("--give", default="")
    ap.add_argument("--get", default="")
    ap.add_argument("--mode", default="fair", choices=["fair", "finesse"])
    ap.add_argument("--team", help="limit ideas / search / show roster for one team")
    ap.add_argument("--punt", default="", help='e.g. "FT%%,TOV"')
    ap.add_argument("--lock", default="", help="search: players never traded")
    ap.add_argument("--tier", default="fair", choices=list(TIERS), help="search: how likely they say yes")
    ap.add_argument("--goal", default="win", choices=["win", "cats", "team"])
    ap.add_argument("--cats", default="", help='search --goal cats: e.g. "STL,AST". lineup: play for these categories only')
    ap.add_argument("--vs", help="search --goal team: the team to beat. week, move, lineup: this week's opponent")
    ap.add_argument("--size", type=int, default=3, help="search: max players per side")
    ap.add_argument("--healthy", action="store_true", help="search: skip injured players you'd get")
    ap.add_argument("--shape", choices=["consolidate", "depth"])
    ap.add_argument("--max-overpay", type=float, help="search: most you'd overpay on the market, in %%")
    ap.add_argument("--outside", type=int, default=0, help="search: role players only, nobody in the market's top N")
    ap.add_argument("--players", default="", help='compare: 2 to 4 players, e.g. "A, B"')
    ap.add_argument("--add", default="", help="move: the free agent to add")
    ap.add_argument("--drop", default="", help="move: your player to drop (none: an open spot, else who you'd miss least)")
    ap.add_argument("--day", help="move: the day this week to make it, YYYY-MM-DD (default: its best day)")
    ap.add_argument("--no-free", action="store_true", help="trade, search: on today's roster, not after your waiver moves")
    ap.add_argument("--their-free", action="store_true", help="trade, search: the other team after its waiver moves too")
    ap.add_argument("--healthy-fa", action="store_true", help="injured free agents off waivers (Waivers' Healthy only)")
    ap.add_argument("--text", default="", help="note: what to write down for Claude Code (none: list the open notes)")
    a = ap.parse_args()
    if a.cmd == "note":  # no league needed
        return print(json.dumps(note(a.text), indent=1, ensure_ascii=False))
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]
    lg, punt = load(), punts(a.punt)
    lg = lg.healthy_fa() if a.healthy_fa else lg
    out = {"team": lambda: {**lg.profile(), "roster": lg.team_roster(lg.me, punt), "il_plan": lg.il_plan(punt)},
           "trade": lambda: lg.trade(split(a.give), split(a.get), punt, not a.no_free, a.their_free),
           "ideas": lambda: lg.ideas(a.mode, punt, a.team),
           "search": lambda: lg.search(split(a.give), split(a.get), split(a.lock), a.team, a.size, a.tier, a.goal,
                                       split(a.cats), a.vs, a.healthy, a.shape, punt, free=not a.no_free,
                                       their_free=a.their_free, max_overpay=a.max_overpay, outside=a.outside),
           "waivers": lambda: lg.waivers(punt),
           "movers": lambda: lg.movers(punt),
           "week": lambda: lg.this_week(a.vs, punt),
           "strategy": lambda: lg.strategy(a.size),
           "news": lg.roster_news,
           "compare": lambda: lg.compare(split(a.players), punt),
           "schedule": lambda: lg.planner(punt),
           "lineup": lambda: lg.start_sit(a.vs, punt, split(a.cats)),
           "move": lambda: lg.add_drop(a.add, a.drop or None, punt, a.vs, a.day),
           "sources": lg.sources,
           "roster": lambda: lg.team_roster(a.team or lg.me, punt),
           "wants": lambda: lg.wants(a.team)}[a.cmd]()
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
