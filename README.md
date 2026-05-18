# Polymarket Trading Bot

An automated trading bot that copy-trades the top 10 Polymarket traders, with conviction-scaled position sizing, Kalshi cross-referencing, Kelly Criterion risk management, market quality filtering, and slippage modeling.

---

## Requirements

- Python 3.10+
- A Polymarket account with a funded wallet
- USDC on Polygon (the currency the bot trades with)

---

## Setup

### 1. A Polymarket account + funded wallet

- Sign up at [polymarket.com](https://polymarket.com)
- Deposit **USDC on Polygon** (not Ethereum mainnet) — that's the currency the bot trades with

### 2. API credentials

Polymarket uses a **proxy wallet** system for bot trading. You'll need to generate API keys from your account:

- Go to your Polymarket profile → API keys
- This gives you: `API Key`, `API Secret`, `API Passphrase`, and your `private key`

Create a `.env` file in the project root with your credentials:

```
POLY_API_KEY=your_key
POLY_API_SECRET=your_secret
POLY_API_PASSPHRASE=your_passphrase
POLY_PRIVATE_KEY=your_wallet_private_key
POLY_WALLET_ADDRESS=your_wallet_address
```

> **Never share your `.env` file or private key with anyone.** The `.env` file is already in `.gitignore` so it will never be pushed to GitHub. Each collaborator should use their own wallet and credentials.

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the bot

```bash
python main.py
```

---

## Project structure

```
config.py               # All constants and API base URLs
main.py                 # Entry point, main loop
polymarket/
    api.py              # Polymarket API calls
    traders.py          # Top trader tracking and signal generation
strategy/
    market_filter.py    # Market quality checks and caching
    kelly.py            # Kelly Criterion position sizing
    conviction.py       # Conviction-scaled consensus logic
    trailing_stop.py    # TrailingStop class
    slippage.py         # Slippage estimation
kalshi/
    api.py              # Kalshi API calls and caching
execution/
    order.py            # Order placement
    portfolio.py        # Bankroll and position tracking
utils/
    cache.py            # Generic TTL cache
    logger.py           # Structured logging
tests/                  # Test cases
```

---

## How it works

1. Fetches the **top 10 Polymarket traders** by volume
2. Monitors their recent buys and aggregates **signals by conviction level** (how many traders agree)
3. **Cross-references each signal on Kalshi** to confirm or adjust the trade
4. Applies **Kelly Criterion** position sizing with conviction and Kalshi multipliers
5. Filters out **low-quality markets** (low volume, low liquidity, closing too soon or too far out)
6. Models **slippage** and skips trades where estimated fill cost is too high
7. Uses a **trailing stop** (10% below peak) to manage open positions

See [PLANNING.md](PLANNING.md) for full technical details.

---

## Running tests

```bash
python tests/test_conviction.py
```

```bash
python tests/test_kalshi.py
```

```bash
python tests/test_kelly.py
```

```bash
python tests/test_slippage.py
```
