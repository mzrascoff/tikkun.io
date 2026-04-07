"""Polymarket support via the public gamma API.

Polymarket runs on Polygon (crypto) and exposes a public REST API at
https://gamma-api.polymarket.com that requires no auth for read-only
market data. Binary YES/NO markets there have the same shape as Kalshi
contracts, so the existing priors and scoring code work unchanged
once we convert the gamma payload to our Market dataclass.

Two key differences vs Kalshi:

  1. Gamma returns `outcomePrices` as a JSON-encoded string inside the
     JSON ("[\"0.21\",\"0.79\"]") rather than a normal array. We parse
     that out and pretend it's a single mid-price (no separate bid/ask).
  2. Polymarket charges no taker fees, so we apply a smaller fee drag
     (0.005) than Kalshi's 0.02 to account for spread only.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Iterator

import httpx

from .api import Market

BASE_URL = "https://gamma-api.polymarket.com"
DEFAULT_UA = "kalshi-agent/0.1 (research; +https://github.com/mzrascoff/tikkun.io)"

# Polymarket has no taker fee — only the spread drags returns.
POLYMARKET_FEE_DRAG = 0.005


def _parse_price_list(raw) -> list[float]:
    """Polymarket encodes outcomePrices as a JSON string inside the JSON."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [float(x) for x in raw]
    if isinstance(raw, str):
        try:
            return [float(x) for x in json.loads(raw)]
        except (ValueError, TypeError, json.JSONDecodeError):
            return []
    return []


