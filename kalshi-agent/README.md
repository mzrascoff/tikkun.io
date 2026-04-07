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
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
kalshi-agent scan          # pretty terminal table with clickable links
kalshi-agent report        # same scan + writes data/last-report.html
kalshi-agent report --email   # also sends the HTML report by SMTP
```

Tests:
```bash
pytest
```

## Daily email report

The agent ships with everything to run itself every day at 7am via macOS
launchd, build an HTML report, and email it to you.

### 1. Set SMTP credentials

For Gmail you must enable 2FA and create an [app
password](https://myaccount.google.com/apppasswords) (Gmail will refuse
your real password over SMTP). Other providers work too — point the
`KALSHI_SMTP_HOST` env var at their server.

### 2. Edit the launchd plist

Open `scripts/com.mrascoff.kalshi-agent.plist` and replace:
- `/Users/mrascoff/tikkun.io` with the absolute path to your clone
- `YOUR_GMAIL@gmail.com` / `YOUR_GMAIL_APP_PASSWORD` / `YOUR_INBOX@example.com`
  with real values

### 3. Install and start

```bash
cp scripts/com.mrascoff.kalshi-agent.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.mrascoff.kalshi-agent.plist
# fire it once on demand to confirm:
launchctl start com.mrascoff.kalshi-agent
# check the logs:
tail -f data/launchd.out.log data/launchd.err.log
```

The job will fire every day at 07:00 local time and email you the
ranked report. Each row in the email links directly to the Kalshi trade
page for that contract.

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
