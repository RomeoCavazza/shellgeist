"""Protocol helpers: response extraction, small-talk detection, thought parsing."""
from __future__ import annotations

import re

PROTOCOL_MARKDOWN_WITHOUT_TOOL = "ERROR: You provided code in markdown but NO <tool_use> tags. Use <tool_use> to execute."

# Matches any tool XML block: <tool_use>, <tool_request>, <tool_call>, <tool>
_TOOL_BLOCK_RE = re.compile(
    r"<(?:tool_use|tool_request|tool_call|tool)\b[^>]*>.*?</(?:tool_use|tool_request|tool_call|tool[_a-z]*)>",
    re.DOTALL | re.IGNORECASE,
)
# Matches hallucinated tool observation blocks
_TOOL_OBS_RE = re.compile(
    r"<tool_observation\b[^>]*>.*?</tool_observation>",
    re.DOTALL | re.IGNORECASE,
)
# Also match unclosed tool tags (LLM sometimes doesn't close them)
_TOOL_OPEN_UNCLOSED_RE = re.compile(
    r"<(?:tool_use|tool_request|tool_call|tool)\b[^>]*>.*",
    re.DOTALL | re.IGNORECASE,
)


def is_small_talk(text: str) -> bool:
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    if len(normalized) > 24:
        return False
    return bool(
        re.fullmatch(
            r"(hi|hey|hello|yo|salut|bonjour|bonsoir|coucou|merci|thanks|ok|okay)[!.? ]*",
            normalized,
        )
    )


def extract_canonical_response(text: str) -> str:
    content = str(text or "")
    content = _TOOL_BLOCK_RE.sub("", content)
    content = _TOOL_OBS_RE.sub("", content)
    # Strip Plan: and Thoughts: headers and their content if they are at the start
    content = re.sub(r"^\s*Plan:\s*.*?(?:\n\n|$)", "", content, flags=re.DOTALL | re.IGNORECASE)
    content = re.sub(r"^\s*Thoughts?:\s*.*?(?:\n\n|$)", "", content, flags=re.DOTALL | re.IGNORECASE)
    content = re.sub(r"^\s*Status:\s*DONE\s*$", "", content, flags=re.MULTILINE | re.IGNORECASE)
    content = content.strip()
    return content or "Terminé."


def extract_actionable_thought(content: str, *, has_tool_calls: bool) -> str | None:
    """Extract the Plan: and Thought: sections from LLM output.

    The thought should be displayed to the user in all cases for transparency.
    """
    # Matches (Plan: ...)? (Thought: ...)
    # or just Plan: ...
    # or just Thought: ...
    pattern = (
        r"^\s*(?:"
        r"(?:Plan:\s*(?P<plan>.*?)(?:\n\n|\nThoughts?:|$))|"
        r"(?:Thoughts?:\s*(?P<thought>.*?)(?:\n\n|\n<(?:tool_use|tool_request|tool_call|tool)\b|\n[A-Z][a-zA-Z]+Input:|$))"
        r")+"
    )
    
    # Simpler approach: capture everything from start until tool/divider
    match = re.search(
        r"^\s*((?:Plan|Thoughts?):\s*.*?)(?:\n\n\n|\n<(?:tool_use|tool_request|tool_call|tool)\b|\n[A-Z][a-zA-Z]+Input:|$)",
        content,
        re.DOTALL | re.IGNORECASE | re.MULTILINE,
    )
    if not match:
        return None
    
    thought = match.group(1).strip()
    # Cut overly long thoughts
    lines = thought.splitlines()
    if len(lines) > 15:
        thought = "\n".join(lines[:15]) + "\n..."
    return thought or None


def has_markdown_without_tool_calls(content: str, *, has_tool_calls: bool) -> bool:
    if has_tool_calls:
        return False
    text = str(content or "")
    # Don't flag if content contains tool-like XML tags (parser may have failed
    # but the LLM was *trying* to use tools — retry won't help)
    if re.search(r"<(?:tool_use|tool_request|tool_call|tool)\b", text, re.IGNORECASE):
        return False
    return "```" in text
