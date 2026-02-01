from __future__ import annotations

from pathlib import Path


def plan(goal: str, *, root: Path) -> list[dict]:
    # placeholder minimal: on respecte l’API root-aware
    return [
        {"kind": "edit", "file": "README.md", "instruction": f"Add Roadmap about: {goal}"},
        {"kind": "shell", "command": "mkdir -p docs"},
    ]
