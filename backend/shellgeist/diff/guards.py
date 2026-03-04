"""Pre- and post-patch safety guards for diff application."""
from __future__ import annotations

import difflib
import os
from pathlib import Path

_TRIPLE_DQ = '"' * 3
_TRIPLE_SQ = "'" * 3

_BOM_ZW = "\ufeff\u200b"


def _strip_bom_zw(s: str) -> str:
    return (s or "").lstrip(_BOM_ZW)


def _is_effectively_blank(s: str) -> bool:
    return _strip_bom_zw(s).strip() == ""


def _guard_trace(msg: str) -> None:
    if os.environ.get("SHELLGEIST_TRACE") == "1":
        print(f"[ShellGeist][guards] {msg}", flush=True)


def _has_future_import(text: str) -> bool:
    for ln in (text or "").splitlines():
        if _strip_bom_zw(ln).lstrip().startswith("from __future__ import"):
            return True
    return False


def _future_import_is_in_allowed_region(new: str) -> bool:
    """
    Python rule: future imports must appear near the top:
    - may be preceded by: blank lines, comments, module docstring
    - NOT preceded by: normal imports/statements/assignments/etc.

    Handles BOM / zero-width chars that LLMs sometimes inject.
    """
    lines = (new or "").splitlines()
    i = 0

    # blank lines + comments
    while i < len(lines):
        s = lines[i]
        if _is_effectively_blank(s):
            i += 1
            continue
        if _strip_bom_zw(s).lstrip().startswith("#"):
            i += 1
            continue
        break

    # optional module docstring
    if i < len(lines):
        s0 = _strip_bom_zw(lines[i]).lstrip()
        if s0.startswith(_TRIPLE_DQ) or s0.startswith(_TRIPLE_SQ):
            q = _TRIPLE_DQ if s0.startswith(_TRIPLE_DQ) else _TRIPLE_SQ
            if s0.count(q) >= 2:
                i += 1
            else:
                i += 1
                while i < len(lines):
                    if q in lines[i]:
                        i += 1
                        break
                    i += 1

    # blank lines after docstring
    while i < len(lines) and _is_effectively_blank(lines[i]):
        i += 1

    return i < len(lines) and _strip_bom_zw(lines[i]).lstrip().startswith("from __future__ import")


def guard_future_import(old: str, new: str) -> tuple[bool, str]:
    """Only enforce if the *old* file had a future import.

    Returns ``(ok, reason)`` — when *ok* is ``False``, *reason* explains
    what went wrong (``future_import_removed`` or ``future_import_moved``).
    """
    if not _has_future_import(old):
        return True, ""

    if not _has_future_import(new):
        return False, "future_import_removed"

    if not _future_import_is_in_allowed_region(new):
        new_lines = (new or "").splitlines()
        first_idx = next(
            (idx for idx, ln in enumerate(new_lines)
             if _strip_bom_zw(ln).lstrip().startswith("from __future__ import")),
            -1,
        )
        _guard_trace(
            "future_import_guard BLOCKED: future_import_moved "
            f"(expected at top, found first at line {first_idx + 1})"
        )
        return False, "future_import_moved"

    return True, ""


