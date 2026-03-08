"""Unified repo-path resolution for all tools."""
from __future__ import annotations

from pathlib import Path


def resolve_repo_path(root: Path, rel: str) -> Path:
    """Resolve *rel* inside *root*, ensuring it stays within the workspace.

    Auto-corrects common mistakes:
    - Absolute paths outside root are retried as relative (e.g. /Arduino → root/Arduino)
    - Home-relative paths (~/) are expanded
    """
    if not rel:
        raise ValueError("invalid_path: empty path")

    root = root.resolve()
    p = Path(rel).expanduser()

    if p.is_absolute():
        res = p.resolve()
        if not res.is_relative_to(root):
            # Auto-correct: treat basename as relative to root
            # Preserve full directory structure: /tasks/foo.py → root/tasks/foo.py
            rel = str(p).lstrip("/")
            corrected = (root / rel).resolve()
            if corrected.is_relative_to(root):
                return corrected
            raise PermissionError(
                f"Access denied: {rel} is outside project root {root}. "
                f"Use a relative path like '{p.name}' instead."
            )
        return res

    res = (root / p).resolve()
    if not res.is_relative_to(root):
        raise PermissionError(
            f"Access denied: {rel} resolves outside project root {root}."
        )
    return res
