"""
Kalshi cross-reference module (Step 5).

Signal flow:
  query Kalshi API → fuzzy-match market title → compare implied probabilities
  → return (signal, multiplier)

Signals:
  "agree"    — Kalshi prob within KALSHI_AGREEMENT_THRESHOLD of Polymarket price → 1.5x
  "disagree" — Kalshi prob diverges beyond threshold                              → 0.0x (skip)
  "no_match" — no Kalshi market found for this title                              → 1.0x (neutral)
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

import requests

import config
from utils.cache import TTLCache
from utils.logger import get_logger

logger = get_logger(__name__)

KALSHI_MARKETS_URL = "https://trading-api.kalshi.com/trade-api/v2/markets"

_cache: TTLCache = TTLCache(ttl=config.KALSHI_CACHE_TTL)

# Signal constants
AGREE = "agree"
DISAGREE = "disagree"
NO_MATCH = "no_match"

_MULTIPLIERS = {
    AGREE: config.KALSHI_AGREE_MULTIPLIER,
    DISAGREE: config.KALSHI_DISAGREE_MULTIPLIER,
    NO_MATCH: config.KALSHI_NO_MATCH_MULTIPLIER,
}


@dataclass
class KalshiResult:
    signal: str          # AGREE | DISAGREE | NO_MATCH
    multiplier: float
    kalshi_title: str = ""
    kalshi_prob: float | None = None
    similarity: float = 0.0


def _fetch_kalshi_markets(query: str) -> list[dict]:
    """Fetch markets from Kalshi API, with TTL caching per query."""
    cache_key = f"kalshi:{query}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        resp = requests.get(
            KALSHI_MARKETS_URL,
            params={"search": query, "limit": 10, "status": "open"},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        markets = resp.json().get("markets", [])
    except Exception as exc:
        logger.warning(f"Kalshi API error for query '{query}': {exc}")
        return []

    _cache.set(cache_key, markets)
    return markets


def _implied_prob(market: dict) -> float | None:
    """Mid-price of yes_bid / yes_ask as implied probability (0–1 scale)."""
    bid = market.get("yes_bid")
    ask = market.get("yes_ask")
    if bid is None or ask is None:
        return None
    try:
        # Kalshi prices are in cents (0–100), normalise to 0–1
        mid = (float(bid) + float(ask)) / 2
        if mid > 1:
            mid /= 100
        return mid
    except (TypeError, ValueError):
        return None


def _best_match(title: str, markets: list[dict]) -> tuple[dict | None, float]:
    """Return (best_market, similarity_score) using SequenceMatcher on titles."""
    best_market: dict | None = None
    best_score = 0.0
    title_lower = title.lower()

    for market in markets:
        kalshi_title = str(market.get("title", "")).lower()
        score = difflib.SequenceMatcher(None, title_lower, kalshi_title).ratio()
        if score > best_score:
            best_score = score
            best_market = market

    return best_market, best_score


def get_kalshi_signal(market_title: str, polymarket_price: float) -> KalshiResult:
    """
    Cross-reference a Polymarket market against Kalshi.

    Args:
        market_title:     human-readable Polymarket market title
        polymarket_price: current YES price on Polymarket (0–1)

    Returns:
        KalshiResult with signal, multiplier, and match metadata.
    """
    # Use first 6 meaningful words as search query to avoid overly long queries
    words = [w for w in market_title.split() if len(w) > 2]
    query = " ".join(words[:6])

    markets = _fetch_kalshi_markets(query)

    if not markets:
        logger.info(f"[KALSHI] No markets returned for '{market_title}' → no_match")
        return KalshiResult(signal=NO_MATCH, multiplier=_MULTIPLIERS[NO_MATCH])

    best_market, similarity = _best_match(market_title, markets)

    if best_market is None or similarity < config.KALSHI_MATCH_THRESHOLD:
        logger.info(
            f"[KALSHI] Best match similarity {similarity:.2f} below threshold "
            f"{config.KALSHI_MATCH_THRESHOLD} for '{market_title}' → no_match"
        )
        return KalshiResult(signal=NO_MATCH, multiplier=_MULTIPLIERS[NO_MATCH], similarity=similarity)

    kalshi_prob = _implied_prob(best_market)
    kalshi_title = best_market.get("title", "")

    if kalshi_prob is None:
        logger.info(f"[KALSHI] Could not compute implied prob for '{kalshi_title}' → no_match")
        return KalshiResult(
            signal=NO_MATCH,
            multiplier=_MULTIPLIERS[NO_MATCH],
            kalshi_title=kalshi_title,
            similarity=similarity,
        )

    diff = round(abs(kalshi_prob - polymarket_price), 10)
    if diff <= config.KALSHI_AGREEMENT_THRESHOLD:
        signal = AGREE
        logger.info(
            f"[KALSHI] AGREE — '{kalshi_title}' prob={kalshi_prob:.3f} vs "
            f"polymarket={polymarket_price:.3f} diff={diff:.3f} (≤{config.KALSHI_AGREEMENT_THRESHOLD}) "
            f"similarity={similarity:.2f} → 1.5x"
        )
    else:
        signal = DISAGREE
        logger.info(
            f"[KALSHI] DISAGREE — '{kalshi_title}' prob={kalshi_prob:.3f} vs "
            f"polymarket={polymarket_price:.3f} diff={diff:.3f} (>{config.KALSHI_AGREEMENT_THRESHOLD}) "
            f"similarity={similarity:.2f} → skip"
        )

    return KalshiResult(
        signal=signal,
        multiplier=_MULTIPLIERS[signal],
        kalshi_title=kalshi_title,
        kalshi_prob=kalshi_prob,
        similarity=similarity,
    )
