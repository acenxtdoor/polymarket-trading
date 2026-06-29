"""
kalshi/api.py
=============
Kalshi Trading API v2 client.

Authentication: RSA signature per request.
  Header KALSHI-ACCESS-KEY:       API key ID
  Header KALSHI-ACCESS-TIMESTAMP: millisecond Unix timestamp (string)
  Header KALSHI-ACCESS-SIGNATURE: base64(RSA-SHA256(timestamp + METHOD + path))

Base URL:       https://trading-api.kalshi.com/trade-api/v2
Prices:         integer cents (1–99); convert with /100 and *100.
Contract size:  $0.01 per contract; count = int(size_dollars * 100 + 0.5)
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

import config
from utils.logger import get_logger

logger = get_logger(__name__)

_BASE = config.KALSHI_BASE_URL

# Load private key once at module level
def _load_private_key():
    key_path = Path(config.KALSHI_API_KEY_FILE)
    if not key_path.exists():
        logger.error(f"[KALSHI-API] Private key file not found: {key_path}")
        return None
    try:
        return serialization.load_pem_private_key(
            key_path.read_bytes(),
            password=None,
        )
    except Exception as exc:
        logger.error(f"[KALSHI-API] Failed to load private key: {exc}")
        return None

_private_key = _load_private_key()


def _sign(method: str, path: str) -> dict[str, str]:
    """Return auth headers for a Kalshi API request."""
    ts_ms = str(int(time.time() * 1000))
    msg = (ts_ms + method.upper() + path).encode("utf-8")
    if _private_key is None:
        return {}
    sig = _private_key.sign(msg, padding.PKCS1v15(), hashes.SHA256())
    return {
        "KALSHI-ACCESS-KEY":       config.KALSHI_API_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": ts_ms,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        "Content-Type":            "application/json",
    }


def _get(path: str, params: dict | None = None) -> dict | None:
    url = f"{_BASE}{path}"
    try:
        resp = requests.get(url, headers=_sign("GET", path), params=params, timeout=config.REQUEST_TIMEOUT)
        if resp.status_code == 401:
            logger.error("[KALSHI-API] 401 Unauthorized — check key ID and private key file")
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.error(f"[KALSHI-API] GET {path} failed: {exc}")
        return None
    except ValueError as exc:
        logger.error(f"[KALSHI-API] GET {path} invalid JSON: {exc}")
        return None


def _post(path: str, body: dict) -> dict | None:
    url = f"{_BASE}{path}"
    try:
        resp = requests.post(url, headers=_sign("POST", path), json=body, timeout=config.REQUEST_TIMEOUT)
        if resp.status_code == 401:
            logger.error("[KALSHI-API] 401 Unauthorized — check key ID and private key file")
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.error(f"[KALSHI-API] POST {path} failed: {exc}")
        return None
    except ValueError as exc:
        logger.error(f"[KALSHI-API] POST {path} invalid JSON: {exc}")
        return None


def get_balance() -> float:
    """Return available cash balance in USD. Returns 0.0 on error."""
    data = _get("/portfolio/balance")
    if not data:
        return 0.0
    try:
        return float(data["balance"]) / 100.0
    except (KeyError, TypeError, ValueError):
        return 0.0


def get_positions() -> list[dict]:
    """Return list of open market positions. Returns [] on error."""
    data = _get("/portfolio/positions")
    if not data:
        return []
    return data.get("market_positions", [])


def get_market(ticker: str) -> dict | None:
    """
    Fetch metadata for a single Kalshi market by ticker.

    Returns the inner 'market' dict with keys:
      ticker, title, yes_bid (cents), yes_ask (cents), volume (USD int),
      open_interest (USD int), close_time (ISO-8601 str), status (str).
    Returns None on 404 or error.
    """
    data = _get(f"/markets/{ticker}")
    if not data:
        return None
    return data.get("market")


def place_order(
    ticker: str,
    side: str,
    count: int,
    price_cents: int,
    action: str = "buy",
) -> dict | None:
    """
    Place a limit order on Kalshi.

    Args:
        ticker:      Kalshi market ticker (e.g. "KXBTC-25JUN-T60000")
        side:        "yes" or "no"
        count:       number of $0.01 contracts
        price_cents: limit price in cents (1–99)
        action:      "buy" or "sell"

    Returns the inner 'order' dict on success, None on error.
    """
    body = {
        "ticker": ticker,
        "action": action,
        "side": side,
        "type": "limit",
        "count": count,
        f"{side}_price": price_cents,
    }
    data = _post("/portfolio/orders", body)
    if not data:
        return None
    return data.get("order")
