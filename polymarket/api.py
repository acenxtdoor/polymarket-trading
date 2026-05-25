from __future__ import annotations

import requests
from config import CLOB_BASE_URL, GAMMA_BASE_URL, DATA_BASE_URL, REQUEST_TIMEOUT
from utils.logger import get_logger

logger = get_logger(__name__)


def _get(url: str, params: dict = None) -> dict | list | None:
    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error(f"GET {url} failed: {e}")
        return None
    except ValueError as e:
        logger.error(f"GET {url} returned invalid JSON: {e}")
        return None


def get_market(slug: str) -> dict | None:
    """Fetch market metadata from Gamma API by slug."""
    data = _get(f"{GAMMA_BASE_URL}/markets", params={"slug": slug})
    if isinstance(data, list) and len(data) > 0:
        return data[0]
    if isinstance(data, dict):
        return data
    return None


def get_markets(limit: int = 100, offset: int = 0) -> list:
    """Fetch a page of active markets from Gamma API."""
    data = _get(f"{GAMMA_BASE_URL}/markets", params={"limit": limit, "offset": offset, "active": "true"})
    if isinstance(data, dict):
        return data.get("data", data.get("results", []))
    return data or []


def get_leaderboard(limit: int = 50) -> list:
    """Fetch top traders from the Data API leaderboard."""
    data = _get(f"{DATA_BASE_URL}/v1/leaderboard", params={"limit": limit})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data", data.get("results", []))
    return []


def get_trader_trades(address: str, limit: int = 50) -> list:
    """Fetch recent trades for a trader address from the Data API."""
    data = _get(f"{DATA_BASE_URL}/trades", params={"user": address, "limit": limit})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data", data.get("results", []))
    return []
