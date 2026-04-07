"""Candidate filtering: keep markets where a science-based prior is likely
to disagree with the crowd.

The category gate is intentionally NOT enforced — Kalshi's category
strings vary across endpoints and we'd rather let the prior dispatcher
in `priors.py` decide based on title keywords. The structural filters
(tail price, days to resolve, liquidity) are the gate that matters.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .api import Market

DEFAULT_TAIL_LOW = 0.20
DEFAULT_TAIL_HIGH = 0.80
DEFAULT_MIN_DAYS_TO_RESOLVE = 30
DEFAULT_MIN_OPEN_INTEREST = 100


@dataclass
class FilterStats:
    """Histogram of why markets were dropped — for `--debug`."""
    seen: int = 0
    bad_status: int = 0
    bad_price: int = 0
    not_in_tail: int = 0
    too_short: int = 0
    too_illiquid: int = 0
    passed: int = 0


def days_until(close_time_iso: str, now: datetime | None = None) -> float:
    if not close_time_iso:
        return 0.0
    now = now or datetime.now(tz=timezone.utc)
    try:
        close = datetime.fromisoformat(close_time_iso.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (close - now).total_seconds() / 86400.0)


def is_candidate(
    m: Market,
    *,
    tail_low: float = DEFAULT_TAIL_LOW,
    tail_high: float = DEFAULT_TAIL_HIGH,
    min_days: int = DEFAULT_MIN_DAYS_TO_RESOLVE,
    min_open_interest: int = DEFAULT_MIN_OPEN_INTEREST,
    now: datetime | None = None,
    stats: FilterStats | None = None,
) -> bool:
    """Structural filter: tail-priced, liquid, far enough out, open status."""
    if stats is not None:
        stats.seen += 1
    if m.status not in ("active", "open"):
        if stats is not None:
            stats.bad_status += 1
        return False
    if m.yes_ask <= 0 or m.yes_ask >= 1:
        if stats is not None:
            stats.bad_price += 1
        return False
    if not (m.yes_ask <= tail_low or m.yes_ask >= tail_high):
        if stats is not None:
            stats.not_in_tail += 1
        return False
    if days_until(m.close_time, now=now) < min_days:
        if stats is not None:
            stats.too_short += 1
        return False
    if m.open_interest < min_open_interest:
        if stats is not None:
            stats.too_illiquid += 1
        return False
    if stats is not None:
        stats.passed += 1
    return True
