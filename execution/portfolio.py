"""
execution/portfolio.py
======================
Capital and position tracking with JSON persistence (Step 8).

Tracks available cash (capital), open positions, and realized P&L for the
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
from datetime import date
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
    otherwise starts fresh with `initial_capital`. Every mutation persists
    to disk immediately.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        initial_capital: float | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else Path(config.PORTFOLIO_PATH)
        self.capital: float = (
            initial_capital if initial_capital is not None else config.INITIAL_CAPITAL
        )
        self.realized_pnl: float = 0.0
        self.positions: dict[str, Position] = {}
        # Slugs closed (stop-loss / take-profit) today — persisted so bot
        # restarts within the same calendar day cannot re-enter these markets.
        self._closed_date: str = date.today().isoformat()
        self._closed_today: set[str] = set()

        if self.path.exists():
            self._load()
            logger.info(
                f"[PORTFOLIO] loaded {self.path}: capital=${self.capital:,.2f}, "
                f"{len(self.positions)} open position(s), "
                f"realized P&L=${self.realized_pnl:,.2f}, "
                f"{len(self._closed_today)} slug(s) closed today"
            )
        else:
            logger.info(
                f"[PORTFOLIO] new portfolio: capital=${self.capital:,.2f}"
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
                        not enough capital to cover the order.
        """
        if dollars <= 0:
            raise ValueError(f"dollars must be > 0, got {dollars}")
        if not (0.0 < fill_price < 1.0):
            raise ValueError(f"fill_price must be in (0, 1), got {fill_price}")
        if dollars > self.capital + 1e-9:
            raise ValueError(
                f"insufficient capital: need ${dollars:,.2f}, have ${self.capital:,.2f}"
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

        self.capital -= dollars
        self.save()
        logger.info(
            f"[PORTFOLIO] BUY  {market_slug} {outcome}  "
            f"+{shares:.2f} shares @ {fill_price:.4f}  cost=${dollars:,.2f}  "
            f"capital=${self.capital:,.2f}"
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
        self.capital += proceeds
        self.realized_pnl += pnl
        del self.positions[market_slug]
        self.save()
        logger.info(
            f"[PORTFOLIO] SELL {market_slug}  {pos.shares:.2f} shares @ {exit_price:.4f}  "
            f"proceeds=${proceeds:,.2f}  P&L=${pnl:,.2f}  capital=${self.capital:,.2f}"
        )
        return pnl

    # ── Closed-today tracking ────────────────────────────────────────────────

    def mark_closed_today(self, market_slug: str) -> None:
        """Record that this slug was exited today (stop-loss or take-profit).

        The set resets automatically when the calendar date rolls over so stale
        blacklist entries never block the next trading day. Persisted to disk so
        bot restarts within the same day honour the blacklist.
        """
        today = date.today().isoformat()
        if today != self._closed_date:
            # New calendar day — clear yesterday's blacklist.
            self._closed_date = today
            self._closed_today = set()
        self._closed_today.add(market_slug)
        self.save()

    def is_closed_today(self, market_slug: str) -> bool:
        """Return True if this slug was exited today and must not be re-entered."""
        today = date.today().isoformat()
        if today != self._closed_date:
            # New calendar day — blacklist is stale, clear it.
            self._closed_date = today
            self._closed_today = set()
        return market_slug in self._closed_today

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
        """Capital plus market value of all priced open positions."""
        held = sum(
            pos.market_value(prices[slug])
            for slug, pos in self.positions.items()
            if slug in prices
        )
        return self.capital + held

    @property
    def open_count(self) -> int:
        return len(self.positions)

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self) -> None:
        data = {
            "capital": self.capital,
            "realized_pnl": self.realized_pnl,
            "positions": {slug: asdict(pos) for slug, pos in self.positions.items()},
            "closed_date": self._closed_date,
            "closed_today": sorted(self._closed_today),
        }
        # Atomic write: write to a temp file then rename over the real file.
        # os.replace() is atomic on Windows and POSIX — the file is either
        # fully old or fully new, never half-written. This prevents a crash
        # or restart mid-write from leaving a stale position in portfolio.json
        # that would be rehydrated into the stop manager on the next startup.
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        # Back-compat: portfolios saved before the bankroll→capital rename used
        # the "bankroll" key. Read it if "capital" is absent; the next save()
        # rewrites the file with the new key.
        self.capital = float(data["capital"] if "capital" in data else data["bankroll"])
        self.realized_pnl = float(data.get("realized_pnl", 0.0))
        self.positions = {
            slug: Position(**pos) for slug, pos in data.get("positions", {}).items()
        }
        # Restore closed-today blacklist. If the saved date is yesterday (or older),
        # discard it — a new trading day starts fresh.
        saved_date = data.get("closed_date", "")
        today = date.today().isoformat()
        if saved_date == today:
            self._closed_date = today
            self._closed_today = set(data.get("closed_today", []))
        else:
            self._closed_date = today
            self._closed_today = set()