def autofix_future_import(old: str, new: str) -> str:
    """Try to move ``from __future__`` back to the legal position.

    If the *old* file had future imports and the LLM displaced or removed
    them in *new*, attempt to relocate them.  Returns *new* unchanged when
    no fix is possible or necessary.
    """
    old_lines = (old or "").splitlines(keepends=True)
    old_future = [
        _strip_bom_zw(ln).lstrip()
        for ln in old_lines
        if _strip_bom_zw(ln).lstrip().startswith("from __future__ import")
    ]
    if not old_future:
        return new

    new_lines = (new or "").splitlines(keepends=True)
    if not new_lines:
        return new

    def _find_insert_point(lines: list[str]) -> int:
        """Skip comments, blanks, and docstring to find the insertion point."""
        j = 0
        while j < len(lines):
            s = lines[j]
            if _is_effectively_blank(s):
                j += 1
                continue
            if _strip_bom_zw(s).lstrip().startswith("#"):
                j += 1
                continue
            break

        if j < len(lines):
            s0 = _strip_bom_zw(lines[j]).lstrip()
            if s0.startswith(_TRIPLE_DQ) or s0.startswith(_TRIPLE_SQ):
                q = _TRIPLE_DQ if s0.startswith(_TRIPLE_DQ) else _TRIPLE_SQ
                if s0.count(q) >= 2:
                    j += 1
                else:
                    j += 1
                    while j < len(lines):
                        if q in lines[j]:
                            j += 1
                            break
                        j += 1
        return j

    fut_idx = [
        i for i, ln in enumerate(new_lines)
        if _strip_bom_zw(ln).lstrip().startswith("from __future__ import")
    ]

    if not fut_idx:
        # Future imports removed — re-insert from old
        insert = _find_insert_point(new_lines)
        fixed = "".join(new_lines[:insert] + old_future + new_lines[insert:])
        ok, _ = guard_future_import(old, fixed)
        return fixed if ok else new

    ok, why = guard_future_import(old, new)
    if ok:
        return new
    if why != "future_import_moved":
        return new

    # Future imports present but displaced — relocate them
    fut_set = set(fut_idx)
    fut_lines = [_strip_bom_zw(new_lines[i]).lstrip() for i in fut_idx]
    rest = [ln for j, ln in enumerate(new_lines) if j not in fut_set]

    insert = _find_insert_point(rest)
    fixed = "".join(rest[:insert] + fut_lines + rest[insert:])
    ok2, _ = guard_future_import(old, fixed)
    return fixed if ok2 else new


def _has_control_chars(s: str) -> bool:
    """Block ASCII control characters except common whitespace."""
    for ch in s:
        o = ord(ch)
        if o < 32 and ch not in ("\n", "\r", "\t"):
            return True
    return False


def _normalize_for_similarity(s: str) -> str:
    """
    Reduce false positives due to formatting-only changes.
    - normalize newlines
    - strip trailing whitespace per line
    - compress runs of blank lines to a single blank line
    - strip leading/trailing blank space overall
    """
    s = s or ""
    s = s.replace("\r\n", "\n").replace("\r", "\n")

    lines = [ln.rstrip() for ln in s.splitlines()]
    out: list[str] = []
    blank_run = 0
    for ln in lines:
        if ln.strip() == "":
            blank_run += 1
            if blank_run <= 1:
                out.append("")
            continue
        blank_run = 0
        out.append(ln)

    return "\n".join(out).strip()


def _similarity_ratio(old: str, new: str) -> float:
    """
    Line-based similarity is much more stable than raw char similarity for code,
    especially under reformatting.
    """
    a = _normalize_for_similarity(old)
    b = _normalize_for_similarity(new)
    return difflib.SequenceMatcher(a=a.splitlines(), b=b.splitlines()).ratio()


def _allow_big_rewrite(instruction: str) -> bool:
    s = (instruction or "").lower()
    keywords = (
        "rewrite",
        "refactor",
        "reformat",
        "format",
        "overhaul",
        "replace",
        "full",
        "cleanup",
        "clean up",
        "modernize",
    )
    return any(k in s for k in keywords)


def enforce_guards(*, relpath: str, instruction: str, old: str, new: str) -> tuple[bool, str]:
    """
    Guardrails:
    - Block control chars in new content
    - Enforce __future__ placement rules (when file had one)
    - Block violent rewrites unless explicitly requested
    - Special-case: README.md rewrite blocked (stricter + specific message)
    """
    old = old or ""
    new = new or ""

    # 0) control chars
    if _has_control_chars(new):
        return False, "guard: control_chars"

    # no-op allowed
    if old == new:
        return True, ""

    # 1) __future__
    ok_fut, why_fut = guard_future_import(old, new)
    if not ok_fut:
        _guard_trace(f"future import blocked: {why_fut}")
        return False, f"guard: {why_fut}"

    # 2) similarity + README special-case
    ratio = _similarity_ratio(old, new)

    p = Path(relpath)
    is_readme = p.name.lower() == "readme.md"

    # README is protected: if user didn't ask for rewrite/refactor, block large rewrites hard.
    if is_readme and not _allow_big_rewrite(instruction):
        # keep threshold high for README; tests expect a rewrite to be blocked
        if ratio < 0.90:
            return False, "guard: README rewrite blocked"

    # generic rewrite-violence guard
    min_ratio = 0.20
    if not _allow_big_rewrite(instruction) and ratio < min_ratio:
        return False, f"guard: rewrite too violent (similarity={ratio:.2f})"

    return True, ""
