"""Tool call queueing and no-tool-call decision logic."""
from __future__ import annotations

import re
from pathlib import Path
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from shellgeist.agent.parsing.json_utils import loads_obj
from shellgeist.agent.parsing.normalize import strip_leading_code_fence, strip_fences, normalize_write_file_content
from shellgeist.agent.parsing.parser import parse_canonical_tool_use, parse_xml_tool_use


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


@dataclass
class ModelTurnClassification:
    kind: str  # tool_batch | none
    tool_calls: list[dict[str, Any]]
    canonical: bool = False
    used_fallback: bool = False


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
    "run_python_command": "run_shell",
    "runpythoncommand": "run_shell",
    "run_in_subshell": "run_shell",
    "runinsubshell": "run_shell",
    "cat": "read_file",
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
# Pattern: tool_name{ ... } (no separator; model outputs write_file{"path": "x", ...})
_PLAINTEXT_TOOL_NO_SEP_RE = re.compile(
    r'\b(write_file|read_file|list_files|run_shell|find_files|edit_file)\s*(\{)',
    re.IGNORECASE,
)


def _normalize_tool_payload(obj: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if isinstance(obj, list):
        for item in obj:
            calls.extend(_normalize_tool_payload(item))
        return calls
    if not isinstance(obj, dict):
        return calls

    # Wrapped payload: {"tool_use": {...}} or {"tool_use": [{...}]}
    if "tool_use" in obj:
        tu = obj.get("tool_use")
        if isinstance(tu, (dict, list)):
            calls.extend(_normalize_tool_payload(tu))
            if calls:
                return calls
        elif isinstance(tu, str):
            mapped = _CLASS_TO_TOOL.get(tu.lower())
            if mapped:
                args = {k: v for k, v in obj.items() if k != "tool_use"}
                calls.append({"name": mapped, "arguments": args})
                return calls

    # Canonical payload: {"name":"write_file","arguments":{...}}
    name = obj.get("name") or obj.get("tool_name") or obj.get("tool") or obj.get("action")
    if isinstance(name, str):
        tool_name = _CLASS_TO_TOOL.get(name.lower())
        if tool_name:
            args = obj.get("arguments") or obj.get("args") or obj.get("parameters")
            if not isinstance(args, dict):
                args = {
                    k: v for k, v in obj.items()
                    if k not in ("name", "tool_name", "tool", "action", "arguments", "args", "parameters")
                }
            # Common natural aliases
            if tool_name in ("write_file", "edit_file"):
                if "content" not in args and isinstance(args.get("contents"), str):
                    args["content"] = args["contents"]
                if "path" not in args:
                    p = args.get("file_path") or args.get("filename") or args.get("file")
                    if isinstance(p, str) and p.strip():
                        args["path"] = p
                if isinstance(args.get("content"), str):
                    args["content"] = normalize_write_file_content(args["content"])
            elif tool_name == "run_shell":
                if "command" not in args:
                    cmd = args.get("cmd") or args.get("script")
                    if isinstance(cmd, str) and cmd.strip():
                        args["command"] = cmd
            elif tool_name == "read_file":
                if "path" not in args:
                    p = args.get("file_path") or args.get("filename") or args.get("file")
                    if isinstance(p, str) and p.strip():
                        args["path"] = p
            calls.append({"name": tool_name, "arguments": args})
            return calls

    # Shorthand payloads:
    # {"write_file": {...}} or {"run_shell": {"command":"..."}}
    for k, v in obj.items():
        mapped = _CLASS_TO_TOOL.get(str(k).lower())
        if not mapped:
            continue
        if isinstance(v, dict):
            if mapped in ("write_file", "edit_file") and isinstance(v.get("content"), str):
                v = {**v, "content": normalize_write_file_content(v["content"])}
            calls.append({"name": mapped, "arguments": v})
        elif isinstance(v, str):
            # Natural shorthand for shell-like tools
            if mapped in ("run_shell", "exec_shell_session"):
                calls.append({"name": mapped, "arguments": {"command": v}})
            elif mapped == "read_file":
                calls.append({"name": mapped, "arguments": {"path": v}})
    return calls


def extract_plaintext_tool_calls(content: str) -> list[dict[str, Any]]:
    """Extract tool calls written as plain text (without XML tags).

    Handles:
    1. ToolName: { json } or ToolName( { json }
    2. tool_name{ json } (no separator)
    3. Content inside ```python or ``` code blocks
    4. Bare JSON with "name": "..."
    """
    raw = content or ""

    # 0. Try inside code fences (model wraps tool call in ```python, ```bash, ```json, or ```)
    for fence_re in (
        r"```python\s*([\s\S]*?)```",
        r"```bash\s*([\s\S]*?)```",
        r"```(?:json|javascript|js|lua|sh)\s*([\s\S]*?)```",
        r"```\s*([\s\S]*?)```",
    ):
        for block in re.finditer(fence_re, raw, re.IGNORECASE):
            inner = block.group(1).strip()
            if "write_file" in inner or "read_file" in inner or "list_files" in inner or "run_shell" in inner:
                calls = _extract_plaintext_tool_calls_impl(inner)
                if calls:
                    return calls

    return _extract_plaintext_tool_calls_impl(raw)


def _extract_plaintext_tool_calls_impl(content: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    # 1a. tool_name{ ... } (no separator; brace-balanced)
    for m in _PLAINTEXT_TOOL_NO_SEP_RE.finditer(content):
        name, brace_start = m.group(1).strip(), m.group(2)
        tool_name = _CLASS_TO_TOOL.get(name.lower(), name)
        start = m.start(2)
        depth = 0
        end = start
        for i, c in enumerate(content[start:], start=start):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if depth != 0:
            continue
        json_body = content[start:end]
        try:
            obj = loads_obj(json_body)
            if isinstance(obj, dict) and obj:
                args = obj.get("arguments", obj)
                if not isinstance(args, dict):
                    args = {k: v for k, v in obj.items() if k not in ("name", "arguments")}
                calls.append({"name": tool_name, "arguments": args or {}})
        except Exception:
            continue
    if calls:
        return calls

    # 1b. Classical "ToolName: { json }" match
    # 1b. Classical "ToolName: { json }" match
    for m in _PLAINTEXT_TOOL_RE.finditer(content):
        raw_name = m.group(1).strip().lower()
        json_body = m.group(2)
        tool_name = _CLASS_TO_TOOL.get(raw_name)
        if not tool_name:
            continue
        try:
            obj = loads_obj(json_body)
            if isinstance(obj, dict):
                calls.append({"name": tool_name, "arguments": obj})
        except Exception:
            continue

    if calls:
        return calls

    # 2) Try fenced JSON blocks
    fenced_blocks = re.findall(r"```json\s*([\s\S]*?)\s*```", content, re.IGNORECASE)
    for block in fenced_blocks:
        try:
            parsed = loads_obj(block)
            calls.extend(_normalize_tool_payload(parsed))
        except Exception:
            continue
    if calls:
        return calls

    # 3) Try parsing the entire message as JSON (object or array)
    stripped = content.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = loads_obj(stripped)
            calls.extend(_normalize_tool_payload(parsed))
        except Exception:
            pass
    if calls:
        return calls

    # 4. Search for any JSON block that has a "name" matching a tool
    # Regex for finding JSON objects (simple heuristic)
    json_blocks = re.findall(r'(\{(?:[^{}]|\{[^{}]*\})*\})', content, re.DOTALL)
    for block in json_blocks:
        try:
            obj = loads_obj(block)
            calls.extend(_normalize_tool_payload(obj))
        except Exception:
            continue

    return calls


def classify_model_turn(content: str) -> ModelTurnClassification:
    """Classify model output using a strict nominal contract first.

    The canonical format is parsed first; permissive parsers remain available
    only as fallbacks so the runtime can progressively tighten the contract.
    """
    # Accept leading markdown code block (e.g. ```python) so that tool_use after it is found
    content = strip_leading_code_fence(content or "")
    canonical_calls = parse_canonical_tool_use(content)
    if canonical_calls:
        return ModelTurnClassification(
            kind="tool_batch",
            tool_calls=canonical_calls,
            canonical=True,
            used_fallback=False,
        )

    xml_calls = parse_xml_tool_use(content)
    if xml_calls:
        return ModelTurnClassification(
            kind="tool_batch",
            tool_calls=xml_calls,
            canonical=False,
            used_fallback=True,
        )

    plaintext_calls = extract_plaintext_tool_calls(content)
    if plaintext_calls:
        return ModelTurnClassification(
            kind="tool_batch",
            tool_calls=plaintext_calls,
            canonical=False,
            used_fallback=True,
        )

    return ModelTurnClassification(kind="none", tool_calls=[])


def salvage_slope_to_tool_calls(
    content: str,
    strict_target: str,
    root: str,
) -> list[dict[str, Any]]:
    """When the model replies with raw code blocks or command lines instead of <tool_use>,
    convert them to tool calls so we don't reject the slope.

    - First ```python or ``` block that looks like code → write_file(strict_target, content)
    - Line matching 'timeout Ns python3 path' or 'python3 path.py' targeting strict_target → run_shell
    """
    if not content or not strict_target or not strict_target.lower().endswith(".py"):
        return []

    calls: list[dict[str, Any]] = []
    target_name = Path(strict_target).name

    # 1) First code block that looks like Python/code (not JSON/tool_use)
    for fence_re in (r"```python\s*([\s\S]*?)```", r"```\s*([\s\S]*?)```"):
        for m in re.finditer(fence_re, content, re.IGNORECASE):
            inner = m.group(1).strip()
            if "<tool_use>" in inner or '"name":' in inner and '"write_file"' in inner:
                continue
            if re.search(r"\b(?:def |import |class |if __name__)", inner):
                # HARDEN: Skip if it looks like a diff or is too fragmented
                if inner.startswith("---") or inner.startswith("@@ ") or "\n+" in inner or "\n-" in inner:
                    continue
                code = strip_fences(inner)
                # Require at least 5 lines or substantive content to be a full file
                if code and len(code) > 60 and code.count("\n") > 3:
                    calls.append({
                        "name": "write_file",
                        "arguments": {"path": strict_target, "content": code},
                    })
                break
        if calls:
            break

    # 2) Line that looks like validation command: timeout Ns python3 ... or python3 ...py
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if re.search(r"\btimeout\s+\d+[smh]?\s+python3\s+", line) or re.match(r"python3\s+\S+\.py", line):
            if target_name in line or strict_target in line:
                calls.append({"name": "run_shell", "arguments": {"command": line}})
                break

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
