"""XML tool-use parser: extracts structured tool calls from LLM output."""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from shellgeist.util_json import loads_obj


def parse_xml_tool_use(
    text: str,
    *,
    debug_log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    pattern_body = r"<tool_use>(.*?)(?:</tool_use>|</tool_[a-z_]+>)"
    matches_body = re.findall(pattern_body, text, re.DOTALL | re.IGNORECASE)
    if not matches_body and "<tool_use>" in text:
        matches_body = [text.split("<tool_use>", 1)[1]]

    calls: list[dict[str, Any]] = []
    for raw in matches_body:
        content = raw.strip()
        try:
            calls.append(loads_obj(content))
        except Exception as exc:
            if debug_log:
                debug_log(f"Tool parse FAIL: {exc}")
    return calls
