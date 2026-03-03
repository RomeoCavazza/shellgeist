"""Agent run state machine: phase tracking and goal classification."""
from __future__ import annotations

import re
from dataclasses import dataclass


class AgentPhase:
    PLAN = "plan"
    EXECUTE_TOOL = "execute_tool"
    VERIFY = "verify"
    RECOVER = "recover"
    DONE = "done"
    FAIL = "fail"


@dataclass
class AgentRunState:
    task_terminal_only: bool
    goal_requires_verify: bool
    touched_code: bool = False
    verified_execution: bool = False
    phase: str = AgentPhase.PLAN

    @staticmethod
    def from_goal(goal: str) -> "AgentRunState":
        goal_lc = str(goal or "").lower()
        task_terminal_only = (
            "terminal" in goal_lc
            or "ascii" in goal_lc
            or "no gui" in goal_lc
            or "sans gui" in goal_lc
        )
        goal_requires_verify = bool(
            re.search(r"\b(run|verify|works|test|ex(é|e)cute|lance|fonctionne)\b", goal_lc)
        )
        return AgentRunState(
            task_terminal_only=task_terminal_only,
            goal_requires_verify=goal_requires_verify,
        )

    def on_tool_start(self) -> None:
        self.phase = AgentPhase.EXECUTE_TOOL

    def on_tool_result(self, *, touched_code: bool, verified_execution: bool) -> None:
        if touched_code:
            self.touched_code = True
        if verified_execution:
            self.verified_execution = True
        self.phase = AgentPhase.VERIFY

    def can_complete(self) -> tuple[bool, str | None]:
        if self.goal_requires_verify and self.touched_code and not self.verified_execution:
            self.phase = AgentPhase.RECOVER
            return (
                False,
                (
                    "VERIFY_REQUIRED: task requests runnable verification, but no successful execution evidence was observed. "
                    "Run a concrete in-project execution and capture success before Status: DONE."
                ),
            )
        self.phase = AgentPhase.DONE
        return (True, None)
