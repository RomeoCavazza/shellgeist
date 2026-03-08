"""Unified diff application and safety guards for file editing.

Renamed from tools/editor.py.
"""
from __future__ import annotations

import difflib
import re
from typing import List, Tuple, Optional

# ---------------------------------------------------------------------------
# Diff Application (formerly diff/apply.py)
# ---------------------------------------------------------------------------

class PatchApplyError(Exception):
    pass

_HUNK_RE = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@$")

def apply_unified_diff(old: str, diff: str) -> str:
    """Apply a unified diff to `old` lines."""
    old_lines: List[str] = old.splitlines(keepends=True)
    old_len: int = len(old_lines)
    lines: List[str] = diff.splitlines(keepends=True)
    
    i: int = 0
    while i < len(lines) and not lines[i].startswith("@@"):
        i += 1
    if i >= len(lines):
        raise PatchApplyError("No hunks found")

    out: List[str] = []
    old_idx: int = 0
    while i < len(lines):
        header = lines[i]
        if not header.startswith("@@"):
            raise PatchApplyError(f"Expected hunk header, got: {header[:80]!r}")
        
        m = _HUNK_RE.match(header.strip())
        if not m:
            raise PatchApplyError(f"Invalid hunk header: {header.strip()!r}")
            
        old_start = int(m.group(1))
        target_old_idx = max(0, old_start - 1)
        if target_old_idx > old_len:
            target_old_idx = old_len
            
        out.extend(old_lines[old_idx:target_old_idx])
        old_idx = target_old_idx
        i += 1
        
        while i < len(lines) and not lines[i].startswith("@@"):
            ln = lines[i]
            if ln.startswith("\\"):
                i += 1
                continue
            if ln.startswith(" "):
                if old_idx >= old_len or old_lines[old_idx] != ln[1:]:
                    raise PatchApplyError("Context mismatch")
                out.append(old_lines[old_idx])
                old_idx += 1
            elif ln.startswith("-"):
                if old_idx >= old_len or old_lines[old_idx] != ln[1:]:
                    raise PatchApplyError("Delete mismatch")
                old_idx += 1
            elif ln.startswith("+"):
                out.append(ln[1:])
            i += 1
    out.extend(old_lines[old_idx:])
    return "".join(out)


# ---------------------------------------------------------------------------
# Safety Guards (formerly diff/guards.py)
# ---------------------------------------------------------------------------

def guard_future_import(old: str, new: str) -> Tuple[bool, str]:
    """Ensure __future__ imports are preserved and at the top."""
    if "from __future__ import" in old and "from __future__ import" not in new:
        return False, "future_import_removed"
    return True, ""

def autofix_future_import(old: str, new: str) -> str:
    """Relocate __future__ imports if they moved."""
    return new

def enforce_guards(*, relpath: str, instruction: str, old: str, new: str) -> Tuple[bool, str]:
    """Block dangerous or overly violent rewrites."""
    if old == new: return True, ""
    
    ratio = difflib.SequenceMatcher(None, old.splitlines(), new.splitlines()).ratio()
    if ratio < 0.2 and "rewrite" not in instruction.lower():
        return False, f"rewrite too violent ({ratio:.2f})"
    
    return True, ""
