# Fantasy GM

A local app for your Yahoo NBA fantasy league. It tells you who to pick up and drop, tests trades from both sides, finds trades worth proposing, flags sell-high / buy-low players, and helps you pick categories to punt in 9-cat. Points leagues work too. An AI GM chat sits on top and uses the same numbers.

It only reads from Yahoo. You still make the actual moves on Yahoo.

## Setup (5 minutes)

```
pip install -r requirements.txt
python app.py
```

That opens http://localhost:8765 with a **demo league**: 12 fake teams drafted from real NBA stats. Everything works on it, so you can try the app before connecting Yahoo.

### Connect your Yahoo league

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
**Waivers** tab. Every free agent is tried against every healthy player on your roster. **Suggested moves** shows the add/drop pairs that raise your weekly win odds the most. Injured players are never suggested as drops. The top 3 moves also show on **My team** under "Worth doing now".

### "Where do I stand?"
**My team** shows your projected finish and your chance of winning a typical week. It also shows your odds in each category and your head-to-head odds against every team. **League** shows projected standings for everyone.

### "Should I punt something?"
**My team** shows your odds in each category. Categories you win under 30% of weeks get a **Punt …** suggestion. You can also toggle any category with the **Punt** chips in the top bar. A punted column greys out in every table, and trades, waivers and ideas re-optimize with that category treated as a loss.

Classic pairings: big men punt FT%, high-usage guards punt TO, wings punt AST.

### "Is this trade good?"
**Trades** tab: check players under *You give*, pick their team, check players under *You get*, then **Evaluate**. You see:
- **Your win odds**: before → after, with both rosters updated. If you take back more players, you drop your weakest; if you send more, you pick up the best free agent. So 2 scrubs for a star scores correctly.
- **Them**: their win odds before → after. A trade can help both teams when the categories fit.
- **How it looks to them**: the deal on this season's hot and cold starts, which is what Yahoo's rankings show them. "Looks good" means they'll probably accept.
- **Projected finish**: your league rank before → after.
- Your odds change in each category.

**Ask AI GM for a pitch** writes a short message to send the other manager.

### "Find me a trade"
**Trade finder**, bottom of the Trades tab. It searches 1-for-1 up to 2-for-2 deals with every team:
- **Fair**: both teams' win odds go up (category fit), and it doesn't look like a loss to them. These are trades people actually accept. (Points leagues have no categories to trade around, so fair means you gain a little and it's near even for them.)
- **Finesse**: your odds go up more. They *look* like the winner on hot starts but lose a little in reality. This is how sell-high works.

**Load** puts an idea into the builder and evaluates it.

### "Sell high / buy low"
**Hot & cold** tab. *Sell high* lists your players playing way above their real value; other managers see the hot start. *Buy low* lists good players on other teams starting cold. Early in the season the samples are tiny, and the app says so.

### "What does team X have?"
**League** tab: every team, its weak spots (what it would value less in a trade), and your projected head-to-head record against it. Click a team to see its roster, then **Trade with them**.

### Ask the AI GM
**AI GM** tab, or in Claude Code (see `/gm` below). Ask anything: "who should I drop?", "find me steals without losing assists", "should I punt FT%?". It calls the same engine as the rest of the app, so its numbers match the tables.

## How the numbers work

**Player lines.** Every player has a rest-of-season per-game line. It starts from last season (2025-26) and shifts toward this season as games pile up: this season's weight is `GP / (GP + 20)`. Rookies start from a fringe-roster line, so three hot games don't make them stars.

**Plays %.** The share of games a player is expected to suit up for. It comes from games played last season plus this season, shrunk halfway toward 85% (one bad injury year shouldn't count fully), then cut by Yahoo's injury tag (GTD 90%, O 70%, INJ 60%, IL 50%). Missed games are half-covered by a waiver-level fill-in, the way a bench player or streamer would cover them.

**Value (the table columns).** z-scores against the top `teams × roster` players by minutes. FG% and FT% are weighted by volume, so a 60% shooter on 2 shots barely counts, and TO counts against you. This is the quick player-ranking view.

**The 9-cat matchup model (what the advice uses).**
1. Each team's expected weekly totals = Σ players' per-game lines × 3.4 games × their Plays %.
2. For every pair of teams and every category: the chance team A beats team B that week. This uses the gap between their totals and realistic weekly randomness (box-score noise, schedule swings, shooting variance on actual attempts).
3. Chance to win the matchup = chance of winning more categories than you lose (5 of 9).
4. **Win odds** = average of that against every other team. **Projected standings** rank every team by their win odds.

A trade, add or drop is judged by how much it changes your win odds, with both rosters updated: extra players get dropped, open spots get the best free agent. This is what makes it account for:
- **Your strengths and weaknesses.** Extra rebounds barely help if you already win REB 90% of weeks, and they help a lot if you're at 50%.
- **What everyone else has.** Odds are computed against every real roster in your league.
- **Punting.** A category you can't win stops mattering on its own. The Punt chips make it explicit: punted categories count as automatic losses, so the advice optimizes for the other categories.

**Now / How it looks to them.** The same line weighted much harder toward this season (`GP / (GP + 5)`), roughly what Yahoo's rankings show other managers. Finesse trades and sell-high use it.

**Points leagues** use the league's scoring weights and per-game values (no matchup model).

**Honest limits:**
- The weekly-randomness settings are educated guesses, not fitted to real Yahoo results. Odds are directionally right, but read 55% vs 58% as "about the same".
- Not modelled yet:
  - positions and roster slots (daily lineup limits)
  - each week's actual NBA schedule
  - injury return dates
  - keeper or dynasty value

The settings live at the top of `model.py` if you want to tune them.

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
| `yahoo.py` | Pulls your league from Yahoo (`python yahoo.py`) or builds the demo (`python yahoo.py demo`) |
| `ai.py` | AI GM: Claude with tools that call `model.py` |
| `test_model.py` | Self-check, no network: `python test_model.py` |
| `ROADMAP.md` | What's untested and what to build next |
| `PRODUCT.md`, `DESIGN.md` | Product and design decisions (used by impeccable) |
| `data/` | Cached NBA stats + your league snapshot (gitignored; delete to refetch) |
