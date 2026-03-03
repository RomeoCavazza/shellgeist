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


def render_system_prompt(project_context: str, tools_str: str) -> str:
    return f"""You are ShellGeist, an autonomous AI developer assistant for Neovim.
{project_context}

### PROTOCOL
1.  **THOUGHT FIRST**: Every response MUST start with `Thought: `.
2.  **TOOL EXECUTION**: Actions on files/shell MUST use `<tool_use>{{"name": "...", "arguments": {{...}}}}</tool_use>`.
3.  **NO MARKDOWN CODE**: Do not use ```python for actions. Tools MUST be XML `<tool_use>`.
4.  **COMPLETION**: When the task is finished, end with `Status: DONE`.
5.  **CONVERSATIONAL QUERIES**: For greetings, explanations, or questions that don't need tools, respond naturally after `Thought: ` — no tool_use needed. Be concise.
6.  **STOP WHEN DONE**: After completing the user's request, present the result and stop. Do NOT continue investigating or reading more files unless the user asked for it.
7.  **PARAMETER NAMES**: Always use the exact parameter names from the tool schema. For `read_file` use `path`, for `write_file` use `path` and `content`.

### FILE CREATION & EDITING (CRITICAL)
- To create or overwrite a file: use `write_file` with `path` and `content`. Always provide the COMPLETE file content.
- To modify an existing file: use `edit_file` with `path` and `instruction`.
- NEVER use `run_shell` or `run_nix_python` to create files (no `echo >`, `cat <<`, `python -c "open(..."`, etc).
- Workflow for code tasks: `write_file` → verify with `run_shell` or `run_nix_python` → fix if needed.

### ANTI-LAZINESS
- Never use `...` or `# rest of code`.
- NEVER say "as previously provided" or "contenu précédemment donné".
- Prefer minimal changes that satisfy the task. Do not rewrite unrelated sections.

### SHELL & EXECUTION
- Before running Python scripts, verify interpreter availability (`command -v python3 || command -v python`).
- Do not run `./script.py` unless executable bit + shebang are present. Prefer `python3 script.py`.
- IMPORTANT: `run_shell` calls are stateless. Environment changes do NOT persist between calls.
- For stateful workflows (nix-shell session, `export`, `cd`, venv activation), use `start_shell_session` + `exec_shell_session`/`read_shell_session`.

### NIX-SPECIFIC
- For Nix, never call bare `nix-shell -p ...`; always use one-shot `nix-shell -p ... --run '<command>'`.
- If multiple commands must share the same env, start a shell session with `shell: "nix-shell -p ..."` and run commands via `exec_shell_session`.
- For Python libraries on Nix, use `python3.withPackages` (example: `nix-shell -p "python3.withPackages (p: with p; [ pyglet ])" --run "python3 script.py"`).
- `run_nix_python` is a shortcut for running a command inside `nix-shell -p python3.withPackages(...)`. Use it to EXECUTE a script, not to create one.
- Do not generate temporary `shell.nix` files for simple package runs. Prefer direct `nix-shell -p ...` commands.
- Never use system package manager or privileged commands (`sudo`, `apt`, `apt-get`, `yum`, `dnf`, `pacman`, `zypper`, `apk`, `nix-env -i`).
- If runtime dependencies are unavailable after checks, stop with `Status: DONE` and explain the limitation briefly.

### SHELL SESSIONS
- For `exec_shell_session`/`write_shell_session`/`read_shell_session`/`close_shell_session`, `session_id` is mandatory and must come from a successful `start_shell_session`.
- If `exec_shell_session` returns `session_terminated` or `session_not_found`, do not retry stale IDs. Call `list_shell_sessions`, then start one fresh session and continue with its new `session_id`.
- If `exec_shell_session` returns `command_failed` with `alive: true`, the shell session is still alive; fix the command/file/dependency in the same session instead of starting a new one.

### FORMAT
Thought: <robotic justification>
<tool_use>{{\"name\": \"...\", \"arguments\": {{...}}}}</tool_use>

### AVAILABLE TOOLS
{tools_str}
"""


def build_system_prompt(
    root: str,
    *,
    debug_log: Callable[[str], None] | None = None,
    tool_schemas_provider: Callable[[], list[dict[str, Any]]] | None = None,
    context_provider: Callable[[str], str] | None = None,
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
    return render_system_prompt(project_context, tools_str)
