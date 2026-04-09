"""Render ranked opportunities for the terminal (rich.Table) and email (HTML).

The public renderers in this module come in two shapes:

  * Flat renderers (`render_rich_table`, `render_html`, `render_markdown`):
    take a list of Opportunities and produce one table/section. Used
    for tests and backward compatibility.

  * Grouped renderers (`render_grouped_sections`, `render_grouped_html`):
    take pre-grouped OpportunityGroups split into new vs held, and
    produce two sections — "New opportunities" and "Already in your
    portfolio" — with correlated contracts collapsed into a single
    entry per group. This is what the CLI uses by default.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Iterable

from rich.table import Table
from rich.text import Text

from .scoring import Opportunity, OpportunityGroup
from .watchlist import trade_url


def render_portfolio_panel(portfolio_ctx) -> Table:
    """Compact rich.Table summarizing the user's current Kalshi positions."""
    table = Table(
        title=f"Your Kalshi Portfolio  —  ${portfolio_ctx.total_bankroll_dollars:.2f} bankroll  ·  ${portfolio_ctx.balance.settled_dollars:.2f} cash",
        title_style="bold magenta",
        header_style="bold",
        show_lines=False,
        expand=True,
    )
    table.add_column("Ticker", style="bold")
    table.add_column("Side", justify="center", width=4)
    table.add_column("Qty", justify="right", width=6)
    table.add_column("Avg cost", justify="right", width=10)
    table.add_column("Cost basis", justify="right", width=12)
    table.add_column("Mkt value", justify="right", width=12)
    table.add_column("% of bankroll", justify="right", width=14)

    bankroll = portfolio_ctx.total_bankroll_dollars or 1.0
    for p in sorted(portfolio_ctx.positions, key=lambda x: x.market_value_cents, reverse=True):
        pct = p.market_value_dollars / bankroll
        warn = " ⚠️" if pct >= 0.40 else ""
        side_style = "green" if p.side == "NO" else "yellow"
        table.add_row(
            p.ticker,
            Text(p.side, style=side_style),
            str(p.quantity),
            f"${p.avg_cost:.2f}",
            f"${p.cost_basis_dollars:.2f}",
            f"${p.market_value_dollars:.2f}",
            f"{pct:.0%}{warn}",
        )
    return table


def _signal_line(*, icon: str, icon_color: str, primary_html: str, secondary: str) -> str:
    return (
        f'<div style="margin-top:4px; font-size:11px;">'
        f'<span style="color:{icon_color};">{icon}</span> '
        f'{primary_html} '
        f'<span style="color:#888;">{escape(secondary)}</span>'
        f'</div>'
    )


