"""Typer CLI: `kalshi-agent scan`, `kalshi-agent grade`, `kalshi-agent brier`."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import typer
from rich.console import Console

from .api import KalshiClient
from .filters import FilterStats, is_candidate
from .priors import estimate_prior
from .report import render_markdown
from .scoring import evaluate
from .storage import Store

app = typer.Typer(help="Science-grounded Kalshi mispricing scanner.")
console = Console()

DEFAULT_DB = Path("data/kalshi.sqlite")


@app.command()
def scan(
    limit: int = typer.Option(2000, help="Maximum markets to inspect."),
    min_edge: float = typer.Option(0.05, help="Minimum fair-vs-cost edge to surface."),
    min_roi: float = typer.Option(0.05, help="Minimum fee-adjusted ROI to surface."),
    db: Path = typer.Option(DEFAULT_DB, help="SQLite database path."),
    persist: bool = typer.Option(True, help="Write results to the SQLite store."),
    debug: bool = typer.Option(False, help="Print filter histogram and sample tail markets."),
) -> None:
    """Pull open markets, score against priors, print a ranked report."""
    client = KalshiClient()
    store = Store(db) if persist else None
    stats = FilterStats()
    categories: Counter[str] = Counter()
    tail_samples: list[tuple[str, str, float, str]] = []  # (ticker, title, ask, category)
    no_prior: list[tuple[str, str, float]] = []  # tail markets that fell through priors
    opportunities = []
    try:
        for m in client.iter_markets(status="open"):
            if stats.seen >= limit:
                break
            categories[m.category or "(blank)"] += 1
            passed = is_candidate(m, stats=stats)
            if not passed:
                continue
            if len(tail_samples) < 25:
                tail_samples.append((m.ticker, m.title, m.yes_ask, m.category))
            prior = estimate_prior(m)
            if prior is None:
                no_prior.append((m.ticker, m.title, m.yes_ask))
                continue
            op = evaluate(m, prior)
            if op is None:
                continue
            if op.edge < min_edge or op.roi < min_roi:
                continue
            opportunities.append(op)
    finally:
        client.close()

    console.print(
        f"Scanned {stats.seen} markets, {stats.passed} passed structural filters, "
        f"found {len(opportunities)} opportunities."
    )
    console.print(render_markdown(opportunities))

    if debug:
        console.print("\n[bold]Filter histogram[/bold]")
        console.print(
            f"  bad_status={stats.bad_status}  bad_price={stats.bad_price}  "
            f"not_in_tail={stats.not_in_tail}  too_short={stats.too_short}  "
            f"too_illiquid={stats.too_illiquid}  passed={stats.passed}"
        )
        console.print("\n[bold]Top categories seen[/bold]")
        for cat, n in categories.most_common(15):
            console.print(f"  {n:5d}  {cat}")
        console.print(
            f"\n[bold]Sample tail markets that passed structural filters "
            f"({len(tail_samples)} of {stats.passed})[/bold]"
        )
        for ticker, title, ask, cat in tail_samples:
            console.print(f"  [{ask:.2f}] [{cat}] {ticker}: {title}")
        console.print(
            f"\n[bold]Tail markets with no matching prior rule "
            f"({len(no_prior)})[/bold] — add rules to priors.py for these:"
        )
        for ticker, title, ask in no_prior[:25]:
            console.print(f"  [{ask:.2f}] {ticker}: {title}")

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
