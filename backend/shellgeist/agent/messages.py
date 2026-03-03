"""Canned messages and formatters for agent UI output."""
from __future__ import annotations

SMALL_TALK_REPLY = "Prêt. Donne une tâche concrète (fichier + action)."
NO_ACTIONABLE_DECISION = "FAILURE: no actionable decision"
SCHEMA_ERROR_FINAL_RESPONSE = (
    "Blocked by malformed tool calls (missing required arguments) after multiple retries. "
    "I need a fresh attempt with valid tool payloads."
)
TOOL_EXECUTION_FAILED_DEFAULT = "Tool execution failed."


def session_repaired_message(*, dropped_count: int, deduped_count: int, normalized_count: int) -> str:
    return (
        "Session repaired before inference "
        f"(drop={dropped_count}, dedupe={deduped_count}, normalize={normalized_count})."
    )


def stream_failed_after_retries(reason: str | None, error_class: str | None) -> str:
    return f"Stream failed after retries: {reason or error_class or 'unknown'}"
