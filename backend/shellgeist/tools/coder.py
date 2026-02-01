from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import difflib
import os
import re
import subprocess
import tempfile
from typing import Any, Dict, Tuple

from shellgeist.models import get_client
from shellgeist.util_json import loads_obj
from shellgeist.diff.apply import apply_unified_diff, PatchApplyError
from shellgeist.diff.guards import enforce_guards


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
# PATH SAFETY
# =============================================================================

def _resolve_repo_path(root: Path, rel: str) -> Path:
    if not rel or rel.startswith(("/", "~")):
        raise ValueError("invalid_path")
    p = (root / rel).resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        raise ValueError("path_escape")
    return p


# =============================================================================
# LLM STRING AUTO-UNESCAPE
# =============================================================================

def _maybe_unescape_llm_string(s: str) -> str:
    """
    Some models double-escape JSON string payloads, so fields arrive with literal "\\n".
    If it looks like that, unescape common sequences.
    """
    if not isinstance(s, str) or not s:
        return s

    # Heuristic: if we see \\n but no real newlines, it's probably double-escaped.
    if "\\n" in s and "\n" not in s:
        s2 = s
        s2 = s2.replace("\\r\\n", "\n")
        s2 = s2.replace("\\n", "\n")
        s2 = s2.replace("\\r", "\r")
        s2 = s2.replace("\\t", "\t")
        s2 = s2.replace('\\"', '"').replace("\\'", "'")
        s2 = s2.replace("\\\\", "\\")
        return s2

    return s


def _strip_fences(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].lstrip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


# =============================================================================
# FULLTEXT SALVAGE (broken JSON -> extract "content")
# =============================================================================

_CONTENT_FIELD_RE = re.compile(
    r'"content"\s*:\s*"(?P<body>(?:\\.|[^"\\])*)"\s*[}\]]?\s*$',
    re.DOTALL,
)


def _unescape_json_string_fragment(s: str) -> str:
    """
    Best-effort unescape for a JSON string fragment (no surrounding quotes).
    Handles common escapes enough for our 'content' salvage path.
    """
    if not isinstance(s, str):
        return ""
    s = s.replace("\\r\\n", "\n")
    s = s.replace("\\n", "\n")
    s = s.replace("\\r", "\r")
    s = s.replace("\\t", "\t")
    s = s.replace('\\"', '"')
    s = s.replace("\\/", "/")
    s = s.replace("\\\\", "\\")
    return s


def _extract_fulltext_content_salvage(raw: str) -> str | None:
    """
    When model returns broken JSON like:
      { "content": "....   (missing closing braces/quotes)
    try to salvage the content field anyway.
    """
    if not isinstance(raw, str) or not raw:
        return None

    txt = raw.strip()

    m = _CONTENT_FIELD_RE.search(txt)
    if m:
        body = m.group("body")
        return _unescape_json_string_fragment(body)

    needle = '"content": "'
    j = txt.find(needle)
    if j != -1:
        frag = txt[j + len(needle):]
        k = frag.rfind('"')
        if k > 0:
            body = frag[:k]
            return _unescape_json_string_fragment(body)

    return None


def _salvage_broken_content_envelope(raw: str) -> str | None:
    """
    Salvage responses shaped like:
      {
      "content": "
      <python code...>
      "
    }
    which is invalid JSON because of raw newlines.
    """
    if not isinstance(raw, str):
        return None

    lines = raw.splitlines()
    if len(lines) < 3:
        return None

    if lines[0].strip() != "{":
        return None
    if not lines[1].lstrip().startswith('"content": "'):
        return None

    body = lines[2:]
    while body and body[-1].strip() in ('"', '"}', '"},', "}", "},"):
        body = body[:-1]

    return "\n".join(body).lstrip("\n")


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

def _validate_diff_for_empty_old(diff: str) -> Tuple[bool, str]:
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


def _get_ctx(model_type: str, cache: Dict[str, _ModelCtx]) -> _ModelCtx:
    ctx = cache.get(model_type)
    if ctx is not None:
        return ctx
    client, model = get_client(model_type)
    ctx = _ModelCtx(client=client, model=model)
    cache[model_type] = ctx
    return ctx


