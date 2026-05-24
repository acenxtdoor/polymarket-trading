"""
strategy/trailing_stop.py
=========================
Trailing stop manager (Step 7).

Tracks the peak YES price for every open position. Fires a CLOSE signal
when the current price falls more than TRAILING_STOP_PCT (10%) below that peak.

Stop condition:
    current_price <= peak_price * (1 - TRAILING_STOP_PCT)

Trail level (the exact price that triggers the stop):
    trail_price = peak_price * (1 - TRAILING_STOP_PCT)

Usage:
    manager = TrailingStopManager()
    manager.open_position("market-abc", entry_price=0.65)

    # on each price tick:
    result = manager.update("market-abc", current_price=0.70)
    if result.should_close:
        # execute exit order
        manager.close_position("market-abc")

    # or batch-check all open positions at once:
    results = manager.check_all({"market-abc": 0.55, "market-xyz": 0.80})
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from config import TRAILING_STOP_PCT
from utils.logger import get_logger

logger = get_logger(__name__)


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class Position:
    """Internal record for a single tracked position."""
    market_id:   str
    entry_price: float
    peak_price:  float
    opened_at:   float = field(default_factory=time.time)

    @property
    def trail_price(self) -> float:
        """The price level at which the trailing stop triggers."""
        return self.peak_price * (1.0 - TRAILING_STOP_PCT)


@dataclass
class StopResult:
    """
    Returned by TrailingStopManager.update() for every price tick.

    Attributes:
        market_id    — market this result belongs to
        should_close — True if the trailing stop has been hit
        current_price — price fed into this update
        peak_price   — highest price seen since position was opened
        trail_price  — stop level = peak * (1 - TRAILING_STOP_PCT)
        drawdown_pct — how far current_price has fallen from peak (0–1)
    """
    market_id:     str
    should_close:  bool
    current_price: float
    peak_price:    float
    trail_price:   float
    drawdown_pct:  float


# ── Manager ────────────────────────────────────────────────────────────────────

class TrailingStopManager:
    """
    In-memory trailing stop tracker for all open positions.

    One position per market_id. Not thread-safe (add a lock if the main
    event loop calls update() from multiple threads).
    """

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}

    # ── Public API ──────────────────────────────────────────────────────────

    def open_position(self, market_id: str, entry_price: float) -> Position:
        """
        Register a new open position.

        Args:
            market_id:   unique market identifier
            entry_price: the price at which the position was entered (0–1)

        Returns:
            The newly created Position.

        Raises:
            ValueError: if a position for this market_id is already open.
            ValueError: if entry_price is not in (0, 1).
        """
        if not (0.0 < entry_price < 1.0):
            raise ValueError(
                f"entry_price must be in (0, 1), got {entry_price}"
            )
        if market_id in self._positions:
            raise ValueError(
                f"Position for '{market_id}' is already open. "
                "Call close_position() before re-opening."
            )

        pos = Position(
            market_id=market_id,
            entry_price=entry_price,
            peak_price=entry_price,
        )
        self._positions[market_id] = pos
        logger.info(
            f"[TRAILING] OPEN  {market_id}  entry={entry_price:.4f}  "
            f"trail={pos.trail_price:.4f}"
        )
        return pos

    def update(self, market_id: str, current_price: float) -> StopResult:
        """
        Feed a new price tick for an open position.

        Updates the peak if current_price is higher, then checks the stop.

        Args:
            market_id:     the market to update
            current_price: latest YES price (0–1)

        Returns:
            StopResult — check .should_close to decide whether to exit.

        Raises:
            KeyError:   if market_id has no open position.
            ValueError: if current_price is negative.
        """
        if market_id not in self._positions:
            raise KeyError(
                f"No open position for '{market_id}'. "
                "Call open_position() first."
            )
        if current_price < 0:
            raise ValueError(
                f"current_price must be >= 0, got {current_price}"
            )

        pos = self._positions[market_id]

        # Update peak
        if current_price > pos.peak_price:
            old_peak = pos.peak_price
            pos.peak_price = current_price
            logger.info(
                f"[TRAILING] PEAK  {market_id}  "
                f"peak {old_peak:.4f} → {current_price:.4f}  "
                f"new trail={pos.trail_price:.4f}"
            )

        drawdown_pct = (pos.peak_price - current_price) / pos.peak_price
        should_close = current_price <= pos.trail_price

        if should_close:
            logger.warning(
                f"[TRAILING] STOP  {market_id}  "
                f"current={current_price:.4f}  peak={pos.peak_price:.4f}  "
                f"trail={pos.trail_price:.4f}  drawdown={drawdown_pct:.1%}"
            )
        else:
            logger.info(
                f"[TRAILING] OK    {market_id}  "
                f"current={current_price:.4f}  peak={pos.peak_price:.4f}  "
                f"trail={pos.trail_price:.4f}  drawdown={drawdown_pct:.1%}"
            )

        return StopResult(
            market_id=market_id,
            should_close=should_close,
            current_price=current_price,
            peak_price=pos.peak_price,
            trail_price=pos.trail_price,
            drawdown_pct=drawdown_pct,
        )

    def close_position(self, market_id: str) -> Position:
        """
        Remove a position from the tracker.

        Args:
            market_id: the market to close

        Returns:
            The closed Position record.

        Raises:
            KeyError: if market_id has no open position.
        """
        if market_id not in self._positions:
            raise KeyError(f"No open position for '{market_id}'.")

        pos = self._positions.pop(market_id)
        logger.info(
            f"[TRAILING] CLOSE {market_id}  "
            f"entry={pos.entry_price:.4f}  peak={pos.peak_price:.4f}"
        )
        return pos

    def check_all(self, prices: dict[str, float]) -> list[StopResult]:
        """
        Batch-update every market_id present in prices.

        Markets in self._positions that are not in prices are skipped.

        Args:
            prices: dict mapping market_id → current YES price

        Returns:
            List of StopResult for each updated position.
        """
        results: list[StopResult] = []
        for market_id, price in prices.items():
            if market_id in self._positions:
                results.append(self.update(market_id, price))
        return results

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def open_positions(self) -> dict[str, Position]:
        """Read-only view of all currently tracked positions."""
        return dict(self._positions)

    @property
    def position_count(self) -> int:
        return len(self._positions)
