"""System prompt builder with tool schemas and project context."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from shellgeist.llm.rules import load_project_rules
except ImportError:
    def load_project_rules(root: str) -> str:
        try:
            base = Path(root).resolve()
            for path in (base / ".shellgeist.md", base / ".shellgeist" / "rules.md"):
                if path.exists() and path.is_file():
                    return path.read_text(encoding="utf-8")
        except Exception:
            pass
        return ""


def get_project_context(root: str) -> str:
    """Return project-specific context for the prompt. Kept short to avoid biasing the model."""
    rules = load_project_rules(root)
    if rules:
        rules = rules.strip()
        if len(rules) > 400:
            rules = rules[:400].rstrip() + "\n..."
        return f"\n### PROJECT RULES (optional)\n{rules}"
    return ""


def render_system_prompt(project_context: str, tools_str: str, local_rules: str | None = None, workspace_root: str = "") -> str:
    rules_section = ""
    if local_rules:
        rules_section = f"\nPROJECT RULES:\n{local_rules}\n"
    root_line = f"\nWORKSPACE ROOT: {workspace_root}\n" if workspace_root else ""

    return f"""You are ShellGeist, a coding assistant. You call tools by outputting <tool_use> with JSON. Workspace root = current directory.
{root_line}

FORMAT: One <tool_use> per line, no other XML/markdown. Example:
<tool_use>{{"name": "list_files", "arguments": {{"directory": "."}}}}</tool_use>

PATHS: Relative only ("README.md", "backend/", "."). No leading "/".

RULES:
1. Do only what the user asked in their last message. Do not add actions they did not ask for (e.g. do not create or run files unless they explicitly asked).
2. Reply with at most 3 <tool_use> per message. After seeing tool results: brief answer + one line "Status: DONE" or "Status: FAILED". Do not reply with only "Listed directory" unless the user asked only to list files.
3. write_file: "content" is one JSON string (use \\n for newlines). read_file: use "path". run_shell: use "python3 file.py" for Python scripts.
4. Only use tools from TOOLS below. Output real <tool_use> only; no explanations, no ``` code blocks, no step-by-step text before or after tool calls.
5. Terminal scripts are allowed: ASCII art, terminal animations, text games, curses-style UIs. When the user asks for one, create and run it; do not refuse.
{project_context}
{rules_section}
TOOLS:
{tools_str}
"""


def build_system_prompt(
    root: str,
    *,
    debug_log: Callable[[str], None] | None = None,
    tool_schemas_provider: Callable[[], list[dict[str, Any]]] | None = None,
    context_provider: Callable[[str], str] | None = None,
    local_rules: str | None = None,
) -> str:
    if tool_schemas_provider is None:
        from shellgeist.tools.base import registry
        tool_schemas_provider = registry.get_tool_schemas
    if context_provider is None:
        context_provider = get_project_context

    tools_str = json.dumps(tool_schemas_provider(), indent=2)
    project_context = context_provider(root)
    return render_system_prompt(project_context, tools_str, local_rules=local_rules, workspace_root=root)
