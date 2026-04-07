"""Curated watchlist of known science / tech / geopolitical longshot
markets where a literature-grounded prior should beat the crowd.

Each entry is a Kalshi *series ticker* (e.g. `KXALIENS`), which groups
related markets across resolution dates. The scanner expands each
series into its individual markets via the API.

Add new entries here as you find them. The matching `priors.py` rule
must already exist or the prior dispatcher will skip the market.

Series tickers are sourced from the public Kalshi market URLs in the
form `kalshi.com/markets/<series>/<slug>`.
"""
from __future__ import annotations

# (series_ticker, short label, why-it's-here)
WATCHLIST: tuple[tuple[str, str, str], ...] = (
    # ----- Science / cosmology -----
    ("KXALIENS", "Aliens confirmed", "ET disclosure has 0 historical hits"),
    ("KXSUPERCON", "Room-temp superconductor", "RT-ambient SC never replicated"),

    # ----- AI / AGI -----
    ("KXOAIAGI", "OpenAI AGI", "Contractual, not capability-based"),
    ("KXAGI", "AGI declared", "Generic AGI longshots"),

    # ----- Space / Mars -----
    ("KXHLS", "SpaceX HLS test", "Starship is years behind crewed cadence"),
    ("KXSTARSHIP", "Starship milestones", "Elon-time discount"),
    ("KXMARS", "Mars landing", "No life support, no orbital refuel"),

    # ----- Fusion / energy -----
    ("KXFUSION", "Commercial fusion", "Grid fusion not on credible roadmap"),

    # ----- Health -----
    ("KXCURE", "Disease cures", "No 'cure' announcement has ever resolved YES"),
    ("KXALZ", "Alzheimer's cure", "No disease-modifying cure exists"),

    # ----- Geopolitics where a science/evidence prior helps -----
    ("KXIRANNUKE", "Iran nuclear weapon", "Weaponization gating step is months+"),
    ("KXUSAIRANAGREEMENT", "US-Iran nuclear deal", "Historical base rate is very low"),
)
