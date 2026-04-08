"""Tests for kalshi_auth (RSA signing) and portfolio (parsing + ranker
integration). No live network calls — we generate an ephemeral RSA key
in-process and round-trip the signature with the public key to verify.
"""
from __future__ import annotations

import base64
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_agent.kalshi_auth import (
    KalshiAuthError,
    KalshiCredentials,
    sign_request,
)
from kalshi_agent.portfolio import (
    Balance,
    PortfolioContext,
    Position,
    parse_balance,
    parse_position,
)


def _ephemeral_creds() -> KalshiCredentials:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return KalshiCredentials(key_id="test-key-id", private_key=key)


def test_sign_request_round_trips_with_public_key():
    creds = _ephemeral_creds()
    headers = sign_request(
        creds, method="GET", path="/trade-api/v2/portfolio/balance", now_ms=1700000000000
    )
    assert headers["KALSHI-ACCESS-KEY"] == "test-key-id"
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == "1700000000000"

    sig = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    expected_message = b"1700000000000GET/trade-api/v2/portfolio/balance"
    public_key = creds.private_key.public_key()
    # Should not raise.
    public_key.verify(
        sig,
        expected_message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )


def test_sign_request_uppercases_method():
    """Both 'get' and 'GET' should sign the same canonical message
    '1GET/x'. Signatures bytes will differ (PSS salt is random) but both
    must verify against the same expected message."""
    creds = _ephemeral_creds()
    headers_lower = sign_request(creds, method="get", path="/x", now_ms=1)
    headers_upper = sign_request(creds, method="GET", path="/x", now_ms=1)
    expected = b"1GET/x"
    public_key = creds.private_key.public_key()
    for h in (headers_lower, headers_upper):
        sig = base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"])
        public_key.verify(
            sig,
            expected,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )


def test_sign_request_different_paths_produce_different_signatures():
    creds = _ephemeral_creds()
    h1 = sign_request(creds, method="GET", path="/a", now_ms=1)
    h2 = sign_request(creds, method="GET", path="/b", now_ms=1)
    assert h1["KALSHI-ACCESS-SIGNATURE"] != h2["KALSHI-ACCESS-SIGNATURE"]


def test_credentials_from_env_missing_keys(monkeypatch):
    monkeypatch.delenv("KALSHI_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    with pytest.raises(KalshiAuthError, match="KALSHI_KEY_ID"):
        KalshiCredentials.from_env()


def test_credentials_from_env_loads_pem(monkeypatch, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pem_path = tmp_path / "key.pem"
    pem_path.write_bytes(pem)

    monkeypatch.setenv("KALSHI_KEY_ID", "key-abc")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(pem_path))
    creds = KalshiCredentials.from_env()
    assert creds.key_id == "key-abc"
    assert isinstance(creds.private_key, rsa.RSAPrivateKey)


def test_parse_balance_handles_cents():
    raw = {"balance": 15400, "payout": 0}
    b = parse_balance(raw)
    assert b.settled_cents == 15400
    assert b.settled_dollars == 154.0
    assert b.total_dollars == 154.0


def test_parse_position_signed_position_field():
    raw = {
        "ticker": "KXALIENS-27",
        "position": -100,  # negative -> NO side
        "market_exposure": 7900,
        "total_traded": 11354,
        "realized_pnl": 0,
    }
    p = parse_position(raw)
    assert p is not None
    assert p.ticker == "KXALIENS-27"
    assert p.side == "NO"
    assert p.quantity == 100
    assert p.cost_basis_dollars == 113.54
    assert p.market_value_dollars == 79.0
    assert abs(p.avg_cost - 1.1354) < 1e-6


def test_parse_position_2026_schema_dollars_strings():
    """Kalshi's 2026 /portfolio/positions schema uses position_fp (string
    float, negative=NO) and *_dollars fields (string floats in dollars)."""
    raw = {
        "ticker": "KXALIENS-27",
        "position_fp": "-1396.00",
        "market_exposure_dollars": "1097.077000",
        "total_traded_dollars": "1097.077000",
        "realized_pnl_dollars": "0.000000",
        "fees_paid_dollars": "16.473000",
        "resting_orders_count": 0,
        "last_updated_ts": "2026-04-08T06:04:43.686201Z",
    }
    p = parse_position(raw)
    assert p is not None
    assert p.ticker == "KXALIENS-27"
    assert p.side == "NO"
    assert p.quantity == 1396
    assert p.cost_basis_cents == 109708  # 1097.077 * 100, rounded
    assert p.market_value_cents == 109708
    assert p.realized_pnl_cents == 0
    assert abs(p.cost_basis_dollars - 1097.08) < 0.01
    assert abs(p.market_value_dollars - 1097.08) < 0.01


def test_parse_position_2026_yes_side():
    raw = {
        "ticker": "KXOAIAGI-27",
        "position_fp": "50.00",
        "market_exposure_dollars": "32.500000",
        "total_traded_dollars": "32.500000",
        "realized_pnl_dollars": "0.000000",
    }
    p = parse_position(raw)
    assert p is not None
    assert p.side == "YES"
    assert p.quantity == 50


def test_parse_position_explicit_side_field():
    raw = {
        "ticker": "KXOAIAGI-26",
        "side": "yes",
        "position": 50,
        "market_exposure": 4500,
        "total_traded": 4500,
    }
    p = parse_position(raw)
    assert p is not None
    assert p.side == "YES"
    assert p.quantity == 50


def test_parse_position_zero_position_returns_none():
    raw = {"ticker": "KXFOO", "position": 0, "market_exposure": 0}
    assert parse_position(raw) is None


def test_portfolio_context_concentration_pct():
    ctx = PortfolioContext(
        balance=Balance(settled_cents=4100, reserved_cents=0),
        positions=[
            Position(
                ticker="KXALIENS-27", side="NO", quantity=100,
                cost_basis_cents=11354, market_value_cents=11400,
                realized_pnl_cents=0,
            ),
            Position(
                ticker="KXOAIAGI-27", side="NO", quantity=10,
                cost_basis_cents=940, market_value_cents=940,
                realized_pnl_cents=0,
            ),
        ],
    )
    assert abs(ctx.total_bankroll_dollars - (41.0 + 114.0 + 9.40)) < 1e-9
    aliens_pct = ctx.concentration_pct("KXALIENS-27")
    assert aliens_pct > 0.65  # ~70% of bankroll
    assert aliens_pct < 0.75
    assert ctx.concentration_pct("NOT-HELD") == 0.0


def test_concentration_warning_in_rank_reason():
    """A position consuming 70%+ of bankroll should trip the DO NOT ADD warning."""
    from datetime import datetime, timedelta, timezone
    from kalshi_agent.api import Market
    from kalshi_agent.priors import estimate_prior
    from kalshi_agent.scoring import evaluate, rank

    now = datetime.now(tz=timezone.utc)
    m = Market(
        ticker="KXALIENS-27",
        title="Will the U.S. confirm that aliens exist before 2027?",
        category="",
        yes_bid=0.20, yes_ask=0.21,
        volume=1000, open_interest=1000,
        close_time=(now + timedelta(days=200)).isoformat(),
        status="active",
    )
    op = evaluate(m, estimate_prior(m, now=now), now=now)
    pos = Position(
        ticker="KXALIENS-27", side="NO", quantity=100,
        cost_basis_cents=11354, market_value_cents=11400,
        realized_pnl_cents=0,
    )
    op = replace(op, series_ticker="KXALIENS", current_position=pos, concentration_pct=0.74)
    ranked = rank([op])
    assert "DO NOT ADD" in ranked[0].rank_reason
