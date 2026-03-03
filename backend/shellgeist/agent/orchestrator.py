"""Tool call queueing and no-tool-call decision logic."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


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


def _looks_like_final_response(content: str) -> bool:
    """Heuristic: if the LLM produced a conversational response without
    any tool_use attempt, treat it as a final answer rather than looping
    forever asking for 'Status: DONE'."""
    stripped = content.strip()
    if not stripped:
        return False
    # Must have some meaningful text (not just a Thought: line)
    lines = [l.strip() for l in stripped.splitlines() if l.strip()]
    non_thought = [l for l in lines if not l.lower().startswith("thought:")]
    # If there's at least one line of actual content, consider it final
    return len(non_thought) >= 1


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

    # Allow conversational / final answers without requiring 'Status: DONE'
    if _looks_like_final_response(content):
        if completion_blocker:
            return NoToolDecision(action="continue", feedback=completion_blocker)
        return NoToolDecision(action="complete", final_response=extract_final_response(content))

    return NoToolDecision(
        action="continue",
        feedback="FAILURE: No tool calls and no final response. Use a tool to proceed or provide a final answer.",
    )


def build_schema_error_message(func_name: str, missing: list[str]) -> str:
    return (
        f"TOOL_SCHEMA_ERROR: {func_name} missing required args: {', '.join(missing)}. "
        "Use exact tool schema and retry with complete parameters."
    )
