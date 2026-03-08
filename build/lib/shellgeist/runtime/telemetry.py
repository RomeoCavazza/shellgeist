"""Telemetry emitter: token usage and retry metrics."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


def build_retry_meta(
    *,
    scope: str,
    attempt: int,
    error_class: str,
    reason: str,
    delay_ms: int,
    total_used: int,
    tool: str | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "retry": {
            "scope": scope,
            "attempt": attempt,
            "class": error_class,
            "reason": reason,
            "delay_ms": delay_ms,
            "total_used": total_used,
        }
    }
    if tool:
        meta["tool"] = tool
    return meta


class TelemetryEmitter:
    def __init__(
        self,
        *,
        emit_execution_event: Callable[..., Awaitable[None]],
        total_retries_provider: Callable[[], int],
    ) -> None:
        self._emit_execution_event = emit_execution_event
        self._total_retries_provider = total_retries_provider

    async def emit_retry_status(
        self,
        scope: str,
        *,
        attempt: int,
        error_class: str,
        reason: str,
        delay_ms: int,
        tool: str | None = None,
    ) -> None:
        meta = build_retry_meta(
            scope=scope,
            attempt=attempt,
            error_class=error_class,
            reason=reason,
            delay_ms=delay_ms,
            total_used=self._total_retries_provider(),
            tool=tool,
        )
        phase = "streaming" if scope == "llm" else "tool_use"
        await self._emit_execution_event("status", "", phase=phase, meta=meta)
