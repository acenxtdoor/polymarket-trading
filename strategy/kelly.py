from __future__ import annotations

from config import MAX_POSITION_PCT
from utils.logger import get_logger

logger = get_logger(__name__)


def kelly_fraction(p: float, price: float) -> float:
    """
    Calculate the base Kelly fraction for a prediction market YES bet.

    Args:
        p:     estimated true probability of YES (from copytraded signal)
        price: current market YES price (implied probability)

    Returns:
        Raw Kelly fraction, floored at 0.0 (negative = no edge).
    """
    if not (0.0 < p < 1.0) or not (0.0 < price < 1.0):
        logger.warning(f"Invalid inputs to kelly_fraction: p={p}, price={price}")
        return 0.0

    q = 1.0 - p
    b = (1.0 - price) / price  # net odds: profit per $1 wagered if YES resolves

    f = p - (q / b)
    return max(f, 0.0)  # never size a negative-edge bet


def position_size(
    bankroll: float,
    p: float,
    price: float,
    conviction_multiplier: float = 1.0,
    kalshi_multiplier: float = 1.0,
) -> float:
    """
    Return the dollar amount to bet after applying multipliers and the 10% bankroll cap.

    Args:
        bankroll:             total available capital in dollars
        p:                    estimated true probability of YES
        price:                current market YES price
        conviction_multiplier: 1.0 / 1.25 / 1.5 based on how many traders agree
        kalshi_multiplier:    1.0 or 1.5 based on Kalshi cross-reference result

    Returns:
        Dollar position size, or 0.0 if there is no edge.
    """
    if bankroll <= 0:
        logger.warning(f"Invalid bankroll: {bankroll}")
        return 0.0

    f = kelly_fraction(p, price)
    if f <= 0.0:
        logger.info(
            f"Kelly fraction={f:.4f} (no edge): p={p:.3f} price={price:.3f} — skipping"
        )
        return 0.0

    adjusted = f * conviction_multiplier * kalshi_multiplier
    capped = min(adjusted, MAX_POSITION_PCT)
    size = bankroll * capped

    logger.info(
        f"Kelly sizing: p={p:.3f} price={price:.3f} f*={f:.4f} "
        f"conviction={conviction_multiplier}x kalshi={kalshi_multiplier}x "
        f"-> capped at {capped:.2%} of bankroll = ${size:.2f}"
    )
    return size
