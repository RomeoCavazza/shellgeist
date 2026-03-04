"""Unified repo-path resolution for all tools."""
from __future__ import annotations

from pathlib import Path


def resolve_repo_path(root: Path, rel: str) -> Path:
    """Resolve *rel* inside *root*, blocking absolute paths and ``~``.

    All tool modules should use this single implementation to guarantee
    consistent security semantics (no path-escape, no ``~`` expansion
    inside the tool layer).
    """
    if not rel or rel.startswith(("/", "~")):
        raise ValueError("invalid_path")
    p = (root / rel).resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        raise ValueError("path_escape")
    return p
