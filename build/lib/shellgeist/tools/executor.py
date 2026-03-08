"""Tool execution engine: dispatch, observation capture, and outcome handling."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from shellgeist.runtime.policy import is_failed_result

@dataclass
class ToolExecutionOutcome:
    kind: str  # observation | failure
    func_name: str
    observation: str = ""
    last_shell_session_id: str | None = None
    touched_code: bool = False
    verified_execution: bool = False

    @property
    def success(self) -> bool:
        """Heuristic for whether the tool actually did its job."""
        from shellgeist.runtime.policy import is_failed_result
        return not is_failed_result(self.observation)

async def execute_tool_call(
    *,
    func_name: str,
    args: dict[str, Any],
    root: str,
    policy: Any,
    loop_guard: Any,
    retry_engine: Any,
) -> ToolExecutionOutcome:
    """Execute a tool call with loop guard and retries."""
    verdict, msg = loop_guard.check_call(func_name, args)
    if verdict != "allow":
        return ToolExecutionOutcome(kind="observation", func_name=func_name, observation=msg)

    # 2. Execution with Retry
    from shellgeist.tools import registry
    tool = registry.tools.get(func_name)
    if not tool:
        return ToolExecutionOutcome(kind="observation", func_name=func_name, observation=f"Error: {func_name} not found")

    async def _run():
        return tool.execute(**args, root=root)

    res = await retry_engine.run_async(
        key=f"tool:{func_name}",
        operation=lambda _: _run(),
        classify_result=lambda r: ("transient" if "timeout" in str(r).lower() else None, "")
    )
    
    res_str = str(res) if res is not None else "Success"
    loop_guard.record_outcome(func_name, args, res_str)
    
    return ToolExecutionOutcome(
        kind="observation",
        func_name=func_name,
        observation=res_str
    )
