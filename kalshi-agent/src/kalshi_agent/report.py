"""Render a ranked list of opportunities as a Markdown table."""
from __future__ import annotations

from typing import Iterable

from .scoring import Opportunity


def render_markdown(opportunities: Iterable[Opportunity]) -> str:
    rows = list(opportunities)
    if not rows:
        return "_No opportunities found._"
    rows.sort(key=lambda o: o.score, reverse=True)

    lines = [
        "| # | Ticker | Title | Side | Cost | Fair | Edge | ROI | Kelly | Days | Why |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, o in enumerate(rows, 1):
        title = (o.market.title or o.market.ticker).replace("|", "\\|")
        rationale = o.prior.rationale.replace("|", "\\|")
        lines.append(
            f"| {i} | `{o.market.ticker}` | {title} | **{o.side}** | "
            f"{o.cost:.2f} | {o.fair:.2f} | {o.edge:+.2f} | {o.roi:.1%} | "
            f"{o.kelly_fraction:.1%} | {o.days_to_resolve:.0f} | {rationale} |"
        )
    return "\n".join(lines)