def _side_panel(lines: list[str], *, bg: str, border: str) -> str:
    if not lines:
        return ""
    return (
        f'<div style="margin-top:8px; padding:6px 8px; background:{bg}; '
        f'border-left:3px solid {border}; border-radius:2px;">'
        + "".join(lines)
        + "</div>"
    )


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
        # Show the most recent matching inbox subject.
        if o.email_mentions:
            top_email = o.email_mentions[0]
            cell_lines.append(
                f"[blue]📧[/blue] {escape(top_email.subject[:90])} [dim]({escape(top_email.sender[:40])})[/dim]"
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


def _render_portfolio_html(portfolio_ctx) -> str:
    """HTML block summarizing the user's current Kalshi positions."""
    if portfolio_ctx is None or portfolio_ctx.balance is None or not portfolio_ctx.positions:
        return ""
    bankroll = portfolio_ctx.total_bankroll_dollars or 1.0
    rows_html = ""
    for p in sorted(portfolio_ctx.positions, key=lambda x: x.market_value_cents, reverse=True):
        pct = p.market_value_dollars / bankroll
        warn = ' <span style="color:#a36600;">⚠️</span>' if pct >= 0.40 else ""
        side_color = "#0a7a30" if p.side == "NO" else "#a36600"
        rows_html += (
            f'<tr style="background:#fff;">'
            f'<td style="padding:6px 8px; border-bottom:1px solid #f0f0f0; font-family:monospace; font-size:11px;">{escape(p.ticker)}</td>'
            f'<td style="padding:6px 8px; border-bottom:1px solid #f0f0f0; color:{side_color}; font-weight:700;">{p.side}</td>'
            f'<td style="padding:6px 8px; border-bottom:1px solid #f0f0f0; text-align:right; font-variant-numeric:tabular-nums;">{p.quantity}</td>'
            f'<td style="padding:6px 8px; border-bottom:1px solid #f0f0f0; text-align:right; font-variant-numeric:tabular-nums;">${p.avg_cost:.2f}</td>'
            f'<td style="padding:6px 8px; border-bottom:1px solid #f0f0f0; text-align:right; font-variant-numeric:tabular-nums;">${p.market_value_dollars:.2f}</td>'
            f'<td style="padding:6px 8px; border-bottom:1px solid #f0f0f0; text-align:right; font-variant-numeric:tabular-nums; font-weight:600;">{pct:.0%}{warn}</td>'
            f'</tr>'
        )
    return (
        '<div style="margin:0 0 24px 0; padding:12px 14px; background:#faf5ff; border:1px solid #e0d4f0; border-radius:6px;">'
        f'<div style="font-size:14px; font-weight:700; color:#5a2d8a; margin-bottom:8px;">Your Kalshi Portfolio</div>'
        f'<div style="font-size:12px; color:#666; margin-bottom:10px;">'
        f'${portfolio_ctx.total_bankroll_dollars:.2f} total bankroll &middot; '
        f'${portfolio_ctx.balance.settled_dollars:.2f} cash &middot; '
        f'{len(portfolio_ctx.positions)} open position(s)'
        f'</div>'
        '<table cellpadding="0" cellspacing="0" style="border-collapse:collapse; width:100%; font-size:12px;">'
        '<thead><tr style="background:#f0e6fa; text-align:left;">'
        '<th style="padding:6px 8px;">Ticker</th>'
        '<th style="padding:6px 8px;">Side</th>'
        '<th style="padding:6px 8px; text-align:right;">Qty</th>'
        '<th style="padding:6px 8px; text-align:right;">Avg cost</th>'
        '<th style="padding:6px 8px; text-align:right;">Mkt value</th>'
        '<th style="padding:6px 8px; text-align:right;">% bankroll</th>'
        '</tr></thead><tbody>'
        + rows_html +
        '</tbody></table></div>'
    )


def render_html(
    opportunities: Iterable[Opportunity],
    *,
    generated_at: datetime | None = None,
    portfolio_ctx=None,
) -> str:
    """Self-contained HTML email body. Inline CSS for client compatibility.

    Trades are listed best-to-worst as ranked by `scoring.rank()`. If a
    `portfolio_ctx` with positions is supplied, a portfolio panel is
    rendered above the opportunities table.
    """
    rows = _ordered(opportunities)
    when = (generated_at or datetime.utcnow()).strftime("%Y-%m-%d %H:%M UTC")
    head = f"""<!doctype html>
<html><head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color:#1a1a1a; max-width:820px; margin:24px auto; padding:0 16px;">
  <h1 style="font-size:20px; margin:0 0 4px 0;">Kalshi Edge Opportunities</h1>
  <p style="color:#666; margin:0 0 18px 0; font-size:13px;">Generated {when} &middot; ranked best to worst &middot; annualized ROI shown &middot; ¼-Kelly suggested sizing</p>
"""
    head += _render_portfolio_html(portfolio_ctx)

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

        news_lines = [
            _signal_line(
                icon="📰",
                icon_color="#a36600",
                primary_html=f'<a href="{escape(h.link)}" style="color:#0a66c2; text-decoration:none;">{escape(h.title)}</a>',
                secondary=f"({h.source})",
            )
            for h in o.news_headlines
        ]
        news_block = _side_panel(news_lines, bg="#fff8e6", border="#f0c040")

        inbox_lines = [
            _signal_line(
                icon="📧",
                icon_color="#0a66c2",
                primary_html=f"<strong>{escape(em.subject)}</strong>",
                secondary=f"— {em.sender}",
            )
            for em in o.email_mentions
        ]
        inbox_block = _side_panel(inbox_lines, bg="#eef4ff", border="#4a8df0")

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
          {inbox_block}
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


# ----------------- grouped two-section renderers -----------------

def _group_table(title: str, title_style: str, groups: list[OpportunityGroup]) -> Table:
    """Build one rich.Table for a list of OpportunityGroups."""
    table = Table(
        title=title,
        title_style=title_style,
        header_style="bold",
        show_lines=True,
        expand=True,
    )
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("Thesis", style="bold cyan", overflow="fold")
    table.add_column("Side", justify="center", width=4)
    table.add_column("Best ROI", justify="right", width=9, style="bold green")
    table.add_column("Annual.", justify="right", width=8, style="bold green")
    table.add_column("¼-Kelly", justify="right", width=8)
    table.add_column("News", justify="center", width=5)
    table.add_column("Notes", overflow="fold")

    if not groups:
        table.add_row("—", "none", "", "", "", "", "", "")
        return table

    for i, g in enumerate(groups, 1):
        p = g.primary
        url = trade_url(p.series_ticker, p.market.ticker, p.venue)
        # First line: human label + venue badge
        venue_color = "magenta" if g.venue == "polymarket" else "cyan"
        header_line = f"[link={url}]{escape(g.label)}[/link] [dim]({g.venue})[/dim]"

        # Sub-contracts block: list up to 4 sibling tickers with their
        # individual cost / ROI / days — so the user sees the full shape
        # of the correlated bet without the ranker treating them as
        # independent positions.
        contract_lines = []
        for c in g.contracts[:5]:
            cost_str = f"${c.cost:.2f}"
            contract_lines.append(
                f"  [dim]{escape(c.market.ticker)}[/dim]  "
                f"{cost_str}  ROI {c.roi:.0%}  {c.annualized_roi:.0%}/yr  "
                f"{c.days_to_resolve:.0f}d"
            )
        if len(g.contracts) > 5:
            contract_lines.append(f"  [dim]... and {len(g.contracts) - 5} more[/dim]")

        # Position info for held groups
        position_line = ""
        if g.held:
            held_dollars = g.held_market_value_dollars
            max_pct = g.max_concentration_pct
            warn = " ⚠️ DO NOT ADD" if max_pct >= 0.40 else ""
            position_line = (
                f"[bold magenta]Currently holding ${held_dollars:.0f} "
                f"({max_pct:.0%} of bankroll){warn}[/bold magenta]"
            )

        # Top headline, if any
        news_line = ""
        if p.news_headlines:
            top = p.news_headlines[0]
            news_line = (
                f"[yellow]📰[/yellow] [link={escape(top.link)}]{escape(top.title[:80])}[/link] "
                f"[dim]({escape(top.source)})[/dim]"
            )

        cell_lines = [header_line]
        if position_line:
            cell_lines.append(position_line)
        cell_lines.extend(contract_lines)
        if news_line:
            cell_lines.append(news_line)
        cell_lines.append(f"[dim]{escape(p.prior.rationale)}[/dim]")

        link_cell = Text.from_markup("\n".join(cell_lines))
        side_style = "green" if p.side == "NO" else "yellow"
        news_count = sum(len(c.news_headlines) for c in g.contracts)
        news_cell = (
            Text(str(news_count), style="bold yellow")
            if news_count else Text("·", style="dim")
        )

        table.add_row(
            str(i),
            link_cell,
            Text(p.side, style=side_style),
            f"{p.roi:.0%}",
            f"{p.annualized_roi:.0%}/yr",
            f"{p.kelly_fraction / 4:.0%}",
            news_cell,
            p.rank_reason or "—",
        )
    return table


def render_grouped_sections(
    new_groups: list[OpportunityGroup],
    held_groups: list[OpportunityGroup],
) -> list[Table]:
    """Return a list of rich Tables: the new-opportunities table and
    (if there are any held groups) the held-positions table."""
    tables = [
        _group_table(
            title="New opportunities  —  you do NOT currently hold these",
            title_style="bold cyan",
            groups=new_groups,
        )
    ]
    if held_groups:
        tables.append(
            _group_table(
                title="Already in your portfolio  —  ranker's view of positions you hold",
                title_style="bold magenta",
                groups=held_groups,
            )
        )
    return tables


def _render_group_html_row(g: OpportunityGroup, i: int, *, row_bg: str) -> str:
    """One row of the grouped HTML email: header + child contracts."""
    p = g.primary
    url = trade_url(p.series_ticker, p.market.ticker, p.venue)
    label = escape(g.label)
    venue = escape(g.venue)
    venue_color = "#7c2db5" if g.venue == "polymarket" else "#0a66c2"
    side_color = "#0a7a30" if p.side == "NO" else "#a36600"
    rationale = escape(p.prior.rationale)

    contract_rows_html = ""
    for c in g.contracts[:5]:
        c_url = trade_url(c.series_ticker, c.market.ticker, c.venue)
        contract_rows_html += (
            f'<div style="margin-top:4px; font-size:11px; font-family:monospace; color:#555;">'
            f'<a href="{c_url}" style="color:#0a66c2; text-decoration:none;">{escape(c.market.ticker)}</a> '
            f'&nbsp;&middot;&nbsp; cost ${c.cost:.2f}'
            f' &nbsp;&middot;&nbsp; ROI {c.roi:.0%}'
            f' &nbsp;&middot;&nbsp; {c.annualized_roi:.0%}/yr'
            f' &nbsp;&middot;&nbsp; {c.days_to_resolve:.0f}d'
            f'</div>'
        )
    if len(g.contracts) > 5:
        contract_rows_html += (
            f'<div style="margin-top:4px; font-size:11px; color:#888;">'
            f'... and {len(g.contracts) - 5} more variants</div>'
        )

    position_html = ""
    if g.held:
        max_pct = g.max_concentration_pct
        warn = ' <strong style="color:#b00020;">⚠️ DO NOT ADD</strong>' if max_pct >= 0.40 else ""
        position_html = (
            f'<div style="margin-top:8px; padding:6px 8px; background:#fff0f8; '
            f'border-left:3px solid #b00020; border-radius:2px; font-size:12px;">'
            f'<strong>Currently holding ${g.held_market_value_dollars:.0f} '
            f'({max_pct:.0%} of bankroll){warn}</strong></div>'
        )

    news_html = ""
    if p.news_headlines:
        news_lines = [
            _signal_line(
                icon="📰",
                icon_color="#a36600",
                primary_html=(
                    f'<a href="{escape(h.link)}" style="color:#0a66c2; text-decoration:none;">'
                    f'{escape(h.title)}</a>'
                ),
                secondary=f"({h.source})",
            )
            for h in p.news_headlines
        ]
        news_html = _side_panel(news_lines, bg="#fff8e6", border="#f0c040")

    return f"""    <tr style="background:{row_bg}; vertical-align:top;">
      <td style="padding:10px 8px; border-bottom:1px solid #eee; color:#888; font-weight:700;">{i}</td>
      <td style="padding:10px 8px; border-bottom:1px solid #eee;">
        <div style="font-size:14px; font-weight:600;">
          <a href="{url}" style="color:#0a66c2; text-decoration:none;">{label}</a>
          <span style="display:inline-block; margin-left:6px; padding:1px 6px; border-radius:8px; background:{venue_color}; color:#fff; font-size:10px; font-weight:700; text-transform:uppercase;">{venue}</span>
        </div>
        <div style="color:#555; font-size:12px; margin-top:4px;">{rationale}</div>
        {contract_rows_html}
        {position_html}
        {news_html}
      </td>
      <td style="padding:10px 8px; border-bottom:1px solid #eee; color:{side_color}; font-weight:700;">{p.side}</td>
      <td style="padding:10px 8px; border-bottom:1px solid #eee; text-align:right; font-weight:700; color:#0a7a30; font-variant-numeric:tabular-nums;">{p.roi:.0%}</td>
      <td style="padding:10px 8px; border-bottom:1px solid #eee; text-align:right; font-weight:700; color:#0a7a30; font-variant-numeric:tabular-nums;">{p.annualized_roi:.0%}/yr</td>
      <td style="padding:10px 8px; border-bottom:1px solid #eee; text-align:right; font-variant-numeric:tabular-nums;">{p.kelly_fraction / 4:.0%}</td>
      <td style="padding:10px 8px; border-bottom:1px solid #eee; color:#444; font-size:12px;">{escape(p.rank_reason or "")}</td>
    </tr>
"""


def _render_groups_html_table(title: str, subtitle: str, groups: list[OpportunityGroup]) -> str:
    if not groups:
        return (
            f'<h2 style="font-size:16px; margin:24px 0 4px 0; color:#444;">{escape(title)}</h2>'
            f'<p style="color:#888; margin:0 0 18px 0; font-size:12px;"><em>None.</em></p>'
        )
    rows_html = "".join(
        _render_group_html_row(g, i, row_bg="#ffffff" if i % 2 else "#fafafa")
        for i, g in enumerate(groups, 1)
    )
    return f"""
  <h2 style="font-size:16px; margin:24px 0 4px 0; color:#444;">{escape(title)}</h2>
  <p style="color:#888; margin:0 0 10px 0; font-size:12px;">{escape(subtitle)}</p>
  <table cellpadding="0" cellspacing="0" style="border-collapse:collapse; width:100%; font-size:13px;">
    <thead>
      <tr style="background:#f4f4f6; text-align:left;">
        <th style="padding:8px;">#</th>
        <th style="padding:8px;">Thesis / contracts</th>
        <th style="padding:8px;">Side</th>
        <th style="padding:8px; text-align:right;">Best ROI</th>
        <th style="padding:8px; text-align:right;">Annual.</th>
        <th style="padding:8px; text-align:right;">¼-Kelly</th>
        <th style="padding:8px;">Notes</th>
      </tr>
    </thead>
    <tbody>
{rows_html}    </tbody>
  </table>
"""


def render_grouped_html(
    new_groups: list[OpportunityGroup],
    held_groups: list[OpportunityGroup],
    *,
    generated_at: datetime | None = None,
    portfolio_ctx=None,
) -> str:
    """Self-contained HTML email body with two sections: new opportunities
    and existing positions, with correlated contracts collapsed into
    single entries per group."""
    when = (generated_at or datetime.utcnow()).strftime("%Y-%m-%d %H:%M UTC")
    head = f"""<!doctype html>
<html><head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color:#1a1a1a; max-width:900px; margin:24px auto; padding:0 16px;">
  <h1 style="font-size:20px; margin:0 0 4px 0;">Kalshi Edge Report</h1>
  <p style="color:#666; margin:0 0 18px 0; font-size:13px;">Generated {when} &middot; correlated contracts grouped &middot; ¼-Kelly suggested sizing</p>
"""
    head += _render_portfolio_html(portfolio_ctx)

    new_html = _render_groups_html_table(
        title="New opportunities",
        subtitle="You do NOT currently hold any contracts in these theses. Ranked best to worst.",
        groups=new_groups,
    )
    held_html = _render_groups_html_table(
        title="Already in your portfolio",
        subtitle="Opportunities on theses where you already hold positions. "
                 "The ranker flags positions over 40% concentration as DO NOT ADD.",
        groups=held_groups,
    )

    footer = """
  <p style="color:#888; font-size:11px; margin-top:18px;">
    Sizing column shows ¼-Kelly. Full-Kelly is fat-tailed; never bet more than this.
    Geopolitical positions should be ⅛-Kelly. Numbers are fee-adjusted (~2pp drag).
    Generated by kalshi-agent.
  </p>
</body></html>
"""
    return head + new_html + held_html + footer
