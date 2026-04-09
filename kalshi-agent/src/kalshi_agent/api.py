"""Read-only client for the public Kalshi markets endpoint.

The elections.kalshi.com host is the unauthenticated, public API. We
intentionally avoid the trading endpoints — this agent does not place
orders.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

import httpx


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _days_until(iso: str) -> float:
    dt = _parse_iso(iso)
    if dt is None:
        return 0.0
    return max(0.0, (dt - datetime.now(tz=timezone.utc)).total_seconds() / 86400.0)

# Despite the name, api.elections.kalshi.com is the canonical host for
# all Kalshi markets. trading-api.kalshi.com returns a migration notice
# and api.kalshi.com does not resolve.
API_HOST = "https://api.elections.kalshi.com"
API_PATH_PREFIX = "/trade-api/v2"
BASE_URL = f"{API_HOST}{API_PATH_PREFIX}"
BASE_URL_FALLBACKS = (BASE_URL,)
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
    event_ticker: str = ""  # parent event, e.g. KXALIENS-27 for an alien market
    created_time: str = ""  # ISO8601 when Kalshi first listed this market

    @classmethod
    def from_api(cls, raw: dict) -> "Market":
        """Parse a market from the Kalshi v2 API.

        Handles two schemas:
          * Legacy: yes_bid/yes_ask as ints in cents
          * Current (2026): yes_bid_dollars/no_bid_dollars as strings in
            dollars. yes_*/no_* are tied by yes_bid + no_ask = 1.0.
        """
        def _f(key: str) -> float:
            v = raw.get(key)
            if v is None or v == "":
                return 0.0
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        # Prefer the new *_dollars fields.
        yes_bid = _f("yes_bid_dollars")
        yes_ask = _f("yes_ask_dollars")
        no_bid = _f("no_bid_dollars")
        no_ask = _f("no_ask_dollars")

        # Derive missing yes-side from no-side: yes_bid + no_ask = 1.0,
        # yes_ask + no_bid = 1.0.
        if yes_bid == 0.0 and no_ask > 0.0:
            yes_bid = round(1.0 - no_ask, 4)
        if yes_ask == 0.0 and no_bid > 0.0:
            yes_ask = round(1.0 - no_bid, 4)

        # Legacy cents-int fallback.
        if yes_bid == 0.0 and yes_ask == 0.0:
            yes_bid = (raw.get("yes_bid") or 0) / 100.0
            yes_ask = (raw.get("yes_ask") or 0) / 100.0

        return cls(
            ticker=raw["ticker"],
            title=raw.get("title", ""),
            category=raw.get("category", "") or "",
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            volume=raw.get("volume") or raw.get("volume_24h") or 0,
            open_interest=raw.get("open_interest") or 0,
            close_time=raw.get("close_time", ""),
            status=raw.get("status", ""),
            event_ticker=raw.get("event_ticker") or "",
            created_time=raw.get("created_time") or "",
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

    def fetch_recent_markets(
        self,
        *,
        lookback_days: int = 14,
        max_pages: int = 5,
        page_size: int = 200,
        min_days_to_close: float = 14.0,
        exclude_event_tickers: set[str] | None = None,
    ) -> list[Market]:
        """Pull open markets from the firehose and keep only ones Kalshi
        listed in the last `lookback_days` that aren't already covered by
        the curated watchlist. Returns a list sorted most-recent first.

        `exclude_event_tickers` should be the set of series/event tickers
        the agent already knows about — any market whose ticker or
        event_ticker starts with one of those is dropped to avoid
        duplicating what the watchlist sections already show.
        """
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
        exclude = {t.upper() for t in (exclude_event_tickers or set())}
        out: list[Market] = []
        for m in self.iter_markets(
            status="open", page_size=page_size, max_pages=max_pages
        ):
            created = _parse_iso(m.created_time)
            if created is None or created < cutoff:
                continue
            if _days_until(m.close_time) < min_days_to_close:
                continue
            upper_ticker = (m.ticker or "").upper()
            upper_event = (m.event_ticker or "").upper()
            if any(
                upper_ticker.startswith(ex) or upper_event.startswith(ex)
                for ex in exclude
            ):
                continue
            out.append(m)
        out.sort(key=lambda m: m.created_time, reverse=True)
        return out

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
