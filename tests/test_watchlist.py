"""
Tests for intelligence/watchlist.py — JARVIS per-period market selection.

Claude calls (assess_markets_parallel) are mocked; no network requests are made.
"""

import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from intelligence.analyst import MarketAssessment
from intelligence.watchlist import (
    MarketCandidate,
    build_watchlist,
    compute_score,
)
from strategy.kalshi import AGREE, DISAGREE, NO_MATCH


def _candidate(market_id, *, hours=72, kalshi=NO_MATCH, traders=2, title=None):
    return MarketCandidate(
        market_id=market_id,
        market_title=title or f"Market {market_id}",
        yes_price=0.55,
        trader_count=traders,
        avg_trader_price=0.54,
        kalshi_signal=kalshi,
        hours_to_resolution=hours,
    )


def _fake_assessor(confidence_by_id):
    """Return a stand-in for assess_markets_parallel keyed by market_id."""
    def _inner(inputs):
        return {
            item["market_id"]: MarketAssessment(
                market_id=item["market_id"],
                confidence=confidence_by_id.get(item["market_id"], "medium"),
                summary="mock",
                flags=[],
            )
            for item in inputs
        }
    return _inner


# ── Unit: compute_score ────────────────────────────────────────────────────────

class TestComputeScore(unittest.TestCase):

    def test_skip_is_zero(self):
        self.assertEqual(compute_score("skip", 5, AGREE), 0.0)

    def test_kalshi_disagree_is_zero(self):
        """Disagree vetoes even a high-confidence, high-conviction market."""
        self.assertEqual(compute_score("high", 5, DISAGREE), 0.0)

    def test_confidence_is_monotonic(self):
        low = compute_score("low", 2, NO_MATCH)
        med = compute_score("medium", 2, NO_MATCH)
        high = compute_score("high", 2, NO_MATCH)
        self.assertLess(low, med)
        self.assertLess(med, high)

    def test_baseline_traders_get_no_bonus(self):
        """At the baseline trader count there is no conviction bonus."""
        base = config.WATCHLIST_CONFIDENCE_BASE["medium"]
        self.assertEqual(compute_score("medium", config.WATCHLIST_CONVICTION_BASELINE, NO_MATCH), base)

    def test_conviction_bonus_increases_with_traders(self):
        two = compute_score("medium", 2, NO_MATCH)
        three = compute_score("medium", 3, NO_MATCH)
        self.assertGreater(three, two)

    def test_conviction_bonus_capped(self):
        """Bonus saturates at the configured cap regardless of trader count."""
        base = config.WATCHLIST_CONFIDENCE_BASE["medium"]
        capped = base + config.WATCHLIST_CONVICTION_BONUS_CAP
        self.assertEqual(compute_score("medium", 99, NO_MATCH), capped)

    def test_kalshi_agree_adds_bonus(self):
        no_match = compute_score("medium", 2, NO_MATCH)
        agree = compute_score("medium", 2, AGREE)
        self.assertEqual(agree - no_match, config.WATCHLIST_KALSHI_AGREE_BONUS)

    def test_score_clamped_to_100(self):
        self.assertLessEqual(compute_score("high", 99, AGREE), 100.0)


# ── Integration: build_watchlist ────────────────────────────────────────────────

class TestBuildWatchlist(unittest.TestCase):

    def test_window_filter_excludes_out_of_range(self):
        candidates = [
            _candidate("too-soon", hours=config.WATCHLIST_MIN_RESOLUTION_HOURS - 1),
            _candidate("too-late", hours=config.WATCHLIST_MAX_RESOLUTION_HOURS + 1),
            _candidate("in-window", hours=72),
        ]
        with patch("intelligence.watchlist.assess_markets_parallel",
                   _fake_assessor({})):
            wl = build_watchlist(candidates)
        ids = {e.market_id for e in wl.entries}
        self.assertEqual(ids, {"in-window"})

    def test_window_bounds_are_inclusive(self):
        candidates = [
            _candidate("lo", hours=config.WATCHLIST_MIN_RESOLUTION_HOURS),
            _candidate("hi", hours=config.WATCHLIST_MAX_RESOLUTION_HOURS),
        ]
        with patch("intelligence.watchlist.assess_markets_parallel",
                   _fake_assessor({})):
            wl = build_watchlist(candidates)
        self.assertEqual({e.market_id for e in wl.entries}, {"lo", "hi"})

    def test_kalshi_disagree_dropped_before_assessment(self):
        """Disagree candidates are never sent to Claude and never appear."""
        candidates = [
            _candidate("keep", kalshi=AGREE),
            _candidate("drop", kalshi=DISAGREE),
        ]
        seen_ids = []

        def _spy(inputs):
            seen_ids.extend(i["market_id"] for i in inputs)
            return _fake_assessor({})(inputs)

        with patch("intelligence.watchlist.assess_markets_parallel", _spy):
            wl = build_watchlist(candidates)

        self.assertNotIn("drop", seen_ids)          # not assessed
        self.assertEqual({e.market_id for e in wl.entries}, {"keep"})

    def test_entries_sorted_by_score_descending(self):
        candidates = [
            _candidate("low-c", traders=2, kalshi=NO_MATCH),
            _candidate("high-c", traders=2, kalshi=NO_MATCH),
            _candidate("mid-c", traders=2, kalshi=NO_MATCH),
        ]
        confidences = {"low-c": "low", "high-c": "high", "mid-c": "medium"}
        with patch("intelligence.watchlist.assess_markets_parallel",
                   _fake_assessor(confidences)):
            wl = build_watchlist(candidates)
        ordered = [e.market_id for e in wl.entries]
        self.assertEqual(ordered, ["high-c", "mid-c", "low-c"])
        scores = [e.score for e in wl.entries]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_tradeable_filters_by_min_score(self):
        candidates = [
            _candidate("strong", traders=2, kalshi=AGREE),
            _candidate("weak", traders=2, kalshi=NO_MATCH),
        ]
        # low confidence (30) stays below the default min score of 50; high clears it
        confidences = {"strong": "high", "weak": "low"}
        with patch("intelligence.watchlist.assess_markets_parallel",
                   _fake_assessor(confidences)):
            wl = build_watchlist(candidates)
        tradeable_ids = {e.market_id for e in wl.tradeable()}
        self.assertEqual(tradeable_ids, {"strong"})

    def test_empty_candidates_returns_empty_watchlist(self):
        with patch("intelligence.watchlist.assess_markets_parallel",
                   _fake_assessor({})):
            wl = build_watchlist([])
        self.assertEqual(wl.entries, [])
        self.assertEqual(wl.tradeable(), [])

    def test_period_label_defaults_to_today(self):
        from datetime import date
        with patch("intelligence.watchlist.assess_markets_parallel",
                   _fake_assessor({})):
            wl = build_watchlist([])
        self.assertEqual(wl.period_label, date.today().isoformat())


if __name__ == "__main__":
    unittest.main(verbosity=2)
