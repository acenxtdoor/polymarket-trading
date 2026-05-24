"""
Tests for strategy/trailing_stop.py — trailing stop manager (Step 7).
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from strategy.trailing_stop import TrailingStopManager, Position, StopResult


STOP = config.TRAILING_STOP_PCT  # 0.10


# ── Helpers ───────────────────────────────────────────────────────────────────

def _manager_with_position(market_id="mkt-001", entry=0.60) -> TrailingStopManager:
    m = TrailingStopManager()
    m.open_position(market_id, entry)
    return m


# ── open_position ─────────────────────────────────────────────────────────────

class TestOpenPosition(unittest.TestCase):

    def test_position_created_with_correct_entry(self):
        m = TrailingStopManager()
        pos = m.open_position("mkt-001", 0.60)
        self.assertEqual(pos.entry_price, 0.60)

    def test_peak_initialised_to_entry(self):
        m = TrailingStopManager()
        pos = m.open_position("mkt-001", 0.60)
        self.assertEqual(pos.peak_price, 0.60)

    def test_trail_price_at_open(self):
        m = TrailingStopManager()
        pos = m.open_position("mkt-001", 0.60)
        expected = 0.60 * (1 - STOP)
        self.assertAlmostEqual(pos.trail_price, expected, places=6)

    def test_position_stored(self):
        m = TrailingStopManager()
        m.open_position("mkt-001", 0.60)
        self.assertIn("mkt-001", m.open_positions)

    def test_position_count_increments(self):
        m = TrailingStopManager()
        m.open_position("mkt-001", 0.60)
        m.open_position("mkt-002", 0.45)
        self.assertEqual(m.position_count, 2)

    def test_duplicate_raises_value_error(self):
        m = _manager_with_position()
        with self.assertRaises(ValueError):
            m.open_position("mkt-001", 0.65)

    def test_entry_price_zero_raises(self):
        m = TrailingStopManager()
        with self.assertRaises(ValueError):
            m.open_position("mkt-001", 0.0)

    def test_entry_price_one_raises(self):
        m = TrailingStopManager()
        with self.assertRaises(ValueError):
            m.open_position("mkt-001", 1.0)

    def test_entry_price_negative_raises(self):
        m = TrailingStopManager()
        with self.assertRaises(ValueError):
            m.open_position("mkt-001", -0.10)

    def test_entry_price_above_one_raises(self):
        m = TrailingStopManager()
        with self.assertRaises(ValueError):
            m.open_position("mkt-001", 1.50)


# ── update — stop NOT triggered ───────────────────────────────────────────────

class TestUpdateNoStop(unittest.TestCase):

    def test_price_rise_no_stop(self):
        """Price going up — no stop triggered."""
        m = _manager_with_position(entry=0.60)
        result = m.update("mkt-001", 0.70)
        self.assertFalse(result.should_close)

    def test_price_flat_no_stop(self):
        """Price unchanged — no stop triggered."""
        m = _manager_with_position(entry=0.60)
        result = m.update("mkt-001", 0.60)
        self.assertFalse(result.should_close)

    def test_drop_below_10pct_no_stop(self):
        """9% drop from entry — just inside the 10% trail."""
        m = _manager_with_position(entry=0.60)
        current = round(0.60 * (1 - (STOP - 0.01)), 6)
        result = m.update("mkt-001", current)
        self.assertFalse(result.should_close)

    def test_peak_rises_then_small_drop_no_stop(self):
        """Peak advances to 0.80; a 5% drop from peak (0.76) should not trigger."""
        m = _manager_with_position(entry=0.60)
        m.update("mkt-001", 0.80)        # new peak
        result = m.update("mkt-001", 0.76)
        self.assertFalse(result.should_close)


# ── update — stop triggered ───────────────────────────────────────────────────

class TestUpdateStopTriggered(unittest.TestCase):

    def test_exactly_at_trail_triggers(self):
        """Price exactly at trail level (≤) must trigger the stop."""
        entry = 0.60
        m = _manager_with_position(entry=entry)
        trail = entry * (1 - STOP)
        result = m.update("mkt-001", trail)
        self.assertTrue(result.should_close)

    def test_below_trail_triggers(self):
        """Price below trail level must trigger the stop."""
        entry = 0.60
        m = _manager_with_position(entry=entry)
        below_trail = entry * (1 - STOP) - 0.01
        result = m.update("mkt-001", below_trail)
        self.assertTrue(result.should_close)

    def test_stop_after_peak_advance(self):
        """Peak advances to 0.80; a 10%+ drop from 0.80 must trigger the stop."""
        m = _manager_with_position(entry=0.60)
        m.update("mkt-001", 0.80)        # peak = 0.80
        trail = 0.80 * (1 - STOP)        # = 0.72
        result = m.update("mkt-001", trail - 0.001)
        self.assertTrue(result.should_close)

    def test_price_collapse_triggers(self):
        """Catastrophic price drop to near zero must trigger the stop."""
        m = _manager_with_position(entry=0.60)
        result = m.update("mkt-001", 0.01)
        self.assertTrue(result.should_close)


# ── update — result fields ────────────────────────────────────────────────────

class TestUpdateResultFields(unittest.TestCase):

    def test_peak_updates_on_new_high(self):
        m = _manager_with_position(entry=0.60)
        result = m.update("mkt-001", 0.75)
        self.assertAlmostEqual(result.peak_price, 0.75, places=6)

    def test_peak_does_not_retreat_on_drop(self):
        m = _manager_with_position(entry=0.60)
        m.update("mkt-001", 0.75)
        result = m.update("mkt-001", 0.65)
        self.assertAlmostEqual(result.peak_price, 0.75, places=6)

    def test_trail_price_reflects_updated_peak(self):
        m = _manager_with_position(entry=0.60)
        m.update("mkt-001", 0.80)
        result = m.update("mkt-001", 0.75)
        expected_trail = 0.80 * (1 - STOP)
        self.assertAlmostEqual(result.trail_price, expected_trail, places=6)

    def test_drawdown_pct_correct(self):
        m = _manager_with_position(entry=0.60)
        m.update("mkt-001", 0.80)        # peak = 0.80
        result = m.update("mkt-001", 0.72)
        expected = (0.80 - 0.72) / 0.80  # 0.10
        self.assertAlmostEqual(result.drawdown_pct, expected, places=6)

    def test_current_price_stored_in_result(self):
        m = _manager_with_position(entry=0.60)
        result = m.update("mkt-001", 0.73)
        self.assertAlmostEqual(result.current_price, 0.73, places=6)

    def test_market_id_in_result(self):
        m = _manager_with_position("mkt-XYZ", entry=0.50)
        result = m.update("mkt-XYZ", 0.55)
        self.assertEqual(result.market_id, "mkt-XYZ")


# ── update — error handling ───────────────────────────────────────────────────

class TestUpdateErrors(unittest.TestCase):

    def test_unknown_market_raises_key_error(self):
        m = TrailingStopManager()
        with self.assertRaises(KeyError):
            m.update("does-not-exist", 0.50)

    def test_negative_price_raises_value_error(self):
        m = _manager_with_position()
        with self.assertRaises(ValueError):
            m.update("mkt-001", -0.05)


# ── close_position ────────────────────────────────────────────────────────────

class TestClosePosition(unittest.TestCase):

    def test_close_removes_position(self):
        m = _manager_with_position()
        m.close_position("mkt-001")
        self.assertNotIn("mkt-001", m.open_positions)

    def test_close_returns_position(self):
        m = _manager_with_position(entry=0.60)
        pos = m.close_position("mkt-001")
        self.assertIsInstance(pos, Position)
        self.assertEqual(pos.entry_price, 0.60)

    def test_close_unknown_raises_key_error(self):
        m = TrailingStopManager()
        with self.assertRaises(KeyError):
            m.close_position("does-not-exist")

    def test_reopen_after_close_succeeds(self):
        m = _manager_with_position(entry=0.60)
        m.close_position("mkt-001")
        pos = m.open_position("mkt-001", 0.55)
        self.assertAlmostEqual(pos.entry_price, 0.55)

    def test_position_count_decrements(self):
        m = TrailingStopManager()
        m.open_position("mkt-001", 0.60)
        m.open_position("mkt-002", 0.45)
        m.close_position("mkt-001")
        self.assertEqual(m.position_count, 1)


# ── check_all ────────────────────────────────────────────────────────────────

class TestCheckAll(unittest.TestCase):

    def test_returns_result_for_each_known_market(self):
        m = TrailingStopManager()
        m.open_position("mkt-001", 0.60)
        m.open_position("mkt-002", 0.45)
        results = m.check_all({"mkt-001": 0.62, "mkt-002": 0.43})
        self.assertEqual(len(results), 2)

    def test_unknown_markets_in_prices_are_ignored(self):
        m = _manager_with_position()
        results = m.check_all({"mkt-001": 0.62, "unknown-mkt": 0.50})
        self.assertEqual(len(results), 1)

    def test_empty_prices_returns_empty(self):
        m = _manager_with_position()
        results = m.check_all({})
        self.assertEqual(results, [])

    def test_stop_detected_in_batch(self):
        m = TrailingStopManager()
        m.open_position("mkt-001", 0.60)
        m.open_position("mkt-002", 0.80)
        # mkt-001 drops below trail, mkt-002 stays healthy
        trail_001 = 0.60 * (1 - STOP) - 0.01
        results = m.check_all({"mkt-001": trail_001, "mkt-002": 0.82})
        stops = [r for r in results if r.should_close]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0].market_id, "mkt-001")

    def test_check_all_updates_peaks(self):
        m = _manager_with_position(entry=0.60)
        m.check_all({"mkt-001": 0.90})
        pos = m.open_positions["mkt-001"]
        self.assertAlmostEqual(pos.peak_price, 0.90, places=6)


# ── open_positions property ───────────────────────────────────────────────────

class TestOpenPositionsProperty(unittest.TestCase):

    def test_returns_copy_not_internal_dict(self):
        """Mutating the returned dict must not affect internal state."""
        m = _manager_with_position()
        snapshot = m.open_positions
        snapshot["mkt-001"] = None  # type: ignore
        self.assertIsNotNone(m.open_positions["mkt-001"])

    def test_empty_when_no_positions(self):
        m = TrailingStopManager()
        self.assertEqual(m.open_positions, {})


# ── Bug-fix regression tests ──────────────────────────────────────────────────

class TestOpenPositionsMutability(unittest.TestCase):
    """Regression: open_positions must return copies, not live references."""

    def test_mutating_returned_position_does_not_affect_internal_state(self):
        """Bug fix: external mutation of peak_price must not corrupt the manager."""
        m = _manager_with_position(entry=0.60)
        snapshot = m.open_positions
        snapshot["mkt-001"].peak_price = 9999.0  # mutate the copy
        # Internal peak must still be 0.60
        internal = m._positions["mkt-001"]
        self.assertAlmostEqual(internal.peak_price, 0.60, places=6)

    def test_trail_price_on_copy_is_consistent(self):
        """Copied Position trail_price still reflects its own peak_price."""
        m = _manager_with_position(entry=0.60)
        m.update("mkt-001", 0.80)  # advance peak
        pos_copy = m.open_positions["mkt-001"]
        expected_trail = 0.80 * (1 - STOP)
        self.assertAlmostEqual(pos_copy.trail_price, expected_trail, places=6)


class TestCheckAllErrorHandling(unittest.TestCase):
    """Regression: a bad price in check_all must not abort the entire batch."""

    def test_invalid_price_skipped_others_still_processed(self):
        """Bug fix: negative price for one market must not stop others updating."""
        m = TrailingStopManager()
        m.open_position("mkt-good", 0.60)
        m.open_position("mkt-bad", 0.50)
        results = m.check_all({"mkt-good": 0.65, "mkt-bad": -0.10})
        market_ids = [r.market_id for r in results]
        self.assertIn("mkt-good", market_ids)
        self.assertNotIn("mkt-bad", market_ids)

    def test_all_invalid_returns_empty(self):
        """All invalid prices → empty result list, no exception raised."""
        m = _manager_with_position()
        results = m.check_all({"mkt-001": -1.0})
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
