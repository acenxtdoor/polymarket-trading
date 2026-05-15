# Polymarket Trading Bot — Planning Document

## Overview
An automated trading bot that copy-trades the top 10 Polymarket traders, with conviction-scaled position sizing, Kalshi cross-referencing, Kelly Criterion risk management, market quality filtering, and slippage modeling.

---

## Components

### 1. Copytrading Engine
- Identify and track the **top 10 Polymarket traders** by volume (volume > $0)
- Monitor their trades in real time via the Polymarket API
- Feed signals into the conviction system before executing

### 2. Conviction-Scaled Consensus
Scale position size based on how many tracked traders signal the same trade:
| Traders Signaling | Action |
|---|---|
| 1 | Add to pending, wait |
| 2 | Execute at base Kelly size |
| 3 | Execute at 1.25x Kelly size |
| 4+ | Execute at 1.5x Kelly size |
- All sizes capped at **10% of bankroll**
- Log conviction level on every executed trade

### 3. Kalshi Cross-Referencing
- For each Polymarket signal, search Kalshi for a matching event using the market title keywords
- API: `https://trading-api.kalshi.com/trade-api/v2/markets`
- Compare `kalshi_prob` (Kalshi YES price) vs `poly_prob` (Polymarket YES price):

| Scenario | Action |
|---|---|
| Both agree (within 5%) | Strong signal — proceed with full Kelly bet |
| Kalshi higher by >5% | Very strong signal — use 1.5x Kelly (capped at 10%) |
| Kalshi lower by >5% | Weak signal — skip the trade |
| No Kalshi match found | Proceed normally without adjustment |

- Cache Kalshi lookups per market slug for **10 minutes**

### 4. Kelly Criterion Position Sizing
- Formula: `f* = p - (q / b)`
  - `p` = probability of winning
  - `q` = probability of losing (1 - p)
  - `b` = odds received on the bet
- Apply conviction and Kalshi multipliers on top of base Kelly size
- Hard cap: **10% of bankroll** per position

### 5. Trailing Stop (TrailingStop class)
- Tracks the **highest price seen** for each position (keyed by `pk`)
- Trail level = peak price - 10%
- On each `check_take_profits()` cycle:
  - Update peak if current price is higher
  - Check if current price has dropped below trail level
  - Fire trail stop if breached; fire take profit if target reached
- Log clearly: trail stop fired vs take profit fired
- Clean up peak tracking dict when a position closes

### 6. Market Quality Filters
Skip any market that fails these checks:
- Volume < $10,000
- Liquidity < $1,000
- Closes within 24 hours (too close to resolution)
- Closes more than 30 days from now (price won't move)
- Market is already closed
- Cache market metadata per slug for **5 minutes**

### 7. Slippage Modeling
- Estimate fill price: `estimated_fill = trader_price + (BUY_AMOUNT / market_liquidity) * 0.5`
- If `estimated_fill` exceeds `trader_price` by more than **3%**: skip trade, log as `SKIP (slippage too high)`
- Use cached market metadata for liquidity value

---

## Architecture

```
main.py                  # entry point, main loop
polymarket/
    api.py               # Polymarket API calls
    traders.py           # top trader tracking & signal generation
kalshi/
    api.py               # Kalshi API calls & caching
strategy/
    kelly.py             # Kelly Criterion sizing
    conviction.py        # conviction-scaled consensus logic
    trailing_stop.py     # TrailingStop class
    slippage.py          # slippage estimation
    market_filter.py     # market quality checks
execution/
    order.py             # order placement
    portfolio.py         # bankroll & position tracking
utils/
    cache.py             # generic TTL cache
    logger.py            # structured logging
config.py                # API keys, constants, caps
```

---

## Key Constants
| Constant | Value |
|---|---|
| Max position size | 10% of bankroll |
| Trailing stop distance | 10% below peak |
| Kalshi agreement threshold | 5% |
| Max slippage | 3% |
| Min market volume | $10,000 |
| Min market liquidity | $1,000 |
| Market window | 24 hours – 30 days |
| Kalshi cache TTL | 10 minutes |
| Market metadata cache TTL | 5 minutes |

---

## Build Order
1. Polymarket API client + top trader fetching
2. Market quality filter + caching layer
3. Kelly Criterion sizing
4. Conviction-scaled consensus
5. Kalshi cross-referencing
6. Slippage modeling
7. TrailingStop class + take profit logic
8. Order execution + portfolio tracking
9. Logging throughout
10. Main loop tying everything together
