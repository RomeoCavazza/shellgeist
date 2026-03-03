"""Tool preview: generates human-readable code snippets for tool calls."""
from __future__ import annotations

from typing import Any


def code_preview_for_tool(func_name: str, arguments: dict[str, Any]) -> str | None:
    if not isinstance(arguments, dict):
        return None

    if func_name == "write_file":
        content = str(arguments.get("content") or "").strip()
        if not content:
            return None
        return content[:1200]

    if func_name == "edit_apply":
        patch = str(arguments.get("patch") or "").strip()
        if not patch:
            return None
        return patch[:1200]

    if func_name == "edit_apply_full":
        text = str(arguments.get("text") or "").strip()
        if not text:
            return None
        return text[:1200]

    return None
