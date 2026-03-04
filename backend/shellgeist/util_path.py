"""Unified repo-path resolution for all tools."""
from __future__ import annotations

from pathlib import Path


def resolve_repo_path(root: Path, rel: str) -> Path:
    """Resolve *rel* inside *root*, blocking absolute paths and ``~``.

    All tool modules should use this single implementation to guarantee
    consistent security semantics (no path-escape, no ``~`` expansion
    inside the tool layer).
    """
    if not rel:
        raise ValueError("invalid_path: empty path")
    if rel.startswith("~") or rel.startswith("/"):
        raise ValueError(
            f"invalid_path: '{rel}' is outside the project. "
            "Tools can only access files inside the repo root. "
            "Use run_shell with 'cat', 'sed', or 'cp' to read/edit files outside the project."
        )
    p = (root / rel).resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        raise ValueError("path_escape")
    return p
