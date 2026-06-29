"""
execution/kalshi_order.py
=========================
Kalshi order executor. Mirrors the interface of execution/order.py but
routes to the Kalshi Trading API instead of the Polymarket CLOB.

In DRY_RUN mode (config.DRY_RUN=True): orders are logged but not placed.
In live mode (config.DRY_RUN=False): orders are submitted via kalshi/api.py.

Returns the same OrderResult dataclass as order.py so main.py result-handling
is unchanged.

Prices:
  - All external prices are decimal (0–1), matching the rest of the bot.
  - Kalshi API expects integer cents (1–99); we convert at the boundary.
Contract count:
  - Each Kalshi contract costs $0.01. count = int(size_dollars * 100 + 0.5)
  - Hard cap at 100,000 contracts (~$1,000) as a safety guard.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from execution.order import OrderResult
from execution.portfolio import Portfolio
from kalshi.api import place_order
from strategy.kalshi_filter import KalshiMarketFilter
from utils.logger import get_logger

logger = get_logger(__name__)

_MAX_CONTRACTS = 100_000   # hard safety cap (~$1,000)


class KalshiOrderExecutor:
    """
    Places buy/sell orders on Kalshi and keeps the Portfolio in sync.

    Args:
        portfolio:     Portfolio to debit/credit and record positions in.
        kalshi_filter: KalshiMarketFilter for metadata lookups (price sourcing).
        dry_run:       Override config.DRY_RUN for this executor.
    """

    def __init__(
        self,
        portfolio: Portfolio,
        kalshi_filter: KalshiMarketFilter,
        dry_run: bool | None = None,
    ) -> None:
        self.portfolio = portfolio
        self.kalshi_filter = kalshi_filter
        self.dry_run = config.DRY_RUN if dry_run is None else dry_run

    def buy(
        self,
        ticker: str,
        side: str,
        price_cents: int,
        size_dollars: float,
    ) -> OrderResult:
        """
        Buy `size_dollars` of YES contracts on `ticker` at `price_cents`.

        Args:
            ticker:      Kalshi market ticker (e.g. "KXBTC-25JUN-T60000")
            side:        "yes" (always YES in this strategy)
            price_cents: limit price in cents (1–99), typically the live yes_ask
            size_dollars: dollar amount to spend
        """
        price_decimal = price_cents / 100.0

        def reject(reason: str) -> OrderResult:
            logger.warning(f"[KALSHI-ORDER] BUY REJECT {ticker}: {reason}")
            return OrderResult(
                filled=False, side="BUY", market_slug=ticker, token_id=ticker,
                outcome=side.upper(), requested_price=price_decimal, fill_price=0.0,
                size_dollars=size_dollars, shares=0.0, slippage_pct=0.0,
                dry_run=self.dry_run, reason=reason,
            )

        if size_dollars <= 0:
            return reject(f"size must be > 0, got {size_dollars}")
        if size_dollars > self.portfolio.capital + 1e-9:
            return reject(
                f"insufficient capital: need ${size_dollars:,.2f}, "
                f"have ${self.portfolio.capital:,.2f}"
            )
        if not (1 <= price_cents <= 99):
            return reject(f"invalid price_cents={price_cents} — must be 1–99")

        count = min(int(size_dollars * 100 + 0.5), _MAX_CONTRACTS)
        if count < 1:
            return reject("size too small to buy even 1 contract ($0.01 minimum)")

        if self.dry_run:
            self.portfolio.record_buy(ticker, ticker, side.upper(), price_decimal, size_dollars)
            shares = size_dollars / price_decimal
            logger.info(
                f"[KALSHI-ORDER] [DRY RUN] BUY {ticker} {side.upper()}  "
                f"${size_dollars:,.2f} @ {price_decimal:.4f} ({price_cents}¢)  "
                f"{count} contracts"
            )
            return OrderResult(
                filled=True, side="BUY", market_slug=ticker, token_id=ticker,
                outcome=side.upper(), requested_price=price_decimal, fill_price=price_decimal,
                size_dollars=size_dollars, shares=shares, slippage_pct=0.0, dry_run=True,
            )

        # Live path
        order = place_order(ticker, side, count, price_cents, action="buy")
        if order is None:
            return reject("Kalshi API returned no order response")

        status = str(order.get("status", "")).lower()
        if status not in ("resting", "filled", "partially_filled", "executed"):
            return reject(f"order status '{status}' — not accepted")

        self.portfolio.record_buy(ticker, ticker, side.upper(), price_decimal, size_dollars)
        shares = size_dollars / price_decimal
        logger.info(
            f"[KALSHI-ORDER] BUY {ticker} {side.upper()}  "
            f"${size_dollars:,.2f} @ {price_decimal:.4f} ({price_cents}¢)  "
            f"{count} contracts  order_id={order.get('order_id', '?')}"
        )
        return OrderResult(
            filled=True, side="BUY", market_slug=ticker, token_id=ticker,
            outcome=side.upper(), requested_price=price_decimal, fill_price=price_decimal,
            size_dollars=size_dollars, shares=shares, slippage_pct=0.0, dry_run=False,
        )

    def sell(
        self,
        ticker: str,
        side: str,
        price_cents: int,
    ) -> OrderResult:
        """
        Close the open position in `ticker` by selling all held contracts.

        Args:
            ticker:      Kalshi market ticker
            side:        "yes"
            price_cents: current yes_bid in cents (1–99), used as limit price
        """
        pos = self.portfolio.get_position(ticker)
        if pos is None:
            logger.warning(f"[KALSHI-ORDER] SELL REJECT {ticker}: no open position")
            return OrderResult(
                filled=False, side="SELL", market_slug=ticker, token_id=ticker,
                outcome=side.upper(), requested_price=price_cents / 100.0, fill_price=0.0,
                size_dollars=0.0, shares=0.0, slippage_pct=0.0, dry_run=self.dry_run,
                reason="no open position",
            )

        price_decimal = price_cents / 100.0
        shares = pos.shares
        # Convert portfolio shares back to contract count.
        # shares = size_dollars / fill_price_decimal, and each contract = $0.01,
        # so contracts = shares * fill_price_decimal / 0.01 = shares * fill_price_decimal * 100.
        # Since pos.avg_price is in decimal (0–1), this equals round(shares * pos.avg_price * 100).
        count = min(max(1, round(shares * pos.avg_price * 100)), _MAX_CONTRACTS)

        if self.dry_run:
            token_id, outcome = pos.token_id, pos.outcome
            pnl = self.portfolio.record_sell(ticker, price_decimal)
            logger.info(
                f"[KALSHI-ORDER] [DRY RUN] SELL {ticker} {outcome}  "
                f"{shares:.2f} shares ({count} contracts) @ {price_decimal:.4f} ({price_cents}¢)  "
                f"P&L=${pnl:,.2f}"
            )
            return OrderResult(
                filled=True, side="SELL", market_slug=ticker, token_id=token_id,
                outcome=outcome, requested_price=price_decimal, fill_price=price_decimal,
                size_dollars=shares * price_decimal, shares=shares,
                slippage_pct=0.0, dry_run=True,
            )

        # Live path
        order = place_order(ticker, side, count, price_cents, action="sell")
        if order is None:
            return OrderResult(
                filled=False, side="SELL", market_slug=ticker, token_id=pos.token_id,
                outcome=pos.outcome, requested_price=price_decimal, fill_price=0.0,
                size_dollars=0.0, shares=0.0, slippage_pct=0.0, dry_run=False,
                reason="Kalshi API returned no order response",
            )

        token_id, outcome = pos.token_id, pos.outcome
        pnl = self.portfolio.record_sell(ticker, price_decimal)
        logger.info(
            f"[KALSHI-ORDER] SELL {ticker} {outcome}  "
            f"{shares:.2f} shares ({count} contracts) @ {price_decimal:.4f} ({price_cents}¢)  "
            f"P&L=${pnl:,.2f}  order_id={order.get('order_id', '?')}"
        )
        return OrderResult(
            filled=True, side="SELL", market_slug=ticker, token_id=token_id,
            outcome=outcome, requested_price=price_decimal, fill_price=price_decimal,
            size_dollars=shares * price_decimal, shares=shares,
            slippage_pct=0.0, dry_run=False,
        )
