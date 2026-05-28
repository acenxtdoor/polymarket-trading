"""
execution/order.py
==================
Order execution (Step 8).

Paper-trading executor. In DRY_RUN mode (the default, set in config.py) no real
orders are ever placed: buy fills are simulated at the slippage-adjusted fill
price, and the resulting position is recorded in the Portfolio. The live order
path is left as a stub seam for a future py-clob-client integration.

Flow:
    executor = OrderExecutor(portfolio, market_filter)
    result = executor.buy(slug, token_id, "YES", price=0.62, size_dollars=300)
    if result.filled:
        ...   # position now tracked in portfolio
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# Add the project root to sys.path so it can find 'config' etc. when run directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from execution.portfolio import Portfolio
from strategy.market_filter import MarketFilter
from strategy.slippage import estimate_slippage
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class OrderResult:
    """Outcome of a buy() or sell() call."""
    filled: bool
    side: str               # "BUY" / "SELL"
    market_slug: str
    token_id: str
    outcome: str
    requested_price: float
    fill_price: float
    size_dollars: float
    shares: float
    slippage_pct: float
    dry_run: bool
    reason: str = ""        # populated when filled is False


class OrderExecutor:
    """
    Places (paper) buy/sell orders and keeps the Portfolio in sync.

    Args:
        portfolio:     Portfolio to debit/credit and record positions in.
        market_filter: MarketFilter whose cache supplies liquidity for the
                       slippage model.
        dry_run:       Override config.DRY_RUN for this executor.
    """

    def __init__(
        self,
        portfolio: Portfolio,
        market_filter: MarketFilter,
        dry_run: bool | None = None,
    ) -> None:
        self.portfolio = portfolio
        self.market_filter = market_filter
        self.dry_run = config.DRY_RUN if dry_run is None else dry_run

    def buy(
        self,
        market_slug: str,
        token_id: str,
        outcome: str,
        price: float,
        size_dollars: float,
    ) -> OrderResult:
        """Buy `size_dollars` of `outcome` at `price`, filling at the slippage-adjusted price."""
        def reject(reason: str, fill_price: float = 0.0, slippage_pct: float = 0.0) -> OrderResult:
            logger.warning(f"[ORDER] BUY REJECT {market_slug} {outcome}: {reason}")
            return OrderResult(
                filled=False, side="BUY", market_slug=market_slug, token_id=token_id,
                outcome=outcome, requested_price=price, fill_price=fill_price,
                size_dollars=size_dollars, shares=0.0, slippage_pct=slippage_pct,
                dry_run=self.dry_run, reason=reason,
            )

        if size_dollars <= 0:
            return reject(f"size must be > 0, got {size_dollars}")
        if size_dollars > self.portfolio.capital + 1e-9:
            return reject(
                f"insufficient capital: need ${size_dollars:,.2f}, "
                f"have ${self.portfolio.capital:,.2f}"
            )

        try:
            slip = estimate_slippage(market_slug, price, size_dollars, self.market_filter)
        except ValueError as exc:
            return reject(str(exc))

        if not slip.is_safe:
            return reject(
                f"slippage {slip.slippage_pct:.1%} exceeds max",
                fill_price=slip.estimated_fill, slippage_pct=slip.slippage_pct,
            )

        fill_price = slip.estimated_fill
        if not self.dry_run:
            return self._live_buy(market_slug, token_id, outcome, fill_price, size_dollars)

        self.portfolio.record_buy(market_slug, token_id, outcome, fill_price, size_dollars)
        shares = size_dollars / fill_price
        logger.info(
            f"[ORDER] [DRY RUN] BUY {market_slug} {outcome}  "
            f"${size_dollars:,.2f} @ {fill_price:.4f}  ({shares:.2f} shares, "
            f"slippage {slip.slippage_pct:.2%})"
        )
        return OrderResult(
            filled=True, side="BUY", market_slug=market_slug, token_id=token_id,
            outcome=outcome, requested_price=price, fill_price=fill_price,
            size_dollars=size_dollars, shares=shares,
            slippage_pct=slip.slippage_pct, dry_run=True,
        )

    def sell(self, market_slug: str, price: float) -> OrderResult:
        """Close the open position in `market_slug` at `price`."""
        pos = self.portfolio.get_position(market_slug)
        if pos is None:
            logger.warning(f"[ORDER] SELL REJECT {market_slug}: no open position")
            return OrderResult(
                filled=False, side="SELL", market_slug=market_slug, token_id="",
                outcome="", requested_price=price, fill_price=0.0, size_dollars=0.0,
                shares=0.0, slippage_pct=0.0, dry_run=self.dry_run,
                reason="no open position",
            )

        if not self.dry_run:
            return self._live_sell(market_slug, price)

        shares = pos.shares
        token_id, outcome = pos.token_id, pos.outcome
        pnl = self.portfolio.record_sell(market_slug, price)
        logger.info(
            f"[ORDER] [DRY RUN] SELL {market_slug} {outcome}  "
            f"{shares:.2f} shares @ {price:.4f}  P&L=${pnl:,.2f}"
        )
        return OrderResult(
            filled=True, side="SELL", market_slug=market_slug, token_id=token_id,
            outcome=outcome, requested_price=price, fill_price=price,
            size_dollars=shares * price, shares=shares, slippage_pct=0.0, dry_run=True,
        )

    # ── Live seam (not implemented) ──────────────────────────────────────────

    def _live_buy(self, *args, **kwargs) -> OrderResult:
        raise NotImplementedError(
            "Live order placement is not implemented. Keep config.DRY_RUN=True. "
            "A real CLOB integration (py-clob-client, EIP-712 signing) plugs in here."
        )

    def _live_sell(self, *args, **kwargs) -> OrderResult:
        raise NotImplementedError(
            "Live order placement is not implemented. Keep config.DRY_RUN=True. "
            "A real CLOB integration (py-clob-client, EIP-712 signing) plugs in here."
        )
