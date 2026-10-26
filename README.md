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

## Workflows

### "Who do I pick up / drop?"
**Waivers** tab. Free agents are ranked on *your* build, so punted categories don't count. **Suggested moves** pairs each good free agent with your weakest healthy player and shows the gain. Injured players are never suggested as drops. The top 3 moves also show on **My team** under "Worth doing now".

### "Should I punt something?"
**My team** shows where you rank in each category. When you're bottom-40% in one or two, the app suggests punting them (the **Punt …** button). You can also toggle any category with the **Punt** chips in the top bar. A punted column greys out in every table, and every value, waiver list and trade idea re-ranks without it.

Classic pairings: big men punt FT%, high-usage guards punt TO, wings punt AST.

### "Is this trade good?"
**Trades** tab: check players under *You give*, pick their team, check players under *You get*, then **Evaluate**. You see:
- **You**: your net change on your build. Uneven trades include the cost of dropping (or the value of picking up) a waiver-level player, so 2 scrubs for a star scores correctly.
- **Them**: their net change on *their* build (their own weak categories count less).
- **How it looks to them**: the deal on this season's hot and cold starts, which is what Yahoo's rankings show them. Positive means they'll probably like it.
- Per-category changes for you.

**Ask AI GM for a pitch** writes a short message to send the other manager.

### "Find me a trade"
**Trade finder**, bottom of the Trades tab. It searches 1-for-1 up to 2-for-2 deals with every team:
- **Fair**: both teams gain on their own builds, and it's close to even on plain overall value. These are trades people actually accept. (Points leagues have no categories to trade around, so fair means you gain a little and it's near even for them.)
- **Finesse**: you gain more; they *look* like the winner on hot starts but lose a little in reality. This is how sell-high works.

**Load** puts an idea into the builder and evaluates it.

### "Sell high / buy low"
**Hot & cold** tab. *Sell high* lists your players playing way above their real value; other managers see the hot start. *Buy low* lists good players on other teams starting cold. Early in the season the samples are tiny, and the app says so.

### "What does team X have?"
**League** tab: every team, its weak spots (what it would value less in a trade), and your projected head-to-head record against it. Click a team to see its roster, then **Trade with them**.

### Ask the AI GM
**AI GM** tab, or in Claude Code (see `/gm` below). Ask anything: "who should I drop?", "find me steals without losing assists", "should I punt FT%?". It calls the same engine as the rest of the app, so its numbers match the tables.

## How the numbers work

- **Value** = rest-of-season, per game. It starts from last season (2025-26) and shifts toward this season as games pile up: this season's weight is `GP / (GP + 20)`. Rookies start from a fringe-roster line, so three hot games don't make them stars.
- **9-cat**: z-scores against the top `teams × roster` players by minutes. FG% and FT% are weighted by volume (a 60% shooter on 2 shots barely counts). TO is inverted. Value is the sum over your non-punted categories.
- **Points**: your league's scoring weights from Yahoo (Yahoo's defaults in demo mode).
- **Now** is the "perceived" value: the same blend weighted much harder toward this season (`GP / (GP + 5)`), roughly what Yahoo's rankings show.
- **Replacement level** = average of the 5 best free agents.

Not modelled yet: positions and roster slots, games per week, injuries beyond Yahoo's status tag, keeper or dynasty value.

## Using Claude Code on this project

Open the folder in Claude Code. These skills and plugins are set up for this project (in `.claude/`):

| What | Use it for | How |
|---|---|---|
| **gm** skill | Ask about your league from Claude Code. Claude refreshes Yahoo data and runs the engine. | `/gm who should I drop?` |
| **humanizer** skill | Make trade pitches and messages sound like you wrote them. | `/humanizer <text>` |
| **impeccable** skill | Design work on the UI: critique, polish, audit, new screens. Reads `PRODUCT.md` and `DESIGN.md`. | `/impeccable critique web/index.html`, `/impeccable polish` |
| **design-taste-frontend** skill | Second opinion on visual quality, or a public landing page if you ever share this. | `/design-taste-frontend` |
| **feature-dev** plugin | Guided flow for a bigger feature (explore → architect → build → review). | `/feature-dev add positional limits to trades` |
| **commit-commands** plugin | Commit, push, open a PR. | `/commit`, `/commit-push-pr` |
| **claude-md-management** plugin | Keep `CLAUDE.md` current after a session. | `/revise-claude-md` |
| **context7** plugin | Up-to-date docs for nba_api, yfpy, pandas while coding. Needs a one-time login. | Run `/mcp` → context7 → authenticate |
| **pyright-lsp** plugin | Python type and error checking while Claude edits. | Automatic |
| **security-guidance** plugin | Warns about risky code patterns (secrets, injection). | Automatic |

**Turned off for this project** (still installed globally): `superpowers` (overlaps with feature-dev and forces a long process on every task), `pr-review-toolkit` (feature-dev has a reviewer), `frontend-design` (replaced by impeccable and design-taste-frontend), `typescript-lsp` (no TypeScript here), `claude-code-setup` (one-time setup tool). Turn any back on in `.claude/settings.json`.

### Typical dev loop

1. `/feature-dev <what you want>`, or just describe it.
2. Claude runs `python test_model.py` and opens the app to check.
3. UI changes: `/impeccable polish web/index.html`.
4. `/commit`.
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
| `PRODUCT.md`, `DESIGN.md` | Product and design decisions (used by impeccable) |
| `data/` | Cached NBA stats + your league snapshot (gitignored; delete to refetch) |
