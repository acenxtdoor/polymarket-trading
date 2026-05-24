"""
scripts/install_hooks.py
========================
Installs git hooks for the polymarket-trading repo.

Hooks installed:
  post-merge — runs sync_vault.py automatically after every `git pull`

Run once per machine:
    python scripts/install_hooks.py
"""

import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = ROOT / ".git" / "hooks"

POST_MERGE_HOOK = HOOKS_DIR / "post-merge"

# Use the same Python executable that's running this script
PYTHON = sys.executable.replace("\\", "/")
SYNC_SCRIPT = (ROOT / "scripts" / "sync_vault.py").as_posix()

HOOK_CONTENT = f"""\
#!/bin/sh
# Auto-syncs Obsidian vault after git pull.
# Installed by scripts/install_hooks.py — do not edit manually.
"{PYTHON}" "{SYNC_SCRIPT}"
"""


def install():
    if not HOOKS_DIR.exists():
        print(f"[install_hooks] .git/hooks not found at {HOOKS_DIR}")
        print("  Are you running this from the repo root?")
        sys.exit(1)

    POST_MERGE_HOOK.write_text(HOOK_CONTENT, encoding="utf-8")

    # Make executable (needed on Linux/macOS; harmless on Windows)
    current = POST_MERGE_HOOK.stat().st_mode
    POST_MERGE_HOOK.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"[install_hooks] post-merge hook installed at {POST_MERGE_HOOK}")
    print("  Dashboard and CSS will now sync automatically after every `git pull`.")


if __name__ == "__main__":
    install()
