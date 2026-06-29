"""
strategy/kalshi_filter.py
=========================
Kalshi market quality filter — equivalent of market_filter.py for Polymarket.

Checks volume, open interest, and time-to-close before allowing execution.
Results are TTL-cached per ticker to avoid hammering the API.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from kalshi.api import get_market
from utils.cache import TTLCache
from utils.logger import get_logger

logger = get_logger(__name__)


def _coerce_float(val) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


class KalshiMarketFilter:
    def __init__(self) -> None:
        self._cache: TTLCache = TTLCache(ttl=config.MARKET_CACHE_TTL)

    def get_market_metadata(self, ticker: str) -> dict | None:
        cached = self._cache.get(ticker)
        if cached is not None:
            return cached
        meta = get_market(ticker)
        if meta is not None:
            self._cache.set(ticker, meta)
        return meta

    def is_tradeable(self, ticker: str) -> bool:
        meta = self.get_market_metadata(ticker)
        if meta is None:
            logger.info(f"[KALSHI-FILTER] {ticker}: could not fetch metadata")
            return False

        status = str(meta.get("status", "")).lower()
        if status != "open":
            logger.info(f"[KALSHI-FILTER] {ticker}: status='{status}' (not open)")
            return False

        volume = _coerce_float(meta.get("volume"))
        if volume is None or volume < config.KALSHI_MIN_VOLUME:
            logger.info(f"[KALSHI-FILTER] {ticker}: volume ${volume} < min ${config.KALSHI_MIN_VOLUME}")
            return False

        oi = _coerce_float(meta.get("open_interest"))
        if oi is None or oi < config.KALSHI_MIN_OPEN_INTEREST:
            logger.info(f"[KALSHI-FILTER] {ticker}: open_interest ${oi} < min ${config.KALSHI_MIN_OPEN_INTEREST}")
            return False

        close_time_str = meta.get("close_time")
        if not close_time_str:
            logger.info(f"[KALSHI-FILTER] {ticker}: no close_time in metadata")
            return False

        try:
            close_dt = datetime.fromisoformat(str(close_time_str).replace("Z", "+00:00"))
            if close_dt.tzinfo is None:
                close_dt = close_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            logger.info(f"[KALSHI-FILTER] {ticker}: could not parse close_time '{close_time_str}'")
            return False

        hours_to_close = (close_dt - datetime.now(tz=timezone.utc)).total_seconds() / 3600

        if hours_to_close < config.KALSHI_MIN_HOURS_TO_CLOSE:
            logger.info(f"[KALSHI-FILTER] {ticker}: closes in {hours_to_close:.1f}h < min {config.KALSHI_MIN_HOURS_TO_CLOSE}h")
            return False

        if hours_to_close / 24 > config.KALSHI_MAX_DAYS_TO_CLOSE:
            logger.info(f"[KALSHI-FILTER] {ticker}: closes in {hours_to_close/24:.1f}d > max {config.KALSHI_MAX_DAYS_TO_CLOSE}d")
            return False

        return True
