"""Render ranked opportunities for the terminal (rich.Table) and email (HTML)."""
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Iterable

from rich.table import Table
from rich.text import Text

from .scoring import Opportunity
from .watchlist import trade_url


def _sorted(opps: Iterable[Opportunity]) -> list[Opportunity]:
    return sorted(opps, key=lambda o: o.score, reverse=True)


def render_markdown(opportunities: Iterable[Opportunity]) -> str:
    """Plain-markdown table for stdout dumps and tests."""
    rows = _sorted(opportunities)
    if not rows:
        return "_No opportunities found._"
    lines = [
        "| # | Ticker | Title | Side | Cost | Fair | Edge | ROI | Kelly | Days | Link |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, o in enumerate(rows, 1):
        title = (o.market.title or o.market.ticker).replace("|", "\\|")
        url = trade_url(o.series_ticker, o.market.ticker)
        lines.append(
            f"| {i} | `{o.market.ticker}` | {title} | **{o.side}** | "
            f"{o.cost:.2f} | {o.fair:.2f} | {o.edge:+.2f} | {o.roi:.1%} | "
            f"{o.kelly_fraction:.1%} | {o.days_to_resolve:.0f} | [trade]({url}) |"
        )
    return "\n".join(lines)


def render_rich_table(opportunities: Iterable[Opportunity]) -> Table:
    """Pretty terminal table with clickable trade links (OSC 8)."""
    rows = _sorted(opportunities)
    table = Table(
        title="Kalshi Edge Opportunities  (NO unless noted)",
        title_style="bold cyan",
        header_style="bold",
        show_lines=False,
        expand=True,
    )
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("Trade", style="bold cyan", overflow="fold")
    table.add_column("Side", justify="center", width=4)
    table.add_column("Cost", justify="right", width=6)
    table.add_column("Fair", justify="right", width=6)
    table.add_column("Edge", justify="right", width=6)
    table.add_column("ROI", justify="right", width=7, style="bold green")
    table.add_column("¼-Kelly", justify="right", width=8)
    table.add_column("Days", justify="right", width=5)
    table.add_column("Why", overflow="fold")

    if not rows:
        table.add_row("—", "No opportunities found", "", "", "", "", "", "", "", "")
        return table

    for i, o in enumerate(rows, 1):
        url = trade_url(o.series_ticker, o.market.ticker)
        title = o.market.title or o.market.ticker
        # Rich's link markup renders OSC 8 in supporting terminals.
        link_cell = Text.from_markup(f"[link={url}]{escape(title)}[/link]\n[dim]{o.market.ticker}[/dim]")
        side_style = "green" if o.side == "NO" else "yellow"
        table.add_row(
            str(i),
            link_cell,
            Text(o.side, style=side_style),
            f"{o.cost:.2f}",
            f"{o.fair:.2f}",
            f"{o.edge:+.2f}",
            f"{o.roi:.1%}",
            f"{o.kelly_fraction / 4:.1%}",
            f"{o.days_to_resolve:.0f}",
            o.prior.rationale,
        )
    return table


def render_html(opportunities: Iterable[Opportunity], *, generated_at: datetime | None = None) -> str:
    """Self-contained HTML email body. Inline CSS for client compatibility."""
    rows = _sorted(opportunities)
    when = (generated_at or datetime.utcnow()).strftime("%Y-%m-%d %H:%M UTC")
    head = f"""<!doctype html>
<html><head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color:#1a1a1a; max-width:780px; margin:24px auto; padding:0 16px;">
  <h1 style="font-size:20px; margin:0 0 4px 0;">Kalshi Edge Opportunities</h1>
  <p style="color:#666; margin:0 0 18px 0; font-size:13px;">Generated {when} &middot; ranked by composite score &middot; ¼-Kelly suggested sizing</p>
"""

    if not rows:
        return head + "<p><em>No opportunities found in today's scan.</em></p></body></html>"

    table = """  <table cellpadding="8" cellspacing="0" style="border-collapse:collapse; width:100%; font-size:13px;">
    <thead>
      <tr style="background:#f4f4f6; text-align:left;">
        <th style="border-bottom:2px solid #ddd;">#</th>
        <th style="border-bottom:2px solid #ddd;">Trade</th>
        <th style="border-bottom:2px solid #ddd;">Side</th>
        <th style="border-bottom:2px solid #ddd; text-align:right;">Cost</th>
        <th style="border-bottom:2px solid #ddd; text-align:right;">Fair</th>
        <th style="border-bottom:2px solid #ddd; text-align:right;">Edge</th>
        <th style="border-bottom:2px solid #ddd; text-align:right;">ROI</th>
        <th style="border-bottom:2px solid #ddd; text-align:right;">¼-Kelly</th>
        <th style="border-bottom:2px solid #ddd; text-align:right;">Days</th>
      </tr>
    </thead>
    <tbody>
"""
    for i, o in enumerate(rows, 1):
        url = trade_url(o.series_ticker, o.market.ticker)
        title = escape(o.market.title or o.market.ticker)
        ticker = escape(o.market.ticker)
        why = escape(o.prior.rationale)
        side_color = "#0a7a30" if o.side == "NO" else "#a36600"
        row_bg = "#ffffff" if i % 2 else "#fafafa"
        table += f"""      <tr style="background:{row_bg}; vertical-align:top;">
        <td style="border-bottom:1px solid #eee; color:#888;">{i}</td>
        <td style="border-bottom:1px solid #eee;">
          <a href="{url}" style="color:#0a66c2; text-decoration:none; font-weight:600;">{title}</a><br>
          <span style="color:#888; font-family:monospace; font-size:11px;">{ticker}</span><br>
          <span style="color:#555; font-size:12px;">{why}</span>
        </td>
        <td style="border-bottom:1px solid #eee; color:{side_color}; font-weight:700;">{o.side}</td>
        <td style="border-bottom:1px solid #eee; text-align:right; font-variant-numeric:tabular-nums;">{o.cost:.2f}</td>
        <td style="border-bottom:1px solid #eee; text-align:right; font-variant-numeric:tabular-nums;">{o.fair:.2f}</td>
        <td style="border-bottom:1px solid #eee; text-align:right; font-variant-numeric:tabular-nums;">{o.edge:+.2f}</td>
        <td style="border-bottom:1px solid #eee; text-align:right; font-weight:700; color:#0a7a30; font-variant-numeric:tabular-nums;">{o.roi:.1%}</td>
        <td style="border-bottom:1px solid #eee; text-align:right; font-variant-numeric:tabular-nums;">{o.kelly_fraction / 4:.1%}</td>
        <td style="border-bottom:1px solid #eee; text-align:right; font-variant-numeric:tabular-nums;">{o.days_to_resolve:.0f}</td>
      </tr>
"""
    table += "    </tbody>\n  </table>\n"

    footer = """  <p style="color:#888; font-size:11px; margin-top:18px;">
    Sizing column shows ¼-Kelly. Full-Kelly is fat-tailed; never bet more than this.
    Geopolitical positions should be ⅛-Kelly. Numbers are fee-adjusted (~2pp drag).
    Generated by kalshi-agent.
  </p>
</body></html>
"""
    return head + table + footer
