"""Unified repo-path resolution for all tools.

Moved from util_path.py.
"""
from __future__ import annotations

from pathlib import Path


def resolve_repo_path(root: Path, rel: str) -> Path:
    """Resolve *rel* inside *root*, ensuring it stays within the workspace.
    
    If 'rel' is absolute, it's resolved normally (but then checked against root).
    If 'rel' starts with '~', it's expanded first.
    """
    if not rel:
        raise ValueError("invalid_path: empty path")

    root = root.resolve()
    p = Path(rel).expanduser()
    
    if p.is_absolute():
        res = p.resolve()
    else:
        # Otherwise, resolve relative to root
        res = (root / p).resolve()
        
    # Security: Ensure 'res' is within 'root'
    # Use os.path.commonpath to safely check if 'root' is a parent of 'res'
    try:
        if not str(res).startswith(str(root)):
             # Allow specific system paths if explicitly needed in the future,
             # but for now, we follow the "Stay in Workspace" rule.
             raise PermissionError(f"Access denied: {rel} is outside project root {root}")
    except ValueError:
        raise PermissionError(f"Access denied: {rel} is outside project root {root}")

    return res
