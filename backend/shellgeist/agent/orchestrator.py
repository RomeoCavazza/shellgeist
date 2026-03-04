"""Tool call queueing and no-tool-call decision logic."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from shellgeist.util_json import loads_obj


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


# ── Plaintext tool call detection ───────────────────────────────────────
# 7B models sometimes output tool calls as plain text instead of XML tags.
# E.g. "ShellCommandInput: {"command": "ls"}" or "I'll use run_shell: {"command": "ls"}"

# Map model-hallucinated class names to actual tool names
_CLASS_TO_TOOL: dict[str, str] = {
    "shellcommandinput": "run_shell",
    "runshellinput": "run_shell",
    "runshellcommand": "run_shell",
    "runcommand": "run_shell",
    "readfileinput": "read_file",
    "writefileinput": "write_file",
    "listfilesinput": "list_files",
    "listdirectoryinput": "list_files",
    "findfilesinput": "find_files",
    "editfileinput": "edit_file",
    "run_shell": "run_shell",
    "read_file": "read_file",
    "write_file": "write_file",
    "list_files": "list_files",
    "find_files": "find_files",
    "edit_file": "edit_file",
    "get_repo_map": "get_repo_map",
    "start_shell_session": "start_shell_session",
    "exec_shell_session": "exec_shell_session",
    "run_nix_python": "run_nix_python",
}

# Pattern: ToolName: { json } or ToolName({ json }) or I'll call tool_name: { json }
_PLAINTEXT_TOOL_RE = re.compile(
    r'(?:^|\n)\s*'
    r'(?:(?:I\'?ll\s+(?:use|call|run)\s+)?'  # optional "I'll use" prefix
    r'([A-Za-z_][A-Za-z0-9_]*)'              # tool/class name
    r'[:\s(]+\s*)'                            # separator (: or ( or space)
    r'(\{[^}]*\})',                           # JSON body
    re.IGNORECASE | re.DOTALL,
)


def extract_plaintext_tool_calls(content: str) -> list[dict[str, Any]]:
    """Extract tool calls written as plain text (without XML tags).

    Returns a list of {"name": ..., "arguments": {...}} dicts, same
    format as parse_xml_tool_use.  Returns empty list if nothing found.
    """
    calls: list[dict[str, Any]] = []
    for m in _PLAINTEXT_TOOL_RE.finditer(content):
        raw_name = m.group(1).strip().lower()
        json_body = m.group(2)

        tool_name = _CLASS_TO_TOOL.get(raw_name)
        if not tool_name:
            continue

        try:
            obj = loads_obj(json_body)
        except Exception:
            continue

        if not isinstance(obj, dict):
            continue

        calls.append({"name": tool_name, "arguments": obj})

    return calls


def _looks_like_final_response(content: str) -> bool:
    """Heuristic: if the LLM produced a conversational response without
    any tool_use attempt, treat it as a final answer rather than looping
    forever asking for 'Status: DONE'."""
    stripped = content.strip()
    if not stripped:
        return False
    # If there are any tool tags, this is NOT a final response — it's a failed parse
    if re.search(r"<(?:tool_use|tool_request|tool_call|tool)\b", stripped, re.IGNORECASE):
        return False
    # If the LLM hallucinated tool observations, not a final response
    if re.search(r"<tool_observation\b", stripped, re.IGNORECASE):
        return False
    # If the text contains plaintext tool calls, not a final response
    if extract_plaintext_tool_calls(stripped):
        return False
    # Must have some meaningful text (not just a Thought: line)
    lines = [l.strip() for l in stripped.splitlines() if l.strip()]
    non_thought = [l for l in lines if not l.lower().startswith("thought:")]
    if len(non_thought) < 1:
        return False

    # Require explicit completion markers or substantial response length.
    # Short, vague replies like "Terminé" or "Done" mid-task are NOT final
    # — they often indicate the LLM forgot to continue with the next step.
    lower = stripped.lower()

    # Explicit protocol marker always counts
    if "status: done" in lower:
        return True

    # A question to the user is a final response (agent is asking for input)
    if stripped.rstrip().endswith("?"):
        return True

    # Substantial text (>= 5 lines of non-thought content) is likely a real answer.
    # Raised from 3 to 5 because 7B models produce 3-4 lines of hallucinated
    # results and then stop without actually doing anything.
    if len(non_thought) >= 5:
        return True

    # Short completion words ("Terminé", "Done", "Voilà") are only final
    # when accompanied by enough context (>= 2 non-thought lines).
    # Allow short completions like "Voici le listing.\nTerminé."
    completion_markers = (
        "terminé", "done.", "done!", "completed", "finished",
        "voilà", "c'est fait", "here is", "voici",
    )
    has_completion = any(m in lower for m in completion_markers)
    return bool(has_completion and len(non_thought) >= 2)


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

    if re.search(r"<(?:tool_use|tool_request|tool_call|tool)\b", content, re.IGNORECASE):
        feedback = (
            "PARSE_ERROR: Your response contained tool XML tags but the content was not valid JSON. "
            "Use EXACTLY this format: <tool_use>{\"name\": \"...\", \"arguments\": {...}}</tool_use>\n"
            f"RAW_CONTENT: {content[:200]}"
        )
        return NoToolDecision(action="continue", feedback=feedback)

    # Allow conversational / final answers without requiring 'Status: DONE'
    if _looks_like_final_response(content):
        if completion_blocker:
            return NoToolDecision(action="continue", feedback=completion_blocker)
        return NoToolDecision(action="complete", final_response=extract_final_response(content))

    return NoToolDecision(
        action="continue",
        feedback=(
            "FAILURE: No tool calls and no final response detected. "
            "You MUST either: (1) call a tool to proceed with the task, or "
            "(2) provide a final answer ending with 'Status: DONE'. "
            "Do NOT respond with only a short word like 'Terminé' — "
            "explain what was accomplished or call the next tool."
        ),
    )


def build_schema_error_message(func_name: str, missing: list[str]) -> str:
    return (
        f"TOOL_SCHEMA_ERROR: {func_name} missing required args: {', '.join(missing)}. "
        "Use exact tool schema and retry with complete parameters."
    )
