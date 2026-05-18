"""
tests/test_slippage.py
======================
Unit tests for strategy/slippage.py.

Covers:
    - Safe slippage scenario (order is small relative to liquidity)
    - Unsafe slippage scenario (order moves price above MAX_SLIPPAGE_PCT)
    - Exact boundary case (slippage == MAX_SLIPPAGE_PCT exactly)
    - Edge cases: zero price, zero liquidity, missing market in cache
    - Correct field population in SlippageResult dataclass
    - Logging output format for both safe and skip cases
"""

import logging
import sys
import os
import pytest
from unittest.mock import MagicMock, PropertyMock

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from strategy.slippage import estimate_slippage, SlippageResult
from config import MAX_SLIPPAGE_PCT


# ── Fixtures ───────────────────────────────────────────────────────────────────

def make_market_filter(market_id: str, liquidity: float) -> MagicMock:
    """
    Builds a minimal MarketFilter mock that returns the given liquidity
    for the given market_id via both get_market() and .cache.
    """
    mock = MagicMock()
    metadata = {"liquidityNum": liquidity}
    
    # Configure get_market to only return metadata if the ID matches
    def get_market_side_effect(mid):
        if mid == market_id:
            return metadata
        return None
        
    mock.get_market.side_effect = get_market_side_effect
    mock.cache = {market_id: metadata}
    return mock


MARKET_ID     = "test-market-abc123"
TRADER_PRICE  = 0.60    # 60 cents — typical Polymarket probability price
SAFE_AMOUNT   = 100.0   # small order
UNSAFE_AMOUNT = 5000.0  # large order relative to liquidity
LIQUIDITY     = 10_000.0


# ── Math verification helpers ──────────────────────────────────────────────────

def expected_fill(trader_price: float, buy_amount: float, liquidity: float) -> float:
    return trader_price + (buy_amount / liquidity) * 0.5


def expected_slippage_pct(trader_price: float, buy_amount: float, liquidity: float) -> float:
    fill = expected_fill(trader_price, buy_amount, liquidity)
    return (fill - trader_price) / trader_price


# ── Safe scenario ──────────────────────────────────────────────────────────────

class TestSafeSlippage:
    """
    $100 order into $10,000 liquidity at price 0.60.
    estimated_fill  = 0.60 + (100 / 10000) * 0.5 = 0.60 + 0.005 = 0.605
    slippage_pct    = (0.605 - 0.60) / 0.60       = 0.00833... = 0.833%
    MAX_SLIPPAGE_PCT = 3.0%
    → is_safe = True
    """

    def setup_method(self):
        self.mf     = make_market_filter(MARKET_ID, LIQUIDITY)
        self.result = estimate_slippage(MARKET_ID, TRADER_PRICE, SAFE_AMOUNT, self.mf)

    def test_is_safe_true(self):
        assert self.result.is_safe is True

    def test_estimated_fill_math(self):
        expected = expected_fill(TRADER_PRICE, SAFE_AMOUNT, LIQUIDITY)
        assert abs(self.result.estimated_fill - expected) < 1e-10

    def test_estimated_fill_value(self):
        # 0.60 + (100/10000)*0.5 = 0.6050
        assert abs(self.result.estimated_fill - 0.605) < 1e-10

    def test_slippage_pct_math(self):
        expected = expected_slippage_pct(TRADER_PRICE, SAFE_AMOUNT, LIQUIDITY)
        assert abs(self.result.slippage_pct - expected) < 1e-10

    def test_slippage_pct_value(self):
        # (0.605 - 0.60) / 0.60 = 0.00833...
        assert abs(self.result.slippage_pct - (0.005 / 0.60)) < 1e-10

    def test_slippage_below_max(self):
        assert self.result.slippage_pct <= (MAX_SLIPPAGE_PCT + 1e-12)

    def test_returns_slippage_result_type(self):
        assert isinstance(self.result, SlippageResult)


# ── Unsafe scenario ────────────────────────────────────────────────────────────

