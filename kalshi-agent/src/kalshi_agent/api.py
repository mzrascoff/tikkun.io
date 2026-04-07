"""Read-only client for the public Kalshi markets endpoint.

The elections.kalshi.com host is the unauthenticated, public API. We
intentionally avoid the trading endpoints — this agent does not place
orders.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterator

import httpx

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Fallback chain. Kalshi runs the regulated DCM under multiple hostnames;
# the elections subdomain returns metadata stubs for non-political markets,
# while trading-api / api carry the live order books.
BASE_URL_FALLBACKS = (
    "https://api.elections.kalshi.com/trade-api/v2",
    "https://trading-api.kalshi.com/trade-api/v2",
    "https://api.kalshi.com/trade-api/v2",
)
DEFAULT_UA = "kalshi-agent/0.1 (research; +https://github.com/mzrascoff/tikkun.io)"


@dataclass(frozen=True)
class Market:
    ticker: str
    title: str
    category: str
    yes_bid: float  # cents -> dollars (0..1)
    yes_ask: float
    volume: int
    open_interest: int
    close_time: str  # ISO8601
    status: str

    @classmethod
    def from_api(cls, raw: dict) -> "Market":
        return cls(
            ticker=raw["ticker"],
            title=raw.get("title", ""),
            category=raw.get("category", "") or "",
            yes_bid=(raw.get("yes_bid") or 0) / 100.0,
            yes_ask=(raw.get("yes_ask") or 0) / 100.0,
            volume=raw.get("volume") or 0,
            open_interest=raw.get("open_interest") or 0,
            close_time=raw.get("close_time", ""),
            status=raw.get("status", ""),
        )


class KalshiClient:
    """Minimal, polite, read-only Kalshi client."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        rate_limit_sec: float = 1.0,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.rate_limit_sec = rate_limit_sec
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": DEFAULT_UA, "Accept": "application/json"},
        )
        self._last_call: float = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.rate_limit_sec:
            time.sleep(self.rate_limit_sec - elapsed)
        self._last_call = time.monotonic()

    def iter_markets(
        self,
        status: str = "open",
        page_size: int = 200,
        max_pages: int | None = None,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
    ) -> Iterator[Market]:
        """Yield markets, paginating via the cursor returned by Kalshi.

        If `series_ticker` or `event_ticker` is given, the query is
        narrowed to that group. Otherwise the full firehose is returned.
        """
        cursor: str | None = None
        pages = 0
        while True:
            self._throttle()
            params: dict[str, str | int] = {"status": status, "limit": page_size}
            if cursor:
                params["cursor"] = cursor
            if series_ticker:
                params["series_ticker"] = series_ticker
            if event_ticker:
                params["event_ticker"] = event_ticker
            resp = self._client.get(f"{self.base_url}/markets", params=params)
            resp.raise_for_status()
            payload = resp.json()
            for raw in payload.get("markets", []):
                yield Market.from_api(raw)
            cursor = payload.get("cursor") or None
            pages += 1
            if not cursor or (max_pages is not None and pages >= max_pages):
                return

    def get_market(self, ticker: str) -> tuple[Market | None, str]:
        """Fetch a single market by ticker for live snapshot data.

        Tries each base URL in BASE_URL_FALLBACKS until one returns a
        market with non-zero bid OR ask. Returns (market_or_none, debug_str).
        """
        attempts: list[str] = []
        for base in BASE_URL_FALLBACKS:
            self._throttle()
            url = f"{base}/markets/{ticker}"
            try:
                resp = self._client.get(url)
                status = resp.status_code
            except httpx.HTTPError as e:
                attempts.append(f"{base}: ERR {type(e).__name__}")
                continue
            if status != 200:
                attempts.append(f"{base}: HTTP {status}")
                continue
            try:
                payload = resp.json()
            except Exception:
                attempts.append(f"{base}: bad JSON")
                continue
            raw = payload.get("market") or payload
            if not raw or not isinstance(raw, dict):
                attempts.append(f"{base}: empty payload")
                continue
            mk = Market.from_api(raw)
            attempts.append(
                f"{base}: bid={mk.yes_bid:.2f} ask={mk.yes_ask:.2f} vol={mk.volume}"
            )
            if mk.yes_bid > 0 or mk.yes_ask > 0:
                return mk, " | ".join(attempts)
            # Try next host if this one returned a stub.
        return None, " | ".join(attempts)

    def fetch_series(self, series_ticker: str) -> list[Market]:
        """Fetch all open markets in a series. Tries series_ticker first,
        then falls back to event_ticker (Kalshi accepts either depending
        on how the URL was constructed)."""
        # Try as series first
        try:
            markets = list(
                self.iter_markets(series_ticker=series_ticker, max_pages=5)
            )
            if markets:
                return markets
        except httpx.HTTPStatusError:
            pass
        # Fallback: try as event ticker (some URLs use event tickers)
        try:
            return list(
                self.iter_markets(event_ticker=series_ticker, max_pages=5)
            )
        except httpx.HTTPStatusError:
            return []

    def close(self) -> None:
        self._client.close()
