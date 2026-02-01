from __future__ import annotations

import re


class PatchApplyError(Exception):
    pass


_HUNK_RE = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@$")


def _split_lines_keepends(s: str) -> list[str]:
    return s.splitlines(keepends=True)


def apply_unified_diff(old: str, diff: str) -> str:
    """
    Apply a unified diff (diff -u style) to `old` and return the new content.

    Tolerates:
    - leading headers/noise (---/+++ etc.) until first @@ hunk
    - hunks that point after EOF => clamped (insertion at end)

    Rejects:
    - empty patch (no hunks)
    - hunk headers with no body lines
    """
    old_lines = _split_lines_keepends(old)
    old_len = len(old_lines)

    lines = diff.splitlines(keepends=True)
    i = 0

    # Skip file headers / noise until first hunk
    while i < len(lines) and not lines[i].startswith("@@"):
        i += 1

    if i >= len(lines):
        raise PatchApplyError("Invalid patch: no hunks found")

    out: list[str] = []
    old_idx = 0

    while i < len(lines):
        header = lines[i]
        if not header.startswith("@@"):
            raise PatchApplyError(f"Invalid patch: expected hunk header, got: {header[:80]!r}")

        m = _HUNK_RE.match(header.strip())
        if not m:
            raise PatchApplyError(f"Invalid hunk header: {header.strip()!r}")

        old_start = int(m.group(1))  # 1-based, can be 0 in some generators
        target_old_idx = max(0, old_start - 1)

        # Clamp to EOF
        if target_old_idx > old_len:
            target_old_idx = old_len

        # Copy unchanged chunk before hunk
        if target_old_idx < old_idx:
            raise PatchApplyError("Patch context out of range (target before current index)")
        out.extend(old_lines[old_idx:target_old_idx])
        old_idx = target_old_idx

        i += 1

        # Apply hunk body
        hunk_lines = 0
        while i < len(lines) and not lines[i].startswith("@@"):
            ln = lines[i]

            # '\ No newline at end of file' marker: ignore
            if ln.startswith("\\"):
                i += 1
                continue

            hunk_lines += 1

            if ln.startswith(" "):  # context
                if old_idx >= old_len:
                    raise PatchApplyError("Patch context out of range (EOF)")
                expected = ln[1:]
                got = old_lines[old_idx]
                if got != expected:
                    raise PatchApplyError("Patch context mismatch")
                out.append(got)
                old_idx += 1

            elif ln.startswith("-"):  # delete
                if old_idx >= old_len:
                    raise PatchApplyError("Patch delete out of range (EOF)")
                expected = ln[1:]
                got = old_lines[old_idx]
                if got != expected:
                    raise PatchApplyError("Patch delete mismatch")
                old_idx += 1

            elif ln.startswith("+"):  # add
                out.append(ln[1:])

            else:
                raise PatchApplyError(f"Invalid hunk line: {ln[:80]!r}")

            i += 1

        if hunk_lines == 0:
            raise PatchApplyError("Empty hunk body")

    out.extend(old_lines[old_idx:])
    return "".join(out)
