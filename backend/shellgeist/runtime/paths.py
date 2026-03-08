"""Unified repo-path resolution for all tools."""
from __future__ import annotations

from pathlib import Path


def resolve_repo_path(root: Path, rel: str) -> Path:
    """Resolve *rel* inside *root*, ensuring it stays within the workspace.

    Rules:
    - Home-relative paths (~/) are expanded.
    - Relative paths are resolved against *root*.
    - Absolute paths must already live under *root*; anything outside is rejected
      with a clear error encouraging RELATIVE paths instead.
    """
    if not rel:
        raise ValueError("invalid_path: empty path")

    root = root.resolve()
    p = Path(rel).expanduser()

    # Absolute path: only allowed if it already lives under the workspace root.
    if p.is_absolute():
        res = p.resolve()
        if not res.is_relative_to(root):
            raise PermissionError(
                f"Access denied: absolute path '{p}' is outside project root {root}. "
                f"Use a relative path from the workspace root instead, for example "
                f"'{p.name}' or '{str(p).lstrip('/')}'."
            )
        return res

    # Relative path: resolve against the workspace root and ensure it stays inside.
    res = (root / p).resolve()
    if not res.is_relative_to(root):
        raise PermissionError(
            f"Access denied: '{rel}' resolves outside project root {root}. "
            "Always use paths that stay within this workspace."
        )
    return res
