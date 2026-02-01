from __future__ import annotations

import json
import re


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*$", re.IGNORECASE)
_UNQUOTED_KEY_RE = re.compile(r'([{\s,])([A-Za-z_][A-Za-z0-9_]*)\s*:')
_SINGLE_QUOTE_KEY_RE = re.compile(r"{\s*'([^']+)'\s*:")

# Remove raw control chars that break json.loads (keep \t \n \r)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s

    lines = s.splitlines()
    if lines and _FENCE_RE.match(lines[0]):
        lines = lines[1:]
        if lines and _FENCE_RE.match(lines[-1]):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


def _extract_first_json_value(raw: str, want: str) -> str:
    s = _strip_code_fences(raw)
    dec = json.JSONDecoder()

    open_ch = "{" if want == "object" else "["
    close_ch = "}" if want == "object" else "]"

    start = s.find(open_ch)
    if start == -1:
        return s

    try:
        _, end = dec.raw_decode(s, idx=start)
        return s[start:end]
    except Exception:
        end = s.rfind(close_ch)
        if end != -1 and end > start:
            return s[start : end + 1]
        return s[start:]


def extract_json_object(raw: str) -> str:
    return _extract_first_json_value(raw, "object")


def extract_json_array(raw: str) -> str:
    return _extract_first_json_value(raw, "array")


def _repair_common_llm_json(s: str) -> str:
    """
    Best-effort repairs for common LLM JSON glitches:
    - unquoted keys: {diff: "..."} -> {"diff": "..."}
    - single-quoted keys at root: {'diff': "..."} -> {"diff": "..."}
    - single-quoted string values: {"diff": 'hi'} -> {"diff": "hi"}
    """
    s = (s or "").strip()
    if not s:
        return s

    s = _CTRL_RE.sub("", s)

    s = _SINGLE_QUOTE_KEY_RE.sub(r'{"\1":', s)
    s = _UNQUOTED_KEY_RE.sub(r'\1"\2":', s)

    def _sq_val(m: re.Match) -> str:
        inner = m.group(1)
        inner = inner.replace("\\'", "'")
        inner = inner.replace('"', '\\"')
        return ': "' + inner + '"'

    s = re.sub(r":\s*'((?:\\'|[^'])*)'", _sq_val, s)
    return s


def _unescape_if_looks_escaped(s: str) -> str:
    """
    If we see literal backslash escapes like '\\n' or '\\"', unescape once.
    """
    if not isinstance(s, str):
        return s
    if "\\n" in s or "\\t" in s or '\\"' in s or "\\r" in s:
        try:
            return bytes(s, "utf-8").decode("unicode_escape")
        except Exception:
            return s
    return s


def _postprocess_obj(d: dict) -> dict:
    # Always unescape common string payload fields if present
    for k in ("diff", "text", "content"):
        if k in d and isinstance(d[k], str):
            d[k] = _unescape_if_looks_escaped(d[k])
    return d


def loads_obj(text: str) -> dict:
    raw = _strip_code_fences(text)
    raw = _CTRL_RE.sub("", raw)

    # 1) fast path
    try:
        v = json.loads(raw)
        if isinstance(v, dict):
            return _postprocess_obj(v)
    except Exception:
        pass

    # 2) extract first object fragment
    frag = extract_json_object(raw).strip()
    frag = _CTRL_RE.sub("", frag)

    # 3) try direct parse
    try:
        v2 = json.loads(frag)
        if isinstance(v2, dict):
            return _postprocess_obj(v2)
    except Exception:
        pass

    # 4) repair + parse
    repaired = _repair_common_llm_json(frag)
    v3 = json.loads(repaired)
    if not isinstance(v3, dict):
        raise ValueError("json_not_object")
    return _postprocess_obj(v3)


def loads_arr(text: str) -> list:
    raw = _strip_code_fences(text)
    raw = _CTRL_RE.sub("", raw)

    try:
        v = json.loads(raw)
        if isinstance(v, list):
            return v
    except Exception:
        pass

    frag = extract_json_array(raw).strip()
    frag = _CTRL_RE.sub("", frag)

    v2 = json.loads(frag)
    if not isinstance(v2, list):
        raise ValueError("json_not_array")
    return v2
