from __future__ import annotations

import time
from dataclasses import dataclass, field
from polymarket.api import get_leaderboard, get_trader_trades
from utils.logger import get_logger
from config import TOP_TRADER_COUNT, MIN_TRADER_VOLUME

# Only consider trader buys made within this window.
# Stale signals (e.g. a buy from 3 days ago) should not drive new entries —
# the trader may have already exited, the market may have moved, or the position
# may have already been traded and closed by this bot in a previous session.
MAX_SIGNAL_AGE_HOURS = 48

logger = get_logger(__name__)


@dataclass
class Trader:
    address: str
    username: str
    volume: float
    pnl: float


@dataclass
class TradeSignal:
    market_slug: str
    token_id: str
    outcome: str       # "YES" or "NO"
    price: float
    trader: Trader
    raw: dict = field(default_factory=dict)


def fetch_top_traders() -> list[Trader]:
    """Return the top N traders by volume with volume > MIN_TRADER_VOLUME."""
    rows = get_leaderboard(limit=100)
    traders = []
    for row in rows:
        # v1/leaderboard uses: proxyWallet, userName, vol, pnl
        volume = float(row.get("vol") or row.get("volume") or 0)
        if volume <= MIN_TRADER_VOLUME:
            continue
        traders.append(Trader(
            address=row.get("proxyWallet") or row.get("address") or row.get("wallet") or "",
            username=row.get("userName") or row.get("name") or row.get("username") or "unknown",
            volume=volume,
            pnl=float(row.get("pnl", 0) or 0),
        ))

    traders.sort(key=lambda t: t.volume, reverse=True)
    top = traders[:TOP_TRADER_COUNT]
    logger.info(f"Fetched {len(top)} top traders")
    for t in top:
        short_addr = t.address[:8] if t.address else "unknown"
        logger.info(f"  {t.username} ({short_addr}...) - volume: ${t.volume:,.0f}, pnl: ${t.pnl:,.0f}")
    return top


def get_trader_signals(trader: Trader) -> list[TradeSignal]:
    """Return recent buy signals from a single trader's trade history.

    Signals older than MAX_SIGNAL_AGE_HOURS are discarded.  Without this filter
    the last-50-trades window can include buys from days ago, causing the bot to
    re-enter markets it already traded (and possibly closed) in a prior session.
    """
    trades = get_trader_trades(trader.address)
    signals = []
    cutoff = time.time() - MAX_SIGNAL_AGE_HOURS * 3600
    stale_count = 0

    for trade in trades:
        side = (trade.get("side") or "").upper()
        if side != "BUY":
            continue

        # Drop signals that are too old.
        ts_raw = trade.get("timestamp") or trade.get("created_at")
        if ts_raw is not None:
            try:
                if float(ts_raw) < cutoff:
                    stale_count += 1
                    continue
            except (TypeError, ValueError):
                pass  # no parseable timestamp — allow it through

        slug = trade.get("slug") or trade.get("market") or ""
        token_id = trade.get("asset") or trade.get("asset_id") or trade.get("token_id") or ""
        outcome = trade.get("outcome") or "YES"
        price = float(trade.get("price", 0) or 0)
        if not slug or not token_id or price <= 0:
            continue
        signals.append(TradeSignal(
            market_slug=slug,
            token_id=token_id,
            outcome=outcome,
            price=price,
            trader=trader,
            raw=trade,
        ))

    if stale_count:
        logger.debug(f"{trader.username}: dropped {stale_count} stale signal(s) (>{MAX_SIGNAL_AGE_HOURS}h old)")
    return signals


def collect_all_signals(traders: list[Trader]) -> list[TradeSignal]:
    """Gather buy signals from all tracked traders."""
    all_signals = []
    for trader in traders:
        signals = get_trader_signals(trader)
        logger.info(f"{trader.username}: {len(signals)} buy signal(s)")
        all_signals.extend(signals)
    return all_signals
