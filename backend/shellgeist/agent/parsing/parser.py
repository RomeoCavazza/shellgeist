"""XML tool-use parser: extracts structured tool calls from LLM output.

Moved from tools/parser.py for better semantic separation.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from shellgeist.agent.parsing.json_utils import loads_obj

# Opening: <tool_use>, <tool_request>, <tool_call>, <tool>
# Optionally with attributes (e.g. name="run_shell")
_TAG_OPEN = r"<(tool_use|tool_request|tool_call|tool)\b([^>]*)>"
# Closing: any </tool...> variant
_TAG_CLOSE = r"</(tool_use|tool_request|tool_call|tool[_a-z]*)>"
# Full pattern: open tag → body → close tag
_TOOL_RE = re.compile(
    _TAG_OPEN + r"(.*?)" + _TAG_CLOSE,
    re.DOTALL | re.IGNORECASE,
)
# Extract name="..." from tag attributes
_ATTR_NAME_RE = re.compile(r'name\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def _wrap_bare_json(text: str) -> str:
    """Wrap bare key-value pairs in {} if not already an object."""
    s = text.strip()
    if s.startswith("{"):
        return s
    # Looks like bare "key": "value" pairs → wrap
    if re.match(r'^"[^"]+"\s*:', s):
        return "{" + s + "}"
    return s


def parse_xml_tool_use(
    text: str,
    *,
    debug_log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    matches: list[Any] = list(_TOOL_RE.finditer(text))

    # Fallback: split on any opening tool tag if regex didn't match
    if not matches:
        for variant in ("tool_use", "tool_request", "tool_call", "tool"):
            tag = f"<{variant}"
            if tag in text.lower():
                idx = text.lower().find(tag)
                # Skip past the opening tag
                gt = text.find(">", idx)
                if gt != -1:
                    remainder = text[gt + 1:]
                    # Try to find closing tag
                    close = re.search(_TAG_CLOSE, remainder, re.IGNORECASE)
                    body = remainder[:close.start()] if close else remainder
                    matches = [_FakeMatch("", variant, text[idx:gt + 1], body.strip())]
                break

    calls: list[dict[str, Any]] = []
    for m in matches:
        if isinstance(m, _FakeMatch):
            tag_name, attrs_str, body = m.tag, m.attrs_raw, m.body
        else:
            tag_name = m.group(1)
            attrs_str = m.group(2).strip()
            body = m.group(3).strip()

        # Extract tool name from attribute if present (e.g. name="run_shell")
        attr_name = None
        if attrs_str:
            name_m = _ATTR_NAME_RE.search(attrs_str)
            if name_m:
                attr_name = name_m.group(1)

        try:
            wrapped = _wrap_bare_json(body)
            obj = loads_obj(wrapped)
        except Exception as exc:
            if debug_log:
                debug_log(f"Tool parse FAIL ({tag_name}): {exc!r} | body={body!r}")
            continue

        # If tag had name= attribute and the parsed dict has no "name" key,
        # inject the tool name + treat the rest as arguments
        if attr_name and "name" not in obj:
            obj = {"name": attr_name, "arguments": obj}

        calls.append(obj)
    return calls


class _FakeMatch:
    """Minimal stand-in used by the fallback path."""
    __slots__ = ("tag", "attrs_raw", "body")

    def __init__(self, _unused: str, tag: str, attrs_raw: str, body: str) -> None:
        self.tag = tag
        self.attrs_raw = attrs_raw
        self.body = body
