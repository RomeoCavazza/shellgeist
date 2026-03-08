"""Core agent loop: LLM interaction, tool dispatch, and result handling."""
from __future__ import annotations

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
from shellgeist.runtime.policy import LoopGuard, RetryEngine, RetryConfig


def _debug_log(msg: str) -> None:
    if not _debug_enabled():
        return
    sys.stderr.write(f"DEBUG: {msg}\n")
    sys.stderr.flush()


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

    async def run_task(self, goal: str, writer: Any | None = None, session_id: str = "default", mode: str = "auto", reader: Any | None = None) -> dict[str, Any]:
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

        self.history = load_recent_history(self.history, session_id=session_id)
        append_user_goal_once(self.history, session_id=session_id, goal=goal)

        max_steps = 8
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
            content, stream_report = await run_llm_stream_with_retry(
                client=self.client,
                model=self.model,
                messages=self.history,
                retry_engine=retry_engine,
                telemetry=telemetry,
                log_retry=_log_retry,
            )
            await ui.status(False)

            if stream_report.outcome != "success":
                return {"ok": False, "error": "provider_error", "logs": logs}

            content_str = str(content or "")
            if content_str:
                self.history.append({"role": "assistant", "content": content_str})
                logs.append(content_str)

            tool_calls = parse_xml_tool_use(content_str)
            if not tool_calls:
                tool_calls = extract_plaintext_tool_calls(content_str)

            if not tool_calls:
                decision = decide_no_tool_action(
                    content_str,
                    completion_blocker=None,
                    extract_final_response=lambda x: x,
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
                await ui.emit_execution_event("tool_result", outcome.observation, phase="tool_use", meta={"tool": func_name})

                obs = f"<tool_observation name=\"{func_name}\">\n{outcome.observation}\n</tool_observation>"
                self.history.append({"role": "user", "content": obs})
                save_db_message(session_id, "user", obs, log_type="context")

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
        await ui.emit_execution_event("response", last_response, phase="done", meta={"final": True})
        return {"ok": True, "status": "stopped", "logs": logs}
