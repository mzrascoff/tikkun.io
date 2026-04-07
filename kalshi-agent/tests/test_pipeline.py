"""End-to-end test of the filter -> prior -> scoring pipeline."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_agent.api import Market
from kalshi_agent.filters import is_candidate, days_until
from kalshi_agent.priors import estimate_prior
from kalshi_agent.scoring import evaluate
from kalshi_agent.storage import Store


NOW = datetime(2026, 4, 7, tzinfo=timezone.utc)


def _market(
    *,
    ticker="KX-TEST",
    title="Will the U.S. confirm aliens before 2027?",
    category="Science",
    yes_bid=14,
    yes_ask=16,
    open_interest=10_000,
    days_out=200,
    status="open",
) -> Market:
    close = (NOW + timedelta(days=days_out)).isoformat()
    return Market(
        ticker=ticker,
        title=title,
        category=category,
        yes_bid=yes_bid / 100.0,
        yes_ask=yes_ask / 100.0,
        volume=50_000,
        open_interest=open_interest,
        close_time=close,
        status=status,
    )


def test_days_until_handles_zulu_suffix():
    iso = (NOW + timedelta(days=10)).isoformat().replace("+00:00", "Z")
    assert 9.5 < days_until(iso, now=NOW) < 10.5


def test_alien_market_is_candidate_and_scored_NO():
    m = _market()
    assert is_candidate(m, now=NOW)
    prior = estimate_prior(m, now=NOW)
    assert prior is not None
    op = evaluate(m, prior, now=NOW)
    assert op is not None
    # The market says ~16% YES; our prior is sub-1% over <1y. NO is the trade.
    assert op.side == "NO"
    assert op.cost < 0.90  # buying NO at 1 - yes_bid = 0.86
    assert op.edge > 0.05
    assert op.roi > 0.05
    assert 0 < op.kelly_fraction < 1


def test_balanced_market_yields_no_opportunity():
    m = _market(yes_bid=49, yes_ask=51)
    # Balanced markets should be excluded by the tail filter.
    assert not is_candidate(m, now=NOW)


def test_short_dated_market_excluded():
    m = _market(days_out=5)
    assert not is_candidate(m, now=NOW)


def test_low_liquidity_market_excluded():
    m = _market(open_interest=10)
    assert not is_candidate(m, now=NOW)


def test_unknown_category_no_prior():
    m = _market(title="Will the Lakers win the title?", category="Sports")
    assert not is_candidate(m, now=NOW)
    assert estimate_prior(m, now=NOW) is None


def test_room_temp_superconductor_prior():
    m = _market(
        title="Room-temp superconductor validated this year?",
        yes_bid=8,
        yes_ask=10,
    )
    prior = estimate_prior(m, now=NOW)
    assert prior is not None
    assert prior.probability < 0.02
    op = evaluate(m, prior, now=NOW)
    assert op is not None and op.side == "NO"


def test_storage_round_trip(tmp_path):
    db = tmp_path / "k.sqlite"
    store = Store(db)
    m = _market()
    op = evaluate(m, estimate_prior(m, now=NOW), now=NOW)
    assert op is not None
    scan_id = store.record_scan([op])
    assert scan_id == 1
    # Grade it as a NO win (the science-likely outcome) and check Brier.
    store.grade(m.ticker, resolved_yes=False)
    brier = store.brier()
    assert brier is not None
    # We picked NO with high confidence; outcome was NO -> low Brier.
    assert brier < 0.25
