"""
scripts/clear_dashboard.py
==========================
Wipe all bot-generated notes from the Obsidian vault and optionally reset
the paper-trading portfolio back to its initial capital.

Usage:
    python scripts/clear_dashboard.py            # clears vault only
    python scripts/clear_dashboard.py --reset-portfolio  # also resets portfolio.json

What gets deleted:
    Trades/         — all trade notes
    Skipped/        — all skip notes
    Daily Reports/  — all daily report notes
    Dashboard.md    — regenerated fresh (so Dataview queries start clean)

What is NOT touched:
    .obsidian/      — your Obsidian config, plugins, CSS snippets
    Markets/        — any manually created market notes
    portfolio.json  — unless --reset-portfolio is passed
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# Allow running from project root or scripts/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from obsidian import templates


CLEARABLE_FOLDERS = ("Trades", "Skipped", "Daily Reports")


def _confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def clear_vault(vault: Path, dry_run: bool = False) -> None:
    total_deleted = 0

    for folder_name in CLEARABLE_FOLDERS:
        folder = vault / folder_name
        if not folder.exists():
            print(f"  [SKIP] {folder_name}/ does not exist")
            continue

        notes = list(folder.glob("*.md"))
        if not notes:
            print(f"  [SKIP] {folder_name}/ is already empty")
            continue

        print(f"  {'[DRY RUN] Would delete' if dry_run else 'Deleting'} "
              f"{len(notes)} note(s) from {folder_name}/")
        if not dry_run:
            for note in notes:
                note.unlink()
        total_deleted += len(notes)

    # Regenerate Dashboard.md so Dataview starts fresh
    dashboard = vault / "Dashboard.md"
    if dry_run:
        print(f"  [DRY RUN] Would regenerate Dashboard.md")
    else:
        dashboard.write_text(templates.dashboard_note(), encoding="utf-8")
        print(f"  Regenerated Dashboard.md")

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Cleared {total_deleted} note(s) from vault.")


def reset_portfolio(dry_run: bool = False) -> None:
    path = Path(config.PORTFOLIO_PATH)
    data = {
        "capital": config.INITIAL_CAPITAL,
        "realized_pnl": 0.0,
        "positions": {},
    }
    if dry_run:
        print(f"  [DRY RUN] Would reset {path} → capital=${config.INITIAL_CAPITAL:,.2f}, no positions")
    else:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"  Reset {path} → capital=${config.INITIAL_CAPITAL:,.2f}, 0 positions")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear the JARVIS Obsidian dashboard.")
    parser.add_argument(
        "--reset-portfolio",
        action="store_true",
        help="Also reset portfolio.json to the initial capital (erases all paper P&L).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting anything.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    args = parser.parse_args()

    vault = Path(config.OBSIDIAN_VAULT_PATH)
    if not vault.exists():
        print(f"ERROR: Vault not found at {vault}")
        sys.exit(1)

    print("JARVIS Dashboard Clear")
    print("=" * 40)
    print(f"Vault:     {vault}")
    print(f"Portfolio: {config.PORTFOLIO_PATH}")
    print(f"Folders:   {', '.join(CLEARABLE_FOLDERS)}")
    if args.reset_portfolio:
        print(f"Portfolio reset: YES → ${config.INITIAL_CAPITAL:,.2f}")
    if args.dry_run:
        print("Mode: DRY RUN (no files will be changed)")
    print()

    if not args.dry_run and not args.yes:
        if not _confirm("This will permanently delete all trade, skip, and daily report notes. Continue?"):
            print("Aborted.")
            sys.exit(0)

    print()
    clear_vault(vault, dry_run=args.dry_run)

    if args.reset_portfolio:
        print()
        reset_portfolio(dry_run=args.dry_run)

    print("\nDone. Restart Obsidian or reload the vault to see the clean dashboard.")


if __name__ == "__main__":
    main()
