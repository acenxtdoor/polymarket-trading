"""
main.py — JARVIS Polymarket copy-trading bot (Step 10)
======================================================
Entry point. Wires every completed step into a single event loop:

  1. Load .env → validate keys
  2. Init Portfolio, MarketFilter, TrailingStopManager, OrderExecutor
  3. Loop every LOOP_INTERVAL seconds:
       a. Fetch top traders → collect BUY signals
       b. Aggregate by market (conviction engine) → ConsensusDecision list
       c. Quality-filter candidates (MarketFilter.is_tradeable)
       d. Fetch news, get Kalshi signals → MarketCandidate list
       e. Build JARVIS watchlist (parallel Claude assessment + scoring)
       f. For each tradeable entry → Kelly size → execute buy → Obsidian note
       g. Check trailing stops on open positions → execute sells
       h. After 24 h → generate JARVIS daily summary → Obsidian daily report

Run:
    python main.py

Environment variables (set in .env):
    ANTHROPIC_API_KEY   — Claude API key (optional; JARVIS offline without it)
    NEWSAPI_KEY         — NewsAPI.org key (optional; news disabled without it)
    OBSIDIAN_VAULT_PATH — path to your Obsidian vault
    PORTFOLIO_PATH      — where portfolio.json is saved (default: portfolio.json)
    LOOP_INTERVAL       — seconds between bot runs (default: 300)
"""

from __future__ import annotations

import os
import signal
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path


# ── 1. Load .env before any os.getenv call ────────────────────────────────────

def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader — no third-party dependency required."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        # Always apply .env value; only skip if key is already set to a non-empty value
        k, v = key.strip(), val.strip().strip('"').strip("'")
        if not os.environ.get(k):
            os.environ[k] = v


_load_dotenv()

# ── 2. Project imports (after env vars are set) ───────────────────────────────

import config
from execution.order import OrderExecutor
from execution.portfolio import Portfolio
from intelligence.watchlist import MarketCandidate, build_watchlist
from obsidian.writer import ensure_vault, update_trade_outcome, update_trade_price, write_daily_report, write_skip, write_trade
from polymarket.api import get_market as _get_market_direct
from polymarket.traders import collect_all_signals, fetch_top_traders
from strategy.conviction import ConvictionEngine, SignalAggregator
from strategy.kalshi import get_kalshi_signal
from strategy.kelly import position_size
from strategy.market_filter import MarketFilter
from strategy.trailing_stop import TrailingStopManager
from utils.logger import get_logger

logger = get_logger(__name__)

LOOP_INTERVAL: int = int(os.getenv("LOOP_INTERVAL", "60"))


# ── 3. Helpers ────────────────────────────────────────────────────────────────

def _market_title(metadata: dict) -> str:
    """Extract a human-readable title from Polymarket market metadata."""
    for key in ("question", "title", "description", "slug"):
        val = metadata.get(key)
        if val and isinstance(val, str):
            return val
    return "Unknown market"


def _hours_to_close(metadata: dict) -> float | None:
    """Hours until market resolution. Returns None if close time cannot be parsed."""
    from strategy.market_filter import _parse_close_time
    close_dt = _parse_close_time(metadata)
    if close_dt is None:
        return None
    return (close_dt - datetime.now(tz=timezone.utc)).total_seconds() / 3600