class TestUnsafeSlippage:
    """
    $5000 order into $10,000 liquidity at price 0.60.
    estimated_fill  = 0.60 + (5000 / 10000) * 0.5 = 0.60 + 0.25 = 0.85
    slippage_pct    = (0.85 - 0.60) / 0.60         = 0.4167 = 41.67%
    MAX_SLIPPAGE_PCT = 3.0%
    → is_safe = False
    """

    def setup_method(self):
        self.mf     = make_market_filter(MARKET_ID, LIQUIDITY)
        self.result = estimate_slippage(MARKET_ID, TRADER_PRICE, UNSAFE_AMOUNT, self.mf)

    def test_is_safe_false(self):
        assert self.result.is_safe is False

    def test_estimated_fill_math(self):
        expected = expected_fill(TRADER_PRICE, UNSAFE_AMOUNT, LIQUIDITY)
        assert abs(self.result.estimated_fill - expected) < 1e-10

    def test_estimated_fill_value(self):
        # 0.60 + (5000/10000)*0.5 = 0.85
        assert abs(self.result.estimated_fill - 0.85) < 1e-10

    def test_slippage_pct_math(self):
        expected = expected_slippage_pct(TRADER_PRICE, UNSAFE_AMOUNT, LIQUIDITY)
        assert abs(self.result.slippage_pct - expected) < 1e-10

    def test_slippage_pct_value(self):
        # (0.85 - 0.60) / 0.60 = 0.41666...
        assert abs(self.result.slippage_pct - (0.25 / 0.60)) < 1e-10

    def test_slippage_above_max(self):
        assert self.result.slippage_pct > MAX_SLIPPAGE_PCT

    def test_returns_slippage_result_type(self):
        assert isinstance(self.result, SlippageResult)


# ── Boundary case ──────────────────────────────────────────────────────────────

class TestBoundarySlippage:
    """
    Constructs buy_amount such that slippage_pct == MAX_SLIPPAGE_PCT exactly.

    slippage_pct = (buy_amount / liquidity) * 0.5 / trader_price = MAX_SLIPPAGE_PCT
    buy_amount   = MAX_SLIPPAGE_PCT * trader_price * liquidity / 0.5
                 = 0.03 * 0.60 * 10000 / 0.5 = 360.0

    estimated_fill = 0.60 + (360 / 10000) * 0.5 = 0.60 + 0.018 = 0.618
    slippage_pct   = (0.618 - 0.60) / 0.60       = 0.03 exactly
    → is_safe = True (<=, not <)
    """

    def setup_method(self):
        self.boundary_amount = MAX_SLIPPAGE_PCT * TRADER_PRICE * LIQUIDITY / 0.5
        self.mf     = make_market_filter(MARKET_ID, LIQUIDITY)
        self.result = estimate_slippage(
            MARKET_ID, TRADER_PRICE, self.boundary_amount, self.mf
        )

    def test_boundary_is_safe(self):
        # Exactly at MAX_SLIPPAGE_PCT should be considered safe (<=)
        assert self.result.is_safe is True

    def test_slippage_equals_max(self):
        assert abs(self.result.slippage_pct - MAX_SLIPPAGE_PCT) < 1e-9

    def test_boundary_plus_one_cent_is_unsafe(self):
        # One cent above boundary should flip to unsafe
        result = estimate_slippage(
            MARKET_ID, TRADER_PRICE, self.boundary_amount + 0.01, self.mf
        )
        assert result.is_safe is False


# ── Edge cases ─────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_zero_trader_price_raises(self):
        mf = make_market_filter(MARKET_ID, LIQUIDITY)
        with pytest.raises(ValueError, match="trader_price must be > 0"):
            estimate_slippage(MARKET_ID, 0.0, SAFE_AMOUNT, mf)

    def test_zero_liquidity_raises(self):
        mf = make_market_filter(MARKET_ID, 0.0)
        with pytest.raises(ValueError, match="zero or negative liquidity"):
            estimate_slippage(MARKET_ID, TRADER_PRICE, SAFE_AMOUNT, mf)

    def test_negative_liquidity_raises(self):
        mf = make_market_filter(MARKET_ID, -500.0)
        with pytest.raises(ValueError, match="zero or negative liquidity"):
            estimate_slippage(MARKET_ID, TRADER_PRICE, SAFE_AMOUNT, mf)

    def test_missing_market_raises(self):
        mf = make_market_filter("other-market", LIQUIDITY)
        with pytest.raises(ValueError, match="not found in MarketFilter cache"):
            estimate_slippage(MARKET_ID, TRADER_PRICE, SAFE_AMOUNT, mf)

    def test_missing_liquidity_num_raises(self):
        mf = MagicMock()
        mf.get_market.return_value = {"someOtherField": 999}
        mf.cache = {MARKET_ID: {"someOtherField": 999}}
        with pytest.raises(ValueError, match="liquidityNum missing"):
            estimate_slippage(MARKET_ID, TRADER_PRICE, SAFE_AMOUNT, mf)

    def test_high_liquidity_minimal_slippage(self):
        """Very deep market — even a large order barely moves price."""
        mf = make_market_filter(MARKET_ID, 10_000_000.0)
        result = estimate_slippage(MARKET_ID, TRADER_PRICE, 10_000.0, mf)
        assert result.is_safe is True
        assert result.slippage_pct < 0.001   # less than 0.1%

    def test_tiny_amount_near_zero_slippage(self):
        """$1 order — slippage should be negligible."""
        mf = make_market_filter(MARKET_ID, LIQUIDITY)
        result = estimate_slippage(MARKET_ID, TRADER_PRICE, 1.0, mf)
        assert result.is_safe is True
        assert result.slippage_pct < 0.0001

    def test_price_near_one(self):
        """Price near 1.0 — check slippage pct still computes correctly."""
        mf = make_market_filter(MARKET_ID, LIQUIDITY)
        result = estimate_slippage(MARKET_ID, 0.99, SAFE_AMOUNT, mf)
        expected = expected_slippage_pct(0.99, SAFE_AMOUNT, LIQUIDITY)
        assert abs(result.slippage_pct - expected) < 1e-10