def _call_model(*, model_type: str, system: str, user: str, cache: Dict[str, _ModelCtx]) -> Tuple[str, str]:
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
# __future__ GUARD (NO LITERAL TRIPLE QUOTES)
# =============================================================================

_TRIPLE_DQ = '"' * 3
_TRIPLE_SQ = "'" * 3
_BOM_ZW = "\ufeff\u200b"


def _strip_bom_zw(s: str) -> str:
    return (s or "").lstrip(_BOM_ZW)


def _is_effectively_blank(s: str) -> bool:
    return _strip_bom_zw(s).strip() == ""


def _future_import_guard(old: str, new: str) -> Tuple[bool, str]:
    old_lines = (old or "").splitlines()
    if not any(l.lstrip().startswith("from __future__ import") for l in old_lines):
        return True, ""

    new_text = new or ""
    new_lines = new_text.splitlines()

    new_future_idxs = [
        idx for idx, ln in enumerate(new_lines)
        if _strip_bom_zw(ln).lstrip().startswith("from __future__ import")
    ]
    if not new_future_idxs:
        return False, "future_import_removed"

    i = 0
    while i < len(new_lines):
        s = new_lines[i]
        if _is_effectively_blank(s):
            i += 1
            continue
        if _strip_bom_zw(s).lstrip().startswith("#"):
            i += 1
            continue
        break

    if i < len(new_lines):
        s0 = _strip_bom_zw(new_lines[i]).lstrip()
        if s0.startswith(_TRIPLE_DQ) or s0.startswith(_TRIPLE_SQ):
            q = _TRIPLE_DQ if s0.startswith(_TRIPLE_DQ) else _TRIPLE_SQ
            if s0.count(q) >= 2:
                i += 1
            else:
                i += 1
                while i < len(new_lines):
                    if q in new_lines[i]:
                        i += 1
                        break
                    i += 1

    while i < len(new_lines) and _is_effectively_blank(new_lines[i]):
        i += 1

    ok = i < len(new_lines) and _strip_bom_zw(new_lines[i]).lstrip().startswith("from __future__ import")
    if not ok:
        first_idx = new_future_idxs[0]
        _trace(
            "future_import_guard BLOCKED: future_import_moved "
            f"(expected at top, found first at line {first_idx+1})\n"
            f"--- NEW head (repr) ---\n{_head_repr(new_text)}\n--- end ---"
        )
        return False, "future_import_moved"

    return True, ""


def _autofix_future_import(old: str, new: str) -> str:
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

    fut_idx = [
        i for i, ln in enumerate(new_lines)
        if _strip_bom_zw(ln).lstrip().startswith("from __future__ import")
    ]

    if not fut_idx:
        rest = new_lines
        i = 0
        while i < len(rest):
            s = rest[i]
            if _is_effectively_blank(s):
                i += 1
                continue
            if _strip_bom_zw(s).lstrip().startswith("#"):
                i += 1
                continue
            break

        if i < len(rest):
            s0 = _strip_bom_zw(rest[i]).lstrip()
            if s0.startswith(_TRIPLE_DQ) or s0.startswith(_TRIPLE_SQ):
                q = _TRIPLE_DQ if s0.startswith(_TRIPLE_DQ) else _TRIPLE_SQ
                if s0.count(q) >= 2:
                    i += 1
                else:
                    i += 1
                    while i < len(rest):
                        if q in rest[i]:
                            i += 1
                            break
                        i += 1

        fixed = "".join(rest[:i] + old_future + rest[i:])
        ok2, _ = _future_import_guard(old, fixed)
        return fixed if ok2 else new

    ok, why = _future_import_guard(old, new)
    if ok:
        return new
    if why != "future_import_moved":
        return new

    fut_set = set(fut_idx)
    fut_lines = [_strip_bom_zw(new_lines[i]).lstrip() for i in fut_idx]
    rest = [ln for j, ln in enumerate(new_lines) if j not in fut_set]

    i = 0
    while i < len(rest):
        s = rest[i]
        if _is_effectively_blank(s):
            i += 1
            continue
        if _strip_bom_zw(s).lstrip().startswith("#"):
            i += 1
            continue
        break

    if i < len(rest):
        s0 = _strip_bom_zw(rest[i]).lstrip()
        if s0.startswith(_TRIPLE_DQ) or s0.startswith(_TRIPLE_SQ):
            q = _TRIPLE_DQ if s0.startswith(_TRIPLE_DQ) else _TRIPLE_SQ
            if s0.count(q) >= 2:
                i += 1
            else:
                i += 1
                while i < len(rest):
                    if q in rest[i]:
                        i += 1
                        break
                    i += 1

    fixed = "".join(rest[:i] + fut_lines + rest[i:])
    ok2, _ = _future_import_guard(old, fixed)
    return fixed if ok2 else new


