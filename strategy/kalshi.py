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

KALSHI_MARKETS_URL = f"{config.KALSHI_BASE_URL}/markets"

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
    kalshi_ticker: str = ""   # Kalshi market ticker for execution (e.g. "KXBTC-25JUN-T60000")
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
    """Mid-price of yes_bid / yes_ask as implied probability (0–1 scale).

    Kalshi prices can be in cent scale (0–100) or decimal scale (0–1).
    We detect cent scale by checking whether either raw value exceeds 1,
    rather than the mid — this correctly handles near-zero probabilities
    like yes_bid=0, yes_ask=2 where the mid (1.0) would otherwise pass
    the decimal-scale check incorrectly.
    """
    bid = market.get("yes_bid")
    ask = market.get("yes_ask")
    if bid is None or ask is None:
        return None
    try:
        bid_f = float(bid)
        ask_f = float(ask)
        # If either raw value exceeds 1, the prices are in cent scale
        if bid_f > 1 or ask_f > 1:
            bid_f /= 100
            ask_f /= 100
        mid = (bid_f + ask_f) / 2
        return max(0.0, min(1.0, mid))  # clamp to valid probability range
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
    if not config.KALSHI_ENABLED:
        return KalshiResult(signal=NO_MATCH, multiplier=_MULTIPLIERS[NO_MATCH])

    if not (0.0 < polymarket_price < 1.0):
        logger.warning(f"[KALSHI] Invalid polymarket_price={polymarket_price} — must be (0, 1)")
        return KalshiResult(signal=NO_MATCH, multiplier=_MULTIPLIERS[NO_MATCH])

    # Use first 6 meaningful words as search query; keep 2-char words (US, UK, AI, EU)
    words = [w for w in market_title.split() if len(w) > 1]
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
    kalshi_ticker = best_market.get("ticker", "")

    if kalshi_prob is None:
        logger.info(f"[KALSHI] Could not compute implied prob for '{kalshi_title}' → no_match")
        return KalshiResult(
            signal=NO_MATCH,
            multiplier=_MULTIPLIERS[NO_MATCH],
            kalshi_title=kalshi_title,
            kalshi_ticker=kalshi_ticker,
            similarity=similarity,
        )

    diff = round(abs(kalshi_prob - polymarket_price), 10)
    if diff <= config.KALSHI_AGREEMENT_THRESHOLD:
        signal = AGREE
        logger.info(
            f"[KALSHI] AGREE — '{kalshi_title}' ({kalshi_ticker}) prob={kalshi_prob:.3f} vs "
            f"polymarket={polymarket_price:.3f} diff={diff:.3f} (≤{config.KALSHI_AGREEMENT_THRESHOLD}) "
            f"similarity={similarity:.2f} → 1.5x"
        )
    else:
        signal = DISAGREE
        logger.info(
            f"[KALSHI] DISAGREE — '{kalshi_title}' ({kalshi_ticker}) prob={kalshi_prob:.3f} vs "
            f"polymarket={polymarket_price:.3f} diff={diff:.3f} (>{config.KALSHI_AGREEMENT_THRESHOLD}) "
            f"similarity={similarity:.2f} → skip"
        )

    return KalshiResult(
        signal=signal,
        multiplier=_MULTIPLIERS[signal],
        kalshi_title=kalshi_title,
        kalshi_ticker=kalshi_ticker,
        kalshi_prob=kalshi_prob,
        similarity=similarity,
    )
