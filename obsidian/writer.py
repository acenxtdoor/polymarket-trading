"""
Obsidian vault writer.

Folder layout inside the vault:
  Markets/        — one note per market (not yet traded, just assessed)
  Trades/         — placed trade notes
  Skipped/        — skipped trade notes
  Daily Reports/  — one note per run date
  Dashboard.md    — top-level Dataview dashboard (created once)
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

import config
from obsidian import templates


def _vault() -> Path:
    return Path(config.OBSIDIAN_VAULT_PATH)


def _safe_filename(title: str) -> str:
    title = re.sub(r'[\\/*?:"<>|]', "", title)
    title = title.strip().replace(" ", "-")
    return title[:80]


def _write(folder: str, filename: str, content: str) -> Path:
    folder_path = _vault() / folder
    folder_path.mkdir(parents=True, exist_ok=True)
    note_path = folder_path / filename
    note_path.write_text(content, encoding="utf-8")
    return note_path


def ensure_vault() -> None:
    vault = _vault()
    for folder in ("Trades", "Skipped", "Daily Reports", "Markets"):
        (vault / folder).mkdir(parents=True, exist_ok=True)

    dashboard = vault / "Dashboard.md"
    if not dashboard.exists():
        dashboard.write_text(templates.dashboard_note(), encoding="utf-8")


def write_trade(
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
) -> Path:
    d = trade_date or date.today()
    filename = f"{d.isoformat()}-{_safe_filename(market_title)}.md"
    content = templates.trade_note(
        market_id=market_id,
        market_title=market_title,
        action=action,
        conviction=conviction,
        kalshi_signal=kalshi_signal,
        kelly_fraction=kelly_fraction,
        position_size=position_size,
        entry_price=entry_price,
        claude_confidence=claude_confidence,
        claude_summary=claude_summary,
        claude_flags=claude_flags,
        traders=traders,
        outcome=outcome,
        pnl=pnl,
        tags=tags,
        trade_date=d,
    )
    return _write("Trades", filename, content)


def write_skip(
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
) -> Path:
    d = skip_date or date.today()
    filename = f"{d.isoformat()}-{_safe_filename(market_title)}.md"
    content = templates.skip_note(
        market_id=market_id,
        market_title=market_title,
        skip_reason=skip_reason,
        conviction=conviction,
        kalshi_signal=kalshi_signal,
        claude_confidence=claude_confidence,
        claude_summary=claude_summary,
        claude_flags=claude_flags,
        traders=traders,
        skip_date=d,
    )
    return _write("Skipped", filename, content)


def write_daily_report(
    summary_text: str,
    trades_count: int,
    skipped_count: int,
    bankroll: float,
    report_date: Optional[date] = None,
) -> Path:
    d = report_date or date.today()
    filename = f"{d.isoformat()}.md"
    content = templates.daily_report_note(
        summary_text=summary_text,
        trades_count=trades_count,
        skipped_count=skipped_count,
        bankroll=bankroll,
        report_date=d,
    )
    return _write("Daily Reports", filename, content)


def update_trade_outcome(
    market_title: str,
    outcome: str,
    pnl: float,
    trade_date: Optional[date] = None,
) -> bool:
    d = trade_date or date.today()
    filename = f"{d.isoformat()}-{_safe_filename(market_title)}.md"
    note_path = _vault() / "Trades" / filename

    if not note_path.exists():
        return False

    text = note_path.read_text(encoding="utf-8")
    text = re.sub(r'outcome: "open"', f'outcome: "{outcome}"', text)
    text = re.sub(r'pnl: null', f'pnl: {pnl:.2f}', text)
    text = re.sub(r'\*\*Status:\*\* open', f'**Status:** {outcome}', text)
    text = re.sub(r'\*\*P&L:\*\* —', f'**P&L:** ${pnl:,.2f}', text)
    note_path.write_text(text, encoding="utf-8")
    return True