# =============================================================================
# FILE I/O + GIT
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


def _backup_file(path: Path) -> None:
    bak = path.with_suffix(path.suffix + ".shellgeist.bak")
    try:
        bak.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    except Exception:
        pass


def _git(root: Path, args: list[str]) -> tuple[int, str]:
    p = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return p.returncode, p.stdout


# =============================================================================
# FINALIZER
# =============================================================================

def _finalize_ok(path: str, instruction: str, old: str, new: str, patch: str) -> dict:
    new2 = _autofix_future_import(old, new)

    if not _py_syntax_ok(path, new2):
        okf, whyf = _future_import_guard(old, new2)
        if not okf:
            _trace(f"guard_blocked(finalize): {whyf}")
            return {"ok": False, "error": "guard_blocked", "detail": whyf, "patch": patch}

        _trace(
            "guard_blocked(finalize): syntax_error_after_edit\n"
            f"--- NEW head (repr) ---\n{_head_repr(new2)}\n--- end ---"
        )
        return {"ok": False, "error": "guard_blocked", "detail": "syntax_error_after_edit", "patch": patch}

    ok, why = enforce_guards(relpath=path, instruction=instruction, old=old, new=new2)
    if not ok:
        _trace(f"guard_blocked(finalize): {why}")
        return {"ok": False, "error": "guard_blocked", "detail": why, "patch": patch}

    # If we changed the content (new -> new2), patch must match new2.
    if new2 != new:
        patch2 = _make_patch_from_fulltext(path, old, new2)
        return {
            "ok": True,
            "file": path,
            "patch": patch2,
            "diff": _ensure_display_diff(path, patch2),
        }

    return {
        "ok": True,
        "file": path,
        "patch": patch,
        "diff": _ensure_display_diff(path, patch),
    }


# =============================================================================
# FULLTEXT FALLBACK
# =============================================================================

def _fulltext_fallback(
    path: str,
    instruction: str,
    old: str,
    *,
    reason: str,
    cache: Dict[str, _ModelCtx],
) -> dict:
    system, user = _build_fulltext_prompts(path, instruction, old, repair=reason)
    raw, _ = _call_model(model_type="smart", system=system, user=user, cache=cache)

    new: str | None = None
    try:
        data = loads_obj(raw)
        v = data.get("content")
        if isinstance(v, str):
            new = v
        else:
            raise ValueError("missing_content")
    except Exception as e:
        _trace(f"bad_json_fulltext => raw fallback: {e}")

        salv1 = _extract_fulltext_content_salvage(raw)
        if isinstance(salv1, str) and salv1.strip():
            new = salv1
        else:
            salv2 = _salvage_broken_content_envelope(raw)
            if isinstance(salv2, str) and salv2.strip():
                new = salv2
            else:
                new = _strip_fences(raw)

    new = _maybe_unescape_llm_string(new)
    new = _autofix_future_import(old, new)

    if not _py_syntax_ok(path, new):
        okf, whyf = _future_import_guard(old, new)
        if not okf:
            return {"ok": False, "error": "guard_blocked", "detail": whyf}

        _trace(
            "guard_blocked(fulltext_fallback): syntax_error_after_edit\n"
            f"--- NEW head (repr) ---\n{_head_repr(new)}\n--- end ---"
        )
        return {"ok": False, "error": "guard_blocked", "detail": "syntax_error_after_edit"}

    ok_future, why_future = _future_import_guard(old, new)
    patch = _make_patch_from_fulltext(path, old, new)

    if not ok_future:
        _trace(
            "future_import_guard BLOCKED (fulltext_fallback): "
            f"{why_future}\n--- NEW head (repr) ---\n{_head_repr(new)}\n--- end ---"
        )
        return {"ok": False, "error": "guard_blocked", "detail": why_future, "patch": patch}

    return _finalize_ok(path, instruction, old, new, patch)


