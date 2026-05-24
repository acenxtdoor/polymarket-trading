"""
execution/portfolio.py
======================
Bankroll and position tracking with JSON persistence (Step 8).

Tracks available cash (bankroll), open positions, and realized P&L for the
paper-trading bot. State is persisted to a JSON file so it survives restarts
of the main loop.

Prediction-market accounting
----------------------------
A position is bought in dollars at a YES price in (0, 1). Each share pays $1
if the outcome resolves true, $0 otherwise:

    shares       = dollars / fill_price
    market_value = shares * current_price
    unrealized   = market_value - cost
    realized      = shares * exit_price - cost   (on sell)

Positions are keyed by market_slug — one position per market, matching the
trailing-stop tracker.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Add the project root to sys.path so it can find 'config' and 'utils' when run directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Position:
    """A single open position in one market."""
    market_slug: str
    token_id: str
    outcome: str          # "YES" / "NO"
    shares: float
    avg_price: float      # cost-weighted average fill price
    cost: float           # total dollars invested
    opened_at: float = field(default_factory=time.time)

    def market_value(self, price: float) -> float:
        return self.shares * price

    def unrealized_pnl(self, price: float) -> float:
        return self.market_value(price) - self.cost


class Portfolio:
    """
    In-memory portfolio backed by a JSON file.

    On construction, loads existing state from `path` if the file exists;
    otherwise starts fresh with `initial_bankroll`. Every mutation persists
    to disk immediately.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        initial_bankroll: float | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else Path(config.PORTFOLIO_PATH)
        self.bankroll: float = (
            initial_bankroll if initial_bankroll is not None else config.INITIAL_BANKROLL
        )
        self.realized_pnl: float = 0.0
        self.positions: dict[str, Position] = {}

        if self.path.exists():
            self._load()
            logger.info(
                f"[PORTFOLIO] loaded {self.path}: bankroll=${self.bankroll:,.2f}, "
                f"{len(self.positions)} open position(s), "
                f"realized P&L=${self.realized_pnl:,.2f}"
            )
        else:
            logger.info(
                f"[PORTFOLIO] new portfolio: bankroll=${self.bankroll:,.2f}"
            )

    # ── Mutations ────────────────────────────────────────────────────────────

    def record_buy(
        self,
        market_slug: str,
        token_id: str,
        outcome: str,
        fill_price: float,
        dollars: float,
    ) -> Position:
        """
        Record a buy fill: deduct cash, open or average into the position.

        Raises:
            ValueError: if dollars <= 0, fill_price not in (0, 1), or there is
                        not enough bankroll to cover the order.
        """
        if dollars <= 0:
            raise ValueError(f"dollars must be > 0, got {dollars}")
        if not (0.0 < fill_price < 1.0):
            raise ValueError(f"fill_price must be in (0, 1), got {fill_price}")
        if dollars > self.bankroll + 1e-9:
            raise ValueError(
                f"insufficient bankroll: need ${dollars:,.2f}, have ${self.bankroll:,.2f}"
            )

        shares = dollars / fill_price
        pos = self.positions.get(market_slug)

        if pos is None:
            pos = Position(
                market_slug=market_slug,
                token_id=token_id,
                outcome=outcome,
                shares=shares,
                avg_price=fill_price,
                cost=dollars,
            )
            self.positions[market_slug] = pos
        else:
            pos.shares += shares
            pos.cost += dollars
            pos.avg_price = pos.cost / pos.shares

        self.bankroll -= dollars
        self.save()
        logger.info(
            f"[PORTFOLIO] BUY  {market_slug} {outcome}  "
            f"+{shares:.2f} shares @ {fill_price:.4f}  cost=${dollars:,.2f}  "
            f"bankroll=${self.bankroll:,.2f}"
        )
        return pos

    def record_sell(self, market_slug: str, exit_price: float) -> float:
        """
        Close a position at exit_price: credit proceeds, realize P&L, remove it.

        Returns the realized P&L for the closed position.

        Raises:
            KeyError:   if there is no open position for market_slug.
            ValueError: if exit_price is negative.
        """
        if exit_price < 0:
            raise ValueError(f"exit_price must be >= 0, got {exit_price}")
        pos = self.positions.get(market_slug)
        if pos is None:
            raise KeyError(f"no open position for '{market_slug}'")

        proceeds = pos.shares * exit_price
        pnl = proceeds - pos.cost
        self.bankroll += proceeds
        self.realized_pnl += pnl
        del self.positions[market_slug]
        self.save()
        logger.info(
            f"[PORTFOLIO] SELL {market_slug}  {pos.shares:.2f} shares @ {exit_price:.4f}  "
            f"proceeds=${proceeds:,.2f}  P&L=${pnl:,.2f}  bankroll=${self.bankroll:,.2f}"
        )
        return pnl

    # ── Queries ──────────────────────────────────────────────────────────────

    def has_position(self, market_slug: str) -> bool:
        return market_slug in self.positions

    def get_position(self, market_slug: str) -> Position | None:
        return self.positions.get(market_slug)

    def unrealized_pnl(self, prices: dict[str, float]) -> float:
        """Total unrealized P&L across open positions priced by `prices`."""
        return sum(
            pos.unrealized_pnl(prices[slug])
            for slug, pos in self.positions.items()
            if slug in prices
        )

    def equity(self, prices: dict[str, float]) -> float:
        """Bankroll plus market value of all priced open positions."""
        held = sum(
            pos.market_value(prices[slug])
            for slug, pos in self.positions.items()
            if slug in prices
        )
        return self.bankroll + held

    @property
    def open_count(self) -> int:
        return len(self.positions)

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self) -> None:
        data = {
            "bankroll": self.bankroll,
            "realized_pnl": self.realized_pnl,
            "positions": {slug: asdict(pos) for slug, pos in self.positions.items()},
        }
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.bankroll = float(data["bankroll"])
        self.realized_pnl = float(data.get("realized_pnl", 0.0))
        self.positions = {
            slug: Position(**pos) for slug, pos in data.get("positions", {}).items()
        }
