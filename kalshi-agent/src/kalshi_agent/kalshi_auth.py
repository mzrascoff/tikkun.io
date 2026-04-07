"""Kalshi API request signing.

Kalshi authenticates trading-API requests with three headers:

  KALSHI-ACCESS-KEY        the public key ID
  KALSHI-ACCESS-TIMESTAMP  current Unix time in milliseconds
  KALSHI-ACCESS-SIGNATURE  base64(RSA-PSS-SHA256(timestamp + method + path))

The user generates the (Key ID, RSA private key) pair on
https://kalshi.com/account/profile and saves the private key as a
.pem file on their local machine. The agent reads the file path from
the KALSHI_PRIVATE_KEY_PATH env var and the key ID from KALSHI_KEY_ID.

The private key never leaves the local filesystem and is never logged.
"""
from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class KalshiAuthError(RuntimeError):
    """Raised when KALSHI_KEY_ID / KALSHI_PRIVATE_KEY_PATH are missing
    or the private key file is unreadable / not a valid RSA key."""


@dataclass(frozen=True)
class KalshiCredentials:
    key_id: str
    # repr=False so the key never appears in stray f-string formats or
    # exception tracebacks that include the dataclass repr.
    private_key: rsa.RSAPrivateKey = field(repr=False)

    @classmethod
    def from_env(cls) -> "KalshiCredentials":
        key_id = os.environ.get("KALSHI_KEY_ID")
        if not key_id:
            raise KalshiAuthError("missing env var: KALSHI_KEY_ID")
        path_str = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
        if not path_str:
            raise KalshiAuthError("missing env var: KALSHI_PRIVATE_KEY_PATH")
        path = Path(path_str).expanduser()
        if not path.exists():
            raise KalshiAuthError(f"private key file not found: {path}")
        try:
            with path.open("rb") as f:
                key = serialization.load_pem_private_key(f.read(), password=None)
        except Exception as e:
            raise KalshiAuthError(f"failed to load private key: {e}") from e
        if not isinstance(key, rsa.RSAPrivateKey):
            raise KalshiAuthError("private key is not an RSA key")
        return cls(key_id=key_id, private_key=key)


def sign_request(
    creds: KalshiCredentials,
    *,
    method: str,
    path: str,
    now_ms: int | None = None,
) -> Mapping[str, str]:
    """Build the auth headers for a request.

    `path` is the URL path including any leading `/trade-api/v2/...`
    portion but WITHOUT the host. Kalshi signs `{ts}{METHOD}{path}`.
    """
    timestamp = str(now_ms if now_ms is not None else int(time.time() * 1000))
    message = (timestamp + method.upper() + path).encode("utf-8")
    signature = creds.private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": creds.key_id,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
    }
