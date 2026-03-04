"""Code editing tools: edit_apply, write_file, edit_plan via LLM."""
from __future__ import annotations

import difflib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from shellgeist.diff.apply import PatchApplyError, apply_unified_diff
from shellgeist.diff.guards import autofix_future_import, enforce_guards, guard_future_import
from shellgeist.llm.client import get_client
from shellgeist.tools.base import registry
from shellgeist.tools.normalize import (
    maybe_unescape_llm_string,
    salvage_fulltext,
)
from shellgeist.util_git import git
from shellgeist.util_json import loads_obj
from shellgeist.util_path import resolve_repo_path


class EditFileInput(BaseModel):
    """Pydantic model for the ``edit_file`` tool input."""
    path: str
    instruction: str


# =============================================================================
# EDIT RESULT — typed return value replacing raw dicts
# =============================================================================

@dataclass
class EditResult:
    """Structured result for all edit operations.

    This replaces the raw ``dict`` returns throughout the edit pipeline,
    making success/failure handling explicit and IDE-friendly.
    """
    ok: bool
    file: str = ""
    error: str = ""
    detail: str = ""
    patch: str = ""
    diff: str = ""
    written: bool = False
    staged: bool = False
    old_content: str = ""
    new_content: str = ""

    def to_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        """Serialize to the dict format the RPC layer expects.

        When *include_content* is True (review mode), ``old_content`` and
        ``new_content`` are included so the frontend can display inline diffs.
        """
        d: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            if self.file:
                d["file"] = self.file
            if self.patch:
                d["patch"] = self.patch
            if self.diff:
                d["diff"] = self.diff
            d["written"] = self.written
            if self.staged:
                d["staged"] = self.staged
            if include_content:
                d["old_content"] = self.old_content
                d["new_content"] = self.new_content
        else:
            if self.error:
                d["error"] = self.error
            if self.detail:
                d["detail"] = self.detail
            if self.patch:
                d["patch"] = self.patch
        return d

# =============================================================================
# TRACE
# =============================================================================

def _trace(msg: str) -> None:
    if os.environ.get("SHELLGEIST_TRACE") == "1":
        print(f"[ShellGeist][coder] {msg}", flush=True)


def _head_repr(s: str, n: int = 40) -> str:
    """
    Debug helper: first N lines as repr(), so invisible chars are visible (\ufeff, \u200b, etc).
    """
    lines = (s or "").splitlines()
    out = []
    for i, ln in enumerate(lines[:n], start=1):
        out.append(f"{i:02d}: {ln!r}")
    return "\n".join(out)


# =============================================================================
# DIFF NORMALIZATION + SALVAGE
# =============================================================================

_HUNK_INLINE_OP_RE = re.compile(r"^(@@.*@@)\s+([+\- ].*)$")
_NOISE_PREFIXES = ("diff --git ", "index ", "--- ", "+++ ")


def _normalize_unified_diff(diff: str) -> str:
    """
    Normalize to hunks-only diff (no ---/+++), which our apply_unified_diff expects.
    """
    if not isinstance(diff, str):
        return ""

    diff = diff.replace("\r\n", "\n").replace("\r", "\n")

    out: list[str] = []
    for raw_line in diff.split("\n"):
        if any(raw_line.startswith(p) for p in _NOISE_PREFIXES):
            continue
        m = _HUNK_INLINE_OP_RE.match(raw_line)
        if m:
            out.append(m.group(1))
            out.append(m.group(2))
        else:
            out.append(raw_line)

    norm = "\n".join(out)
    if not norm.endswith("\n"):
        norm += "\n"
    return norm


def _extract_diff_fallback(raw: str) -> str | None:
    """
    Hard salvage: take substring from first @@ to end and normalize.
    """
    if not isinstance(raw, str):
        return None
    i = raw.find("@@")
    if i == -1:
        return None
    guess = _normalize_unified_diff(raw[i:])
    return guess if "@@" in guess else None


