# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Plain HTML/CSS/JS in one file (`web/index.html`), served by the Python stdlib server in `app.py`. No build step, no npm.

## Users

One person for now: the owner, managing their own Yahoo NBA fantasy team on a laptop/desktop. Other league managers might use it later (undecided; would need hosting and per-user Yahoo auth).

## Product Purpose

Help a fantasy basketball manager make better moves faster: evaluate trades, find trades worth proposing, pick up and drop the right players, spot sell-high / buy-low windows, and pick categories to punt in 9-cat. Success = the user acts on a suggestion with confidence and the numbers hold up.

## Positioning

Every recommendation is computed from the user's actual league (rosters, free agents, scoring) and their chosen punt build, and every trade is judged from both sides, including how the other manager will *perceive* it, not just its true value. The AI GM only quotes numbers from the engine.

## Operating Context

Used alongside the Yahoo Fantasy site/app: the user analyzes here, then executes trades and add/drops on Yahoo. Checked around waiver periods, before proposing trades, and when a player gets hot or injured. League data is pulled on demand (`yahoo.py`); a demo league exists for use without Yahoo.

## Capabilities and Constraints

- Modes: H2H 9-cat (FG%, FT%, 3PM, PTS, REB, AST, STL, BLK, TO) and points.
- Values are per game, rest of season; early-season samples are noisy and the UI should say so.
- Read-only toward Yahoo: no trade proposals or roster moves are sent from the app.
- AI chat needs an Anthropic API key; everything else works without one.
- Ignores positions/roster slots and games-per-week schedules (known gap).

## Evidence on Hand

Real NBA stats via nba_api (2025-26 and 2026-27). No real league data is committed; the demo league is generated.

## Product Principles

1. Show the number and the reason: every verdict comes with the categories that moved.
2. Both sides of every deal: a trade the other manager would never accept isn't a suggestion.
3. Fast to scan: the user decides in seconds, details on demand.
4. Honest about uncertainty: small samples and missing data are flagged, never hidden.

## Brand Commitments

Category standard, played straight (chosen over themed directions). Craft bar: Sleeper (friendly, modern fantasy UI), Linear / Notion (crisp, quiet, fast), Basketball Monster / Hashtag Basketball (dense, data-first stat tables).
