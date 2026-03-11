"""XML tool-use parser: extracts structured tool calls from LLM output.

Moved from tools/parser.py for better semantic separation.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from shellgeist.agent.parsing.json_utils import loads_obj
from shellgeist.agent.parsing.normalize import strip_fences

# Opening: <tool_use>, <tool_request>, <tool_call>, <tool call>, <tool_invocation>, <tool>
# Optionally with attributes (e.g. name="run_shell")
_TAG_OPEN = r"<(tool_use|tool_request|tool_call|tool\s+call|tool_invocation|tool)\b([^>]*)>"
# Closing: any </tool...> variant, including </tool call>, </tool_invocation>
_TAG_CLOSE = r"</(tool_use|tool_request|tool_call|tool\s+call|tool_invocation|tool[_a-z]*)>"
# Full pattern: open tag → body → close tag
_TOOL_RE = re.compile(
    _TAG_OPEN + r"(.*?)" + _TAG_CLOSE,
    re.DOTALL | re.IGNORECASE,
)
# Same but body can end at markdown fence (model puts <tool_use> inside ```python and omits </tool_use>)
_TOOL_RE_UNTIL_FENCE = re.compile(
    _TAG_OPEN + r"(.*?)(?=" + _TAG_CLOSE + r"|\n```)",
    re.DOTALL | re.IGNORECASE,
)
# Markdown code block: ```tool_use\n{...}\n``` (model sometimes outputs this instead of XML)
_MD_TOOL_RE = re.compile(
    r"```\s*tool_use\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_CANONICAL_TOOL_RE = re.compile(
    r"<tool_use>\s*(\{.*?\})\s*</tool_use>",
    re.DOTALL,
)


def _extract_brace_balanced_body(text: str, start_marker: str) -> str | None:
    """Extract a single {...} body from text after start_marker, handling nested braces."""
    idx = text.find(start_marker)
    if idx == -1:
        return None
    start = text.find("{", idx + len(start_marker))
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    quote_char = None
    i = start
    while i < len(text):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == quote_char:
                in_string = False
            i += 1
            continue
        if c in ('"', "'"):
            in_string = True
            quote_char = c
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return None


# Extract name="..." from tag attributes
_ATTR_NAME_RE = re.compile(r'name\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
# XML-like body: <name>tool_name</name> and/or <arguments>...</arguments> or <parameters>...</parameters>
_XML_NAME_RE = re.compile(r"<name>\s*([^<]+?)\s*</name>", re.IGNORECASE | re.DOTALL)
_XML_ARGS_OPEN = re.compile(r"<arguments?\s*>", re.IGNORECASE)
# Match <arguments>...</arguments> OR <parameters>...</parameters> with nested tags allowed in content
_XML_ARGS_BLOCK_RE = re.compile(
    r"<arguments?\s*>(.*?)</arguments?>",
    re.IGNORECASE | re.DOTALL,
)
_XML_PARAMS_BLOCK_RE = re.compile(
    r"<parameters?\s*>(.*?)</parameters?>",
    re.IGNORECASE | re.DOTALL,
)
# Inner XML key/value: <key>value</key>
_XML_KV_RE = re.compile(
    r"<([A-Za-z_][A-Za-z0-9_]*)>\s*([^<]*?)\s*</\1>",
    re.IGNORECASE | re.DOTALL,
)
# Salvage write_file when content uses Python + concatenation (invalid JSON)
_WRITE_FILE_PATH_RE = re.compile(r'"path"\s*:\s*"([^"]*)"', re.IGNORECASE)
_WRITE_FILE_CONTENT_RE = re.compile(
    r'"content"\s*:\s*((?:"(?:[^"\\]|\\.)*"\s*(?:\+\s*)?)+)',
    re.IGNORECASE | re.DOTALL,
)
_ONE_QUOTED_STR = re.compile(r'"((?:[^"\\]|\\.)*)"')

_TOOL_NAME_ALIASES: dict[str, str] = {
    "run_in_subshell": "run_shell",
    "runinsubshell": "run_shell",
    "run_python_command": "run_shell",
    "runpythoncommand": "run_shell",
    "cat": "read_file",
}


def _wrap_bare_json(text: str) -> str:
    """Wrap bare key-value pairs in {} if not already an object."""
    s = text.strip()
    if s.startswith("{"):
        return s
    # Looks like bare "key": "value" pairs → wrap
    if re.match(r'^"[^"]+"\s*:', s):
        return "{" + s + "}"
    return s


def _parse_xml_like_body(body: str, attr_name: str | None = None) -> dict[str, Any] | None:
    """Parse XML-style tool_use body: <name>X</name><arguments>...</arguments> or <parameters>...</parameters>.

    Many models output this instead of JSON. We accept it so we don't
    trigger PROTOCOL_VIOLATION and can still run the tool.
    """
    body = (body or "").strip()
    if not body:
        return None
    name_match = _XML_NAME_RE.search(body)
    name = (name_match.group(1).strip() if name_match else None) or attr_name
    args_block = _XML_ARGS_BLOCK_RE.search(body) or _XML_PARAMS_BLOCK_RE.search(body)
    arguments: dict[str, Any] = {}
    if args_block:
        inner = args_block.group(1).strip()
        if inner.startswith("{"):
            try:
                arguments = loads_obj(inner)
                if not isinstance(arguments, dict):
                    arguments = {}
            except Exception:
                pass
        if not arguments and inner:
            # Parse <key>value</key> pairs
            for m in _XML_KV_RE.finditer(inner):
                key, val = m.group(1), m.group(2).strip()
                arguments[key] = val
    if not name:
        return None
    return {"name": name, "arguments": arguments}


def _salvage_write_file(body: str) -> dict[str, Any] | None:
    """When write_file body is invalid JSON (e.g. content uses Python + concat), extract path and content."""
    if not body or "write_file" not in body.lower():
        return None
    path_m = _WRITE_FILE_PATH_RE.search(body)
    path = path_m.group(1) if path_m else ""
    if not path:
        return None
    content_m = _WRITE_FILE_CONTENT_RE.search(body)
    if not content_m:
        return {"name": "write_file", "arguments": {"path": path, "content": ""}}
    inner = content_m.group(1)
    parts: list[str] = []
    for m in _ONE_QUOTED_STR.finditer(inner):
        raw = m.group(1)
        raw = raw.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")
        parts.append(raw)
    content = "".join(parts)
    return {"name": "write_file", "arguments": {"path": path, "content": content}}


def parse_xml_tool_use(
    text: str,
    *,
    debug_log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    # Strip markdown code fences so <tool_use> inside ```json / ```python / ```bash etc. is still found
    cleaned = re.sub(r"^```\s*[a-z0-9]*\s*\n?", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    if cleaned != text:
        text = cleaned

    matches: list[Any] = list(_TOOL_RE.finditer(text))
    matches_fence: list[Any] = list(_TOOL_RE_UNTIL_FENCE.finditer(text))
    if len(matches_fence) > len(matches):
        matches = matches_fence

    # Fallback 1: markdown code blocks ```tool_use\n{...}```
    if not matches:
        md_calls = []
        for md in _MD_TOOL_RE.finditer(text):
            body = md.group(1).strip()
            try:
                wrapped = _wrap_bare_json(body)
                obj = loads_obj(wrapped)
                if isinstance(obj, dict) and obj.get("name"):
                    args = obj.get("arguments") or obj
                    if args is obj:
                        args = {k: v for k, v in obj.items() if k not in ("name", "arguments")}
                    md_calls.append({"name": obj["name"], "arguments": args})
            except Exception:
                continue
        if md_calls:
            return _normalize_calls(md_calls)

    # Fallback 2: split on any opening tool tag if regex didn't match
    if not matches:
        for variant in ("tool_use", "tool_request", "tool_call", "tool_invocation", "tool"):
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
            # Many models output XML-style <name>X</name><arguments>...</arguments>
            obj = _parse_xml_like_body(body, attr_name=attr_name)
            if obj is None and (attr_name == "write_file" or "write_file" in body):
                obj = _salvage_write_file(body)
            if obj is None:
                if debug_log:
                    debug_log(f"Tool parse FAIL ({tag_name}): {exc!r} | body={body!r}")
                continue

        # If tag had name= attribute and the parsed dict has no "name" key,
        # inject the tool name + treat the rest as arguments
        if attr_name and "name" not in obj:
            obj = {"name": attr_name, "arguments": obj}

        calls.append(obj)
    return _normalize_calls(calls)


def parse_canonical_tool_use(text: str) -> list[dict[str, Any]]:
    """Parse only the canonical <tool_use>{json}</tool_use> format.

    This is the nominal contract expected from the model. More permissive
    parsers remain available as compatibility fallbacks.
    Uses brace-balanced extraction so nested JSON in "arguments" is parsed correctly.
    """
    raw = text or ""
    calls: list[dict[str, Any]] = []
    pos = 0
    while True:
        open_tag = raw.find("<tool_use>", pos)
        if open_tag == -1:
            break
        # Extract body with balanced braces (handles "arguments": { ... })
        body = _extract_brace_balanced_body(raw[open_tag:], "<tool_use>")
        if body:
            try:
                obj = loads_obj(body)
            except Exception:
                obj = None
            if isinstance(obj, dict) and obj.get("name"):
                normalized = _normalize_calls([obj])
                for call in normalized:
                    if isinstance(call, dict) and isinstance(call.get("name"), str):
                        calls.append(call)
        # Move past this tag for next iteration (support multiple <tool_use> in one message)
        close_tag = raw.find("</tool_use>", open_tag)
        pos = close_tag + len("</tool_use>") if close_tag != -1 else open_tag + 1
    return calls


def _normalize_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure each call has name and arguments keys."""
    out = []
    for c in calls:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or c.get("tool_name") or c.get("tool") or c.get("action")
        if not name:
            handled_shorthand = False
            for k, v in c.items():
                mapped = _TOOL_NAME_ALIASES.get(str(k).lower())
                if not mapped:
                    continue
                if isinstance(v, dict):
                    out.append({"name": mapped, "arguments": v})
                    handled_shorthand = True
                elif isinstance(v, str):
                    if mapped == "read_file":
                        out.append({"name": mapped, "arguments": {"path": v}})
                        handled_shorthand = True
                    elif mapped == "run_shell":
                        out.append({"name": mapped, "arguments": {"command": v}})
                        handled_shorthand = True
                break
            if handled_shorthand:
                continue
        if isinstance(name, str):
            name = _TOOL_NAME_ALIASES.get(name.lower(), name)
        args = c.get("arguments")
        if args is None:
            args = {
                k: v for k, v in c.items()
                if k not in ("name", "tool_name", "tool", "action", "arguments", "args", "parameters")
            }
        if not isinstance(args, dict):
            args = c.get("args") or c.get("parameters") or {}
        if not isinstance(args, dict):
            args = {}
        if isinstance(name, str):
            low = name.lower()
            if low in ("write_file", "edit_file"):
                if "content" not in args and isinstance(args.get("contents"), str):
                    args["content"] = args["contents"]
                if "path" not in args:
                    p = args.get("file_path") or args.get("filename") or args.get("file")
                    if isinstance(p, str) and p.strip():
                        args["path"] = p
                if isinstance(args.get("content"), str):
                    args["content"] = strip_fences(args["content"])
            elif low == "run_shell":
                if "command" not in args:
                    cmd = args.get("cmd") or args.get("script")
                    if isinstance(cmd, str) and cmd.strip():
                        args["command"] = cmd
            elif low == "read_file":
                if "path" not in args:
                    p = args.get("file_path") or args.get("filename") or args.get("file")
                    if isinstance(p, str) and p.strip():
                        args["path"] = p
        if name:
            out.append({"name": name, "arguments": args or {}})
    return out


class _FakeMatch:
    """Minimal stand-in used by the fallback path."""
    __slots__ = ("tag", "attrs_raw", "body")

    def __init__(self, _unused: str, tag: str, attrs_raw: str, body: str) -> None:
        self.tag = tag
        self.attrs_raw = attrs_raw
        self.body = body