def _ensure_display_diff(path: str, patch_hunks_only: str) -> str:
    """
    UI helper: Neovim diff previewers often expect ---/+++ headers.
    Our internal patch format is hunks-only; create a display-friendly version.
    """
    p = patch_hunks_only or ""
    if "@@" not in p:
        return p

    # Already has file headers? Keep it.
    if p.lstrip().startswith("--- "):
        return p if p.endswith("\n") else (p + "\n")

    a = f"--- a/{path}\n"
    b = f"+++ b/{path}\n"
    out = a + b + p
    return out if out.endswith("\n") else (out + "\n")


# =============================================================================
# EMPTY FILE DIFF VALIDATOR
# =============================================================================

def _validate_diff_for_empty_old(diff: str) -> tuple[bool, str]:
    """
    If OLD is empty, diff must be pure insertions:
    - no context lines starting with ' '
    - no deletions '-'
    - at least one '+'
    """
    lines = (diff or "").splitlines()
    in_hunk = False
    saw_plus = False

    for ln in lines:
        if ln.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if ln.startswith("\\") or ln == "":
            continue
        if ln.startswith(" "):
            return False, "context_lines"
        if ln.startswith("-"):
            return False, "deletions"
        if ln.startswith("+"):
            saw_plus = True
            continue
        return False, "invalid_line"

    if in_hunk and not saw_plus:
        return False, "empty_hunk"
    if not in_hunk:
        return False, "no_hunks"
    return True, ""


# =============================================================================
# MODEL CALL (cached client per edit_plan invocation)
# =============================================================================

@dataclass
class _ModelCtx:
    client: Any
    model: str


def _get_ctx(model_type: str, cache: dict[str, _ModelCtx]) -> _ModelCtx:
    ctx = cache.get(model_type)
    if ctx is not None:
        return ctx
    client, model = get_client(model_type)
    ctx = _ModelCtx(client=client, model=model)
    cache[model_type] = ctx
    return ctx


def _call_model(*, model_type: str, system: str, user: str, cache: dict[str, _ModelCtx]) -> tuple[str, str]:
    ctx = _get_ctx(model_type, cache)
    r = ctx.client.chat.completions.create(
        model=ctx.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    raw = (r.choices[0].message.content or "").strip()
    return raw, ctx.model


# =============================================================================
# PROMPTS
# =============================================================================

def _build_prompts(path: str, instruction: str, old: str, repair: str | None = None):
    empty_hint = ""
    if old == "":
        empty_hint = "OLD IS EMPTY. Only + lines. No context lines.\n"

    future_hint = (
        "If the file contains `from __future__ import ...`, it MUST remain the first non-comment/non-docstring statement.\n"
        "Do not add imports or code before it.\n"
    )

    system = (
        "Return JSON only: {\"diff\": \"<unified diff>\"}\n"
        "No markdown. No explanations.\n"
        f"{empty_hint}"
        f"{future_hint}"
    )
    if repair:
        system += f"REPAIR: {repair}\n"

    user = f"FILE: {path}\nINSTRUCTION: {instruction}\nOLD:\n{old}"
    return system, user


def _build_fulltext_prompts(path: str, instruction: str, old: str, repair: str | None = None):
    future_hint = (
        "If the file contains `from __future__ import ...`, it MUST remain the first non-comment/non-docstring statement.\n"
        "Do not add imports or code before it.\n"
    )

    system = (
        "Return JSON only: {\"content\": \"<full new file>\"}\n"
        "No markdown. No explanations.\n"
        f"{future_hint}"
    )
    if repair:
        system += f"REPAIR: {repair}\n"
    user = f"FILE: {path}\nINSTRUCTION: {instruction}\nOLD:\n{old}"
    return system, user


def _repair_hint_for_detail(detail: str) -> str:
    d = (detail or "")
    if "rewrite too violent" in d:
        return (
            "rewrite_too_violent: Your previous output rewrote too much.\n"
            "Generate a MINIMAL unified diff that changes ONLY what is necessary to satisfy the instruction.\n"
            "Do NOT reformat. Do NOT reorder imports. Do NOT rename identifiers unless required.\n"
            "Keep all unrelated lines EXACTLY identical.\n"
        )
    return f"guard_blocked: {d}"


# =============================================================================
# PATCH UTILS
# =============================================================================

def _make_patch_from_fulltext(path: str, old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"{path} (old)",
            tofile=f"{path} (new)",
        )
    )


