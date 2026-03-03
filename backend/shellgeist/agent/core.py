"""Core agent loop: LLM interaction, tool dispatch, and result handling."""
from __future__ import annotations

import os
import sys
from typing import Any

from shellgeist.agent.messages import (
    NO_ACTIONABLE_DECISION,
    SCHEMA_ERROR_FINAL_RESPONSE,
    SMALL_TALK_REPLY,
    TOOL_EXECUTION_FAILED_DEFAULT,
    session_repaired_message,
    stream_failed_after_retries,
)
from shellgeist.agent.orchestrator import (
    ToolCallQueue,
    build_schema_error_message,
    decide_no_tool_action,
)
from shellgeist.agent.state import AgentRunState
from shellgeist.io import UIEventEmitter, TelemetryEmitter, completed_result, failed_result, stopped_result
from shellgeist.llm import get_client, build_system_prompt, run_llm_stream_with_retry
from shellgeist.protocol.helpers import (
    PROTOCOL_MARKDOWN_WITHOUT_TOOL,
    extract_actionable_thought,
    extract_canonical_response,
    has_markdown_without_tool_calls,
    is_small_talk,
)
from shellgeist.safety import LoopGuard, LoopGuardConfig, RetryEngine, RetryConfig, VerifyRuntime
from shellgeist.session import repair_conversation_history
from shellgeist.session.ops import (
    append_context_observation,
    append_user_goal_once,
    initialize_history_db,
    load_recent_history,
    save_assistant_message,
)
from shellgeist.tools import registry
from shellgeist.tools.executor import execute_tool_call
from shellgeist.tools.parser import parse_xml_tool_use as _parse_xml_tool_use
from shellgeist.tools.policy import ToolPolicy
from shellgeist.tools.preview import code_preview_for_tool
from shellgeist.tools.runtime import missing_required_args, normalize_tool_args


def _debug_enabled() -> bool:
    v = str(os.getenv("SHELLGEIST_DEBUG", "")).strip().lower()
    return v in {"1", "true", "yes", "on", "debug"}

def _debug_log(msg: str):
    if not _debug_enabled():
        return
    sys.stderr.write(f"DEBUG: {msg}\n")
    sys.stderr.flush()

