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

import dataclasses
import time
from dataclasses import dataclass, field

from config import TRAILING_STOP_PCT
from utils.logger import get_logger

logger = get_logger(__name__)


def adaptive_stop_pct(entry_price: float) -> float:
    """Compute a per-position trailing stop percentage scaled to remaining upside.

    A fixed 10% stop on a 0.97 entry guarantees losses — the stop fires at 0.873
    while the max possible gain is only 3%.  This function scales the stop so it
    never exceeds 50% of the remaining upside, with 10% as the hard ceiling.

    Formula:  stop_pct = min(TRAILING_STOP_PCT, 0.5 × (1 − entry) / entry)

    Examples:
        entry=0.30  → stop=10.0%  (normal market, full stop applies)
        entry=0.60  → stop=10.0%  (still below ceiling)
        entry=0.80  → stop=10.0%  (ceiling; max gain=25%, risk=10%)
        entry=0.85  → stop= 8.8%  (max gain=17.6%, risk=8.8%)
        entry=0.90  → stop= 5.6%  (max gain=11.1%, risk=5.6%)
        entry=0.95  → stop= 2.6%  (max gain= 5.3%, risk=2.6%)
        entry=0.97  → stop= 1.5%  (max gain= 3.1%, risk=1.5%)

    Risk/reward is always ≥ 2:1 by construction.
    """
    remaining_upside = (1.0 - entry_price) / max(entry_price, 1e-9)
    return min(TRAILING_STOP_PCT, max(0.01, remaining_upside * 0.50))


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class Position:
    """Internal record for a single tracked position."""
    market_id:   str
    entry_price: float
    peak_price:  float
    stop_pct:    float = TRAILING_STOP_PCT   # per-position, set at open
    opened_at:   float = field(default_factory=time.time)

    @property
    def trail_price(self) -> float:
        """The price level at which the trailing stop triggers."""
        return self.peak_price * (1.0 - self.stop_pct)


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

        stop = adaptive_stop_pct(entry_price)
        pos = Position(
            market_id=market_id,
            entry_price=entry_price,
            peak_price=entry_price,
            stop_pct=stop,
        )
        self._positions[market_id] = pos
        logger.info(
            f"[TRAILING] OPEN  {market_id}  entry={entry_price:.4f}  "
            f"stop={stop:.1%}  trail={pos.trail_price:.4f}"
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
        Invalid prices (e.g. negative) are logged and skipped — they do not
        abort the rest of the batch.

        Args:
            prices: dict mapping market_id → current YES price

        Returns:
            List of StopResult for each successfully updated position.
        """
        results: list[StopResult] = []
        for market_id, price in prices.items():
            if market_id not in self._positions:
                continue
            try:
                results.append(self.update(market_id, price))
            except ValueError as exc:
                logger.warning(
                    f"[TRAILING] check_all skipping '{market_id}': {exc}"
                )
        return results

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def open_positions(self) -> dict[str, Position]:
        """
        Snapshot of all currently tracked positions.

        Returns shallow copies of each Position so callers cannot accidentally
        mutate internal peak_price / entry_price state.
        """
        return {mid: dataclasses.replace(pos) for mid, pos in self._positions.items()}

    @property
    def position_count(self) -> int:
        return len(self._positions)
