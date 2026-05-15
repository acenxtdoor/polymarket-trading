from dataclasses import dataclass, field
from polymarket.api import get_leaderboard, get_trader_trades
from utils.logger import get_logger
from config import TOP_TRADER_COUNT, MIN_TRADER_VOLUME

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
        volume = float(row.get("volume", 0) or 0)
        if volume <= MIN_TRADER_VOLUME:
            continue
        traders.append(Trader(
            address=row.get("address", row.get("wallet", "")),
            username=row.get("name", row.get("username", "unknown")),
            volume=volume,
            pnl=float(row.get("pnl", 0) or 0),
        ))

    traders.sort(key=lambda t: t.volume, reverse=True)
    top = traders[:TOP_TRADER_COUNT]
    logger.info(f"Fetched {len(top)} top traders")
    for t in top:
        short_addr = t.address[:8] if t.address else "unknown"
        logger.info(f"  {t.username} ({short_addr}...) — volume: ${t.volume:,.0f}, pnl: ${t.pnl:,.0f}")
    return top


def get_trader_signals(trader: Trader) -> list[TradeSignal]:
    """Return recent buy signals from a single trader's trade history."""
    trades = get_trader_trades(trader.address)
    signals = []
    for trade in trades:
        side = trade.get("side", "").upper()
        if side != "BUY":
            continue
        slug = trade.get("market", trade.get("slug", ""))
        token_id = trade.get("asset_id", trade.get("token_id", ""))
        outcome = trade.get("outcome", "YES")
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
    return signals


def collect_all_signals(traders: list[Trader]) -> list[TradeSignal]:
    """Gather buy signals from all tracked traders."""
    all_signals = []
    for trader in traders:
        signals = get_trader_signals(trader)
        logger.info(f"{trader.username}: {len(signals)} buy signal(s)")
        all_signals.extend(signals)
    return all_signals
