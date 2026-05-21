"""
strategy/slippage.py
====================
Linear slippage model for Polymarket order execution.

Prevents executing trades where our own order size would move the price
beyond an acceptable threshold. Works with liquidity data from MarketFilter
and the MAX_SLIPPAGE_PCT constant from config.

Formula:
    estimated_fill = trader_price + (buy_amount / market_liquidity) * 0.5
    slippage_pct   = (estimated_fill - trader_price) / trader_price

Safe to execute if slippage_pct <= MAX_SLIPPAGE_PCT (3%).
"""

import logging
from dataclasses import dataclass

from config import MAX_SLIPPAGE_PCT
from strategy.market_filter import MarketFilter

logger = logging.getLogger(__name__)


# ── Result object ──────────────────────────────────────────────────────────────

@dataclass
class SlippageResult:
    """
    Returned by estimate_slippage() for every check.

    Attributes:
        is_safe        — True if slippage_pct <= MAX_SLIPPAGE_PCT, False if trade
                         should be skipped.
        estimated_fill — The price we expect to actually pay after market impact.
        slippage_pct   — Estimated price impact as a decimal fraction
                         (e.g. 0.025 = 2.5%).
    """
    is_safe:        bool
    estimated_fill: float
    slippage_pct:   float


# ── Core model ─────────────────────────────────────────────────────────────────

def estimate_slippage(
    market_id:    str,
    trader_price: float,
    buy_amount:   float,
    market_filter: MarketFilter,
) -> SlippageResult:
    """
    Estimates the price impact of placing a buy order of buy_amount dollars
    into a market with the given liquidity, and determines whether the trade
    is safe to execute.

    Args:
        market_id     : Polymarket market identifier — used to pull liquidity
                        from the MarketFilter cache.
        trader_price  : Current market price we intend to buy at (0.0 – 1.0
                        for a prediction market probability).
        buy_amount    : Dollar amount of the order we intend to place.
        market_filter : Initialised MarketFilter instance whose cache holds
                        liquidityNum for this market.

    Returns:
        SlippageResult with is_safe, estimated_fill, and slippage_pct.

    Raises:
        ValueError: If trader_price is zero (division by zero in slippage_pct).
        ValueError: If market liquidity is missing or zero for this market_id.
    """
    if trader_price <= 0:
        raise ValueError(
            f"trader_price must be > 0, got {trader_price}. "
            "Cannot compute slippage against a zero price."
        )

    # Pull liquidity from MarketFilter cache
    market_liquidity = _get_liquidity(market_id, market_filter)

    if market_liquidity <= 0:
        raise ValueError(
            f"Market liquidity for {market_id} is {market_liquidity}. "
            "Cannot model slippage against zero or negative liquidity."
        )

    # ── Linear slippage model ──
    estimated_fill = trader_price + (buy_amount / market_liquidity) * 0.5
    slippage_pct   = (estimated_fill - trader_price) / trader_price
    # Use a tiny epsilon to handle floating point precision in boundary comparisons
    is_safe        = slippage_pct <= (MAX_SLIPPAGE_PCT + 1e-12)

    # ── Logging ──
    if is_safe:
        logger.info(
            "[SLIPPAGE] OK: market=%s  price=%.4f  amount=$%.2f  "
            "liquidity=$%.2f  fill=%.4f  slippage=%.2f%%",
            market_id,
            trader_price,
            buy_amount,
            market_liquidity,
            estimated_fill,
            slippage_pct * 100,
        )
    else:
        logger.warning(
            "[SLIPPAGE] SKIP: estimated slippage %.1f%% exceeds %.0f%% maximum.  "
            "market=%s  price=%.4f  amount=$%.2f  liquidity=$%.2f  fill=%.4f",
            slippage_pct * 100,
            MAX_SLIPPAGE_PCT * 100,
            market_id,
            trader_price,
            buy_amount,
            market_liquidity,
            estimated_fill,
        )

    return SlippageResult(
        is_safe        = is_safe,
        estimated_fill = estimated_fill,
        slippage_pct   = slippage_pct,
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

def _get_liquidity(market_id: str, market_filter: MarketFilter) -> float:
    """
    Extracts liquidityNum from the MarketFilter cache for the given market.

    Expects market_filter to expose either:
        market_filter.get_market_metadata(market_id)  → dict with "liquidityNum" key
        market_filter.get_market(market_id)           → dict with "liquidityNum" key
        market_filter._cache                          → TTLCache
        market_filter.cache                           → dict[market_id, metadata_dict]

    Raises ValueError if the market is not found in the cache.
    """
    metadata = None

    # Try get_market_metadata (real MarketFilter public method)
    if hasattr(market_filter, "get_market_metadata"):
        try:
            val = market_filter.get_market_metadata(market_id)
            if isinstance(val, dict):
                metadata = val
        except Exception:
            pass

    # Try get_market (mock/legacy method)
    if metadata is None and hasattr(market_filter, "get_market"):
        try:
            val = market_filter.get_market(market_id)
            if isinstance(val, dict):
                metadata = val
        except Exception:
            pass

    # Try _cache (real MarketFilter private cache attribute)
    if metadata is None and hasattr(market_filter, "_cache"):
        try:
            val = market_filter._cache.get(market_id)
            if isinstance(val, dict):
                metadata = val
        except Exception:
            pass

    # Try cache (mock/legacy dict attribute)
    if metadata is None and hasattr(market_filter, "cache"):
        try:
            val = market_filter.cache.get(market_id)
            if isinstance(val, dict):
                metadata = val
        except Exception:
            pass

    if metadata is None:
        raise ValueError(
            f"Market {market_id!r} not found in MarketFilter cache. "
            "Ensure the market has been fetched and cached before calling estimate_slippage()."
        )

    liquidity = metadata.get("liquidityNum")

    if liquidity is None:
        raise ValueError(
            f"liquidityNum missing from cached metadata for market {market_id!r}. "
            "Check that MarketFilter is populating this field."
        )

    return float(liquidity)