def _extract_outcome_price(
    market_data: dict | None,
    outcome: str | None,
    fallback: float,
    allow_resolved: bool = False,
) -> float:
    """Extract the live price for a specific outcome token from Gamma API metadata.

    Polymarket head-to-head markets (team vs team, player vs player) label their
    tokens with the team/player name rather than "YES"/"NO".  We therefore do two
    passes through the tokens array:

      1. Match the stored outcome name exactly (case-insensitive).
         e.g. outcome="Chicago White Sox" finds the White Sox token price.
      2. Fall back to a "YES" token for standard binary prediction markets.

    allow_resolved=True widens the accepted range from (0, 1) to [0, 1] so that
    prices from resolved markets (winner=1.0, loser=0.0) are returned correctly.
    Use this only on the EXIT side — never when assessing a market for entry.

    Returns ``fallback`` only if neither pass finds a valid price.
    """
    if market_data and outcome:
        tokens = market_data.get("tokens", [])

        def _valid(price: float) -> bool:
            return 0.0 <= price <= 1.0 if allow_resolved else 0.0 < price < 1.0

        # Single-pass: match the exact outcome name (team/player for head-to-head
        # markets, or "YES"/"NO" for binary prediction markets).
        # The old Pass-2 "YES token" fallback has been removed: if the specific
        # outcome doesn't match, we fall through to `fallback` rather than risk
        # matching a "YES" token from a cached wrong market (price contamination).
        for token in tokens:
            if str(token.get("outcome", "")).upper() == outcome.upper():
                try:
                    price = float(token.get("price", 0))
                    if _valid(price):
                        return price
                except (TypeError, ValueError):
                    pass

        # Fallback: some Gamma responses omit tokens[] and encode prices as
        # JSON strings in outcomes / outcomePrices instead.
        if not tokens:
            try:
                import json as _json
                _outcomes = _json.loads(market_data.get("outcomes", "[]"))
                _prices   = _json.loads(market_data.get("outcomePrices", "[]"))
                for _out, _pstr in zip(_outcomes, _prices):
                    if str(_out).upper() == outcome.upper():
                        _p = float(_pstr)
                        if _valid(_p):
                            return _p
                        break
            except (ValueError, TypeError):
                pass
    return fallback


def _get_resolved_price(exit_meta: dict, outcome: str, slug: str) -> float | None:
    """Determine the final settlement price for a position in a resolved market.

    Called only when the normal live-price lookup returns the entry-price fallback,
    meaning Gamma couldn't find a live price for our outcome token.  At that point
    the market is likely archived — this function tries to confirm resolution and
    return the correct 0.0 (loss) or 1.0 (win) settlement price.

    Returns:
        float — 0.0 or 1.0 when resolution is confirmed
        None  — market not yet resolved or outcome indeterminate (hold position)
    """
    from strategy.market_filter import _parse_close_time

    # ── 1. Check metadata flags ───────────────────────────────────────────────
    is_resolved = bool(
        exit_meta.get("closed")
        or exit_meta.get("is_closed")
        or str(exit_meta.get("status", "")).lower() in ("closed", "resolved", "cancelled")
    )

    # ── 2. Fall back to timing: >4 h past close → treat as resolved ───────────
    if not is_resolved:
        close_dt = _parse_close_time(exit_meta)
        if close_dt is not None:
            hours_past = (datetime.now(tz=timezone.utc) - close_dt).total_seconds() / 3600
            is_resolved = hours_past > 4.0

    if not is_resolved:
        return None

    tokens = exit_meta.get("tokens", [])

    # ── 3. Direct match: find our outcome token at a settled price (0 or 1) ───
    for tok in tokens:
        if str(tok.get("outcome", "")).upper() == outcome.upper():
            try:
                p = float(tok.get("price", -1))
                if 0.0 <= p <= 1.0:
                    logger.info(
                        f"[MAIN] {slug}: resolved — outcome='{outcome}' settled at {p:.4f}"
                    )
                    return p
            except (TypeError, ValueError):
                pass

    # ── 4. Indirect: find the winner token; if it's not ours we lost ──────────
    for tok in tokens:
        try:
            p = float(tok.get("price", -1))
        except (TypeError, ValueError):
            continue
        if p >= 0.95:   # winner settled at ~1.0
            tok_outcome = str(tok.get("outcome", "")).upper()
            if outcome and tok_outcome == outcome.upper():
                logger.info(f"[MAIN] {slug}: resolved — '{outcome}' is the winner (1.0)")
                return 1.0
            else:
                logger.info(
                    f"[MAIN] {slug}: resolved — winner is '{tok_outcome}', "
                    f"our outcome '{outcome}' lost (0.0)"
                )
                return 0.0

    # ── 5. No token data but market confirmed past close → assume loss ─────────
    # If the market is archived and Gamma returns no tokens, we have no way to
    # know the outcome.  Closing at 0.0 is conservative but unblocks the position.
    logger.warning(
        f"[MAIN] {slug}: market past close time with no token data — "
        f"closing at 0.0 to unblock stuck position (check manually if in doubt)"
    )
    return 0.0


