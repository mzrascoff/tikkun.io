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
    ) -> Iterator[Market]:
        """Yield markets, paginating via the cursor returned by Kalshi."""
        cursor: str | None = None
        pages = 0
        while True:
            self._throttle()
            params: dict[str, str | int] = {"status": status, "limit": page_size}
            if cursor:
                params["cursor"] = cursor
            resp = self._client.get(f"{self.base_url}/markets", params=params)
            resp.raise_for_status()
            payload = resp.json()
            for raw in payload.get("markets", []):
                yield Market.from_api(raw)
            cursor = payload.get("cursor") or None
            pages += 1
            if not cursor or (max_pages is not None and pages >= max_pages):
                return

    def close(self) -> None:
        self._client.close()