def _parse_outcomes(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        try:
            return [str(x) for x in json.loads(raw)]
        except (ValueError, TypeError, json.JSONDecodeError):
            return []
    return []


def market_from_gamma(raw: dict) -> Market | None:
    """Convert one gamma /markets entry to our Market dataclass.

    Returns None if the market is not a binary YES/NO contract or if
    prices are missing — multi-outcome markets aren't supported by the
    current scoring path.
    """
    outcomes = _parse_outcomes(raw.get("outcomes"))
    prices = _parse_price_list(raw.get("outcomePrices"))
    if len(outcomes) != 2 or len(prices) != 2:
        return None
    # Identify which outcome is YES — Polymarket usually orders ["Yes","No"]
    # but some markets are reversed. Be tolerant.
    yes_idx = 0
    for i, label in enumerate(outcomes):
        if label.strip().lower() in ("yes", "true", "y"):
            yes_idx = i
            break
    yes_price = prices[yes_idx]
    # Use the price as both bid and ask (gamma doesn't expose order book).
    yes_bid = yes_ask = max(0.0, min(1.0, yes_price))

    slug = raw.get("slug") or raw.get("conditionId") or ""
    if not slug:
        return None

    closed = bool(raw.get("closed"))
    active = bool(raw.get("active"))
    status = "active" if (active and not closed) else "closed"

    try:
        volume = int(float(raw.get("volume") or 0))
    except (TypeError, ValueError):
        volume = 0
    try:
        liquidity = int(float(raw.get("liquidity") or 0))
    except (TypeError, ValueError):
        liquidity = 0

    return Market(
        ticker=slug,
        title=raw.get("question") or raw.get("title") or "",
        category=raw.get("category") or "",
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        volume=volume,
        open_interest=liquidity,  # gamma doesn't expose OI; use liquidity as proxy
        close_time=raw.get("endDate") or raw.get("end_date_iso") or "",
        status=status,
    )


@dataclass(frozen=True)
class PolymarketKeywordSet:
    label: str
    why: str
    title_patterns: tuple[str, ...]   # regexes matched against the question
    news_keywords: tuple[str, ...] = ()


# Curated keyword groups. Polymarket doesn't have stable Kalshi-style
# series tickers; we instead pull a wide net of active markets and
# filter client-side by question text.
POLYMARKET_WATCHLIST: tuple[PolymarketKeywordSet, ...] = (
    PolymarketKeywordSet(
        label="Aliens / UAP disclosure",
        why="ET disclosure has 0 historical hits",
        title_patterns=(r"\balien", r"extraterrestrial", r"\bUFO\b", r"\bUAP\b"),
        news_keywords=(r"\bUAP\b", r"\bUFO\b", r"\balien", r"extraterrestrial",
                       r"NASA disclosure"),
    ),
    PolymarketKeywordSet(
        label="OpenAI / AGI",
        why="AGI declarations are contractual, not capability-based",
        title_patterns=(r"\bAGI\b", r"artificial general intelligence",
                        r"\bOpenAI\b.*announce", r"GPT-?5", r"GPT-?6"),
        news_keywords=(r"\bAGI\b", r"\bOpenAI\b", r"GPT-?5"),
    ),
    PolymarketKeywordSet(
        label="Crewed Mars",
        why="No life support, no orbital refuel demonstrated",
        title_patterns=(r"\bMars\b.*(land|crew|human)", r"SpaceX.*Mars"),
        news_keywords=(r"\bMars\b", r"Starship"),
    ),
    PolymarketKeywordSet(
        label="Room-temp superconductor",
        why="RT-ambient SC has never been replicated",
        title_patterns=(r"room.?temp", r"superconductor"),
        news_keywords=(r"superconductor", r"\bLK-?99\b"),
    ),
    PolymarketKeywordSet(
        label="Commercial fusion",
        why="Grid fusion not on credible roadmap before 2030",
        title_patterns=(r"\bfusion\b.*(commercial|grid|net)", r"nuclear fusion"),
        news_keywords=(r"\bfusion\b", r"\bITER\b"),
    ),
    PolymarketKeywordSet(
        label="Disease cure",
        why="No 'cure' announcement has ever resolved YES",
        title_patterns=(r"\bcure\b.*(cancer|alzheim|parkinson|HIV|ALS)",),
        news_keywords=(r"\bcure\b",),
    ),
    PolymarketKeywordSet(
        label="Iran nuclear weapon",
        why="Weaponization gating step is months away even from breakout",
        title_patterns=(r"\bIran\b.*(nuclear weapon|nuke|warhead|weaponiz)",),
        news_keywords=(r"\bIran\b.*nuclear",),
    ),
    PolymarketKeywordSet(
        label="Iran nuclear deal",
        why="Negotiated deals historically rare in <1y windows",
        title_patterns=(r"\bIran\b.*(nuclear deal|agreement|JCPOA)",),
        news_keywords=(r"\bIran\b.*deal", r"\bJCPOA\b"),
    ),
)


class PolymarketClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        rate_limit_sec: float = 0.4,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.rate_limit_sec = rate_limit_sec
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": DEFAULT_UA, "Accept": "application/json"},
            follow_redirects=True,
        )
        self._last_call: float = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.rate_limit_sec:
            time.sleep(self.rate_limit_sec - elapsed)
        self._last_call = time.monotonic()

    def fetch_active_markets(
        self,
        *,
        max_pages: int = 10,
        page_size: int = 100,
    ) -> Iterator[Market]:
        """Paginate through active, open binary markets on Polymarket."""
        offset = 0
        for _ in range(max_pages):
            self._throttle()
            params = {
                "active": "true",
                "closed": "false",
                "limit": page_size,
                "offset": offset,
            }
            try:
                resp = self._client.get(f"{self.base_url}/markets", params=params)
            except httpx.HTTPError:
                return
            if resp.status_code != 200:
                return
            try:
                payload = resp.json()
            except ValueError:
                return
            if not payload:
                return
            yielded = 0
            for raw in payload:
                m = market_from_gamma(raw)
                if m is not None:
                    yielded += 1
                    yield m
            if len(payload) < page_size:
                return
            offset += page_size

    def fetch_by_slug(self, slug: str) -> Market | None:
        self._throttle()
        try:
            resp = self._client.get(f"{self.base_url}/markets", params={"slug": slug})
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        try:
            payload = resp.json()
        except ValueError:
            return None
        if not payload:
            return None
        first = payload[0] if isinstance(payload, list) else payload
        return market_from_gamma(first)

    def close(self) -> None:
        self._client.close()


def matches_any(title: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return False
    for p in patterns:
        if re.search(p, title, re.IGNORECASE):
            return True
    return False


def filter_by_watchlist(
    markets: Iterator[Market] | list[Market],
) -> list[tuple[Market, PolymarketKeywordSet]]:
    """Match each Polymarket market against POLYMARKET_WATCHLIST.

    Returns one (market, matching_keyword_set) pair per match — a market
    matching multiple sets is yielded multiple times so the caller can
    decide which scoring path to use, but in practice the keyword sets
    are designed to be disjoint.
    """
    out: list[tuple[Market, PolymarketKeywordSet]] = []
    for m in markets:
        for kws in POLYMARKET_WATCHLIST:
            if matches_any(m.title, kws.title_patterns):
                out.append((m, kws))
                break
    return out


def trade_url(slug: str) -> str:
    return f"https://polymarket.com/market/{slug}"
