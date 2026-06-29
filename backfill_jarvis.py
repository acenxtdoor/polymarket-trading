#!/usr/bin/env python3
"""
backfill_jarvis.py
==================
Re-runs JARVIS on every Obsidian note that currently shows "Claude API error"
in its claude_flags and patches the note in-place.

Run from the project root:
    python backfill_jarvis.py
"""

import os
import re
import sys
from pathlib import Path

# ── Bootstrap: load .env and add project root to path ─────────────────────────

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def _load_dotenv(path: str = ".env") -> None:
    p = ROOT / path
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        k, v = key.strip(), val.strip().strip('"').strip("'")
        if not os.environ.get(k):
            os.environ[k] = v


_load_dotenv()

import config
from intelligence.analyst import assess_market


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fmt_list(items: list[str]) -> str:
    if not items:
        return "[]"
    escaped = [s.replace("\\", "\\\\").replace('"', '\\"') for s in items]
    return "[" + ", ".join(f'"{e}"' for e in escaped) + "]"


def patch_note(path: Path, confidence: str, summary: str, flags: list[str]) -> None:
    text = path.read_text(encoding="utf-8")

    # 1. Patch YAML frontmatter
    text = re.sub(
        r'claude_confidence: "[^"]*"',
        f'claude_confidence: "{confidence}"',
        text,
    )
    text = re.sub(
        r'claude_flags: \[.*?\]',
        f'claude_flags: {_fmt_list(flags)}',
        text,
    )

    # 2. Patch body — replace from **Confidence:** to the next ## header (or EOF)
    flags_body = ""
    if flags:
        flags_body = "\n\n**Flags:**\n" + "\n".join(f"- {f}" for f in flags)
    new_jarvis_block = f"**Confidence:** {confidence}\n\n{summary}{flags_body}"

    text = re.sub(
        r"\*\*Confidence:\*\*.*?(?=\n## |\Z)",
        new_jarvis_block,
        text,
        flags=re.DOTALL,
    )

    path.write_text(text, encoding="utf-8")


def extract_field(text: str, field: str, default: str = "") -> str:
    m = re.search(rf"^{re.escape(field)}: (.+)$", text, re.MULTILINE)
    return m.group(1).strip().strip('"') if m else default


def extract_float(text: str, field: str, default: float = 0.5) -> float:
    m = re.search(rf"^{re.escape(field)}: ([\d.]+)", text, re.MULTILINE)
    try:
        return float(m.group(1)) if m else default
    except (ValueError, TypeError):
        return default


def extract_int(text: str, field: str, default: int = 1) -> int:
    m = re.search(rf"^{re.escape(field)}: (\d+)", text, re.MULTILINE)
    try:
        return int(m.group(1)) if m else default
    except (ValueError, TypeError):
        return default


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    key = config.ANTHROPIC_API_KEY
    if key:
        print(f"API key loaded: {key[:8]}...{key[-4:]} (len={len(key)})")
    else:
        print("ERROR: ANTHROPIC_API_KEY is empty — .env not loading correctly")
        return

    vault = Path(config.OBSIDIAN_VAULT_PATH)
    folders = [vault / "Trades", vault / "Skipped"]

    candidates = []
    for folder in folders:
        if not folder.exists():
            continue
        for p in sorted(folder.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            if "Claude API error" in text:
                candidates.append(p)

    if not candidates:
        print("No notes with 'Claude API error' found. Nothing to do.")
        return

    print(f"Found {len(candidates)} note(s) to backfill.\n")

    ok = failed = 0
    for path in candidates:
        text = path.read_text(encoding="utf-8")
        market_id    = extract_field(text, "market_id")
        market_title = extract_field(text, "market_title")
        kalshi       = extract_field(text, "kalshi_signal", "no_match")
        conviction   = extract_int(text, "conviction", 1)
        price        = (
            extract_float(text, "entry_price")
            or extract_float(text, "current_price", 0.5)
        )

        print(f"  → {path.name}")
        print(f"     title={market_title!r}  price={price:.3f}  conviction={conviction}")

        assessment = assess_market(
            market_id=market_id,
            market_title=market_title,
            yes_price=price,
            articles=[],           # retrospective — no live news fetch
            trader_count=conviction,
            avg_trader_price=price,
            kalshi_signal=kalshi,
        )

        if "Claude API error" in assessment.flags:
            print(f"     ✗ JARVIS still failing — {assessment.summary}\n")
            failed += 1
            continue

        patch_note(path, assessment.confidence, assessment.summary, assessment.flags)
        print(f"     ✓ {assessment.confidence}  flags={assessment.flags}\n")
        ok += 1

    print(f"\nDone. {ok} patched, {failed} still failing.")


if __name__ == "__main__":
    main()
