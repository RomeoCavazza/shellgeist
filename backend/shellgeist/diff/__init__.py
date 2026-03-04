"""Unified diff application and safety guards."""

from shellgeist.diff.apply import PatchApplyError, apply_unified_diff
from shellgeist.diff.guards import autofix_future_import, enforce_guards, guard_future_import

__all__ = [
    "PatchApplyError",
    "apply_unified_diff",
    "autofix_future_import",
    "enforce_guards",
    "guard_future_import",
]
