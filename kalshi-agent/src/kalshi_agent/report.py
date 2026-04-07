"""Render ranked opportunities for the terminal (rich.Table) and email (HTML)."""
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Iterable

from rich.table import Table
from rich.text import Text

from .scoring import Opportunity
from .watchlist import trade_url


def _ordered(opps: Iterable[Opportunity]) -> list[Opportunity]:
    """Trust the input order if it looks pre-ranked (rank() populates
    rank_reason); otherwise fall back to sorting by score."""
    rows = list(opps)
    if rows and any(o.rank_reason for o in rows):
        return rows
    return sorted(rows, key=lambda o: o.score, reverse=True)


def render_markdown(opportunities: Iterable[Opportunity]) -> str:
    """Plain-markdown table for stdout dumps and tests."""
    rows = _ordered(opportunities)
    if not rows:
        return "_No opportunities found._"
    lines = [
        "| # | Venue | Ticker | Title | Side | Cost | ROI | Annualized | Days | Why this rank | Link |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, o in enumerate(rows, 1):
        title = (o.market.title or o.market.ticker).replace("|", "\\|")
        url = trade_url(o.series_ticker, o.market.ticker, o.venue)
        reason = (o.rank_reason or "").replace("|", "\\|")
        lines.append(
            f"| {i} | {o.venue} | `{o.market.ticker}` | {title} | **{o.side}** | "
            f"{o.cost:.2f} | {o.roi:.1%} | {o.annualized_roi:.0%}/yr | "
            f"{o.days_to_resolve:.0f} | {reason} | [trade]({url}) |"
        )
    return "\n".join(lines)


def render_rich_table(opportunities: Iterable[Opportunity]) -> Table:
    """Pretty terminal table with clickable trade links (OSC 8).

    Trades are listed best-to-worst as ranked by `scoring.rank()`.
    """
    rows = _ordered(opportunities)
    table = Table(
        title="Kalshi Edge Opportunities  —  best to worst",
        title_style="bold cyan",
        header_style="bold",
        show_lines=True,
        expand=True,
    )
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("Venue", justify="center", width=10)
    table.add_column("Trade", style="bold cyan", overflow="fold")
    table.add_column("Side", justify="center", width=4)
    table.add_column("Cost", justify="right", width=6)
    table.add_column("ROI", justify="right", width=7, style="bold green")
    table.add_column("Annual.", justify="right", width=8, style="bold green")
    table.add_column("¼-Kelly", justify="right", width=8)
    table.add_column("Days", justify="right", width=5)
    table.add_column("News", justify="center", width=5)
    table.add_column("Why this rank", overflow="fold")

    if not rows:
        table.add_row("—", "", "No opportunities found", "", "", "", "", "", "", "", "")
        return table

    for i, o in enumerate(rows, 1):
        url = trade_url(o.series_ticker, o.market.ticker, o.venue)
        title = o.market.title or o.market.ticker
        venue_color = "magenta" if o.venue == "polymarket" else "cyan"
        venue_cell = Text(o.venue, style=f"bold {venue_color}")
        cell_lines = [f"[link={url}]{escape(title)}[/link]", f"[dim]{o.market.ticker}[/dim]"]
        # Show the most recent matching headline directly under the trade.
        if o.news_headlines:
            top = o.news_headlines[0]
            cell_lines.append(
                f"[yellow]📰[/yellow] [link={escape(top.link)}]{escape(top.title[:90])}[/link] [dim]({escape(top.source)})[/dim]"
            )
        link_cell = Text.from_markup("\n".join(cell_lines))
        side_style = "green" if o.side == "NO" else "yellow"
        news_cell = (
            Text(str(len(o.news_headlines)), style="bold yellow")
            if o.news_headlines else Text("·", style="dim")
        )
        table.add_row(
            str(i),
            venue_cell,
            link_cell,
            Text(o.side, style=side_style),
            f"{o.cost:.2f}",
            f"{o.roi:.1%}",
            f"{o.annualized_roi:.0%}/yr",
            f"{o.kelly_fraction / 4:.1%}",
            f"{o.days_to_resolve:.0f}",
            news_cell,
            o.rank_reason or o.prior.rationale,
        )
    return table


def render_html(opportunities: Iterable[Opportunity], *, generated_at: datetime | None = None) -> str:
    """Self-contained HTML email body. Inline CSS for client compatibility.

    Trades are listed best-to-worst as ranked by `scoring.rank()`.
    """
    rows = _ordered(opportunities)
    when = (generated_at or datetime.utcnow()).strftime("%Y-%m-%d %H:%M UTC")
    head = f"""<!doctype html>
<html><head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color:#1a1a1a; max-width:820px; margin:24px auto; padding:0 16px;">
  <h1 style="font-size:20px; margin:0 0 4px 0;">Kalshi Edge Opportunities</h1>
  <p style="color:#666; margin:0 0 18px 0; font-size:13px;">Generated {when} &middot; ranked best to worst &middot; annualized ROI shown &middot; ¼-Kelly suggested sizing</p>
"""

    if not rows:
        return head + "<p><em>No opportunities found in today's scan.</em></p></body></html>"

    table = """  <table cellpadding="8" cellspacing="0" style="border-collapse:collapse; width:100%; font-size:13px;">
    <thead>
      <tr style="background:#f4f4f6; text-align:left;">
        <th style="border-bottom:2px solid #ddd;">#</th>
        <th style="border-bottom:2px solid #ddd;">Venue</th>
        <th style="border-bottom:2px solid #ddd;">Trade</th>
        <th style="border-bottom:2px solid #ddd;">Side</th>
        <th style="border-bottom:2px solid #ddd; text-align:right;">Cost</th>
        <th style="border-bottom:2px solid #ddd; text-align:right;">ROI</th>
        <th style="border-bottom:2px solid #ddd; text-align:right;">Annual.</th>
        <th style="border-bottom:2px solid #ddd; text-align:right;">¼-Kelly</th>
        <th style="border-bottom:2px solid #ddd; text-align:right;">Days</th>
        <th style="border-bottom:2px solid #ddd;">Why this rank</th>
      </tr>
    </thead>
    <tbody>
"""
    for i, o in enumerate(rows, 1):
        url = trade_url(o.series_ticker, o.market.ticker, o.venue)
        title = escape(o.market.title or o.market.ticker)
        ticker = escape(o.market.ticker)
        rationale = escape(o.prior.rationale)
        reason = escape(o.rank_reason or "")
        side_color = "#0a7a30" if o.side == "NO" else "#a36600"
        row_bg = "#ffffff" if i % 2 else "#fafafa"
        venue_color = "#7c2db5" if o.venue == "polymarket" else "#0a66c2"
        venue_label = escape(o.venue)

        news_block = ""
        if o.news_headlines:
            news_lines = []
            for h in o.news_headlines:
                news_lines.append(
                    f'<div style="margin-top:4px; font-size:11px;">'
                    f'<span style="color:#a36600;">📰</span> '
                    f'<a href="{escape(h.link)}" style="color:#0a66c2; text-decoration:none;">{escape(h.title)}</a> '
                    f'<span style="color:#888;">({escape(h.source)})</span>'
                    f'</div>'
                )
            news_block = (
                '<div style="margin-top:8px; padding:6px 8px; background:#fff8e6; '
                'border-left:3px solid #f0c040; border-radius:2px;">'
                + "".join(news_lines)
                + '</div>'
            )

        table += f"""      <tr style="background:{row_bg}; vertical-align:top;">
        <td style="border-bottom:1px solid #eee; color:#888; font-weight:700;">{i}</td>
        <td style="border-bottom:1px solid #eee;">
          <span style="display:inline-block; padding:2px 8px; border-radius:10px; background:{venue_color}; color:#fff; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px;">{venue_label}</span>
        </td>
        <td style="border-bottom:1px solid #eee;">
          <a href="{url}" style="color:#0a66c2; text-decoration:none; font-weight:600;">{title}</a><br>
          <span style="color:#888; font-family:monospace; font-size:11px;">{ticker}</span><br>
          <span style="color:#555; font-size:12px;">{rationale}</span>
          {news_block}
        </td>
        <td style="border-bottom:1px solid #eee; color:{side_color}; font-weight:700;">{o.side}</td>
        <td style="border-bottom:1px solid #eee; text-align:right; font-variant-numeric:tabular-nums;">{o.cost:.2f}</td>
        <td style="border-bottom:1px solid #eee; text-align:right; font-weight:700; color:#0a7a30; font-variant-numeric:tabular-nums;">{o.roi:.1%}</td>
        <td style="border-bottom:1px solid #eee; text-align:right; font-weight:700; color:#0a7a30; font-variant-numeric:tabular-nums;">{o.annualized_roi:.0%}/yr</td>
        <td style="border-bottom:1px solid #eee; text-align:right; font-variant-numeric:tabular-nums;">{o.kelly_fraction / 4:.1%}</td>
        <td style="border-bottom:1px solid #eee; text-align:right; font-variant-numeric:tabular-nums;">{o.days_to_resolve:.0f}</td>
        <td style="border-bottom:1px solid #eee; color:#444; font-size:12px;">{reason}</td>
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
