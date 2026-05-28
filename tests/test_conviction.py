import sys
import os
import time

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from strategy.conviction import ConvictionEngine, SignalAggregator
from polymarket.traders import Trader, TradeSignal

PASS = "PASS"
FAIL = "FAIL"
results = []

def check(label, condition):
    status = PASS if condition else FAIL
    results.append((status, label))
    print(f"[{status}] {label}")

def check_val(label, got, expected):
    status = PASS if got == expected else FAIL
    results.append((status, label, got, expected))
    print(f"[{status}] {label}: got={got}, expected={expected}")

def run_tests():
    print("\n=== ConvictionEngine Tests ===\n")
    engine = ConvictionEngine()
    capital = 10000.0
    
    # 1 trader: execute=False
    res = engine.evaluate(500.0, 1, capital)
    check_val("1 trader: execute", res.execute, False)
    check_val("1 trader: size", res.size, 0.0)

    # 2 traders: 1.0x multiplier
    res = engine.evaluate(500.0, 2, capital)
    check_val("2 traders: execute", res.execute, True)
    check_val("2 traders: size", res.size, 500.0)
    check_val("2 traders: multiplier", res.multiplier, 1.0)

    # 3 traders: 1.25x multiplier
    res = engine.evaluate(500.0, 3, capital)
    check_val("3 traders: size", res.size, 625.0)
    check_val("3 traders: multiplier", res.multiplier, 1.25)

    # 4 traders: 1.5x multiplier
    res = engine.evaluate(500.0, 4, capital)
    check_val("4 traders: multiplier", res.multiplier, 1.5)

    # Capping (10% of 10000 = 1000)
    res = engine.evaluate(800.0, 4, capital) # 800 * 1.5 = 1200
    check_val("Capping: size", res.size, 1000.0)
    check_val("Capping: capped flag", res.capped, True)

    print("\n=== SignalAggregator Tests ===\n")
    agg = SignalAggregator()
    t1 = Trader("0x1", "t1", 10, 10)
    t2 = Trader("0x2", "t2", 10, 10)
    t3 = Trader("0x3", "t3", 10, 10)

    # Tick 1: market-A (t1) -> PENDING
    s1 = [TradeSignal("market-A", "token-A", "YES", 0.60, t1)]
    res1 = agg.process(s1, engine, 500.0, capital)
    check_val("Aggregator Tick 1: count", len(res1), 1)
    check_val("Aggregator Tick 1: market-A status", res1[0].conviction.execute, False)
    check_val("Aggregator Tick 1: pending count", agg.pending_count, 1)

    # Tick 2: market-A (t2) -> EXECUTE (promoted from pending)
    s2 = [TradeSignal("market-A", "token-A", "YES", 0.62, t2)]
    res2 = agg.process(s2, engine, 500.0, capital)
    decision_A = next(d for d in res2 if d.market_slug == "market-A")
    check_val("Aggregator Tick 2: market-A status", decision_A.conviction.execute, True)
    check_val("Aggregator Tick 2: pending count", agg.pending_count, 0)

    # Tick 3: same trader twice in one tick -> deduplicated -> PENDING
    s3 = [
        TradeSignal("market-B", "token-B", "YES", 0.50, t1),
        TradeSignal("market-B", "token-B", "YES", 0.51, t1)
    ]
    res3 = agg.process(s3, engine, 500.0, capital)
    check_val("Deduplication: trader count", res3[0].aggregated.trader_count, 1)
    check_val("Deduplication: status", res3[0].conviction.execute, False)

    # Tick 4: verify pending persistence when not in signals
    # We don't send anything, but market-B was pending.
    # process() returns decisions for everything in the pending queue too.
    res4 = agg.process([], engine, 500.0, capital)
    check_val("Persistence: pending decision emitted", any(d.market_slug == "market-B" for d in res4), True)

    print("\n=== Expiration Tests ===\n")
    # Manually inject a signal with an old timestamp into a fresh aggregator
    agg2 = SignalAggregator()
    old_ts = time.time() - 4000 # 4000 seconds ago (> 3600 default)
    old_signal = TradeSignal("old-market", "old-tok", "YES", 0.50, t1, raw={"timestamp": old_ts})
    agg2.process([old_signal], engine, 500.0, capital)
    check_val("Expiration pre-check: pending count", agg2.pending_count, 1)
    
    agg2.expire_pending(max_age_seconds=3600)
    check_val("Expiration post-check: pending count", agg2.pending_count, 0)

    print("\n=== Summary ===\n")
    passed = sum(1 for r in results if r[0] == PASS)
    failed = sum(1 for r in results if r[0] == FAIL)
    print(f"  {passed} passed, {failed} failed out of {len(results)} tests")
    if failed:
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
