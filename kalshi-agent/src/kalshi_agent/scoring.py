"""Edge, Kelly, and fee-adjusted ROI for a (market, prior) pair."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .api import Market
from .filters import days_until
from .priors import Prior

# Kalshi fee approximation. Real fees depend on price; this is a
# conservative round-trip drag we apply to all gross ROIs.
DEFAULT_FEE_DRAG = 0.02


@dataclass(frozen=True)
class Opportunity:
    market: Market
    prior: Prior
    side: str  # "YES" or "NO"
    cost: float  # cents in [0, 1] you pay per contract
    fair: float  # the prior probability of the side winning
    edge: float  # fair - cost (always >= 0 for listed opportunities)
    roi: float  # edge / cost, fee-adjusted
    kelly_fraction: float  # full-Kelly bankroll fraction (use a fraction of this!)
    days_to_resolve: float
    score: float  # composite ranking score


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


def _scored(o: Opportunity) -> Opportunity:
    """Composite ranking score: ROI, weighted by prior confidence and
    liquidity, divided by sqrt(days) so we prefer faster turns."""
    liquidity = min(1.0, (o.market.open_interest / 5000.0))
    time_penalty = max(1.0, (o.days_to_resolve / 30.0)) ** 0.5
    score = (o.roi * o.prior.confidence * (0.5 + 0.5 * liquidity)) / time_penalty
    return Opportunity(
        market=o.market,
        prior=o.prior,
        side=o.side,
        cost=o.cost,
        fair=o.fair,
        edge=o.edge,
        roi=o.roi,
        kelly_fraction=o.kelly_fraction,
        days_to_resolve=o.days_to_resolve,
        score=score,
    )
