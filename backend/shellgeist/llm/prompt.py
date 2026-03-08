"""System prompt builder with tool schemas and project context."""
from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Project context helpers (inlined from former context.py)
# ---------------------------------------------------------------------------

_RULES_FILES = (".shellgeist.md", "AGENTS.md", "CLAUDE.md", ".cursorrules", ".windsurfrules")


def discover_project_rules(root: str) -> str:
    """Scan for project-specific rule files and return their content."""
    root_path = Path(root).resolve()
    parts: list[str] = []
    for filename in _RULES_FILES:
        p = root_path / filename
        if p.exists() and p.is_file():
            try:
                content = p.read_text(encoding="utf-8")
                if content:
                    parts.append(f"### Rules from {filename}\n{content}")
            except Exception:
                pass
    return "\n\n".join(parts) if parts else ""


def get_enhanced_context(root: str) -> str:
    """Return project-specific context (rules + runtime facts)."""
    rules = discover_project_rules(root)
    runtime_facts = [
        f"- daemon_python: {sys.executable}",
        f"- which_python3: {shutil.which('python3') or 'NOT_FOUND'}",
        f"- which_python: {shutil.which('python') or 'NOT_FOUND'}",
        f"- which_nix_shell: {shutil.which('nix-shell') or 'NOT_FOUND'}",
    ]

    parts = ["\n\n### RUNTIME FACTS\n" + "\n".join(runtime_facts)]
    if rules:
        parts.append(f"\n\n### PROJECT SPECIFIC RULES\n{rules}")
    return "".join(parts)


def render_system_prompt(project_context: str, tools_str: str, local_rules: str | None = None) -> str:
    rules_section = ""
    if local_rules:
        rules_section = f"\nPROJECT-SPECIFIC RULES:\n{local_rules}\n"

    return f"""You are ShellGeist, a task-oriented AI assistant for Neovim.

CRITICAL PROTOCOL:
1. **Goal**: Perform the user's request using tools.
2. **Tool Use**: Use EXACTLY this XML format and nothing else:
   <tool_use>{{"name": "tool_name", "arguments": {{"key": "value"}}}}</tool_use>
3. **Action First**: Do NOT explain before calling a tool. Do NOT apologize. Do NOT output bare JSON. JUST emit the <tool_use> tag.
4. **One Action**: Only ONE tool call per response.
5. **After Results**: When you receive a <tool_observation>, present the results to the user clearly, then end with: Status: DONE
6. **Completion**: Always end your final response with: Status: DONE

EXAMPLES (copy this format exactly):

User: "list files"
Assistant: <tool_use>{{"name": "list_files", "arguments": {{"directory": "."}}}}</tool_use>

[after receiving tool_observation with file list]
Assistant: Voici les fichiers du répertoire courant : [list results here]
Status: DONE

User: "read file src/main.py"
Assistant: <tool_use>{{"name": "read_file", "arguments": {{"path": "src/main.py"}}}}</tool_use>

FORBIDDEN FORMATS (never do these):
- {{"response": "..."}}          ← WRONG, not a tool call
- "I'll list the files..."       ← WRONG, explain nothing before acting
- {{"name": "list_files"}}       ← WRONG, missing <tool_use> wrapper
- Calling the same tool again after already receiving its results ← WRONG

WORKSPACE: {project_context}

{rules_section}

AVAILABLE TOOLS:
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
        context_provider = get_enhanced_context

    if debug_log:
        debug_log("registry.get_tool_schemas()...")
    tools_str = json.dumps(tool_schemas_provider(), indent=2)

    if debug_log:
        debug_log("get_enhanced_context()...")
    project_context = context_provider(root)

    if debug_log:
        debug_log("Compiling prompt...")
    return render_system_prompt(project_context, tools_str, local_rules=local_rules)
