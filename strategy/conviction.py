from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from config import MAX_POSITION_PCT
from polymarket.traders import TradeSignal
from utils.logger import get_logger

logger = get_logger(__name__)

_EXECUTE_THRESHOLD = 2

_MULTIPLIERS: dict[int, float] = {
    2: 1.0,
    3: 1.25,
}
_MULTIPLIER_MAX = 1.5


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConvictionResult:
    """Sizing decision returned by ConvictionEngine.evaluate()."""
    execute: bool
    size: float
    multiplier: float
    trader_count: int
    capped: bool


@dataclass
class AggregatedSignal:
    """All TradeSignals that agree on the same market + outcome."""
    market_slug: str
    outcome: str
    signals: list[TradeSignal] = field(default_factory=list)

    # Representative price: median of individual signal prices.
    @property
    def price(self) -> float:
        prices = sorted(s.price for s in self.signals)
        mid = len(prices) // 2
        if len(prices) % 2 == 1:
            return prices[mid]
        return (prices[mid - 1] + prices[mid]) / 2.0

    @property
    def trader_count(self) -> int:
        return len({s.trader.address for s in self.signals})


@dataclass(frozen=True)
class ConsensusDecision:
    """Output of SignalAggregator.process() for a single market+outcome key."""
    market_slug: str
    outcome: str
    aggregated: AggregatedSignal
    conviction: ConvictionResult   # execute=False means still pending


# ---------------------------------------------------------------------------
# Sizing engine (pure function wrapper)
# ---------------------------------------------------------------------------

class ConvictionEngine:
    """
    Scales a base Kelly bet size by trader consensus count and enforces the
    hard bankroll cap.

    Tiers
    -----
    1 trader  → pending (execute=False, size=0)
    2 traders → 1.0x Kelly
    3 traders → 1.25x Kelly
    4+ traders → 1.5x Kelly
    """

    def evaluate(
        self,
        base_kelly_size: float,
        trader_count: int,
        bankroll: float,
    ) -> ConvictionResult:
        if trader_count < _EXECUTE_THRESHOLD:
            logger.info(
                f"[CONVICTION] trader_count={trader_count} — below threshold, "
                f"adding to pending"
            )
            return ConvictionResult(
                execute=False, size=0.0, multiplier=0.0,
                trader_count=trader_count, capped=False,
            )

        multiplier = _MULTIPLIERS.get(trader_count, _MULTIPLIER_MAX)
        raw_size = base_kelly_size * multiplier
        cap = bankroll * MAX_POSITION_PCT
        final_size = min(raw_size, cap)
        capped = final_size < raw_size

        logger.info(
            f"[CONVICTION] trader_count={trader_count}, multiplier={multiplier}x, "
            f"kelly_base=${base_kelly_size:.2f}, raw=${raw_size:.2f}, "
            f"cap=${cap:.2f}, final=${final_size:.2f}"
            + (" [CAP APPLIED]" if capped else "")
        )

        return ConvictionResult(
            execute=True, size=final_size, multiplier=multiplier,
            trader_count=trader_count, capped=capped,
        )


# ---------------------------------------------------------------------------
# Aggregation + pending queue
# ---------------------------------------------------------------------------