# =============================================================================
# MAIN EDIT PLAN
# =============================================================================

def edit_plan(path: str, instruction: str, *, root: Path) -> dict:
    file_path = _resolve_repo_path(root, path)
    if not file_path.exists():
        return {"ok": False, "error": "file_not_found", "file": path}

    cache: Dict[str, _ModelCtx] = {}
    old = file_path.read_text(encoding="utf-8", errors="replace")

    system1, user1 = _build_prompts(path, instruction, old)
    raw1, _ = _call_model(model_type="smart", system=system1, user=user1, cache=cache)

    diff1 = ""
    try:
        data1 = loads_obj(raw1)
        diff1 = _normalize_unified_diff(_maybe_unescape_llm_string(data1.get("diff", "")))
    except Exception as e:
        _trace(f"bad_json(1): {e}")
        salvage = _extract_diff_fallback(raw1)
        if salvage:
            diff1 = salvage

    if "@@" not in diff1:
        salvage = _extract_diff_fallback(raw1)
        if salvage:
            diff1 = salvage

    if "@@" not in diff1:
        return _fulltext_fallback(path, instruction, old, reason="missing_diff", cache=cache)

    if old == "":
        ok_empty, why_empty = _validate_diff_for_empty_old(diff1)
        if not ok_empty:
            return _fulltext_fallback(path, instruction, old, reason=f"bad_diff_empty_old: {why_empty}", cache=cache)

    apply_err1 = ""
    try:
        new1 = apply_unified_diff(old, diff1)
        out1 = _finalize_ok(path, instruction, old, new1, diff1)
        if out1.get("ok") is True:
            return out1

        detail1 = str(out1.get("detail", ""))
        repair1 = _repair_hint_for_detail(detail1)
        systemg, userg = _build_prompts(path, instruction, old, repair=repair1)
        rawg, _ = _call_model(model_type="smart", system=systemg, user=userg, cache=cache)
        try:
            datag = loads_obj(rawg)
            diffg = _normalize_unified_diff(_maybe_unescape_llm_string(datag.get("diff", "")))
        except Exception as eg:
            _trace(f"bad_json(guard-repair1): {eg}")
            diffg = _extract_diff_fallback(rawg) or ""

        if "@@" in diffg:
            try:
                newg = apply_unified_diff(old, diffg)
                outg = _finalize_ok(path, instruction, old, newg, diffg)
                if outg.get("ok") is True:
                    return outg
            except PatchApplyError as eg2:
                _trace(f"patch_apply_failed(guard-repair1): {eg2}")

        if "rewrite too violent" in detail1:
            return out1

        return out1

    except PatchApplyError as e:
        apply_err1 = str(e)
        _trace(f"patch_apply_failed(1): {apply_err1}")

    system2, user2 = _build_prompts(path, instruction, old, repair=f"patch_apply_failed: {apply_err1}")
    raw2, _ = _call_model(model_type="smart", system=system2, user=user2, cache=cache)

    diff2 = ""
    try:
        data2 = loads_obj(raw2)
        diff2 = _normalize_unified_diff(_maybe_unescape_llm_string(data2.get("diff", "")))
    except Exception as e2:
        _trace(f"bad_json(2): {e2}")
        salvage2 = _extract_diff_fallback(raw2)
        if salvage2:
            diff2 = salvage2

    if "@@" not in diff2:
        salvage2 = _extract_diff_fallback(raw2)
        if salvage2:
            diff2 = salvage2

    if "@@" in diff2:
        if old == "":
            ok_empty2, why_empty2 = _validate_diff_for_empty_old(diff2)
            if not ok_empty2:
                return _fulltext_fallback(path, instruction, old, reason=f"bad_diff_empty_old: {why_empty2}", cache=cache)

        try:
            new2 = apply_unified_diff(old, diff2)
            out2 = _finalize_ok(path, instruction, old, new2, diff2)
            if out2.get("ok") is True:
                return out2

            detail2 = str(out2.get("detail", ""))
            repair2 = _repair_hint_for_detail(detail2)
            systemg2, userg2 = _build_prompts(path, instruction, old, repair=repair2)
            rawg2, _ = _call_model(model_type="smart", system=systemg2, user=userg2, cache=cache)
            try:
                datag2 = loads_obj(rawg2)
                diffg2 = _normalize_unified_diff(_maybe_unescape_llm_string(datag2.get("diff", "")))
            except Exception as eg3:
                _trace(f"bad_json(guard-repair2): {eg3}")
                diffg2 = _extract_diff_fallback(rawg2) or ""

            if "@@" in diffg2:
                try:
                    newg2 = apply_unified_diff(old, diffg2)
                    outg2 = _finalize_ok(path, instruction, old, newg2, diffg2)
                    if outg2.get("ok") is True:
                        return outg2
                except PatchApplyError as eg4:
                    _trace(f"patch_apply_failed(guard-repair2): {eg4}")

            if "rewrite too violent" in detail2:
                return out2

            return out2

        except PatchApplyError as e2a:
            _trace(f"patch_apply_failed(2): {e2a}")

    return _fulltext_fallback(path, instruction, old, reason="patch_apply_failed twice", cache=cache)


