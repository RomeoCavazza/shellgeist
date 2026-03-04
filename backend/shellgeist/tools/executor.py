"""Tool execution engine: dispatch, observation capture, outcome building."""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from shellgeist.safety.retry import classify_result_payload
from shellgeist.tools.runtime import (
    SHELL_SESSION_TOOLS,
    auto_heal_session_id,
    build_policy_args,
    build_tool_meta,
    detect_execution_evidence,
)

# Patterns that indicate a shell command is trying to create/write a file
_FILE_CREATION_RE = re.compile(
    r"""
    (?:^|\s|;|&&|\|\|)              # start or chained command
    (?:
        echo\s+.*?>\s*\S            # echo ... > file
        | printf\s+.*?>\s*\S        # printf ... > file
        | cat\s*<<                  # cat << heredoc
        | tee\s+\S                  # tee file
        | >\s*\S                    # > file (truncate)
        | python[23]?\s+-c\s+.*open\(  # python -c "open(..."
        | dd\s+.*of=                # dd of=file
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _shell_creates_file(cmd: str) -> bool:
    """Return True if a shell command appears to create/write a file."""
    return bool(_FILE_CREATION_RE.search(cmd))


@dataclass
class ToolExecutionOutcome:
    kind: str  # observation | failure
    func_name: str
    observation: str = ""
    last_shell_session_id: str | None = None
    touched_code: bool = False
    verified_execution: bool = False
    error_code: str | None = None
    final_response: str | None = None
    done_meta: dict[str, Any] | None = None


async def execute_tool_call(
    *,
    func_name: str,
    args: dict[str, Any],
    root: str,
    last_shell_session_id: str | None,
    task_terminal_only: bool,
    policy: Any,
    loop_guard: Any,
    retry_engine: Any,
    telemetry: Any,
    registry: Any,
    emit_execution_event: Callable[..., Awaitable[None]],
    log: Callable[..., Awaitable[None]],
    code_preview_for_tool: Callable[[str, dict[str, Any]], str | None],
    review_mode: bool = False,
    request_review: Callable[..., Awaitable[str | None]] | None = None,
) -> ToolExecutionOutcome:
    tool_meta = build_tool_meta(func_name, args)
    await emit_execution_event("status", "", phase="tool_use", meta={"tool": func_name})

    policy_args = build_policy_args(args, task_terminal_only=task_terminal_only)
    decision = policy.evaluate(func_name, policy_args)
    if not decision.allowed:
        policy_msg = decision.reason or "POLICY_DENY: tool call is not allowed by active policy."
        await log(policy_msg, type="info", meta=tool_meta)
        return ToolExecutionOutcome(kind="observation", func_name=func_name, observation=policy_msg, last_shell_session_id=last_shell_session_id)

    verdict, verdict_msg = loop_guard.check_call(func_name, args)
    if verdict == "circuit":
        final_response = (
            "Agent loop stopped by circuit breaker due to excessive repetitive tool activity. "
            "Please retry with a narrower task."
        )
        return ToolExecutionOutcome(
            kind="failure",
            func_name=func_name,
            error_code="loop_circuit_breaker",
            final_response=final_response,
            done_meta={"thinking": False, "circuit": True},
            last_shell_session_id=last_shell_session_id,
        )

    if verdict == "block":
        res = verdict_msg or (
            "BLOCKED_REPEAT_TOOL: This exact tool call failed repeatedly. "
            "Do not retry it; choose another approach."
        )
        await log(f"Calling: {func_name}", type="action", meta=tool_meta)
        res_str = str(res)
        await log(res_str[:4000], type="observation")
        return ToolExecutionOutcome(kind="observation", func_name=func_name, observation=res_str, last_shell_session_id=last_shell_session_id)

    code_preview = code_preview_for_tool(func_name, args)
    if code_preview:
        await emit_execution_event("code", code_preview, phase="tool_use", meta=tool_meta)

    async def _run_tool_once(_attempt: int):
        call_args = dict(args)

        if func_name in SHELL_SESSION_TOOLS:
            call_args, healed = auto_heal_session_id(call_args, last_shell_session_id)
            if healed:
                await log(f"Auto-heal session_id -> {last_shell_session_id}", type="info")

        # Inject review_mode for edit_file in review mode
        if review_mode and func_name == "edit_file":
            call_args["review_mode"] = True

        # Block run_shell from creating/writing files — force use of write_file
        if func_name in ("run_shell", "exec_shell_session"):
            cmd = str(call_args.get("command") or "")
            if _shell_creates_file(cmd):
                await log("Blocked: shell file creation detected, use write_file instead", type="error")
                return (
                    "INVALID_ACTION: Do NOT create files via shell commands. "
                    "Use the `write_file` tool with `path` and `content` parameters instead. "
                    'Example: <tool_use>{"name": "write_file", "arguments": {"path": "file.md", "content": "# Title\\nContent"}}</tool_use>'
                )

        if func_name == "write_file":
            code = str(call_args.get("content") or "")
            placeholders = ["...", "// code here", "# content here", "<code", "précédemment donné"]
            if any(p.lower() in code.lower() for p in placeholders):
                await log("Failure: Laziness detected", type="error")
                return "INVALID_ACTION: Placeholders detected. Re-write the file FULLY."

        tool = registry.tools.get(func_name)
        return tool.execute(**call_args, root=root) if tool else f"Error: {func_name} not found."

    async def _on_tool_retry(attempt: int, error_class: str, reason: str, delay_ms: int, _last: Any | None):
        await telemetry.emit_retry_status(
            "tool",
            attempt=attempt,
            error_class=error_class,
            reason=reason,
            delay_ms=delay_ms,
            tool=func_name,
        )
        await log(
            f"Retry tool {func_name} (attempt {attempt + 1}) in {delay_ms}ms [{error_class}] {reason}",
            type="info",
            meta=tool_meta,
        )

    await log(f"Calling: {func_name}", type="action", meta=tool_meta)
    res, tool_report = await retry_engine.run_async(
        key=f"tool:{func_name}",
        operation=_run_tool_once,
        classify_result=lambda result: classify_result_payload(result, tool_name=func_name),
        on_retry=_on_tool_retry,
    )

    res = res if tool_report.outcome == "success" else f"Error: {tool_report.reason or tool_report.error_class or 'tool_failed'}"
    if tool_report.outcome != "success":
        await log(
            f"Tool failure after retries: {func_name} [{tool_report.error_class or 'unknown'}] {tool_report.reason}",
            type="info",
            meta=tool_meta,
        )

    updated_session_id = last_shell_session_id
    if tool_report.outcome == "success" and func_name == "start_shell_session":
        try:
            start_obj = json.loads(str(res or ""))
            if isinstance(start_obj, dict) and start_obj.get("ok") is True:
                sid = str(start_obj.get("session_id") or "").strip()
                if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", sid):
                    updated_session_id = sid
        except Exception:
            pass

    res_str = str(res or "Success")
    touched_code, verified_execution = detect_execution_evidence(func_name, res_str)

    if func_name == "exec_shell_session":
        try:
            obj = json.loads(res_str)
            if isinstance(obj, dict):
                if obj.get("error") == "command_failed" and obj.get("alive") is True:
                    res_str += (
                        "\nSTATE_HINT: command failed but shell session is still alive. "
                        "Do NOT start a new session; fix and retry in this same session."
                    )
                elif obj.get("error") in {"session_terminated", "session_not_found"}:
                    res_str += (
                        "\nSTATE_HINT: shell session is not usable. "
                        "List sessions, then start one fresh session and use its new session_id."
                    )
        except Exception:
            pass

    blocked_outcome, blocked_msg = loop_guard.record_outcome(func_name, args, res_str)
    if blocked_outcome and blocked_msg:
        await log(blocked_msg, type="info", meta=tool_meta)

    # ── Review mode: hunk-level review for edit_file ──
    if review_mode and func_name == "edit_file" and tool_report.outcome == "success" and request_review:
        try:
            result_data = res if isinstance(res, dict) else {}
            if result_data.get("ok") and not result_data.get("written"):
                old_c = result_data.get("old_content", "")
                new_c = result_data.get("new_content", "")
                fpath = result_data.get("file", "")
                if fpath and (old_c or new_c):
                    await log(f"Review pending: {fpath}", type="info", meta=tool_meta)
                    resolved = await request_review(fpath, old_c, new_c)
                    if resolved is not None:
                        from shellgeist.tools.coder import write_reviewed_content
                        write_reviewed_content(fpath, resolved, root=root)
                        res_str = f"Successfully edited {fpath} (review approved, hunks resolved)"
                        touched_code = True
                    else:
                        res_str = f"Edit rejected by user for {fpath} in review mode"
                        await log(res_str, type="info", meta=tool_meta)
        except Exception as exc:
            _trace_msg = f"review_flow_error: {exc}"
            await log(_trace_msg, type="error", meta=tool_meta)

    await log(res_str[:4000], type="observation")

    # ── Emit file_changed event for write_file / edit_file ──
    # This allows the frontend to open inline diff in the source buffer.
    if func_name in ("write_file", "edit_file") and tool_report.outcome == "success":
        file_path = str(args.get("path") or args.get("file_path") or args.get("file") or "")
        if file_path and "Successfully" in res_str:
            await emit_execution_event(
                "file_changed",
                file_path,
                phase="tool_use",
                meta={
                    "file": file_path,
                    "root": root,
                    "tool": func_name,
                },
            )

    return ToolExecutionOutcome(
        kind="observation",
        func_name=func_name,
        observation=res_str,
        last_shell_session_id=updated_session_id,
        touched_code=touched_code,
        verified_execution=verified_execution,
    )
