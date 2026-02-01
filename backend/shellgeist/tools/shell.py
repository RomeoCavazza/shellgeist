from __future__ import annotations

from pathlib import Path


def plan_shell(task: str, *, root: Path) -> list[str]:
    # placeholder minimal: root-aware
    return [f"echo 'TODO shell plan for {task}'"]
