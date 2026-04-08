"""Read-only Kalshi portfolio fetchers.

Wraps the authenticated /portfolio/* endpoints. By design this module
imports nothing that can place orders — adding trading would require
a separate explicit module with its own safety scaffolding.

Configuration via env vars:

  KALSHI_KEY_ID            the public key ID from kalshi.com/account/profile
  KALSHI_PRIVATE_KEY_PATH  path to the .pem file (e.g. ~/.kalshi/private-key.pem)

If either is missing, fetch_portfolio() returns an empty PortfolioContext
with `enabled=False` and the rest of the agent silently degrades — the
ranker still produces a useful report, just without position context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import httpx

from .api import API_HOST, API_PATH_PREFIX, DEFAULT_UA
from .kalshi_auth import KalshiAuthError, KalshiCredentials, sign_request


@dataclass(frozen=True)
class Position:
    ticker: str
    side: str                  # "YES" or "NO" — canonical uppercase, matches Opportunity.side
    quantity: int              # contracts held
    cost_basis_cents: int      # what you paid
    market_value_cents: int    # current mark
    realized_pnl_cents: int

    @property
    def avg_cost(self) -> float:
        if self.quantity <= 0:
            return 0.0
        return self.cost_basis_cents / self.quantity / 100.0

    @property
    def market_value_dollars(self) -> float:
        return self.market_value_cents / 100.0

    @property
    def cost_basis_dollars(self) -> float:
        return self.cost_basis_cents / 100.0


@dataclass(frozen=True)
class Balance:
    settled_cents: int
    reserved_cents: int  # cash locked in resting orders

    @property
    def settled_dollars(self) -> float:
        return self.settled_cents / 100.0

    @property
    def total_dollars(self) -> float:
        return (self.settled_cents + self.reserved_cents) / 100.0


@dataclass
class PortfolioContext:
    """One snapshot of the user's Kalshi account. Held for the scan run."""
    balance: Balance | None = None
    positions: list[Position] = field(default_factory=list)
    fetch_error: str = ""
    enabled: bool = True

    @property
    def total_bankroll_dollars(self) -> float:
        """Cash + market value of all open positions."""
        cash = self.balance.total_dollars if self.balance else 0.0
        positions = sum(p.market_value_dollars for p in self.positions)
        return cash + positions

    def position_for(self, ticker: str) -> Position | None:
        for p in self.positions:
            if p.ticker == ticker:
                return p
        return None

    def concentration_pct(self, ticker: str) -> float:
        """What fraction of total bankroll is in this single ticker."""
        bankroll = self.total_bankroll_dollars
        if bankroll <= 0:
            return 0.0
        pos = self.position_for(ticker)
        if pos is None:
            return 0.0
        return pos.market_value_dollars / bankroll


def _safe_int(v) -> int:
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


def parse_balance(raw: dict) -> Balance:
    """Kalshi /portfolio/balance returns balance in cents."""
    return Balance(
        settled_cents=_safe_int(raw.get("balance")),
        reserved_cents=_safe_int(raw.get("payout")) or _safe_int(raw.get("reserved")),
    )


def parse_position(raw: dict) -> Position | None:
    """Kalshi /portfolio/positions entries.

    Schema (as of 2026):
      ticker, position (signed contracts), market_exposure (cents),
      realized_pnl (cents), total_traded (cents), resting_orders_count

    A positive `position` is a YES holding; negative is NO. Some Kalshi
    payloads instead use a separate `side` field — handle both.
    """
    ticker = raw.get("ticker") or raw.get("market_ticker")
    if not ticker:
        return None
    raw_position = raw.get("position")
    explicit_side = (raw.get("side") or "").lower().strip()

    if explicit_side in ("yes", "no") and raw_position is not None:
        side = explicit_side.upper()
        quantity = abs(_safe_int(raw_position))
    elif raw_position is not None:
        position = _safe_int(raw_position)
        if position == 0:
            return None
        side = "YES" if position > 0 else "NO"
        quantity = abs(position)
    else:
        return None

    return Position(
        ticker=ticker,
        side=side,
        quantity=quantity,
        cost_basis_cents=_safe_int(raw.get("total_traded") or raw.get("cost_basis")),
        market_value_cents=_safe_int(raw.get("market_exposure") or raw.get("market_value")),
        realized_pnl_cents=_safe_int(raw.get("realized_pnl")),
    )


def fetch_portfolio(
    *,
    timeout: float = 15.0,
    client: httpx.Client | None = None,
) -> PortfolioContext:
    """Pull current balance + open positions. Read-only."""
    try:
        creds = KalshiCredentials.from_env()
    except KalshiAuthError as e:
        return PortfolioContext(fetch_error=str(e), enabled=False)

    own_client = client is None
    if own_client:
        client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": DEFAULT_UA, "Accept": "application/json"},
        )
    ctx = PortfolioContext()
    try:
        # Balance
        path = f"{API_PATH_PREFIX}/portfolio/balance"
        headers = dict(sign_request(creds, method="GET", path=path))
        try:
            resp = client.get(f"{API_HOST}{path}", headers=headers)
            if resp.status_code == 200:
                payload = resp.json()
                raw_balance = payload.get("balance") if isinstance(payload, dict) else None
                if isinstance(raw_balance, dict):
                    ctx.balance = parse_balance(raw_balance)
                else:
                    ctx.balance = parse_balance(payload if isinstance(payload, dict) else {})
            else:
                ctx.fetch_error = f"balance HTTP {resp.status_code}: {resp.text[:200]}"
                return ctx
        except httpx.HTTPError as e:
            ctx.fetch_error = f"balance: {type(e).__name__}: {e}"
            return ctx

        # Positions, paginated. Kalshi requires the signed path to
        # exclude the query string, so we sign the base path and build
        # the request URL with a separate `params=` dict. Cap by
        # detecting a stuck/repeating cursor rather than a fixed
        # iteration count, so legitimate large accounts paginate fully
        # but a buggy server can't loop forever.
        positions_path = f"{API_PATH_PREFIX}/portfolio/positions"
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params: dict[str, str] = {"limit": "200"}
            if cursor:
                params["cursor"] = cursor
            headers = dict(sign_request(creds, method="GET", path=positions_path))
            try:
                resp = client.get(f"{API_HOST}{positions_path}", params=params, headers=headers)
            except httpx.HTTPError as e:
                ctx.fetch_error = f"positions: {type(e).__name__}: {e}"
                return ctx
            if resp.status_code != 200:
                ctx.fetch_error = f"positions HTTP {resp.status_code}: {resp.text[:200]}"
                return ctx
            payload = resp.json()
            raw_positions = (
                payload.get("market_positions")
                or payload.get("positions")
                or []
            )
            for raw in raw_positions:
                pos = parse_position(raw)
                if pos is not None and pos.quantity > 0:
                    ctx.positions.append(pos)
            next_cursor = payload.get("cursor") or None
            if not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
    finally:
        if own_client:
            client.close()

    return ctx
