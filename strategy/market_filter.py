from __future__ import annotations

import os
import sys

# Add the project root to sys.path so it can find 'config' and 'utils' when run directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from datetime import datetime, timezone

from config import (
    MARKET_CACHE_TTL,
    MIN_MARKET_VOLUME,
    MIN_MARKET_LIQUIDITY,
    MIN_HOURS_TO_CLOSE,
    MAX_DAYS_TO_CLOSE,
)
from polymarket.api import get_market
from utils.cache import TTLCache
from utils.logger import get_logger

logger = get_logger(__name__)


def _parse_close_time(meta: dict) -> datetime | None:
    for key in ("endDate", "end_date", "closeTime", "close_time", "resolutionTime", "resolution_time"):
        raw = meta.get(key)
        if not raw:
            continue
        # Handle Unix timestamps (int or float)
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass
        # Handle ISO 8601 strings
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            continue
    return None


def _coerce_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class MarketFilter:
    def __init__(self) -> None:
        self._cache: TTLCache = TTLCache(ttl=MARKET_CACHE_TTL)

    def get_market_metadata(self, slug: str, token_id: str | None = None) -> dict | None:
        cached = self._cache.get(slug)
        if cached is not None:
            return cached
        meta = get_market(slug, token_id=token_id)
        if meta is not None:
            self._cache.set(slug, meta)
        return meta

    def is_tradeable(self, slug: str, token_id: str | None = None) -> bool:
        meta = self.get_market_metadata(slug, token_id=token_id)

        if meta is None:
            logger.info(f"[REJECT] {slug}: could not fetch metadata")
            return False

        closed = meta.get("closed") or meta.get("is_closed")
        if closed:
            logger.info(f"[REJECT] {slug}: market is closed")
            return False

        status = str(meta.get("status", "")).lower()
        if status in ("closed", "resolved", "cancelled"):
            logger.info(f"[REJECT] {slug}: market status is '{status}'")
            return False

        volume = None
        for vol_key in ("volume", "volumeNum", "volume_num", "volume24hr"):
            raw_vol = meta.get(vol_key)
            if raw_vol is not None:
                volume = _coerce_float(raw_vol)
                if volume is not None:
                    break

        if volume is None or volume < MIN_MARKET_VOLUME:
            logger.info(
                f"[REJECT] {slug}: volume ${volume} below minimum ${MIN_MARKET_VOLUME}"
            )
            return False

        liquidity = None
        for liq_key in ("liquidity", "liquidityNum", "liquidity_num"):
            raw_liq = meta.get(liq_key)
            if raw_liq is not None:
                liquidity = _coerce_float(raw_liq)
                if liquidity is not None:
                    break

        if liquidity is None or liquidity < MIN_MARKET_LIQUIDITY:
            logger.info(
                f"[REJECT] {slug}: liquidity ${liquidity} below minimum ${MIN_MARKET_LIQUIDITY}"
            )
            return False

        close_dt = _parse_close_time(meta)

        if close_dt is None:
            logger.info(f"[REJECT] {slug}: could not parse close time from metadata")
            return False

        now = datetime.now(tz=timezone.utc)
        hours_to_close = (close_dt - now).total_seconds() / 3600

        if hours_to_close < MIN_HOURS_TO_CLOSE:
            logger.info(
                f"[REJECT] {slug}: closes in {hours_to_close:.1f}h, under {MIN_HOURS_TO_CLOSE}h minimum"
            )
            return False

        days_to_close = hours_to_close / 24

        if days_to_close > MAX_DAYS_TO_CLOSE:
            logger.info(
                f"[REJECT] {slug}: closes in {days_to_close:.1f} days, over {MAX_DAYS_TO_CLOSE}-day maximum"
            )
            return False

        return True