def _validate_env() -> None:
    """Warn about missing optional keys; abort if nothing useful can run."""
    logger.info("JARVIS intelligence layer offline — trading on conviction signals only.")
    if config.DRY_RUN:
        logger.info("DRY_RUN=True — no real orders will be placed.")


# ── 4. Single-loop execution ──────────────────────────────────────────────────

def run_once(
    aggregator: SignalAggregator,
    engine: ConvictionEngine,
    market_filter: MarketFilter,
    portfolio: Portfolio,
    executor: OrderExecutor,
    stop_manager: TrailingStopManager,
    session_trades: list[dict],
    session_skips: list[dict],
    session_flags: list[str],
) -> None:
    """Execute one full iteration of the bot loop."""

    # ── a. Fetch traders + signals ────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("JARVIS — new run starting")
    try:
        traders = fetch_top_traders()
    except Exception as exc:
        logger.error(f"Could not fetch top traders: {exc}")
        return

    signals = collect_all_signals(traders)
    if not signals:
        logger.info("No buy signals found this run.")
        return

    # ── b. Conviction aggregation ─────────────────────────────────────────
    decisions = aggregator.process(
        signals,
        engine,
        base_kelly_size=position_size(
            portfolio.capital, p=0.60, price=0.55
        ),
        capital=portfolio.capital,
    )
    actionable = [d for d in decisions if d.conviction.execute]
    logger.info(
        f"{len(decisions)} market(s) in conviction flow, "
        f"{len(actionable)} ready to assess"
    )

    # ── c. Quality filter + candidate assembly ────────────────────────────
    candidates: list[MarketCandidate] = []
    for decision in actionable:
        slug = decision.market_slug
        # Pull the token_id from the first signal — used as Gamma API fallback when
        # the event-level slug doesn't resolve to a Gamma question-level market.
        _first_token_id = (
            decision.aggregated.signals[0].token_id
            if decision.aggregated.signals else None
        )
        if not market_filter.is_tradeable(slug, token_id=_first_token_id):
            # Fetch cached metadata for a better title (already in cache from is_tradeable)
            _meta = market_filter.get_market_metadata(slug) or {}
            _title = _market_title(_meta) or slug
            session_skips.append({
                "market_title": _title,
                "skip_reason": "failed market quality filter",
            })
            write_skip(
                market_id=slug,
                market_title=_title,
                skip_reason="failed market quality filter",
                conviction=decision.aggregated.trader_count,
                kalshi_signal="no_match",
                claude_confidence="skip",
                claude_summary="Market failed quality checks (volume, liquidity, or timing).",
                claude_flags=["Quality filter rejection"],
                traders=[s.trader.username for s in decision.aggregated.signals],
            )
            continue

        metadata = market_filter.get_market_metadata(slug, token_id=_first_token_id) or {}
        title = _market_title(metadata)
        agg = decision.aggregated
        # avg_trader_price: weighted avg price top traders paid — used as estimated true probability
        avg_trader_price = agg.price
        trader_count = agg.trader_count
        # yes_price: current live market price from Gamma API (fallback: trader avg).
        # Use the first signal's outcome to match head-to-head markets by team/player name.
        _candidate_outcome = agg.signals[0].outcome if agg.signals else None
        yes_price = _extract_outcome_price(metadata, _candidate_outcome, avg_trader_price)

        # ── d. Kalshi ─────────────────────────────────────────────────────
        kalshi_result = get_kalshi_signal(title, yes_price)

        if kalshi_result.multiplier == 0.0:
            logger.info(f"[MAIN] {slug}: Kalshi disagrees — skip")
            session_skips.append({
                "market_title": title,
                "skip_reason": "Kalshi disagrees",
            })
            write_skip(
                market_id=slug,
                market_title=title,
                skip_reason="Kalshi disagrees",
                conviction=trader_count,
                kalshi_signal=kalshi_result.signal,
                claude_confidence="skip",
                claude_summary="Kalshi hard-disagrees with Polymarket signal. Bot rule: skip.",
                claude_flags=["Kalshi disagree veto"],
                traders=[s.trader.username for s in agg.signals],
            )
            continue

        hours = _hours_to_close(metadata)
        if hours is None:
            logger.warning(f"[MAIN] {slug}: could not parse close time — skipping candidate")
            continue

        candidates.append(MarketCandidate(
            market_id=slug,
            market_title=title,
            yes_price=yes_price,
            trader_count=trader_count,
            avg_trader_price=avg_trader_price,
            kalshi_signal=kalshi_result.signal,
            hours_to_resolution=hours,
        ))

    # ── e. JARVIS watchlist ───────────────────────────────────────────────
    if not candidates:
        logger.info("No candidates survived filters — nothing to trade.")
        return

    # Deduplicate by title — Polymarket creates separate YES/NO token markets
    # for the same match (different slugs, same human-readable title).
    # Keep the entry with the highest trader conviction for each title.
    _seen: dict[str, MarketCandidate] = {}
    for c in candidates:
        existing = _seen.get(c.market_title)
        if existing is None or c.trader_count > existing.trader_count:
            _seen[c.market_title] = c
    deduped = list(_seen.values())
    if len(deduped) < len(candidates):
        logger.info(
            f"[MAIN] Deduped {len(candidates)} candidates → {len(deduped)} "
            f"(removed {len(candidates) - len(deduped)} duplicate title(s))"
        )
    candidates = deduped

    watchlist = build_watchlist(candidates)
    tradeable = watchlist.tradeable()
    logger.info(
        f"Watchlist: {len(watchlist.entries)} assessed, "
        f"{len(tradeable)} tradeable (score ≥ {config.WATCHLIST_MIN_SCORE})"
    )

    # Write skipped entries (below min score) to Obsidian
    for entry in watchlist.entries:
        if entry.score < config.WATCHLIST_MIN_SCORE:
            session_skips.append({
                "market_title": entry.market_title,
                "skip_reason": f"JARVIS score {entry.score:.0f} below threshold",
            })
            if entry.flags:
                session_flags.extend(entry.flags)
            write_skip(
                market_id=entry.market_id,
                market_title=entry.market_title,
                skip_reason=f"JARVIS score {entry.score:.0f} below {config.WATCHLIST_MIN_SCORE}",
                conviction=entry.trader_count,
                kalshi_signal=entry.kalshi_signal,
                claude_confidence=entry.confidence,
                claude_summary=entry.summary,
                claude_flags=entry.flags,
                traders=[],
            )

    # ── f. Execute buys ───────────────────────────────────────────────────
    for entry in tradeable:
        if portfolio.has_position(entry.market_id):
            logger.info(f"[MAIN] {entry.market_id}: already holding position — skip")
            continue
        if portfolio.is_closed_today(entry.market_id):
            logger.info(f"[MAIN] {entry.market_id}: closed by stop/take-profit today — skip re-buy")
            continue

        # Find the matching decision for token_id + Kelly inputs
        decision = next(
            (d for d in actionable if d.market_slug == entry.market_id), None
        )
        if decision is None:
            continue

        agg = decision.aggregated
        token_id = agg.signals[0].token_id if agg.signals else ""
        outcome = agg.signals[0].outcome if agg.signals else "YES"

        kalshi_result = get_kalshi_signal(entry.market_title, entry.yes_price)
        size = position_size(
            capital=portfolio.capital,
            # p = avg price top traders paid → their implied true probability
            # price = current live market price → the "cost" we pay
            # Edge exists when traders paid less than current price (or vice-versa).
            p=entry.avg_trader_price,
            price=entry.yes_price,
            conviction_multiplier=decision.conviction.multiplier,
            kalshi_multiplier=kalshi_result.multiplier,
        )
        if size <= 0:
            logger.info(f"[MAIN] {entry.market_id}: Kelly size=0 — no edge, skip")
            continue

        # Reject if current price is already below the trailing stop threshold
        # relative to the trader average — the position would stop-loss immediately.
        stop_floor = entry.avg_trader_price * (1.0 - config.TRAILING_STOP_PCT)
        if entry.yes_price < stop_floor:
            logger.warning(
                f"[MAIN] {entry.market_id}: current price {entry.yes_price:.4f} already "
                f"below stop floor {stop_floor:.4f} (trader avg {entry.avg_trader_price:.4f}) "
                "— skipping to avoid immediate stop-loss"
            )
            portfolio.mark_closed_today(entry.market_id)  # persisted blacklist for today
            continue

        result = executor.buy(
            market_slug=entry.market_id,
            token_id=token_id,
            outcome=outcome,
            price=entry.yes_price,
            size_dollars=size,
        )

        if result.filled:
            stop_manager.open_position(entry.market_id, result.fill_price)
            kelly_f = size / portfolio.capital if portfolio.capital > 0 else 0
            session_trades.append({
                "market_title": entry.market_title,
                "action": "BUY",
                "conviction": entry.trader_count,
                "kelly_fraction": kelly_f,
                "entry_price": result.fill_price,
                "claude_confidence": entry.confidence,
            })
            if entry.flags:
                session_flags.extend(entry.flags)
            write_trade(
                market_id=entry.market_id,
                market_title=entry.market_title,
                action="BUY",
                conviction=entry.trader_count,
                kalshi_signal=entry.kalshi_signal,
                kelly_fraction=kelly_f,
                position_size=size,
                entry_price=result.fill_price,
                take_profit=min(result.fill_price * (1.0 + config.TAKE_PROFIT_PCT), 0.99),
                current_price=entry.yes_price,
                claude_confidence=entry.confidence,
                claude_summary=entry.summary,
                claude_flags=entry.flags,
                traders=[s.trader.username for s in agg.signals],
            )
        else:
            session_skips.append({
                "market_title": entry.market_title,
                "skip_reason": result.reason,
            })

    # ── g. Trailing stop checks ───────────────────────────────────────────
    open_slugs = list(stop_manager.open_positions.keys())
    for slug in open_slugs:
        # Fetch position first so we have token_id for the metadata lookup.
        pos = portfolio.get_position(slug)
        if pos is None:
            try:
                stop_manager.close_position(slug)
            except KeyError:
                pass
            continue

        # Exit pricing — direct API call, bypasses market filter cache entirely.
        #
        # Strategy: try the slug first (clean path). If Gamma returns nothing
        # (Data-API event slugs often differ from Gamma question slugs), retry
        # with token_id so the lookup actually resolves.
        #
        # Contamination is blocked by the outcome-name check below — NOT by
        # withholding token_id. If token_id resolves to the wrong market, that
        # market's tokens will have different outcome names (e.g. a soccer
        # market won't contain "Andrey Rublev"), the match fails, and we fall
        # back to avg_price (hold) rather than use a corrupt price.
        _exit_meta = _get_market_direct(slug) or {}
        # Retry with token_id if the slug result has no tokens OR if the
        # expected outcome token is absent (Gamma may return a different market
        # for the same event — e.g. an over/under instead of the moneyline).
        # The outcome-name check below still guards against wrong-market prices.
        _outcome_in_meta = any(
            str(t.get("outcome", "")).upper() == pos.outcome.upper()
            for t in _exit_meta.get("tokens", [])
        )
        if not _outcome_in_meta:
            _exit_meta = _get_market_direct(slug, token_id=pos.token_id) or {}
        metadata = _exit_meta  # still used below for market title / Obsidian update
        current_price = pos.avg_price  # safe default: no exit if price unavailable
        for _tok in _exit_meta.get("tokens", []):
            if str(_tok.get("outcome", "")).upper() == pos.outcome.upper():
                try:
                    _p = float(_tok.get("price", 0))
                    if 0.0 <= _p <= 1.0:
                        current_price = _p
                except (TypeError, ValueError):
                    pass
                break  # stop after first outcome match — never read another market's tokens

        # Fallback A: parse outcomePrices / outcomes JSON strings (Gamma returns
        # these instead of a tokens[] array for some markets).
        if current_price == pos.avg_price:
            try:
                import json as _json
                _outcomes = _json.loads(_exit_meta.get("outcomes", "[]"))
                _prices   = _json.loads(_exit_meta.get("outcomePrices", "[]"))
                for _out, _pstr in zip(_outcomes, _prices):
                    if str(_out).upper() == pos.outcome.upper():
                        _p2 = float(_pstr)
                        if 0.0 <= _p2 <= 1.0:
                            current_price = _p2
                        break
            except (ValueError, TypeError):
                pass

        # Fallback B: market has resolved — determine 0.0/1.0 settlement price.
        if current_price == pos.avg_price:
            _resolved_price = _get_resolved_price(_exit_meta, pos.outcome, slug)
            if _resolved_price is not None:
                current_price = _resolved_price

        # Push live price to the Obsidian trade note so the dashboard stays current.
        update_trade_price(
            market_title=_market_title(metadata) or slug,
            current_price=current_price,
            market_id=slug,
        )

        # ── Price sanity check — reject obviously corrupt prices ──────────
        # If current_price is >10× the entry price, the price feed is likely
        # returning a value from a different market (cross-contamination).
        # Exception: price == 1.0 is legitimate for a fully-resolved winning
        # position — do NOT block it.
        # Skip all exits for this tick rather than realize a fake P&L.
        if current_price < 1.0 and current_price > pos.avg_price * 10:
            logger.warning(
                f"[MAIN] {slug}: price sanity fail — current={current_price:.4f} "
                f"is implausible vs entry={pos.avg_price:.4f} — skipping exit this tick"
            )
            continue

        # ── Take-profit check (runs before trailing stop) ─────────────────
        take_profit_price = min(pos.avg_price * (1.0 + config.TAKE_PROFIT_PCT), 0.99)
        if current_price >= take_profit_price:
            pos_cost = pos.cost
            pos_avg_price = pos.avg_price
            logger.info(
                f"[MAIN] Take-profit triggered: {slug}  "
                f"current={current_price:.4f}  entry={pos_avg_price:.4f}  "
                f"target={take_profit_price:.4f}"
            )
            sell_result = None
            try:
                sell_result = executor.sell(slug, current_price)
            except Exception as exc:
                logger.error(
                    f"[MAIN] Take-profit sell threw exception for {slug}: {exc!r}"
                )
            finally:
                # Always evict from stop tracker and blacklist — no matter what the sell does.
                try:
                    stop_manager.close_position(slug)
                except KeyError:
                    pass
                portfolio.mark_closed_today(slug)  # persisted — survives restarts
            if sell_result is not None and sell_result.filled:
                pnl = sell_result.size_dollars - pos_cost
                title_for_note = _market_title(metadata) or slug
                updated = update_trade_outcome(
                    market_title=title_for_note,
                    outcome="closed",
                    pnl=pnl,
                    market_id=slug,
                )
                if not updated:
                    write_trade(
                        market_id=slug,
                        market_title=title_for_note,
                        action="BUY",
                        conviction=1,
                        kalshi_signal="unknown",
                        kelly_fraction=0.0,
                        position_size=pos_cost,
                        entry_price=pos_avg_price,
                        claude_confidence="unknown",
                        claude_summary=(
                            "Position closed by take-profit. "
                            "Original trade note was not found."
                        ),
                        claude_flags=["Note reconstructed on close", "Take-profit exit"],
                        traders=[],
                        outcome="closed",
                        pnl=pnl,
                    )
            elif sell_result is not None:
                logger.error(
                    f"[MAIN] Take-profit sell rejected for {slug}: {sell_result.reason}"
                )
            continue  # skip trailing stop check for this position

        stop_result = stop_manager.update(slug, current_price)

        if stop_result.should_close:
            # Capture cost before executor.sell() removes the position from portfolio.
            pos_cost = pos.cost
            pos_avg_price = pos.avg_price
            sell_result = None
            try:
                sell_result = executor.sell(slug, current_price)
            except Exception as exc:
                logger.error(
                    f"[MAIN] Stop-loss sell threw exception for {slug}: {exc!r}"
                )
            finally:
                # Always evict from stop tracker and blacklist — no matter what the sell does.
                try:
                    stop_manager.close_position(slug)
                except KeyError:
                    pass
                portfolio.mark_closed_today(slug)  # persisted — survives restarts
            if sell_result is not None and sell_result.filled:
                pnl = sell_result.size_dollars - pos_cost
                logger.info(
                    f"[MAIN] Trailing stop triggered: {slug}  P&L=${pnl:,.2f}"
                )
                # Update the Obsidian trade note with final outcome and P&L.
                title_for_note = _market_title(metadata) or slug
                updated = update_trade_outcome(
                    market_title=title_for_note,
                    outcome="closed",
                    pnl=pnl,
                    market_id=slug,
                )
                if not updated:
                    # Note not found (e.g. dashboard was cleared but portfolio wasn't reset).
                    # Create a new closed-trade note so the dashboard P&L stays accurate.
                    logger.warning(
                        f"[MAIN] No Obsidian note found for {slug} — writing new closed note"
                    )
                    write_trade(
                        market_id=slug,
                        market_title=title_for_note,
                        action="BUY",
                        conviction=1,
                        kalshi_signal="unknown",
                        kelly_fraction=0.0,
                        position_size=pos_cost,
                        entry_price=pos_avg_price,
                        claude_confidence="unknown",
                        claude_summary=(
                            "Position closed by trailing stop. "
                            "Original trade note was not found (dashboard may have been cleared)."
                        ),
                        claude_flags=["Note reconstructed on close"],
                        traders=[],
                        outcome="closed",
                        pnl=pnl,
                    )
            elif sell_result is not None:
                logger.error(
                    f"[MAIN] Stop-loss sell rejected for {slug}: {sell_result.reason}"
                )

    logger.info(
        f"Run complete — capital=${portfolio.capital:,.2f}  "
        f"open={portfolio.open_count}  "
        f"realized P&L=${portfolio.realized_pnl:,.2f}"
    )


