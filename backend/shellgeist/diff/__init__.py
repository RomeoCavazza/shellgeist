"""Unified diff application and safety guards."""

from shellgeist.diff.apply import PatchApplyError, apply_unified_diff
from shellgeist.diff.guards import enforce_guards

__all__ = [
    "PatchApplyError",
    "apply_unified_diff",
    "enforce_guards",
]
