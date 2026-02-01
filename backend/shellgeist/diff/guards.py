from __future__ import annotations

import difflib
from pathlib import Path
from typing import Tuple


_TRIPLE_DQ = '"' * 3
_TRIPLE_SQ = "'" * 3


def _has_control_chars(s: str) -> bool:
    """
    Block ASCII control characters except common whitespace: \\n, \\r, \\t.
    """
    for ch in s:
        o = ord(ch)
        if o < 32 and ch not in ("\n", "\r", "\t"):
            return True
    return False


def _has_future_import(text: str) -> bool:
    for ln in (text or "").splitlines():
        if ln.lstrip().startswith("from __future__ import"):
            return True
    return False


def _future_import_is_in_allowed_region(new: str) -> bool:
    """
    Python rule: future imports must appear near the top:
    - may be preceded by: blank lines, comments, module docstring
    - NOT preceded by: normal imports/statements/assignments/etc.
    """
    lines = (new or "").splitlines()
    i = 0

    # blank lines + comments
    while i < len(lines):
        s = lines[i]
        if s.strip() == "":
            i += 1
            continue
        if s.lstrip().startswith("#"):
            i += 1
            continue
        break

    # optional module docstring
    if i < len(lines):
        s0 = lines[i].lstrip()
        if s0.startswith(_TRIPLE_DQ) or s0.startswith(_TRIPLE_SQ):
            q = _TRIPLE_DQ if s0.startswith(_TRIPLE_DQ) else _TRIPLE_SQ

            # opening+closing on same line
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
    while i < len(lines) and lines[i].strip() == "":
        i += 1

    return i < len(lines) and lines[i].lstrip().startswith("from __future__ import")


def _guard_future_import(old: str, new: str) -> Tuple[bool, str]:
    """
    Only enforce if the *old* file had a future import.
    """
    if not _has_future_import(old):
        return True, ""

    if not _has_future_import(new):
        return False, "future_import_removed"

    if not _future_import_is_in_allowed_region(new):
        return False, "future_import_moved"

    return True, ""


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


def enforce_guards(*, relpath: str, instruction: str, old: str, new: str) -> Tuple[bool, str]:
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
    ok_fut, why_fut = _guard_future_import(old, new)
    if not ok_fut:
        head = "\n".join((new or "").splitlines()[:40])
        print(
            "[ShellGeist][guards] future import blocked:",
            why_fut,
            "\n--- NEW head ---\n",
            head,
            "\n--- end ---",
            sep="",
        )
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
