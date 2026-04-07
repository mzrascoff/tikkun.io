"""Science-grounded base rates.

Each prior is a function from a Market to a probability that YES resolves
true, plus a short rationale string. The dispatcher uses keyword matches
on the title; unmatched markets fall back to a category default.

This is intentionally a hand-curated table rather than an LLM call. The
calibration loop in `storage.py` lets us update these as evidence comes
in. Add new rules here as you find new market families worth scanning.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .api import Market
from .filters import days_until


@dataclass(frozen=True)
class Prior:
    probability: float  # in [0, 1]
    rationale: str
    confidence: float = 0.5  # how strongly we hold this prior, 0..1


# ---------- domain-specific rules ----------

def _annualized(rate_per_year: float, years: float) -> float:
    """Convert a per-year hazard rate to a probability over `years` years
    under a Poisson assumption."""
    years = max(years, 0.0)
    return 1.0 - math.exp(-rate_per_year * years)


def _years_to_close(m: Market, now: datetime | None = None) -> float:
    return days_until(m.close_time, now=now) / 365.25


def _alien_disclosure(m: Market, now: datetime | None = None) -> Prior | None:
    if not re.search(r"\balien|extraterrestrial|UAP|UFO\b", m.title, re.I):
        return None
    yrs = _years_to_close(m, now)
    # Base rate: in ~80 years of post-Roswell history there has been zero
    # cabinet-level confirmation. Use ~0.5%/yr as a generous upper bound.
    p = _annualized(0.005, yrs)
    return Prior(
        probability=p,
        rationale=(
            "No federal cabinet-level confirmation of ET life in 80+ years. "
            f"Hazard ~0.5%/yr -> ~{p:.1%} over {yrs:.2f}y."
        ),
        confidence=0.85,
    )


def _room_temp_superconductor(m: Market, now: datetime | None = None) -> Prior | None:
    if not re.search(r"room.?temp|superconductor", m.title, re.I):
        return None
    yrs = _years_to_close(m, now)
    # Validated, replicated, ambient-pressure room-temperature SC has
    # never happened. Hg1223 record is still ~140C away. ~0.2%/yr.
    p = _annualized(0.002, yrs)
    return Prior(
        probability=p,
        rationale=(
            "RT ambient-pressure SC has never been replicated. "
            f"Hazard ~0.2%/yr -> ~{p:.1%} over {yrs:.2f}y."
        ),
        confidence=0.9,
    )


def _agi_declared(m: Market, now: datetime | None = None) -> Prior | None:
    if not re.search(r"\bAGI\b|artificial general intelligence", m.title, re.I):
        return None
    yrs = _years_to_close(m, now)
    # AGI 'declarations' are governed by contracts (e.g. OpenAI/MSFT
    # profit clause), not capability tests. Use ~3%/yr through 2030.
    p = _annualized(0.03, yrs)
    return Prior(
        probability=p,
        rationale=(
            "AGI declarations are contractual, not capability-based. "
            f"Hazard ~3%/yr -> ~{p:.1%} over {yrs:.2f}y."
        ),
        confidence=0.55,
    )


def _mars_humans(m: Market, now: datetime | None = None) -> Prior | None:
    if not re.search(r"mars", m.title, re.I):
        return None
    if not re.search(r"human|crew|land|astronaut", m.title, re.I):
        return None
    yrs = _years_to_close(m, now)
    # No life support, no orbital refueling demonstrated, no crewed
    # Starship. Hazard ~0.5%/yr through 2030.
    p = _annualized(0.005, yrs)
    return Prior(
        probability=p,
        rationale=(
            "No demonstrated crewed Starship, life support, or orbital refuel. "
            f"Hazard ~0.5%/yr -> ~{p:.1%} over {yrs:.2f}y."
        ),
        confidence=0.9,
    )


def _commercial_fusion(m: Market, now: datetime | None = None) -> Prior | None:
    if not re.search(r"fusion", m.title, re.I):
        return None
    if not re.search(r"commercial|grid|net.?energy|power", m.title, re.I):
        return None
    yrs = _years_to_close(m, now)
    # NIF achieved scientific Q>1 once; commercial grid power is a
    # decade+ out per ITER and DOE roadmaps. ~0.5%/yr.
    p = _annualized(0.005, yrs)
    return Prior(
        probability=p,
        rationale=(
            "Commercial grid fusion not on credible roadmap before 2030. "
            f"Hazard ~0.5%/yr -> ~{p:.1%} over {yrs:.2f}y."
        ),
        confidence=0.85,
    )


def _cancer_alz_cure(m: Market, now: datetime | None = None) -> Prior | None:
    if not re.search(r"\b(cure|cures|cured)\b", m.title, re.I):
        return None
    if not re.search(r"cancer|alzheim|parkinson|als|hiv", m.title, re.I):
        return None
    yrs = _years_to_close(m, now)
    # "Cure" framings for these diseases have ~0% historical hit rate.
    p = _annualized(0.002, yrs)
    return Prior(
        probability=p,
        rationale=(
            "No historical 'cure' announcement for these diseases. "
            f"Hazard ~0.2%/yr -> ~{p:.1%} over {yrs:.2f}y."
        ),
        confidence=0.9,
    )


def _iran_nuclear_weapon(m: Market, now: datetime | None = None) -> Prior | None:
    if not re.search(r"iran", m.title, re.I):
        return None
    if not re.search(r"nuclear weapon|nuke|warhead|weaponiz", m.title, re.I):
        return None
    yrs = _years_to_close(m, now)
    # Weaponization (not enrichment) is the gating step. IAEA monitoring
    # plus historical breakout-to-weapon timelines put the per-year
    # hazard at ~3-5%. Use 4%.
    p = _annualized(0.04, yrs)
    return Prior(
        probability=p,
        rationale=(
            "Weaponization (not enrichment) gates this. "
            f"Hazard ~4%/yr -> ~{p:.1%} over {yrs:.2f}y."
        ),
        confidence=0.55,
    )


def _iran_nuclear_deal(m: Market, now: datetime | None = None) -> Prior | None:
    if not re.search(r"iran", m.title, re.I):
        return None
    if not re.search(r"nuclear deal|agreement|JCPOA|treaty", m.title, re.I):
        return None
    yrs = _years_to_close(m, now)
    # Negotiated nuclear agreements within ~1y of active conflict are
    # historically rare. ~10%/yr.
    p = _annualized(0.10, yrs)
    return Prior(
        probability=p,
        rationale=(
            "Negotiated nuclear deals are historically rare in <1y windows. "
            f"Hazard ~10%/yr -> ~{p:.1%} over {yrs:.2f}y."
        ),
        confidence=0.5,
    )


# Order matters: more specific rules first.
RULES = (
    _alien_disclosure,
    _room_temp_superconductor,
    _agi_declared,
    _mars_humans,
    _commercial_fusion,
    _cancer_alz_cure,
    _iran_nuclear_weapon,
    _iran_nuclear_deal,
)


def estimate_prior(m: Market, now: datetime | None = None) -> Prior | None:
    """Return a science-grounded prior for `m`, or None if no rule fires."""
    for rule in RULES:
        prior = rule(m, now=now)
        if prior is not None:
            return prior
    return None
