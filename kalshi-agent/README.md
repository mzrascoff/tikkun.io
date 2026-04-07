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

## Polymarket support

The same scan also pulls **Polymarket** in parallel via the public
gamma API (`https://gamma-api.polymarket.com`). No auth required.

For each active binary YES/NO market on Polymarket, the agent:

1. Filters by question-text regex against the curated keyword groups
   in `polymarket.POLYMARKET_WATCHLIST` (alien disclosure, OpenAI/AGI,
   crewed Mars, room-temp superconductor, commercial fusion, disease
   cures, Iran nuclear weapon, Iran nuclear deal).
2. Runs the same `priors.estimate_prior()` and `scoring.evaluate()`
   that Kalshi markets use — the priors are keyword-matched on titles,
   not venue-specific identifiers.
3. Applies a smaller **fee drag of 0.5%** instead of Kalshi's 2%
   (Polymarket has no taker fees; the 0.5% covers spread only).
4. Tags the resulting `Opportunity` with `venue="polymarket"`.

Both venues' opportunities go into the same `rank()` call, so the
final daily report is a unified, best-to-worst list across both
exchanges. The terminal table and HTML email both show a colored
**Venue** badge for each row, and the trade link points to either
`kalshi.com/markets/...` or `polymarket.com/market/...` automatically.

Skip Polymarket with `--no-polymarket` (e.g. when running offline or
when you only want Kalshi's regulated markets).

```bash
kalshi-agent scan --no-polymarket    # Kalshi only
kalshi-agent scan                    # both venues, default
```

## Inbox signal (read-only IMAP)

The agent can also scan your **personal inbox** for fresh signal each
morning, using read-only IMAP. Newsletter mentions, friends' tips,
exchange alerts — anything you're already paying attention to is a
strong indicator that the *price* of a thesis is moving on flow that
the static science prior cannot see. The agent demotes those trades
in the ranker (without touching the underlying probability) and shows
the matching subjects right next to the trade in the report.

### Privacy guarantees (read these)

- **Read-only.** The agent opens INBOX with `readonly=True`. It
  never sends, deletes, marks-read, forwards, or modifies any message.
- **No bodies are persisted.** Only the sender, subject, and the
  first 120 characters of the body of *matching* messages appear in
  the diagnostic JSON or the report. Full bodies are dropped after
  matching.
- **Credentials live in env vars** (`KALSHI_IMAP_*`), never in the
  diagnostic dump or any log file.
- Default lookback is 3 days, capped at 200 messages per scan.
- Skip the inbox scan with `--no-inbox`.

### Setup

1. Enable IMAP in Gmail settings, then create an [app
   password](https://myaccount.google.com/apppasswords) (the same
   one you use for the SMTP daily-report sender works fine — Gmail
   app passwords are valid for both protocols).
2. Add the `KALSHI_IMAP_*` block to your launchd plist (see
   `scripts/com.mrascoff.kalshi-agent.plist` for the template).
3. Test on demand:

```bash
KALSHI_IMAP_HOST=imap.gmail.com \
KALSHI_IMAP_USER=you@gmail.com \
KALSHI_IMAP_PASS='your_app_password' \
kalshi-agent scan
```

If your inbox has a relevant message, you'll see a `📧` indicator on
that row in the terminal table and a blue inbox block in the email.

## News analysis

Each morning's scan also pulls public RSS feeds from the New York
Times, Wall Street Journal, and Financial Times (no auth required —
the feeds are headlines + abstracts only). For every market on the
watchlist, the agent counts how many fresh headlines (last 48 hours)
match the series' keyword set, and:

1. **Surfaces the matching headlines** in both the terminal report
   and the daily HTML email, with direct links to each article.
2. **Reduces the trade's effective confidence** in the ranker by
   `0.05 × N` (capped at `0.25`). Heavy news flow on a thesis pushes
   that trade *down* the ranking — when a market is moving on news,
   you do not want to be the one fading it cold.

The science prior itself is **never** mechanically overridden by news.
The base rate stays the base rate; only the trade's weighting in the
final ranker shifts. The user sees the headlines and can override.

To skip the news fetch entirely (e.g. when running offline):

```bash
kalshi-agent scan --no-news
kalshi-agent report --no-news
```

### Caveats

- FT/NYT/WSJ articles are paywalled. The agent only sees what each
  publication exposes in its public RSS feed (headline + short
  abstract). Anything that needs the full article body needs a
  subscription and a per-publication scraper, which is fragile.
- Feed URLs occasionally rotate. Failures are non-fatal — the agent
  prints a warning and skips that feed.

## Architecture

```
src/kalshi_agent/
  __init__.py
  api.py          # Kalshi REST client (read-only, no auth needed)
  polymarket.py   # Polymarket gamma API client + watchlist + filter
  filters.py      # tail-price + liquidity filtering
  watchlist.py    # curated Kalshi series + venue-aware URL builder
  priors.py       # venue-agnostic base-rate table for science claims
  news.py         # FT / NYT / WSJ RSS reader + matching
  email_reader.py # READ-ONLY IMAP inbox scanner for personal signal
  scoring.py      # edge, Kelly, annualized ROI, multi-venue ranker
  storage.py      # SQLite persistence + run history
  report.py       # rich + markdown + HTML renderers with venue badge
  mailer.py       # SMTP email sender for daily report
  cli.py          # `kalshi-agent` entry point
scripts/
  com.mrascoff.kalshi-agent.plist   # macOS launchd job, runs daily 7am
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
