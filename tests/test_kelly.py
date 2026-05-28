import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategy.kelly import kelly_fraction, position_size

PASS = "PASS"
FAIL = "FAIL"
results = []

def check(label, got, expected, tol=0.0001):
    ok = abs(got - expected) <= tol
    status = PASS if ok else FAIL
    results.append((status, label, got, expected))
    print(f"[{status}] {label}: got={got:.6f}, expected={expected:.6f}")

def check_zero(label, got):
    ok = got == 0.0
    status = PASS if ok else FAIL
    results.append((status, label, got, 0.0))
    print(f"[{status}] {label}: got={got:.6f}, expected=0.0")

print("\n=== kelly_fraction tests ===\n")

# p=price => no edge => f*=0
check_zero("No edge (p == price = 0.5)", kelly_fraction(0.5, 0.5))

# p > price => positive edge
# f* = p - (1-p)*price/(1-price)
# p=0.7, price=0.5: b=1.0, q=0.3, f=0.7-0.3=0.4
check("Clear edge (p=0.7, price=0.5)", kelly_fraction(0.7, 0.5), 0.4)

# p=0.6, price=0.5: b=1.0, q=0.4, f=0.6-0.4=0.2
check("Mild edge (p=0.6, price=0.5)", kelly_fraction(0.6, 0.5), 0.2)

# p < price => negative => floored at 0
check_zero("No edge (p < price): p=0.3, price=0.5", kelly_fraction(0.3, 0.5))

# p=0.8, price=0.6: b=(0.4/0.6)=0.6667, q=0.2, f=0.8-(0.2/0.6667)=0.8-0.3=0.5
check("Strong edge (p=0.8, price=0.6)", kelly_fraction(0.8, 0.6), 0.5)

# Invalid inputs — should return 0.0
check_zero("Invalid: p=0",   kelly_fraction(0.0, 0.5))
check_zero("Invalid: p=1",   kelly_fraction(1.0, 0.5))
check_zero("Invalid: price=0", kelly_fraction(0.5, 0.0))
check_zero("Invalid: price=1", kelly_fraction(0.5, 1.0))
check_zero("Invalid: p negative", kelly_fraction(-0.1, 0.5))

print("\n=== position_size tests ===\n")

# No edge => $0
check_zero("No edge => $0", position_size(10000, 0.5, 0.5))

# Basic: p=0.7, price=0.5, f*=0.4, capital=10000, no multipliers => 0.4 capped at 0.10 => $1000
check("Capped at 10% (f*=0.4)", position_size(10000, 0.7, 0.5), 1000.0)

# Mild edge, no multipliers: p=0.6, price=0.5, f*=0.2 => 0.2 capped at 0.10 => $1000
check("Capped at 10% (f*=0.2)", position_size(10000, 0.6, 0.5), 1000.0)

# Small edge under cap: p=0.52, price=0.5
# b=1.0, q=0.48, f=0.52-0.48=0.04 => 4% of 10000 = $400
check("Under cap (f*=0.04)", position_size(10000, 0.52, 0.5), 400.0)

# Conviction multiplier: f*=0.04, conviction=1.25 => 0.05 => $500
check("Conviction 1.25x (0.04->0.05)", position_size(10000, 0.52, 0.5, conviction_multiplier=1.25), 500.0)

# Conviction + Kalshi: f*=0.04, conviction=1.25, kalshi=1.5 => 0.075 => $750
check("Conviction+Kalshi (0.04->0.075)", position_size(10000, 0.52, 0.5, conviction_multiplier=1.25, kalshi_multiplier=1.5), 750.0)

# Multipliers push past 10% cap: f*=0.04, conviction=1.5, kalshi=1.5 => 0.09 => $900
check("Multipliers near cap (0.04->0.09)", position_size(10000, 0.52, 0.5, conviction_multiplier=1.5, kalshi_multiplier=1.5), 900.0)

# Multipliers exceed cap: f*=0.2, conviction=1.5, kalshi=1.5 => 0.45 capped at 0.10 => $1000
check("Multipliers exceed cap => capped at $1000", position_size(10000, 0.6, 0.5, conviction_multiplier=1.5, kalshi_multiplier=1.5), 1000.0)

# Invalid capital
check_zero("Invalid capital=0", position_size(0, 0.6, 0.5))
check_zero("Invalid capital=-100", position_size(-100, 0.6, 0.5))

print("\n=== Summary ===\n")
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
print(f"  {passed} passed, {failed} failed out of {len(results)} tests")
if failed:
    print("\nFailed tests:")
    for r in results:
        if r[0] == FAIL:
            print(f"  {r[1]}: got={r[2]:.6f}, expected={r[3]:.6f}")