class Agent:
    def __init__(self, root: str) -> None:
        _debug_log(f"Agent.__init__ start: {root}")
        self.root = root
        _debug_log("Getting client...")
        try:
           self.client, self.model = get_client("smart")
           _debug_log(f"Client OK: {self.model}")
        except Exception as e:
           _debug_log(f"Client FAIL: {e}")
           raise

        self.history: list[dict[str, Any]] = []
        _debug_log("Setting up system prompt...")
        self._setup_system_prompt()
        _debug_log("Agent.__init__ done")

    def _setup_system_prompt(self) -> None:
        sys_prompt = build_system_prompt(
            self.root,
            debug_log=_debug_log,
            tool_schemas_provider=registry.get_tool_schemas,
        )
        self.history = [{"role": "system", "content": sys_prompt}]

    async def run_task(self, goal: str, writer: Any | None = None, session_id: str = "default") -> dict[str, Any]:
        _debug_log(f"run_task start: {goal}")
        initialize_history_db()

        ui = UIEventEmitter(writer)
        _emit_execution_event = ui.emit_execution_event
        _log = ui.log
        _status = ui.status

        # 1. Load history from DB
        self.history = load_recent_history(self.history, session_id=session_id, max_recent=40)

        # 2. Append new goal
        append_user_goal_once(self.history, session_id=session_id, goal=goal)

        # Fast-path for small talk: keep UX concise and avoid autonomous loop noise.
        if is_small_talk(goal):
            reply = SMALL_TALK_REPLY
            self.history.append({"role": "assistant", "content": reply})
            save_assistant_message(session_id=session_id, content=reply)
            await _log(reply, type="assistant")
            await _emit_execution_event("status", "", phase="done", meta={"thinking": False})
            return completed_result(logs=[reply])

        max_steps = 15
        logs = []
        last_shell_session_id: str | None = None
        last_thought_emitted: str = ""
        schema_error_count: int = 0
        run_state = AgentRunState.from_goal(goal)
        loop_guard = LoopGuard(LoopGuardConfig(global_call_limit=max_steps * 6))
        policy = ToolPolicy(root=self.root, session_id=session_id)
        retry_engine = RetryEngine(RetryConfig.from_env())
        verifier = VerifyRuntime(goal_requires_verify=run_state.goal_requires_verify)

        def _retry_stats() -> dict[str, Any]:
            return retry_engine.stats_snapshot()

        telemetry = TelemetryEmitter(
            emit_execution_event=_emit_execution_event,
            total_retries_provider=lambda: retry_engine.total_retries_used,
        )

        async def _repair_history_if_needed() -> None:
            repaired, report = repair_conversation_history(self.history, max_non_system=80)
            if report.changed():
                self.history = repaired
                await _log(
                    session_repaired_message(
                        dropped_count=report.dropped_count,
                        deduped_count=report.deduped_count,
                        normalized_count=report.normalized_count,
                    ),
                    type="info",
                )

        _debug_log(f"Entering loop for goal: {goal}")
        for i in range(max_steps):
            _debug_log(f"Step {i} start")
            await _repair_history_if_needed()
            await _status(True)
            content, stream_report = await run_llm_stream_with_retry(
                client=self.client,
                model=self.model,
                messages=self.history,
                retry_engine=retry_engine,
                telemetry=telemetry,
                log_retry=lambda message: _log(message, type="info"),
                debug_log=_debug_log,
            )

            if stream_report.outcome != "success":
                msg = stream_failed_after_retries(stream_report.reason, stream_report.error_class)
                await _log(msg, type="error")
                await _status(False)
                return failed_result(error="provider_error", detail=msg, retry=_retry_stats())
            await _status(False)

            content = str(content or "")
            _debug_log(f"Content length: {len(content)}")
            if content:
                self.history.append({"role": "assistant", "content": content})
                logs.append(content)
                
                if content.startswith("ERROR:"):
                     _debug_log(f"Fatal error from provider: {content}")
                     await _log(content, type="error")
                     return failed_result(error="provider_error", status="", detail=content)

            tool_calls = _parse_xml_tool_use(content, debug_log=_debug_log)
            _debug_log(f"Found {len(tool_calls)} tool calls")

            thought_text = extract_actionable_thought(content, has_tool_calls=bool(tool_calls))
            if thought_text and thought_text != last_thought_emitted:
                last_thought_emitted = thought_text
                await _log(thought_text, type="thought")
            
            if has_markdown_without_tool_calls(content, has_tool_calls=bool(tool_calls)):
                v_msg = PROTOCOL_MARKDOWN_WITHOUT_TOOL
                _debug_log("Protocol Violation: Markdown without Tool")
                await _log(v_msg, type="info")
                self.history.append({"role": "user", "content": v_msg})
                continue

            if not tool_calls:
                blocker = verifier.completion_blocker()
                decision = decide_no_tool_action(
                    content,
                    completion_blocker=blocker,
                    extract_final_response=extract_canonical_response,
                )
                if decision.action == "complete" and decision.final_response:
                    run_state.phase = "done"
                    final_response = decision.final_response
                    save_assistant_message(session_id=session_id, content=final_response)
                    await _emit_execution_event("response", final_response, phase="done", meta={"final": True})
                    await _emit_execution_event("status", "", phase="done", meta={"thinking": False})
                    return completed_result(logs=logs, response=final_response, retry=_retry_stats())

                if blocker:
                    run_state.phase = "recover"
                _debug_log("Failure: Missing or malformed tool use")
                v_msg = decision.feedback or NO_ACTIONABLE_DECISION
                await _log(v_msg, type="info")
                self.history.append({"role": "user", "content": v_msg})
                continue

            observations = []
            queue = ToolCallQueue(tool_calls)
            while queue.has_next():
                tc = queue.next()
                if not tc:
                    break
                func_name = tc.get("name")
                args = tc.get("arguments", {})
                
                if not func_name: continue
                args = normalize_tool_args(
                    func_name,
                    tc,
                    args,
                    last_shell_session_id=last_shell_session_id,
                )
                missing = missing_required_args(func_name, args)
                if missing:
                    schema_error_count += 1
                    msg = build_schema_error_message(func_name, missing)
                    await _log(msg, type="info", meta={"tool": func_name})
                    observations.append(f"<tool_observation name=\"{func_name}\">\n{msg}\n</tool_observation>")
                    if schema_error_count >= 3:
                        final_response = SCHEMA_ERROR_FINAL_RESPONSE
                        save_assistant_message(session_id=session_id, content=final_response)
                        await _emit_execution_event("response", final_response, phase="done", meta={"final": True})
                        await _emit_execution_event("status", "", phase="done", meta={"thinking": False, "schema_error": True})
                        return failed_result(error="tool_schema_error", logs=logs, response=final_response)
                    continue

                run_state.on_tool_start()
                outcome = await execute_tool_call(
                    func_name=func_name,
                    args=args if isinstance(args, dict) else {},
                    root=self.root,
                    last_shell_session_id=last_shell_session_id,
                    task_terminal_only=run_state.task_terminal_only,
                    policy=policy,
                    loop_guard=loop_guard,
                    retry_engine=retry_engine,
                    telemetry=telemetry,
                    registry=registry,
                    emit_execution_event=_emit_execution_event,
                    log=_log,
                    code_preview_for_tool=code_preview_for_tool,
                )

                if outcome.kind == "failure":
                    final_response = outcome.final_response or TOOL_EXECUTION_FAILED_DEFAULT
                    save_assistant_message(session_id=session_id, content=final_response)
                    await _emit_execution_event("response", final_response, phase="done", meta={"final": True})
                    await _emit_execution_event("status", "", phase="done", meta=outcome.done_meta or {"thinking": False})
                    return failed_result(
                        error=outcome.error_code or "tool_execution_failed",
                        logs=logs,
                        response=final_response,
                    )

                last_shell_session_id = outcome.last_shell_session_id
                verifier.record(touched_code=outcome.touched_code, verified_execution=outcome.verified_execution)
                run_state.on_tool_result(touched_code=outcome.touched_code, verified_execution=outcome.verified_execution)
                observations.append(
                    f"<tool_observation name=\"{outcome.func_name}\">\n{outcome.observation}\n</tool_observation>"
                )

            if observations:
                obs_content = "\n\n".join(observations)
                append_context_observation(self.history, session_id=session_id, content=obs_content)

        await _emit_execution_event("status", "", phase="done", meta={"thinking": False, "stopped": True})
        return stopped_result(logs=logs, retry=_retry_stats())
