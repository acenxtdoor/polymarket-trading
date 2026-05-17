"""
Post-run daily summary generator.

Streams a JARVIS-style narrative from Claude and saves it to the Obsidian vault.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import anthropic
import config

_SYSTEM_PROMPT = """\
You are JARVIS, the AI analyst for an automated Polymarket trading bot.
Write a concise, plain-English daily briefing for the bot's operator.
Tone: confident, analytical, no jargon. Use bullet points where helpful.
Structure: opening headline sentence, then bullets for trades placed, trades skipped (with top reason), market flags raised, and a closing observation.
Keep it under 250 words.
"""


@dataclass
class DailySummaryInput:
    trades_placed: list[dict]   # list of {market_title, action, conviction, kelly_fraction, entry_price, claude_confidence}
    trades_skipped: list[dict]  # list of {market_title, skip_reason}
    flags_raised: list[str]
    bankroll: float
    run_date: date | None = None


def _build_prompt(data: DailySummaryInput) -> str:
    run_date = data.run_date or date.today()
    placed = len(data.trades_placed)
    skipped = len(data.trades_skipped)

    placed_lines = "\n".join(
        f"  - {t['market_title']} | {t.get('action','BUY')} | conviction={t.get('conviction','?')} | Kelly={t.get('kelly_fraction',0):.1%} | entry={t.get('entry_price',0):.2f} | Claude={t.get('claude_confidence','?')}"
        for t in data.trades_placed
    ) or "  (none)"

    skipped_lines = "\n".join(
        f"  - {t['market_title']}: {t.get('skip_reason','unknown')}"
        for t in data.trades_skipped
    ) or "  (none)"

    flags_lines = "\n".join(f"  - {f}" for f in data.flags_raised) or "  (none)"

    return f"""\
Date: {run_date}
Bankroll: ${data.bankroll:,.2f}
Trades placed ({placed}):
{placed_lines}
Trades skipped ({skipped}):
{skipped_lines}
Flags raised:
{flags_lines}

Write the daily briefing."""


def generate_daily_summary(data: DailySummaryInput) -> str:
    if not config.ANTHROPIC_API_KEY:
        d = data.run_date or date.today()
        return (
            f"JARVIS offline — no ANTHROPIC_API_KEY set.\n\n"
            f"**{d.isoformat()}:** {len(data.trades_placed)} trade(s) placed, "
            f"{len(data.trades_skipped)} skipped. Bankroll: ${data.bankroll:,.2f}."
        )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    prompt = _build_prompt(data)

    summary_parts: list[str] = []
    try:
        with client.messages.stream(
            model="claude-opus-4-7",
            max_tokens=512,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                summary_parts.append(text)
    except Exception as exc:
        return f"JARVIS daily summary unavailable: {exc}"

    return "".join(summary_parts)
