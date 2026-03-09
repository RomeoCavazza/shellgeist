"""Shared loading of project rules (.shellgeist.md, .shellgeist/rules.md)."""
from __future__ import annotations

from pathlib import Path


def load_project_rules(root: str) -> str:
    """Load project rules from the first existing candidate under root.

    Candidates in order: .shellgeist.md, .shellgeist/rules.md.
    Returns the content of the first file found, or empty string if none exist.
    """
    base = Path(root).resolve()
    candidates = [
        base / ".shellgeist.md",
        base / ".shellgeist" / "rules.md",
    ]
    for path in candidates:
        if path.exists() and path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except Exception:
                pass
    return ""