# =============================================================================
# FULL REPLACE (exported) - used by tests
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
    file_path = _resolve_repo_path(root, path)
    if not file_path.exists():
        return {"ok": False, "error": "file_not_found", "file": path}
    if not isinstance(new, str):
        return {"ok": False, "error": "invalid_content"}

    old = file_path.read_text(encoding="utf-8", errors="replace")
    new = _maybe_unescape_llm_string(new)

    patch = _make_patch_from_fulltext(path, old, new)

    ok_future, why_future = _future_import_guard(old, new)
    if not ok_future:
        return {"ok": False, "error": "guard_blocked", "detail": why_future, "patch": patch}

    if not _py_syntax_ok(path, new):
        return {"ok": False, "error": "guard_blocked", "detail": "syntax_error_after_edit", "patch": patch}

    ok, why = enforce_guards(relpath=path, instruction=instruction, old=old, new=new)
    if not ok:
        return {"ok": False, "error": "guard_blocked", "detail": why, "patch": patch}

    if backup:
        _backup_file(file_path)
    _atomic_write_text(file_path, new)

    staged = False
    if stage:
        rc, out = _git(root, ["add", "--", path])
        if rc != 0:
            return {"ok": False, "error": "git_add_failed", "detail": out[:8000]}
        staged = True

    return {"ok": True, "file": path, "written": True, "staged": staged, "patch": patch}


# =============================================================================
# APPLY EDIT (Run 1)
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
    file_path = _resolve_repo_path(root, path)
    if not file_path.exists():
        return {"ok": False, "error": "file_not_found", "file": path}
    if not isinstance(patch, str) or "@@" not in patch:
        return {"ok": False, "error": "invalid_patch"}

    old = file_path.read_text(encoding="utf-8", errors="replace")
    patch = _normalize_unified_diff(_maybe_unescape_llm_string(patch))

    if old == "":
        ok_empty, why = _validate_diff_for_empty_old(patch)
        if not ok_empty:
            return {"ok": False, "error": "bad_patch_empty_old", "detail": why}

    try:
        new = apply_unified_diff(old, patch)
    except PatchApplyError as e:
        return {"ok": False, "error": "patch_apply_failed", "detail": str(e)}

    new = _autofix_future_import(old, new)

    if not _py_syntax_ok(path, new):
        okf, whyf = _future_import_guard(old, new)
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

    ok_future, why_future = _future_import_guard(old, new)
    if not ok_future:
        _trace(
            "future_import_guard BLOCKED (apply_edit): "
            f"{why_future}\n--- NEW head (repr) ---\n{_head_repr(new)}\n--- end ---"
        )
        return {"ok": False, "error": "guard_blocked", "detail": why_future}

    if backup:
        _backup_file(file_path)
    _atomic_write_text(file_path, new)

    staged = False
    if stage:
        rc, out = _git(root, ["add", "--", path])
        if rc != 0:
            return {"ok": False, "error": "git_add_failed", "detail": out[:8000]}
        staged = True

    return {"ok": True, "file": path, "written": True, "staged": staged}
