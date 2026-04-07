"""Typer CLI: `kalshi-agent scan`, `kalshi-agent report`, `kalshi-agent grade`, `kalshi-agent brier`."""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console

from .api import KalshiClient, Market
from .filters import days_until
from .mailer import EmailConfigError, send_html_email
from .news import NewsContext, confidence_adjustment, fetch_news
from .priors import estimate_prior
from .report import render_html, render_markdown, render_rich_table
from .scoring import Opportunity, evaluate, rank
from .storage import Store
from .watchlist import SERIES_BY_TICKER, WATCHLIST

app = typer.Typer(help="Science-grounded Kalshi mispricing scanner.")
console = Console()

DEFAULT_DB = Path("data/kalshi.sqlite")


CLOSED_STATUSES = {"closed", "settled", "finalized", "determined"}


def _eligible(m: Market, *, min_days: float, min_oi: int) -> tuple[bool, str]:
    """Light structural filter for watchlist markets. Returns (ok, reason)."""
    if m.status.lower() in CLOSED_STATUSES:
        return False, f"status={m.status}"
    # Accept either side having a price. If only yes_bid is set, we can
    # still trade NO at (1 - yes_bid). If only yes_ask is set, we can
    # still trade YES at yes_ask.
    if m.yes_ask <= 0 and m.yes_bid <= 0:
        return False, "no quotes"
    if m.yes_ask >= 1 and m.yes_bid >= 1:
        return False, "fully resolved"
    days = days_until(m.close_time)
    if days < min_days:
        return False, f"only {days:.1f}d to resolve"
    if m.open_interest < min_oi:
        return False, f"oi={m.open_interest}"
    return True, "ok"


def _run_scan(
    *,
    min_edge: float,
    min_roi: float,
    min_days: float,
    min_oi: int,
    news: bool = True,
) -> tuple[list[Opportunity], list[tuple[str, int, int, int]], dict, NewsContext]:
    """Shared fetch+score loop. Returns (opportunities, series_log, diag, news_ctx)."""
    client = KalshiClient()
    news_ctx = fetch_news() if news else NewsContext()
    if news_ctx.headlines:
        console.print(
            f"[dim]Pulled {len(news_ctx.headlines)} headlines from "
            f"{len(set(h.source for h in news_ctx.headlines))} feeds[/dim]"
        )
    if news_ctx.fetch_errors:
        for src, err in news_ctx.fetch_errors.items():
            console.print(f"[dim yellow]news warn {src}: {err}[/dim yellow]")
    opportunities: list[Opportunity] = []
    series_log: list[tuple[str, int, int, int]] = []
    diag: dict[str, list[dict]] = {}

    try:
        for series in WATCHLIST:
            series_ticker = series.ticker
            try:
                stubs = client.fetch_series(series_ticker)
            except Exception:
                series_log.append((series_ticker, 0, 0, 0))
                diag[series_ticker] = []
                continue

            # The series endpoint returns metadata stubs with empty
            # order books. Re-fetch each market individually (across
            # multiple Kalshi hosts) for the live quote.
            markets: list[Market] = []
            fetch_debug: dict[str, str] = {}
            for stub in stubs:
                live, attempts = client.get_market(stub.ticker)
                fetch_debug[stub.ticker] = attempts
                markets.append(live or stub)

            series_diag: list[dict] = []
            eligible = []
            for m in markets:
                row: dict = {**dataclasses.asdict(m)}
                row["fetch_debug"] = fetch_debug.get(m.ticker, "")
                ok, reason = _eligible(m, min_days=min_days, min_oi=min_oi)
                if not ok:
                    row["verdict"] = f"drop:{reason}"
                    series_diag.append(row)
                    continue
                eligible.append(m)
                row["verdict"] = "eligible"
                series_diag.append(row)

            scored = 0
            for m in eligible:
                prior = estimate_prior(m)
                row = next(r for r in series_diag if r["ticker"] == m.ticker)
                if prior is None:
                    row["verdict"] = "no_prior"
                    continue
                row["prior_p"] = prior.probability
                row["prior_rationale"] = prior.rationale
                op = evaluate(m, prior)
                if op is None:
                    row["verdict"] = "no_edge"
                    continue
                # tag with originating series so the renderer can build URLs
                op = dataclasses.replace(op, series_ticker=series_ticker)
                # Attach news pressure for this series.
                series_meta = SERIES_BY_TICKER.get(series_ticker)
                if series_meta and series_meta.news_keywords:
                    matched = news_ctx.matching(list(series_meta.news_keywords))
                    drag = confidence_adjustment(len(matched))
                    op = dataclasses.replace(
                        op,
                        news_headlines=tuple(matched),
                        news_confidence_drag=drag,
                    )
                row["side"] = op.side
                row["edge"] = op.edge
                row["roi"] = op.roi
                row["news_hits"] = len(op.news_headlines)
                if op.edge < min_edge or op.roi < min_roi:
                    row["verdict"] = "below_threshold"
                    continue
                row["verdict"] = "scored"
                opportunities.append(op)
                scored += 1
            diag[series_ticker] = series_diag
            series_log.append((series_ticker, len(markets), len(eligible), scored))
    finally:
        client.close()

    return opportunities, series_log, diag, news_ctx


