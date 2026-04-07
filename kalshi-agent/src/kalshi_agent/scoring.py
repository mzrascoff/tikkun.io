"""Edge, Kelly, and fee-adjusted ROI for a (market, prior) pair."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Iterable, Protocol

from .api import Market
from .filters import days_until
from .priors import Prior


class _PositionLike(Protocol):
    """Structural type for the user's existing Kalshi position on a
    market. Defined here (rather than imported from portfolio.py) so
    scoring.py stays free of any auth/network dependency. portfolio.
    Position satisfies this protocol implicitly."""
    quantity: int
    side: str

# Kalshi fee approximation. Real fees depend on price; this is a
# conservative round-trip drag we apply to all gross ROIs.
DEFAULT_FEE_DRAG = 0.02

# Cap days when annualizing so a 4-year longshot doesn't look better
# than a 6-month one. The longer the window, the more time a tail
# event has to actually fire.
ANNUALIZATION_DAY_CAP = 540  # ~18 months
THIN_EDGE_THRESHOLD = 0.10  # ROI below this gets penalized — fees eat it
LONG_LOCKUP_DAYS = 730       # 2y+ trades take a confidence haircut
CORRELATION_DECAY = 0.5      # 2nd ticket from same series scores 50%, 3rd 25%, ...


@dataclass(frozen=True)
class Opportunity:
    market: Market
    prior: Prior
    side: str
    cost: float
    fair: float
    edge: float
    roi: float                # fee-adjusted dollar ROI on cost
    kelly_fraction: float     # full-Kelly bankroll fraction
    days_to_resolve: float
    score: float              # composite ranking score
    series_ticker: str = ""
    annualized_roi: float = 0.0
    rank_reason: str = ""
    news_headlines: tuple = ()  # tuple[news.Headline, ...]
    news_confidence_drag: float = 0.0  # how much we cut prior.confidence
    venue: str = "kalshi"  # "kalshi" or "polymarket"
    email_mentions: tuple = ()  # tuple[email_reader.EmailItem, ...]
    email_confidence_drag: float = 0.0
    current_position: _PositionLike | None = None
    concentration_pct: float = 0.0   # 0..1 fraction of bankroll already in this ticker


def _kelly(p: float, b: float) -> float:
    """Standard Kelly: f* = (bp - q) / b, where q = 1-p, b = decimal odds - 1."""
    if b <= 0:
        return 0.0
    q = 1.0 - p
    f = (b * p - q) / b
    return max(0.0, f)


def evaluate(
    m: Market,
    prior: Prior,
    *,
    fee_drag: float = DEFAULT_FEE_DRAG,
    now: datetime | None = None,
) -> Opportunity | None:
    """Return the best (YES or NO) trade for this market under `prior`,
    or None if neither side has positive fee-adjusted edge.

    Handles one-sided books: if only yes_bid is quoted, we can still
    evaluate the NO side at cost = 1 - yes_bid; if only yes_ask is
    quoted, we can evaluate the YES side at cost = yes_ask.
    """
    p_yes = prior.probability
    yes_cost = m.yes_ask if 0 < m.yes_ask < 1 else None
    no_cost = (1.0 - m.yes_bid) if 0 < m.yes_bid < 1 else None

    if yes_cost is None and no_cost is None:
        return None

    candidates: list[Opportunity] = []
    days = days_until(m.close_time, now=now)

    # YES side
    if yes_cost is not None:
        yes_edge = p_yes - yes_cost
        if yes_edge > 0:
            b = (1.0 / yes_cost) - 1.0
            gross_roi = yes_edge / yes_cost
            roi = gross_roi - fee_drag
            if roi > 0:
                candidates.append(
                    Opportunity(
                        market=m,
                        prior=prior,
                        side="YES",
                        cost=yes_cost,
                        fair=p_yes,
                        edge=yes_edge,
                        roi=roi,
                        kelly_fraction=_kelly(p_yes, b),
                        days_to_resolve=days,
                        score=0.0,
                    )
                )

    # NO side
    p_no = 1.0 - p_yes
    if no_cost is not None:
        no_edge = p_no - no_cost
        if no_edge > 0:
            b = (1.0 / no_cost) - 1.0
            gross_roi = no_edge / no_cost
            roi = gross_roi - fee_drag
            if roi > 0:
                candidates.append(
                    Opportunity(
                        market=m,
                        prior=prior,
                        side="NO",
                        cost=no_cost,
                        fair=p_no,
                        edge=no_edge,
                        roi=roi,
                        kelly_fraction=_kelly(p_no, b),
                        days_to_resolve=days,
                        score=0.0,
                    )
                )

    if not candidates:
        return None

    best = max(candidates, key=lambda o: o.roi)
    return _scored(best)


def _annualize(roi: float, days: float) -> float:
    """Compound `roi` over `days`, treating days > ANNUALIZATION_DAY_CAP
    as if they were ANNUALIZATION_DAY_CAP. The cap exists because a
    longer window gives a tail event more chances to fire — pretending
    a 4-year contract scales linearly to 1 year is dishonest."""
    if roi <= 0:
        return 0.0
    effective_days = max(min(days, ANNUALIZATION_DAY_CAP), 1.0)
    return (1.0 + roi) ** (365.0 / effective_days) - 1.0


def _scored(o: Opportunity) -> Opportunity:
    """Initial single-trade score (pre correlation pass).

    score = annualized_roi
            * effective_confidence       (prior.confidence - news drag)
            * thin_edge_factor           (penalizes tiny gross ROIs)
            * long_lockup_factor         (penalizes 2y+ capital lockups)
    """
    annualized = _annualize(o.roi, o.days_to_resolve)

    thin_edge_factor = 1.0
    if o.roi < THIN_EDGE_THRESHOLD:
        # Linearly scale from 1.0 at THIN_EDGE_THRESHOLD down to 0 at 0.
        thin_edge_factor = max(0.0, o.roi / THIN_EDGE_THRESHOLD)

    long_lockup_factor = 1.0
    if o.days_to_resolve > LONG_LOCKUP_DAYS:
        # Each additional 365 days past LONG_LOCKUP_DAYS halves the score.
        excess_years = (o.days_to_resolve - LONG_LOCKUP_DAYS) / 365.0
        long_lockup_factor = 0.5 ** excess_years

    effective_confidence = max(
        0.05,
        o.prior.confidence - o.news_confidence_drag - o.email_confidence_drag,
    )
    score = annualized * effective_confidence * thin_edge_factor * long_lockup_factor

    return replace(o, score=score, annualized_roi=annualized)


def rank(opportunities: Iterable[Opportunity]) -> list[Opportunity]:
    """Apply the correlation pass and produce a final best-to-worst
    ordering with rank_reason labels populated.

    Steps:
      1. Sort by initial score (set in _scored).
      2. Walk top-to-bottom: each subsequent ticket from a series we've
         already seen gets its score multiplied by CORRELATION_DECAY ** k.
      3. Re-sort by adjusted score.
      4. Compute and attach a human-readable rank_reason for each.
    """
    # Re-score each opportunity in case its news_confidence_drag was
    # populated after the initial evaluate() call.
    opps = [_scored(o) for o in opportunities]
    if not opps:
        return []

    # Step 1: pre-rank by initial score so the correlation pass walks
    # the highest-conviction trades first.
    opps.sort(key=lambda o: o.score, reverse=True)

    # Step 2: correlation decay
    series_seen: dict[str, int] = {}
    adjusted: list[Opportunity] = []
    for o in opps:
        seen = series_seen.get(o.series_ticker, 0)
        decay = CORRELATION_DECAY ** seen
        adjusted.append(replace(o, score=o.score * decay))
        series_seen[o.series_ticker] = seen + 1

    # Step 3: final sort
    adjusted.sort(key=lambda o: o.score, reverse=True)

    # Step 4: rank_reason labels
    if adjusted:
        best_annualized = max(o.annualized_roi for o in adjusted)
        highest_confidence = max(o.prior.confidence for o in adjusted)
    else:
        best_annualized = 0.0
        highest_confidence = 0.0

    series_rank: dict[str, int] = {}
    labeled: list[Opportunity] = []
    for o in adjusted:
        reasons: list[str] = []
        if o.prior.confidence == highest_confidence and o.prior.confidence >= 0.8:
            reasons.append("highest certainty")
        if abs(o.annualized_roi - best_annualized) < 1e-9 and best_annualized > 0:
            reasons.append("best annualized")
        s_rank = series_rank.get(o.series_ticker, 0)
        if s_rank > 0:
            reasons.append(f"correlated to higher {o.series_ticker} pick")
        if o.roi < THIN_EDGE_THRESHOLD:
            reasons.append("thin edge — fees may eat it")
        if o.days_to_resolve > LONG_LOCKUP_DAYS:
            reasons.append("long capital lockup")
        if o.news_confidence_drag > 0:
            reasons.append(f"{len(o.news_headlines)} fresh news mention(s) — thesis is moving")
        if o.email_confidence_drag > 0:
            reasons.append(f"{len(o.email_mentions)} inbox mention(s) — you're already watching this")
        if o.current_position is not None:
            pos = o.current_position
            if o.concentration_pct >= 0.40:
                reasons.append(
                    f"⚠️ already {o.concentration_pct:.0%} of bankroll on this ticker — DO NOT ADD"
                )
            else:
                reasons.append(
                    f"already holding {pos.quantity} {pos.side} contracts"
                )
        if not reasons:
            reasons.append("solid risk-adjusted return")
        series_rank[o.series_ticker] = s_rank + 1
        labeled.append(replace(o, rank_reason="; ".join(reasons)))

    return labeled
