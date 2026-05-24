"""
tests/test_portfolio.py
=======================
Unit tests for execution/portfolio.py.

Covers buy/sell accounting, P&L math, averaging into a position, bankroll
guards, equity/unrealized helpers, and JSON round-trip persistence.
"""

import os
import sys

import pytest

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from execution.portfolio import Portfolio, Position


@pytest.fixture
def portfolio(tmp_path):
    return Portfolio(path=tmp_path / "portfolio.json", initial_bankroll=10_000.0)


# ── Buy accounting ───────────────────────────────────────────────────────────

def test_record_buy_opens_position_and_debits_bankroll(portfolio):
    pos = portfolio.record_buy("mkt", "tok", "YES", fill_price=0.50, dollars=1_000.0)
    assert pos.shares == pytest.approx(2_000.0)      # 1000 / 0.50
    assert pos.avg_price == pytest.approx(0.50)
    assert pos.cost == pytest.approx(1_000.0)
    assert portfolio.bankroll == pytest.approx(9_000.0)
    assert portfolio.open_count == 1


def test_record_buy_averages_into_existing_position(portfolio):
    portfolio.record_buy("mkt", "tok", "YES", fill_price=0.50, dollars=1_000.0)  # 2000 sh
    pos = portfolio.record_buy("mkt", "tok", "YES", fill_price=0.40, dollars=400.0)  # 1000 sh
    assert pos.shares == pytest.approx(3_000.0)
    assert pos.cost == pytest.approx(1_400.0)
    assert pos.avg_price == pytest.approx(1_400.0 / 3_000.0)
    assert portfolio.bankroll == pytest.approx(8_600.0)
    assert portfolio.open_count == 1


def test_buy_rejects_overspend(portfolio):
    with pytest.raises(ValueError, match="insufficient bankroll"):
        portfolio.record_buy("mkt", "tok", "YES", fill_price=0.50, dollars=20_000.0)


def test_buy_rejects_bad_price(portfolio):
    with pytest.raises(ValueError, match="fill_price"):
        portfolio.record_buy("mkt", "tok", "YES", fill_price=1.5, dollars=100.0)


def test_buy_rejects_nonpositive_dollars(portfolio):
    with pytest.raises(ValueError, match="dollars must be"):
        portfolio.record_buy("mkt", "tok", "YES", fill_price=0.5, dollars=0.0)


# ── Sell accounting ──────────────────────────────────────────────────────────

def test_record_sell_realizes_profit(portfolio):
    portfolio.record_buy("mkt", "tok", "YES", fill_price=0.50, dollars=1_000.0)  # 2000 sh
    pnl = portfolio.record_sell("mkt", exit_price=0.60)
    # proceeds = 2000 * 0.60 = 1200; pnl = 1200 - 1000 = 200
    assert pnl == pytest.approx(200.0)
    assert portfolio.realized_pnl == pytest.approx(200.0)
    assert portfolio.bankroll == pytest.approx(10_200.0)
    assert portfolio.open_count == 0


def test_record_sell_realizes_loss(portfolio):
    portfolio.record_buy("mkt", "tok", "YES", fill_price=0.50, dollars=1_000.0)
    pnl = portfolio.record_sell("mkt", exit_price=0.40)
    assert pnl == pytest.approx(-200.0)
    assert portfolio.bankroll == pytest.approx(9_800.0)


def test_sell_without_position_raises(portfolio):
    with pytest.raises(KeyError, match="no open position"):
        portfolio.record_sell("nope", exit_price=0.5)


# ── Query helpers ────────────────────────────────────────────────────────────

def test_unrealized_and_equity(portfolio):
    portfolio.record_buy("a", "t", "YES", fill_price=0.50, dollars=1_000.0)  # 2000 sh
    portfolio.record_buy("b", "t", "YES", fill_price=0.25, dollars=500.0)    # 2000 sh
    prices = {"a": 0.55, "b": 0.20}
    # a: 2000*0.55 - 1000 = +100 ; b: 2000*0.20 - 500 = -100
    assert portfolio.unrealized_pnl(prices) == pytest.approx(0.0)
    # equity = bankroll(8500) + 2000*0.55 + 2000*0.20 = 8500 + 1100 + 400 = 10000
    assert portfolio.equity(prices) == pytest.approx(10_000.0)


def test_has_and_get_position(portfolio):
    assert portfolio.has_position("mkt") is False
    assert portfolio.get_position("mkt") is None
    portfolio.record_buy("mkt", "tok", "YES", fill_price=0.5, dollars=100.0)
    assert portfolio.has_position("mkt") is True
    assert isinstance(portfolio.get_position("mkt"), Position)


# ── Persistence ──────────────────────────────────────────────────────────────

def test_state_persists_across_reload(tmp_path):
    path = tmp_path / "portfolio.json"
    p1 = Portfolio(path=path, initial_bankroll=10_000.0)
    p1.record_buy("mkt", "tok", "YES", fill_price=0.50, dollars=1_000.0)
    p1.record_buy("other", "tok2", "NO", fill_price=0.30, dollars=300.0)
    p1.record_sell("other", exit_price=0.45)

    p2 = Portfolio(path=path)  # reload from disk
    assert p2.bankroll == pytest.approx(p1.bankroll)
    assert p2.realized_pnl == pytest.approx(p1.realized_pnl)
    assert p2.open_count == 1
    pos = p2.get_position("mkt")
    assert pos.shares == pytest.approx(2_000.0)
    assert pos.outcome == "YES"


def test_initial_bankroll_ignored_when_file_exists(tmp_path):
    path = tmp_path / "portfolio.json"
    Portfolio(path=path, initial_bankroll=10_000.0).record_buy(
        "mkt", "tok", "YES", fill_price=0.5, dollars=1_000.0
    )
    reloaded = Portfolio(path=path, initial_bankroll=999.0)
    assert reloaded.bankroll == pytest.approx(9_000.0)  # loaded, not the new initial
