"""Tests for OpportunityGroup grouping + new/held split + report sections."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from kalshi_agent.api import Market
from kalshi_agent.portfolio import Balance, PortfolioContext, Position
from kalshi_agent.priors import estimate_prior
from kalshi_agent.scoring import (
    OpportunityGroup,
    evaluate,
    group_opportunities,
    rank,
    split_new_vs_held,
)


NOW = datetime(2026, 4, 8, tzinfo=timezone.utc)


def _make_iran_op(ticker: str, days_out: int, yes_ask: float):
    close = (NOW + timedelta(days=days_out)).isoformat()
    m = Market(
        ticker=ticker,
        title="Will the US agree to a new Iranian nuclear deal this year?",
        category="",
        yes_bid=yes_ask - 0.01,
        yes_ask=yes_ask,
        volume=1000,
        open_interest=1000,
        close_time=close,
        status="active",
        event_ticker="KXUSAIRANAGREEMENT",
    )
    op = evaluate(m, estimate_prior(m, now=NOW), now=NOW)
    return replace(op, series_ticker="KXUSAIRANAGREEMENT")


def _make_alien_op(yes_ask: float = 0.21):
    close = (NOW + timedelta(days=268)).isoformat()
    m = Market(
        ticker="KXALIENS-27",
        title="Will the U.S. confirm that aliens exist before 2027?",
        category="",
        yes_bid=yes_ask - 0.01,
        yes_ask=yes_ask,
        volume=5000,
        open_interest=5000,
        close_time=close,
        status="active",
        event_ticker="KXALIENS-27",
    )
    op = evaluate(m, estimate_prior(m, now=NOW), now=NOW)
    return replace(op, series_ticker="KXALIENS")


def test_group_opportunities_collapses_correlated_contracts():
    """4 Iran deal tickets from the same series should become 1 group."""
    opps = [
        _make_iran_op("KXUSAIRANAGREEMENT-27-26MAY", 23, 0.88),
        _make_iran_op("KXUSAIRANAGREEMENT-27-26JUN", 54, 0.73),
        _make_iran_op("KXUSAIRANAGREEMENT-27-26AUG", 115, 0.65),
        _make_iran_op("KXUSAIRANAGREEMENT-27", 268, 0.49),
    ]
    ranked = rank(opps)
    groups = group_opportunities(ranked)
    assert len(groups) == 1
    g = groups[0]
    assert g.series_ticker == "KXUSAIRANAGREEMENT"
    assert g.label == "US-Iran nuclear deal"
    assert len(g.contracts) == 4
    # contracts must be sorted best-to-worst
    scores = [c.score for c in g.contracts]
    assert scores == sorted(scores, reverse=True)
    assert g.primary is g.contracts[0]


def test_group_opportunities_different_series_stay_separate():
    iran = _make_iran_op("KXUSAIRANAGREEMENT-27", 268, 0.49)
    alien = _make_alien_op()
    groups = group_opportunities(rank([iran, alien]))
    assert len(groups) == 2
    series = {g.series_ticker for g in groups}
    assert series == {"KXUSAIRANAGREEMENT", "KXALIENS"}


def test_split_new_vs_held_routes_by_current_position():
    """An opportunity with a current_position should land in the held bucket."""
    iran = _make_iran_op("KXUSAIRANAGREEMENT-27-26JUN", 54, 0.73)
    alien = _make_alien_op()
    pos = Position(
        ticker="KXALIENS-27", side="NO", quantity=1396,
        cost_basis_cents=109708, market_value_cents=109708,
        realized_pnl_cents=0,
    )
    alien_held = replace(alien, current_position=pos, concentration_pct=0.97)
    groups = group_opportunities(rank([iran, alien_held]))
    new, held = split_new_vs_held(groups)
    new_series = {g.series_ticker for g in new}
    held_series = {g.series_ticker for g in held}
    assert new_series == {"KXUSAIRANAGREEMENT"}
    assert held_series == {"KXALIENS"}


def test_group_max_concentration_pct_reflects_worst_contract():
    """A group with one 74%-concentrated contract should report 74%."""
    iran = _make_iran_op("KXUSAIRANAGREEMENT-27-26JUN", 54, 0.73)
    pos = Position(
        ticker="KXUSAIRANAGREEMENT-27-26JUN", side="NO", quantity=12,
        cost_basis_cents=876, market_value_cents=876,
        realized_pnl_cents=0,
    )
    iran_with_pos = replace(iran, current_position=pos, concentration_pct=0.74)
    other_iran = _make_iran_op("KXUSAIRANAGREEMENT-27", 268, 0.49)
    groups = group_opportunities([iran_with_pos, other_iran])
    assert len(groups) == 1
    g = groups[0]
    assert g.held
    assert g.max_concentration_pct == 0.74


def test_position_for_flexible_ticker_match():
    """Should match on exact ticker, event_ticker, or case/whitespace variants."""
    ctx = PortfolioContext(
        balance=Balance(settled_cents=100, reserved_cents=0),
        positions=[
            Position(
                ticker="KXALIENS-27", side="NO", quantity=100,
                cost_basis_cents=10000, market_value_cents=10000,
                realized_pnl_cents=0,
            ),
        ],
    )
    # Exact match
    assert ctx.position_for("KXALIENS-27") is not None
    # Event ticker fallback
    assert ctx.position_for("kxaliens-27-sub", "KXALIENS-27") is not None
    # Case/whitespace normalization
    assert ctx.position_for(" kxaliens-27 ") is not None
    # No match
    assert ctx.position_for("KXOAIAGI-27") is None


def test_render_grouped_sections_produces_one_or_two_tables():
    from kalshi_agent.report import render_grouped_sections

    iran = _make_iran_op("KXUSAIRANAGREEMENT-27", 268, 0.49)
    alien = _make_alien_op()
    pos = Position(
        ticker="KXALIENS-27", side="NO", quantity=1396,
        cost_basis_cents=109708, market_value_cents=109708,
        realized_pnl_cents=0,
    )
    alien_held = replace(alien, current_position=pos, concentration_pct=0.97)
    groups = group_opportunities(rank([iran, alien_held]))
    new, held = split_new_vs_held(groups)

    tables = render_grouped_sections(new, held)
    # Two tables — one for new, one for held
    assert len(tables) == 2

    # No held opportunities -> one table only
    tables_new_only = render_grouped_sections(new, [])
    assert len(tables_new_only) == 1


def test_render_grouped_html_contains_both_sections_and_warnings():
    from kalshi_agent.report import render_grouped_html

    iran = _make_iran_op("KXUSAIRANAGREEMENT-27-26JUN", 54, 0.73)
    alien = _make_alien_op()
    pos = Position(
        ticker="KXALIENS-27", side="NO", quantity=1396,
        cost_basis_cents=109708, market_value_cents=109708,
        realized_pnl_cents=0,
    )
    alien_held = replace(alien, current_position=pos, concentration_pct=0.97)
    groups = group_opportunities(rank([iran, alien_held]))
    new, held = split_new_vs_held(groups)

    html = render_grouped_html(new, held, portfolio_ctx=None)
    assert "New opportunities" in html
    assert "Already in your portfolio" in html
    # US-Iran deal label rendered
    assert "US-Iran nuclear deal" in html
    # Concentration warning fired on the held section
    assert "DO NOT ADD" in html
    # Both children of the Iran group collapsed into one row
    assert "KXUSAIRANAGREEMENT-27-26JUN" in html


def test_render_grouped_html_no_held_section_when_empty():
    from kalshi_agent.report import render_grouped_html

    iran = _make_iran_op("KXUSAIRANAGREEMENT-27", 268, 0.49)
    groups = group_opportunities(rank([iran]))
    new, held = split_new_vs_held(groups)

    html = render_grouped_html(new, held, portfolio_ctx=None)
    assert "New opportunities" in html
    # Held heading still rendered but with "None." message
    assert "Already in your portfolio" in html
    assert "None." in html


def test_render_grouped_html_has_dedicated_news_column():
    """News should live in its own <th>News</th> column, not inlined
    into the thesis cell."""
    from kalshi_agent.report import render_grouped_html

    iran = _make_iran_op("KXUSAIRANAGREEMENT-27-26JUN", 54, 0.73)
    groups = group_opportunities(rank([iran]))
    new, held = split_new_vs_held(groups)
    html = render_grouped_html(new, held, portfolio_ctx=None)
    # The table header must include a standalone News column
    assert ">News</th>" in html


def test_render_recent_markets_table_renders_ticker_and_date():
    from kalshi_agent.api import Market
    from kalshi_agent.report import render_recent_markets_table

    m = Market(
        ticker="KXNEWEVENT-26MAY",
        title="Will the new thing happen before May?",
        category="",
        yes_bid=0.14, yes_ask=0.16,
        volume=50, open_interest=50,
        close_time="2026-05-15T00:00:00Z",
        status="active",
        event_ticker="KXNEWEVENT",
        created_time="2026-04-07T12:00:00Z",
    )
    table = render_recent_markets_table([m])
    # rich.Table doesn't expose rendered string directly; rely on
    # row_count to confirm we added one data row
    assert table.row_count == 1


def test_render_recent_markets_html_includes_newly_listed_markets():
    from kalshi_agent.api import Market
    from kalshi_agent.report import _render_recent_markets_html

    m = Market(
        ticker="KXNEWEVENT-26MAY",
        title="Will the new thing happen before May?",
        category="",
        yes_bid=0.14, yes_ask=0.16,
        volume=50, open_interest=50,
        close_time="2026-05-15T00:00:00Z",
        status="active",
        event_ticker="KXNEWEVENT",
        created_time="2026-04-07T12:00:00Z",
    )
    html = _render_recent_markets_html([m])
    assert "Recently added on Kalshi" in html
    assert "KXNEWEVENT-26MAY" in html
    assert "Will the new thing happen before May?" in html


def test_render_recent_markets_html_empty_state():
    from kalshi_agent.report import _render_recent_markets_html

    html = _render_recent_markets_html([])
    assert "Recently added on Kalshi" in html
    assert "No new markets listed" in html
