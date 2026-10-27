"""AI general manager: Claude with tools that call the model, so every number it quotes comes from model.py.

Needs ANTHROPIC_API_KEY in the environment or .env.
"""
import json

import anthropic
from anthropic import beta_tool

SYSTEM = """You are a sharp, blunt fantasy basketball GM advising the user on their Yahoo league.
Use the tools for every number you state. Never invent stats or values.
Player "value" is a per-game z-score sum (9-cat) or fantasy points per game, rest of season; "avail" is the
expected share of games played. In 9-cat, trades, trade ideas and waiver moves are scored by a matchup model:
"net" = change in the user's chance to win a typical weekly matchup vs the league, in percentage points
("win" -> "win_after"); "their_net" is the same for the other team; "odds" are per-category win-chance changes.
league_overview has projected standings, per-category win odds and the user's odds vs each team. A category the
user can't win or already dominates barely moves their odds, so explain punts from that. "their_view" is how a
deal looks on this season's hype (what Yahoo ranks show them); "accept" is how likely they say yes.
evaluate_trade also returns cats_detail (both teams' category totals and odds, before -> after), vs (user's odds
against each team), standings (flag "rival": partner or a top-4 team gaining more than the user), flags (injury,
small sample, buying at peak hype) and build_after (categories the user would win under 30% of weeks, a punt hint).
Use find_trades for "who can I get for X", "who wants Y", "fix my steals" or "beat team Z"; offer a harder tier
when nothing clears the bar. Early in the season, small samples swing hard.
For trades: say who wins, which categories move, the risk (injury, role, age, sample size), and when asked,
write a short casual pitch the user can paste to the other manager. Keep answers short and concrete."""


class GM:
    def __init__(self, league):
        self.lg, self.punt, self.messages = league, [], []
        self.client = anthropic.Anthropic()
        self.tools = self._tools()

    def _tools(self):
        lg, gm = self.lg, self
        dump = lambda x: json.dumps(x, ensure_ascii=False)

        @beta_tool
        def evaluate_trade(give: list[str], get: list[str]) -> str:
            """Evaluate a trade for the user's team under their current punt.

            Args:
                give: players the user sends
                get: players the user receives
            """
            try:
                r = lg.trade(give, get, gm.punt)
            except KeyError as e:
                return f"Error: {e}"
            if "roster_after" in r:  # names and moves are enough here; full stat lines cost ~8k tokens a call
                r["roster_after"] = {s: [{k: p[k] for k in ("name", "value", "move")} for p in ps]
                                     for s, ps in r["roster_after"].items()}
            return dump(r)

        @beta_tool
        def find_trades(give: list[str] | None = None, get: list[str] | None = None, lock: list[str] | None = None,
                        tier: str = "fair", goal: str = "win", goal_cats: list[str] | None = None,
                        goal_team: str | None = None, size: int = 3, team: str | None = None) -> str:
            """Search for trades, up to size-for-size. Pin players to aim it: get = offers for their player,
            give = buyers for yours, both = complete that trade, neither = open search. Each result has "accept"
            (Likely yes / Maybe / Long shot).

            Args:
                give: user's players every deal must include
                get: other team's players every deal must include (all from one team)
                lock: players never offered or asked for
                tier: how likely they say yes. "easy": both gain and it looks good to them. "fair": both gain.
                    "finesse": they look like winners on hot starts but lose a little. "hard": user gains a lot.
                goal: "win" (weekly win odds), "cats" (user's odds in goal_cats) or "team" (head-to-head vs goal_team)
                goal_cats: categories for goal "cats", e.g. ["STL", "AST"]
                goal_team: exact team name for goal "team"
                size: max players per side, 1-3
                team: only search this opponent (exact team name), or omit for all
            """
            if lg.mode == "points":  # points leagues keep the simple finder
                return dump(lg.ideas("finesse" if tier == "finesse" else "fair", gm.punt, team, limit=12))
            try:
                return dump(lg.search(give or [], get or [], lock or [], team, size, tier, goal, goal_cats or [],
                                      goal_team, punt=gm.punt, limit=12))
            except KeyError as e:
                return f"Error: {e}"

        @beta_tool
        def waivers() -> str:
            """Best free agents for the user's build, drop candidates, and suggested add/drop moves."""
            return dump(lg.waivers(gm.punt))

        @beta_tool
        def sell_high_buy_low() -> str:
            """User's players running hot vs their real value (sell high) and slumping studs on other teams (buy low)."""
            return dump(lg.movers(gm.punt))

        @beta_tool
        def league_overview() -> str:
            """User's category ranks, H2H record vs each team, punt suggestions, and every team's detected build."""
            return dump(lg.profile() if lg.mode != "points" else {"mode": "points"})

        @beta_tool
        def roster(team: str) -> str:
            """A team's roster with values and per-category z-scores.

            Args:
                team: exact team name
            """
            if team not in lg.rosters:
                return f"Error: unknown team. Teams: {list(lg.rosters)}"
            return dump(lg.team_roster(team, gm.punt))

        return [evaluate_trade, find_trades, waivers, sell_high_buy_low, league_overview, roster]

    def ask(self, text, punt=()):
        self.punt = list(punt)
        lg = self.lg
        context = (f"League: {lg.snap.get('name')} ({lg.mode}, {lg.teams_n} teams). User's team: {lg.me}. "
                   f"Categories: {lg.cats}. User is punting: {self.punt or 'nothing'}. Teams: {list(lg.rosters)}.")
        self.messages.append({"role": "user", "content": f"{context}\n\n{text}"})
        runner = self.client.beta.messages.tool_runner(
            model="claude-opus-5-5",
            max_tokens=16000,
            system=SYSTEM,
            tools=self.tools,
            messages=self.messages,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        last = None
        for message in runner:  # the runner keeps its own copy of history, so mirror it
            last = message
            self.messages.append({"role": "assistant", "content": message.content})
            tool_response = runner.generate_tool_call_response()
            if tool_response is not None:
                self.messages.append(tool_response)
        if last is None or last.stop_reason == "refusal":
            return "The AI declined to answer that one."
        return "".join(b.text for b in last.content if b.type == "text")

    def reset(self):
        self.messages = []
