"""Tests for the Polymarket gamma API integration."""
from __future__ import annotations

from kalshi_agent.polymarket import (
    POLYMARKET_FEE_DRAG,
    PolymarketKeywordSet,
    filter_by_watchlist,
    market_from_gamma,
    matches_any,
    trade_url,
)


SAMPLE_GAMMA_BINARY = {
    "id": "12345",
    "slug": "will-aliens-be-confirmed-by-2027",
    "question": "Will the U.S. government confirm alien life by 2027?",
    "category": "Science",
    "active": True,
    "closed": False,
    "endDate": "2027-01-01T00:00:00Z",
    "volume": "4500000",
    "liquidity": "85000",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.21", "0.79"]',
}

SAMPLE_GAMMA_REVERSED = {
    "id": "67890",
    "slug": "will-openai-announce-agi-by-2027",
    "question": "Will OpenAI announce AGI by 2027?",
    "active": True,
    "closed": False,
    "endDate": "2027-01-01T00:00:00Z",
    "outcomes": '["No", "Yes"]',  # reversed!
    "outcomePrices": '["0.86", "0.14"]',
}

SAMPLE_GAMMA_MULTI = {
    "id": "33333",
    "slug": "presidential-election",
    "question": "Who will win the 2028 presidential election?",
    "outcomes": '["Vance", "Newsom", "Other"]',
    "outcomePrices": '["0.40", "0.30", "0.30"]',
    "active": True,
    "closed": False,
}


def test_market_from_gamma_binary():
    m = market_from_gamma(SAMPLE_GAMMA_BINARY)
    assert m is not None
    assert m.ticker == "will-aliens-be-confirmed-by-2027"
    assert m.title.startswith("Will the U.S.")
    assert abs(m.yes_bid - 0.21) < 1e-9
    assert abs(m.yes_ask - 0.21) < 1e-9
    assert m.status == "active"
    assert m.volume == 4_500_000
    assert m.open_interest == 85_000


def test_market_from_gamma_handles_reversed_outcomes():
    m = market_from_gamma(SAMPLE_GAMMA_REVERSED)
    assert m is not None
    # Yes is the second outcome here, priced 0.14.
    assert abs(m.yes_bid - 0.14) < 1e-9


def test_market_from_gamma_skips_multi_outcome():
    assert market_from_gamma(SAMPLE_GAMMA_MULTI) is None


def test_market_from_gamma_skips_missing_prices():
    bad = {**SAMPLE_GAMMA_BINARY, "outcomePrices": ""}
    assert market_from_gamma(bad) is None


def test_filter_by_watchlist_picks_alien_market():
    markets = [
        market_from_gamma(SAMPLE_GAMMA_BINARY),
        market_from_gamma(SAMPLE_GAMMA_REVERSED),
    ]
    matched = filter_by_watchlist([m for m in markets if m is not None])
    # Both should match — alien and OpenAI/AGI watchlist entries.
    assert len(matched) == 2
    labels = {kws.label for _, kws in matched}
    assert "Aliens / UAP disclosure" in labels
    assert "OpenAI / AGI" in labels


def test_filter_by_watchlist_skips_unmatched():
    sport = market_from_gamma({
        "id": "1",
        "slug": "lakers-win-title",
        "question": "Will the Lakers win the title?",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.18","0.82"]',
        "active": True,
        "closed": False,
        "endDate": "2027-01-01T00:00:00Z",
    })
    assert sport is not None
    assert filter_by_watchlist([sport]) == []


def test_matches_any_is_case_insensitive():
    assert matches_any("Pentagon UAP task force expands", (r"\bUAP\b",))
    assert matches_any("alien life is real", (r"\balien",))
    assert not matches_any("nothing relevant", (r"\bUAP\b", r"\balien"))


def test_trade_url_format():
    assert trade_url("will-aliens") == "https://polymarket.com/market/will-aliens"


def test_polymarket_fee_drag_is_lower_than_kalshi():
    from kalshi_agent.scoring import DEFAULT_FEE_DRAG
    assert POLYMARKET_FEE_DRAG < DEFAULT_FEE_DRAG


def test_evaluate_uses_polymarket_fee_drag():
    """A trade that's marginal at Kalshi fees should still score at Polymarket fees."""
    from datetime import datetime, timedelta, timezone
    from kalshi_agent.api import Market
    from kalshi_agent.priors import estimate_prior
    from kalshi_agent.scoring import evaluate

    now = datetime.now(tz=timezone.utc)
    m = Market(
        ticker="will-aliens",
        title="Will the U.S. confirm aliens before 2027?",
        category="",
        yes_bid=0.20,
        yes_ask=0.21,
        volume=1000,
        open_interest=1000,
        close_time=(now + timedelta(days=200)).isoformat(),
        status="active",
    )
    prior = estimate_prior(m, now=now)
    op_kalshi = evaluate(m, prior, now=now)  # default fee drag
    op_poly = evaluate(m, prior, fee_drag=POLYMARKET_FEE_DRAG, now=now)
    assert op_kalshi is not None and op_poly is not None
    # Polymarket fees give a higher net ROI on the same trade.
    assert op_poly.roi > op_kalshi.roi
