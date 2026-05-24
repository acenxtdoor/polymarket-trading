"""
scripts/sync_vault.py
=====================
Syncs the vault template files from the repo into the live Obsidian vault.

Files synced:
  repo: vault/Dashboard.md                        → <vault>/Dashboard.md
  repo: vault/.obsidian/snippets/jarvis-nebula.css → <vault>/.obsidian/snippets/jarvis-nebula.css

Run manually:
    python scripts/sync_vault.py

Or automatically via the git post-merge hook (installed by scripts/install_hooks.py).
"""

import shutil
import sys
from pathlib import Path

# Project root is one level up from this script
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config

VAULT = Path(config.OBSIDIAN_VAULT_PATH)

SYNC_PAIRS = [
    (ROOT / "vault" / "Dashboard.md",
     VAULT / "Dashboard.md"),
    (ROOT / "vault" / ".obsidian" / "snippets" / "jarvis-nebula.css",
     VAULT / ".obsidian" / "snippets" / "jarvis-nebula.css"),
]


def sync():
    if not VAULT.exists():
        print(f"[sync_vault] Vault not found at {VAULT} — skipping.")
        return

    synced = 0
    for src, dst in SYNC_PAIRS:
        if not src.exists():
            print(f"[sync_vault] Source missing: {src} — skipping.")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"[sync_vault] {src.relative_to(ROOT)}  ->  {dst}")
        synced += 1

    print(f"[sync_vault] Done — {synced}/{len(SYNC_PAIRS)} file(s) synced.")


if __name__ == "__main__":
    sync()
