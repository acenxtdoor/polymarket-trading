"""
tests/test_order.py
===================
Unit tests for execution/order.py (paper-trading OrderExecutor).

Covers DRY_RUN buy/sell fills via the slippage model, rejection paths
(capital, slippage, missing market, no position), and the live seam.
"""

import os
import sys

import pytest

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from execution.order import OrderExecutor, OrderResult
from execution.portfolio import Portfolio


class FakeMarketFilter:
    """Minimal MarketFilter stand-in exposing get_market_metadata()."""
    def __init__(self, liquidity: dict[str, float]):
        self._liq = liquidity

    def get_market_metadata(self, slug):
        if slug in self._liq:
            return {"liquidityNum": self._liq[slug]}
        return None


@pytest.fixture
def portfolio(tmp_path):
    return Portfolio(path=tmp_path / "portfolio.json", initial_capital=10_000.0)


@pytest.fixture
def executor(portfolio):
    mf = FakeMarketFilter({"mkt": 10_000.0})
    return OrderExecutor(portfolio, mf, dry_run=True)


# ── Buy ──────────────────────────────────────────────────────────────────────

def test_buy_fills_at_slippage_adjusted_price(executor, portfolio):
    # fill = 0.60 + (100/10000)*0.5 = 0.605
    result = executor.buy("mkt", "tok", "YES", price=0.60, size_dollars=100.0)
    assert result.filled is True
    assert result.side == "BUY"
    assert result.fill_price == pytest.approx(0.605)
    assert result.shares == pytest.approx(100.0 / 0.605)
    assert result.dry_run is True
    # Portfolio reflects the fill
    pos = portfolio.get_position("mkt")
    assert pos.shares == pytest.approx(100.0 / 0.605)
    assert portfolio.capital == pytest.approx(9_900.0)


def test_buy_rejected_on_high_slippage(executor, portfolio):
    # 5000 into 10000 liquidity -> ~41% slippage, well over 3%
    result = executor.buy("mkt", "tok", "YES", price=0.60, size_dollars=5_000.0)
    assert result.filled is False
    assert "slippage" in result.reason
    assert portfolio.open_count == 0
    assert portfolio.capital == pytest.approx(10_000.0)


def test_buy_rejected_on_insufficient_capital(executor, portfolio):
    result = executor.buy("mkt", "tok", "YES", price=0.60, size_dollars=20_000.0)
    assert result.filled is False
    assert "insufficient capital" in result.reason
    assert portfolio.open_count == 0


def test_buy_rejected_on_nonpositive_size(executor):
    result = executor.buy("mkt", "tok", "YES", price=0.60, size_dollars=0.0)
    assert result.filled is False
    assert "size must be" in result.reason


def test_buy_rejected_when_market_not_cached(portfolio):
    executor = OrderExecutor(portfolio, FakeMarketFilter({}), dry_run=True)
    result = executor.buy("mkt", "tok", "YES", price=0.60, size_dollars=100.0)
    assert result.filled is False
    assert "not found" in result.reason
    assert portfolio.open_count == 0


# ── Sell ─────────────────────────────────────────────────────────────────────

def test_sell_closes_position_and_returns_filled(executor, portfolio):
    executor.buy("mkt", "tok", "YES", price=0.60, size_dollars=100.0)
    shares = portfolio.get_position("mkt").shares
    result = executor.sell("mkt", price=0.70)
    assert result.filled is True
    assert result.side == "SELL"
    assert result.fill_price == pytest.approx(0.70)
    assert result.shares == pytest.approx(shares)
    assert portfolio.open_count == 0
    # proceeds added back to capital
    assert portfolio.capital == pytest.approx(9_900.0 + shares * 0.70)


def test_sell_without_position_rejected(executor):
    result = executor.sell("mkt", price=0.50)
    assert result.filled is False
    assert result.reason == "no open position"


# ── Live seam ────────────────────────────────────────────────────────────────

def test_live_buy_not_implemented(portfolio):
    mf = FakeMarketFilter({"mkt": 10_000.0})
    live = OrderExecutor(portfolio, mf, dry_run=False)
    with pytest.raises(NotImplementedError, match="Live order placement"):
        live.buy("mkt", "tok", "YES", price=0.60, size_dollars=100.0)


def test_live_sell_not_implemented(portfolio):
    # open a position with a dry-run executor first
    mf = FakeMarketFilter({"mkt": 10_000.0})
    OrderExecutor(portfolio, mf, dry_run=True).buy("mkt", "tok", "YES", 0.60, 100.0)
    live = OrderExecutor(portfolio, mf, dry_run=False)
    with pytest.raises(NotImplementedError, match="Live order placement"):
        live.sell("mkt", price=0.70)


def test_order_result_is_frozen():
    r = OrderResult(
        filled=True, side="BUY", market_slug="m", token_id="t", outcome="YES",
        requested_price=0.5, fill_price=0.5, size_dollars=10.0, shares=20.0,
        slippage_pct=0.0, dry_run=True,
    )
    with pytest.raises(Exception):
        r.filled = False
