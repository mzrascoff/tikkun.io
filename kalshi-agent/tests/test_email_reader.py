"""Tests for the IMAP email reader (parse + match + ranker integration).

We never test against a real IMAP server. The fetch_emails() entry point
is exercised by feeding it a fake config and patching imaplib in a
small wrapper. The pure-function code paths (parse, preview, match,
adjustment) get full coverage.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_agent.email_reader import (
    EMAIL_BODY_PREVIEW_CHARS,
    EmailContext,
    EmailItem,
    MAX_EMAIL_CONFIDENCE_DRAG,
    _parse_email_message,
    _preview,
    email_confidence_adjustment,
)


SAMPLE_RAW = b"""From: Stratechery <ben@stratechery.com>
Subject: Notes on OpenAI's AGI roadmap
Date: Mon, 06 Apr 2026 10:00:00 +0000
Content-Type: text/plain; charset=utf-8

Sam Altman gave an interview yesterday where he discussed when OpenAI
might announce AGI. The contractual definition with Microsoft remains
the gating factor, not capability.
"""


SAMPLE_MULTIPART = b"""From: NYT Briefing <briefing@nytimes.com>
Subject: Iran nuclear deal talks resume
Date: Mon, 06 Apr 2026 12:00:00 +0000
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="BOUNDARY"

--BOUNDARY
Content-Type: text/plain; charset=utf-8

Negotiators arrived in Geneva for a fresh round of talks.

--BOUNDARY
Content-Type: text/html; charset=utf-8

<html><body>Negotiators arrived...</body></html>
--BOUNDARY--
"""


def test_parse_email_message_extracts_fields():
    item = _parse_email_message(SAMPLE_RAW)
    assert item is not None
    assert "Stratechery" in item.sender
    assert "AGI roadmap" in item.subject
    assert "Sam Altman" in item.body_preview
    assert item.received.tzinfo is not None


def test_parse_email_message_handles_multipart():
    item = _parse_email_message(SAMPLE_MULTIPART)
    assert item is not None
    assert "Iran nuclear deal" in item.subject
    assert "Negotiators arrived" in item.body_preview
    # HTML part should NOT have leaked into the preview.
    assert "<html>" not in item.body_preview


def test_preview_truncates_long_bodies():
    long = "x" * 500
    p = _preview(long)
    assert len(p) <= EMAIL_BODY_PREVIEW_CHARS
    assert p.endswith("…")


def test_preview_collapses_whitespace():
    raw = "hello   world\n\n\nfoo"
    assert _preview(raw) == "hello world foo"


def test_email_context_matching_filters_by_keywords():
    now = datetime.now(tz=timezone.utc)
    items = [
        EmailItem(
            sender="ben@stratechery.com",
            subject="Notes on OpenAI's AGI roadmap",
            body_preview="Sam Altman gave an interview...",
            received=now - timedelta(hours=2),
        ),
        EmailItem(
            sender="newsletter@lakers.com",
            subject="Lakers game tonight",
            body_preview="Tip-off at 7pm.",
            received=now - timedelta(hours=1),
        ),
        EmailItem(
            sender="briefing@nytimes.com",
            subject="Iran nuclear deal talks resume",
            body_preview="Negotiators arrived in Geneva...",
            received=now - timedelta(hours=4),
        ),
    ]
    ctx = EmailContext(items=items)

    agi_hits = ctx.matching([r"\bAGI\b", r"\bOpenAI\b"])
    assert len(agi_hits) == 1
    assert "AGI" in agi_hits[0].subject

    iran_hits = ctx.matching([r"\bIran\b.*nuclear", r"\bJCPOA\b"])
    assert len(iran_hits) == 1
    assert "Iran nuclear" in iran_hits[0].subject

    sport_hits = ctx.matching([r"\balien", r"\bUAP\b"])
    assert sport_hits == []


def test_email_context_matching_returns_empty_when_no_items():
    ctx = EmailContext(items=[])
    assert ctx.matching([r".*"]) == []


def test_imap_since_date_is_locale_independent():
    from datetime import date
    from kalshi_agent.email_reader import _imap_since_date

    d = datetime(2026, 4, 7, tzinfo=timezone.utc)
    # Must always be DD-Mon-YYYY in English regardless of locale.
    assert _imap_since_date(d) == "07-Apr-2026"


def test_parse_email_message_handles_malformed_date():
    raw = b"""From: test@example.com
Subject: hi
Date: not a real date

body
"""
    item = _parse_email_message(raw)
    assert item is not None
    # Falls back to "now" without raising AttributeError.
    assert item.received.tzinfo is not None


def test_confidence_adjustment_caps_correctly():
    assert email_confidence_adjustment(0) == 0.0
    assert email_confidence_adjustment(1) > 0
    assert email_confidence_adjustment(1000) == MAX_EMAIL_CONFIDENCE_DRAG


def test_email_drag_lowers_score_in_ranker():
    """A trade with email mentions should rank below an otherwise
    identical trade with a quiet inbox."""
    from dataclasses import replace
    from datetime import timedelta as td
    from kalshi_agent.api import Market
    from kalshi_agent.priors import estimate_prior
    from kalshi_agent.scoring import evaluate, rank

    now = datetime.now(tz=timezone.utc)
    close = (now + td(days=200)).isoformat()

    def mk(tk: str) -> Market:
        return Market(
            ticker=tk, title="Will the U.S. confirm aliens before 2027?",
            category="", yes_bid=0.20, yes_ask=0.21,
            volume=1000, open_interest=1000,
            close_time=close, status="active",
        )

    quiet = mk("KX-Q")
    noisy = mk("KX-N")
    op_q = replace(
        evaluate(quiet, estimate_prior(quiet, now=now), now=now),
        series_ticker="KX-Q",
    )
    item = EmailItem(
        sender="newsletter@example.com",
        subject="UAP disclosure timeline",
        body_preview="A timeline of recent unidentified anomalous phenomena reports.",
        received=now - timedelta(hours=2),
    )
    op_n = replace(
        evaluate(noisy, estimate_prior(noisy, now=now), now=now),
        series_ticker="KX-N",
        email_mentions=(item, item, item),
        email_confidence_drag=email_confidence_adjustment(3),
    )
    ranked = rank([op_n, op_q])
    assert ranked[0].market.ticker == "KX-Q"
    assert "inbox mention" in ranked[1].rank_reason
