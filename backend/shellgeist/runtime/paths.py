"""Unified repo-path resolution for all tools."""
from __future__ import annotations

from pathlib import Path


def read_repo_file(path: Path) -> str:
    """Read file content as UTF-8, replacing invalid bytes.

    Use this for any file under the repo so behavior is consistent across tools.
    """
    return path.read_text(encoding="utf-8", errors="replace")


def resolve_repo_path(root: Path | str, rel: str) -> Path:
    """Resolve *rel* inside *root*, ensuring it stays within the workspace.

    *root* may be a Path or a path string.
    If *rel* is a path suffix of the absolute root (e.g. root is
    /home/user/Bureau/projets/shellgeist and rel is "Bureau/projets/shellgeist"),
    it is normalized to "." so that "describe Bureau/projets/shellgeist" works
    when the workspace is that directory.
    Rules:
    - Home-relative paths (~/) are expanded.
    - Relative paths are resolved against *root*.
    - Absolute paths must already live under *root*; anything outside is rejected
      with a clear error encouraging RELATIVE paths instead.
    """
    if not rel:
        raise ValueError("invalid_path: empty path")

    root = Path(root).resolve() if isinstance(root, str) else root.resolve()
    rel_stripped = rel.strip().rstrip("/")
    if rel_stripped:
        root_parts = root.parts
        rel_parts = Path(rel_stripped).expanduser().parts
        if rel_parts and len(rel_parts) <= len(root_parts):
            if root_parts[-len(rel_parts) :] == rel_parts:
                rel = "."
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
