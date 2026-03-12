"""Tool execution engine: dispatch, observation capture, and outcome handling."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from shellgeist.runtime.policy import is_failed_result


def _tool_failed(func_name: str, observation: str) -> bool:
    content = (observation or "").strip()
    if not content:
        return False
    if func_name == "read_file":
        if content.startswith(("Error:", "Blocked:", "BLOCKED_", "POLICY_DENY", "CIRCUIT_BREAKER")):
            return True
        try:
            obj = json.loads(content)
            return isinstance(obj, dict) and obj.get("ok") is False
        except Exception:
            return False
    return is_failed_result(content)

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
        return not _tool_failed(self.func_name, self.observation)

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

    try:
        res = await retry_engine.run_async(
            key=f"tool:{func_name}",
            operation=lambda _: _run(),
            classify_result=lambda r: ("transient" if "timeout" in str(r).lower() else None, "")
        )
    except Exception as exc:
        return ToolExecutionOutcome(kind="observation", func_name=func_name, observation=f"Error: {exc}")
    
    res_str = _observation_string(res, func_name)
    loop_guard.record_outcome(func_name, args, f"Error: read_file_failed" if _tool_failed(func_name, res_str) else res_str)
    
    return ToolExecutionOutcome(
        kind="observation",
        func_name=func_name,
        observation=res_str
    )


def _observation_string(res: Any, func_name: str) -> str:
    """Turn tool result into a string observation for history and UI (sidebar diff display)."""
    if res is None:
        return "Success"
    if isinstance(res, dict):
        if func_name == "edit_file":
            if res.get("ok") and res.get("diff"):
                file = res.get("file", "?")
                return f"Successfully applied to {file}\n\nDiff:\n{res['diff']}"
            if res.get("ok"):
                return f"Successfully applied to {res.get('file', '?')}."
            err = res.get("error", "unknown")
            detail = res.get("detail", "")
            if err == "guard_blocked" and detail == "syntax_error_after_edit":
                return (
                    "Edit rejected: the change would introduce a syntax error. "
                    "Use write_file with the full file content instead of edit_file."
                )
            return f"Error: {err}" + (f" — {detail}" if detail else "")
    return str(res)
