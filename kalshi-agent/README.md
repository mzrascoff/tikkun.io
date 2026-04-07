# kalshi-agent

A research agent that scans Kalshi for prediction-market contracts whose
prices diverge from a science-grounded view of the world, then ranks the
opportunities by expected value.

This is a **research / decision-support tool**. It does not place trades.
It writes ranked opportunities to a SQLite database and prints a report.

## What it does

1. **Ingest** — pulls open markets from the public Kalshi API
   (`api.elections.kalshi.com/trade-api/v2/markets`).
2. **Filter** — keeps science / tech / space / health markets, plus any
   contract whose `yes_ask` is in the longshot tails (`< 0.15` or `> 0.85`)
   with at least 30 days to resolution and meaningful liquidity.
3. **Score** — for each candidate, computes a "scientific prior" from a
   maintained base-rate table (`src/kalshi_agent/priors.py`) plus optional
   LLM-assisted critique. The edge is `prior - market_price` (for YES) or
   `market_price - prior` (for NO).
4. **Rank** — orders candidates by expected ROI on cost, discounted by
   fee drag, time-to-resolve, and liquidity.
5. **Report** — emits a Markdown table to stdout and persists everything
   to `data/kalshi.duckdb`-compatible SQLite for later calibration.

## Quickstart

```bash
cd kalshi-agent
python -m venv .venv && source .venv/bin/activate
pip install -e .
kalshi-agent scan --limit 200 --min-edge 0.05
```

To run the unit tests:

```bash
pytest
```

## Architecture

```
src/kalshi_agent/
  __init__.py
  api.py          # Kalshi REST client (read-only, no auth needed)
  filters.py      # category + tail-price filtering
  priors.py       # base-rate table for science/tech claims
  scoring.py      # edge, Kelly, fee-adjusted ROI
  storage.py      # SQLite persistence + run history
  report.py       # markdown report rendering
  cli.py          # `kalshi-agent` entry point
```

## Calibration loop

Every closed market is graded against the prior the agent assigned at
scan time. Brier scores are tracked per category in the `calibration`
table. This is the only honest way to know whether the priors are any
good — use it.

## Safety

- Read-only. No trading endpoints are wired up.
- No credentials required.
- Rate-limited to 1 req/sec by default.