# =============================================================================
# FILE I/O
# =============================================================================

def _py_syntax_ok(path: str, content: str) -> bool:
    if not path.endswith(".py"):
        return True
    try:
        compile(content, path, "exec")
        return True
    except SyntaxError:
        return False


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="strict") as f:
            f.write(content)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


# =============================================================================
# FINALIZER
# =============================================================================

def _finalize_ok(
    path: str, instruction: str, old: str, new: str, patch: str, root: Path,
    *, review_mode: bool = False,
) -> EditResult:
    """Run guards, autofix, and write the file atomically.

    When *review_mode* is ``True``, guards and autofix still run but the
    file is **not** written to disk.  Instead ``old_content`` and
    ``new_content`` are populated so the frontend can show an inline diff.
    """
    new2 = autofix_future_import(old, new)

    if not _py_syntax_ok(path, new2):
        okf, whyf = guard_future_import(old, new2)
        if not okf:
            _trace(f"guard_blocked(finalize): {whyf}")
            return EditResult(ok=False, error="guard_blocked", detail=whyf, patch=patch)

        _trace(
            "guard_blocked(finalize): syntax_error_after_edit\n"
            f"--- NEW head (repr) ---\n{_head_repr(new2)}\n--- end ---"
        )
        return EditResult(ok=False, error="guard_blocked", detail="syntax_error_after_edit", patch=patch)

    ok, why = enforce_guards(relpath=path, instruction=instruction, old=old, new=new2)
    if not ok:
        _trace(f"guard_blocked(finalize): {why}")
        return EditResult(ok=False, error="guard_blocked", detail=why, patch=patch)

    # If we changed the content (new -> new2), patch must match new2.
    if new2 != new:
        patch2 = _make_patch_from_fulltext(path, old, new2)
        display = _ensure_display_diff(path, patch2)
    else:
        patch2 = patch
        display = _ensure_display_diff(path, patch)

    if review_mode:
        # Don't write — return old/new for frontend-side hunk review.
        return EditResult(
            ok=True, file=path, patch=patch2, diff=display,
            written=False, old_content=old, new_content=new2,
        )

    # ACTUALLY WRITE THE FILE (MVP Empowerment)
    file_path = resolve_repo_path(root, path)
    _atomic_write_text(file_path, new2)
    return EditResult(ok=True, file=path, patch=patch2, diff=display, written=True)


# =============================================================================
# FULLTEXT FALLBACK
# =============================================================================

def _fulltext_fallback(
    path: str,
    instruction: str,
    old: str,
    *,
    reason: str,
    cache: dict[str, _ModelCtx],
    root: Path,
    review_mode: bool = False,
) -> EditResult:
    """Last-resort strategy: ask LLM for complete file content instead of a diff."""
    system, user = _build_fulltext_prompts(path, instruction, old, repair=reason)
    raw, _ = _call_model(model_type="smart", system=system, user=user, cache=cache)

    new: str | None = None
    try:
        data = loads_obj(raw)
        v = data.get("content")
        if isinstance(v, str):
            new = maybe_unescape_llm_string(v)
        else:
            raise ValueError("missing_content")
    except Exception as e:
        _trace(f"bad_json_fulltext => raw fallback: {e}")
        new = salvage_fulltext(raw)

    new = autofix_future_import(old, new)

    if not _py_syntax_ok(path, new):
        okf, whyf = guard_future_import(old, new)
        if not okf:
            return EditResult(ok=False, error="guard_blocked", detail=whyf)

        _trace(
            "guard_blocked(fulltext_fallback): syntax_error_after_edit\n"
            f"--- NEW head (repr) ---\n{_head_repr(new)}\n--- end ---"
        )
        return EditResult(ok=False, error="guard_blocked", detail="syntax_error_after_edit")

    ok_future, why_future = guard_future_import(old, new)
    patch = _make_patch_from_fulltext(path, old, new)

    if not ok_future:
        _trace(
            "future_import_guard BLOCKED (fulltext_fallback): "
            f"{why_future}\n--- NEW head (repr) ---\n{_head_repr(new)}\n--- end ---"
        )
        return EditResult(ok=False, error="guard_blocked", detail=why_future, patch=patch)

    return _finalize_ok(path, instruction, old, new, patch, root, review_mode=review_mode)


