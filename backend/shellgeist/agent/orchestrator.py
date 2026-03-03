"""Tool call queueing and no-tool-call decision logic."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class NoToolDecision:
    action: str  # complete | continue
    final_response: str | None = None
    feedback: str | None = None


class ToolCallQueue:
    def __init__(self, tool_calls: list[dict[str, Any]] | None = None) -> None:
        self._items = list(tool_calls or [])
        self._index = 0

    def has_next(self) -> bool:
        return self._index < len(self._items)

    def next(self) -> dict[str, Any] | None:
        if not self.has_next():
            return None
        item = self._items[self._index]
        self._index += 1
        return item


def decide_no_tool_action(
    content: str,
    *,
    completion_blocker: str | None,
    extract_final_response: Callable[[str], str],
) -> NoToolDecision:
    if "Status: DONE" in content:
        if completion_blocker:
            return NoToolDecision(action="continue", feedback=completion_blocker)
        return NoToolDecision(action="complete", final_response=extract_final_response(content))

    if "<tool_use>" in content:
        feedback = (
            "PARSE_ERROR: Your response contained <tool_use> tags but the content was not valid JSON. "
            f"Revise your format.\nRAW_CONTENT: {content[:200]}"
        )
        return NoToolDecision(action="continue", feedback=feedback)

    return NoToolDecision(
        action="continue",
        feedback="FAILURE: No tool calls and no 'Status: DONE'. Use a tool to proceed or say 'Status: DONE' if finished.",
    )


def build_schema_error_message(func_name: str, missing: list[str]) -> str:
    return (
        f"TOOL_SCHEMA_ERROR: {func_name} missing required args: {', '.join(missing)}. "
        "Use exact tool schema and retry with complete parameters."
    )
