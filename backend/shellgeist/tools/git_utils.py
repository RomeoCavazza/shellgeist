"""Shared git helper used by tools and other modules.

Moved from util_git.py.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def git(root: Path, args: list[str]) -> tuple[int, str]:
    """Run a git command inside *root* and return ``(returncode, stdout)``."""
    p = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return p.returncode, p.stdout
