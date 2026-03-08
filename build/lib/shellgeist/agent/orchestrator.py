"""Tool call queueing and no-tool-call decision logic."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from shellgeist.agent.parsing.json_utils import loads_obj


def is_small_talk(goal: str) -> str | None:
    """Return a direct response if the goal is just a greeting/small talk.

    This heuristic bypasses the LLM loop for instant response to common greetings.
    """
    low = goal.strip().lower().rstrip("?.!")
    greetings = (
        "hey", "hi", "hello", "yo", "salut", "bonjour", "oi", "hola", "coucou",
        "test", "ca va", "comment ca va", "comment tu vas", "how are you",
        "tu es qui", "qui es-tu", "who are you", "c'est quoi shellgeist",
        "aide-moi", "help", "aide", "sos"
    )
    # Only match pure greetings (short messages with no task content).
    # Anything > 20 chars almost certainly contains a real request.
    if len(low) > 20:
        return None
    if any(low.startswith(g) for g in greetings) or len(low) < 3:
        # Basic check to avoid false positives on commands like "ls" or "rm"
        if len(low) > 3 or low in greetings:
            return "Bonjour ! Je suis ShellGeist. Comment puis-je t'aider avec ton code aujourd'hui ?"
    return None


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

    Handles:
    1. ToolName: { json }
    2. Bare JSON with "name": "..."
    """
    calls: list[dict[str, Any]] = []
    
    # 1. Classical "ToolName: { json }" match
    for m in _PLAINTEXT_TOOL_RE.finditer(content):
        raw_name = m.group(1).strip().lower()
        json_body = m.group(2)
        tool_name = _CLASS_TO_TOOL.get(raw_name)
        if not tool_name: continue
        try:
            obj = loads_obj(json_body)
            if isinstance(obj, dict):
                calls.append({"name": tool_name, "arguments": obj})
        except Exception:
            continue

    if calls: return calls

    # 2. Search for any JSON block that has a "name" matching a tool
    # Regex for finding JSON objects (simple heuristic)
    json_blocks = re.findall(r'(\{(?:[^{}]|\{[^{}]*\})*\})', content, re.DOTALL)
    for block in json_blocks:
        try:
            obj = loads_obj(block)
            if not isinstance(obj, dict): continue
            
            name = obj.get("name")
            if not isinstance(name, str): continue
            
            tool_name = _CLASS_TO_TOOL.get(name.lower())
            if tool_name:
                # Map "parameters" -> "arguments" if present
                args = obj.get("arguments") or obj.get("parameters") or obj
                if args == obj:
                    # If the whole object is the tool call, remove "name" from args
                    args = {k: v for k, v in obj.items() if k not in ("name", "parameters")}
                
                calls.append({"name": tool_name, "arguments": args})
        except Exception:
            continue

    return calls


