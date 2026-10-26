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
deal looks on this season's hype (what Yahoo ranks show them). Early in the season, small samples swing hard.
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
                return dump(lg.trade(give, get, gm.punt))
            except KeyError as e:
                return f"Error: {e}"

        @beta_tool
        def trade_ideas(mode: str = "fair", team: str | None = None) -> str:
            """Search for trades. mode "fair" = both teams gain on their builds; "finesse" = user wins more, other
            side looks like a winner on hot-start hype.

            Args:
                mode: "fair" or "finesse"
                team: only search this opponent (exact team name), or omit for all
            """
            return dump(lg.ideas(mode, gm.punt, team, limit=12))

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

        return [evaluate_trade, trade_ideas, waivers, sell_high_buy_low, league_overview, roster]

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
