"""
Pre-trade market intelligence via Claude.

Produces a structured JSON assessment:
  {
    "confidence": "high" | "medium" | "low" | "skip",
    "summary": "<2-3 sentence plain-English rationale>",
    "flags": ["<unusual condition>", ...]
  }

confidence == "skip" means Claude detected a strong reason to avoid the trade.
"""

from __future__ import annotations

import json
import concurrent.futures
from dataclasses import dataclass, field

import anthropic
import config
from news.newsapi import NewsArticle

_SYSTEM_PROMPT = """\
You are JARVIS, an AI trading analyst for a Polymarket copy-trading bot.
Your job is to assess prediction market trades given:
- Market title and current YES price
- Recent news headlines relevant to the market
- Trader signals (how many top traders are buying and at what prices)
- Kalshi cross-reference signal

Respond ONLY with valid JSON in exactly this schema:
{
  "confidence": "<high|medium|low|skip>",
  "summary": "<2-3 sentence plain-English assessment>",
  "flags": ["<flag1>", ...]
}

confidence meanings:
  high   — strong fundamental support, proceed
  medium — mixed signals, proceed with caution
  low    — weak support, consider skipping
  skip   — detected a clear reason to avoid (e.g. resolving soon, contradictory news, liquidity trap)

flags are unusual conditions worth noting (e.g. "Market resolves in <48h", "Contradictory news found", "Thin order book").
An empty flags list is fine when there is nothing unusual.
Do not include any text outside the JSON object.
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "confidence": {"type": "string", "enum": ["high", "medium", "low", "skip"]},
        "summary": {"type": "string"},
        "flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["confidence", "summary", "flags"],
}


@dataclass
class MarketAssessment:
    market_id: str
    confidence: str = "low"
    summary: str = ""
    flags: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def _build_user_message(
    market_title: str,
    yes_price: float,
    articles: list[NewsArticle],
    trader_count: int,
    avg_trader_price: float,
    kalshi_signal: str,
) -> str:
    news_block = "\n".join(f"- {a.to_text()}" for a in articles) or "No recent news found."
    return f"""\
Market: {market_title}
Current YES price: {yes_price:.1%}
Trader signals: {trader_count} top trader(s) buying at avg {avg_trader_price:.1%}
Kalshi cross-reference: {kalshi_signal}

Recent news:
{news_block}

Assess this trade."""


def assess_market(
    market_id: str,
    market_title: str,
    yes_price: float,
    articles: list[NewsArticle],
    trader_count: int,
    avg_trader_price: float,
    kalshi_signal: str,
) -> MarketAssessment:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    user_msg = _build_user_message(
        market_title, yes_price, articles, trader_count, avg_trader_price, kalshi_signal
    )

    try:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=512,
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "json_schema": {"name": "assessment", "schema": _SCHEMA}}},
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_msg}],
        )

        text = next(
            (b.text for b in response.content if hasattr(b, "text")), "{}"
        )
        data = json.loads(text)
    except Exception as exc:
        return MarketAssessment(
            market_id=market_id,
            confidence="low",
            summary=f"Intelligence layer unavailable: {exc}",
            flags=["Claude API error"],
        )

    return MarketAssessment(
        market_id=market_id,
        confidence=data.get("confidence", "low"),
        summary=data.get("summary", ""),
        flags=data.get("flags", []),
        raw=data,
    )


def assess_markets_parallel(
    assessments_input: list[dict],
) -> dict[str, MarketAssessment]:
    """
    assessments_input: list of dicts with keys matching assess_market() args.
    Returns dict keyed by market_id.
    """
    results: dict[str, MarketAssessment] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.INTELLIGENCE_WORKERS) as pool:
        futures = {
            pool.submit(assess_market, **item): item["market_id"]
            for item in assessments_input
        }
        for future in concurrent.futures.as_completed(futures):
            market_id = futures[future]
            try:
                results[market_id] = future.result()
            except Exception as exc:
                results[market_id] = MarketAssessment(
                    market_id=market_id,
                    confidence="low",
                    summary=str(exc),
                    flags=["Parallel execution error"],
                )

    return results
