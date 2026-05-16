"""
Update the 'Running tests' section of README.md to reflect all files in tests/.

Can be run directly:
    python scripts/update_readme_tests.py

Or via Claude Code PostToolUse hook (reads JSON from stdin to filter by file path).
"""
import glob
import json
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README_PATH = os.path.join(PROJECT_ROOT, "README.md")
TESTS_DIR = os.path.join(PROJECT_ROOT, "tests")


def should_run() -> bool:
    """When called from a hook, only run if the written file is a test file."""
    if sys.stdin.isatty():
        return True  # Called directly — always run
    try:
        data = json.load(sys.stdin)
        file_path = data.get("tool_input", {}).get("file_path", "")
        return bool(re.search(r"tests[/\\]test_.*\.py$", file_path))
    except (json.JSONDecodeError, KeyError, ValueError):
        return False


def get_test_files() -> list[str]:
    pattern = os.path.join(TESTS_DIR, "test_*.py")
    files = sorted(glob.glob(pattern))
    return [os.path.relpath(f, PROJECT_ROOT).replace("\\", "/") for f in files]


def build_section(test_files: list[str]) -> str:
    if not test_files:
        return "## Running tests\n\nNo test files yet.\n"
    lines = ["## Running tests\n\n"]
    for f in test_files:
        lines.append(f"```bash\npython {f}\n```\n\n")
    return "".join(lines).rstrip() + "\n"


def update_readme(test_files: list[str]) -> bool:
    with open(README_PATH, "r", encoding="utf-8") as fh:
        content = fh.read()

    new_section = build_section(test_files)
    new_content = re.sub(
        r"## Running tests\n.*?(?=\n## |\Z)",
        new_section,
        content,
        flags=re.DOTALL,
    )

    if new_content == content:
        return False

    with open(README_PATH, "w", encoding="utf-8") as fh:
        fh.write(new_content)
    return True


if __name__ == "__main__":
    if not should_run():
        sys.exit(0)

    test_files = get_test_files()
    changed = update_readme(test_files)

    if changed:
        print(f"README.md updated — {len(test_files)} test file(s): {test_files}")
    else:
        print("README.md already up to date")
