"""LLM output normalization & salvage helpers.

Moved from tools/normalize.py for better semantic separation.
"""
from __future__ import annotations

import re

# =============================================================================
# LLM STRING AUTO-UNESCAPE
# =============================================================================


def maybe_unescape_llm_string(s: str) -> str:
    """Un-double-escape common sequences (``\\\\n`` → ``\\n``).

    Some models double-escape JSON string payloads, so fields arrive with
    literal ``"\\\\n"`` instead of real newlines.  If it looks like that,
    unescape the most common sequences.
    """
    if not isinstance(s, str) or not s:
        return s

    # Heuristic: if we see \\n but no real newlines, it's probably double-escaped.
    if "\\n" in s and "\n" not in s:
        s2 = s
        s2 = s2.replace("\\r\\n", "\n")
        s2 = s2.replace("\\n", "\n")
        s2 = s2.replace("\\r", "\r")
        s2 = s2.replace("\\t", "\t")
        s2 = s2.replace('\\"', '"').replace("\\'", "'")
        s2 = s2.replace("\\\\", "\\")
        return s2

    return s


def strip_fences(s: str) -> str:
    """Remove surrounding markdown code fences (`` ``` ``)."""
    s = (s or "").strip()
    if not s:
        return s
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].lstrip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


# Trailing junk that LLMs sometimes append to write_file content (protocol / JSON bleed)
# Match only at end: }} or }}..., or a final line "Status: DONE" / "Status: FAILED..."
_WRITE_FILE_JUNK_RE = re.compile(
    r"(?:\s*\}\s*\}+|\s*[\n\r]+\s*Status:\s*DONE\s*\.?\s*|\s*[\n\r]+\s*Status:\s*FAILED\s*:?\s*[^\n]*)\s*$",
    re.IGNORECASE,
)


def normalize_write_file_content(content: str) -> str:
    """Strip markdown fences and protocol junk from write_file content.

    - Leading ```python or ``` → removed (content only).
    - Trailing }} or }}..., Status: DONE, Status: FAILED... → removed.
    Use for every write_file payload so that when the model sends code in a block
    or bleeds protocol into content, we still write clean code.
    """
    s = (content or "").strip()
    if not s:
        return s
    s = strip_fences(s)
    for _ in range(3):
        prev = s
        s = _WRITE_FILE_JUNK_RE.sub("", s).strip()
        if s == prev:
            break
    return s


def extract_trailing_after_last_fence(content: str) -> str:
    """Return the text after the last closing markdown code fence (```).
    Used when the model outputs code + prose: we run the code as write_file, then show the prose as the assistant response."""
    if not content or not isinstance(content, str):
        return ""
    s = content.strip()
    # Find last occurrence of a line that closes a fence (e.g. \n``` or \n```\n)
    close_match = list(re.finditer(r"\n```\s*$", s, re.MULTILINE))
    if not close_match:
        close_match = list(re.finditer(r"\n```\s*(?=\n|$)", s))
    if not close_match:
        return ""
    last = close_match[-1]
    after = s[last.end() :].strip()
    # Remove trailing <tool_use>...</tool_use> and Status lines
    after = re.sub(r"\s*<tool_use>[\s\S]*$", "", after, flags=re.IGNORECASE)
    after = re.sub(r"\n?\s*Status:\s*(?:DONE|FAILED)[^\n]*\s*$", "", after, flags=re.IGNORECASE)
    return after.strip()


def strip_leading_code_fence(text: str) -> str:
    """Remove one leading markdown code block (e.g. ```python\\n...\\n```) so that
    <tool_use> or other content after it is found by parsers.
    Accepts any language: python, bash, json, etc.
    """
    if not text or not text.strip():
        return text
    s = text.lstrip()
    if not s.startswith("```"):
        return text
    first_newline = s.find("\n")
    if first_newline == -1:
        return text
    rest = s[first_newline + 1:]
    close = rest.find("\n```")
    if close == -1:
        for i, line in enumerate(rest.splitlines()):
            if line.strip().startswith("```"):
                lines = rest.splitlines()
                rest = "\n".join(lines[i + 1:]) if i + 1 < len(lines) else ""
                return rest.lstrip()
        # Unclosed fence: strip first line (```lang) and return the rest
        return rest.lstrip()
    rest = rest[close + 1:].lstrip()
    after_close = rest.find("\n")
    if after_close != -1:
        rest = rest[after_close + 1:]
    else:
        rest = ""
    return rest.lstrip()


# =============================================================================
# FULLTEXT SALVAGE — broken JSON → extract "content" field
# =============================================================================

_CONTENT_FIELD_RE = re.compile(
    r'"content"\s*:\s*"(?P<body>(?:\\.|[^"\\])*)"\s*[}\]]?\s*$',
    re.DOTALL,
)


def _unescape_json_string_fragment(s: str) -> str:
    """Best-effort unescape for a JSON string fragment (no surrounding quotes).

    Handles common escapes enough for our 'content' salvage path.
    """
    if not isinstance(s, str):
        return ""
    s = s.replace("\\r\\n", "\n")
    s = s.replace("\\n", "\n")
    s = s.replace("\\r", "\r")
    s = s.replace("\\t", "\t")
    s = s.replace('\\"', '"')
    s = s.replace("\\/", "/")
    s = s.replace("\\\\", "\\")
    return s


def extract_fulltext_content_salvage(raw: str) -> str | None:
    """Salvage the ``"content"`` field from broken JSON.

    When a model returns something like::

        { "content": "....   (missing closing braces/quotes)

    try to pull out the content anyway.
    """
    if not isinstance(raw, str) or not raw:
        return None

    txt = raw.strip()

    m = _CONTENT_FIELD_RE.search(txt)
    if m:
        body = m.group("body")
        return _unescape_json_string_fragment(body)

    needle = '"content": "'
    j = txt.find(needle)
    if j != -1:
        frag = txt[j + len(needle):]
        k = frag.rfind('"')
        if k > 0:
            body = frag[:k]
            return _unescape_json_string_fragment(body)

    return None


def salvage_broken_content_envelope(raw: str) -> str | None:
    """Salvage content from a broken JSON envelope with raw newlines.

    Handles responses shaped like::

        {
        "content": "
        <python code...>
        "
        }

    which is invalid JSON because of raw newlines inside the string value.
    """
    if not isinstance(raw, str):
        return None

    lines = raw.splitlines()
    if len(lines) < 3:
        return None

    if lines[0].strip() != "{":
        return None
    if not lines[1].lstrip().startswith('"content": "'):
        return None

    body = lines[2:]
    while body and body[-1].strip() in ('"', '"}', '"},', "}", "},"):
        body = body[:-1]

    return "\n".join(body).lstrip("\n")


def salvage_fulltext(raw: str) -> str:
    """Try all salvage strategies in order, return best-effort content.

    Cascade: ``extract_fulltext_content_salvage`` → ``salvage_broken_content_envelope`` → ``strip_fences``.
    The result is then un-double-escaped via ``maybe_unescape_llm_string``.
    """
    salv1 = extract_fulltext_content_salvage(raw)
    if isinstance(salv1, str) and salv1.strip():
        return maybe_unescape_llm_string(salv1)

    salv2 = salvage_broken_content_envelope(raw)
    if isinstance(salv2, str) and salv2.strip():
        return maybe_unescape_llm_string(salv2)

    return maybe_unescape_llm_string(strip_fences(raw))
