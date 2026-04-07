"""Candidate filtering: keep markets where a science-based prior is likely
to disagree with the crowd."""
from __future__ import annotations

from datetime import datetime, timezone

from .api import Market

# Categories where we believe a literature-grounded prior can outperform
# the crowd. Sports/elections are excluded — those crowds are sharper than
# we are.
SCIENCE_CATEGORIES = {
    "Science",
    "Climate",
    "Health",
    "Space",
    "Technology",
    "AI",
    "Crypto",  # only included for tail-strike longshots
}

DEFAULT_TAIL_LOW = 0.20
DEFAULT_TAIL_HIGH = 0.80
DEFAULT_MIN_DAYS_TO_RESOLVE = 30
DEFAULT_MIN_OPEN_INTEREST = 100


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
) -> bool:
    """A market is a candidate iff:
      * its category is in our science set, AND
      * its yes_ask sits in a tail (longshot or near-cert), AND
      * it has enough time to resolve, AND
      * it has minimum liquidity.
    """
    if m.status != "active" and m.status != "open":
        # Kalshi uses both labels in different schemas; accept either.
        return False
    if m.category not in SCIENCE_CATEGORIES:
        return False
    if m.yes_ask <= 0 or m.yes_ask >= 1:
        return False
    if not (m.yes_ask <= tail_low or m.yes_ask >= tail_high):
        return False
    if days_until(m.close_time, now=now) < min_days:
        return False
    if m.open_interest < min_open_interest:
        return False
    return True
