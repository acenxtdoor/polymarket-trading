"""
Obsidian note templates with Dataview-compatible YAML frontmatter.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional


def _yaml_str(s: str) -> str:
    """Escape a string for safe inclusion in a YAML double-quoted scalar."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _fmt_list(items: list[str]) -> str:
    if not items:
        return "[]"
    return "[" + ", ".join(f'"{_yaml_str(i)}"' for i in items) + "]"


def trade_note(
    market_id: str,
    market_title: str,
    action: str,
    conviction: int,
    kalshi_signal: str,
    kelly_fraction: float,
    position_size: float,
    entry_price: float,
    claude_confidence: str,
    claude_summary: str,
    claude_flags: list[str],
    traders: list[str],
    outcome: Optional[str] = None,
    pnl: Optional[float] = None,
    tags: Optional[list[str]] = None,
    trade_date: Optional[date] = None,
) -> str:
    d = trade_date or date.today()
    tags_list = tags or ["trade", action.lower()]
    outcome_str = outcome or "open"
    pnl_str = f"{pnl:.2f}" if pnl is not None else "0"

    return f"""\
---
market_id: "{_yaml_str(market_id)}"
market_title: "{_yaml_str(market_title)}"
date: {d.isoformat()}
action: "{_yaml_str(action)}"
conviction: {conviction}
kalshi_signal: "{_yaml_str(kalshi_signal)}"
kelly_fraction: {kelly_fraction:.4f}
position_size: {position_size:.2f}
entry_price: {entry_price:.4f}
claude_confidence: "{_yaml_str(claude_confidence)}"
claude_flags: {_fmt_list(claude_flags)}
traders: {_fmt_list(traders)}
outcome: "{_yaml_str(outcome_str)}"
pnl: {pnl_str}
tags: {_fmt_list(tags_list)}
---

# {market_title}

**Date:** {d.isoformat()}
**Action:** {action} | **Conviction:** {conviction} trader(s) | **Kalshi:** {kalshi_signal}
**Kelly fraction:** {kelly_fraction:.2%} → **Position size:** ${position_size:,.2f}
**Entry price:** {entry_price:.4f}

## JARVIS Assessment

**Confidence:** {claude_confidence}

{claude_summary}

{"**Flags:**" + chr(10) + chr(10).join(f"- {f}" for f in claude_flags) if claude_flags else ""}

## Traders

{chr(10).join(f"- {t}" for t in traders)}

## Outcome

**Status:** {outcome_str}
**P&L:** {f"${pnl:,.2f}" if pnl is not None else "—"}
"""


def skip_note(
    market_id: str,
    market_title: str,
    skip_reason: str,
    conviction: int,
    kalshi_signal: str,
    claude_confidence: str,
    claude_summary: str,
    claude_flags: list[str],
    traders: list[str],
    skip_date: Optional[date] = None,
) -> str:
    d = skip_date or date.today()

    return f"""\
---
market_id: "{_yaml_str(market_id)}"
market_title: "{_yaml_str(market_title)}"
date: {d.isoformat()}
action: "SKIP"
skip_reason: "{_yaml_str(skip_reason)}"
conviction: {conviction}
kalshi_signal: "{_yaml_str(kalshi_signal)}"
claude_confidence: "{_yaml_str(claude_confidence)}"
claude_flags: {_fmt_list(claude_flags)}
traders: {_fmt_list(traders)}
tags: ["skipped"]
---

# [SKIPPED] {market_title}

**Date:** {d.isoformat()}
**Skip reason:** {skip_reason}
**Conviction:** {conviction} trader(s) | **Kalshi:** {kalshi_signal}

## JARVIS Assessment

**Confidence:** {claude_confidence}

{claude_summary}

{"**Flags:**" + chr(10) + chr(10).join(f"- {f}" for f in claude_flags) if claude_flags else ""}

## Traders signalling

{chr(10).join(f"- {t}" for t in traders)}
"""


def daily_report_note(
    summary_text: str,
    trades_count: int,
    skipped_count: int,
    capital: float,
    report_date: Optional[date] = None,
) -> str:
    d = report_date or date.today()

    return f"""\
---
date: {d.isoformat()}
trades_placed: {trades_count}
trades_skipped: {skipped_count}
capital: {capital:.2f}
tags: ["daily-report"]
---

# Daily Report — {d.isoformat()}

**Capital:** ${capital:,.2f}
**Trades placed:** {trades_count}
**Trades skipped:** {skipped_count}

## JARVIS Briefing

{summary_text}
"""


def dashboard_note() -> str:
    return """\
---
tags: ["dashboard"]
---

# JARVIS Trading Dashboard

> *Last updated automatically by the bot.*

## Total P&L

```dataview
TABLE WITHOUT ID round(sum(rows.pnl), 2) as "Realized P&L ($)", length(rows) as "Closed Trades"
FROM "Trades"
WHERE outcome != "open"
GROUP BY true
```

## Active Positions

```dataview
TABLE market_title, entry_price, position_size, claude_confidence, traders
FROM "Trades"
WHERE outcome = "open"
SORT date DESC
```

## Recent Trades (30 days)

```dataview
TABLE market_title, date, action, conviction, kelly_fraction, entry_price, pnl, claude_confidence
FROM "Trades"
SORT date DESC
LIMIT 30
```

## Skipped Trades (Today)

```dataview
TABLE market_title, skip_reason, claude_confidence, conviction
FROM "Skipped"
WHERE date = date(today)
SORT file.mtime DESC
LIMIT 20
```

## JARVIS Flags (Today)

```dataview
TABLE market_title, claude_flags
FROM "Trades" OR "Skipped"
WHERE claude_flags != [] AND date = date(today)
SORT file.mtime DESC
```

## Daily Reports

```dataview
TABLE date, trades_placed, trades_skipped, capital
FROM "Daily Reports"
SORT date DESC
```
"""
