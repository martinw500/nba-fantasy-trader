# Fantasy GM

A local app for your Yahoo NBA fantasy league. It tells you who to pick up and drop, tests trades from both sides, finds trades worth proposing, flags sell-high / buy-low players, and helps you pick categories to punt in 9-cat. Points leagues work too. An AI GM chat sits on top and uses the same numbers.

It only reads from Yahoo. You still make the actual moves on Yahoo.

## Setup (5 minutes)

```
pip install -r requirements.txt
python app.py
```

That opens http://localhost:8765 with a **demo league**: 12 fake teams drafted from real NBA stats. Everything works on it, so you can try the app before connecting Yahoo.

### Load your league without the Yahoo API

Since summer 2026, Yahoo only gives Fantasy API access to applications it approves, and approval has no timeline. Until then, load the league from a text snapshot:

1. Get a snapshot of the league as a text file. Instinct's "MCHC FANTASY BASKETBALL - FULL LEAGUE SNAPSHOT" works as-is: league settings, numbered team headings, one fixed-width row per player (`PG    Cade Cunningham    DET - PG,SG  [O: Out]   Pre 5  Cur 41  Ros 100%`), free-agent and waiver sections with roster % and claim dates, this week's matchup totals, transactions, Yahoo projections, adds used, waiver priority and the season schedule. Sections it doesn't recognise are skipped. A hand-typed list works too:
   ```
   ## Toronto blue jays
   Cade Cunningham
   Ivica Zubac (O)
   ```
