"""Core agent loop: LLM interaction, tool dispatch, and result handling."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from shellgeist.agent.messages import Message
from shellgeist.runtime.protocol import SGResult
from shellgeist.agent.signals import UIEvent
from shellgeist.agent.orchestrator import (
    ToolCallQueue,
    build_schema_error_message,
    decide_no_tool_action,
    extract_plaintext_tool_calls,
    is_small_talk,
    normalize_final_response,
)
from shellgeist.config import debug_enabled as _debug_enabled
from shellgeist.llm import build_system_prompt, get_client, run_llm_stream_with_retry
from shellgeist.runtime.session import (
    append_user_goal_once,
    init_db,
    load_recent_history,
    save_message as save_db_message,
    repair_conversation_history,
)
from shellgeist.runtime.telemetry import TelemetryEmitter
from shellgeist.runtime.transport import UIEventEmitter
from shellgeist.tools import load_tools, registry
from shellgeist.tools.executor import execute_tool_call
from shellgeist.agent.parsing.parser import parse_xml_tool_use
from shellgeist.runtime.policy import LoopGuard, RetryEngine, RetryConfig, is_failed_result


def _debug_log(msg: str) -> None:
    if not _debug_enabled():
        return
    sys.stderr.write(f"DEBUG: {msg}\n")
    sys.stderr.flush()


# Max chars for tool observations and assistant replies kept in history (avoids model "continuing" huge past content)
_MAX_HISTORY_OBS_CHARS = 2800
_MAX_HISTORY_ASSISTANT_CHARS = 4000


class Agent:
    def __init__(self, root: str) -> None:
        load_tools()
        self.root = root
        self.client, self.model = get_client("smart")
        self.history: list[dict[str, Any]] = []
        self._setup_system_prompt()

    def _setup_system_prompt(self) -> None:
        local_rules = self._load_local_rules()
        sys_prompt = build_system_prompt(
            self.root,
            debug_log=_debug_log,
            tool_schemas_provider=registry.get_tool_schemas,
            local_rules=local_rules,
        )
        self.history = [{"role": "system", "content": sys_prompt}]

    def _load_local_rules(self) -> str | None:
        candidates = [
            Path(self.root) / ".shellgeist.md",
            Path(self.root) / ".shellgeist" / "rules.md",
        ]
        for c in candidates:
            if c.exists() and c.is_file():
                return c.read_text(encoding="utf-8")
        return None

    async def run_task(self, goal: str, writer: Any | None = None, session_id: str = "default", mode: str = "auto", reader: Any | None = None, fresh_conversation: bool = False) -> dict[str, Any]:
        greeting = is_small_talk(goal)
        if greeting:
             ui = UIEventEmitter(writer, reader=reader)
             await ui.emit_execution_event("response", greeting, phase="done", meta={"final": True})
             return {"ok": True, "status": "completed", "logs": [greeting], "response": greeting}

        init_db()
        ui = UIEventEmitter(writer, reader=reader)
        review_mode = (mode == "review")

        # Strip internal PROTOCOL_VIOLATION feedbacks from history before the new task
        # so failed attempts from the previous turn don't poison the LLM context.
        self.history = [
            m for m in self.history
            if not (m.get("role") == "user" and m.get("content", "").startswith("PROTOCOL_VIOLATION:"))
        ]

        if not fresh_conversation:
            self.history = load_recent_history(self.history, session_id=session_id)
        append_user_goal_once(self.history, session_id=session_id, goal=goal)

        max_steps = 12
        logs: list[str] = []
        any_tool_succeeded = False
        last_shell_session_id = None
        loop_guard = LoopGuard()
        retry_engine = RetryEngine(RetryConfig.from_env())

        telemetry = TelemetryEmitter(
            emit_execution_event=ui.emit_execution_event,
            total_retries_provider=lambda: retry_engine.total_retries_used
        )

        async def _log_retry(msg: str) -> None:
            await ui.emit_execution_event("status", msg, phase="streaming")

        for i in range(max_steps):
            repaired, report = repair_conversation_history(self.history)
            self.history = repaired
            
            await ui.status(True)

            # Stream response tokens to UI in real-time
            async def _on_response_chunk(delta: str) -> None:
                await ui.emit_execution_event(
                    "response", delta, phase="streaming",
                    meta={"chunk": True},
                )

            content, stream_report = await run_llm_stream_with_retry(
                client=self.client,
                model=self.model,
                messages=self.history,
                retry_engine=retry_engine,
                telemetry=telemetry,
                log_retry=_log_retry,
                on_chunk=_on_response_chunk,
            )
            await ui.status(False)

            if stream_report.outcome != "success":
                return {"ok": False, "error": "provider_error", "logs": logs}

            content_str = str(content or "")
            if content_str:
                # Truncate huge assistant replies in history to avoid model "continuing" them next turn
                to_append = content_str
                if len(to_append) > _MAX_HISTORY_ASSISTANT_CHARS:
                    to_append = (
                        to_append[:_MAX_HISTORY_ASSISTANT_CHARS].rstrip()
                        + f"\n\n... [truncated, {len(content_str)} chars]"
                    )
                self.history.append({"role": "assistant", "content": to_append})
                logs.append(content_str)

            tool_calls = parse_xml_tool_use(content_str)
            if not tool_calls:
                tool_calls = extract_plaintext_tool_calls(content_str)

            # Enforce max 3 tool calls per turn to avoid LLM dumping 10+ calls and repeated failures
            max_tools_per_turn = 3
            num_calls = len(tool_calls)
            excess = max(0, num_calls - max_tools_per_turn)
            if excess > 0:
                tool_calls = tool_calls[:max_tools_per_turn]

            if not tool_calls:
                decision = decide_no_tool_action(
                    content_str,
                    completion_blocker=None,
                    extract_final_response=normalize_final_response,
                    any_tool_succeeded=any_tool_succeeded,
                )
                if decision.action == "complete":
                    await ui.emit_execution_event("response", decision.final_response or "", phase="done", meta={"final": True})
                    return {"ok": True, "status": "completed", "logs": logs, "response": decision.final_response}
                
                # PRUNING: Remove the blathering from history so the LLM doesn't get confused by its own errors.
                if content_str and self.history and self.history[-1].get("role") == "assistant":
                    self.history.pop()
                
                feedback = f"PROTOCOL_VIOLATION: {decision.feedback}"
                self.history.append({"role": "user", "content": feedback})
                _debug_log(f"No tool call detected. Pruned blather. Feedback: {feedback}")
                continue

            for tc in tool_calls:
                func_name = tc.get("name")
                args = tc.get("arguments", {})

                # Emit rich status for each tool call
                _STATUS_ICONS = {
                    "read_file": "🔍", "list_files": "📂", "find_files": "🔎",
                    "write_file": "✏️", "edit_file": "✏️",
                    "run_shell": "🐚", "start_shell_session": "🐚",
                    "exec_shell_session": "🐚", "get_repo_map": "🗺️",
                }
                icon = _STATUS_ICONS.get(func_name, "⚙️")
                status_label = f"{icon} {func_name}"
                if func_name in ("read_file", "write_file", "edit_file") and args.get("path"):
                    status_label += f" {args['path']}"
                elif func_name == "run_shell" and args.get("command"):
                    cmd_short = args["command"][:40]
                    status_label += f" {cmd_short}"
                elif func_name == "list_files" and args.get("directory"):
                    status_label += f" {args['directory']}"
                await ui.emit_execution_event("status", status_label, phase="tool_use", meta={"thinking": True})

                # Emit the tool call to the sidebar so the user can see what's happening
                await ui.emit_execution_event("tool_call", func_name or "", phase="tool_use", meta={"tool": func_name, "args": args})

                # Manual approval logic...
                if review_mode:
                    approved = await ui.request_approval(func_name, args)
                    if not approved:
                        continue

                outcome = await execute_tool_call(
                    func_name=func_name,
                    args=args,
                    root=self.root,
                    policy=None,
                    loop_guard=loop_guard,
                    retry_engine=retry_engine,
                )

                if outcome.success:
                    any_tool_succeeded = True

                # Emit the tool result to the sidebar immediately — don't wait for LLM to re-present it
                await ui.emit_execution_event(
                    "tool_result", outcome.observation, phase="tool_use",
                    meta={"tool": func_name, "success": outcome.success},
                )

                obs = outcome.observation
                if "BLOCKED_REPEAT" in obs:
                    obs = obs.rstrip() + "\nDo not call any tool again. Reply with Status: FAILED and a one-sentence explanation for the user."
                # Truncate huge observations in history so the model doesn't "continue" them next turn
                if len(obs) > _MAX_HISTORY_OBS_CHARS:
                    obs = (
                        obs[:_MAX_HISTORY_OBS_CHARS].rstrip()
                        + f"\n\n... [truncated, {len(outcome.observation)} chars total]. Use the result above; do not repeat or paste it in your reply."
                    )
                obs = f"<tool_observation name=\"{func_name}\">\n{obs}\n</tool_observation>"
                self.history.append({"role": "user", "content": obs})
                save_db_message(session_id, "user", obs, log_type="context")

            # If we had more than max_tools_per_turn, tell the model only first 3 were run
            if excess > 0:
                feedback = f"Only the first {max_tools_per_turn} tool calls were executed. You sent {excess + max_tools_per_turn}. Next message: use at most {max_tools_per_turn} <tool_use> tags."
                self.history.append({"role": "user", "content": feedback})
                save_db_message(session_id, "user", feedback, log_type="context")

            # If the model sent Status: DONE/FAILED in the same message as <tool_use>, remind it not to
            if re.search(r"Status:\s*(?:DONE|FAILED)", content_str, re.IGNORECASE):
                reminder = (
                    "Reminder: do not write Status: DONE or Status: FAILED in the same message as <tool_use>. "
                    "Send only <tool_use> tags; after you see the tool results, reply with your answer and one status line."
                )
                self.history.append({"role": "user", "content": reminder})
                save_db_message(session_id, "user", reminder, log_type="context")

            # If every tool call this step was blocked (BLOCKED_REPEAT), stop the loop to avoid infinite retries
            step_observations = [
                m.get("content", "") for m in self.history
                if m.get("role") == "user" and "<tool_observation" in m.get("content", "")
            ]
            recent_obs = step_observations[-len(tool_calls):] if len(tool_calls) else []
            all_blocked = (
                len(recent_obs) >= 1
                and all("BLOCKED_REPEAT" in o for o in recent_obs)
            )
            if all_blocked:
                await ui.emit_execution_event(
                    "error",
                    "Repeated tool calls were blocked. Use relative paths (e.g. README.md, .) and do not retry the same failing call.",
                    phase="done",
                    meta={"final": True},
                )
                return {"ok": False, "status": "failed", "logs": logs, "error": "repeated_tool_blocked"}

        # Loop exhausted without completion — prefer the last tool observation over the
        # last LLM output (which is likely a <tool_use> tag, not useful to the user).
        last_obs = None
        for m in reversed(self.history):
            content = m.get("content", "")
            if m.get("role") == "user" and "<tool_observation" in content:
                import re as _re
                last_obs = _re.sub(r"</?tool_observation[^>]*>", "", content).strip()
                break
        last_response = last_obs or logs[-1] if (last_obs or logs) else "ShellGeist: max steps reached without completing the task."

        # If no tool ever succeeded and the last observation looks like a failure,
        # surface this as an explicit error instead of a generic 'stopped' status.
        if last_obs and not any_tool_succeeded and is_failed_result(last_obs):
            await ui.emit_execution_event("error", last_response, phase="done", meta={"final": True})
            return {"ok": False, "status": "failed", "logs": logs, "error": last_response}

        await ui.emit_execution_event("response", last_response, phase="done", meta={"final": True})
        return {"ok": True, "status": "stopped", "logs": logs}
