"""Project context discovery: file listing, gitignore, tree snapshot."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, List

def discover_project_rules(root: str) -> str:
    """
    Scans for project-specific rule files and returns their content.
    Supported files: .shellgeist.md, AGENTS.md, CLAUDE.md
    """
    rules_files = [".shellgeist.md", "AGENTS.md", "CLAUDE.md", ".cursorrules", ".windsurfrules"]
    accumulated_rules = []
    
    root_path = Path(root).resolve()
    for filename in rules_files:
        p = root_path / filename
        if p.exists() and p.is_file():
            try:
                content = p.read_text(encoding="utf-8")
                if content:
                    accumulated_rules.append(f"### Rules from {filename}\n{content}")
            except Exception:
                pass
                
    return "\n\n".join(accumulated_rules) if accumulated_rules else ""

def summarize_history(history: List[dict[str, Any]], model: str, client: Any) -> str:
    """
    Uses the LLM to summarize a long history into a concise context block.
    """
    if not history:
        return ""
        
    summary_prompt = (
        "You are an expert developer assistant. Summarize the following conversation history "
        "concisely, focusing on tasks completed, decisions made, and current state. "
        "Keep it dense and technical."
    )
    
    # We only summarize if it's long, but here we just provide the logic
    history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history if m['role'] != 'system'])
    
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": summary_prompt},
                {"role": "user", "content": f"History to summarize:\n{history_text}"}
            ]
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        return f"Error summarizing history: {e}"

def get_enhanced_context(root: str) -> str:
    """
    Returns a string containing all project-specific context (rules, etc.)
    """
    rules = discover_project_rules(root)
    runtime_facts = [
        f"- daemon_python: {sys.executable}",
        f"- which_python3: {shutil.which('python3') or 'NOT_FOUND'}",
        f"- which_python: {shutil.which('python') or 'NOT_FOUND'}",
        f"- which_nix_shell: {shutil.which('nix-shell') or 'NOT_FOUND'}",
    ]

    parts = [f"\n\n### RUNTIME FACTS\n" + "\n".join(runtime_facts)]
    if rules:
        parts.append(f"\n\n### PROJECT SPECIFIC RULES\n{rules}")
    return "".join(parts)
