"""
JARVIS watchlist — per-period market selection.

Given the markets the copy-trade conviction flow already surfaced, JARVIS:
  1. keeps only markets resolving inside the near-term window,
  2. drops markets Kalshi disagrees with (no point spending a Claude call),
  3. runs a parallel Claude assessment on the survivors (intelligence.analyst),
  4. converts each assessment into a deterministic 0-100 score, and
  5. returns a ranked Watchlist the bot can threshold on.

The score is transparent math over {confidence, trader_count, kalshi_signal}.
Claude provides the qualitative judgment (and the "skip" veto); the ranking
itself is reproducible and lives in config.py.
"""

from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from datetime import date, datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from intelligence.analyst import MarketAssessment, assess_markets_parallel
from news.newsapi import NewsArticle
from strategy.kalshi import AGREE, DISAGREE
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MarketCandidate:
    """A trader-flagged market handed to JARVIS for evaluation.

    The caller assembles these from the conviction flow's ConsensusDecisions
    plus market metadata, news, and the Kalshi cross-reference.
    """
    market_id: str
    market_title: str
    yes_price: float
    trader_count: int
    avg_trader_price: float
    kalshi_signal: str            # AGREE | DISAGREE | NO_MATCH
    hours_to_resolution: float
    articles: list[NewsArticle] = field(default_factory=list)


@dataclass
class WatchlistEntry:
    market_id: str
    market_title: str
    score: float
    confidence: str
    hours_to_resolution: float
    trader_count: int
    kalshi_signal: str
    summary: str
    flags: list[str]
    assessment: MarketAssessment


@dataclass
class Watchlist:
    generated_at: datetime
    period_label: str
    min_resolution_hours: float
    max_resolution_hours: float
    entries: list[WatchlistEntry]   # sorted by score, descending

    def tradeable(self, min_score: float = config.WATCHLIST_MIN_SCORE) -> list[WatchlistEntry]:
        """Entries the bot should trade this period."""
        return [e for e in self.entries if e.score >= min_score]


def compute_score(confidence: str, trader_count: int, kalshi_signal: str) -> float:
    """Deterministic 0-100 ranking score.

    A "skip" confidence or a Kalshi "disagree" both veto the market (score 0),
    matching the main pipeline where Kalshi disagreement zeroes the bet size.
    """
    if confidence == "skip" or kalshi_signal == DISAGREE:
        return 0.0

    base = config.WATCHLIST_CONFIDENCE_BASE.get(
        confidence, config.WATCHLIST_CONFIDENCE_BASE["low"]
    )

    extra_traders = max(0, trader_count - config.WATCHLIST_CONVICTION_BASELINE)
    conviction_bonus = min(
        extra_traders * config.WATCHLIST_CONVICTION_BONUS_PER_TRADER,
        config.WATCHLIST_CONVICTION_BONUS_CAP,
    )

    kalshi_bonus = config.WATCHLIST_KALSHI_AGREE_BONUS if kalshi_signal == AGREE else 0.0

    return max(0.0, min(100.0, base + conviction_bonus + kalshi_bonus))


def _in_window(candidate: MarketCandidate, lo: float, hi: float) -> bool:
    return lo <= candidate.hours_to_resolution <= hi


def build_watchlist(
    candidates: list[MarketCandidate],
    *,
    period_label: str | None = None,
    min_resolution_hours: float = config.WATCHLIST_MIN_RESOLUTION_HOURS,
    max_resolution_hours: float = config.WATCHLIST_MAX_RESOLUTION_HOURS,
) -> Watchlist:
    """Rank trader-flagged markets for the current period.

    Markets outside the resolution window or that Kalshi disagrees with are
    dropped before any Claude calls. Survivors are assessed in parallel, scored,
    and returned sorted by score (descending).
    """
    label = period_label or date.today().isoformat()

    in_window = [
        c for c in candidates
        if _in_window(c, min_resolution_hours, max_resolution_hours)
    ]
    survivors = [c for c in in_window if c.kalshi_signal != DISAGREE]

    dropped_window = len(candidates) - len(in_window)
    dropped_kalshi = len(in_window) - len(survivors)
    logger.info(
        f"[WATCHLIST] {label}: {len(candidates)} candidate(s) → "
        f"{dropped_window} outside {min_resolution_hours:.0f}-{max_resolution_hours:.0f}h window, "
        f"{dropped_kalshi} Kalshi-disagree, {len(survivors)} to assess"
    )

    assessments = assess_markets_parallel([
        {
            "market_id": c.market_id,
            "market_title": c.market_title,
            "yes_price": c.yes_price,
            "articles": c.articles,
            "trader_count": c.trader_count,
            "avg_trader_price": c.avg_trader_price,
            "kalshi_signal": c.kalshi_signal,
        }
        for c in survivors
    ])

    entries: list[WatchlistEntry] = []
    for c in survivors:
        assessment = assessments.get(c.market_id)
        if assessment is None:
            continue
        score = compute_score(assessment.confidence, c.trader_count, c.kalshi_signal)
        entries.append(WatchlistEntry(
            market_id=c.market_id,
            market_title=c.market_title,
            score=score,
            confidence=assessment.confidence,
            hours_to_resolution=c.hours_to_resolution,
            trader_count=c.trader_count,
            kalshi_signal=c.kalshi_signal,
            summary=assessment.summary,
            flags=assessment.flags,
            assessment=assessment,
        ))

    entries.sort(key=lambda e: e.score, reverse=True)

    tradeable = sum(1 for e in entries if e.score >= config.WATCHLIST_MIN_SCORE)
    logger.info(
        f"[WATCHLIST] {label}: ranked {len(entries)} market(s), "
        f"{tradeable} at/above min score {config.WATCHLIST_MIN_SCORE}"
    )
    for e in entries:
        logger.info(
            f"[WATCHLIST]   {e.score:5.1f}  {e.confidence:<6}  "
            f"traders={e.trader_count}  kalshi={e.kalshi_signal}  {e.market_title}"
        )

    return Watchlist(
        generated_at=datetime.now(),
        period_label=label,
        min_resolution_hours=min_resolution_hours,
        max_resolution_hours=max_resolution_hours,
        entries=entries,
    )