class SignalAggregator:
    """
    Groups TradeSignals by (market_slug, outcome) across loop iterations,
    maintains a pending queue for single-trader signals, and promotes them
    to execution decisions once consensus is reached.

    Intended usage in the main loop
    --------------------------------
        aggregator = SignalAggregator()
        engine     = ConvictionEngine()

        while True:
            signals  = collect_all_signals(traders)          # fresh each tick
            decisions = aggregator.process(signals, engine, base_kelly, bankroll)

            for decision in decisions:
                if decision.conviction.execute:
                    place_order(decision)   # Step 8
                # pending decisions are silently retained in the queue

            aggregator.expire_pending(max_age_seconds=3600)  # optional TTL
            time.sleep(300)

    Key behaviour
    -------------
    - Each call to process() rebuilds the current-tick aggregation from the
      fresh signal list, then merges with any signals already in the pending
      queue that are NOT present in this tick (stale pending signals are kept
      until explicitly expired via expire_pending()).
    - A trader address can only contribute one signal per market+outcome key
      per tick; duplicate addresses within the same tick are deduplicated so
      that a single trader cannot inflate the count.
    - The pending queue is keyed by (market_slug, outcome).  When a pending
      entry reaches the execution threshold it is removed from the queue and
      returned as execute=True.
    """

    def __init__(self) -> None:
        # (market_slug, outcome) → AggregatedSignal for single-trader signals
        # waiting for a second confirmation.
        self._pending: dict[tuple[str, str], AggregatedSignal] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(
        self,
        signals: list[TradeSignal],
        engine: ConvictionEngine,
        base_kelly_size: float,
        bankroll: float,
    ) -> list[ConsensusDecision]:
        """
        Aggregate signals from this tick, merge with pending queue, and
        return one ConsensusDecision per distinct (market, outcome) pair.
        """
        grouped = self._group(signals)
        decisions: list[ConsensusDecision] = []

        seen_keys: set[tuple[str, str]] = set()

        for key, agg in grouped.items():
            seen_keys.add(key)
            merged = self._merge_with_pending(key, agg)
            conviction = engine.evaluate(base_kelly_size, merged.trader_count, bankroll)

            if conviction.execute:
                self._pending.pop(key, None)
                logger.info(
                    f"[CONSENSUS] {key[0]} {key[1]} — {merged.trader_count} traders, "
                    f"price={merged.price:.4f}, EXECUTING"
                )
            else:
                self._pending[key] = merged
                logger.info(
                    f"[CONSENSUS] {key[0]} {key[1]} — {merged.trader_count} trader(s), "
                    f"price={merged.price:.4f}, PENDING"
                )

            decisions.append(ConsensusDecision(
                market_slug=key[0],
                outcome=key[1],
                aggregated=merged,
                conviction=conviction,
            ))

        # Emit decisions for keys that are only in the pending queue this tick
        # so the caller knows they still exist but haven't been re-signalled.
        for key, agg in self._pending.items():
            if key not in seen_keys:
                conviction = engine.evaluate(base_kelly_size, agg.trader_count, bankroll)
                decisions.append(ConsensusDecision(
                    market_slug=key[0],
                    outcome=key[1],
                    aggregated=agg,
                    conviction=conviction,
                ))

        return decisions

    def expire_pending(self, max_age_seconds: int = 3600) -> None:
        """
        Remove pending signals whose oldest contributing signal is stale.
        Uses the 'timestamp' field from the raw trade dict if present;
        falls back to keeping the entry if no timestamp is parseable.
        """
        import time
        now = time.time()
        to_delete: list[tuple[str, str]] = []

        for key, agg in self._pending.items():
            timestamps = []
            for sig in agg.signals:
                ts = sig.raw.get("timestamp") or sig.raw.get("created_at")
                try:
                    timestamps.append(float(ts))
                except (TypeError, ValueError):
                    pass
            if timestamps and (now - min(timestamps)) > max_age_seconds:
                to_delete.append(key)
                logger.info(
                    f"[PENDING EXPIRED] {key[0]} {key[1]} — "
                    f"oldest signal {int(now - min(timestamps))}s ago"
                )

        for key in to_delete:
            del self._pending[key]

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _group(signals: list[TradeSignal]) -> dict[tuple[str, str], AggregatedSignal]:
        """Deduplicate by trader address within each key, then aggregate."""
        # (market_slug, outcome) → { trader_address → TradeSignal }
        buckets: dict[tuple[str, str], dict[str, TradeSignal]] = defaultdict(dict)

        for sig in signals:
            key = (sig.market_slug, sig.outcome)
            addr = sig.trader.address
            # Keep the signal with the higher price if the same trader appears twice
            existing = buckets[key].get(addr)
            if existing is None or sig.price > existing.price:
                buckets[key][addr] = sig

        return {
            key: AggregatedSignal(
                market_slug=key[0],
                outcome=key[1],
                signals=list(addr_map.values()),
            )
            for key, addr_map in buckets.items()
        }

    def _merge_with_pending(
        self,
        key: tuple[str, str],
        current: AggregatedSignal,
    ) -> AggregatedSignal:
        """Combine current-tick signals with any already in the pending queue."""
        pending = self._pending.get(key)
        if pending is None:
            return current

        # Merge: current-tick signals take precedence for the same trader address
        addr_map: dict[str, TradeSignal] = {s.trader.address: s for s in pending.signals}
        for sig in current.signals:
            addr_map[sig.trader.address] = sig

        return AggregatedSignal(
            market_slug=key[0],
            outcome=key[1],
            signals=list(addr_map.values()),
        )