# ── 5. Daily summary ──────────────────────────────────────────────────────────

def maybe_write_daily_summary(
    session_trades: list[dict],
    session_skips: list[dict],
    session_flags: list[str],
    portfolio: Portfolio,
    last_summary_date: date,
) -> date:
    """Generate and write the JARVIS daily report if the date has rolled over.

    When the report is written the session accumulators are cleared in-place
    so the main loop starts fresh for the new day without a separate reset step.
    """
    today = date.today()
    if today <= last_summary_date:
        return last_summary_date

    logger.info("[MAIN] Generating daily summary...")
    summary_text = (
        f"**{last_summary_date.isoformat()}** — "
        f"{len(session_trades)} trade(s) placed, "
        f"{len(session_skips)} skipped. "
        f"Capital: ${portfolio.capital:,.2f}. "
        f"Running on trader signals only (JARVIS offline)."
    )
    write_daily_report(
        summary_text=summary_text,
        trades_count=len(session_trades),
        skipped_count=len(session_skips),
        capital=portfolio.capital,
        report_date=last_summary_date,
    )
    logger.info(f"[MAIN] Daily report written for {last_summary_date}")

    # Reset accumulators in-place for the new calendar day.
    # We clear here (not in main()) because after we return `today`,
    # the caller sets last_summary_date = today and the old
    # `if date.today() > last_summary_date` guard would always be False.
    session_trades.clear()
    session_skips.clear()
    session_flags.clear()
    return today


