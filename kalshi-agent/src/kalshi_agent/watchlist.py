"""Curated watchlist of known science / tech / geopolitical longshot
markets where a literature-grounded prior should beat the crowd.

Each entry includes:
  * the canonical Kalshi series-page URL (so the report can link
    directly to the trade page), and
  * a list of regex keywords used by the news fetcher to find
    relevant headlines from FT/NYT/WSJ each morning.
"""
from __future__ import annotations

from typing import NamedTuple


class Series(NamedTuple):
    ticker: str               # e.g. "KXALIENS"
    url: str                  # canonical kalshi.com series-page URL
    label: str                # short human label
    why: str                  # rationale for inclusion
    news_keywords: tuple[str, ...] = ()  # regexes matched against RSS headlines


# URL pattern is `https://kalshi.com/markets/<series_lower>/<slug>` and
# specific contracts append `/<ticker_lower>`.
WATCHLIST: tuple[Series, ...] = (
    # ----- Science / cosmology -----
    Series("KXALIENS",  "https://kalshi.com/markets/kxaliens/aliens",
           "Aliens confirmed", "ET disclosure has 0 historical hits",
           news_keywords=(r"\bUAP\b", r"\bUFO\b", r"\balien", r"extraterrestrial",
                          r"unidentified anomalous", r"NASA disclosure")),
    Series("KXSUPERCON", "https://kalshi.com/markets/supercon/roomtemp-superconductor",
           "Room-temp superconductor", "RT-ambient SC never replicated",
           news_keywords=(r"superconductor", r"room.?temp",
                          r"\bLK-?99\b", r"ambient pressure")),

    # ----- AI / AGI -----
    Series("KXOAIAGI",  "https://kalshi.com/markets/kxoaiagi/openai-achieves-agi",
           "OpenAI AGI", "Contractual, not capability-based",
           news_keywords=(r"\bOpenAI\b", r"\bAGI\b", r"GPT-?5", r"GPT-?6",
                          r"artificial general intelligence", r"Sam Altman")),
    Series("KXAGI",     "https://kalshi.com/markets/kxagi/agi",
           "AGI declared", "Generic AGI longshots",
           news_keywords=(r"\bAGI\b", r"artificial general intelligence",
                          r"superintelligence")),

    # ----- Space / Mars -----
    Series("KXHLS",     "https://kalshi.com/markets/kxhls/hls",
           "SpaceX HLS test", "Starship is years behind crewed cadence",
           news_keywords=(r"SpaceX", r"Starship", r"\bHLS\b",
                          r"Human Landing System", r"Artemis")),
    Series("KXSTARSHIP", "https://kalshi.com/markets/kxstarship/starship",
           "Starship milestones", "Elon-time discount",
           news_keywords=(r"Starship", r"SpaceX", r"\bMusk\b", r"Boca Chica",
                          r"Starbase")),
    Series("KXMARS",    "https://kalshi.com/markets/kxmars/mars",
           "Mars landing", "No life support, no orbital refuel",
           news_keywords=(r"\bMars\b", r"Starship", r"crewed Mars",
                          r"Mars mission")),

    # ----- Fusion / energy -----
    Series("KXFUSION",  "https://kalshi.com/markets/kxfusion/nuclear-fusion",
           "Commercial fusion", "Grid fusion not on credible roadmap",
           news_keywords=(r"\bfusion\b", r"\bITER\b", r"\bNIF\b",
                          r"Commonwealth Fusion", r"Helion", r"TAE Technologies")),

    # ----- Health -----
    Series("KXCURE",    "https://kalshi.com/markets/kxcure/disease-cures",
           "Disease cures", "No 'cure' announcement has ever resolved YES",
           news_keywords=(r"\bcure\b", r"breakthrough therapy", r"FDA approval",
                          r"clinical trial")),
    Series("KXALZ",     "https://kalshi.com/markets/kxalz/alzheimers",
           "Alzheimer's cure", "No disease-modifying cure exists",
           news_keywords=(r"Alzheimer", r"\bdementia\b",
                          r"Leqembi", r"Kisunla", r"amyloid")),

    # ----- Geopolitics where a science/evidence prior helps -----
    Series("KXIRANNUKE", "https://kalshi.com/markets/kxirannuke/iran-nuclear-weapon",
           "Iran nuclear weapon", "Weaponization gating step is months+",
           news_keywords=(r"\bIran\b.*nuclear", r"\bIAEA\b", r"enrichment",
                          r"weaponization", r"\bNatanz\b", r"\bFordow\b")),
    Series("KXUSAIRANAGREEMENT",
           "https://kalshi.com/markets/kxusairanagreement/us-iran-nuclear-deal",
           "US-Iran nuclear deal", "Historical base rate is very low",
           news_keywords=(r"\bIran\b.*deal", r"\bJCPOA\b", r"Tehran.*talks",
                          r"\bIran\b.*agreement", r"Iran.*sanctions")),
)


SERIES_BY_TICKER: dict[str, Series] = {s.ticker: s for s in WATCHLIST}


def trade_url(series_ticker: str, market_ticker: str, venue: str = "kalshi") -> str:
    """Build a direct trade-page URL for a specific market on the given venue."""
    if venue == "polymarket":
        # Polymarket uses one URL per market slug; the slug is stored
        # as the Market.ticker for polymarket-sourced opportunities.
        return f"https://polymarket.com/market/{market_ticker}"
    series = SERIES_BY_TICKER.get(series_ticker)
    if series is None:
        return f"https://kalshi.com/markets/{series_ticker.lower()}"
    return f"{series.url}/{market_ticker.lower()}"
