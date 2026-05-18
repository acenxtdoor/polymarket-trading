---
tags:
---

# JARVIS Trading Dashboard

> *Last updated automatically by the bot.*

## Total P&L

```dataview
TABLE WITHOUT ID
  sum(rows.pnl) AS "Total P&L ($)",
  length(filter(rows.outcome, (o) => o = "won")) AS "Won",
  length(filter(rows.outcome, (o) => o = "lost")) AS "Lost",
  length(filter(rows.outcome, (o) => o = "open")) AS "Open"
FROM "Trades"
WHERE pnl != null OR outcome = "open"
GROUP BY true
```

## Active Positions

```dataview
TABLE market_title, entry_price, position_size, claude_confidence, traders
FROM "Trades"
WHERE outcome = "open"
SORT date DESC
```

## Recent Trades (30 days)

```dataview
TABLE market_title, date, action, conviction, kelly_fraction, entry_price, pnl, claude_confidence
FROM "Trades"
SORT date DESC
LIMIT 30
```

## Skipped Trades

```dataview
TABLE market_title, date, skip_reason, claude_confidence, conviction
FROM "Skipped"
SORT date DESC
LIMIT 20
```

## JARVIS Flags

```dataview
TABLE market_title, date, claude_flags
FROM "Trades" OR "Skipped"
WHERE claude_flags != []
SORT date DESC
```

## Daily Reports

```dataview
TABLE date, trades_placed, trades_skipped, bankroll
FROM "Daily Reports"
SORT date DESC
```