def normalize_final_response(content: str) -> str:
    """Clean LLM final response for display: one status line, no trailing tool_use."""
    if not content or not content.strip():
        return ""
    s = content.strip()
    # Remove trailing <tool_use>...</tool_use> and anything after
    tool_tag = re.search(r"<tool_use\b", s, re.IGNORECASE)
    if tool_tag:
        s = s[: tool_tag.start()].strip()
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if not lines:
        return ""
    # Split into content lines and status lines; keep one status at end (prefer FAILED)
    rest = []
    status_lines = []
    for line in lines:
        low = line.lower()
        if low.startswith("status: failed") or low.startswith("status: done"):
            status_lines.append(line)
        else:
            rest.append(line)
    if status_lines:
        one = next((l for l in status_lines if "failed" in l.lower()), None) or status_lines[-1]
        # Avoid bare "Status: FAILED:" with nothing after the colon
        if one.strip().lower() in ("status: failed", "status: failed:"):
            one = "Status: FAILED (see tool error above)."
        rest.append(one)
    # Collapse ".Status: DONE.Status: FAILED: msg" style in single line
    result = "\n".join(rest)
    result = re.sub(r"(?i)\s*\.?\s*Status:\s*DONE\s*\.?\s*", "\n", result)
    result = re.sub(r"(?i)\s*\.?\s*Status:\s*FAILED\s*:?\s*", "\nStatus: FAILED: ", result)
    result = "\n".join(ln for ln in result.splitlines() if ln.strip()).strip()
    return result


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
    if "status: done" in lower or "status: failed" in lower:
        return True

    # A question to the user is a final response (agent is asking for input)
    if stripped.rstrip().endswith("?"):
        return True

    # Substantial text (>= 6 lines) is likely a real answer.
    # We raise this because 7B models can blather a lot.
    if len(non_thought) >= 6:
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
    any_tool_succeeded: bool = True,
) -> NoToolDecision:
    if "Status: FAILED" in content:
        return NoToolDecision(action="complete", final_response=extract_final_response(content))

    if "Status: DONE" in content:
        if not any_tool_succeeded:
            # Allow pure conversational replies (no action needed)
            stripped = content.strip()
            is_question = stripped.rstrip().endswith("?")
            if not is_question:
                return NoToolDecision(
                    action="continue",
                    feedback=(
                        "INVALID_COMPLETION: You said 'Status: DONE' but you have NOT called "
                        "any tool yet. You MUST actually perform the action (call a tool) before "
                        "declaring completion. Do NOT describe what you would do — DO IT."
                    ),
                )
        if completion_blocker:
            return NoToolDecision(action="continue", feedback=completion_blocker)
        return NoToolDecision(action="complete", final_response=extract_final_response(content))

    if re.search(r"<(?:tool_use|tool_request|tool_call|tool)\b", content, re.IGNORECASE):
        truncated = str(content)[:200]
        feedback = (
            "PARSE_ERROR: Your response contained tool XML tags but the content was not valid JSON. "
            "Use EXACTLY this format: <tool_use>{\"name\": \"...\", \"arguments\": {...}}</tool_use>\n"
            f"RAW_CONTENT: {truncated}"
        )
        return NoToolDecision(action="continue", feedback=feedback)

    # Allow conversational / final answers
    if _looks_like_final_response(content):
        # We require Status: DONE or a question to consider it truly complete.
        # This prevents 7B models from trailing off into hallucinations.
        is_done = "status: done" in content.lower()
        is_failed = "status: failed" in content.lower()
        is_question = content.strip().rstrip().endswith("?")

        if is_done or is_failed or is_question:
            if not any_tool_succeeded and is_done:
                # Still check if we did anything if it says "DONE"
                return NoToolDecision(
                    action="continue",
                    feedback=(
                        "You said 'Status: DONE' but you have NOT called any tool. "
                        "Perform the action first."
                    )
                )
            if not any_tool_succeeded and is_question:
                # LLM asked a clarifying question instead of using a tool — reject it.
                return NoToolDecision(
                    action="continue",
                    feedback=(
                        "CLARIFICATION_FORBIDDEN: Do NOT ask for clarification. "
                        "The user's request is actionable. Call the appropriate tool immediately."
                    ),
                )
            return NoToolDecision(action="complete", final_response=extract_final_response(content))

        # If it's neither "DONE" nor a question, it's likely blather or an unfinished task.
        return NoToolDecision(
            action="continue",
            feedback=(
                "If you are finished, you MUST end your response with 'Status: DONE'. "
                "If you are not finished, keep going by calling the next tool. "
                "Do NOT just explain your progress."
            )
        )

    return NoToolDecision(
        action="continue",
        feedback=(
            "FORMAT_ERROR: You did not emit a tool call. "
            "Output ONLY a <tool_use> tag like this — no other text:\n"
            "<tool_use>{\"name\": \"list_files\", \"arguments\": {\"directory\": \".\"}}</tool_use>"
        ),
    )


def build_schema_error_message(func_name: str, missing: list[str]) -> str:
    return (
        f"TOOL_SCHEMA_ERROR: {func_name} missing required args: {', '.join(missing)}. "
        "Use exact tool schema and retry with complete parameters."
    )
