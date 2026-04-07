"""Typer CLI: `kalshi-agent scan`, `kalshi-agent grade`, `kalshi-agent brier`."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from .api import KalshiClient
from .filters import is_candidate
from .priors import estimate_prior
from .report import render_markdown
from .scoring import evaluate
from .storage import Store

app = typer.Typer(help="Science-grounded Kalshi mispricing scanner.")
console = Console()

DEFAULT_DB = Path("data/kalshi.sqlite")


@app.command()
def scan(
    limit: int = typer.Option(500, help="Maximum markets to inspect."),
    min_edge: float = typer.Option(0.05, help="Minimum fair-vs-cost edge to surface."),
    min_roi: float = typer.Option(0.05, help="Minimum fee-adjusted ROI to surface."),
    db: Path = typer.Option(DEFAULT_DB, help="SQLite database path."),
    persist: bool = typer.Option(True, help="Write results to the SQLite store."),
) -> None:
    """Pull open markets, score against priors, print a ranked report."""
    client = KalshiClient()
    store = Store(db) if persist else None
    seen = 0
    opportunities = []
    try:
        for m in client.iter_markets(status="open"):
            seen += 1
            if seen > limit:
                break
            if not is_candidate(m):
                continue
            prior = estimate_prior(m)
            if prior is None:
                continue
            op = evaluate(m, prior)
            if op is None:
                continue
            if op.edge < min_edge or op.roi < min_roi:
                continue
            opportunities.append(op)
    finally:
        client.close()

    console.print(f"Scanned {seen} markets, found {len(opportunities)} opportunities.")
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