# =============================================================================
# MAIN EDIT PLAN
# =============================================================================

@registry.register(
    description="Plan and apply edits to a file using natural language instructions.",
    input_model=EditFileInput
)
def edit_file(
    path: str | None = None,
    instruction: str = "",
    root: str = "",
    file_path: str | None = None,
    file: str | None = None,
    review_mode: bool = False,
) -> dict:
    """Agent-facing tool for editing files.

    Wraps ``edit_plan`` to handle the root ``Path`` conversion and
    return the ``dict`` format expected by the RPC layer.

    When *review_mode* is ``True``, the file is **not** written;
    the result includes ``old_content`` and ``new_content`` for
    frontend-side hunk-level review.
    """
    target = (path or file_path or file or "").strip()
    result = edit_plan(target, instruction, root=Path(root), review_mode=review_mode)
    return result.to_dict(include_content=review_mode)


# ---------------------------------------------------------------------------
# Diff extraction helper (parse LLM raw → normalised diff string)
# ---------------------------------------------------------------------------

def _parse_diff_from_raw(raw: str, *, tag: str = "?") -> str:
    """Extract and normalise a unified diff from raw LLM output.

    Returns a normalised diff string, or ``""`` if nothing usable was found.
    """
    diff = ""
    try:
        data = loads_obj(raw)
        diff = _normalize_unified_diff(maybe_unescape_llm_string(data.get("diff", "")))
    except Exception as e:
        _trace(f"bad_json({tag}): {e}")

    if "@@" not in diff:
        salvage = _extract_diff_fallback(raw)
        if salvage:
            diff = salvage

    return diff


# ---------------------------------------------------------------------------
# Single "try diff → apply → guard-repair" attempt
# ---------------------------------------------------------------------------

def _try_diff_attempt(
    path: str,
    instruction: str,
    old: str,
    diff: str,
    *,
    cache: dict[str, _ModelCtx],
    root: Path,
    tag: str = "1",
    review_mode: bool = False,
) -> EditResult | None:
    """Try to apply *diff* and finalize.  On guard failure, attempt one LLM repair.

    Returns an ``EditResult`` on success or terminal guard block, or ``None``
    if the diff could not be applied at all (``PatchApplyError``).
    """
    try:
        new = apply_unified_diff(old, diff)
    except PatchApplyError as e:
        _trace(f"patch_apply_failed({tag}): {e}")
        return None  # caller should try next strategy

    result = _finalize_ok(path, instruction, old, new, diff, root, review_mode=review_mode)
    if result.ok:
        return result

    # Guard failed — try one LLM repair round
    detail = result.detail
    if "rewrite too violent" in detail:
        return result  # terminal — repair won't help

    repair_hint = _repair_hint_for_detail(detail)
    sys_r, usr_r = _build_prompts(path, instruction, old, repair=repair_hint)
    raw_r, _ = _call_model(model_type="smart", system=sys_r, user=usr_r, cache=cache)
    diff_r = _parse_diff_from_raw(raw_r, tag=f"guard-repair-{tag}")

    if "@@" in diff_r:
        try:
            new_r = apply_unified_diff(old, diff_r)
            result_r = _finalize_ok(path, instruction, old, new_r, diff_r, root, review_mode=review_mode)
            if result_r.ok:
                return result_r
        except PatchApplyError as e2:
            _trace(f"patch_apply_failed(guard-repair-{tag}): {e2}")

    return result  # return the original guard-blocked result