# ── 6. Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    _validate_env()
    ensure_vault()

    # Shared state
    portfolio = Portfolio()
    market_filter = MarketFilter()
    stop_manager = TrailingStopManager()

    # Rehydrate trailing stops from portfolio.json so persisted positions
    # remain exit-eligible across restarts. Without this, the manager
    # starts empty and existing positions can never trigger a stop sell.
    # peak_price isn't persisted, so we seed peak = entry = avg_price —
    # the stop is active again from the original cost basis.
    rehydrated = 0
    skipped_rehydration = 0
    for slug, pos in portfolio.positions.items():
        try:
            stop_manager.open_position(slug, pos.avg_price)
            rehydrated += 1
        except ValueError as exc:
            # avg_price outside (0, 1) — market may have already resolved.
            # Log and skip rather than crashing the entire bot on startup.
            logger.warning(
                f"[MAIN] Skipping rehydration for {slug}: {exc} "
                "(position will not trigger trailing stop until manually resolved)"
            )
            skipped_rehydration += 1
    if rehydrated:
        logger.info(
            f"[MAIN] Rehydrated {rehydrated} trailing stop(s) from portfolio"
            + (f" ({skipped_rehydration} skipped — invalid avg_price)" if skipped_rehydration else "")
        )

    executor = OrderExecutor(portfolio, market_filter)
    aggregator = SignalAggregator()
    engine = ConvictionEngine()

    # Graceful shutdown on Ctrl+C / SIGTERM
    running = True
    def _shutdown(sig, frame):  # noqa: ANN001
        nonlocal running
        logger.info("Shutdown signal received — finishing current run then exiting.")
        running = False
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Per-session accumulators (reset each calendar day)
    session_trades: list[dict] = []
    session_skips: list[dict] = []
    session_flags: list[str] = []
    # closed_this_session is now persisted inside Portfolio.mark_closed_today /
    # Portfolio.is_closed_today — no separate in-memory set needed here.
    last_summary_date: date = date.today()

    logger.info(
        f"JARVIS bot started — interval={LOOP_INTERVAL}s  "
        f"dry_run={config.DRY_RUN}  capital=${portfolio.capital:,.2f}"
    )

    while running:
        try:
            run_once(
                aggregator, engine, market_filter,
                portfolio, executor, stop_manager,
                session_trades, session_skips, session_flags,
            )
            # Evict pending signals older than 48 h (matches the staleness window
            # in traders.py). Without this, single-trader signals that never got a
            # second confirmation accumulate in memory forever and could fire stale
            # entries if _EXECUTE_THRESHOLD is ever raised above 1.
            aggregator.expire_pending(max_age_seconds=48 * 3600)
            last_summary_date = maybe_write_daily_summary(
                session_trades, session_skips, session_flags,
                portfolio, last_summary_date,
            )
            # Note: accumulators are cleared inside maybe_write_daily_summary()
            # when the date rolls over — no separate reset needed here.
        except Exception as exc:
            logger.exception(f"Unhandled error in run loop: {exc}")

        if running:
            logger.info(f"Sleeping {LOOP_INTERVAL}s until next run...")
            time.sleep(LOOP_INTERVAL)

    logger.info("JARVIS bot stopped.")


if __name__ == "__main__":
    main()
