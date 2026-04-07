"""Typer CLI: `kalshi-agent scan`, `kalshi-agent grade`, `kalshi-agent brier`."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from .api import KalshiClient, Market
from .filters import days_until
from .priors import estimate_prior
from .report import render_markdown
from .scoring import evaluate
from .storage import Store
from .watchlist import WATCHLIST

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


@app.command()
def scan(
    min_edge: float = typer.Option(0.03, help="Minimum fair-vs-cost edge to surface."),
    min_roi: float = typer.Option(0.03, help="Minimum fee-adjusted ROI to surface."),
    min_days: float = typer.Option(7.0, help="Minimum days to resolution."),
    min_oi: int = typer.Option(0, help="Minimum open interest."),
    db: Path = typer.Option(DEFAULT_DB, help="SQLite database path."),
    persist: bool = typer.Option(True, help="Write results to the SQLite store."),
    debug: bool = typer.Option(False, help="Print per-series fetch and matching detail."),
) -> None:
    """Fetch each watchlist series directly, score against priors, rank.

    This is the default mode and the right one to use. It mirrors the
    hand-curated 'Kalshi Edge Opportunities' list.
    """
    client = KalshiClient()
    store = Store(db) if persist else None
    opportunities = []
    series_log: list[tuple[str, int, int, int]] = []  # series, fetched, eligible, scored

    try:
        for series_ticker, label, _why in WATCHLIST:
            try:
                markets = client.fetch_series(series_ticker)
            except Exception as e:
                if debug:
                    console.print(f"[red]ERR[/red] {series_ticker}: {e}")
                series_log.append((series_ticker, 0, 0, 0))
                continue

            if debug:
                console.print(f"\n[bold cyan]{series_ticker}[/bold cyan] -> {len(markets)} markets")
                for m in markets:
                    console.print(
                        f"  {m.ticker}  status={m.status}  bid={m.yes_bid:.2f}  "
                        f"ask={m.yes_ask:.2f}  oi={m.open_interest}  "
                        f"close={m.close_time}  | {m.title}"
                    )

            eligible = []
            for m in markets:
                ok, reason = _eligible(m, min_days=min_days, min_oi=min_oi)
                if not ok:
                    if debug:
                        console.print(f"    [red]drop[/red] {m.ticker}: {reason}")
                    continue
                eligible.append(m)

            scored = 0
            for m in eligible:
                prior = estimate_prior(m)
                if prior is None:
                    if debug:
                        console.print(
                            f"    [yellow]no prior[/yellow] {m.ticker}: {m.title}"
                        )
                    continue
                op = evaluate(m, prior)
                if op is None:
                    if debug:
                        console.print(
                            f"    [dim]no edge[/dim] {m.ticker}: prior={prior.probability:.2%} "
                            f"vs ask={m.yes_ask:.2f} bid={m.yes_bid:.2f}"
                        )
                    continue
                if op.edge < min_edge or op.roi < min_roi:
                    if debug:
                        console.print(
                            f"    [dim]below threshold[/dim] {m.ticker}: "
                            f"side={op.side} edge={op.edge:+.2f} roi={op.roi:.1%}"
                        )
                    continue
                opportunities.append(op)
                scored += 1
            series_log.append((series_ticker, len(markets), len(eligible), scored))
    finally:
        client.close()

    if debug:
        console.print("\n[bold]Watchlist fetch summary[/bold]")
        console.print(f"  {'series':<24} {'fetched':>8} {'eligible':>9} {'scored':>7}")
        for s, f, e, sc in series_log:
            console.print(f"  {s:<24} {f:>8} {e:>9} {sc:>7}")

    total_fetched = sum(f for _, f, _, _ in series_log)
    console.print(
        f"\nFetched {total_fetched} markets across {len(WATCHLIST)} watchlist "
        f"series, surfaced {len(opportunities)} opportunities."
    )
    console.print(render_markdown(opportunities))

    if store and opportunities:
        scan_id = store.record_scan(opportunities)
        console.print(f"\n[dim]Persisted scan #{scan_id} to {db}[/dim]")


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
