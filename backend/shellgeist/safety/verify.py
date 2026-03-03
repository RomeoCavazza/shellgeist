"""Verify runtime: tracks whether agent actually tested its code changes."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VerifyRuntime:
    goal_requires_verify: bool
    touched_code: bool = False
    verified_execution: bool = False

    def record(self, *, touched_code: bool, verified_execution: bool) -> None:
        if touched_code:
            self.touched_code = True
        if verified_execution:
            self.verified_execution = True

    def completion_blocker(self) -> str | None:
        if self.goal_requires_verify and self.touched_code and not self.verified_execution:
            return (
                "VERIFY_REQUIRED: task requests runnable verification, but no successful execution evidence was observed. "
                "Run a concrete in-project execution and capture success before Status: DONE."
            )
        return None