# ── Logging tests ──────────────────────────────────────────────────────────────

class TestLogging:

    def test_safe_trade_logs_ok(self, caplog):
        mf = make_market_filter(MARKET_ID, LIQUIDITY)
        with caplog.at_level(logging.INFO, logger="strategy.slippage"):
            estimate_slippage(MARKET_ID, TRADER_PRICE, SAFE_AMOUNT, mf)
        assert "[SLIPPAGE] OK" in caplog.text

    def test_unsafe_trade_logs_skip_with_percentage(self, caplog):
        mf = make_market_filter(MARKET_ID, LIQUIDITY)
        with caplog.at_level(logging.WARNING, logger="strategy.slippage"):
            estimate_slippage(MARKET_ID, TRADER_PRICE, UNSAFE_AMOUNT, mf)
        assert "[SLIPPAGE] SKIP" in caplog.text
        assert "exceeds" in caplog.text
        assert "3%" in caplog.text or "3.0%" in caplog.text

    def test_skip_log_contains_actual_slippage(self, caplog):
        """The skip log should include the actual computed slippage percentage."""
        mf = make_market_filter(MARKET_ID, LIQUIDITY)
        with caplog.at_level(logging.WARNING, logger="strategy.slippage"):
            result = estimate_slippage(MARKET_ID, TRADER_PRICE, UNSAFE_AMOUNT, mf)
        # The log should contain the actual slippage pct (41.7%)
        pct_str = f"{result.slippage_pct * 100:.1f}%"
        assert pct_str in caplog.text


# ── SlippageResult dataclass ───────────────────────────────────────────────────

class TestSlippageResultDataclass:

    def test_all_fields_present(self):
        mf     = make_market_filter(MARKET_ID, LIQUIDITY)
        result = estimate_slippage(MARKET_ID, TRADER_PRICE, SAFE_AMOUNT, mf)
        assert hasattr(result, "is_safe")
        assert hasattr(result, "estimated_fill")
        assert hasattr(result, "slippage_pct")

    def test_is_safe_is_bool(self):
        mf     = make_market_filter(MARKET_ID, LIQUIDITY)
        result = estimate_slippage(MARKET_ID, TRADER_PRICE, SAFE_AMOUNT, mf)
        assert isinstance(result.is_safe, bool)

    def test_estimated_fill_is_float(self):
        mf     = make_market_filter(MARKET_ID, LIQUIDITY)
        result = estimate_slippage(MARKET_ID, TRADER_PRICE, SAFE_AMOUNT, mf)
        assert isinstance(result.estimated_fill, float)

    def test_slippage_pct_is_float(self):
        mf     = make_market_filter(MARKET_ID, LIQUIDITY)
        result = estimate_slippage(MARKET_ID, TRADER_PRICE, SAFE_AMOUNT, mf)
        assert isinstance(result.slippage_pct, float)

    def test_estimated_fill_always_gte_trader_price(self):
        """Buy orders always increase the fill price — never get a better price."""
        for amount in [1, 10, 100, 1000, 5000]:
            mf     = make_market_filter(MARKET_ID, LIQUIDITY)
            result = estimate_slippage(MARKET_ID, TRADER_PRICE, float(amount), mf)
            assert result.estimated_fill >= TRADER_PRICE
