"""Tests for the RSS news fetcher and matching."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_agent.news import (
    CONFIDENCE_REDUCTION_PER_HIT,
    MAX_CONFIDENCE_REDUCTION,
    Headline,
    NewsContext,
    confidence_adjustment,
    parse_rss,
)


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>NYT Science</title>
    <item>
      <title>NASA opens new UAP review panel</title>
      <link>https://www.nytimes.com/2026/04/06/science/uap-nasa.html</link>
      <description>Officials said the panel will publish findings...</description>
      <pubDate>Tue, 07 Apr 2026 06:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Iran nuclear deal talks resume in Geneva</title>
      <link>https://www.nytimes.com/2026/04/06/world/iran-nuclear-deal.html</link>
      <description>Negotiators arrived in Geneva for a fresh round...</description>
      <pubDate>Mon, 06 Apr 2026 22:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Stale headline from a year ago</title>
      <link>https://example.com/old</link>
      <description>nothing relevant</description>
      <pubDate>Fri, 01 Jan 2025 00:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def test_parse_rss_extracts_headlines():
    headlines = parse_rss(SAMPLE_RSS, source="NYT Science")
    assert len(headlines) == 3
    h = headlines[0]
    assert h.source == "NYT Science"
    assert "UAP" in h.title
    assert h.link.startswith("https://www.nytimes.com/")
    assert h.published.tzinfo is not None


def test_news_context_fresh_filters_old_items():
    headlines = parse_rss(SAMPLE_RSS, source="NYT")
    ctx = NewsContext(headlines=headlines)
    # Within a 48h lookback ending now, the 2025-01-01 item must be excluded.
    fresh = ctx.fresh(lookback=timedelta(days=10_000))
    assert len(fresh) == 3  # huge lookback keeps everything
    fresh_recent = ctx.fresh(lookback=timedelta(hours=48))
    # Depending on when the test runs, the 2026-04-06 items may or may not
    # be fresh. The 2025 item must NEVER be fresh against a 48h window.
    assert all(h.published.year >= 2026 for h in fresh_recent)


def test_news_context_matching_picks_relevant():
    # Build a context with a known-fresh headline so we don't depend on
    # the test running before April 9, 2026.
    now = datetime.now(tz=timezone.utc)
    fresh_alien = Headline(
        source="NYT Science",
        title="Pentagon UAP task force expands",
        link="https://example.com/uap",
        published=now - timedelta(hours=3),
    )
    fresh_iran = Headline(
        source="WSJ World",
        title="Iran nuclear talks make progress",
        link="https://example.com/iran",
        published=now - timedelta(hours=5),
    )
    irrelevant = Headline(
        source="NYT Sports",
        title="Lakers win in overtime",
        link="https://example.com/lakers",
        published=now - timedelta(hours=2),
    )
    ctx = NewsContext(headlines=[fresh_alien, fresh_iran, irrelevant])

    alien_hits = ctx.matching([r"\bUAP\b", r"\balien", r"extraterrestrial"])
    assert len(alien_hits) == 1
    assert alien_hits[0].title.startswith("Pentagon UAP")

    iran_hits = ctx.matching([r"\bIran\b.*nuclear", r"\bJCPOA\b"])
    assert len(iran_hits) == 1
    assert "Iran nuclear" in iran_hits[0].title


def test_confidence_adjustment_caps_correctly():
    assert confidence_adjustment(0) == 0.0
    assert confidence_adjustment(1) == CONFIDENCE_REDUCTION_PER_HIT
    assert confidence_adjustment(3) == 3 * CONFIDENCE_REDUCTION_PER_HIT
    # Past the cap.
    assert confidence_adjustment(100) == MAX_CONFIDENCE_REDUCTION


def test_news_drag_lowers_score_in_ranker():
    """A trade with news drag should rank below an otherwise identical
    trade with no news drag."""
    from dataclasses import replace
    from datetime import timedelta

    from kalshi_agent.api import Market
    from kalshi_agent.priors import estimate_prior
    from kalshi_agent.scoring import evaluate, rank

    now_utc = datetime.now(tz=timezone.utc)
    close = (now_utc + timedelta(days=200)).isoformat()

    def mk(ticker: str) -> Market:
        return Market(
            ticker=ticker,
            title="Will the U.S. confirm that aliens exist before 2027?",
            category="",
            yes_bid=0.20,
            yes_ask=0.21,
            volume=1_000,
            open_interest=1_000,
            close_time=close,
            status="active",
        )

    quiet = mk("KXALIENS-Q")
    noisy = mk("KXALIENS-N")
    op_q = replace(
        evaluate(quiet, estimate_prior(quiet, now=now_utc), now=now_utc),
        series_ticker="KXALIENS-Q",
    )
    op_n = replace(
        evaluate(noisy, estimate_prior(noisy, now=now_utc), now=now_utc),
        series_ticker="KXALIENS-N",
        news_headlines=tuple(
            Headline(
                source="NYT",
                title="Pentagon UAP task force expands again",
                link="https://example.com/x",
                published=now_utc - timedelta(hours=3),
            )
            for _ in range(3)
        ),
        news_confidence_drag=confidence_adjustment(3),
    )
    ranked = rank([op_n, op_q])
    # Quiet trade ranks above the noisy one.
    assert ranked[0].market.ticker == "KXALIENS-Q"
    # Noisy one's rank_reason mentions news pressure.
    assert "news mention" in ranked[1].rank_reason
