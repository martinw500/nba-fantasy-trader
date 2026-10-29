# Fantasy GM

A local app for your Yahoo NBA fantasy league. It tells you who to pick up and drop, tests trades from both sides, finds trades worth proposing, flags sell-high / buy-low players, and helps you pick categories to punt in 9-cat. Points leagues work too. An AI GM chat sits on top and uses the same numbers.

It only reads from Yahoo. You still make the actual moves on Yahoo.

## Setup (5 minutes)

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

That opens http://localhost:8765 with a **demo league**: 12 fake teams drafted from real NBA stats. Everything works on it, so you can try the app before connecting Yahoo.

Homebrew Python blocks a global `pip install`, hence the `.venv`. Where this README says `python`, use `.venv/bin/python` (or run `source .venv/bin/activate` once per terminal; on Windows it's `.venv\Scripts\python`).

### Load your league without the Yahoo API

Since summer 2026, Yahoo only gives Fantasy API access to applications it approves, and approval has no timeline. Until then, load the league from a text snapshot:

1. Get a snapshot of the league as a text file. Instinct's "MCHC FANTASY BASKETBALL - FULL LEAGUE SNAPSHOT" works as-is: league settings, numbered team headings, one fixed-width row per player (`PG    Cade Cunningham    DET - PG,SG  [O: Out]   Pre 5  Cur 41  Ros 100%`), free-agent and waiver sections with roster % and claim dates, this week's matchup totals, transactions, Yahoo projections, adds used, waiver priority and the season schedule. Sections it doesn't recognise are skipped. A hand-typed list works too:
   ```
   ## Toronto blue jays
   Cade Cunningham
   Ivica Zubac (O)
   ```
2. Run `.venv/bin/python yahoo.py import mchc-fantasy-snapshot-2026-10-26.txt`. Your team comes from the file's `My team:` line; name it after the file to pick another (`... import file.txt "fatty zhou"`). It lists any player it couldn't match to NBA stats (usually rookies who haven't played yet).
3. Restart `python app.py`. For a new snapshot, drop the file in the repo folder and press **Refresh**: it imports the newest `*snapshot*.txt`. The sidebar shows when the snapshot was pulled, and every view warns once it's over a day old or its matchup week is over.

   Under that, the sidebar lists how old each downloaded source is: **Stats** and **Box scores** (refreshed every 12 hours), **Schedule** and **Projections** (daily), **Injuries** and **News** (every 3 hours). Hover one for the exact time. An entry turns amber once it's past its refresh time (press **Refresh**), shows **old copy** when the last download failed and the app is using what it had, or **none** when it has never downloaded. A failed or missing source (other than News, which never changes a number) also puts a notice at the top of every view, saying what the numbers leave out. **Refresh** always fetches injuries again; if that fails, the old copy stays. `python model.py sources` prints the same list.

Pick your team from the dropdown under the league name in the top bar. Snapshot files (`*snapshot*.txt`) are committed, so `git pull` brings them to your other computers.

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

Nothing to set up if Claude Code is installed and logged in: the chat runs `claude -p` on your own login, no API key. It uses whatever account `claude auth status` shows, and its usage counts against that plan's limits. Answers take 10–30 seconds. Its sessions show up in Claude Code's `/resume` list for this folder.

The AI GM tab has three settings, saved in your browser: **Model** (Opus 5.5, or Fable 5.1, which needs usage credits on a Pro plan), **Effort** (Medium by default; higher thinks longer and uses more of your plan) and **Web search** (on by default: it can look up injury, role and trade news the model can't see, and names its sources, but every number still comes from `model.py`). They only apply to the `claude -p` chat, not to `ai.py`.

**History** on the AI GM tab lists past conversations, newest first. New conversation keeps the old one there. Click one to reopen it and keep going (Claude resumes that session, so it remembers), and **Delete** removes the open one. They're saved in `data/chats.json` on your Mac, so every browser sees the same list, and `data/` isn't in git.

With `ANTHROPIC_API_KEY=sk-ant-...` in `.env` (key from https://console.anthropic.com, billed separately), it uses `ai.py` and the API instead. Everything except the chat works without either.

## Moving to a new computer

The code lives on GitHub (`martinw500/nba-fantasy-trader`). Two things are deliberately **not** in git: `.env` (your keys) and `data/` (cached stats and the imported league, rebuilt from the newest snapshot on Refresh).

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
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   .venv/bin/python test_model.py      # should print: ok
   ```
3. Put your `.env` back in the folder, or redo "Connect your Yahoo league" above.
4. `python yahoo.py import <newest>-snapshot.txt` (or press **Refresh** in the app), or skip it to use the demo.
5. `python app.py`. NBA stats download on their own the first time (`data/` is rebuilt automatically).
6. In Claude Code: open the folder and accept the prompt to install the project's plugins. Run `/mcp` → context7 → authenticate if you want it. The project skills (`/gm`, humanizer, impeccable, design-taste-frontend) come with the repo.

Then see [ROADMAP.md](ROADMAP.md) for what's next.

## Workflows

### "Who do I pick up / drop?"
**Waivers** tab. Every free agent is tried against every healthy player on your roster. **Suggested moves** shows the add/drop pairs that raise your win % the most. Injured players are never suggested as drops. The top 3 moves also show on **My team** under "Worth doing now". **Recently dropped** lists the players let go in the snapshot's transactions that nobody has picked up since, newest first.

**Try a move** (top of Waivers) scores any add and drop you pick, like the trade builder does for trades. Type any free agent, pick one of your players (or **Pick for me**: your open spot if you have one, else the healthy player you'd miss least; players in IL slots are marked), optionally the day to make it on (**Best day** by default), and press **Evaluate move**. **Try** on a suggested move, a free agent or a drop candidate loads it in. You get a verdict (Worth it, Toss-up, Not worth it), the change in your season win % and projected finish, the change in this week's win % against your opponent (with his games left this week against your drop's, your lineup's starts before → after, since an add whose game lands on a full day can bench someone, and the expected score), and each category's odds for the season and this week, before → after. Punted categories dim. It's scored on today's roster, the same way as Suggested moves, not on top of them, so the best suggested move scores exactly what that list says. IL slots work as in Yahoo: an injured player you add goes into a free IL slot, dropping a player from your IL slot doesn't open an active spot (the player you'd miss least goes too, and the note says so), and a spot a move leaves open stays open, since filling it is another add. A waiver claim counts this week only from the day it clears. This week's number is for the move made on its best day (the note says when, and how many games your drop starts first). It also says how many adds you have left this week; with none left the move waits until Monday, so only the season number counts. A move that helps this week but costs you over the season is flagged as a stream. This week's number needs an opponent: pick one on This week. The URL updates to `#waivers?add=A&drop=B`, so you can bookmark a move. CLI: `python model.py move --add "A" --drop "B" [--vs "Team"] [--day 2026-10-30]` (`--day`: make it that day instead of its best one).

### "Where do I stand?"
**My team** shows your projected finish and your win %: the share of categories you'd win in a typical week (see "The 9-cat matchup model" for One Win leagues). It also shows your odds in each category and your head-to-head odds against every team, plus your chance to make the playoffs. **League** shows projected standings for everyone and the **Playoff race**: each team's record so far, its expected final record, and its chance to make the playoffs (top 8 of 12) and win the title (three one-week rounds, reseeded each round, the higher seed winning a tied week). The odds come from playing the rest of the season out thousands of times: the snapshot's records, this week's score so far plus the rest of the week, then every week on the schedule (weeks 7 and 17 count double). Each simulated season also lets every team run above or below its projection for the whole season and in single weeks, by as much as real teams did in 2025-26 (`python backtest.py playoffs`). Without that, teams' final category totals would spread about half as wide as they really do (2.6x too narrow from opening week, about 2x mid-season), and teams a naive simulation gave 80–90% made the playoffs only 72% of the time.

### "What's the news?"
The **News** panel on My team lists ESPN's fantasy news (Rotowire's blurbs) on your players and their NBA teammates who play 10+ minutes. It covers the time since your snapshot, or at least the last two days, and tags items that came out after the snapshot as **new**. Your players come first. Teammates show five items, with the rest behind a toggle. A player's card shows his three latest items. News never changes a number by itself: the model reacts through return dates, minutes and expert projections, and the AI GM can search the web for the rest. ESPN's player list (ids and the time of each player's latest news) refreshes every 3 hours, and a player's news is fetched again only when that list shows something newer (`data/news.json`). `python model.py news` prints the same list.

### "How's my matchup this week? Who do I stream?"
**This week** tab. Pick your opponent once (it's saved until the week ends on Sunday). You get the expected score (categories won–lost), your chance to win more categories than you lose, each category's projected total and odds with a call (*Safe*, *Chase*, *Lost*), and **Streaming adds**: free agents ranked by how much they raise this week's win %, with games left, who to drop, the best day to make the add (your drop plays his games until then: drop a player after his Wednesday game, add the streamer for Thursday on), and what keeping him does to your season-long win %. The league allows 4 adds a week, so the panel says how many you have left: the higher of the snapshot's count and the adds and waiver claims in its transaction log since the week started (trades and drops don't count; moves made after the snapshot aren't seen). Under it, the best way to spend them: the streams by day, each scored on top of the days before, where a later one can replace an earlier streamer once his games are used. With one add left it names the day to save it for. With none left there are no streams until Monday. Projections count each player's games left this week, but only the 10 best players with a game each day who fit your starting slots by position (PG, SG, G, SF, PF, F, C, C, Util, Util; Yahoo scores starters only, so a bench player's game counts only on a light day, and a third center can sit even with a guard spot open). Games left shows the same count. When the snapshot has them, Yahoo's own games left (and games played so far) show next to it. Yahoo counts the lineups as they're set now, so its number is usually lower. Players in IL slots sit, and a waiver claim counts only from the day it clears. A snapshot with a matchups section (or a Yahoo pull) fills in the opponent and the score so far, and when it was pulled after that day's games started, the rest of the week counts from the next day. Without one, totals start at zero (right on Mondays). Once the snapshot's week is over, the season schedule in the snapshot names the next opponent and the week's dates (weeks 7 and 17 run 14 days) until a new snapshot brings the score. CLI: `python model.py week --vs "Team"`.

### "When should I stream? Whose schedule is heavy?"
**Schedule** tab. **Games per day** is a calendar of the rest of this matchup week and the next three (a 14-day week takes two rows): each day shows how many of your active players have an NBA game. Only 10 start, so a red day has more than that (the number that sit) and a green day has open starting spots a streamer would fill. Pick a day to see who starts and who sits (your lowest on your build's value, so punts count). The week totals show starts, games lost to a full lineup, and open spots, and the week's label shows your adds left (4 a week; a picked day with open spots warns when you have none left). **Your players** lists each one's NBA games per week, with a red number for games he'd sit because 10 better players have one that day. **NBA teams ahead** ranks all 30 teams by games over those four weeks, plus the fantasy playoff weeks (21–23, read from the snapshot's settings), your players on each team and the best healthy free agent there for your build. Switch to **On your open days** to count only games on days you have an open spot: that's how many games a streamer from that team would actually start for you. Counts use today's roster, players in IL slots sit, injured players count from their return date, and starters fill your slots by position, so a player can sit on a day with a spot open when no open slot fits him (the day's line then says which positions a streamer needs). The NBA teams' open-day counts don't check a streamer's position. CLI: `python model.py schedule`.

### "Should I punt something?"
**My team** shows your odds in each category. Categories you win under 35% of weeks get a **Punt …** suggestion. You can also toggle any category with the **Punt** chips in the top bar. A punted column greys out in every table, and trades, waivers and ideas re-optimize with that category treated as a loss. Win % numbers scored that way say so ("FT% counted as lost"); My team's header and the trade's Standings movers show your real odds.

Toggling a chip always lowers today's number, because it concedes a category without changing your roster. A punt only pays off through the moves it unlocks. The **Strategy** panel on My team tests that: **Compare builds** tries staying balanced, every single punt and six common pairs (FT%+TO, FG%+TO, FG%+FT%, AST+TO, 3PM+FT%, FG%+3PM). Each build makes its own waiver adds, then up to three fair trades in a row. Each trade is the one that helps that build most, searched with its punt on the rosters the trade before left, and each clears the fair tier on its own. A punt usually pays off over several moves, and the first one costs the most, so a plan's first trade may lower your real odds before the next ones pay it back. Every step is scored on your real odds, with nothing conceded, and a build keeps its best stretch of the plan (0 to 3 trades). The verdict says "Stay balanced" unless a punt's plan beats staying balanced by more than a toss-up. Staying balanced also tries every trade the other builds found and repeats each build's plan for as long as every trade still lifts your real odds. So a punt only wins through moves it alone unlocks. Pick a build under **Every build** to see its plan step by step: waiver adds, each trade with the waiver moves it leads to, your win % after each step, and **Load** to open a trade in Trades. Load is greyed out on a trade that needs the earlier ones first. Each punt also shows its **star fit** (your top five players' weak spots it concedes, e.g. Jokić's TO) a **buy list**: players on other teams the market prices furthest below their worth to that build, since Yahoo's ranks count every category. The **sell list** is its mirror: your players the market prices 5 or more above their worth to the build, each with why (Yahoo projects him higher than we do, a hot start, games he'll miss, or his value is in what the build punts), and **Find buyers** to shop him in Trades. It takes about 15 seconds. CLI: `python model.py strategy [--size 2]`.

Under the category odds, **Cheapest to move** lists the three categories where one more z (a player's category cell going up by 1.0) raises your odds most. That's where a trade or stream pays most. Odds near 50% move most, but noisy categories move less than their odds suggest.

Classic pairings: big men punt FT%, high-usage guards punt TO, wings punt AST.

### "Is this trade good?"
**Trades** tab: check players under *You give*, pick their team, check players under *You get*, then **Evaluate**. You see:
- **Your win %**: before → after, with both rosters updated. If you take back more players, you drop your weakest; if you send more, you pick up the best free agent. So 2 scrubs for a star scores correctly.
- **Compared with waivers.** Your number starts from the waiver moves you could make without a trade (the note under the verdict names them, e.g. "Nikola Vučević for Precious Achiuwa (+1.2%)"), so a 2-for-1 gets no credit for a pickup you'd make anyway: its open spot gets the next-best free agent. The finder works the same way. The other team is judged on the trade alone, since that's what managers look at. Tick **Count their waiver moves** in the finder toolbar to measure them against their own waiver moves too (remembered in this browser).
- **Them**: their win % before → after. A trade can help both teams when the categories fit.
- **Market**: how managers would price it, from Yahoo's ranks (see "Market value" below): what you send and get in market value, and a verdict like "you overpay 35%" or "even". **Will they say yes** reads mostly off this. The note under the verdict says how the deal looks on this season's hot and cold starts alone.
- **Projected finish**: your league rank before → after.
- **Playoff odds**: your chance to make the playoffs before → after, and your partner's (the League tab's Playoff race, rerun on both new rosters), and **Title odds** from the same run. Shown once the snapshot has the season schedule. Worth a look when a deal pushes a rival over the cut line.
- Your odds change in each category.

**Copy offer** copies the offer and a short pitch for the note on Yahoo's trade offer, built from the numbers (their weak spots it shores up, categories it adds, the market when it favours them, roster spots they free). It never claims more than that. **Ask AI GM for a pitch** writes a longer one in the chat. **Save trade** keeps it for comparing later.

**Add any player to the trade** (top right) jumps to any rostered player: it switches to his team and checks him. The top bar's **Find a player** opens his card instead. The URL updates to `#trades?give=A,B&get=C` after each evaluation, so you can bookmark a trade. Last names work in the link too, as long as only one player has that name.

### "Find me a trade"
The finder panel sits under the builder. The players you check aim the search, and its title says which search it will run:
- Check **their** player: **Offers for X**, what you could give for him.
- Check **yours**: **Buyers for Y**, every team that might take him and what they'd send back.
- Check **both**: **Complete this trade**, deals that keep everyone checked and add players around them.
- Check **nobody**: **Open search** across every team.

The padlock on a player means the finder never uses him. Deals go up to 3-for-3. Toolbar:
- **Difficulty**, i.e. how likely they say yes, priced on the market. **Easy**: they win on market value and you still gain. **Fair**: market value within 15% either way, an offer that looks even to them, with deals where both teams gain first. **Finesse**: even on market value or in their favour, but the fit is strongly yours: they pay market price for players worth more to you. **Hard**: you win on market value, so it needs a good pitch. That includes sending slightly lesser players (up to 30%) when the deal helps their team too, which is the pitch; Hard and Finesse list the deals likelier to get a yes first. No difficulty lets you overpay by more than 30%. A suggested deal never includes a player the side taking him would drop at once (it's the same deal without him), and a team with an open roster spot takes an extra player without dropping anyone.
- **Max overpay**: the most you'll overpay on market value (20%, 10% or 0%). It only tightens the difficulty.
- **Players**: **Outside the top 60** or **Outside the top 100** keeps everyone that high in the market's ranking out of the deal on both sides, for trades between role players. Players you check stay in. Improve this deal uses it too.
- **Optimize for**: overall win %, your odds in chosen **categories**, or your head-to-head odds against **one team** (e.g. a playoff rival). The last two skip deals that cost you more than 0.4% win % (1% in One Win leagues).
- **Max size**, **Shape** (consolidate: you send more players; depth: you get more), **Healthy only**, and a team filter.

Every deal shows its **Market** column: market value you send → get and the gap. Columns sort on click. **Load** evaluates a deal in the builder, and **Save** keeps it.

Points leagues keep the simpler finder: Fair or Finesse, 1-for-1 up to 2-for-2, no checkboxes or locks.

**Improve this deal** appears after you evaluate a trade. **Throw-ins** keeps everyone in the deal and adds a player to either side. **Swaps** replaces one player at a time. Both use the finder's settings.

**Saved trades** (bottom of the tab) compares saved deals side by side. They're re-scored against today's rosters and your punts every time, so the numbers never go stale.

**Offers sent** (under Saved trades) lists every offer you copied with what the app predicted then (market, Will they say yes). Mark each one Accepted or Declined when you hear back. The log lives in `data/offers.json`, and it's the only real data for tuning how the app guesses whether a manager says yes.

**Find a buyer** on My team and **Find offers** on Hot & cold's buy-low list run these searches directly.

### "Sell high / buy low"
**Hot & cold** tab. It runs on recent form: a player's value on your build over the last 14 days of box scores (players with 3+ games in them), against his rest-of-season value. *Sell high* lists your players running well above it; other managers see the hot run. *Buy low* lists good players on other teams running cold. "Well above" is a gap of 3.0 or more, about one standard deviation of a 14-day window among 2025-26 regulars mid-season, so a third of them clear it at any time. Player tables mark the same runs with an arrow next to **Value** (hover the cell for the 14-day value and games). Early in the season two weeks is three or four games, and the app says so.

### "What does team X have?"
**League** tab: every team, its weak spots (what it would value less in a trade), and your projected head-to-head record against it. Click a team to see its roster, then **Trade with them**.

### "Tell me about this player"
Click a player's name in any player table to open his card under the row. It shows his per-game line for the rest of the season (the model's projection), the Yahoo or ESPN projection blended into it (Yahoo's has no minutes, so that cell shows a dash), this season, and the last 30, 14 and 7 days. A window that holds the same games as the one before is left out (Compare keeps every window so players line up). It also shows the injury note and expected return, the minutes and stats he picks up while a teammate is out (and until when), NBA games left this matchup week and in each of the next three, and his market rank next to his rank on your build, with a one-line read, then his three latest ESPN news items. **Find buyers** (your player) and **Find offers** (another team's) open the trade finder on him. Recent lines come from this season's NBA box scores, refreshed every 12 hours. Names in deal rows and sentences don't open cards yet: **Load** a deal to see its players in a table, or look him up in the top bar.

**Find a player** in the top bar (press `/` to jump there) searches every rostered player and the snapshot's free agents, accents and all, and opens his card. Pick a name from the list, or type part of one and press Enter when only one player matches. If he's in a player table on the screen, the card opens under his row there. Otherwise it opens in a panel at the top of the page, until you close it or change tabs.

### "Who do I keep / start / trade for?"
**Compare** tab: two to four players side by side. Press **Compare** on any player card to add him (it opens the tab once two are in), or search any rostered player or free agent at the top of the tab. Each column shows the injury note, your win % with him in one of your roster spots, Value, Now, recent form, Plays and games played, his market price and his rank on your build (with the card's one-line read), NBA games this matchup week and in each of the next three, and every category as a rest-of-season z-score next to his per-game line. Pick which line: rest of season, this season, or the last 30, 14 or 7 days. Bold marks who's ahead on each number, and punted categories dim like everywhere else.

The win % row answers the keep-or-get question directly. The players you own in the comparison all leave your roster, then each player in turn takes one spot. Your other players and the best free agents fill the rest, and a player who'd be your worst gets cut ("He wouldn't make your roster"). Other teams' rosters stay as they are, so this isn't a trade result: use **Find offers** / **Find buyers** under each column for that. The list is saved in your browser, and `#compare?players=A,B` links straight to a comparison. `python model.py compare --players "A, B"` prints the same numbers.

### Ask the AI GM
**AI GM** tab, or in Claude Code (see `/gm` below). Ask anything: "who should I drop?", "find me steals without losing assists", "should I punt FT%?". It calls the same engine as the rest of the app, so its numbers match the tables.

## How the numbers work

**Player lines (the projection).** Every player has a rest-of-season per-game line. The pre-season baseline blends the last two seasons (a game in 2024-25 counts half as much as one in 2025-26) and is pulled toward a fringe-rotation line by 25 games, so a 5-game season isn't taken as gospel and a player back from a lost season isn't treated as a rookie. The line then shifts toward this season as games pile up, in two parts: minutes per game × production per minute. Minutes move fast, `GP / (GP + 6)` (a third after 3 games, half after 6), because a new role or team shows up at once and sticks. Production per minute moves slowly, by minutes played against 20 games of baseline minutes (13% after 3 games at his usual minutes, less if he barely played), because 3 games of shooting is noise. So a starter who lost his spot drops fast, and a bench player who got 8 minutes a night barely moves his per-minute numbers. Replaying 2025-26, this cut the error of each Monday's rest-of-season lines by 8% in the first six weeks (10% for players whose minutes changed), and the matchup backtest improved slightly. Rookies nobody projects start from a fringe-roster line, so three hot games don't make them stars.

**Expert projections.** The pre-season baseline is blended with an expert projection, weighted 60% expert, 40% box scores. The expert line is Yahoo's rest-of-season projection from the snapshot (Part 8: rostered players and the top 100 free agents) and ESPN's season projection for everyone else (fetched daily, each day's copy kept in `data/projections_<date>.json`). Yahoo shows no minutes, so its minutes are ESPN's (or ours) scaled by how much more or less Yahoo has the player scoring, rebounding and assisting: a projected role change moves minutes, not per-minute rates. A rookie's baseline is the fringe line, so a rookie with no games yet (AJ Dybantsa) still gets a projection-based line. The 60% comes from replaying 2025-26 with ESPN's preseason projections: each Monday's rest-of-season error fell 5% at the start of the season, 3% in weeks 1–6 and 2% over the season (top 200 players: 7%, 3% and 2%), and 6% for rookies in weeks 1–6, who before had no line at all until their first game. This season's games then take over as usual, so the expert part fades as games pile up. The player card shows which projection went in.

**Plays %.** The share of games a player is expected to suit up for. It comes from games played in the better of the last two seasons and this season so far, pulled toward 70%, then cut by the injury tag if he sat his team's last game (GTD 93%, O 80%, INJ or IL 70%: what players who'd sat played over 2022-23 to 2025-26). A tagged player who played his team's last game isn't cut. When ESPN's injury report has an expected return date, that replaces the tag: the player counts for the share of remaining games after that date. Missed games are half-covered by a waiver-level fill-in, the way a bench player or streamer would cover them. The rule was fitted on 2024-25 and 2025-26 and holds on 2022-23 and 2023-24 (`python backtest.py availability`): for players who are playing now, past games played barely predict how many of the games left they play, and the worse of two past seasons adds nothing. So one lost season isn't treated as fragility, a game this season counts five times a game in the better past season, and an iron man and a player who keeps getting hurt end up closer than you'd think (about 79% vs 65% early in the season). It guesses games played 13% better than the old formula (36% better for players back from one lost season), and the matchup odds improved too.

**Wk.** Games left this week (Monday to Sunday) from the NBA schedule, counting only games after a return date. `League.week_vec()` turns that into this week's expected totals. Trades and the season-long odds still use an average 3.4-game week.

**Injury news.** ESPN's public injury report (refreshed every 3 hours) adds the latest note and the expected return date. Hover an injury tag to read it. The league's own tag wins when they disagree.

**Teammates' injuries.** When a regular (25+ minutes a game) is out with an expected return date, his teammates play more until he's back. Half his minutes go to teammates who play 10+ minutes, split by how much room each has under 36 minutes (nobody goes past it), with about twice as much going to players in a similar role (by assists, rebounds, blocks and threes per minute). When two or more are out at once, which is most weeks a regular is out, their minutes go to the players left, never to each other, in stretches until each one returns. They play those minutes at their own rates and also pick up a quarter of the shots, points, assists and turnovers those minutes don't replace. The rest goes to the end of the bench. The lift only counts for a player's games before the return date: all of them in This week, and their share of the rest of the season in the season-long line. This season's box scores already include games where a teammate sat, so they're read net of what each player picked up in those games. That way the boost isn't counted twice, and it goes away when the injured player returns. A player out since before the season was already in the preseason projections, so his teammates' lifts are cut by the part of their line that comes from those. A star's minutes spread over a whole rotation, so most lifts are small: LaMelo Ball gets +1.0 min and +1.4 pts a game while Anthony Edwards is out (back Dec 21). Player tables tag a lift of a minute or more (hover it for who and until when), and the player card lists every one. Replaying 2025-26 (`python backtest.py teammates`), next week's minutes error falls 3.1% for players with an injured teammate who played all their team's games that week (2.4% before teammates out together were spread together), and 3.6% on 2024-25, which nothing was fitted on.

**Value (the table columns).** z-scores against the top `teams × roster` players by minutes. FG% and FT% are weighted by volume, so a 60% shooter on 2 shots barely counts, and TO counts against you. This is the quick player-ranking view.

**The 9-cat matchup model (what the advice uses).**
1. Each team's expected weekly totals = Σ players' per-game lines × 3.4 games × their Plays %.
2. For every pair of teams and every category: the chance team A beats team B that week. This uses the gap between their totals and realistic weekly randomness (box-score noise, schedule swings, shooting variance on actual attempts).
3. What the week is worth in the standings. **Head-to-Head Categories** (the default, and Yahoo's): every category counts as a win or loss, so it's the expected share of categories won. **One Win**: only the matchup counts, so it's the chance of winning more categories than you lose (5 of 9). The format comes from the snapshot's `Scoring type:` line or Yahoo's `scoring_type`.
4. **Win %** = average of that against every other team: your projected standings win %. **Projected standings** rank every team by it.

Category win % moves about 2.5× less than One Win odds per move (a +2.7 trade there is about +1.1 here), so the cutoffs (trade tiers, verdicts, waiver moves) scale with the format (`CAT_SCALE` in `model.py`). Projected standings run tighter too: about 41–57% on the real league.

A trade, add or drop is judged by how much it changes your win %, with both rosters updated: extra players get dropped, open spots get the best free agent. IL slots count as in Yahoo: an injured player you get goes into a free IL slot, and sending away a player from your IL slot doesn't open a spot. A player sitting in an IL slot counts only for the games after his return, minus the waiver-level player he'll replace then. Your side of a trade starts from your free moves: the best add/drop (or plain add into an open spot), repeated while one still raises win % by 0.2 or more (0.5 in One Win). Waivers lists yours. This is what makes it account for:
- **Your strengths and weaknesses.** Extra rebounds barely help if you already win REB 90% of weeks, and they help a lot if you're at 50%.
- **What everyone else has.** Odds are computed against every real roster in your league.
- **Punting.** A category you can't win stops mattering on its own. The Punt chips make it explicit: punted categories count as automatic losses, so the advice optimizes for the other categories.

**Now / How it looks to them.** The same line with production per minute weighted harder toward this season (10 games of baseline minutes instead of 20: 23% after 3 games at his usual minutes). For trades, a player's look is counted over the games he actually plays (managers remember who missed a season) and never below what the best free agents look like. A manager taking back extra players feels the loss of the players they'd drop. Trades show it as a note (Hot & cold runs on the last 14 days instead).

**Market value (Mkt).** What other managers pay for a player comes from outside the model: Yahoo's preseason rank blended with its current (season-to-date) rank, the current one weighted `GP / (GP + 20)` because it's noise early (injured players sink to 600+), blended as a geometric mean so a cold start costs a player as much as an equally hot one gains, then re-ranked. Four cold games no longer sink Jalen Brunson (#27 preseason) behind a hot Paul George (#75): they read #35 and #59. A player Yahoo hasn't ranked uses the model's own rank, marked `*`. Value = `100 × 0.97^(rank − 1)`: rank 1 = 100, 25 = 48, 50 = 22, 100 = 5, 150 = 1, so a star is worth more than the sum of spare parts. A trade's market counts roster spots: the side taking more players loses the market value of the ones it drops, and each open spot the other side fills is worth the waiver level (about 1), whoever fills it: a free agent is free, so a pickup never makes a deal look richer. Trade tiers and "Will they say yes" run on it.

**Points leagues** use the league's scoring weights and per-game values (no matchup model).

**Honest limits:**
- The weekly-randomness settings are fitted to real 2025-26 box scores (`python backtest.py`: 20 simulated leagues, each week predicted from what was known that Monday, with injured players sitting until a rough guess at their return). Category odds come true at the rate they say, within a few points (what a categories league runs on); whole-matchup odds within about 3 points. Still, read 55% vs 58% as "about the same".
- The market curve (`MARKET` in `model.py`) and the tier cutoffs are starting guesses. There's no data yet on which offers real managers accept.
- Not modelled yet:
  - positions in the season-long numbers (trades, waivers, standings): only This week and Schedule fill slots by position. On the real rosters it costs 0.0–0.1% of games beyond the 10-starter cap, but a roster with no center loses about 6%, so a trade that leaves a starting slot nobody can fill is flagged instead
  - the real schedule in trades and season odds: they use an average 3.4-game week (This week uses the real one). That includes the playoff odds' all-star break and playoff weeks
  - Yahoo's tiebreaker for equal records: the playoff odds break ties at random
  - keeper or dynasty value
- An expert projection updated after a mid-season injury (Yahoo's may be) can count a teammate's lift twice for its part of his line; only injuries from before the season are taken out. The randomness settings assume ESPN's return dates are roughly right (nobody has measured how far off they run yet). If ESPN's injury feed is down, the app only has Yahoo's injury tags and its odds run a little bold.

- Playoff odds: replaying 2025-26 in 60 simulated leagues from every Monday, they come true within about 3 points (teams given 80–90% made it 87% of the time). Those leagues never trade or pick anyone up, so a real season swings a bit more. Title odds can't be checked as closely, since each league crowns only one champion.

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
| `backtest.py` | Checks the matchup odds (and with `playoffs`, the playoff odds) against a real NBA season and prints predicted vs actual win rates |
| `yahoo.py` | Pulls your league from Yahoo (`python yahoo.py`) or builds the demo (`python yahoo.py demo`) |
| `ai.py` | AI GM over the API (when `ANTHROPIC_API_KEY` is set): Claude with tools that call `model.py`. Without a key, `app.py` runs `claude -p` instead |
| `test_model.py` | Self-check, no network: `python test_model.py` |
| `ROADMAP.md` | What's untested and what to build next |
| `PRODUCT.md`, `DESIGN.md` | Product and design decisions (used by impeccable) |
| `data/` | Cached NBA stats + your league snapshot (gitignored; delete to refetch) |
