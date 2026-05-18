"""
Tests for strategy/kalshi.py — Kalshi cross-reference module.

All HTTP calls are mocked; no real network requests are made.
"""

import sys
import os
import time
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from strategy.kalshi import (
    get_kalshi_signal,
    _implied_prob,
    _best_match,
    _cache,
    AGREE,
    DISAGREE,
    NO_MATCH,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_market(title: str, yes_bid: float, yes_ask: float) -> dict:
    """Build a minimal Kalshi market dict (prices in 0–100 cent scale)."""
    return {
        "title": title,
        "yes_bid": yes_bid * 100,
        "yes_ask": yes_ask * 100,
        "status": "open",
    }


def _mock_get(markets: list[dict]):
    """Return a mock requests.get that yields the given markets list."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"markets": markets}
    mock = MagicMock(return_value=mock_resp)
    return mock


# ── Unit: _implied_prob ───────────────────────────────────────────────────────

class TestImpliedProb(unittest.TestCase):

    def test_cent_scale_normalised(self):
        """Cent-scale prices (0–100) are divided by 100."""
        market = {"yes_bid": 58.0, "yes_ask": 62.0}
        self.assertAlmostEqual(_implied_prob(market), 0.60, places=4)

    def test_decimal_scale_unchanged(self):
        """Decimal-scale prices (0–1) are kept as-is."""
        market = {"yes_bid": 0.58, "yes_ask": 0.62}
        self.assertAlmostEqual(_implied_prob(market), 0.60, places=4)

    def test_missing_bid_returns_none(self):
        self.assertIsNone(_implied_prob({"yes_ask": 60.0}))

    def test_missing_ask_returns_none(self):
        self.assertIsNone(_implied_prob({"yes_bid": 60.0}))

    def test_non_numeric_returns_none(self):
        self.assertIsNone(_implied_prob({"yes_bid": "n/a", "yes_ask": 60.0}))

    def test_empty_dict_returns_none(self):
        self.assertIsNone(_implied_prob({}))


# ── Unit: _best_match ─────────────────────────────────────────────────────────

class TestBestMatch(unittest.TestCase):

    def test_exact_match_scores_1(self):
        title = "Will the Fed cut rates in June 2026?"
        markets = [_make_market(title, 0.58, 0.62)]
        best, score = _best_match(title, markets)
        self.assertEqual(best["title"], title)
        self.assertAlmostEqual(score, 1.0, places=2)

    def test_picks_closest_of_multiple(self):
        markets = [
            _make_market("Will Bitcoin reach $100k?", 0.40, 0.44),
            _make_market("Will the Fed cut rates in June 2026?", 0.58, 0.62),
        ]
        best, score = _best_match("Will the Fed cut rates in June 2026?", markets)
        self.assertEqual(best["title"], "Will the Fed cut rates in June 2026?")

    def test_empty_markets_returns_none(self):
        best, score = _best_match("anything", [])
        self.assertIsNone(best)
        self.assertEqual(score, 0.0)

    def test_case_insensitive(self):
        markets = [_make_market("Will The Fed Cut Rates In June 2026?", 0.58, 0.62)]
        _, score = _best_match("will the fed cut rates in june 2026?", markets)
        self.assertAlmostEqual(score, 1.0, places=2)


# ── Integration: get_kalshi_signal ────────────────────────────────────────────

class TestGetKalshiSignal(unittest.TestCase):

    def setUp(self):
        # Clear TTL cache before each test
        _cache._store.clear()

    def test_agree_within_threshold(self):
        """Kalshi prob within 5% of Polymarket → AGREE, 1.5x."""
        markets = [_make_market("Will the Fed cut rates in June 2026?", 0.60, 0.64)]
        with patch("strategy.kalshi.requests.get", _mock_get(markets)):
            result = get_kalshi_signal(
                "Will the Fed cut rates in June 2026?",
                polymarket_price=0.61,
            )
        self.assertEqual(result.signal, AGREE)
        self.assertAlmostEqual(result.multiplier, config.KALSHI_AGREE_MULTIPLIER)
        self.assertIsNotNone(result.kalshi_prob)

    def test_disagree_outside_threshold(self):
        """Kalshi prob >5% away from Polymarket → DISAGREE, 0x."""
        markets = [_make_market("Will the Fed cut rates in June 2026?", 0.30, 0.34)]
        with patch("strategy.kalshi.requests.get", _mock_get(markets)):
            result = get_kalshi_signal(
                "Will the Fed cut rates in June 2026?",
                polymarket_price=0.61,
            )
        self.assertEqual(result.signal, DISAGREE)
        self.assertAlmostEqual(result.multiplier, config.KALSHI_DISAGREE_MULTIPLIER)

    def test_no_match_empty_response(self):
        """Empty Kalshi response → NO_MATCH, 1.0x."""
        with patch("strategy.kalshi.requests.get", _mock_get([])):
            result = get_kalshi_signal("Some Polymarket title", 0.55)
        self.assertEqual(result.signal, NO_MATCH)
        self.assertAlmostEqual(result.multiplier, config.KALSHI_NO_MATCH_MULTIPLIER)

    def test_no_match_low_similarity(self):
        """Best match below similarity threshold → NO_MATCH."""
        markets = [_make_market("Completely unrelated market about sports", 0.50, 0.54)]
        with patch("strategy.kalshi.requests.get", _mock_get(markets)):
            result = get_kalshi_signal(
                "Will the Federal Reserve raise interest rates in Q4?",
                polymarket_price=0.60,
            )
        self.assertEqual(result.signal, NO_MATCH)

    def test_api_error_returns_no_match(self):
        """Network/API error → graceful NO_MATCH, no exception raised."""
        with patch("strategy.kalshi.requests.get", side_effect=Exception("timeout")):
            result = get_kalshi_signal("Will anything happen?", 0.50)
        self.assertEqual(result.signal, NO_MATCH)
        self.assertAlmostEqual(result.multiplier, config.KALSHI_NO_MATCH_MULTIPLIER)

    def test_missing_price_data_returns_no_match(self):
        """Market matched but missing yes_bid/yes_ask → NO_MATCH."""
        markets = [{"title": "Will the Fed cut rates in June 2026?", "status": "open"}]
        with patch("strategy.kalshi.requests.get", _mock_get(markets)):
            result = get_kalshi_signal(
                "Will the Fed cut rates in June 2026?",
                polymarket_price=0.61,
            )
        self.assertEqual(result.signal, NO_MATCH)

    def test_ttl_cache_used_on_second_call(self):
        """Second call with same title uses cache — requests.get called only once."""
        markets = [_make_market("Will the Fed cut rates in June 2026?", 0.60, 0.64)]
        mock_get = _mock_get(markets)
        with patch("strategy.kalshi.requests.get", mock_get):
            get_kalshi_signal("Will the Fed cut rates in June 2026?", 0.61)
            get_kalshi_signal("Will the Fed cut rates in June 2026?", 0.61)
        self.assertEqual(mock_get.call_count, 1)

    def test_cache_expires_after_ttl(self):
        """After TTL expires the cache misses and a fresh request is made."""
        markets = [_make_market("Will the Fed cut rates in June 2026?", 0.60, 0.64)]
        mock_get = _mock_get(markets)
        with patch("strategy.kalshi.requests.get", mock_get):
            get_kalshi_signal("Will the Fed cut rates in June 2026?", 0.61)
            # Manually expire the cache entry
            for key in _cache._store:
                _cache._store[key]["ts"] -= config.KALSHI_CACHE_TTL + 1
            get_kalshi_signal("Will the Fed cut rates in June 2026?", 0.61)
        self.assertEqual(mock_get.call_count, 2)

    def test_exactly_at_threshold_is_agree(self):
        """Difference exactly equal to KALSHI_AGREEMENT_THRESHOLD → AGREE (≤, not <)."""
        threshold = config.KALSHI_AGREEMENT_THRESHOLD
        polymarket_price = 0.60
        kalshi_mid = polymarket_price + threshold  # exactly at boundary
        # yes_bid and yes_ask straddle the mid (in cent scale)
        bid = (kalshi_mid - 0.01) * 100
        ask = (kalshi_mid + 0.01) * 100
        markets = [_make_market("Will the Fed cut rates in June 2026?", bid / 100, ask / 100)]
        with patch("strategy.kalshi.requests.get", _mock_get(markets)):
            result = get_kalshi_signal(
                "Will the Fed cut rates in June 2026?",
                polymarket_price=polymarket_price,
            )
        self.assertEqual(result.signal, AGREE)

    def test_multiplier_integrates_with_kelly(self):
        """AGREE multiplier correctly scales Kelly position size."""
        from strategy.kelly import position_size

        markets = [_make_market("Will the Fed cut rates in June 2026?", 0.60, 0.64)]
        with patch("strategy.kalshi.requests.get", _mock_get(markets)):
            result = get_kalshi_signal(
                "Will the Fed cut rates in June 2026?",
                polymarket_price=0.61,
            )

        size_no_kalshi = position_size(10_000, p=0.70, price=0.61, conviction_multiplier=1.0, kalshi_multiplier=1.0)
        size_with_kalshi = position_size(10_000, p=0.70, price=0.61, conviction_multiplier=1.0, kalshi_multiplier=result.multiplier)
        # AGREE → 1.5x, so kalshi-boosted size should be larger (up to the 10% cap)
        self.assertGreaterEqual(size_with_kalshi, size_no_kalshi)


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases(unittest.TestCase):

    def setUp(self):
        _cache._store.clear()

    def test_extreme_polymarket_price_high(self):
        """Very high Polymarket price (0.95) still processes without error."""
        markets = [_make_market("High confidence market", 0.93, 0.97)]
        with patch("strategy.kalshi.requests.get", _mock_get(markets)):
            result = get_kalshi_signal("High confidence market", 0.95)
        self.assertIn(result.signal, [AGREE, DISAGREE, NO_MATCH])

    def test_extreme_polymarket_price_low(self):
        """Very low Polymarket price (0.05) still processes without error."""
        markets = [_make_market("Low confidence market", 0.03, 0.07)]
        with patch("strategy.kalshi.requests.get", _mock_get(markets)):
            result = get_kalshi_signal("Low confidence market", 0.05)
        self.assertIn(result.signal, [AGREE, DISAGREE, NO_MATCH])

    def test_multiple_markets_picks_best(self):
        """With multiple Kalshi markets, the most similar title wins."""
        markets = [
            _make_market("Will Bitcoin hit $50k?", 0.20, 0.24),
            _make_market("Will the Fed cut rates in June 2026?", 0.60, 0.64),
            _make_market("Will inflation drop below 3%?", 0.45, 0.49),
        ]
        with patch("strategy.kalshi.requests.get", _mock_get(markets)):
            result = get_kalshi_signal(
                "Will the Fed cut rates in June 2026?",
                polymarket_price=0.61,
            )
        self.assertEqual(result.signal, AGREE)
        self.assertIn("Fed", result.kalshi_title)


# ── Bug-fix regression tests ──────────────────────────────────────────────────

class TestImpliedProbBugFixes(unittest.TestCase):
    """Regression tests for the three bugs found during code review."""

    def test_near_zero_cent_scale_bid_zero(self):
        """Bug fix: yes_bid=0, yes_ask=2 (cent scale) must return 0.01, not 1.0."""
        market = {"yes_bid": 0, "yes_ask": 2}
        result = _implied_prob(market)
        self.assertAlmostEqual(result, 0.01, places=4)

    def test_near_zero_cent_scale_both_low(self):
        """Bug fix: yes_bid=1, yes_ask=3 (cent scale) must return 0.02, not 0.02 direct."""
        market = {"yes_bid": 1, "yes_ask": 3}
        result = _implied_prob(market)
        self.assertAlmostEqual(result, 0.02, places=4)

    def test_scale_detection_uses_raw_values_not_mid(self):
        """Bug fix: detection is based on raw bid/ask, not the mid-price."""
        # yes_bid=0, yes_ask=2 → mid=1.0 which incorrectly passed the old > 1 check
        # New logic: ask=2 > 1, so correctly treats as cent scale
        market = {"yes_bid": 0, "yes_ask": 2}
        self.assertAlmostEqual(_implied_prob(market), 0.01, places=4)

    def test_prob_clamped_to_zero_on_zero_bid_ask(self):
        """Prob is clamped to [0, 1] — zero bid/ask yields 0.0."""
        market = {"yes_bid": 0, "yes_ask": 0}
        result = _implied_prob(market)
        self.assertEqual(result, 0.0)


class TestWordFilterBugFix(unittest.TestCase):
    """Regression tests for the 2-char word filter bug."""

    def setUp(self):
        _cache._store.clear()

    def test_two_char_words_included_in_query(self):
        """Bug fix: 'US', 'UK', 'AI', 'EU' must not be stripped from the search query."""
        markets = [_make_market("Will the US raise interest rates?", 0.58, 0.62)]
        mock_get = _mock_get(markets)
        with patch("strategy.kalshi.requests.get", mock_get):
            get_kalshi_signal("Will the US raise interest rates?", 0.60)
        call_params = mock_get.call_args
        query_used = call_params[1]["params"]["search"]
        self.assertIn("US", query_used)

    def test_single_char_words_still_excluded(self):
        """Single-char words (e.g. 'a', 'I') are still filtered."""
        markets = [_make_market("Will a rate cut happen?", 0.58, 0.62)]
        mock_get = _mock_get(markets)
        with patch("strategy.kalshi.requests.get", mock_get):
            get_kalshi_signal("Will a rate cut happen?", 0.60)
        query_used = mock_get.call_args[1]["params"]["search"]
        # 'a' should not appear as a standalone word in the query
        self.assertNotIn(" a ", f" {query_used} ")


class TestPolymarketPriceValidation(unittest.TestCase):
    """Regression tests for invalid polymarket_price guard."""

    def setUp(self):
        _cache._store.clear()

    def test_price_zero_returns_no_match(self):
        """polymarket_price=0 is invalid — must return NO_MATCH without API call."""
        mock_get = _mock_get([])
        with patch("strategy.kalshi.requests.get", mock_get):
            result = get_kalshi_signal("Some market", 0.0)
        self.assertEqual(result.signal, NO_MATCH)
        mock_get.assert_not_called()

    def test_price_one_returns_no_match(self):
        """polymarket_price=1 is invalid — must return NO_MATCH without API call."""
        mock_get = _mock_get([])
        with patch("strategy.kalshi.requests.get", mock_get):
            result = get_kalshi_signal("Some market", 1.0)
        self.assertEqual(result.signal, NO_MATCH)
        mock_get.assert_not_called()

    def test_price_negative_returns_no_match(self):
        """Negative polymarket_price is invalid — must return NO_MATCH."""
        mock_get = _mock_get([])
        with patch("strategy.kalshi.requests.get", mock_get):
            result = get_kalshi_signal("Some market", -0.1)
        self.assertEqual(result.signal, NO_MATCH)
        mock_get.assert_not_called()

    def test_price_above_one_returns_no_match(self):
        """polymarket_price > 1 is invalid — must return NO_MATCH."""
        mock_get = _mock_get([])
        with patch("strategy.kalshi.requests.get", mock_get):
            result = get_kalshi_signal("Some market", 1.5)
        self.assertEqual(result.signal, NO_MATCH)
        mock_get.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