# ---------------------------------------------------------------------------
# Main edit pipeline
# ---------------------------------------------------------------------------

def edit_plan(path: str, instruction: str, *, root: Path, review_mode: bool = False) -> EditResult:
    """Plan and apply an edit to *path* using the LLM.

    Pipeline:
      1. Ask LLM for a unified diff, try to apply it.
      2. On ``PatchApplyError``, ask LLM again with the error message.
      3. If both diff attempts fail, fall back to full-text rewrite.

    Each diff attempt includes an automatic guard-repair sub-attempt if
    guards reject the result (e.g. \"rewrite too violent\").

    When *review_mode* is ``True``, guards still run but no file is
    written — ``EditResult.old_content`` / ``new_content`` are populated
    instead.
    """
    file_path = resolve_repo_path(root, path)
    if not file_path.exists():
        old = ""
    else:
        old = file_path.read_text(encoding="utf-8", errors="replace")

    cache: dict[str, _ModelCtx] = {}

    # --- Attempt 1: ask LLM for a diff ----------------------------------
    sys1, usr1 = _build_prompts(path, instruction, old)
    raw1, _ = _call_model(model_type="smart", system=sys1, user=usr1, cache=cache)
    diff1 = _parse_diff_from_raw(raw1, tag="1")

    if "@@" not in diff1:
        return _fulltext_fallback(path, instruction, old, reason="missing_diff", cache=cache, root=root, review_mode=review_mode)

    if old == "" and not _validate_diff_for_empty_old(diff1)[0]:
        why = _validate_diff_for_empty_old(diff1)[1]
        return _fulltext_fallback(path, instruction, old, reason=f"bad_diff_empty_old: {why}", cache=cache, root=root, review_mode=review_mode)

    result1 = _try_diff_attempt(path, instruction, old, diff1, cache=cache, root=root, tag="1", review_mode=review_mode)
    if result1 is not None:
        return result1

    # --- Attempt 2: retry with error feedback ----------------------------
    sys2, usr2 = _build_prompts(path, instruction, old, repair="patch_apply_failed: see attempt 1")
    raw2, _ = _call_model(model_type="smart", system=sys2, user=usr2, cache=cache)
    diff2 = _parse_diff_from_raw(raw2, tag="2")

    if "@@" in diff2:
        if old == "" and not _validate_diff_for_empty_old(diff2)[0]:
            why2 = _validate_diff_for_empty_old(diff2)[1]
            return _fulltext_fallback(path, instruction, old, reason=f"bad_diff_empty_old: {why2}", cache=cache, root=root, review_mode=review_mode)

        result2 = _try_diff_attempt(path, instruction, old, diff2, cache=cache, root=root, tag="2", review_mode=review_mode)
        if result2 is not None:
            return result2

    # --- Attempt 3: full-text fallback -----------------------------------
    return _fulltext_fallback(path, instruction, old, reason="patch_apply_failed twice", cache=cache, root=root, review_mode=review_mode)


# =============================================================================
# FULL REPLACE (exported)
# =============================================================================

