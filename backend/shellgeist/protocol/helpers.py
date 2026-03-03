"""Protocol helpers: response extraction, small-talk detection, thought parsing."""
from __future__ import annotations

import re

PROTOCOL_MARKDOWN_WITHOUT_TOOL = "ERROR: You provided code in markdown but NO <tool_use> tags. Use <tool_use> to execute."


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
    content = re.sub(r"<tool_use>.*?</tool_use>", "", content, flags=re.DOTALL | re.IGNORECASE)
    content = re.sub(r"^\s*Thoughts?:\s*.*?(?:\n\n|$)", "", content, flags=re.DOTALL | re.IGNORECASE)
    content = re.sub(r"^\s*Status:\s*DONE\s*$", "", content, flags=re.MULTILINE | re.IGNORECASE)
    content = content.strip()
    return content or "Terminé."


def extract_actionable_thought(content: str, *, has_tool_calls: bool) -> str | None:
    if not has_tool_calls:
        return None
    thought_match = re.search(r"Thoughts?:\s*(.*?)(?:\n\n|\n<tool_use|$)", content, re.DOTALL | re.IGNORECASE)
    if not thought_match:
        return None
    thought = thought_match.group(1).strip()
    return thought or None


def has_markdown_without_tool_calls(content: str, *, has_tool_calls: bool) -> bool:
    return "```" in str(content or "") and not has_tool_calls
