# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

An automated Polymarket copy-trading bot. It copies the top 10 traders on Polymarket, aggregates their signals by conviction, cross-references with Kalshi, sizes positions via Kelly Criterion, runs a pre-trade AI analysis (JARVIS), and logs everything to an Obsidian vault.

**Note: `main.py` does not exist yet.** The entry point is planned but unimplemented. The strategy, intelligence, Obsidian, and execution layers are complete. The `execution/` module is paper-only: `DRY_RUN=True` (config.py) means no real orders are ever placed — live CLOB order signing is left as a stub seam in `execution/order.py`.

## Commands

Install dependencies:
```bash
pip install -r requirements.txt
```

Run all tests:
```bash
cd polymarket-trading && python -m pytest tests/
```

Run a single test file:
```bash
python -m pytest tests/test_kelly.py
```

Run a module standalone (some have `if __name__ == "__main__"` smoke tests):
```bash
python strategy/conviction.py
```

## Architecture

### Data Flow

```
polymarket/api.py (get_leaderboard)
  → polymarket/traders.py (fetch_top_traders, collect_all_signals)
  → strategy/conviction.py (SignalAggregator → ConvictionEngine)
  → strategy/kalshi.py (get_kalshi_signal)
  → strategy/kelly.py (position_size)
  → strategy/market_filter.py (MarketFilter.is_tradeable)
  → strategy/slippage.py (estimate_slippage)
  → intelligence/analyst.py (JARVIS: assess_market via Claude API)
  → [execution — not yet built]
  → obsidian/writer.py (write_trade / write_skip / write_daily_report)
```

### Key Modules

**`polymarket/`**
- `api.py` — three Polymarket endpoints: CLOB (trades), Gamma (market metadata), Data (leaderboard). All calls go through a single `_get()` helper.
- `traders.py` — `Trader` and `TradeSignal` dataclasses; `collect_all_signals()` fetches buy-side trades from all top traders.

**`strategy/`**
- `conviction.py` — `SignalAggregator` groups signals by `(market_slug, outcome)`, maintains a pending queue for single-trader signals, and promotes to execution once ≥2 traders agree. `ConvictionEngine.evaluate()` applies the multiplier tier (1.0x / 1.25x / 1.5x) and bankroll cap.
- `kalshi.py` — fuzzy-matches market titles against Kalshi API using `difflib.SequenceMatcher` (threshold: 0.40). Returns `agree` (1.5x), `disagree` (0.0x — skip), or `no_match` (1.0x). Results cached 10 minutes per query.
- `kelly.py` — `kelly_fraction(p, price)` computes raw Kelly; `position_size()` applies conviction and Kalshi multipliers, caps at 10% of bankroll.
- `market_filter.py` — `MarketFilter.is_tradeable()` rejects markets below volume/liquidity thresholds or outside the 24h–30d window. Results cached 5 minutes per slug.
- `slippage.py` — linear model: `fill = price + (amount / liquidity) * 0.5`. Skips trade if slippage > 3%. Pulls liquidity from `MarketFilter`'s cache via `get_market_metadata()`.

**`intelligence/`**
- `analyst.py` — calls `claude-opus-4-7` with adaptive thinking and ephemeral prompt caching. Returns a `MarketAssessment` with `confidence` (`high/medium/low/skip`) and `flags`. `assess_markets_parallel()` fans out across up to 4 workers. Degrades gracefully if `ANTHROPIC_API_KEY` is unset.
- `summarizer.py` — streams a daily JARVIS briefing from `claude-opus-4-7` at end of run.

**`execution/`**
- `portfolio.py` — `Portfolio` tracks bankroll, open `Position`s (keyed by `market_slug`), and realized P&L, persisting to a JSON file (`PORTFOLIO_PATH`) on every mutation. `record_buy`/`record_sell` handle prediction-market share math (`shares = dollars / price`).
- `order.py` — `OrderExecutor.buy()/sell()` place paper orders. `buy()` fills at the slippage-adjusted price from `estimate_slippage` and records the position; rejects on insufficient bankroll, unsafe slippage, or uncached market. `DRY_RUN=False` routes to `_live_buy/_live_sell`, which raise `NotImplementedError` (the live CLOB seam). Returns a frozen `OrderResult`.

**`obsidian/`**
- `writer.py` — writes Markdown notes to `OBSIDIAN_VAULT_PATH` (env var). Folders: `Trades/`, `Skipped/`, `Daily Reports/`, `Markets/`.
- `templates.py` — YAML-frontmatter note templates for Obsidian Dataview queries.

**`utils/`**
- `cache.py` — simple in-memory `TTLCache(ttl=seconds)` used throughout for API responses.
- `logger.py` — `get_logger(name)` returns a UTF-8 stdout logger; all modules use this.

### Required Environment Variables

```
POLY_API_KEY
POLY_API_SECRET
POLY_API_PASSPHRASE
POLY_PRIVATE_KEY
POLY_WALLET_ADDRESS
ANTHROPIC_API_KEY      # optional; JARVIS degrades gracefully without it
NEWSAPI_KEY            # optional; news context for JARVIS
OBSIDIAN_VAULT_PATH    # path to Obsidian vault root
```

## Key Constants (all in `config.py`)

| Constant | Value | Purpose |
|---|---|---|
| `MAX_POSITION_PCT` | 0.10 | Hard bankroll cap per position |
| `TRAILING_STOP_PCT` | 0.10 | 10% below peak (TrailingStop, not yet wired) |
| `KALSHI_AGREEMENT_THRESHOLD` | 0.05 | Max prob diff for AGREE signal |
| `KALSHI_MATCH_THRESHOLD` | 0.40 | Min title similarity to consider a Kalshi match |
| `MAX_SLIPPAGE_PCT` | 0.03 | Skip if estimated fill exceeds 3% |
| `MIN_MARKET_VOLUME` | $10,000 | Market quality filter |
| `MIN_MARKET_LIQUIDITY` | $1,000 | Market quality filter |
| `INTELLIGENCE_WORKERS` | 4 | Parallel Claude API calls |

## Coding Conventions

- All modules use `from utils.logger import get_logger` — never configure logging directly.
- Modules that may be run standalone insert their package root into `sys.path` at the top.
- `TTLCache` is instantiated at module level (not per-call) so the cache persists across the loop.
- `SlippageResult`, `KalshiResult`, `ConvictionResult`, `MarketAssessment` are frozen/plain dataclasses — keep them that way.
- The Kalshi multiplier for DISAGREE is `0.0` — multiplying Kelly size by 0 is intentional (skip the trade entirely).