def apply_full_replace(
    path: str,
    new: str,
    *,
    root: Path,
    instruction: str = "full_replace",
    stage: bool = False,
    backup: bool = True,
) -> dict:
    """Replace a file's entire content after running guards.

    Returns a ``dict`` for the RPC layer.
    """
    file_path = resolve_repo_path(root, path)
    if not file_path.exists():
        return {"ok": False, "error": "file_not_found", "file": path}
    if not isinstance(new, str):
        return {"ok": False, "error": "invalid_content"}

    old = file_path.read_text(encoding="utf-8", errors="replace")
    new = maybe_unescape_llm_string(new)

    patch = _make_patch_from_fulltext(path, old, new)

    ok_future, why_future = guard_future_import(old, new)
    if not ok_future:
        return {"ok": False, "error": "guard_blocked", "detail": why_future, "patch": patch}

    if not _py_syntax_ok(path, new):
        return {"ok": False, "error": "guard_blocked", "detail": "syntax_error_after_edit", "patch": patch}

    ok, why = enforce_guards(relpath=path, instruction=instruction, old=old, new=new)
    if not ok:
        return {"ok": False, "error": "guard_blocked", "detail": why, "patch": patch}

    _atomic_write_text(file_path, new)

    staged = False
    if stage:
        rc, out = git(root, ["add", "--", path])
        if rc != 0:
            return {"ok": False, "error": "git_add_failed", "detail": out[:8000]}
        staged = True

    return {"ok": True, "file": path, "written": True, "staged": staged, "patch": patch}


# =============================================================================
# APPLY EDIT (pre-computed patch)
# =============================================================================

def apply_edit(
    path: str,
    patch: str,
    *,
    root: Path,
    instruction: str = "apply",
    stage: bool = False,
    backup: bool = True,
) -> dict:
    """Apply a pre-computed unified diff patch to *path*.

    Returns a ``dict`` for the RPC layer.
    """
    file_path = resolve_repo_path(root, path)
    if not file_path.exists():
        return {"ok": False, "error": "file_not_found", "file": path}
    if not isinstance(patch, str) or "@@" not in patch:
        return {"ok": False, "error": "invalid_patch"}

    old = file_path.read_text(encoding="utf-8", errors="replace")
    patch = _normalize_unified_diff(maybe_unescape_llm_string(patch))

    if old == "":
        ok_empty, why = _validate_diff_for_empty_old(patch)
        if not ok_empty:
            return {"ok": False, "error": "bad_patch_empty_old", "detail": why}

    try:
        new = apply_unified_diff(old, patch)
    except PatchApplyError as e:
        return {"ok": False, "error": "patch_apply_failed", "detail": str(e)}

    new = autofix_future_import(old, new)

    if not _py_syntax_ok(path, new):
        okf, whyf = guard_future_import(old, new)
        if not okf:
            _trace(
                "future_import_guard BLOCKED (apply_edit): "
                f"{whyf}\n--- NEW head (repr) ---\n{_head_repr(new)}\n--- end ---"
            )
            return {"ok": False, "error": "guard_blocked", "detail": whyf}

        _trace(
            "guard_blocked(apply_edit): syntax_error_after_edit\n"
            f"--- NEW head (repr) ---\n{_head_repr(new)}\n--- end ---"
        )
        return {"ok": False, "error": "guard_blocked", "detail": "syntax_error_after_edit"}

    ok_guard, why_guard = enforce_guards(relpath=path, instruction=instruction or "apply", old=old, new=new)
    if not ok_guard:
        return {"ok": False, "error": "guard_blocked", "detail": why_guard}

    ok_future, why_future = guard_future_import(old, new)
    if not ok_future:
        _trace(
            "future_import_guard BLOCKED (apply_edit): "
            f"{why_future}\n--- NEW head (repr) ---\n{_head_repr(new)}\n--- end ---"
        )
        return {"ok": False, "error": "guard_blocked", "detail": why_future}

    _atomic_write_text(file_path, new)

    staged = False
    if stage:
        rc, out = git(root, ["add", "--", path])
        if rc != 0:
            return {"ok": False, "error": "git_add_failed", "detail": out[:8000]}
        staged = True

    return {"ok": True, "file": path, "written": True, "staged": staged}


# =============================================================================
# POST-REVIEW WRITE — called after user resolves hunks in the frontend
# =============================================================================

def write_reviewed_content(path: str, content: str, *, root: str | Path) -> None:
    """Write user-reviewed content to disk.

    Called by the review flow after the user resolves hunks in the frontend.
    Performs an atomic write without re-running guards (they already ran
    before the review was sent to the user).
    """
    file_path = resolve_repo_path(Path(root) if isinstance(root, str) else root, path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(file_path, content)
