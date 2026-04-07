"""Curated watchlist of known science / tech / geopolitical longshot
markets where a literature-grounded prior should beat the crowd.

Each entry includes the canonical Kalshi series-page URL so the report
can link directly to the trade page.
"""
from __future__ import annotations

from typing import NamedTuple


class Series(NamedTuple):
    ticker: str           # e.g. "KXALIENS"
    url: str              # canonical kalshi.com series-page URL
    label: str            # short human label
    why: str              # rationale for inclusion


# URL pattern is `https://kalshi.com/markets/<series_lower>/<slug>` and
# specific contracts append `/<ticker_lower>`.
WATCHLIST: tuple[Series, ...] = (
    # ----- Science / cosmology -----
    Series("KXALIENS",  "https://kalshi.com/markets/kxaliens/aliens",
           "Aliens confirmed", "ET disclosure has 0 historical hits"),
    Series("KXSUPERCON", "https://kalshi.com/markets/supercon/roomtemp-superconductor",
           "Room-temp superconductor", "RT-ambient SC never replicated"),

    # ----- AI / AGI -----
    Series("KXOAIAGI",  "https://kalshi.com/markets/kxoaiagi/openai-achieves-agi",
           "OpenAI AGI", "Contractual, not capability-based"),
    Series("KXAGI",     "https://kalshi.com/markets/kxagi/agi",
           "AGI declared", "Generic AGI longshots"),

    # ----- Space / Mars -----
    Series("KXHLS",     "https://kalshi.com/markets/kxhls/hls",
           "SpaceX HLS test", "Starship is years behind crewed cadence"),
    Series("KXSTARSHIP", "https://kalshi.com/markets/kxstarship/starship",
           "Starship milestones", "Elon-time discount"),
    Series("KXMARS",    "https://kalshi.com/markets/kxmars/mars",
           "Mars landing", "No life support, no orbital refuel"),

    # ----- Fusion / energy -----
    Series("KXFUSION",  "https://kalshi.com/markets/kxfusion/nuclear-fusion",
           "Commercial fusion", "Grid fusion not on credible roadmap"),

    # ----- Health -----
    Series("KXCURE",    "https://kalshi.com/markets/kxcure/disease-cures",
           "Disease cures", "No 'cure' announcement has ever resolved YES"),
    Series("KXALZ",     "https://kalshi.com/markets/kxalz/alzheimers",
           "Alzheimer's cure", "No disease-modifying cure exists"),

    # ----- Geopolitics where a science/evidence prior helps -----
    Series("KXIRANNUKE", "https://kalshi.com/markets/kxirannuke/iran-nuclear-weapon",
           "Iran nuclear weapon", "Weaponization gating step is months+"),
    Series("KXUSAIRANAGREEMENT",
           "https://kalshi.com/markets/kxusairanagreement/us-iran-nuclear-deal",
           "US-Iran nuclear deal", "Historical base rate is very low"),
)


SERIES_BY_TICKER: dict[str, Series] = {s.ticker: s for s in WATCHLIST}


def trade_url(series_ticker: str, market_ticker: str) -> str:
    """Build a direct trade-page URL for a specific market."""
    series = SERIES_BY_TICKER.get(series_ticker)
    if series is None:
        return f"https://kalshi.com/markets/{series_ticker.lower()}"
    return f"{series.url}/{market_ticker.lower()}"