2. Run `.venv/bin/python yahoo.py import mchc-fantasy-snapshot-2026-10-26.txt`. Your team comes from the file's `My team:` line; name it after the file to pick another (`... import file.txt "fatty zhou"`). It lists any player it couldn't match to NBA stats (usually rookies who haven't played yet).
3. Restart `python app.py`. For a new snapshot, drop the file in the repo folder and press **Refresh**: it imports the newest `*snapshot*.txt`.

Pick your team from the dropdown under the league name in the top bar. Snapshot files (`*snapshot*.txt`) are gitignored.

When the file lists free agents, those are used, with roster % and "claim by" dates in Waivers. Otherwise free agents are the best players nobody rosters.

### Connect your Yahoo league (needs approved API access)

1. Go to https://developer.yahoo.com/apps/create and make an app:
   - API permissions: **Fantasy Sports → Read**
   - Redirect URI: `https://localhost:8080`
2. Create a file called `.env` in this folder:
   ```
   YAHOO_CONSUMER_KEY=your key
   YAHOO_CONSUMER_SECRET=your secret
   YAHOO_LEAGUE_ID=12345
   ```
   The league ID is the number in your league's URL (`basketball.fantasysports.yahoo.com/nba/12345`).
3. Run `python yahoo.py`. The first time, a browser window asks you to log in and approve the app. The token is saved to `.env`, so you only do this once.
4. Restart `python app.py`, or hit **Refresh** in the app.

`python yahoo.py` saves your league to `data/league.json`: scoring settings, every roster with injury status, and the top 100 free agents. Run it (or press Refresh) whenever rosters change.

### Turn on the AI GM (optional)

Add `ANTHROPIC_API_KEY=sk-ant-...` to `.env` (key from https://console.anthropic.com) and restart. Everything except the chat works without it.

## Moving to a new computer

The code lives on GitHub (`martinw500/nba-fantasy-trader`). Two things are deliberately **not** in git: `.env` (your keys) and `data/` (cached stats + your league snapshot).

**Before you leave the old computer:**
```
git add -A
git commit -m "Add roadmap"
git push
```
Also copy `.env` somewhere private (password manager, or a USB stick) if you created one. Never commit it.

**On the new computer:**
1. Install [Python 3.11+](https://www.python.org/downloads/) (built on 3.13; tick "Add to PATH"), [Git](https://git-scm.com/), and Claude Code / VS Code.
   - Optional: [Node.js](https://nodejs.org/) (only the design-taste-frontend skill's scripts use it) and Chrome (for Claude's screenshot checks).
2. Get the code and install packages:
   ```
   git clone https://github.com/martinw500/nba-fantasy-trader.git
   cd nba-fantasy-trader
   pip install -r requirements.txt
   python test_model.py      # should print: ok
   ```
3. Put your `.env` back in the folder, or redo "Connect your Yahoo league" above.
4. `python yahoo.py` to pull your league, or skip it to use the demo.
5. `python app.py`. NBA stats download on their own the first time (`data/` is rebuilt automatically).
6. In Claude Code: open the folder and accept the prompt to install the project's plugins. Run `/mcp` → context7 → authenticate if you want it. The project skills (`/gm`, humanizer, impeccable, design-taste-frontend) come with the repo.

Then see [ROADMAP.md](ROADMAP.md) for what's next.

## Workflows

### "Who do I pick up / drop?"
**Waivers** tab. Every free agent is tried against every healthy player on your roster. **Suggested moves** shows the add/drop pairs that raise your win % the most. Injured players are never suggested as drops. The top 3 moves also show on **My team** under "Worth doing now".

### "Where do I stand?"
**My team** shows your projected finish and your win %: the share of categories you'd win in a typical week (see "The 9-cat matchup model" for One Win leagues). It also shows your odds in each category and your head-to-head odds against every team. **League** shows projected standings for everyone.

### "How's my matchup this week? Who do I stream?"
**This week** tab. Pick your opponent once (it's saved until the week ends on Sunday). You get the expected score (categories won–lost), your chance to win more categories than you lose, each category's projected total and odds with a call (*Safe*, *Chase*, *Lost*), and **Streaming adds**: free agents ranked by how much they raise this week's win %, with games left, who to drop, and what keeping him does to your season-long win %. Projections count each player's games left this week, but only the 10 best players with a game each day (Yahoo scores starters only, so a bench player's game counts only on a light day). Games left shows the same count. When the snapshot has them, Yahoo's own games left (and games played so far) show next to it. Yahoo counts the lineups as they're set now, so its number is usually lower. Players in IL slots sit, and a waiver claim counts only from the day it clears. A snapshot with a matchups section (or a Yahoo pull) fills in the opponent and the score so far, and when it was pulled after that day's games started, the rest of the week counts from the next day. Without one, totals start at zero (right on Mondays). CLI: `python model.py week --vs "Team"`.

### "Should I punt something?"
**My team** shows your odds in each category. Categories you win under 35% of weeks get a **Punt …** suggestion. You can also toggle any category with the **Punt** chips in the top bar. A punted column greys out in every table, and trades, waivers and ideas re-optimize with that category treated as a loss.

Classic pairings: big men punt FT%, high-usage guards punt TO, wings punt AST.

### "Is this trade good?"
**Trades** tab: check players under *You give*, pick their team, check players under *You get*, then **Evaluate**. You see:
- **Your win %**: before → after, with both rosters updated. If you take back more players, you drop your weakest; if you send more, you pick up the best free agent. So 2 scrubs for a star scores correctly.
- **Them**: their win % before → after. A trade can help both teams when the categories fit.
- **How it looks to them**: the deal on this season's hot and cold starts, which is what Yahoo's rankings show them. "Looks good" means they'll probably accept.
- **Projected finish**: your league rank before → after.
- Your odds change in each category.

**Ask AI GM for a pitch** writes a short message to send the other manager. **Save trade** keeps it for comparing later.

**Find any player** (top right) jumps to any rostered player: it switches to his team and checks him. The URL updates to `#trades?give=A,B&get=C` after each evaluation, so you can bookmark a trade. Last names work in the link too, as long as only one player has that name.

### "Find me a trade"
The finder panel sits under the builder. The players you check aim the search, and its title says which search it will run:
- Check **their** player: **Offers for X**, what you could give for him.
- Check **yours**: **Buyers for Y**, every team that might take him and what they'd send back.
- Check **both**: **Complete this trade**, deals that keep everyone checked and add players around them.
- Check **nobody**: **Open search** across every team.

The padlock on a player means the finder never uses him. Deals go up to 3-for-3. Toolbar:
- **Difficulty**, i.e. how likely they say yes. **Easy**: both teams gain and it looks good to them. **Fair**: both teams gain. **Finesse**: they *look* like the winner on hot starts but lose a little in reality, which is how sell-high works. **Hard**: you gain a lot, so it needs a good pitch.
- **Optimize for**: overall win %, your odds in chosen **categories**, or your head-to-head odds against **one team** (e.g. a playoff rival). The last two skip deals that cost you more than 0.4% win % (1% in One Win leagues).
- **Max size**, **Shape** (consolidate: you send more players; depth: you get more), **Healthy only**, and a team filter.

Columns sort on click. **Load** evaluates a deal in the builder, and **Save** keeps it.

Points leagues keep the simpler finder: Fair or Finesse, 1-for-1 up to 2-for-2, no checkboxes or locks.

**Improve this deal** appears after you evaluate a trade. **Throw-ins** keeps everyone in the deal and adds a player to either side. **Swaps** replaces one player at a time. Both use the finder's settings.

**Saved trades** (bottom of the tab) compares saved deals side by side. They're re-scored against today's rosters and your punts every time, so the numbers never go stale.

**Find a buyer** on My team and **Find offers** on Hot & cold's buy-low list run these searches directly.

### "Sell high / buy low"
**Hot & cold** tab. *Sell high* lists your players playing way above their real value; other managers see the hot start. *Buy low* lists good players on other teams starting cold. Early in the season the samples are tiny, and the app says so.

### "What does team X have?"
**League** tab: every team, its weak spots (what it would value less in a trade), and your projected head-to-head record against it. Click a team to see its roster, then **Trade with them**.

### Ask the AI GM
**AI GM** tab, or in Claude Code (see `/gm` below). Ask anything: "who should I drop?", "find me steals without losing assists", "should I punt FT%?". It calls the same engine as the rest of the app, so its numbers match the tables.

## How the numbers work

**Player lines (the projection).** Every player has a rest-of-season per-game line. The pre-season baseline blends the last two seasons (a game in 2024-25 counts half as much as one in 2025-26) and is pulled toward a fringe-rotation line by 25 games, so a 5-game season isn't taken as gospel and a player back from a lost season isn't treated as a rookie. The line then shifts toward this season as games pile up: this season's weight is `GP / (GP + 20)`. That's 13% after 3 games and half after 20. Rookies start from a fringe-roster line, so three hot games don't make them stars. These are home-made projections; expert ones (Hashtag Basketball, FantasyPros) aren't plugged in yet (ROADMAP, Build next #4).

**Plays %.** The share of games a player is expected to suit up for. It comes from games played over the last two seasons plus this one, shrunk toward 85% (one bad injury year shouldn't count fully), then cut by the injury tag (GTD 90%, O 70%, INJ 60%, IL 50%). When ESPN's injury report has an expected return date, that replaces the tag: the player counts for the share of remaining games after that date. Missed games are half-covered by a waiver-level fill-in, the way a bench player or streamer would cover them.

**Wk.** Games left this week (Monday to Sunday) from the NBA schedule, counting only games after a return date. `League.week_vec()` turns that into this week's expected totals. Trades and the season-long odds still use an average 3.4-game week.

**Injury news.** ESPN's public injury report (refreshed every 3 hours) adds the latest note and the expected return date. Hover an injury tag to read it. The league's own tag wins when they disagree.

**Value (the table columns).** z-scores against the top `teams × roster` players by minutes. FG% and FT% are weighted by volume, so a 60% shooter on 2 shots barely counts, and TO counts against you. This is the quick player-ranking view.

**The 9-cat matchup model (what the advice uses).**
1. Each team's expected weekly totals = Σ players' per-game lines × 3.4 games × their Plays %.
2. For every pair of teams and every category: the chance team A beats team B that week. This uses the gap between their totals and realistic weekly randomness (box-score noise, schedule swings, shooting variance on actual attempts).
3. What the week is worth in the standings. **Head-to-Head Categories** (the default, and Yahoo's): every category counts as a win or loss, so it's the expected share of categories won. **One Win**: only the matchup counts, so it's the chance of winning more categories than you lose (5 of 9). The format comes from the snapshot's `Scoring type:` line or Yahoo's `scoring_type`.
4. **Win %** = average of that against every other team: your projected standings win %. **Projected standings** rank every team by it.

Category win % moves about 2.5× less than One Win odds per move (a +2.7 trade there is about +1.1 here), so the cutoffs (trade tiers, verdicts, waiver moves) scale with the format (`CAT_SCALE` in `model.py`). Projected standings run tighter too: about 40–61% on the real league, against 26–77% scored as One Win.

A trade, add or drop is judged by how much it changes your win %, with both rosters updated: extra players get dropped, open spots get the best free agent. This is what makes it account for:
- **Your strengths and weaknesses.** Extra rebounds barely help if you already win REB 90% of weeks, and they help a lot if you're at 50%.
- **What everyone else has.** Odds are computed against every real roster in your league.
- **Punting.** A category you can't win stops mattering on its own. The Punt chips make it explicit: punted categories count as automatic losses, so the advice optimizes for the other categories.

**Now / How it looks to them.** The same line weighted harder toward this season (`GP / (GP + 10)`: 23% after 3 games), roughly what Yahoo's rankings show other managers. For trades, a player's look is counted over the games he actually plays (managers remember who missed a season) and never below what the best free agents look like. A manager taking back extra players feels the loss of the players they'd drop. Finesse trades and sell-high use it.

**Points leagues** use the league's scoring weights and per-game values (no matchup model).

**Honest limits:**
- The weekly-randomness settings are fitted to real 2025-26 box scores (`python backtest.py`: 20 simulated leagues, each week predicted from what was known that Monday). Category odds come true at the rate they say, within a few points (what a categories league runs on); whole-matchup odds within about 3 points. Still, read 55% vs 58% as "about the same".
- How "looks to them" works is a judgment call. There's no data on which offers real managers accept.
- Not modelled yet:
  - positions and roster slots (daily lineup limits)
  - this week's schedule in the weekly matchup itself (it's shown, and available as `week_vec`, but trades and odds use an average week)
  - keeper or dynasty value

The settings live at the top of `model.py`. Re-run `python backtest.py` after changing how players are projected; it prints predicted vs actual win rates.

## Using Claude Code on this project

Open the folder in Claude Code. These skills and plugins are set up for this project (in `.claude/`):

| What | Use it for | How |
|---|---|---|
| **gm** skill | Ask about your league from Claude Code. Claude refreshes Yahoo data and runs the engine. | `/gm who should I drop?` |
| **humanizer** skill | Make trade pitches and messages sound like you wrote them. | `/humanizer <text>` |
| **impeccable** skill | Design work on the UI: critique, polish, audit, new screens. Reads `PRODUCT.md` and `DESIGN.md`. | `/impeccable critique web/index.html`, `/impeccable polish` |
| **claude-md-management** plugin | Keep `CLAUDE.md` current after a session. | `/revise-claude-md` |
| **context7** plugin | Up-to-date docs for nba_api, yfpy, pandas while coding. | Automatic |
| **playwright** plugin | Claude clicks through the app in a real browser. Uses Microsoft Edge in the background (set in `~/.claude/settings.json`, since Chrome isn't installed). | Automatic |

**Turned off for this project** (installed for your account): `superpowers` (forces a long process on every task), `frontend-design` (impeccable covers it), `typescript-lsp` (no TypeScript here), the `humanizer` plugin (same version as the project skill above).

**Left out on purpose:** `pyright-lsp` (reports ~75 false errors on the pandas code, so every edit gets noisy), `commit-commands` and `feature-dev` (plain git and plain requests do the same job), `security-guidance` (an AI review on every turn, for an app that only runs on your machine).

### Typical dev loop

1. Describe what you want, e.g. *"Read ROADMAP.md and do Trade lab T3"*.
2. Claude runs `python test_model.py` and clicks through the app to check.
3. UI changes: `/impeccable polish web/index.html`.
4. Commit.
5. `/revise-claude-md` when something non-obvious came up.

## Files

| File | What |
|---|---|
| `app.py` | Local web server + JSON API (`python app.py`, add `--no-browser` to skip opening a tab) |
| `web/index.html` | The whole UI: one file, no build step |
| `model.py` | Engine: values, trades, finder, waivers, hot/cold, punts. Also a CLI (`python model.py --help`) |
| `backtest.py` | Checks the matchup odds against a real NBA season and prints predicted vs actual win rates |
| `yahoo.py` | Pulls your league from Yahoo (`python yahoo.py`) or builds the demo (`python yahoo.py demo`) |
| `ai.py` | AI GM: Claude with tools that call `model.py` |
| `test_model.py` | Self-check, no network: `python test_model.py` |
| `ROADMAP.md` | What's untested and what to build next |
| `PRODUCT.md`, `DESIGN.md` | Product and design decisions (used by impeccable) |
| `data/` | Cached NBA stats + your league snapshot (gitignored; delete to refetch) |