def _print_summary(
    opportunities: list[Opportunity],
    series_log: list[tuple[str, int, int, int]],
    diag: dict,
    *,
    show_table: bool = True,
) -> None:
    diag_path = Path("data/last-scan-debug.json")
    diag_path.parent.mkdir(parents=True, exist_ok=True)
    diag_path.write_text(json.dumps(diag, indent=2, default=str))

    console.print("\n[bold]Watchlist fetch summary[/bold]")
    console.print(f"  {'series':<24} {'fetched':>8} {'eligible':>9} {'scored':>7}")
    for s, f, e, sc in series_log:
        console.print(f"  {s:<24} {f:>8} {e:>9} {sc:>7}")

    total_fetched = sum(f for _, f, _, _ in series_log)
    console.print(
        f"\nFetched {total_fetched} markets across {len(WATCHLIST)} watchlist "
        f"series, surfaced {len(opportunities)} opportunities."
    )
    console.print(f"[dim]Per-market diagnostic written to {diag_path}[/dim]\n")

    if show_table:
        console.print(render_rich_table(opportunities))


@app.command()
def scan(
    min_edge: float = typer.Option(0.03, help="Minimum fair-vs-cost edge to surface."),
    min_roi: float = typer.Option(0.03, help="Minimum fee-adjusted ROI to surface."),
    min_days: float = typer.Option(7.0, help="Minimum days to resolution."),
    min_oi: int = typer.Option(0, help="Minimum open interest."),
    db: Path = typer.Option(DEFAULT_DB, help="SQLite database path."),
    persist: bool = typer.Option(True, help="Write results to the SQLite store."),
    news: bool = typer.Option(True, "--news/--no-news", help="Pull RSS news pressure."),
) -> None:
    """Fetch each watchlist series, score, and print a pretty terminal table."""
    opportunities, series_log, diag, _news_ctx = _run_scan(
        min_edge=min_edge, min_roi=min_roi, min_days=min_days, min_oi=min_oi, news=news,
    )
    ranked = rank(opportunities)
    _print_summary(ranked, series_log, diag)
    if persist and ranked:
        scan_id = Store(db).record_scan(ranked)
        console.print(f"\n[dim]Persisted scan #{scan_id} to {db}[/dim]")


@app.command()
def report(
    min_edge: float = typer.Option(0.03),
    min_roi: float = typer.Option(0.03),
    min_days: float = typer.Option(7.0),
    min_oi: int = typer.Option(0),
    db: Path = typer.Option(DEFAULT_DB),
    email: bool = typer.Option(
        False,
        "--email/--no-email",
        help="Send the HTML report via SMTP using KALSHI_SMTP_* env vars.",
    ),
    persist: bool = typer.Option(True),
    news: bool = typer.Option(True, "--news/--no-news"),
) -> None:
    """Run a scan and produce the daily report (terminal + optional email)."""
    opportunities, series_log, diag, _news_ctx = _run_scan(
        min_edge=min_edge, min_roi=min_roi, min_days=min_days, min_oi=min_oi, news=news,
    )
    ranked = rank(opportunities)
    _print_summary(ranked, series_log, diag)

    # Always write the HTML report to disk so launchd users can inspect it.
    now = datetime.now(tz=timezone.utc)
    html = render_html(ranked, generated_at=now)
    html_path = Path("data/last-report.html")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html)
    console.print(f"[dim]HTML report written to {html_path}[/dim]")

    if persist and ranked:
        scan_id = Store(db).record_scan(ranked)
        console.print(f"[dim]Persisted scan #{scan_id} to {db}[/dim]")

    if email:
        try:
            subject = (
                f"Kalshi Edge Report — {now.strftime('%Y-%m-%d')} — "
                f"{len(ranked)} opportunities"
            )
            send_html_email(
                subject=subject,
                html_body=html,
                text_fallback=render_markdown(ranked),
            )
            console.print(f"[green]✓[/green] Emailed report to recipients.")
        except EmailConfigError as e:
            console.print(f"[red]Email config error:[/red] {e}")
            raise typer.Exit(code=2)
        except Exception as e:
            console.print(f"[red]Email send failed:[/red] {type(e).__name__}: {e}")
            raise typer.Exit(code=3)


@app.command()
def probe(ticker: str = typer.Argument(...)) -> None:
    """Hit each Kalshi host directly for `ticker` and print the raw response."""
    import httpx as _httpx

    from .api import BASE_URL_FALLBACKS, DEFAULT_UA

    with _httpx.Client(
        timeout=15, headers={"User-Agent": DEFAULT_UA, "Accept": "application/json"}
    ) as c:
        for base in BASE_URL_FALLBACKS:
            url = f"{base}/markets/{ticker}"
            console.print(f"\n[bold]GET {url}[/bold]")
            try:
                r = c.get(url)
                console.print(f"  HTTP {r.status_code}")
                txt = r.text
                console.print(f"  body[:600]: {txt[:600]}")
            except Exception as e:
                console.print(f"  ERR {type(e).__name__}: {e}")


@app.command()
def grade(
    ticker: str = typer.Argument(..., help="Kalshi ticker to grade."),
    yes: bool = typer.Option(..., help="Did YES win?"),
    db: Path = typer.Option(DEFAULT_DB),
) -> None:
    """Record the resolved outcome of a market for calibration."""
    Store(db).grade(ticker, resolved_yes=yes)
    console.print(f"Graded {ticker} -> {'YES' if yes else 'NO'}")


@app.command()
def brier(db: Path = typer.Option(DEFAULT_DB)) -> None:
    """Print the running Brier score across all graded predictions."""
    score = Store(db).brier()
    if score is None:
        console.print("No graded predictions yet.")
    else:
        console.print(f"Brier score: {score:.4f} (lower is better; 0.25 = coin flip)")


if __name__ == "__main__":
    app()
